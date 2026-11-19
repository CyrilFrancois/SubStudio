# test_gemini.py — GeminiTranslator: no key means no translator and no request; with a key, the request carries it. Exports: none (pytest).

import json

import httpx
import pytest

import core.translator as gemini
from core.subtitles import TranslationContext, TranslationFailed
from core.translators import make_translator

REPLY = {"candidates": [{"finishReason": "STOP",
                         "content": {"parts": [{"text": json.dumps([{"id": 1, "text": "Bonjour"}])}]}}]}


@pytest.fixture
def sent(monkeypatch):
    """Every request any GeminiTranslator would send, captured instead of reaching Google."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=REPLY)

    real_client = httpx.Client
    monkeypatch.setattr(gemini.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("TRANSLATOR", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return requests


@pytest.mark.parametrize("key", [None, "", "  \n"])
def test_no_key_no_translator_no_request(sent, monkeypatch, key):
    if key is not None:
        monkeypatch.setenv("GEMINI_API_KEY", key)
    with pytest.raises(TranslationFailed, match="GEMINI_API_KEY is not set"):
        make_translator()
    with pytest.raises(TranslationFailed, match="GEMINI_API_KEY is not set"):
        gemini.GeminiTranslator().translate([{"id": 1, "text": "Hello"}], "fr", TranslationContext(source_lang="en"))
    assert sent == []


def test_with_key_the_request_is_sent_with_it(sent, monkeypatch):
    # The positive control: the capture above does see a request when one is made.
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    out = make_translator().translate([{"id": 1, "text": "Hello"}], "fr", TranslationContext(source_lang="en"))
    assert out == [{"id": 1, "text": "Bonjour"}]
    assert len(sent) == 1 and sent[0].headers["x-goog-api-key"] == "test-key-not-real"
