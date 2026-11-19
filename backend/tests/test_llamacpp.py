# test_llamacpp.py — LlamaCppTranslator against a fake llama-server: prompt, parsing by id, truncation, backend choice. Exports: none (pytest).

import http.server
import json
import socket
import sys
import threading

import httpx
import pytest

from core.subtitles import BatchMismatch, Cue, TranslationContext, TranslationFailed, translate_cues
from core.translators import make_translator
from core.translators.llama_server import LlamaServer
from core.translators.llamacpp import LlamaCppTranslator

# The shape of TranslateGemma's real template (languages map, raise_exception on an unknown code), shortened.
TEMPLATE = (
    "{%- set languages = {'en': 'English', 'fr': 'French'} -%}"
    "{{ bos_token }}{%- for m in messages -%}{%- set c = m['content'][0] -%}"
    "{%- for code in [c['source_lang_code'], c['target_lang_code']] -%}{%- if code not in languages -%}"
    "{{ raise_exception('unsupported ' + code) }}{%- endif -%}{%- endfor -%}<start_of_turn>user\n{{ languages[c['source_lang_code']] }} to {{ languages[c['target_lang_code']] }}"
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


def tag_fr(prompt):
    return "\n".join(f"{l.split('|', 1)[0]}|[fr] {l.split('|', 1)[1]}" for l in lines_of(prompt)), "eos"


CTX = TranslationContext(title="t", source_lang="en")
CUES = [Cue(id=i, start_ms=i * 1000, end_ms=i * 1000 + 900, text=f"line {i}\nsecond row") for i in range(1, 24)]


def test_prompt_is_the_models_template_with_one_numbered_line_per_cue():
    t, fake = translator(tag_fr)
    t.translate([{"id": 7, "text": "Hello\nthere"}, {"id": 8, "text": "Bye"}], "fr", CTX)
    assert fake.prompts[0].startswith("<bos><start_of_turn>user\nEnglish to French:")
    assert lines_of(fake.prompts[0]) == ["7|Hello there", "8|Bye"]  # a cue's line break never splits its id line


def test_full_pipeline_keeps_every_id_and_timing():
    t, _ = translator(tag_fr)
    out = translate_cues(CUES, "fr", CTX, t, retry_delay_s=0)
    assert [(c.id, c.start_ms, c.end_ms) for c in out] == [(c.id, c.start_ms, c.end_ms) for c in CUES]
    assert out[0].text == "[fr] line 1 second row"


def test_a_dropped_line_is_a_wrong_answer_and_the_batch_is_split():
    def drop_second(prompt):
        ls = lines_of(prompt)
        kept = ls if len(ls) == 1 else ls[:1] + ls[2:]
        return "\n".join(kept), "eos"
    t, fake = translator(drop_second)
    out = translate_cues(CUES[:4], "fr", CTX, t, retry_delay_s=0)
    assert [c.text for c in out] == [f"line {i} second row" for i in range(1, 5)]  # each cue alone came back
    assert len(fake.prompts) > 2


@pytest.mark.parametrize("reply, expected", [
    ("Oui, je l'ai vu. – Vous aussi ?", "- Oui, je l'ai vu.\n- Vous aussi ?"),   # the live answer of 2026-11-19
    ("- Oui. - Vraiment ?", "- Oui.\n- Vraiment ?"),
    ("Oui, je l'ai vu, vous aussi ?", "Oui, je l'ai vu, vous aussi ?"),         # no dash to split on: left as is
])
def test_a_two_speaker_cue_gets_one_dashed_line_per_speaker_back(reply, expected):
    t, _ = translator(lambda p: (f"5|{reply}", "eos"))
    out = t.translate([{"id": 5, "text": "- Yeah I saw.\n- Did you?"}], "fr", CTX)
    assert out == [{"id": 5, "text": expected}]


def test_a_dash_inside_one_speakers_line_is_left_alone():
    t, _ = translator(lambda p: ("5|Je suis - enfin - là.", "eos"))
    assert t.translate([{"id": 5, "text": "I'm - finally - here."}], "fr", CTX)[0]["text"] == "Je suis - enfin - là."


def test_reply_cut_at_the_token_limit_is_a_wrong_answer():
    t, _ = translator(lambda p: ("1|half", "limit"))
    with pytest.raises(BatchMismatch, match="token limit"):
        t.translate([{"id": 1, "text": "a"}], "fr", CTX)


@pytest.mark.parametrize("reply, why", [
    ("Sure, here is the translation\n1|un\n2|deux", "unnumbered"),
    ("1|un\n1|encore", "twice"),
    ("272\xa0? Une chambre\xa0?\n273|deux", "unnumbered"),  # S05E12: « ? » is not where a translation starts
    ("1|un\n2|deux\nSure, here is the translation.", "after the last answer"),  # never glued onto cue 2
])
def test_chatter_or_duplicates_are_rejected(reply, why):
    t, _ = translator(lambda p: (reply, "eos"))
    with pytest.raises(BatchMismatch, match=why):
        t.translate([{"id": 1, "text": "one"}, {"id": 2, "text": "two"}], "fr", CTX)


# Reply shapes TranslateGemma produced on Ghosts S05E12 FR (backend log, 2026-11-19), each one a split batch then.
@pytest.mark.parametrize("reply, expected", [
    ("68|Je vais bien.\n69/afin que je me sente mieux ?", {68: "Je vais bien.", 69: "afin que je me sente mieux ?"}),
    ("199|Il faut ouvrir\n200 le contenu, puis de remettre la boîte à sa place.",
     {199: "Il faut ouvrir", 200: "le contenu, puis de remettre la boîte à sa place."}),
    ("12\xa0: Bonjour.\n13\u202f| Salut.", {12: "Bonjour.", 13: "Salut."}),
    ("40|On l'a caché\ndans le bunker.\n41|Oui.", {40: "On l'a caché dans le bunker.", 41: "Oui."}),
    ("7|Bon,\n\xa0à propos de cela, j'ai écrit.\n8|Ok.", {7: "Bon, à propos de cela, j'ai écrit.", 8: "Ok."}),
    ("29|Je suis Hetty,\n30– et j'ai construit cette maison.", {29: "Je suis Hetty,", 30: "et j'ai construit cette maison."}),
    ("11|Oui.\n12 - Non.", {11: "Oui.", 12: "- Non."}),         # a hyphen after the id is a dialogue dash
])
def test_reply_shapes_seen_live_are_read_by_id(reply, expected):
    t, _ = translator(lambda p: (reply, "eos"))
    sent = [{"id": i, "text": "x"} for i in expected]
    assert {c["id"]: c["text"] for c in t.translate(sent, "fr", CTX)} == expected


def test_a_line_that_was_really_the_next_cue_never_ships_under_a_valid_id_set():
    # Between two answers, « dans le bunker. » joins the cue before; then cue 41 is missing and translate_cues
    # rejects the batch. After the last answer it is rejected at once: a continuation is never guessed there.
    t, _ = translator(lambda p: ("40|On l'a caché\ndans le bunker.\n42|Oui.", "eos"))
    sent = [{"id": i, "text": "x"} for i in (40, 41, 42)]
    assert [c["id"] for c in t.translate(sent, "fr", CTX)] == [40, 42]
    t, _ = translator(lambda p: ("40|On l'a caché\ndans le bunker.", "eos"))
    with pytest.raises(BatchMismatch, match="after the last answer"):
        t.translate(sent[:2], "fr", CTX)


def test_one_cue_is_sent_as_plain_text_and_its_answer_needs_no_id():
    t, fake = translator(lambda p: ("Une chambre\xa0?", "eos"))
    assert t.translate([{"id": 272, "text": "A room?"}], "fr", CTX) == [{"id": 272, "text": "Une chambre\xa0?"}]
    assert lines_of(fake.prompts[0]) == ["A room?"]
    t, _ = translator(lambda p: ("272|Une chambre ?", "eos"))  # an id the model adds anyway is dropped
    assert t.translate([{"id": 272, "text": "A room?"}], "fr", CTX) == [{"id": 272, "text": "Une chambre ?"}]
    t, _ = translator(lambda p: ("3 hommes sont venus.", "eos"))  # a translation starting with its own number
    assert t.translate([{"id": 3, "text": "3 men came."}], "fr", CTX) == [{"id": 3, "text": "3 hommes sont venus."}]


@pytest.mark.parametrize("reply", [
    "272\xa0? Une chambre\xa0?",                     # the S05E12 shape, alone: still not a translation
    "Here is the translation:\nUne chambre ?",       # chatter before
    "Une chambre ?\nI kept the meaning.",            # chatter after
    "",
])
def test_a_one_cue_answer_that_is_not_just_the_translation_is_a_wrong_answer(reply):
    t, _ = translator(lambda p: (reply, "eos"))
    with pytest.raises(BatchMismatch):
        t.translate([{"id": 272, "text": "A room?"}], "fr", CTX)


def test_the_s05e12_cue_that_failed_a_whole_language_now_translates():
    # In a batch the model answers cue 272 as « 272\xa0? Une chambre\xa0? »; alone, as plain text, it answers well.
    def model(prompt):
        ls = lines_of(prompt)
        if len(ls) == 1 and "|" not in ls[0]:
            return ("Une chambre ?" if ls[0] == "A room?" else f"[fr] {ls[0]}"), "eos"
        return "\n".join("272\xa0? Une chambre\xa0?" if l.startswith("272|") else f"{l.split('|')[0]}|[fr] {l}"
                         for l in ls), "eos"
    cues = [Cue(id=i, start_ms=i * 1000, end_ms=i * 1000 + 900, text="A room?" if i == 272 else f"line {i}")
            for i in range(265, 281)]
    out = translate_cues(cues, "fr", CTX, translator(model)[0], retry_delay_s=0)
    assert {c.id: c.text for c in out}[272] == "Une chambre ?" and len(out) == 16


def test_unknown_source_language_fails_the_language_loudly_not_silently():
    t, fake = translator(tag_fr)
    with pytest.raises(TranslationFailed, match="could not be detected"):
        translate_cues(CUES[:2], "fr", TranslationContext(), t, retry_delay_s=0)
    assert fake.prompts == []


@pytest.mark.parametrize("source, target", [("en", "xx"), ("xx", "fr")])
def test_a_language_the_model_does_not_know_raises_before_any_text_is_sent(source, target):
    t, fake = translator(tag_fr)
    with pytest.raises(ValueError, match="unsupported xx"):
        t.translate([{"id": 1, "text": "one"}], target, TranslationContext(source_lang=source))
    assert fake.prompts == []


def test_server_refuses_a_missing_binary(tmp_path):
    s = LlamaServer(binary=str(tmp_path / "nope.exe"), model=__file__, port=_free_port(), gpu_layers=0, log_dir=tmp_path)
    with pytest.raises(RuntimeError, match="LLAMA_SERVER_BIN"):
        s.ensure_running()


def test_server_that_exits_at_start_raises_with_its_log(tmp_path):
    # Python handed llama-server's arguments exits at once with a usage error: the shape of a bad model file.
    s = LlamaServer(binary=sys.executable, model=__file__, port=_free_port(), gpu_layers=0, log_dir=tmp_path)
    with pytest.raises(RuntimeError, match="exited with code"):
        s.ensure_running()


def test_server_never_reuses_an_orphan_on_its_port(tmp_path):
    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()

        def log_message(self, *a):
            pass
    orphan = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=orphan.serve_forever, daemon=True).start()
    try:
        s = LlamaServer(binary=sys.executable, model=__file__, port=orphan.server_address[1], gpu_layers=0,
                        log_dir=tmp_path)
        with pytest.raises(RuntimeError, match="already served"):
            s.ensure_running()
    finally:
        orphan.shutdown()


def _free_port():
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def test_backend_choice(monkeypatch):
    monkeypatch.delenv("TRANSLATOR", raising=False)
    assert make_translator().name.startswith("llamacpp:")
    monkeypatch.setenv("TRANSLATOR", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")  # without one there is no Gemini translator (test_gemini)
    assert make_translator().name.startswith("gemini:")
    monkeypatch.setenv("TRANSLATOR", "openai")
    with pytest.raises(ValueError):
        make_translator()
