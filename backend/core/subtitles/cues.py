# cues.py — Cue: the one typed subtitle unit; read (charset-detected), shift, clip, wrap, write SRT. Exports: Cue, CueText, SubtitleReadError, load_file, parse, to_srt, shift, drop_after, wrap_lines, detect_language.
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


# Statistical detection cannot be trusted on Latin-script text: charset-normalizer 3.5 reads cp1252 French and
# German as cp1250 (« Très » → « Trčs ») even on long files, and a short French file as johab (Korean) (measured
# 2026-11-19). So the script is decided from the bytes, and for Latin-script text the code page from the language:
# a legacy file's code page follows its language, and the language is detected the same under any Latin decoding.
_CP1250_LANGS = {"cs", "sk", "pl", "hu", "sl", "hr", "ro", "sq", "bs"}
_CP1257_LANGS = {"lt", "lv", "et"}
_CP1254_LANGS = {"tr", "az"}


def _latin_codepage(raw: bytes) -> Optional[str]:
    """The Windows code page of a Latin-script text, or None when the text is not Latin script."""
    as_1252 = raw.decode("cp1252", errors="replace")
    letters = [ch for ch in as_1252 if ch.isalpha()]
    # Latin-script languages are mostly ASCII letters (French ~4 % accented, Czech ~20 %); Cyrillic, Greek or CJK
    # bytes read as cp1252 are almost all non-ASCII.
    if not letters or sum(ord(ch) > 127 for ch in letters) / len(letters) > 0.5:
        return None
    from langdetect import DetectorFactory, LangDetectException, detect
    DetectorFactory.seed = 0  # langdetect is random unless seeded
    try:
        lang = detect(as_1252)
    except LangDetectException:
        return "cp1252"
    return ("cp1250" if lang in _CP1250_LANGS else "cp1257" if lang in _CP1257_LANGS
            else "cp1254" if lang in _CP1254_LANGS else "cp1252")


def decode(raw: bytes) -> str:
    """UTF-8 first (strict, BOM allowed); else the detected charset. Never drops a character silently."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # UTF-16 (Notepad's « Unicode ») is mostly ASCII letters too, with a NUL byte between them: decide it first.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.count(b"\x00") * 4 > len(raw):
        even_nuls = raw[0::2].count(b"\x00")
        try:
            return raw.decode("utf-16-be" if even_nuls > len(raw) // 4 else "utf-16-le")
        except UnicodeDecodeError:
            raise SubtitleReadError("binary file, not a subtitle")
    encoding = _latin_codepage(raw)
    if encoding:
        try:
            text = raw.decode(encoding)
            logger.info(f"Subtitle is not UTF-8; decoded as {encoding}")
            return text
        except UnicodeDecodeError:
            pass  # a byte undefined in that code page: let detection decide
    best = from_bytes(raw).best()
    if best is None:
        raise SubtitleReadError("unknown text encoding")
    logger.info(f"Subtitle is not UTF-8; decoded as {best.encoding}")
    return str(best)


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
        if c.end_ms + d <= 0:
            continue
        out.append(replace(c, start_ms=max(0, c.start_ms + d), end_ms=c.end_ms + d))
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
    return _wrap_line(line[:cut], max_chars) + "\n" + _wrap_line(line[cut + 1:], max_chars)


def wrap_lines(cues: List[Cue], max_chars: int = 50) -> List[Cue]:
    """Splits each line longer than max_chars at the space nearest its middle, again until every piece fits."""
    return [replace(c, text="\n".join(_wrap_line(l, max_chars) for l in c.text.split("\n"))) for c in cues]


def detect_language(cues: List[Cue], sample: int = 300, min_probability: float = 0.95) -> Optional[str]:
    """ISO 639-1 of the cues' text (langdetect on up to `sample` cues), or None when there is too little text or the
    detection is not clear: a wrong answer would ship an untranslated source as « kept », None means « translate »."""
    from langdetect import DetectorFactory, LangDetectException, detect_langs
    DetectorFactory.seed = 0
    text = " ".join(c.text for c in cues[:sample] if "♪" not in c.text)
    if len(text.split()) < 20:
        return None
    try:
        best = detect_langs(text)[0]
    except (LangDetectException, IndexError):
        return None
    if best.prob < min_probability:
        return None
    return best.lang.split("-")[0]  # langdetect says zh-cn / zh-tw
