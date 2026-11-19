# llamacpp.py — LlamaCppTranslator: the default, local, free backend of the Translator contract (TranslateGemma on llama.cpp). Exports: LlamaCppTranslator.
"""
Free and local (D-0001), chosen on the bench (D-0003). One call translates one batch of cues sent as numbered lines
`id|text`; the answer is parsed back by id. translate_cues checks the id set, retries, splits and re-attaches timings.

The prompt is the model's own chat template, read from the running server (/props) and rendered here, because
llama.cpp's chat endpoint cannot pass TranslateGemma's source_lang_code/target_lang_code fields. A language the
template does not know raises from the template itself, before any text is sent.
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import jinja2

from ..subtitles import BatchMismatch, CueText, TranslationContext
from .llama_server import LlamaServer

logger = logging.getLogger("SubStudio.LocalTranslator")

_HOME = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "SubStudio"
_LINE = re.compile(r"^\s*\[?(\d+)\]?\s*[|:.)\]]\s*(.*)$")


class LlamaCppTranslator:
    batch_size = 10  # bench 2026-11-19: 15/15 batches of 10 came back with exactly their ids (D-0003)

    def __init__(self):
        model = os.getenv("LLAMA_MODEL_PATH", str(_HOME / "models" / "translategemma-4b-it-Q4_K_M.gguf"))
        self.server = LlamaServer(
            binary=os.getenv("LLAMA_SERVER_BIN", str(_HOME / "llama.cpp" / "b12532-vulkan" / "llama-server.exe")),
            model=model,
            port=int(os.getenv("LLAMA_PORT", "8092")),
            gpu_layers=int(os.getenv("LLAMA_GPU_LAYERS", "99")),  # the iGPU: same speed, CPU left free (D-0003)
            log_dir=_HOME / "logs",
        )
        self.name = f"llamacpp:{Path(model).stem}"
        self._template: Optional[jinja2.Template] = None
        self.http = httpx.Client(timeout=600)

    def get_context_profile(self, filename: str) -> str:
        # A 4B translation model asked « what show is this? » invents a cast (architecture review §4.3);
        # looked-up context replaces it (user-process.md rung 3). Until then: no invented context.
        return ""

    def unload(self) -> None:
        """Free the model's RAM before the next video's transcription (one heavy model at a time, D-0001)."""
        self.server.stop()

    def translate(self, cues: List[CueText], target: str, context: TranslationContext) -> List[CueText]:
        source = context.source_lang
        if not source:
            # TranslateGemma's prompt names the source language; guessing it per batch fails on a « ♪ »-only batch.
            raise RuntimeError("source language unknown: the orchestrator sets TranslationContext.source_lang")
        url = self.server.ensure_running()
        # A cue's own line breaks are layout, not meaning: one line per cue keeps the id prefix parseable,
        # and wrap_lines re-wraps the translation afterwards.
        text = "\n".join(f"{c['id']}|{' '.join(c['text'].split())}" for c in cues)
        prompt = self._render(text, source, target)
        r = self.http.post(f"{url}/completion", json={
            "prompt": prompt, "temperature": 0, "cache_prompt": True,
            "n_predict": 64 + 2 * len(text),  # French runs ~1.2x English; twice the characters is a safe ceiling
        })
        if r.status_code != 200:
            raise RuntimeError(f"llama-server HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("stop_type") == "limit":
            # Cut at the token limit: a wrong answer, so translate_cues splits the batch instead of resending it.
            raise BatchMismatch("reply cut at the output token limit (stop_type=limit)")
        return self._parse(data.get("content", ""))

    def _render(self, text: str, source: str, target: str) -> str:
        if self._template is None:
            props = self.http.get(f"{self.server.url}/props", timeout=10).json()
            tpl = props.get("chat_template")
            if not tpl:
                raise RuntimeError("llama-server /props has no chat_template: wrong model file?")
            env = jinja2.Environment()

            def fail(message):
                raise ValueError(message)
            env.globals["raise_exception"] = fail
            self._template = env.from_string(tpl)
        msg = {"role": "user", "content": [
            {"type": "text", "source_lang_code": source, "target_lang_code": target, "text": text}]}
        return self._template.render(messages=[msg], add_generation_prompt=True, bos_token="<bos>")

    @staticmethod
    def _parse(reply: str) -> List[CueText]:
        out: Dict[int, str] = {}
        for line in reply.splitlines():
            m = _LINE.match(line)
            if not m:
                if line.strip():
                    raise BatchMismatch(f"unnumbered line in the reply: {line.strip()[:60]!r}")
                continue
            i = int(m.group(1))
            if i in out:
                raise BatchMismatch(f"id {i} answered twice")
            out[i] = m.group(2).strip()
        return [{"id": i, "text": t} for i, t in out.items()]
