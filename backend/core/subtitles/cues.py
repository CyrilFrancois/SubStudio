# cues.py — Cue: the one typed subtitle unit; read (charset-detected), shift, clip, wrap, write SRT. Exports: Cue, CueText, SubtitleReadError, load_file, parse, to_srt, shift, drop_after, wrap_lines.
"""
Every stage of the pipeline passes a list of Cue, never an SRT string. Timings are integers in
MILLISECONDS and only this module turns them into text, so no other file parses or prints a
timestamp. Cue ids are 1..n in order and never change after the source is read: the translator
matches its answers to the source by id, never by position in its reply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, TypedDict

import pysubs2
from charset_normalizer import from_bytes

logger = logging.getLogger("SubStudio.Subtitles")

# Sidecars are user files of any size; a real subtitle file is well under 1 MB.
MAX_SUBTITLE_BYTES = 10 * 1024 * 1024


class SubtitleReadError(ValueError):
    """The file could not be read as a subtitle with at least one cue."""


@dataclass(frozen=True)
class Cue:
    id: int
    start_ms: int
    end_ms: int
    text: str  # lines separated by "\n"; no formatting tags


class CueText(TypedDict):
    """What a translator sees and returns: an id and a text, never a timing."""
    id: int
    text: str


def _from_ssafile(subs: pysubs2.SSAFile) -> List[Cue]:
    cues: List[Cue] = []
    for ev in subs:
        if ev.is_comment:
            continue
        # plaintext drops {\i1}-style tags and turns \N into a newline.
        text = "\n".join(line.strip() for line in ev.plaintext.split("\n")).strip()
        if not text:
            continue
        cues.append(Cue(id=len(cues) + 1, start_ms=max(0, ev.start), end_ms=max(0, ev.end), text=text))
    return cues


def parse(text: str, fmt: Optional[str] = None) -> List[Cue]:
    """Parses subtitle TEXT (SRT, VTT, ASS…; detected when fmt is None) into cues numbered 1..n."""
    try:
        subs = pysubs2.SSAFile.from_string(text, format_=fmt)
    except Exception as e:
        raise SubtitleReadError(f"not a readable subtitle ({type(e).__name__}: {e})") from e
    return _from_ssafile(subs)


# Statistical detection cannot tell the Latin code pages apart: charset-normalizer 3.5 reads cp1252 French and
# German as cp1250 (« Très » → « Trčs »), even on long files (measured 2026-11-19). A legacy subtitle's code page
# follows its language, and the language is detected the same under either decoding, so it decides.
_LATIN_GUESSES = {"cp1250", "cp1252", "cp1257", "latin_1", "iso8859_2", "iso8859_10", "iso8859_15", "cp850",
                  "cp852", "cp775", "hp_roman8", "mac_roman", "mac_latin2"}
_CP1250_LANGS = {"cs", "sk", "pl", "hu", "sl", "hr", "ro", "sq", "bs"}
_CP1257_LANGS = {"lt", "lv", "et"}


def _latin_codepage(raw: bytes) -> Optional[str]:
    from langdetect import DetectorFactory, LangDetectException, detect
    DetectorFactory.seed = 0  # langdetect is random unless seeded
    try:
        lang = detect(raw.decode("cp1252", errors="replace"))
    except LangDetectException:
        return None
    return "cp1250" if lang in _CP1250_LANGS else "cp1257" if lang in _CP1257_LANGS else "cp1252"


def decode(raw: bytes) -> str:
    """UTF-8 first (strict, BOM allowed); else the detected charset. Never drops a character silently."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw).best()
    if best is None:
        raise SubtitleReadError("unknown text encoding")
    encoding = best.encoding
    if encoding in _LATIN_GUESSES:
        encoding = _latin_codepage(raw) or encoding
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding, text = best.encoding, str(best)
    logger.info(f"Subtitle is not UTF-8; decoded as {encoding}")
    return text


def load_file(path: Path | str) -> List[Cue]:
    """Reads a subtitle file of any common format and encoding. Raises SubtitleReadError when it holds no cue."""
    p = Path(path)
    size = p.stat().st_size
    if size > MAX_SUBTITLE_BYTES:
        raise SubtitleReadError(f"{p.name} is {size} bytes, over the {MAX_SUBTITLE_BYTES}-byte limit")
    cues = parse(decode(p.read_bytes()))
    if not cues:
        raise SubtitleReadError(f"{p.name} holds no subtitle cue")
    return cues


def _srt_time(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def to_srt(cues: List[Cue]) -> str:
    """SRT text, renumbered 1..n in output order (players need consecutive indices, not our ids)."""
    blocks = [f"{n}\n{_srt_time(c.start_ms)} --> {_srt_time(c.end_ms)}\n{c.text}\n"
              for n, c in enumerate(cues, start=1)]
    return "\n".join(blocks)


def shift(cues: List[Cue], offset_seconds: float) -> List[Cue]:
    """Positive delays, negative advances; clamped at zero. A cue pushed wholly before 0 is dropped."""
    if offset_seconds == 0:
        return list(cues)
    d = round(offset_seconds * 1000)
    out = []
    for c in cues:
        start, end = max(0, c.start_ms + d), max(0, c.end_ms + d)
        if end > start:
            out.append(replace(c, start_ms=start, end_ms=end))
    return out


def drop_after(cues: List[Cue], duration_seconds: float) -> List[Cue]:
    """Removes cues starting at or after the end of the video (a player holds a black frame for them)."""
    limit = duration_seconds * 1000
    return [c for c in cues if c.start_ms < limit]


def _wrap_line(line: str, max_chars: int) -> str:
    if len(line) <= max_chars:
        return line
    spaces = [i for i, ch in enumerate(line) if ch == " "]
    if not spaces:
        return line
    mid = len(line) // 2
    cut = min(spaces, key=lambda i: abs(i - mid))
    return line[:cut] + "\n" + line[cut + 1:]


def wrap_lines(cues: List[Cue], max_chars: int = 50) -> List[Cue]:
    """Splits each line longer than max_chars at the space nearest its middle."""
    return [replace(c, text="\n".join(_wrap_line(l, max_chars) for l in c.text.split("\n"))) for c in cues]
