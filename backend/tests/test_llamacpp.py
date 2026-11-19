# test_llamacpp.py — LlamaCppTranslator against a fake llama-server: prompt, parsing by id, truncation, backend choice. Exports: none (pytest).

import json

import httpx
import pytest

from core.subtitles import BatchMismatch, Cue, TranslationContext, TranslationFailed, translate_cues
from core.translators import make_translator, source_language
from core.translators.llamacpp import LlamaCppTranslator

# The shape of TranslateGemma's real template (languages map, raise_exception on an unknown code), shortened.
TEMPLATE = (
    "{%- set languages = {'en': 'English', 'fr': 'French'} -%}"
    "{{ bos_token }}{%- for m in messages -%}{%- set c = m['content'][0] -%}"
    "{%- if c['target_lang_code'] not in languages -%}{{ raise_exception('unsupported ' + c['target_lang_code']) }}"
    "{%- endif -%}<start_of_turn>user\n{{ languages[c['source_lang_code']] }} to {{ languages[c['target_lang_code']] }}"
    ":\n\n\n{{ c['text'] | trim }}<end_of_turn>\n{%- endfor -%}<start_of_turn>model\n"
)


class FakeServer:
    """Answers /props and /completion like llama-server; `reply(prompt)` returns (content, stop_type)."""

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(200, json={"chat_template": TEMPLATE})
        body = json.loads(request.content)
        self.prompts.append(body["prompt"])
        content, stop = self.reply(body["prompt"])
        return httpx.Response(200, json={"content": content, "stop_type": stop})


def lines_of(prompt):
    return prompt.split("\n\n\n", 1)[1].split("<end_of_turn>")[0].splitlines()


def translator(reply):
    t = LlamaCppTranslator()
    fake = FakeServer(reply)
    t.http = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://test")
    t.server.ensure_running = lambda: "http://test"
    t.server.url = "http://test"
    return t, fake


def echo_upper(prompt):
    return "\n".join(f"{l.split('|', 1)[0]}|{l.split('|', 1)[1].upper()}" for l in lines_of(prompt)), "eos"


CTX = TranslationContext(title="t", source_lang="en")
CUES = [Cue(id=i, start_ms=i * 1000, end_ms=i * 1000 + 900, text=f"line {i}\nsecond row") for i in range(1, 24)]


def test_prompt_is_the_models_template_with_one_numbered_line_per_cue():
    t, fake = translator(echo_upper)
    t.translate([{"id": 7, "text": "Hello\nthere"}, {"id": 8, "text": "Bye"}], "fr", CTX)
    assert fake.prompts[0].startswith("<bos><start_of_turn>user\nEnglish to French:")
    assert lines_of(fake.prompts[0]) == ["7|Hello there", "8|Bye"]  # a cue's line break never splits its id line


def test_full_pipeline_keeps_every_id_and_timing():
    t, _ = translator(echo_upper)
    out = translate_cues(CUES, "fr", CTX, t, retry_delay_s=0)
    assert [(c.id, c.start_ms, c.end_ms) for c in out] == [(c.id, c.start_ms, c.end_ms) for c in CUES]
    assert out[0].text == "LINE 1 SECOND ROW"


def test_a_dropped_line_is_a_wrong_answer_and_the_batch_is_split():
    def drop_second(prompt):
        ls = lines_of(prompt)
        kept = ls if len(ls) == 1 else ls[:1] + ls[2:]
        return "\n".join(kept), "eos"
    t, fake = translator(drop_second)
    out = translate_cues(CUES[:4], "fr", CTX, t, retry_delay_s=0)
    assert [c.text for c in out] == [f"line {i} second row" for i in range(1, 5)]  # each cue alone came back
    assert len(fake.prompts) > 2


def test_reply_cut_at_the_token_limit_is_a_wrong_answer():
    t, _ = translator(lambda p: ("1|half", "limit"))
    with pytest.raises(BatchMismatch, match="token limit"):
        t.translate([{"id": 1, "text": "a"}], "fr", CTX)


@pytest.mark.parametrize("reply, why", [
    ("1|un\nSure, here is the translation", "unnumbered"),
    ("1|un\n1|encore", "twice"),
])
def test_chatter_or_duplicates_are_rejected(reply, why):
    t, _ = translator(lambda p: (reply, "eos"))
    with pytest.raises(BatchMismatch, match=why):
        t.translate([{"id": 1, "text": "one"}], "fr", CTX)


def test_unknown_source_language_fails_the_language_loudly_not_silently():
    t, fake = translator(echo_upper)
    with pytest.raises(TranslationFailed, match="source language unknown"):
        translate_cues(CUES[:2], "fr", TranslationContext(), t, retry_delay_s=0)
    assert fake.prompts == []


def test_a_language_the_model_does_not_know_raises_before_any_text_is_sent():
    t, fake = translator(echo_upper)
    with pytest.raises(ValueError, match="unsupported xx"):
        t.translate([{"id": 1, "text": "one"}], "xx", CTX)
    assert fake.prompts == []


def test_backend_choice(monkeypatch):
    monkeypatch.delenv("TRANSLATOR", raising=False)
    assert make_translator().name.startswith("llamacpp:")
    monkeypatch.setenv("TRANSLATOR", "gemini")
    assert make_translator().name.startswith("gemini:")
    monkeypatch.setenv("TRANSLATOR", "openai")
    with pytest.raises(ValueError):
        make_translator()


def test_source_language_detected_once_over_the_whole_file():
    cues = [Cue(id=1, start_ms=0, end_ms=1, text="♪")] + [
        Cue(id=i, start_ms=0, end_ms=1, text="Je ne sais pas ce que tu veux dire, mais c'est très bien.")
        for i in range(2, 6)]
    assert source_language(cues) == "fr"
    assert source_language([Cue(id=1, start_ms=0, end_ms=1, text="♪ ♪")]) is None
