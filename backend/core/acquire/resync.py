# resync.py — re-time a subtitle whose shift CHANGES part-way (made for another cut of the episode: a recap, an ad break, a trimmed scene): find where the shift jumps and give each segment its own. Exports: resync, Resync.
"""Pure function over cues and speech spans (ms), no binary, no model. ffsubsync applies ONE shift (plus a frame-rate
factor) to the whole file; a download made for another cut of the episode needs a different shift after each cut, and
the gate turns it down with « timing moves X s between the first and second half » (9 of the 13 rejected downloads
on Ghosts S05, bench/results/download.jsonl, 2026-11-19).

How: the dialogue cues are taken in blocks of BLOCK_CUES; each block is scored against the speech at every shift
within ±REACH_MS (frames of cue on speech). A Viterbi pass picks one shift per block, paying CHANGE_PENALTY frames for
every change, so a segment changes shift only where the speech says so clearly. Each change is then placed at the cue
boundary, between the two blocks, that fits best. Same principle as alass (piecewise shifts with a split penalty),
in numpy over the speech the gate already detected."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..gate.speech import Span
from ..gate.sync import FRAME_MS, dialogue_cues
from ..subtitles import Cue

BLOCK_CUES = 30           # ~1.5-2 min of dialogue: enough pauses to locate a block, small enough to isolate a cut
REACH_MS = 15_000         # a cut moves the timing by seconds (2.1-6.5 s measured), never by a quarter of a minute
CHANGE_PENALTY = 300      # frames (3 s of cue-on-speech) a change must win; set on the Ghosts S05 bench
MIN_SEGMENT_CUES = BLOCK_CUES


@dataclass(frozen=True)
class Resync:
    cues: List[Cue]                      # every input cue, re-timed (sound-only cues follow their neighbours)
    segments: List[Tuple[int, int]]      # (first cue start in the INPUT timing, shift ms) in time order


def _signal(spans: Sequence[Tuple[int, int]], n: int) -> np.ndarray:
    s = np.zeros(n, dtype=np.float32)
    for a, b in spans:
        i, j = max(0, a // FRAME_MS), min(n, b // FRAME_MS + 1)
        if j > i:
            s[i:j] = 1.0
    return s


def _scores(speech: np.ndarray, frames: Sequence[Tuple[int, int]], reach: int) -> np.ndarray:
    """score[lag + reach] = frames of these cues that land on speech once shifted by lag frames."""
    lo, hi = min(a for a, _ in frames), max(b for _, b in frames)
    cue = np.zeros(hi - lo, dtype=np.float32)
    for a, b in frames:
        cue[a - lo:b - lo] = 1.0
    pad = np.pad(speech, (reach, reach + hi))
    window = pad[lo:lo + (hi - lo) + 2 * reach]              # speech from lo - reach to hi + reach
    return np.correlate(window, cue, mode="valid")           # index k ↔ lag k - reach


def resync(cues: Sequence[Cue], speech: Sequence[Span], duration_ms: Optional[int] = None) -> Optional[Resync]:
    """None when there is too little dialogue to cut (under two blocks) or no speech. A result with one segment means
    one shift fits best everywhere: then this function has nothing to add to ffsubsync."""
    talk = sorted(dialogue_cues(cues), key=lambda c: c.start_ms)
    if len(talk) < 2 * MIN_SEGMENT_CUES or not speech:
        return None
    reach = REACH_MS // FRAME_MS
    end = max(duration_ms or 0, max(c.end_ms for c in talk), speech[-1][1])
    n = end // FRAME_MS + 1
    s = _signal(speech, n)
    frames = [(c.start_ms // FRAME_MS, max(c.start_ms // FRAME_MS + 1, c.end_ms // FRAME_MS)) for c in talk]

    blocks = [list(range(i, min(i + BLOCK_CUES, len(talk)))) for i in range(0, len(talk), BLOCK_CUES)]
    if len(blocks[-1]) < BLOCK_CUES // 2 and len(blocks) > 1:   # a short tail joins the block before it
        blocks[-2] += blocks.pop()
    table = np.stack([_scores(s, [frames[i] for i in b], reach) for b in blocks])   # blocks × lags

    # Viterbi: best total with one shift per block, CHANGE_PENALTY per change.
    total = table[0].copy()
    back = np.zeros(table.shape, dtype=np.int32)
    for k in range(1, len(blocks)):
        jump = int(np.argmax(total))
        stay = total
        moved = total[jump] - CHANGE_PENALTY
        back[k] = np.where(stay >= moved, np.arange(table.shape[1]), jump)
        total = np.maximum(stay, moved) + table[k]
    lag = [int(np.argmax(total))]
    for k in range(len(blocks) - 1, 0, -1):
        lag.append(int(back[k][lag[-1]]))
    lag.reverse()

    # One shift per cue: blocks keep theirs; at each change, the boundary inside the two blocks that fits best.
    shift = [lag[k] for k, b in enumerate(blocks) for _ in b]
    for k in range(1, len(blocks)):
        if lag[k] == lag[k - 1]:
            continue
        lo, hi = blocks[k - 1][0], blocks[k][-1] + 1
        a, b = lag[k - 1] - reach, lag[k] - reach
        on = lambda i, l: float(s[max(0, frames[i][0] + l):max(0, min(n, frames[i][1] + l))].sum())
        left = np.cumsum([0.0] + [on(i, a) for i in range(lo, hi)])
        right = np.cumsum([0.0] + [on(i, b) for i in range(lo, hi)])
        best = max(range(1, hi - lo), key=lambda j: left[j] + (right[-1] - right[j]))
        for i in range(lo, hi):
            shift[i] = lag[k - 1] if i < lo + best else lag[k]
    shift_ms = [(l - reach) * FRAME_MS for l in shift]

    # Segments in the input timing; every input cue (sound-only too) takes the shift of the dialogue cue at or before it.
    segments: List[Tuple[int, int]] = []
    for c, sh in zip(talk, shift_ms):
        if not segments or segments[-1][1] != sh:
            segments.append((c.start_ms, sh))
    segments[0] = (0, segments[0][1])
    out: List[Cue] = []
    for c in sorted(cues, key=lambda c: c.start_ms):
        sh = next(v for start, v in reversed(segments) if c.start_ms >= start or start == 0)
        out.append(replace(c, start_ms=max(0, c.start_ms + sh), end_ms=max(0, c.end_ms + sh)))
    # Where the shift drops, the end of one segment can now overlap the next one's start: end it just before.
    for i in range(len(out) - 1):
        if out[i].end_ms > out[i + 1].start_ms:
            out[i] = replace(out[i], end_ms=max(out[i].start_ms + 1, out[i + 1].start_ms - 1))
    return Resync(out, segments)
