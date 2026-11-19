# translators — picks the translation backend: local llama.cpp by default, Gemini opt-in. Exports: make_translator.

import os


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
