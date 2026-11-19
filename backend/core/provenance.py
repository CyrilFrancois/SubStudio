# provenance.py — The one rule for « this subtitle was made by the current SubStudio ». Exports: TRACK_TITLE_PREFIX, substudio_track_title, is_substudio_track.
#
# Read by the Library (coverage), the planner (what is missing) and the muxer (what title to write),
# so the three can never disagree. Anything this rule does not recognise is « existing »: by default
# it does not count as coverage (Settings.count_existing), because the pre-2026-11-19 SubStudio wrote
# muxed tracks titled "AI FR" and plain {stem}.{lang}.srt sidecars, and Cyril judged them bad.

import os
import re
from typing import Collection, Literal, Optional

TRACK_TITLE_PREFIX = "SubStudio "
# Exactly "SubStudio <iso 639-1>", e.g. "SubStudio fr". The old muxer wrote "AI FR", which can
# never match, so an old track is never mistaken for a new one.
_TITLE_RE = re.compile(r"^SubStudio [a-z]{2}$")


def substudio_track_title(lang: str) -> str:
    """Title the muxer writes on every track it adds. `lang` is ISO 639-1 ("fr")."""
    if not re.fullmatch(r"[a-z]{2}", lang):
        raise ValueError(f"Expected an ISO 639-1 code, got {lang!r}")
    return f"{TRACK_TITLE_PREFIX}{lang}"


def is_substudio_track(
    *,
    kind: Literal["embedded", "sidecar"],
    title: Optional[str] = None,
    path: Optional[str] = None,
    manifest: Collection[str] = (),
) -> bool:
    """embedded: recognised by its title. sidecar: players need a plain Movie.fr.srt, so the name
    cannot carry a marker; it is ours only when its path is in the video's work-folder manifest
    (the « jobs survive a restart » task writes it). Until that manifest exists, pass nothing and
    every sidecar is « existing »."""
    if kind == "embedded":
        return bool(title) and bool(_TITLE_RE.match(title))
    if kind == "sidecar":
        if not path:
            return False
        wanted = os.path.normcase(os.path.realpath(path))
        return any(os.path.normcase(os.path.realpath(p)) == wanted for p in manifest)
    raise ValueError(f"Unknown track kind {kind!r}")
