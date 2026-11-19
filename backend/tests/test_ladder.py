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


def _retry(run, fetched_now, monkeypatch):
    """A new ladder on the same run, as POST /api/retry builds it, whose search now returns `fetched_now`."""
    monkeypatch.setattr("core.ladder.load_file", lambda path: cues(FRENCH))
    calls = []

    def fetch(path, langs, work_dir):
        calls.append(sorted(langs))
        return fetched_now
    deps = LadderDeps(translator=Tag(), transcribe=lambda: (cues(ENGLISH), "en"), load_embedded=lambda i: [],
                      fetch=fetch)
    return Ladder(run, deps), calls


def test_retry_searches_again_after_a_failed_search(monkeypatch):
    # First pass: the sites were down, so FR came from a transcription; the daily quota resets, the user retries.
    run, ladder, _, _ = make(["fr"], original="en", fetched=FetchResult({}, frozenset({"opensubtitlescom"})))
    go(run, ladder)
    assert run.cells["fr"].origin.kind() == "translated"
    retry, calls = _retry(run, FetchResult({"fr": Acquired("fr", "fr.srt", "gestdown", False, True, 0.1)}), monkeypatch)
    run.cells["fr"].state = CellState.PENDING
    asyncio.run(retry.fill("fr"))
    assert calls == [["fr"]] and run.cells["fr"].origin.kind() == "downloaded"
    assert not any(a.startswith("download") for a in run.cells["fr"].attempts)  # the old failure is not kept


def test_retry_does_not_search_again_for_an_unmatched_download(monkeypatch):
    unmatched = FetchResult({"fr": Acquired("fr", "fr.srt", "gestdown", False, False)})
    run, ladder, _, _ = make(["fr"], original="en", fetched=unmatched)
    go(run, ladder)
    retry, calls = _retry(run, unmatched, monkeypatch)
    run.cells["fr"].state = CellState.PENDING
    asyncio.run(retry.fill("fr"))
    assert calls == []


def test_first_pass_never_searches_twice():
    run, ladder, _, fetches = make(["fr"], original="en")
    go(run, ladder)
    assert fetches == [["en", "fr"]]


def test_retry_searches_with_the_video_as_it_is_now(monkeypatch):
    # After the in-place swap the file is « X (Sub-EN).mkv »: a search on the old name would find no file.
    run, ladder, _, _ = make(["fr"], original="en", fetched=FetchResult({}, frozenset({"gestdown"})))
    go(run, ladder)
    run.output_path = "v (Sub-EN).mkv"
    retry, _ = _retry(run, FetchResult({}), monkeypatch)
    seen = []
    retry.deps.fetch = lambda path, langs, work_dir: seen.append(path) or FetchResult({})
    run.cells["fr"].state = CellState.PENDING
    asyncio.run(retry.fill("fr"))
    assert seen == ["v (Sub-EN).mkv"]


def test_each_source_gets_the_context_with_its_own_names():
    # The glossary must hold the names of the subtitle actually being translated (core/context.get_context).
    from core.subtitles import TranslationContext
    seen = []

    def context(c):
        seen.append(c)
        return TranslationContext(title="Ghosts (2021) S05E07", glossary=("Hetty",) if c else ())
    ours = ExistingSub("en", "embedded", "0", marked=True)
    run, ladder, _, _ = make(["en", "fr"], [ours], downloads=False)
    ladder.deps.context = context
    go(run, ladder)
    src = run.human["en"]
    assert src.context.glossary == ("Hetty",) and src.context.source_lang == "en" and not src.context.source_is_asr
    assert seen and seen[0] == src.cues


def test_offline_context_says_why_nothing_was_looked_up():
    pytest.importorskip("faster_whisper")
    import main
    ctx = main.PipelineOrchestrator()._context("C:/x/Ghosts.2021.S05E07.mkv")  # conftest: SUBSTUDIO_DOWNLOADS=0
    assert "online lookups are off" in ctx.missing and ctx.season == 5 and ctx.episode == 7


# --- the quality gate (core/gate.evaluate) on every rung -----------------------------------------------------------
class _Gate:
    """Records each call; rejects the languages in `reject` with a sentence, like Verdict.reason."""

    def __init__(self, reject=()):
        self.reject, self.calls, self.from_audio = set(reject), [], []

    def __call__(self, cues_, speech, lang, label=None, duration_ms=None, source=None, timing_from_audio=False):
        from core.gate import Check, Verdict
        self.calls.append((lang, label, source is not None, duration_ms))
        self.from_audio.append((lang, timing_from_audio))
        ok = lang not in self.reject
        why = "" if ok else f"{lang}: 41 % of lines overlap speech (min 85 %)"
        return Verdict(ok, [Check("sync", 0.41, 0.85, ok, why)])


def gated(wanted, existing=(), reject=(), fetched=None, count_existing=True, **kw):
    run, ladder, asr, fetches = make(wanted, existing, fetched=fetched, **kw)
    speech_reads = []
    ladder.deps.gate = _Gate(reject)
    ladder.deps.speech = lambda: speech_reads.append(1) or ([(0, 1500), (2000, 3500)], 9000)
    ladder.deps.count_existing = count_existing
    return run, ladder, asr, fetches, speech_reads


def test_unmarked_existing_counts_when_the_gate_passes_it():
    # Cyril, 2026-11-19: count_existing on, gated — a good release track leaves the file as is for that language.
    sdh = ExistingSub("en", "embedded", "0")
    run, ladder, asr, fetches, reads = gated(["en"], [sdh], original="en")
    cells = go(run, ladder)
    assert cells["en"]["origin"] == "kept" and fetches == [] and ladder.deps.gate.calls[0][:2] == ("en", "en")


def test_rejected_existing_falls_through_with_the_gate_sentence(monkeypatch):
    sdh = ExistingSub("en", "embedded", "0")
    fetched = FetchResult({"en": Acquired("en", "en.srt", "gestdown", False, True, 0.1)})
    run, ladder, _, fetches, _ = gated(["en"], [sdh], reject=(), fetched=fetched, original="en")
    calls = {"n": 0}
    inner = ladder.deps.gate

    def first_rejected(*a, **k):  # the embedded track fails, the download passes
        calls["n"] += 1
        return _Gate(reject=("en",))(*a, **k) if calls["n"] == 1 else inner(*a, **k)
    ladder.deps.gate = first_rejected
    monkeypatch.setattr("core.ladder.load_file", lambda path: cues(ENGLISH))
    cells = go(run, ladder)
    assert cells["en"]["attempts"] == ["existing: en embedded rejected: en: 41 % of lines overlap speech (min 85 %)"]
    assert cells["en"]["origin"] == "downloaded" and fetches == [["en"]]


def test_count_existing_off_never_checks_an_unmarked_subtitle():
    sdh = ExistingSub("en", "embedded", "0")
    run, ladder, _, _, reads = gated(["en"], [sdh], count_existing=False, downloads=False, original="en")
    go(run, ladder)
    assert run.cells["en"].attempts[0] == "existing: ignored: en embedded"
    assert ("en", "en", False, 9000) not in ladder.deps.gate.calls  # the track itself was never gated


def test_rejected_download_is_noted_and_not_used(monkeypatch):
    fetched = FetchResult({"fr": Acquired("fr", "fr.srt", "gestdown", False, True, 0.1)})
    run, ladder, _, _, _ = gated(["fr"], reject=("fr",), fetched=fetched, original="en")
    monkeypatch.setattr("core.ladder.load_file", lambda path: cues(FRENCH))
    cells = go(run, ladder)
    assert cells["fr"]["attempts"][0] == "download: rejected: fr: 41 % of lines overlap speech (min 85 %)"


def test_translated_cell_is_gated_against_its_source_and_fails_with_the_sentence():
    ours = ExistingSub("en", "embedded", "0", marked=True)
    run, ladder, asr, _, reads = gated(["en", "fr"], [ours], reject=("fr",), downloads=False)
    cells = go(run, ladder)
    assert cells["en"]["state"] == "passed"
    assert cells["fr"]["state"] == "failed" and cells["fr"]["reason"] == "fr: 41 % of lines overlap speech (min 85 %)"
    assert ("fr", None, True, 9000) in ladder.deps.gate.calls        # source cues given for a translation
    assert reads == [1] and asr == []                                 # speech read once for the whole video
    assert ("fr", False) in ladder.deps.gate.from_audio               # a human source's times: must be measured


def test_cues_timed_by_whisper_tell_the_gate_their_times_come_from_the_audio():
    # A short clip's sync may be unmeasurable: that is no fault for a transcript or its translation, only for a human
    # track (core/gate/verdict.evaluate, timing_from_audio).
    run, ladder, asr, _, _ = gated(["en", "fr"], downloads=False, original="en")
    go(run, ladder)
    assert asr == [1] and sorted(ladder.deps.gate.from_audio) == [("en", True), ("fr", True)]


def test_a_gate_that_cannot_measure_rejects_instead_of_crashing_the_video():
    def no_speech(*a, **k):
        raise ValueError("no speech detected: sync cannot be measured")
    ours = ExistingSub("en", "embedded", "0", marked=True)
    run, ladder, _, _, _ = gated(["en"], [ours], downloads=False)
    ladder.deps.gate = no_speech
    cells = go(run, ladder)
    assert cells["en"]["attempts"][0] == "existing: en embedded rejected: no speech detected: sync cannot be measured"


def test_the_real_gate_passes_cues_that_sit_on_the_speech():
    # A real episode's shape (120 lines over ~6 min; four lines are too few for the offset measure).
    import random
    from core.gate import evaluate
    rnd, t, c = random.Random(5), 2000, []
    for i in range(120):
        d = rnd.randint(3000, 5000)
        c.append(Cue(id=i + 1, start_ms=t, end_ms=t + d, text=ENGLISH[i % len(ENGLISH)]))
        t += d + rnd.randint(400, 3000)
    from core.gate import THRESHOLDS
    from core.subtitles import wrap_lines
    c = wrap_lines(c, THRESHOLDS["max_line_chars"])  # as the ladder writes them
    v = evaluate(c, [(q.start_ms, q.end_ms) for q in c], "en", duration_ms=t + 5000)
    assert v.passed, v.reason
    shifted = [Cue(id=q.id, start_ms=q.start_ms + 4000, end_ms=q.end_ms + 4000, text=q.text) for q in c]
    assert not evaluate(shifted, [(q.start_ms, q.end_ms) for q in c], "en", duration_ms=t + 9000).passed


def test_count_existing_follows_the_saved_setting(monkeypatch):
    pytest.importorskip("faster_whisper")
    import api.settings_store as store
    from api.schemas import Settings
    import main
    orch = main.PipelineOrchestrator()
    monkeypatch.setattr(store, "load_settings", lambda: Settings(languages=["en"], count_existing=False))
    assert orch.count_existing is False
    monkeypatch.setattr(store, "load_settings", lambda: Settings(languages=["en"]))
    assert orch.count_existing is True                                 # the default: on (D-0002)
