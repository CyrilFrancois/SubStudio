# test_translate.py — translate_cues: timings re-attached by id, wrong batches retried then split, failures loud. Exports: none (pytest).

import pytest

from core.jobs import JobAborted
from core.subtitles import Cue, TranslationContext, TranslationFailed, translate_cues

CUES = [Cue(id=i, start_ms=i * 1000, end_ms=i * 1000 + 800, text=f"line {i}") for i in range(1, 11)]
CTX = TranslationContext(title="Test")


class Fake:
    name = "fake"

    def __init__(self, batch_size=4, behave=None):
        self.batch_size = batch_size
        self.behave = behave or (lambda cues: [{"id": c["id"], "text": c["text"].upper()} for c in cues])
        self.calls = []

    def translate(self, cues, target, context):
        self.calls.append(([c["id"] for c in cues], context))
        return self.behave(cues)


def run(fake, **kw):
    return translate_cues(CUES, "fr", CTX, fake, retry_delay_s=0, **kw)


def test_timings_come_from_the_source_never_the_model():
    out = run(Fake(behave=lambda cues: [{"id": c["id"], "text": c["text"].upper()} for c in reversed(cues)]))
    assert [(c.id, c.start_ms, c.end_ms) for c in out] == [(c.id, c.start_ms, c.end_ms) for c in CUES]
    assert [c.text for c in out] == [f"LINE {i}" for i in range(1, 11)]


def test_the_model_never_sees_a_timing_and_gets_previous_cues_as_context():
    fake = Fake()
    run(fake)
    assert [ids for ids, _ in fake.calls] == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]]
    assert fake.calls[1][1].previous == ("line 2", "line 3", "line 4")
    assert fake.calls[0][1].previous == ()


def test_a_batch_the_model_keeps_merging_is_split_until_it_succeeds():
    # Models merge lines in big batches; with two or fewer cues this one behaves.
    def merges(cues):
        if len(cues) > 2:
            return [{"id": cues[0]["id"], "text": " ".join(c["text"] for c in cues)}]
        return [{"id": c["id"], "text": "ok"} for c in cues]
    fake = Fake(behave=merges)
    out = run(fake)
    assert [c.text for c in out] == ["ok"] * 10
    assert [c.end_ms for c in out] == [c.end_ms for c in CUES]


def test_one_cue_the_model_never_returns_fails_the_language_by_name():
    fake = Fake(behave=lambda cues: [{"id": c["id"], "text": "x"} for c in cues if c["id"] != 6])
    with pytest.raises(TranslationFailed, match=r"fr: .*cue 6"):
        run(fake)


def test_empty_text_is_a_wrong_answer():
    with pytest.raises(TranslationFailed, match="cue 2"):
        run(Fake(behave=lambda cues: [{"id": c["id"], "text": "" if c["id"] == 2 else "x"} for c in cues]))


def test_a_duplicated_id_is_rejected_and_the_batch_split():
    # Answering the first id for every line is only right once a batch holds one cue.
    fake = Fake(behave=lambda cues: [{"id": cues[0]["id"], "text": "x"}] * len(cues))
    assert [c.text for c in run(fake)] == ["x"] * 10
    assert [len(ids) for ids, _ in fake.calls[:3]] == [4, 4, 2]


def test_a_failing_backend_is_retried_then_fails_loudly_never_ships_the_source():
    def down(cues):
        raise ConnectionError("no route")
    fake = Fake(behave=down)
    with pytest.raises(TranslationFailed, match="failed 3 times"):
        run(fake)
    assert len(fake.calls) == 3


def test_a_transient_error_is_retried():
    state = {"n": 0}
    def flaky(cues):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("slow")
        return [{"id": c["id"], "text": "y"} for c in cues]
    assert [c.text for c in run(Fake(behave=flaky))] == ["y"] * 10


def test_abort_stops_at_once():
    def abort():
        raise JobAborted("f")
    with pytest.raises(JobAborted):
        run(Fake(), check_abort=abort)


def test_progress_reaches_the_total():
    seen = []
    run(Fake(), on_batch=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (10, 10) and [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_nothing_to_translate_is_an_error():
    with pytest.raises(TranslationFailed):
        translate_cues([], "fr", CTX, Fake())
