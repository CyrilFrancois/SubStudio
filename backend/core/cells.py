# cells.py — one video × language and its verdict (state, origin, one-sentence reason), D-0002. Exports: CellState, Origin, Cell, Source, VideoRun, produce_cell.
"""
A *cell* is one wanted language of one video. Each cell ends PASSED with its origin, or FAILED with one sentence
saying why, independently of the other languages (doc/user-process.md §3). A cell's cues are kept in memory so a
single failed cell can be retried and the output rebuilt from every passed cell.

How later stages plug in: the ladder (core/acquire plan/fetch) decides, per cell, which Source to start from and
sets Origin.rung accordingly; the quality gate (task 7) runs on Cell.cues before a cell is marked PASSED; the output
stage reads VideoRun.passed().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from .acquire.types import Rung
from .subtitles import Cue, TranslationContext, TranslationFailed, Translator, translate_cues


class CellState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class Origin:
    """Where a passed cell's subtitle came from, in the words of doc/user-process.md §3."""
    rung: Rung
    from_language: Optional[str] = None  # the source language a translation started from, when known

    def label(self) -> str:
        if self.rung == Rung.EXISTING:
            return "kept"
        if self.rung == Rung.DOWNLOAD:
            return "downloaded"
        source = self.from_language or "unknown language"
        if self.rung == Rung.ASR:
            return "transcribed" if self.from_language is None else f"transcribed, translated from {source}"
        return f"translated from {source}"


@dataclass(frozen=True)
class Source:
    """The subtitle a cell starts from."""
    cues: List[Cue]
    rung: Rung                      # EXISTING/DOWNLOAD/TRANSLATE: a human subtitle; ASR: a transcription
    language: Optional[str]         # detected ISO 639-1, or None
    context: TranslationContext


@dataclass
class Cell:
    language: str
    state: CellState = CellState.PENDING
    origin: Optional[Origin] = None
    reason: str = ""                                        # one sentence, only when FAILED
    cues: Optional[List[Cue]] = field(default=None, repr=False)

    def to_json(self) -> dict:
        return {"language": self.language, "state": self.state.value,
                "origin": self.origin.label() if self.origin else None,
                "rung": self.origin.rung.value if self.origin else None, "reason": self.reason}

    def summary(self) -> str:
        if self.state == CellState.PASSED:
            return f"{self.language.upper()} ✓ {self.origin.label()}"
        if self.state == CellState.FAILED:
            return f"{self.language.upper()} failed: {self.reason}"
        return f"{self.language.upper()} {self.state.value}"


@dataclass
class VideoRun:
    """Everything one video's cells need, kept in memory so one cell can be retried without redoing the source."""
    path: str
    source: Source
    cells: Dict[str, Cell]

    def passed(self) -> Dict[str, List[Cue]]:
        return {lang: c.cues for lang, c in self.cells.items() if c.state == CellState.PASSED}

    def all_passed(self) -> bool:
        return all(c.state == CellState.PASSED for c in self.cells.values())

    def summary(self) -> str:
        return " · ".join(c.summary() for c in self.cells.values())

    def to_json(self) -> List[dict]:
        return [c.to_json() for c in self.cells.values()]


def produce_cell(cell: Cell, source: Source, translator: Translator,
                 on_batch: Optional[Callable[[int, int], None]] = None,
                 check_abort: Optional[Callable[[], None]] = None) -> None:
    """Fills one cell from the source: kept as is when it is already in that language, else translated.
    Sets PASSED with its origin, or FAILED with the one-sentence reason. JobAborted propagates."""
    cell.state, cell.reason, cell.cues = CellState.RUNNING, "", None
    if source.language == cell.language:
        rung = Rung.ASR if source.rung == Rung.ASR else Rung.EXISTING
        cell.cues, cell.origin, cell.state = source.cues, Origin(rung), CellState.PASSED
        return
    rung = Rung.ASR if source.rung == Rung.ASR else Rung.TRANSLATE
    cell.origin = Origin(rung, source.language)
    try:
        cell.cues = translate_cues(source.cues, cell.language, source.context, translator,
                                   on_batch=on_batch, check_abort=check_abort)
    except TranslationFailed as e:
        cell.state, cell.reason = CellState.FAILED, str(e)
        return
    cell.state = CellState.PASSED
