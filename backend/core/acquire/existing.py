# existing.py — what a video already carries: its subtitle tracks and language-named sidecars as ExistingSub, and its audio language. Exports: find_existing, picked_sidecar, audio_language.
import json
import logging
import os
import re
import subprocess
from typing import List, Optional, Tuple

from ..provenance import is_substudio_track
from ..subtitle_processor import TEXT_SUBTITLE_CODECS
from ..subtitles import Cue, detect_language, load_file, shift
from .plan import normalize_language
from .types import ExistingSub

logger = logging.getLogger("SubStudio.Acquire.Existing")

_SIDECAR_EXT = (".srt", ".ass", ".ssa", ".vtt")
_SIDECAR_DIRS = (".", "Subs", "Subtitles")
_LANG_SEGMENT = re.compile(r"\.([A-Za-z]{2,3}(?:-[A-Za-z]{2})?)$")


def _probe(video_path: str, selector: str) -> list:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-select_streams", selector, video_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"ffprobe could not run on {os.path.basename(video_path)}: {e}") from e
    if r.returncode != 0:
        # A failed probe must never read as « this video has no subtitles ».
        raise RuntimeError(f"ffprobe failed on {os.path.basename(video_path)}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout or "{}").get("streams", [])


def find_existing(video_path: str) -> List[ExistingSub]:
    """Embedded subtitle tracks (ref = index among subtitle streams, for `-map 0:s:N`) and sidecars named
    `{stem}.{lang}.{ext}` beside the video or in Subs/ / Subtitles/. A sidecar with no language in its name is not
    listed: it cannot be checked against a language (the user can still pick it as the source explicitly)."""
    found: List[ExistingSub] = []
    for i, s in enumerate(_probe(video_path, "s")):
        tags = s.get("tags") or {}
        title = tags.get("title")
        found.append(ExistingSub(
            language=normalize_language(tags.get("language")), kind="embedded", ref=str(i),
            text=(s.get("codec_name") or "").lower() in TEXT_SUBTITLE_CODECS,
            marked=is_substudio_track(kind="embedded", title=title)))
    folder, stem = os.path.dirname(video_path), os.path.splitext(os.path.basename(video_path))[0]
    for sub in _SIDECAR_DIRS:
        d = os.path.join(folder, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            base, ext = os.path.splitext(name)
            if ext.lower() not in _SIDECAR_EXT or not base.lower().startswith(stem.lower() + "."):
                continue
            m = _LANG_SEGMENT.search(base)
            lang = normalize_language(m.group(1)) if m else None
            if lang:
                path = os.path.join(d, name)
                found.append(ExistingSub(lang, "sidecar", path, marked=is_substudio_track(kind="sidecar", path=path)))
    return found


def picked_sidecar(video_path: str, workflow_mode: str, selected: Optional[str],
                   offset_s: float) -> Optional[Tuple[ExistingSub, List[Cue]]]:
    """The sidecar the user chose (workflowMode srt/hybrid: his pick, else `{stem}.srt`), loaded and shifted by his
    offset (seconds), as a trusted ExistingSub with its cues; None when there is none. An unreadable or empty one
    raises: it is never silently replaced by another rung. Its language comes from its text."""
    if workflow_mode not in ("srt", "hybrid"):
        return None
    candidates = ([selected] if selected else []) + [os.path.splitext(video_path)[0] + ".srt"]
    found = next((c for c in candidates if os.path.isfile(c)), None)
    if found is None:
        return None
    logger.info(f"🔍 Using the chosen subtitle {os.path.basename(found)}")
    cues = load_file(found)
    if offset_s:
        cues = shift(cues, offset_s)
    lang = detect_language(cues)
    if lang is None:
        logger.warning(f"The language of {os.path.basename(found)} could not be detected: it cannot be used")
    return ExistingSub(lang, "sidecar", found, marked=True), cues


def audio_language(video_path: str) -> Optional[str]:
    """ISO 639-1 of the first audio stream's language tag, or None when untagged (`und`) or unknown."""
    streams = _probe(video_path, "a:0")
    return normalize_language((streams[0].get("tags") or {}).get("language")) if streams else None
