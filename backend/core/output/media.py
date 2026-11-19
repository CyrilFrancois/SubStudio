# media.py — What the swap and its check read from a video: ffprobe, ffmpeg runs, stream signatures, which subtitle tracks stay; and the output's errors. Exports: OutputError, OutputBusy, subtitle_streams.
"""Shared by core/output/inplace.py (the mux and the swap) and core/output/verify.py (the check after the mux),
so both read a video the same way. Moved unchanged from inplace.py (2026-11-19, file over 400 lines)."""

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Union

from ..acquire.plan import normalize_language
from ..jobs import JobAborted, JobHandle
from ..provenance import is_substudio_track

# Subtitle codecs an MKV cannot hold as they are; converted to SRT when the source is an MP4.
_TEXT_TO_SRT = {"mov_text"}


class OutputError(RuntimeError):
    """The swap did not happen; the source is unchanged (the message says why, in one sentence)."""


class OutputBusy(OutputError):
    """Another swap of the same video is running in this process; nothing was done (callers answer « busy »)."""


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


def subtitle_streams(path: Path) -> Dict[int, int]:
    """{position among subtitle streams: stream index} of a video as it is (an untouched one maps to itself)."""
    subs = [s["index"] for s in _probe(Path(path)).get("streams", []) if s.get("codec_type") == "subtitle"]
    return dict(enumerate(subs))

def _is_cover(stream: dict) -> bool:
    return bool(stream.get("disposition", {}).get("attached_pic"))


def _disposition(s: dict, demoted: bool = False) -> str:
    """The source stream's flags as ffmpeg's -disposition value (`default+forced`, or `0` for none); `demoted`
    drops « default » and keeps the rest."""
    on = [k for k, v in s.get("disposition", {}).items() if v and not (demoted and k == "default")]
    return "+".join(on) if on else "0"


def _flags(s: dict) -> tuple:
    d = s.get("disposition", {})
    return (int(d.get("default", 0)), int(d.get("forced", 0)))


def _sub_key(s: dict, demoted: bool = False) -> tuple:
    """What must survive of a kept subtitle track. MP4 text becomes SRT in MKV, so mov_text counts as subrip;
    `demoted`: the track is expected without « default » (_disposition)."""
    codec = s.get("codec_name", "?")
    tags = s.get("tags", {})
    default, forced = _flags(s)
    return ("subrip" if codec in _TEXT_TO_SRT else codec, normalize_language(tags.get("language")),
            tags.get("title"), (0 if demoted else default, forced))


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


def _untagged_audio(info: dict) -> bool:
    """True when the first audio stream (the one « OV » reads) has no language: no tag, « und », or not a language.
    False when it is tagged, or when the video has no audio at all (nothing to tag)."""
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    return bool(audio) and normalize_language(audio[0].get("tags", {}).get("language")) is None


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


def _sub_plan(info: dict, new_langs: Sequence[str]) -> List[Union[dict, str]]:
    """The new file's subtitle tracks in order: each kept source stream (its ffprobe dict) in source order, and our
    track of each new language (its code) placed just BEFORE the first kept track of that language, else at the end.
    A player given « English » takes the first English track: on S05E21 it was the old one the gate had rejected.
    The mux follows this plan and the check recomputes it from the source, so a file in another order is refused."""
    plan: List[Union[dict, str]] = []
    placed: Set[str] = set()
    for s in _kept_subs(info, set(new_langs)):
        lang = normalize_language(s.get("tags", {}).get("language"))
        if lang in new_langs and lang not in placed:
            plan.append(lang)
            placed.add(lang)
        plan.append(s)
    return plan + [lang for lang in new_langs if lang not in placed]
