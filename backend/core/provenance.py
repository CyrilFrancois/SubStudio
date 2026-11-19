# provenance.py — The one rule for « made and validated by the current SubStudio »: track title, origin tag, file-name marker. Exports: TRACK_TITLE_PREFIX, ORIGIN_TAG, ORIGINS, REJECTED, OV, substudio_track_title, track_key, is_substudio_track, name_marker, parse_name_marker, strip_name_marker.
#
# Read by the Library (coverage), the planner (what is missing) and the muxer (what title to write),
# so the three can never disagree. Anything this rule does not recognise is « existing »: by default
# it does not count as coverage (Settings.count_existing), because the pre-2026-11-19 SubStudio wrote
# muxed tracks titled "AI FR" and plain {stem}.{lang}.srt sidecars, and Cyril judged them bad.

import os
import re
from typing import Collection, List, Literal, Optional, Sequence

TRACK_TITLE_PREFIX = "SubStudio "
# Exactly "SubStudio <iso 639-1>", e.g. "SubStudio fr". The old muxer wrote "AI FR", which can
# never match, so an old track is never mistaken for a new one.
_TITLE_RE = re.compile(r"^SubStudio [a-z]{2}$")

# Stream tag the muxer writes beside the title: how the validated track was obtained (D-0002 §3).
# ffprobe returns it under tags; a track without it is ours but of unrecorded origin.
ORIGIN_TAG = "SUBSTUDIO_ORIGIN"
ORIGINS = ("kept", "downloaded", "translated", "transcribed")
# Verdict-record value of a pre-existing subtitle the quality gate refused (an accepted one carries its origin).
REJECTED = "rejected"

# « Original version »: the language of the audio, a wanted-language entry like "fr" but resolved per video.
OV = "ov"

# D-0002 §4 (Cyril, 2026-11-19): a file SubStudio changed is renamed « Title (Sub-EN,FR).mkv » with the
# ACTUAL languages (OV resolved, never the literal "OV"); a file that was already good keeps its name.
_MARKER_RE = re.compile(r"\s*\(Sub-((?!OV\b)[A-Z]{2}(?:,(?!OV\b)[A-Z]{2})*)\)$")


def substudio_track_title(lang: str) -> str:
    """Title the muxer writes on every track it adds. `lang` is ISO 639-1 ("fr")."""
    if not re.fullmatch(r"[a-z]{2}", lang):
        raise ValueError(f"Expected an ISO 639-1 code, got {lang!r}")
    return f"{TRACK_TITLE_PREFIX}{lang}"


def track_key(*, kind: Literal["embedded", "sidecar"], stream_index: Optional[int] = None,
              path: Optional[str] = None) -> str:
    """How a verdict record names one subtitle of one video file: 'stream:2' for a track inside it,
    the normalised absolute path for a file next to it. A record is only valid for the exact file
    (path + mtime + size) it was written for."""
    if kind == "embedded":
        if stream_index is None:
            raise ValueError("An embedded track needs its stream index")
        return f"stream:{stream_index}"
    if kind == "sidecar":
        if not path:
            raise ValueError("A sidecar needs its path")
        return os.path.normcase(os.path.realpath(path))
    raise ValueError(f"Unknown track kind {kind!r}")


def is_substudio_track(
    *,
    kind: Literal["embedded", "sidecar"],
    title: Optional[str] = None,
    stream_index: Optional[int] = None,
    path: Optional[str] = None,
    manifest: Collection[str] = (),
) -> bool:
    """True when the current SubStudio made or validated this subtitle.

    A track SubStudio muxed carries the title « SubStudio <lang> ». A subtitle it left untouched
    because the quality gate accepted it (Cyril: a good file keeps its exact bytes and name), or a
    sidecar (players need a plain Movie.fr.srt), carries no mark: it is ours only when the video's
    verdict record (`manifest`, keys from track_key) lists it. No record: pass nothing."""
    if kind == "embedded" and title and _TITLE_RE.match(title):
        return True
    if not manifest:
        return False
    try:
        key = track_key(kind=kind, stream_index=stream_index, path=path)
    except ValueError:
        return False
    return key in manifest


def _code(lang: str) -> str:
    # OV must be resolved to the audio's language before naming the file (Cyril, 2026-11-19).
    if lang == OV or not re.fullmatch(r"[a-z]{2}", lang):
        raise ValueError(f"Expected a resolved ISO 639-1 code, got {lang!r}")
    return lang.upper()


def name_marker(langs: Sequence[str]) -> str:
    """'(Sub-EN,FR)' for ['en', 'fr']; order kept as given, duplicates (OV = EN) dropped."""
    langs = list(dict.fromkeys(langs))
    if not langs:
        raise ValueError("A marker needs at least one language")
    return f"(Sub-{','.join(_code(l) for l in langs)})"


def parse_name_marker(stem: str) -> Optional[List[str]]:
    """The validated languages a file name claims (['en', 'fr']), or None when it carries no marker.
    A claim, not a proof: the Library still requires the tracks themselves to carry the title."""
    m = _MARKER_RE.search(stem)
    return [c.lower() for c in m.group(1).split(",")] if m else None


def strip_name_marker(stem: str) -> str:
    """'Ghosts S05E01 (Sub-EN,FR)' -> 'Ghosts S05E01', so re-marking never stacks two markers."""
    return _MARKER_RE.sub("", stem)
