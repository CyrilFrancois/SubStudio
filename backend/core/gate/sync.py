# sync.py — Is a subtitle on the speech? Offset and drift by cross-correlating 10 ms speech and cue signals, plus overlap and coverage. Exports: measure_sync, SyncMeasure, dialogue_cues.
"""Pure functions over cues and speech spans (ms). The offset is the shift to ADD to every cue so it best matches the
speech; ffsubsync uses the same principle (correlating speech activity), so the two can check each other."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

from ..subtitles import Cue
from .speech import Span

FRAME_MS = 10
MAX_OFFSET_MS = 60_000   # a release-to-release shift is seconds; a minute covers intros and ad breaks
COVER_PAD_MS = 1_000     # a cue covers speech up to 1 s around it: captions lead and trail the voice
MIN_HALF_CUES = 20       # below this, half a subtitle gives no trustworthy offset of its own
# What locates a half's offset is the pattern of PAUSES in its speech, not how many cues it has: with few speech
# spans, many lags within ±MAX_OFFSET_MS fit about as well and the argmax is noise. Measured 2026-11-19: the halves of
# the 15 cached Ghosts S05 episodes hold 71-161 speech spans; a 5-min clip whose dialogue barely pauses (S05E07
# 120-420 s) holds ~22 per half, and Whisper turbo's transcript of that very audio read « timing moves -53.5 s ».
# A peak-sharpness guard was tried instead and rejected: a frame-rate fault smears each half's peak too, so it hid
# the fault drift exists to catch (16 of 16 seeded 25/23.976 copies lost their drift reason, 4 passed).
MIN_HALF_SPEECH_SPANS = 40
# The whole-file offset has the same weakness. A 5-min clip whose speech barely pauses (~25 spans) read Whisper cues
# made from that very audio as shifted -59.9 / -5.9 / -51.0 s, and a copy shifted +3 s as +0.5 s (2026-11-19): there is
# no information to locate a shift. Below this the offset is « not measurable » (None), never a number; the halves of
# the 15 cached episodes hold 71-161 spans each, so every one of them stays measured.
MIN_SPEECH_SPANS = 2 * MIN_HALF_SPEECH_SPANS
# A cue that only describes a sound or music (« [laughs] », « (door closes) », « ♪ ») has no speech under it.
_SOUND_ONLY = re.compile(r"^\s*(?:[\[(][^\])]*[\])]\s*|[♪#*]+.*)+$", re.S)


def dialogue_cues(cues: Sequence[Cue]) -> List[Cue]:
    return [c for c in cues if c.end_ms > c.start_ms and not _SOUND_ONLY.match(c.text)]


@dataclass(frozen=True)
class SyncMeasure:
    offset_ms: Optional[int]  # shift that best aligns the cues to the speech; None when the speech has fewer than
                              # MIN_SPEECH_SPANS spans: not measurable, which is not « 0 »
    drift_ms: Optional[int]   # offset of the second half minus the first (a frame-rate mismatch grows over time);
                              # None when a half has fewer than MIN_HALF_CUES dialogue cues or fewer than
                              # MIN_HALF_SPEECH_SPANS speech spans: not measurable, which is not « 0 »
    peak_ratio: Optional[float]  # correlation at the best offset / at zero: how clearly the best one wins (None with
                                 # the offset)
    on_speech: float          # share of dialogue-cue time that lies on detected speech (0-1)
    speech_covered: float     # share of speech time that has a cue within COVER_PAD_MS (0-1)
    dialogue_cues: int


def _signal(spans: Sequence[Span], n: int, pad_ms: int = 0) -> np.ndarray:
    s = np.zeros(n, dtype=np.float32)
    for a, b in spans:
        i, j = max(0, (a - pad_ms) // FRAME_MS), min(n, (b + pad_ms) // FRAME_MS + 1)
        if j > i:
            s[i:j] = 1.0
    return s


def _best_offset(speech: np.ndarray, cue: np.ndarray, reach: int) -> tuple[int, float]:
    """Lag (frames) in [-reach, reach] maximising sum(speech[t] * cue[t - lag]); signals are centred so long
    silences do not dominate."""
    a, b = speech - speech.mean(), cue - cue.mean()
    size = 1 << int(np.ceil(np.log2(2 * len(a))))
    corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    lags = np.arange(-reach, reach + 1)
    values = corr[lags % size]  # a negative lag sits at the end of the circular correlation
    best = int(np.argmax(values))
    zero = float(corr[0])
    ratio = float(values[best] / zero) if zero > 0 else float("inf")
    return int(lags[best]), ratio


def _offset_ms(speech: Sequence[Span], cues: Sequence[Cue], n: int) -> tuple[int, float]:
    lag, ratio = _best_offset(_signal(speech, n), _signal([(c.start_ms, c.end_ms) for c in cues], n),
                              MAX_OFFSET_MS // FRAME_MS)
    return lag * FRAME_MS, ratio


def measure_sync(cues: Sequence[Cue], speech: Sequence[Span], duration_ms: Optional[int] = None) -> SyncMeasure:
    # Time order is not guaranteed (ASS events may be stored in any order); the halves and the end need it.
    talk = sorted(dialogue_cues(cues), key=lambda c: c.start_ms)
    if not talk:
        raise ValueError("no dialogue cue to measure")
    if not speech:
        raise ValueError("no speech detected: sync cannot be measured")
    end = max(duration_ms or 0, max(c.end_ms for c in talk), speech[-1][1])
    n = end // FRAME_MS + 1
    offset: Optional[int] = None
    ratio: Optional[float] = None
    if len(speech) >= MIN_SPEECH_SPANS:
        offset, ratio = _offset_ms(speech, talk, n)
        ratio = round(ratio, 3)

    half = (talk[0].start_ms + max(c.end_ms for c in talk)) // 2
    first = [c for c in talk if c.end_ms <= half]
    second = [c for c in talk if c.start_ms >= half]
    drift: Optional[int] = None  # not measurable: too few cues per half (forced subtitles, a short clip)
    spans_first = sum(1 for a, b in speech if b <= half)
    spans_second = sum(1 for a, b in speech if a >= half)
    if (len(first) >= MIN_HALF_CUES and len(second) >= MIN_HALF_CUES
            and min(spans_first, spans_second) >= MIN_HALF_SPEECH_SPANS):
        # Halves, not smaller parts: a sixth of an episode (~90 cues) locked onto wrong peaks a minute away on Ghosts
        # S05E07, halves (~260 cues) never did on the 17 S05 tracks; a frame-rate fault needs the full reach.
        drift = _offset_ms(speech, second, n)[0] - _offset_ms(speech, first, n)[0]

    speech_sig = _signal(speech, n)
    cue_sig = _signal([(c.start_ms, c.end_ms) for c in talk], n)
    on_speech = float((cue_sig * speech_sig).sum() / cue_sig.sum())
    padded = _signal([(c.start_ms, c.end_ms) for c in talk], n, COVER_PAD_MS)
    covered = float((padded * speech_sig).sum() / speech_sig.sum())
    return SyncMeasure(offset, drift, ratio, round(on_speech, 3), round(covered, 3), len(talk))
