# review.py — Marks each line of a subtitle with what the quality gate holds against it, using the gate's own measures and limits. Exports: flag_cues.
"""The gate (core/gate) judges a whole track: « 6 % of lines too fast ». The Review screen needs the lines
themselves. Every rule here is the gate's per-line rule with the gate's THRESHOLDS, never a second copy of a
limit; tests/test_review.py checks the counts agree with core/gate/text.measure_text on the same cues."""

from typing import List

from core.gate import THRESHOLDS
from core.gate.sync import dialogue_cues
from core.gate.text import _BROKEN, untranslated
from core.subtitles import Cue as CoreCue

from .schemas import Cue, CueLimits, CueList


def flag_cues(cue_list: CueList) -> CueList:
    """Returns a copy with `flags` and `limits` set. Pure: no file, no model."""
    max_cps, max_line = THRESHOLDS["max_cps"], THRESHOLDS["max_line_chars"]
    core = [CoreCue(id=c.id, start_ms=c.start_ms, end_ms=c.end_ms, text=c.text) for c in cue_list.cues]
    talk = {c.id for c in dialogue_cues(core)}
    sources = [CoreCue(id=c.id, start_ms=c.start_ms, end_ms=c.end_ms, text=c.source_text)
               for c in cue_list.cues if c.source_text is not None]
    same_as_source = set(untranslated(sources, core)) if sources else set()

    out: List[Cue] = []
    prev = None
    for c in cue_list.cues:
        flags = []
        seconds = (c.end_ms - c.start_ms) / 1000
        if c.id in talk and len(c.text.replace("\n", "")) / seconds > max_cps:
            flags.append("too_fast")
        if any(len(line) > max_line for line in c.text.split("\n")):
            flags.append("long_line")
        if _BROKEN.search(c.text):
            flags.append("broken_text")
        if prev is not None and prev.text.strip() == c.text.strip():
            flags.append("duplicate")
        if c.id in same_as_source:
            flags.append("untranslated")
        out.append(c.model_copy(update={"flags": flags}))
        prev = c
    return cue_list.model_copy(update={"cues": out, "limits": CueLimits(max_cps=max_cps, max_line_chars=max_line)})
