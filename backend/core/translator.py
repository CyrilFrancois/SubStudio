# translator.py — SubtitleTranslator: story-bible context + batched SRT translation. Exports: SubtitleTranslator.

import os
import logging
import time
import httpx
from typing import Callable, Any, List, Optional

logger = logging.getLogger("SubStudio.Translator")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

class SubtitleTranslator:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        # Benchmarked 2026-11-08 on a 3-block SRT: ~2 s at thinkingLevel "low" vs ~5.5 s at the default.
        # thinkingLevel is a Gemini 3.x field — a 2.5 model override would need thinkingBudget instead.
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.http = httpx.Client(timeout=120)
        if not self.api_key:
            logger.error("GEMINI_API_KEY is not set — every translation request will fail.")

    def _generate(self, user_content: str, temperature: float, system_prompt: str = "") -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": {"temperature": temperature, "thinkingConfig": {"thinkingLevel": "low"}},
        }
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
        if reason != "STOP":
            raise RuntimeError(f"Gemini stopped with finishReason={reason}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            raise RuntimeError("Gemini returned an empty text")
        return text

    def get_context_profile(self, filename: str) -> str:
        """
        Research Phase: Identifies the show/movie to ensure character names 
        and gender-specific grammar are correct.
        """
        logger.info(f"AI searching filename context: {filename}")
        
        prompt = f"""Identify the media from this filename: '{filename}'
        Return a 'Story Bible' for a translator:
        - Series/Movie Title
        - Characters (Names, Genders, talking default)
        - Tone (Slang, Formal, Medical, Sci-Fi)
        - Brief Plot Context
        If unknown, infer from keywords."""

        try:
            return self._generate(prompt, temperature=0.3)
        except Exception as e:
            logger.error(f"   ❌ Context Research failed: {e}")
            return "Neutral media content. No specific character data."

    def refine_and_translate(
        self, 
        srt_content: str, 
        target_lang: str, 
        file_id: str, 
        on_progress: Callable, 
        task_manager: Any,
        context_profile: str,
        current_file: int,
        total_files: int,
        is_whisper_source: bool = False
    ) -> str:
        """
        Processes the SRT in batches to avoid token limits and maintain format.
        Includes terminal logging for every 20% of progress.
        """
        blocks = srt_content.strip().split('\n\n')
        batch_size = 30 
        results = []
        total_batches = (len(blocks) + batch_size - 1) // batch_size
        
        prefix = f"[{current_file}/{total_files} Files]"
        logger.info(f"Translating {len(blocks)} blocks into {target_lang.upper()}...")

        last_logged_pct = -1

        for i in range(0, len(blocks), batch_size):
            if task_manager.is_aborted:
                logger.warning(f"   🛑 {prefix} Translation aborted by user.")
                return ""

            batch = blocks[i : i + batch_size]
            batch_text = "\n\n".join(batch)
            current_batch_idx = (i // batch_size) + 1
            
            # Progress calculation for Frontend (stays within Step 4 range)
            progress_pct = 40 + int((current_batch_idx / total_batches) * 40)
            on_progress(file_id, "translating", progress_pct, 
                        f"{prefix} Step 4/5: Translating {target_lang.upper()} ({current_batch_idx}/{total_batches})")

            # Terminal logging every 20%
            completion_pct = int((current_batch_idx / total_batches) * 100)
            if completion_pct >= last_logged_pct + 20:
                logger.info(f"Translation Progress ({target_lang.upper()}): {completion_pct}%")
                last_logged_pct = (completion_pct // 20) * 20

            system_prompt = self._build_system_prompt(target_lang, context_profile, is_whisper_source)

            try:
                translated_batch = self._call_llm(system_prompt, batch_text)
                results.append(translated_batch)
            except Exception as e:
                logger.error(f"   ❌ {prefix} Batch {current_batch_idx} failed: {e}")
                results.append(batch_text) # Fallback to original

        return "\n\n".join(results)

    def _call_llm(self, system_prompt: str, user_content: str, retries=2) -> str:
        """Wrapper for the Gemini call with specific SRT formatting enforcement."""
        for attempt in range(retries + 1):
            try:
                content = self._generate(user_content, temperature=0.2, system_prompt=system_prompt)
                # Clean up AI formatting artifacts
                return content.replace("```srt", "").replace("```", "").strip()
            except Exception as e:
                if attempt == retries: raise e
                logger.warning(f"Gemini call failed (attempt {attempt + 1}/{retries + 1}): {e}")
                time.sleep(2)

    def _build_system_prompt(self, lang: str, context: str, is_whisper: bool) -> str:
        whisper_instruction = ""
        if is_whisper:
            whisper_instruction = """
            NOTE: Source is AI-transcribed and may have phonetic errors. 
            Use the provided context bible to fix character names and terms in the speech.
            No other information than the translation.
            """

        return f"""You are an expert subtitle translator ({lang}).
        {whisper_instruction}
        
        STORY BIBLE FOR CONTEXT:
        {context}

        RULES:
        1. Keep TIMESTAMPS and INDEX NUMBERS exactly as provided.
        2. Preserve SRT structure: [Index]\\n[Time] --> [Time]\\n[Text]\\n\\n
        3. Translate only the text content. Don't add the name of the people talking... focus only on the translation. And don't translate the names.
        4. Do not include any explanations or markdown.
        """