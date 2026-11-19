# inplace.py — The verified swap: put new subtitle tracks INTO the source video and rename it with a (Sub-EN,FR) marker, never losing the source. Exports: update_in_place, marked_name, OutputResult, OutputError.
"""
Cyril's rule (doc/user-process.md §4, D-0002; CLAUDE.md « Forbidden »): a source video is replaced only by this swap.

    free disk ≥ source size → write `<stem>.substudio-tmp.mkv` next to the source → verify it against the source
    → atomic os.replace onto the final name → only then remove the source if the name changed (.mp4 → .mkv, marker)

The source is never opened for writing. Any failure before the os.replace leaves it byte-identical and removes the
temp file; that property is what tests/test_output.py checks with a hash, and what makes the swap safe to run on a
library. A video with nothing new is not touched at all, not even renamed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Mapping, Optional, Sequence, Set

import pysubs2
from babelfish import Language

from ..acquire.plan import normalize_language
from ..jobs import JobAborted, JobHandle
from ..provenance import is_substudio_track, substudio_track_title
from ..subtitles import Cue, drop_after, to_srt

logger = logging.getLogger("SubStudio.Output")

# The temp file is `<stem>.substudio-tmp-<pid>-<random>.mkv`, created with O_EXCL: a unique name this run owns, so
# no user file is ever overwritten or deleted as « our temp », and two runs can never write the same temp.
TMP_MARK = ".substudio-tmp-"
DURATION_TOLERANCE_S = 1.0

# One swap at a time per source in this process (the job system runs in one backend process).
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
# The marker at the end of a stem: « (Sub-EN,FR) ». Matched case-insensitively so a hand-typed one is replaced too.
_MARKER_RE = re.compile(r"\s*\(Sub-[A-Za-z]{2}(?:,[A-Za-z]{2})*\)$", re.IGNORECASE)
# Subtitle codecs an MKV cannot hold as they are; converted to SRT when the source is an MP4.
_TEXT_TO_SRT = {"mov_text"}


class OutputError(RuntimeError):
    """The swap did not happen; the source is unchanged (the message says why, in one sentence)."""


@dataclass(frozen=True)
class OutputResult:
    status: Literal["updated", "untouched"]
    path: Path                      # where the video is now (the source path when untouched)
    added: List[str] = field(default_factory=list)   # ISO 639-1 of the tracks written
    marker: Optional[str] = None    # « (Sub-EN,FR) » when the file was updated


# ---------------------------------------------------------------- names

def _iso1(lang: str) -> str:
    code = normalize_language(lang)
    if not code:
        # « OV » must be resolved to the audio's language by the caller: the marker lists ACTUAL languages.
        raise ValueError(f"Not a language this output can name: {lang!r} (resolve OV to the audio language first)")
    return code


def marker(languages: Sequence[str]) -> str:
    """[en, fr, en] → `(Sub-EN,FR)`: the actual languages, each once, in the order given."""
    langs: List[str] = []
    for raw in languages:
        code = _iso1(raw)
        if code not in langs:
            langs.append(code)
    if not langs:
        raise ValueError("A marker needs at least one validated language")
    return f"(Sub-{','.join(c.upper() for c in langs)})"


def marked_name(source: Path, languages: Sequence[str]) -> str:
    """`Ghosts S05E01.mp4`, [en, fr] → `Ghosts S05E01 (Sub-EN,FR).mkv`. An existing marker is replaced, never stacked."""
    base = _MARKER_RE.sub("", source.stem).rstrip()
    return f"{base} {marker(languages)}.mkv"


# ---------------------------------------------------------------- ffmpeg

def _run(cmd: List[str], job: Optional[JobHandle]) -> subprocess.CompletedProcess:
    # Popen (not run) so a Cancel can kill ffmpeg mid-write, as the muxer does.
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if job:
        job.track(process)
    try:
        out, err = process.communicate()
    finally:
        if job:
            job.untrack()
    if job and job.is_aborted:
        raise JobAborted(job.file_id)
    return subprocess.CompletedProcess(cmd, process.returncode, out, err)


def _probe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", "-show_chapters",
           str(path)]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise OutputError(f"ffprobe could not read {path.name}: {result.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return json.loads(result.stdout.decode("utf-8", "replace"))


def _is_cover(stream: dict) -> bool:
    return bool(stream.get("disposition", {}).get("attached_pic"))


def _disposition(s: dict) -> str:
    """The source stream's flags as ffmpeg's -disposition value (`default+forced`, or `0` for none)."""
    on = [k for k, v in s.get("disposition", {}).items() if v]
    return "+".join(on) if on else "0"


def _flags(s: dict) -> tuple:
    d = s.get("disposition", {})
    return (int(d.get("default", 0)), int(d.get("forced", 0)))


def _sub_key(s: dict) -> tuple:
    """What must survive of a kept subtitle track. MP4 text becomes SRT in MKV, so mov_text counts as subrip."""
    codec = s.get("codec_name", "?")
    tags = s.get("tags", {})
    return ("subrip" if codec in _TEXT_TO_SRT else codec, normalize_language(tags.get("language")),
            tags.get("title"), _flags(s))


def _signature(info: dict) -> dict:
    """Everything of the source the new file must carry unchanged, except the subtitle tracks (checked apart)."""
    streams = info.get("streams", [])
    return {
        "video": [(s.get("codec_name"), _flags(s)) for s in streams
                  if s.get("codec_type") == "video" and not _is_cover(s)],
        "audio": [(s.get("codec_name"), normalize_language(s.get("tags", {}).get("language")), _flags(s))
                  for s in streams if s.get("codec_type") == "audio"],
        "covers": len([s for s in streams if s.get("codec_type") == "video" and _is_cover(s)]),
        "attachments": sorted(s.get("tags", {}).get("filename", "?") for s in streams
                              if s.get("codec_type") == "attachment"),
        "chapters": len(info.get("chapters", [])),
    }


def _duration(info: dict) -> float:
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise OutputError("the video has no readable duration")


def _kept_subs(info: dict, new_langs: Set[str]) -> List[dict]:
    """Existing subtitle tracks that stay: all of them, except OUR previous track of a language being rewritten."""
    kept = []
    for s in info.get("streams", []):
        if s.get("codec_type") != "subtitle":
            continue
        tags = s.get("tags", {})
        if normalize_language(tags.get("language")) in new_langs and \
                is_substudio_track(kind="embedded", title=tags.get("title")):
            continue
        kept.append(s)
    return kept


_COVER_TYPES = {"mjpeg": ("jpg", "image/jpeg"), "png": ("png", "image/png"),
                "webp": ("webp", "image/webp"), "bmp": ("bmp", "image/bmp")}


def _extract_covers(source: Path, info: dict, work: Path) -> List[tuple]:
    """Each cover picture as a file (path, mimetype, name). MKV keeps a cover only as an attached FILE: copied as a
    stream (even from an MKV) it becomes an ordinary video track, measured with ffmpeg 9.0.2 on 2026-11-19."""
    covers = []
    for s in info.get("streams", []):
        if s.get("codec_type") != "video" or not _is_cover(s):
            continue
        kind = _COVER_TYPES.get(s.get("codec_name"))
        if not kind:
            raise OutputError(f"the cover picture #{s['index']} is {s.get('codec_name')}, which MKV cannot attach")
        ext, mime = kind
        p = work / f"cover-{s['index']}.{ext}"
        result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                                 "-map", f"0:{s['index']}", "-c", "copy", "-frames:v", "1", "-f", "image2", str(p)],
                                capture_output=True)
        if result.returncode != 0 or not p.is_file() or p.stat().st_size == 0:
            raise OutputError(f"the cover picture #{s['index']} could not be extracted")
        name = s.get("tags", {}).get("filename") or s.get("tags", {}).get("FILENAME") or f"cover.{ext}"
        covers.append((p, mime, name))
    return covers


def _mux_args(source: Path, info: dict, srt_paths: List[tuple], tmp: Path, covers: Sequence[tuple] = ()) -> List[str]:
    """The ffmpeg command writing the temp MKV: every video, audio and attachment stream copied, cover pictures
    re-attached as files, kept subtitles copied (MP4 text → SRT), our replaced tracks dropped, new SRT tracks
    added. Chapters and global metadata follow by ffmpeg's default. Data streams (MP4 timecode) are not carried by
    MKV: dropped, logged."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    for _, p in srt_paths:
        cmd += ["-i", str(p)]

    kept = {s["index"] for s in _kept_subs(info, {lang for lang, _ in srt_paths})}
    out_idx = out_sub = 0
    codec_args: List[str] = []
    for s in info.get("streams", []):
        kind, idx = s.get("codec_type"), s["index"]
        if kind == "video" and _is_cover(s):
            continue   # re-attached below from its extracted file
        if kind in ("video", "audio", "attachment") or (kind == "subtitle" and idx in kept):
            cmd += ["-map", f"0:{idx}"]
            if kind != "attachment":
                # Written explicitly: the MKV muxer otherwise marks the first subtitle « default » and turns an
                # MP4 cover (attached_pic) into an ordinary video track a player may show instead of the film.
                codec_args += [f"-disposition:{out_idx}", _disposition(s)]
            if kind == "subtitle":
                if s.get("codec_name") in _TEXT_TO_SRT:
                    codec_args += [f"-c:s:{out_sub}", "srt"]
                out_sub += 1
            out_idx += 1
        elif kind == "data":
            logger.warning("%s: data stream #%s (%s) is not carried into MKV", source.name, idx, s.get("codec_tag_string"))

    cmd += ["-c", "copy", *codec_args]
    for i, (lang, _) in enumerate(srt_paths):
        k = out_sub + i
        cmd += ["-map", f"{i + 1}:0", f"-c:s:{k}", "srt", f"-disposition:{out_idx + i}", "0",
                f"-metadata:s:s:{k}", f"language={Language.fromalpha2(lang).alpha3b}",
                f"-metadata:s:s:{k}", f"title={substudio_track_title(lang)}"]
    n_att = sum(1 for s in info.get("streams", []) if s.get("codec_type") == "attachment")
    for j, (p, mime, name) in enumerate(covers):
        cmd += ["-attach", str(p), f"-metadata:s:t:{n_att + j}", f"mimetype={mime}",
                f"-metadata:s:t:{n_att + j}", f"filename={name}"]
    cmd += ["-f", "matroska", str(tmp)]
    return cmd


def _decoded_cue_count(path: Path, sub_index: int) -> int:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", f"0:s:{sub_index}", "-f", "srt", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise OutputError(f"the new subtitle track #{sub_index} cannot be decoded")
    return len(pysubs2.SSAFile.from_string(result.stdout.decode("utf-8", "replace"), format_="srt").events)


def _verify(source_info: dict, tmp: Path, expected: Dict[str, int]) -> None:
    """The check the swap depends on. Raises OutputError on the first difference."""
    out = _probe(tmp)
    before, after = _signature(source_info), _signature(out)
    for part in before:
        if before[part] != after[part]:
            raise OutputError(f"the new file's {part} differ from the source ({after[part]} vs {before[part]})")
    drift = abs(_duration(out) - _duration(source_info))
    if drift > DURATION_TOLERANCE_S:
        raise OutputError(f"the new file's duration differs from the source by {drift:.1f} s")

    subs = [s for s in out.get("streams", []) if s.get("codec_type") == "subtitle"]
    kept_before = [_sub_key(s) for s in _kept_subs(source_info, set(expected))]
    kept_after = [_sub_key(s) for s in subs[:len(kept_before)]]
    if kept_after != kept_before or len(subs) != len(kept_before) + len(expected):
        raise OutputError(f"the existing subtitle tracks were not all kept ({kept_after} vs {kept_before})")
    for lang, count in expected.items():
        title = substudio_track_title(lang)
        found = [i for i, s in enumerate(subs) if s.get("tags", {}).get("title") == title]
        if len(found) != 1:
            raise OutputError(f"the new file has {len(found)} « {title} » tracks instead of one")
        tag = subs[found[0]].get("tags", {}).get("language")
        if normalize_language(tag) != lang:
            raise OutputError(f"the « {title} » track is tagged {tag!r}")
        decoded = _decoded_cue_count(tmp, found[0])
        if decoded != count:
            raise OutputError(f"the « {title} » track decodes to {decoded} cues instead of {count}")


# ---------------------------------------------------------------- the swap

def _same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _lock_for(source: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(source))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _new_temp(source: Path) -> Path:
    """A temp path next to the source that did not exist and now belongs to this run (O_EXCL)."""
    for _ in range(5):
        tmp = source.with_name(f"{source.stem}{TMP_MARK}{os.getpid()}-{secrets.token_hex(4)}.mkv")
        try:
            os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return tmp
        except FileExistsError:
            continue
    raise OutputError("could not create a unique temp file next to the source")


def _place(tmp: Path, final: Path, source: Path) -> None:
    """Moves the verified temp onto its final name. Onto the source itself: atomic replace. Onto a NEW name: never
    over an existing file, even one that appeared since the first check (Windows rename and link both refuse)."""
    if _same_file(final, source):
        os.replace(tmp, final)
    elif os.name == "nt":
        os.rename(tmp, final)          # raises FileExistsError if final exists; atomic on one volume
    else:
        os.link(tmp, final)            # raises FileExistsError if final exists
        os.unlink(tmp)


def update_in_place(
    source: Path,
    tracks: Mapping[str, List[Cue]],
    languages_validated: Sequence[str],
    *,
    job: Optional[JobHandle] = None,
) -> OutputResult:
    """Writes `tracks` ({iso 639-1: cues}) into `source` by the verified swap and renames it with the marker of
    `languages_validated` (every language the file now carries validated: the new ones and the kept ones).

    Nothing new → untouched (no rename). Raises OutputError (source unchanged) on any failed step,
    JobAborted (source unchanged) on Cancel."""
    source = Path(source)
    if not tracks:
        return OutputResult(status="untouched", path=source)

    new: Dict[str, List[Cue]] = {}
    for lang, cues in tracks.items():
        code = _iso1(lang)
        if code in new:
            raise ValueError(f"{lang!r} and another key are the same language ({code}): one track would be lost")
        new[code] = cues
    validated = list(languages_validated)
    if any(lang not in {_iso1(v) for v in validated} for lang in new):
        raise ValueError("every written language must also be in languages_validated")
    if any(not cues for cues in new.values()):
        raise OutputError("a track to write has no cues")

    lock = _lock_for(source)
    if not lock.acquire(blocking=False):
        raise OutputError(f"{source.name} is already being updated by another job")
    try:
        return _swap(source, new, validated, job)
    finally:
        lock.release()


def _swap(source: Path, new: Dict[str, List[Cue]], validated: List[str], job: Optional[JobHandle]) -> OutputResult:
    if not source.is_file():
        raise OutputError(f"the source {source.name} does not exist")
    final = source.with_name(marked_name(source, validated))
    if not _same_file(final, source) and final.exists():
        raise OutputError(f"{final.name} already exists next to the source")

    size = source.stat().st_size
    free = shutil.disk_usage(source.parent).free
    if free < size:
        raise OutputError(f"not enough free disk: {free / 1e9:.1f} GB free, {size / 1e9:.1f} GB needed for the swap")

    info = _probe(source)
    duration = _duration(info)
    with tempfile.TemporaryDirectory(prefix="substudio-out-") as work:
        srt_paths: List[tuple] = []
        expected: Dict[str, int] = {}
        for lang, cues in new.items():
            kept = drop_after(cues, duration)
            if not kept:
                raise OutputError(f"every {lang} cue starts after the end of the video")
            p = Path(work) / f"{lang}.srt"
            p.write_text(to_srt(kept), encoding="utf-8")
            srt_paths.append((lang, p))
            expected[lang] = len(kept)

        tmp = _new_temp(source)
        try:
            covers = _extract_covers(source, info, Path(work))
            result = _run(_mux_args(source, info, srt_paths, tmp, covers), job)
            if result.returncode != 0:
                raise OutputError(f"ffmpeg could not write the new file: "
                                  f"{result.stderr.decode('utf-8', 'replace').strip()[:300]}")
            _verify(info, tmp, expected)
            # The one moment the library changes.
            try:
                _place(tmp, final, source)
            except FileExistsError:
                raise OutputError(f"{final.name} appeared next to the source during the update; nothing replaced")
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError as e:
                # Never hide the real failure behind the cleanup's; name the leftover instead.
                logger.error("Could not remove the temp file %s (%s); delete it by hand", tmp.name, e)
            raise

    if not _same_file(final, source):
        try:
            source.unlink()
        except OSError as e:
            # Both files exist and the new one is verified: nothing is lost, but say it, never swallow it.
            raise OutputError(f"the new file {final.name} is in place, but the old {source.name} could not be "
                              f"removed ({e}); delete it by hand") from e
    logger.info("Updated %s → %s (+%s)", source.name, final.name, ",".join(new))
    return OutputResult(status="updated", path=final, added=list(new), marker=marker(validated))
