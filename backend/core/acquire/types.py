# types.py — shapes shared by the subtitle ladder: existing tracks and plan steps. Exports: Rung, ExistingSub, Step, LanguagePlan.
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class Rung(str, Enum):
    """The subtitle ladder, Cyril's order (doc/user-process.md §2). Every rung's result goes through the quality gate;
    a result that fails it falls through to the next step of the same LanguagePlan."""
    EXISTING = "existing"    # a subtitle already in or beside the file: evaluate it; good = file unchanged
    DOWNLOAD = "download"    # a human subtitle in this language, downloaded, re-timed, then evaluated
    TRANSLATE = "translate"  # a GOOD subtitle of this run in another language, translated locally
    ASR = "asr"              # nothing good anywhere: transcribe in OV, correct; another language is then
                             # translated from that corrected OV transcript


@dataclass(frozen=True)
class ExistingSub:
    language: Optional[str]  # ISO 639-1 (`fr`), or None when the file does not say
    kind: str                # "embedded" or "sidecar"
    ref: str                 # sidecar path, or the embedded stream index as text
    text: bool = True        # False for PGS/VobSub: watchable, but not translatable without OCR
    marked: bool = False     # made by the current SubStudio (core/provenance.is_substudio_track): evaluated first
    # Our own translated track's tags (core/provenance UNTRANSLATED_TAG, FROM_TAG): the positions (1..n) of its lines
    # still in the source language, and that language. Rung 1 keeps the rest and re-translates only those (ladder).
    untranslated: Tuple[int, ...] = ()
    from_language: Optional[str] = None


@dataclass(frozen=True)
class Step:
    rung: Rung
    language: str                            # the language this step produces
    source: Optional[ExistingSub] = None     # EXISTING: the subtitle to evaluate
    # TRANSLATE: languages to translate from, best first (OV, then the other wanted ones, then source-only ones);
    # the executor takes the first that holds a gate-passed subtitle in this run, since which rung succeeds for
    # each language is only known at run time.
    from_languages: Tuple[str, ...] = ()


@dataclass
class LanguagePlan:
    language: str
    steps: List[Step] = field(default_factory=list)  # ordered fallbacks; steps[0] is the rung to try first
    is_ov: bool = False        # the audio's own language
    source_only: bool = False  # not asked for: run only to feed TRANSLATE, never written into the file

    @property
    def rung(self) -> Rung:
        return self.steps[0].rung
