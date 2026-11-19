# text.py — Is a subtitle a good file? Language of the text, reading speed, broken encoding, duplicates, translated id set and lines left untranslated. Exports: TextMeasure, measure_text, detect_language, missing_ids, untranslated.
"""Pure functions over cues. Nothing here asks a model for an opinion: each value is counted."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set

from ..subtitles import Cue
from .sync import dialogue_cues

# Text decoded with the wrong code page: U+FFFD, UTF-8 read as cp1252 (« Ã© », « â€™ »), or a « ? » inside a word
# where an accent stood (« caf? »).
# Built from code points: a literal U+FFFD in source would trip the « no U+FFFD after an edit » damage check.
_BROKEN = re.compile("|".join([
    re.escape(chr(0xFFFD)),
    chr(0xC3) + "[" + chr(0x80) + "-" + chr(0xBF) + "]",   # Ã followed by a continuation byte read as cp1252
    chr(0xE2) + chr(0x20AC),                               # â€ : the start of ’ “ ” – read as cp1252
    r"[^\W\d_]\?[^\W\d_]",
]))
# Speaker labels and SDH tags are not read as words; they would not change the language but do skew short cues.
_LABEL = re.compile(r"^[A-Z][A-Z .'-]{1,30}:\s*|[\[(][^\])]*[\])]")


@dataclass(frozen=True)
class TextMeasure:
    language: Optional[str]     # ISO 639-1 detected from the dialogue text, None when undecidable
    language_share: float       # langdetect's probability for that language (0-1)
    within_cps: float           # share of dialogue cues read at ≤ the cps limit (0-1)
    long_lines: int             # lines over the length limit
    broken_chars: int           # signs of a wrong decoding
    duplicates: int             # consecutive cues with the same text
    cues: int


def detect_language(texts: Iterable[str]) -> tuple[Optional[str], float]:
    from langdetect import DetectorFactory, LangDetectException, detect_langs
    DetectorFactory.seed = 0  # langdetect is random unless seeded
    sample = " ".join(_LABEL.sub(" ", t) for t in texts)[:20_000]
    try:
        best = detect_langs(sample)[0]
    except LangDetectException:
        return None, 0.0
    return best.lang.split("-")[0], round(best.prob, 3)


def measure_text(cues: Sequence[Cue], max_cps: float, max_line: int) -> TextMeasure:
    talk = dialogue_cues(cues)
    if not talk:
        raise ValueError("no dialogue cue to read")
    lang, share = detect_language(c.text for c in talk)
    # Characters per second over the whole cue, line breaks excluded: the reading-speed measure caption guides use.
    within = sum(1 for c in talk if len(c.text.replace("\n", "")) / ((c.end_ms - c.start_ms) / 1000) <= max_cps)
    long_lines = sum(1 for c in cues for line in c.text.split("\n") if len(line) > max_line)
    broken = sum(len(_BROKEN.findall(c.text)) for c in cues)
    dups = sum(1 for a, b in zip(cues, cues[1:]) if a.text.strip() == b.text.strip())
    return TextMeasure(lang, share, round(within / len(talk), 3), long_lines, broken, dups, len(cues))


def untranslated(source: Sequence[Cue], translated: Sequence[Cue]) -> List[int]:
    """Ids whose translated text is still the source text. Compared by id, exactly (whitespace and case aside):
    one detection over the whole file would still call a track with 20 % English lines « French ». Lines under
    3 words are exempt, since a name or « OK. » is often the same in both languages."""
    src = {c.id: " ".join(c.text.split()).lower() for c in source}
    return [c.id for c in translated
            if len(c.text.split()) >= 3 and src.get(c.id) == " ".join(c.text.split()).lower()]


def missing_ids(source_ids: Iterable[int], translated: Sequence[Cue]) -> Set[int]:
    """Source ids with no translated cue, plus ids the translation invented: both must be empty."""
    src, out = set(source_ids), {c.id for c in translated}
    return (src - out) | (out - src)
