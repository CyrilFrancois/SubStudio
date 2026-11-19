# subs.py — Reads a subtitle file (SRT, ASS, VTT) into timed plain-text cues, decoding legacy files by their language. Exports: Cue, load_cues, legacy_codepage.
import re
from dataclasses import dataclass
from pathlib import Path

import pysubs2

# Statistics cannot tell French cp1252 from Polish cp1250 (both decode, measured 2026-11-19: charset-normalizer
# picked mac_latin2 then cp1250 for French), so the language decides. ISO 639-1 -> the Windows code page that
# subtitle files in that language use when they are not UTF-8.
_CODEPAGES = {
    "cp1252": "en fr de es it pt nl da sv no nb nn fi is ca eu gl ga af id ms sw",
    "cp1250": "pl cs sk hu ro hr sl bs sq",
    "cp1251": "ru uk bg sr mk be",
    "cp1253": "el",
    "cp1254": "tr az",
    "cp1255": "he",
    "cp1256": "ar fa ur",
    "cp1257": "et lv lt",
    "cp1258": "vi",
    "cp874": "th",
    "cp932": "ja",
    "gb18030": "zh",
    "cp949": "ko",
}
_LANG_TO_CP = {lang: cp for cp, langs in _CODEPAGES.items() for lang in langs.split()}


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def legacy_codepage(lang: str) -> str:
    cp = _LANG_TO_CP.get(lang.lower().split("-")[0])
    if cp is None:
        raise ValueError(f"no legacy code page known for language {lang!r}; add it to subs._CODEPAGES")
    return cp


def _lang_from_name(path: Path) -> str | None:
    # "movie.fr.srt" -> "fr"; "ref.pt-BR.srt" -> "pt-BR"
    m = re.search(r"\.([a-z]{2}(?:-[A-Za-z]{2})?)\.[^.]+$", path.name)
    return m.group(1) if m else None


def _decode(raw: bytes, lang: str | None) -> tuple[str, str]:
    # UTF-8 first and strictly: a valid UTF-8 file is never re-guessed.
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    if not lang:
        # Guessing garbled a cp1252 French file as cp1250 (« œuvre » -> « śuvre »), which would quietly lower every
        # candidate's score: refuse instead.
        raise ValueError("not UTF-8 and no language: pass lang (--lang) or name the file like ref.fr.srt")
    cp = legacy_codepage(lang)
    return raw.decode(cp), cp  # strict: a byte the page does not define raises rather than vanishing


def load_cues(path: Path, lang: str | None = None) -> tuple[list[Cue], str]:
    """Cues in time order, tags stripped, lines joined by spaces, empty cues dropped; plus the encoding read.

    `lang` (ISO 639-1) picks the legacy code page for a non-UTF-8 file; it defaults to the one in the file name.
    """
    path = Path(path)
    text, encoding = _decode(path.read_bytes(), lang or _lang_from_name(path))
    events = pysubs2.SSAFile.from_string(text).events
    cues = []
    for e in sorted(events, key=lambda e: e.start):
        if e.is_comment:
            continue
        plain = " ".join(e.plaintext.split())
        if plain:
            cues.append(Cue(e.start, e.end, plain))
    return cues, encoding
