# types.py — shapes shared by the subtitle ladder: existing tracks and plan steps. Exports: Rung, ExistingSub, Step, LanguagePlan.
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Rung(str, Enum):
    """The subtitle ladder, best first (doc/architecture-review-2026-11-19.md §4.1)."""
    EXISTING = "existing"    # a track in the wanted language is already there
    DOWNLOAD = "download"    # a human subtitle in the wanted language, downloaded then re-timed
    TRANSLATE = "translate"  # a human subtitle in another language, translated locally
    ASR = "asr"              # nothing human anywhere: transcribe, then translate


@dataclass(frozen=True)
class ExistingSub:
    language: Optional[str]  # ISO 639-1 (`fr`), or None when the file does not say
    kind: str                # "embedded" or "sidecar"
    ref: str                 # sidecar path, or the embedded stream index as text
    text: bool = True        # False for PGS/VobSub: watchable, but not translatable without OCR
    marked: bool = False     # made by the current SubStudio (core/provenance.is_substudio_track): always trusted


@dataclass(frozen=True)
class Step:
    rung: Rung
    language: str                          # the wanted language this step produces
    source: Optional[ExistingSub] = None   # EXISTING / TRANSLATE from a track already in the file
    download_language: Optional[str] = None  # TRANSLATE from a subtitle to download in this language


@dataclass
class LanguagePlan:
    language: str
    steps: List[Step] = field(default_factory=list)  # ordered fallbacks; steps[0] is the rung to try first

    @property
    def rung(self) -> Rung:
        return self.steps[0].rung
