# inplace.py — The verified swap: put new subtitle tracks INTO the source video and rename it with a (Sub-EN,FR) marker, never losing the source. Exports: update_in_place, marked_name, marker, OutputResult.
"""
Cyril's rule (doc/user-process.md §4, D-0002; CLAUDE.md « Forbidden »): a source video is replaced only by this swap.

    free disk ≥ source size → write `<stem>.substudio-tmp.mkv` next to the source → verify it against the source
    → atomic os.replace onto the final name → only then remove the source if the name changed (.mp4 → .mkv, marker)

The source is never opened for writing. Any failure before the os.replace leaves it byte-identical and removes the
temp file; that property is what tests/test_output.py checks with a hash, and what makes the swap safe to run on a
library. A video with nothing new is not touched at all, not even renamed.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Collection, Dict, List, Literal, Mapping, Optional, Sequence, Set

from babelfish import Language

from ..acquire.plan import normalize_language
from ..jobs import JobHandle
from ..provenance import (FROM_TAG, ORIGIN_TAG, ORIGINS, REVIEWED_TAG, REVIEWED_YES, UNTRANSLATED_TAG, cue_ids_value,
                          name_marker, strip_name_marker, substudio_track_title)
from ..subtitles import Cue, drop_after, to_srt
from .media import (_TEXT_TO_SRT, OutputBusy, OutputError, _disposition, _duration, _is_cover, _probe,
                    _run, _sub_plan, _untagged_audio, subtitle_streams)
from .verify import _verify

logger = logging.getLogger("SubStudio.Output")

# The temp file is `<stem>.substudio-tmp-<pid>-<random>.mkv`, created with O_EXCL: a unique name this run owns, so
# no user file is ever overwritten or deleted as « our temp », and two runs can never write the same temp.
TMP_MARK = ".substudio-tmp-"

# One swap at a time per source in this process (the job system runs in one backend process).
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

@dataclass(frozen=True)
class OutputResult:
    status: Literal["updated", "untouched"]
    path: Path                      # where the video is now (the source path when untouched)
    added: List[str] = field(default_factory=list)   # ISO 639-1 of the tracks written
    marker: Optional[str] = None    # « (Sub-EN,FR) » when the file was updated
    # Where each subtitle of the source is now: its position among the source's subtitle streams (the ladder's
    # `-map 0:s:N` ref) → its stream index in the file at `path`. A track the update replaced is absent.
    subs: Dict[int, int] = field(default_factory=dict)


# ---------------------------------------------------------------- names

def _iso1(lang: str) -> str:
    code = normalize_language(lang)
    if not code:
        # « OV » must be resolved to the audio's language by the caller: the marker lists ACTUAL languages.
        raise ValueError(f"Not a language this output can name: {lang!r} (resolve OV to the audio language first)")
    return code


def marker(languages: Sequence[str]) -> str:
    """[en, fr, en] → `(Sub-EN,FR)`: the actual languages, each once, in the order given (provenance owns the format)."""
    return name_marker([_iso1(raw) for raw in languages])


def marked_name(source: Path, languages: Sequence[str]) -> str:
    """`Ghosts S05E01.mp4`, [en, fr] → `Ghosts S05E01 (Sub-EN,FR).mkv`. An existing marker is replaced, never stacked."""
    base = strip_name_marker(source.stem).rstrip()
    return f"{base} {marker(languages)}.mkv"


# ---------------------------------------------------------------- ffmpeg


def _moved_subs(source_info: dict, new_langs: List[str], final: Path) -> Dict[int, int]:
    # The mux writes the subtitles in the order of _sub_plan: the k-th subtitle of the new file is plan[k]. The
    # stream indexes are read from the placed file, never assumed.
    position = {s["index"]: i for i, s in
                enumerate(s for s in source_info.get("streams", []) if s.get("codec_type") == "subtitle")}
    now = subtitle_streams(final)
    return {position[item["index"]]: now[k] for k, item in enumerate(_sub_plan(source_info, new_langs))
            if isinstance(item, dict) and k in now}


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


def _mux_args(source: Path, info: dict, srt_paths: List[tuple], tmp: Path, covers: Sequence[tuple] = (),
              origins: Mapping[str, str] = {}, reviewed: Collection[str] = (),
              audio_tag: Optional[str] = None, extra: Mapping[str, Mapping[str, str]] = {}, *,
              default_lang: Optional[str] = None, demoted: Collection[int] = ()) -> List[str]:
    """The ffmpeg command writing the temp MKV: every video and audio stream copied, then the subtitles in the
    order of media._sub_plan (kept ones copied, MP4 text → SRT; our replaced tracks dropped; each new SRT first in
    its language), then attachments, cover pictures re-attached as files. Our track of `default_lang` is marked
    « default », our others not; a kept track whose stream index is in `demoted` loses « default ». Chapters and
    global metadata follow by ffmpeg's default. Data streams (MP4 timecode) are not carried by MKV: dropped, logged."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    for _, p in srt_paths:
        cmd += ["-i", str(p)]
    inputs = {lang: i + 1 for i, (lang, _) in enumerate(srt_paths)}

    streams = info.get("streams", [])
    out_idx = 0
    maps: List[str] = []
    codec_args: List[str] = []
    for s in streams:
        kind, idx = s.get("codec_type"), s["index"]
        if kind in ("video", "audio") and not _is_cover(s):   # a cover is re-attached below from its file
            maps += ["-map", f"0:{idx}"]
            # Written explicitly: the MKV muxer otherwise marks the first subtitle « default » and turns an
            # MP4 cover (attached_pic) into an ordinary video track a player may show instead of the film.
            codec_args += [f"-disposition:{out_idx}", _disposition(s)]
            out_idx += 1
        elif kind == "data":
            logger.warning("%s: data stream #%s (%s) is not carried into MKV", source.name, idx, s.get("codec_tag_string"))
    meta: List[str] = []
    for k, item in enumerate(_sub_plan(info, list(inputs))):
        if isinstance(item, dict):
            maps += ["-map", f"0:{item['index']}"]
            codec_args += [f"-disposition:{out_idx}", _disposition(item, demoted=item["index"] in demoted)]
            if item.get("codec_name") in _TEXT_TO_SRT:
                codec_args += [f"-c:s:{k}", "srt"]
        else:
            lang = item
            maps += ["-map", f"{inputs[lang]}:0"]
            codec_args += [f"-c:s:{k}", "srt", f"-disposition:{out_idx}", "default" if lang == default_lang else "0"]
            tags = {"language": Language.fromalpha2(lang).alpha3b, "title": substudio_track_title(lang),
                    ORIGIN_TAG: origins[lang], **({REVIEWED_TAG: REVIEWED_YES} if lang in reviewed else {}),
                    **extra.get(lang, {})}
            for key, value in tags.items():
                meta += [f"-metadata:s:s:{k}", f"{key}={value}"]
        out_idx += 1
    maps += [x for s in streams if s.get("codec_type") == "attachment" for x in ("-map", f"0:{s['index']}")]

    cmd += [*maps, "-c", "copy", *codec_args, *meta]
    if audio_tag:
        # The first audio stream of the output is the source's first (mapped in source order): what « OV » reads.
        cmd += ["-metadata:s:a:0", f"language={Language.fromalpha2(audio_tag).alpha3b}"]
    n_att = sum(1 for s in info.get("streams", []) if s.get("codec_type") == "attachment")
    for j, (p, mime, name) in enumerate(covers):
        cmd += ["-attach", str(p), f"-metadata:s:t:{n_att + j}", f"mimetype={mime}",
                f"-metadata:s:t:{n_att + j}", f"filename={name}"]
    cmd += ["-f", "matroska", str(tmp)]
    return cmd


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
    origins: Mapping[str, str],
    reviewed: Collection[str] = (),
    audio_language: Optional[str] = None,
    untranslated: Mapping[str, Collection[int]] = {},
    from_languages: Mapping[str, str] = {},
    rejected: Collection[int] = (),
    job: Optional[JobHandle] = None,
) -> OutputResult:
    """Writes `tracks` ({iso 639-1: cues}) into `source` by the verified swap and renames it with the marker of
    `languages_validated` (every language the file now carries validated: the new ones and the kept ones).
    `origins` ({lang: one of provenance.ORIGINS except "kept"}) is written as the ORIGIN_TAG of each new track,
    so the Library reads how it was obtained after a rescan. `reviewed` (languages of `tracks`) also get the
    REVIEWED_TAG: a person checked them in the Review screen. `audio_language` (the language the run detected, sure
    enough to trust) is written on the first audio stream only when the source's is untagged or « und »: a tagged
    stream is never retagged, and the tag alone never causes a swap. `from_languages` ({lang: source language}) and
    `untranslated` ({lang: ids of `tracks` cues left in that language}) become the FROM_TAG and UNTRANSLATED_TAG of a
    new track, the ids turned into the track's positions 1..n (a cue past the end is dropped, so it is not listed).
    Each new track comes first among its language's subtitles; the one of the first validated language is marked
    « default ». `rejected` (positions among the source's subtitle streams, the ladder's refs) are the old tracks the
    gate refused: one of a language written here loses « default », and is kept.

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
    origin_of = {_iso1(lang): o for lang, o in origins.items()}
    for lang in new:
        o = origin_of.get(lang)
        if o not in ORIGINS or o == "kept":
            raise ValueError(f"{lang}: origin must be one of {[x for x in ORIGINS if x != 'kept']}, got {o!r}")
    audio_code = _iso1(audio_language) if audio_language else None
    left = {_iso1(lang): set(ids) for lang, ids in untranslated.items() if ids}
    source_lang = {_iso1(lang): _iso1(f) for lang, f in from_languages.items() if f}

    lock = _lock_for(source)
    if not lock.acquire(blocking=False):
        raise OutputBusy(f"{source.name} is already being updated by another job")
    try:
        return _swap(source, new, validated, {l: origin_of[l] for l in new}, {_iso1(l) for l in reviewed} & set(new),
                     audio_code, job, left=left, source_lang=source_lang, rejected=set(rejected))
    finally:
        lock.release()


def _swap(source: Path, new: Dict[str, List[Cue]], validated: List[str], origins: Dict[str, str],
          reviewed: Set[str], audio_language: Optional[str], job: Optional[JobHandle], *,
          left: Mapping[str, Set[int]] = {}, source_lang: Mapping[str, str] = {},
          rejected: Set[int] = frozenset()) -> OutputResult:
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
    audio_tag = audio_language if audio_language and _untagged_audio(info) else None
    first = _iso1(validated[0])
    default_lang = first if first in new else None     # a kept first language keeps its own flags
    old = [s for s in info.get("streams", []) if s.get("codec_type") == "subtitle"]
    demoted = {old[p]["index"] for p in rejected if 0 <= p < len(old)
               and normalize_language(old[p].get("tags", {}).get("language")) in new}
    with tempfile.TemporaryDirectory(prefix="substudio-out-") as work:
        srt_paths: List[tuple] = []
        expected: Dict[str, int] = {}
        extra: Dict[str, Dict[str, str]] = {}
        for lang, cues in new.items():
            kept = drop_after(cues, duration)
            if not kept:
                raise OutputError(f"every {lang} cue starts after the end of the video")
            p = Path(work) / f"{lang}.srt"
            p.write_text(to_srt(kept), encoding="utf-8")
            srt_paths.append((lang, p))
            expected[lang] = len(kept)
            extra[lang] = {}
            if lang in source_lang:
                extra[lang][FROM_TAG] = source_lang[lang]
            positions = [n for n, c in enumerate(kept, start=1) if c.id in left.get(lang, ())]
            if positions:
                extra[lang][UNTRANSLATED_TAG] = cue_ids_value(positions)

        tmp = _new_temp(source)
        try:
            covers = _extract_covers(source, info, Path(work))
            result = _run(_mux_args(source, info, srt_paths, tmp, covers, origins, reviewed, audio_tag, extra,
                                    default_lang=default_lang, demoted=demoted), job)
            if result.returncode != 0:
                raise OutputError(f"ffmpeg could not write the new file: "
                                  f"{result.stderr.decode('utf-8', 'replace').strip()[:300]}")
            _verify(info, tmp, expected, origins, reviewed, audio_tag, extra, default_lang=default_lang, demoted=demoted)
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
    try:
        subs = _moved_subs(info, list(new), final)
    except OutputError as e:
        # The update is done and verified; without the map, the kept tracks just read « not checked yet ».
        logger.error("Updated %s, but could not re-read its subtitle streams (%s): their verdicts are not recorded",
                     final.name, e)
        subs = {}
    return OutputResult(status="updated", path=final, added=list(new), marker=marker(validated), subs=subs)
