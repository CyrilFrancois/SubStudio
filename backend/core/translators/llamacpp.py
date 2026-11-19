# llamacpp.py — LlamaCppTranslator: the default, local, free backend of the Translator contract (TranslateGemma on llama.cpp). Exports: LlamaCppTranslator.
"""
Free and local (D-0001), chosen on the bench (D-0003). One call translates one batch of cues sent as numbered lines
`id|text`; the answer is parsed back by id. translate_cues checks the id set, retries, splits and re-attaches timings.
A batch of ONE cue is sent as plain text, with no id: its id is known, so its answer cannot be misnumbered. That is
where a split ends, so the reply format can never be the reason a cue stays untranslated.

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

from ..subtitles import BatchMismatch, CueText, TranslationContext, TranslationFailed
from .llama_server import LlamaServer

logger = logging.getLogger("SubStudio.LocalTranslator")

_HOME = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "SubStudio"
# The id, then a separator: « | : . ) ] / – — » or « -> », or only whitespace (NBSP and U+202F included, which French
# typography puts before « ? ») when the text then starts like a sentence. Shapes seen on Ghosts S05E12 FR
# (2026-11-19): « 69/afin que… », « 200 le contenu… », « 30– et j'ai construit… ». A plain « - » is not one: it
# opens a dialogue line (« 12 - Oui »), kept as text. « 272\xa0? Une chambre\xa0? » is NOT read as cue 272's text
# (a reply starting « ? » is not a translation); it stays a wrong answer, so the batch splits down to a plain cue.
_LINE = re.compile(r"^\s*\[?(\d+)\]?(?:\s*(?:[|:.)\]/–—]|->)\s*|\s+(?=[^\W\d_]|[«\"“'¿¡-]))(.*)$")
# A one-cue answer is sent no id; one still put in front, with an explicit separator, is dropped. A reply that starts
# with the cue's number and then punctuation (« 272\xa0? Une chambre ») is a wrong answer, never a translation.
_ONE_ID = r"^\s*\[?{id}\]?\s*(?:[|:.)\]/]|->)\s*"
_ONE_BAD = r"^\s*{id}\s*[^\w\s«\"“'¿¡-]"
_DASH = re.compile(r"^\s*[-–—]\s*")
_SPEAKER_SPLIT = re.compile(r"\s+[-–—]\s+")


def _dialogue_lines(text: str) -> int:
    """Number of speakers when a cue is a dialogue (every line starts with a dash), else 0."""
    lines = [l for l in text.splitlines() if l.strip()]
    return len(lines) if len(lines) >= 2 and all(_DASH.match(l) for l in lines) else 0


def _restore_dialogue(text: str, speakers: int) -> str:
    # A cue goes to the model as one line, so « - Yeah I saw.\n- Did you? » comes back as « Oui. – Vous ? »
    # (measured live 2026-11-19). The layout is computable from the source: one dashed line per speaker.
    # When the dashes do not split it into exactly that many parts, the text is left as the model wrote it.
    if not speakers:
        return text
    parts = _SPEAKER_SPLIT.split(_DASH.sub("", text))
    return "\n".join(f"- {p.strip()}" for p in parts) if len(parts) == speakers else text


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

    def unload(self) -> None:
        """Free the model's RAM before the next video's transcription (one heavy model at a time, D-0001)."""
        self.server.stop()

    def translate(self, cues: List[CueText], target: str, context: TranslationContext) -> List[CueText]:
        source = context.source_lang
        if not source:
            # TranslateGemma's prompt names the source language; guessing it per batch fails on a « ♪ »-only batch.
            # No retry can change it, so the language fails now, with the reason the user will read.
            raise TranslationFailed(f"{target}: the subtitle's own language could not be detected, and the local "
                                    "model needs it to translate")
        url = self.server.ensure_running()
        # A cue's own line breaks are layout, not meaning: one line per cue keeps the id prefix parseable,
        # and wrap_lines re-wraps the translation afterwards.
        single = len(cues) == 1
        text = (" ".join(cues[0]["text"].split()) if single
                else "\n".join(f"{c['id']}|{' '.join(c['text'].split())}" for c in cues))
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
        reply = data.get("content", "")
        speakers = {c["id"]: _dialogue_lines(c["text"]) for c in cues}
        try:
            got = self._parse_one(reply, cues[0]["id"], speakers[cues[0]["id"]]) if single else self._parse(reply)
        except BatchMismatch:
            # The raw reply is the evidence a parser change is judged on: the warning above it shows one line only.
            logger.warning(f"Unparseable reply for cues {cues[0]['id']}-{cues[-1]['id']}: {reply[:600]!r}")
            raise
        return [{"id": t["id"], "text": _restore_dialogue(t["text"], speakers.get(t["id"], 0))} for t in got]

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
        """Numbered lines back to {id, text}. An unnumbered line BETWEEN two numbered ones continues the one before:
        the model wraps a long translation (« dans le bunker. », « à l'aéroport. » on S05E12). When that line was
        really the next cue's, its id is missing and translate_cues rejects the batch: the id set is the check.
        An unnumbered line before the first or after the last numbered line is chatter: a wrong answer."""
        out: Dict[int, str] = {}
        last: Optional[int] = None
        pending: List[str] = []  # unnumbered lines waiting for a numbered line to prove they were a continuation
        for line in reply.splitlines():
            if not line.strip():
                continue
            m = _LINE.match(line)
            if not m:
                if last is None:
                    raise BatchMismatch(f"unnumbered line in the reply: {line.strip()[:60]!r}")
                pending.append(line.strip())
                continue
            if pending:
                out[last] = " ".join([out[last], *pending]).strip()
                pending = []
            i = int(m.group(1))
            if i in out:
                raise BatchMismatch(f"id {i} answered twice")
            out[i] = m.group(2).strip()
            last = i
        if pending:
            raise BatchMismatch(f"unnumbered line after the last answer: {pending[0][:60]!r}")
        return [{"id": i, "text": t} for i, t in out.items()]

    @staticmethod
    def _parse_one(reply: str, cue_id: int, speakers: int) -> List[CueText]:
        """A one-cue answer is the translation itself, on one line, or one line per speaker of a dialogue cue.
        More lines is chatter (« Here is the translation: »); a leading « 272 ? » is a wrong answer. translate_cues
        then keeps the cue in its source language, named, rather than ship either."""
        lines = [l.strip() for l in reply.splitlines() if l.strip()]
        if lines:
            lines[0] = re.sub(_ONE_ID.format(id=cue_id), "", lines[0], count=1)
        if not lines or not lines[0] or re.match(_ONE_BAD.format(id=cue_id), lines[0]):
            raise BatchMismatch(f"not a translation of cue {cue_id}: {reply.strip()[:60]!r}")
        if len(lines) > max(1, speakers):
            raise BatchMismatch(f"{len(lines)} lines for one cue: {reply.strip()[:60]!r}")
        return [{"id": cue_id, "text": "\n".join(lines)}]
