# settings_store.py — Persists the UI Settings as JSON under STATE_DIR. Exports: load_settings, save_settings, provider_status.

import json
import logging
import os
import threading
from typing import List

import langcodes
from pydantic import ValidationError

from core.provenance import OV

from .config import STATE_DIR
from .schemas import ProviderStatus, Settings

logger = logging.getLogger("SubStudio.Settings")

_LOCK = threading.Lock()
_FILE = STATE_DIR / "settings.json"
DEFAULT = Settings(languages=[OV, "en", "fr"])  # D-0002: « Original Version, English, French »


def normalise_language(tag: str) -> str:
    """'eng' / 'fre' / 'FR' -> 'fr', 'OV' -> 'ov'; raises ValueError on a tag that is not a language."""
    if tag.strip().lower() == OV:
        return OV
    try:
        code = langcodes.Language.get(tag.strip()).language
    except ValueError:
        code = None
    if not code or len(code) != 2 or not langcodes.tag_is_valid(code):
        raise ValueError(f"'{tag}' is not a language code")
    return code


def load_settings() -> Settings:
    with _LOCK:
        if not _FILE.exists():
            return DEFAULT
        try:
            return Settings.model_validate_json(_FILE.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as e:
            # A corrupt file must not silently become the defaults: the user would think their
            # list was saved. Fail the request so the UI shows the error.
            logger.error(f"Settings file unreadable ({_FILE}): {e}")
            raise RuntimeError(f"Settings file is unreadable: {_FILE}") from e


def save_settings(settings: Settings) -> Settings:
    languages: List[str] = []
    for tag in settings.languages:
        code = normalise_language(tag)
        if code not in languages:
            languages.append(code)
    clean = settings.model_copy(update={"languages": languages})

    with _LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(clean.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, _FILE)  # atomic: a crash mid-write leaves the old file, never half a file
    return clean


def provider_status() -> List[ProviderStatus]:
    # Only providers the code actually calls today; each new provider task adds its own line.
    # The local translator's paths come from the translator itself, so this line cannot drift from what it runs.
    from core.translators.llamacpp import LlamaCppTranslator
    local = LlamaCppTranslator().server
    env = os.environ
    return [
        ProviderStatus(name="Local translator (TranslateGemma 4B, llama.cpp)",
                       configured=os.path.isfile(local.model) and os.path.isfile(local.binary)),
        ProviderStatus(name="OpenSubtitles.com account (optional subtitle downloads)",
                       configured=bool(env.get("OPENSUBTITLES_USERNAME", "").strip() and env.get("OPENSUBTITLES_PASSWORD", "").strip())),
        ProviderStatus(name="Gemini (optional cloud translation)", configured=bool(env.get("GEMINI_API_KEY", "").strip())),
    ]
