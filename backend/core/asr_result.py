# asr_result.py — Typed ASR output (language + confidence, VAD speech, scored segments) and its cue list. Exports: AsrWord, AsrSegment, AsrResult.
"""
The quality gate (doc/user-process.md §3) and the rung-4 correction pass need more than cue text: the detected
language with its probability, where the speech is (VAD), and how sure the model was of each segment. Times here
are float seconds from the start of the audio, as Whisper gives them; to_cues() turns them into the pipeline's
integer-millisecond Cue list (core/subtitles/cues.py is the only place that writes a timestamp as text).
"""
from dataclasses import dataclass, field
from typing import List, Tuple

from .subtitles import Cue

MAX_CHARS_TOTAL = 70  # characters before a cue is forced to break
MAX_CHARS_LINE = 35   # target characters per line (two lines per cue at most)


@dataclass
class AsrWord:
    start: float
    end: float
    text: str
    probability: float


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str
    avg_logprob: float         # mean token log-probability; Whisper itself retries a segment below -1.0
    no_speech_prob: float      # probability the window held no speech; high + text = likely hallucination
    compression_ratio: float   # gzip ratio of the text; > 2.4 = repetitive loop (Whisper's own threshold)
    words: List[AsrWord] = field(default_factory=list)


@dataclass
class AsrResult:
    model: str
    language: str                      # ISO 639-1 as Whisper names it ("en", "fr")
    language_probability: float        # 0..1; 1.0 when the language was given, not detected
    duration: float                    # whole audio track, seconds
    speech: List[Tuple[float, float]]  # VAD speech spans over the whole track (the gate's "synced/complete" base)
    segments: List[AsrSegment]

    def to_cues(self) -> List[Cue]:
        """Cues built from word timestamps: break on sentence end or past MAX_CHARS_TOTAL; ids 1..n."""
        words = [w for s in self.segments for w in s.words if w.text.strip()]
        groups, chunk, chars = [], [], 0
        for w in words:
            t = w.text.strip()
            chunk.append(w)
            chars += len(t) + 1
            if t.endswith((".", "!", "?")) or chars > MAX_CHARS_TOTAL:
                groups.append(chunk)
                chunk, chars = [], 0
        if chunk:
            groups.append(chunk)
        return [Cue(id=i, start_ms=_ms(g[0].start), end_ms=max(_ms(g[0].start), _ms(g[-1].end)), text=_two_lines(g))
                for i, g in enumerate(groups, 1)]


def _ms(seconds: float) -> int:
    return int(round(max(0.0, seconds) * 1000))


def _two_lines(words: List[AsrWord]) -> str:
    """Two lines at most, broken at the space nearest the middle."""
    text = " ".join(w.text.strip() for w in words)
    if len(text) > MAX_CHARS_LINE:
        spaces = [i for i, c in enumerate(text) if c == " "]
        if spaces:
            cut = min(spaces, key=lambda i: abs(i - len(text) // 2))
            text = text[:cut] + "\n" + text[cut + 1:]
    return text
