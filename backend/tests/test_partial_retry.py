# test_partial_retry.py — rung 1 on our own track with lines left in the source language: only those are translated again, from the source as it passed, the rest kept as shipped; a retry that cannot run or fails keeps the track as it was. Exports: none (pytest).
from unittest import mock

from core.acquire import existing
from core.acquire.types import ExistingSub
from core.subtitles import Cue
from test_ladder import ENGLISH, FRENCH, cues, gated, go

# What shipped: line 2 was refused by the translator and left in English (SUBSTUDIO_UNTRANSLATED=2, SUBSTUDIO_FROM=en).
PARTIAL = FRENCH[:1] + ENGLISH[1:2] + FRENCH[2:]


class _Ids:
    """A translator that records which source ids it was sent."""
    name, batch_size = "ids", 30

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def translate(self, items, target, context):
        self.sent += [c["id"] for c in items]
        if self.fail:
            raise RuntimeError("server down")
        return [{"id": c["id"], "text": f"[{target}] {c['text']}"} for c in items]


def _setup(english=None, translator=None, reject=()):
    en = ExistingSub("en", "embedded", "0", marked=True)
    fr = ExistingSub("fr", "embedded", "1", marked=True, untranslated=(2,), from_language="en")
    run, ladder, asr, fetches, _ = gated(["en", "fr"], [en, fr], reject=reject, downloads=False, original="en")
    tracks = {0: english or cues(ENGLISH), 1: cues(PARTIAL)}
    ladder.deps.load_embedded = lambda i: tracks[i]
    ladder.deps.translator = translator or _Ids()
    return run, ladder, asr


def test_only_the_lines_left_in_the_source_are_translated_again():
    run, ladder, asr = _setup()
    cells = go(run, ladder)
    assert ladder.deps.translator.sent == [2]                          # one line sent, not the episode
    lines = [" ".join(c.text.split()) for c in run.cells["fr"].cues]   # every line is wrapped as it will be written
    assert lines == [FRENCH[0], f"[fr] {ENGLISH[1]}", FRENCH[2], FRENCH[3]]
    assert cells["fr"]["origin_label"] == "translated from en"         # written back, so the tag is recomputed
    assert cells["fr"]["attempts"][0] == "existing: fr kept, 1 line still in en: translating it again"
    assert asr == []


def test_a_source_that_no_longer_matches_line_for_line_keeps_the_track_as_it_was():
    moved = [Cue(c.id, c.start_ms + 700, c.end_ms + 700, c.text) for c in cues(ENGLISH)]   # another English since
    run, ladder, _ = _setup(english=moved)
    cells = go(run, ladder)
    assert ladder.deps.translator.sent == []                           # positions would name other lines
    assert cells["fr"]["origin"] == "kept" and [c.text for c in run.cells["fr"].cues] == PARTIAL
    assert "the en subtitle no longer matches it line for line" in cells["fr"]["attempts"][-1]


def test_a_failed_retry_never_leaves_the_language_worse_than_it_was():
    run, ladder, _ = _setup(translator=_Ids(fail=True))
    cells = go(run, ladder)
    assert cells["fr"]["state"] == "passed" and cells["fr"]["origin"] == "kept"
    assert [c.text for c in run.cells["fr"].cues] == PARTIAL
    assert cells["fr"]["attempts"][-1].endswith("; kept as it was")


def test_a_retry_the_gate_turns_down_keeps_the_track_as_it_was():
    run, ladder, _ = _setup(reject=("fr",))
    # The gate rejects every FR here, the shipped track included: rung 1 must pass it for this test, so only the
    # retry's verdict (given its source) is a rejection.
    gate = ladder.deps.gate
    ladder.deps.gate = lambda c, s, lang, source=None, **k: gate(c, s, lang if source is not None else "ok", **k)
    cells = go(run, ladder)
    assert ladder.deps.translator.sent == [2]
    assert cells["fr"]["origin"] == "kept" and [c.text for c in run.cells["fr"].cues] == PARTIAL


def test_the_scan_reads_both_tags_on_our_track_and_only_there():
    streams = [
        {"codec_name": "subrip", "tags": {"language": "fre", "title": "SubStudio fr",
                                          "SUBSTUDIO_UNTRANSLATED": "2,7", "substudio_from": "eng"}},
        {"codec_name": "subrip", "tags": {"language": "eng", "title": "English (SDH)",
                                          "SUBSTUDIO_UNTRANSLATED": "3"}},     # not ours: its tags mean nothing
        {"codec_name": "subrip", "tags": {"language": "fre", "title": "SubStudio fr",
                                          "SUBSTUDIO_UNTRANSLATED": "two"}},   # malformed reads as none
    ]
    with mock.patch.object(existing, "_probe", return_value=streams):
        found = [s for s in existing.find_existing("C:/nowhere/v.mkv") if s.kind == "embedded"]
    assert (found[0].untranslated, found[0].from_language) == ((2, 7), "en")
    assert (found[1].untranslated, found[1].from_language) == ((), None)
    assert found[2].untranslated == ()
