# translators — picks the translation backend (local llama.cpp by default, Gemini opt-in) and detects the source language. Exports: make_translator, source_language.

import logging
import os
from typing import List, Optional

from langdetect import DetectorFactory, LangDetectException, detect

from ..subtitles import Cue

logger = logging.getLogger("SubStudio.Translators")
DetectorFactory.seed = 0  # langdetect is random by default: the same text must give the same answer


def make_translator():
    """TRANSLATOR=local (default: free, on this PC, D-0001) or gemini (cloud, needs GEMINI_API_KEY, opt-in only)."""
    choice = os.getenv("TRANSLATOR", "local").strip().lower()
    if choice == "local":
        from .llamacpp import LlamaCppTranslator
        return LlamaCppTranslator()
    if choice == "gemini":
        from ..translator import GeminiTranslator
        return GeminiTranslator()
    raise ValueError(f"TRANSLATOR must be 'local' or 'gemini', not {choice!r}")


def source_language(cues: List[Cue]) -> Optional[str]:
    """ISO 639-1 code of the whole subtitle, detected once (a single batch can be only « ♪ »). None if undecidable."""
    sample = " ".join(c.text for c in cues[:400])
    try:
        # langdetect says zh-cn / zh-tw; TranslateGemma's template knows « zh » and raises on « zh-cn ».
        return detect(sample).split("-")[0]
    except LangDetectException as e:
        logger.warning(f"Could not detect the subtitle's language: {e}")
        return None
