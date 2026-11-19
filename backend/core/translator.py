# translator.py — GeminiTranslator: optional cloud backend of the Translator contract. Exports: GeminiTranslator.

import json
import logging
import os
from typing import List

import httpx

from .subtitles import BatchMismatch, CueText, TranslationContext

logger = logging.getLogger("SubStudio.Translator")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Gemini's structured output: the reply is parsed as JSON and must be a list of {id, text}.
# translate_cues still checks the id set; the schema only makes a well-formed answer likely.
_BATCH_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"}, "text": {"type": "STRING"}},
        "required": ["id", "text"],
    },
}


class GeminiTranslator:
    batch_size = 30

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        # Benchmarked 2026-11-08 on a 3-block SRT: ~2 s at thinkingLevel "low" vs ~5.5 s at the default.
        # thinkingLevel is a Gemini 3.x field — a 2.5 model override would need thinkingBudget instead.
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.name = f"gemini:{self.model}"
        self.http = httpx.Client(timeout=120)
        if not self.api_key:
            logger.error("GEMINI_API_KEY is not set — every translation request will fail.")

    def _generate(self, user_content: str, temperature: float, system_prompt: str = "", schema: dict = None) -> str:
        config = {"temperature": temperature, "thinkingConfig": {"thinkingLevel": "low"}}
        if schema:
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = schema
        body = {"contents": [{"role": "user", "parts": [{"text": user_content}]}], "generationConfig": config}
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        r = self.http.post(GEMINI_URL.format(model=self.model), json=body,
                           headers={"x-goog-api-key": self.api_key})
        if r.status_code != 200:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidate (blocked?): {data.get('promptFeedback')}")
        # MAX_TOKENS / SAFETY / RECITATION mean a partial or empty text: accepting it would ship a
        # silently truncated subtitle file, so it is an error, not a result.
        reason = candidates[0].get("finishReason")
        if reason == "MAX_TOKENS":
            # Too much text for one answer: a wrong answer, so translate_cues splits the batch instead of resending it.
            raise BatchMismatch("reply cut at the output token limit (finishReason=MAX_TOKENS)")
        if reason != "STOP":
            raise RuntimeError(f"Gemini stopped with finishReason={reason}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            raise RuntimeError("Gemini returned an empty text")
        return text

    def translate(self, cues: List[CueText], target: str, context: TranslationContext) -> List[CueText]:
        reply = self._generate(json.dumps(cues, ensure_ascii=False), temperature=0.2,
                               system_prompt=self._system_prompt(target, context), schema=_BATCH_SCHEMA)
        try:
            return json.loads(reply)
        except json.JSONDecodeError as e:
            raise BatchMismatch(f"reply is not JSON ({e})") from e

    def _system_prompt(self, lang: str, ctx: TranslationContext) -> str:
        parts = [
            f"You are an expert subtitle translator. Translate each subtitle into the language with ISO code '{lang}'.",
            "Input: a JSON array of {id, text}. Output: a JSON array with exactly one {id, text} per input id, "
            "the same ids, the text translated. Keep line breaks inside a text. Never merge, split or skip a "
            "subtitle. Do not add speaker names. Do not translate people's names.",
        ]
        if ctx.source_is_asr:
            parts.append("The source is machine-transcribed and may have phonetic errors: use the context to fix "
                         "names and terms.")
        if ctx.glossary:
            parts.append("Keep these untranslated: " + ", ".join(ctx.glossary))
        if ctx.notes:
            parts.append("STORY BIBLE FOR CONTEXT:\n" + ctx.notes)
        if ctx.previous:
            parts.append("The subtitles just before this batch (context only, do not output them):\n"
                         + "\n".join(ctx.previous))
        return "\n\n".join(parts)
