# asr_result.py — Typed ASR output (language + confidence, VAD speech, scored segments) and its SRT rendering. Exports: AsrWord, AsrSegment, AsrResult, format_timestamp.
"""
The quality gate (doc/user-process.md §3) and the rung-4 correction pass need more than SRT text: the detected
language with its probability, where the speech is (VAD), and how sure the model was of each segment. Everything
is in seconds (float) from the start of the video's audio.
"""
from dataclasses import dataclass, field
from typing import List, Tuple

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
    duration: float                    # whole audio track
    speech: List[Tuple[float, float]]  # VAD speech spans over the whole track (the gate's "synced/complete" base)
    segments: List[AsrSegment]

    def to_srt(self) -> str:
        """Cues built from word timestamps: break on sentence end or past MAX_CHARS_TOTAL."""
        words = [w for s in self.segments for w in s.words if w.text.strip()]
        blocks, chunk, chars = [], [], 0
        for w in words:
            t = w.text.strip()
            chunk.append(w)
            chars += len(t) + 1
            if t.endswith((".", "!", "?")) or chars > MAX_CHARS_TOTAL:
                blocks.append(_srt_block(len(blocks) + 1, chunk))
                chunk, chars = [], 0
        if chunk:
            blocks.append(_srt_block(len(blocks) + 1, chunk))
        return "\n".join(blocks)


def format_timestamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def _srt_block(index: int, words: List[AsrWord]) -> str:
    """One cue, two lines at most, broken at the space nearest the middle."""
    text = " ".join(w.text.strip() for w in words)
    if len(text) > MAX_CHARS_LINE:
        spaces = [i for i, c in enumerate(text) if c == " "]
        if spaces:
            cut = min(spaces, key=lambda i: abs(i - len(text) // 2))
            text = text[:cut] + "\n" + text[cut + 1:]
    return f"{index}\n{format_timestamp(words[0].start)} --> {format_timestamp(words[-1].end)}\n{text}\n"
