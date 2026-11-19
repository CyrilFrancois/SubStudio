# test_ladder.py — the subtitle ladder end to end with fake sources: rung order, fall-through reasons, OV-first translation, ASR only when no human subtitle exists, untagged tracks labelled by their text. Exports: none (pytest).
import asyncio

import pytest

from core.acquire import plan
from core.acquire.existing import label_by_text
from core.acquire.fetch import Acquired, FetchResult
from core.acquire.types import ExistingSub
from core.cells import Cell, CellState, VideoRun
from core.ladder import Ladder, LadderDeps
from core.subtitles import Cue

ENGLISH = ["Good morning, everyone, and welcome to the house.", "I think the ghosts are hiding something from us.",
           "We need to talk about what happened last night in the kitchen.",
           "Nobody leaves this room until we find out the truth."]
FRENCH = ["Bonjour à tous, et bienvenue dans la maison.", "Je pense que les fantômes nous cachent quelque chose.",
          "Il faut parler de ce qui s'est passé hier soir dans la cuisine.",
          "Personne ne quitte cette pièce avant que nous trouvions la vérité."]


def cues(lines):
    return [Cue(id=i + 1, start_ms=i * 2000, end_ms=i * 2000 + 1500, text=t) for i, t in enumerate(lines)]


class Tag:
    name, batch_size = "tag", 30

    def __init__(self):
        self.calls = []

    def translate(self, items, target, context):
        self.calls.append((target, context.source_lang))
        return [{"id": c["id"], "text": f"[{target}] {c['text']}"} for c in items]


def make(wanted, existing=(), original=None, fetched=None, preloaded=None, downloads=True, unlabelled=None):
    plans = plan(wanted, list(existing), original, downloads_enabled=downloads)
    run = VideoRun(path="v.mkv", cells={p.language: Cell(p.language) for p in plans if not p.source_only},
                   plans={p.language: p for p in plans})
    asr_calls, fetch_calls = [], []

    def transcribe():
        asr_calls.append(1)
        return cues(ENGLISH), "en"

    def fetch(path, langs, work_dir):
        fetch_calls.append(sorted(langs))
        return fetched if fetched is not None else FetchResult({})

    deps = LadderDeps(translator=Tag(), transcribe=transcribe, load_embedded=lambda i: cues(ENGLISH), fetch=fetch,
                      downloads_enabled=downloads, preloaded=preloaded or {}, unlabelled=unlabelled)
    return run, Ladder(run, deps), asr_calls, fetch_calls


def go(run, ladder):
    async def body():
        await ladder.human()
        for lang, cell in run.cells.items():
            if cell.state != CellState.PASSED:
                await ladder.fill(lang)
    asyncio.run(body())
    return {lang: c.to_json() for lang, c in run.cells.items()}


def test_unmarked_existing_is_ignored_then_download_then_translate_from_ov(monkeypatch):
    # The C:\Videos case: an unmarked « English (SDH) » track, nothing online for FR, an English download.
    sdh = ExistingSub("en", "embedded", "0")
    fetched = FetchResult({"en": Acquired("en", "en.srt", "gestdown", False, True, -0.2)})
    run, ladder, asr, fetches = make(["OV", "en", "fr"], [sdh], original="en", fetched=fetched)
    monkeypatch.setattr("core.ladder.load_file", lambda path: cues(ENGLISH))  # the downloaded file
    cells = go(run, ladder)
    assert fetches == [["en", "fr"]]                       # one search for every missing language
    assert cells["en"]["origin"] == "downloaded" and cells["en"]["attempts"] == ["existing: ignored: en embedded"]
    assert cells["fr"]["origin_label"] == "translated from en"
    assert cells["fr"]["attempts"] == ["download: none found online"]
    assert asr == []                                       # a human subtitle existed: no transcription


def test_marked_existing_is_kept_and_never_downloaded():
    ours = ExistingSub("fr", "embedded", "1", marked=True)
    run, ladder, asr, fetches = make(["fr"], [ours], original="en")
    cells = go(run, ladder)
    assert cells["fr"]["origin"] == "kept" and fetches == [["en"]]  # only OV, as a translation source


def test_unsynced_download_falls_through_with_its_reason():
    fetched = FetchResult({"fr": Acquired("fr", "fr.srt", "gestdown", False, False)}, frozenset({"podnapisi"}))
    run, ladder, asr, _ = make(["fr"], original="en", fetched=fetched)
    cells = go(run, ladder)
    assert cells["fr"]["attempts"][:2] == ["download: found, but it could not be matched to the audio",
                                          "translate: no good subtitle in another language"]
    assert cells["fr"]["origin_label"] == "transcribed, translated from en" and asr == [1]


def test_missing_language_says_which_sites_were_unreachable():
    run, ladder, _, _ = make(["fr"], original="en", fetched=FetchResult({}, frozenset({"podnapisi", "tvsubtitles"})))
    cells = go(run, ladder)
    assert cells["fr"]["attempts"][0] == "download: none found online (podnapisi, tvsubtitles unreachable)"


def test_transcription_runs_once_for_every_language():
    run, ladder, asr, _ = make(["de", "fr"], downloads=False)
    cells = go(run, ladder)
    assert asr == [1] and {(c["origin"], c["from_language"]) for c in cells.values()} == {("translated", "en")}


def test_unlabelled_user_pick_is_still_a_translation_source():
    run, ladder, asr, _ = make(["fr"], downloads=False, unlabelled=cues(["Hi!"]))
    cells = go(run, ladder)
    assert cells["fr"]["origin_label"] == "translated from unknown language" and asr == []


def test_untagged_text_track_is_labelled_by_its_text():
    tracks = [ExistingSub(None, "embedded", "0"), ExistingSub(None, "embedded", "1", text=False),
              ExistingSub("en", "embedded", "2")]
    out, loaded = label_by_text(tracks, lambda i: cues(FRENCH))
    assert [(s.language, s.ref) for s in out] == [("fr", "0"), (None, "1"), ("en", "2")]
    assert list(loaded) == ["0"]


def test_unreadable_untagged_track_is_left_out_not_fatal():
    def boom(i):
        raise RuntimeError("ffmpeg failed")
    out, loaded = label_by_text([ExistingSub(None, "embedded", "0")], boom)
    assert out == [] and loaded == {}


def test_process_refuses_a_second_job_while_one_runs():
    # One heavy model at a time on this PC (D-0001): a second batch waits instead of loading another model.
    pytest.importorskip("faster_whisper")
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as client:
        main.orchestrator.active_jobs.add("busy.mkv")
        try:
            r = client.post("/api/process", json={"videos": [], "globalOptions": {}})
        finally:
            main.orchestrator.active_jobs.discard("busy.mkv")
        assert r.status_code == 409
        assert client.post("/api/process", json={"videos": [], "globalOptions": {}}).status_code == 200


def test_failed_translation_from_a_human_subtitle_fails_the_cell_without_transcribing():
    # Rung 4 is for « no good subtitle anywhere »: a human EN existed, so a failed FR translation is reported as such.
    class Drops(Tag):
        def translate(self, items, target, context):
            return super().translate(items, target, context)[1:]
    ours = ExistingSub("en", "embedded", "0", marked=True)
    run, ladder, asr, _ = make(["en", "fr"], [ours], downloads=False)
    ladder.deps.translator = Drops()
    cells = go(run, ladder)
    assert cells["en"]["origin"] == "kept"
    assert cells["fr"]["state"] == "failed" and cells["fr"]["reason"].startswith("fr:") and asr == []
