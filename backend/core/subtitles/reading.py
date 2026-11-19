# reading.py — ease_reading: gives a too-fast cue more time on screen by extending its end into the silence before the next cue; never moves a start, never merges. Exports: ease_reading, MIN_GAP_MS, MAX_CUE_MS.
"""Why (S05E12, 2026-11-19): French is about 20 % longer than English, but a translation keeps its source's times by
id, and a transcript's times are Whisper's, tight to the speech. 9 % of the French lines were over the gate's 35
characters a second, so the language failed although every line was right. Captioning practice gives such a line
the pause after it: a viewer reads on after the voice stops.

Rules, all computed, none asked of a model:
- a start never moves (the line appears when it is spoken);
- an end only moves later, and stops MIN_GAP_MS before the next cue starts (no overlap, a visible change of line),
  at MAX_CUE_MS of display, and at the end of the audio;
- a cue already readable is left exactly as it was; ids, order and text never change, so everything keyed by id
  (Review's source lines, the translation cache, the untranslated-line record) still lines up.
Measured on the real S05E12 French: 91.2 % of lines readable → 96.7 % (the gate needs 95 %), share of cue time on
speech unchanged (0.931). Merging two cues would also work but breaks the 1:1 ids: not needed, so not done.
"""

from dataclasses import replace
from typing import List, Optional

from .cues import Cue

MIN_GAP_MS = 80        # two cues never touch: the eye sees the line change (common subtitling guides: 2-3 frames)
MAX_CUE_MS = 7000      # a line never stays longer than this (common subtitling guides: 6-7 s)


def _chars(c: Cue) -> int:
    return len(c.text.replace("\n", ""))


def ease_reading(cues: List[Cue], max_cps: float, audio_end_ms: Optional[int] = None,
                 min_gap_ms: int = MIN_GAP_MS, max_cue_ms: int = MAX_CUE_MS) -> List[Cue]:
    """Same cues, same ids and starts; each cue over `max_cps` ends later, as far as it needs and the gap allows.
    `cues` must be in time order (as SubStudio writes them)."""
    out: List[Cue] = []
    for i, c in enumerate(cues):
        need_ms = int(_chars(c) * 1000 / max_cps) + 1   # the shortest display at which this cue reads within max_cps
        if c.end_ms - c.start_ms >= need_ms:
            out.append(c)
            continue
        limit = c.start_ms + max_cue_ms
        if i + 1 < len(cues):
            limit = min(limit, cues[i + 1].start_ms - min_gap_ms)
        if audio_end_ms is not None:
            limit = min(limit, audio_end_ms)
        end = max(c.end_ms, min(c.start_ms + need_ms, limit))   # later or unchanged, never earlier
        out.append(c if end == c.end_ms else replace(c, end_ms=end))
    return out
