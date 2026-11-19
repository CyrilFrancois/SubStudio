# test_subtitles_api.py — GET/PUT /api/subtitles on real files: cues read back from the SubStudio track, text-only edits written by the verified swap, reviewed kept across rewrites. Exports: none (pytest).
"""Generated clips in pytest's temp folder only: never C:\\Videos, never real media."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import library, routes
from api.job_runner import job_runner
from api.schemas import CueList
from api.subtitles import SubtitleError, editing, get_cue_list, save_cue_list
from bench.media import ffmpeg
from core.acquire.types import Rung
from core.cells import Cell, CellState, Origin, Source, VideoRun
from core.jobs import job_registry
from core.output import read_track, update_in_place, write_run
from core.subtitles import Cue, TranslationContext

SECONDS = 5
_SRC = ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=10:duration={SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={SECONDS}"]
_AV = ["-c:v", "mpeg4", "-q:v", "8", "-c:a", "aac", "-b:a", "48k"]

# The source cues keep their own ids (positions in the human English subtitle); the translation keys on them.
EN = [Cue(10, 500, 2000, "Hello."), Cue(11, 2500, 4000, "Goodbye."), Cue(12, 4200, 4800, "The end.")]
FR = [Cue(10, 500, 2000, "Bonjour."), Cue(11, 2500, 4000, "Au revoir."), Cue(12, 4200, 4800, "Fin.")]
DE = [Cue(1, 600, 1900, "Hallo."), Cue(2, 2600, 3900, "Tschüss.")]


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _video(folder: Path, audio_tag=None) -> Path:
    """A clip carrying a SubStudio fr track (translated) and de track (transcribed): « Show S01E01 (Sub-FR,DE).mkv »."""
    raw = folder / "Show S01E01.mkv"
    tag = ["-metadata:s:a:0", f"language={audio_tag}"] if audio_tag else []
    ffmpeg(*_SRC, "-map", "0:v", "-map", "1:a", *_AV, *tag, str(raw))
    return update_in_place(raw, {"fr": FR, "de": DE}, ["fr", "de"],
                           origins={"fr": "translated", "de": "transcribed"}).path


def _run(path: Path) -> VideoRun:
    """The run the pipeline keeps for /api/retry after writing `path`."""
    fr = Cell("fr", CellState.PASSED, Origin(Rung.TRANSLATE, "en"), cues=list(FR))
    de = Cell("de", CellState.PASSED, Origin(Rung.ASR), cues=list(DE))
    run = VideoRun(path=str(path.with_name("Show S01E01.mkv")), cells={"fr": fr, "de": de},
                   human={"en": Source(cues=EN, rung=Rung.DOWNLOAD, language="en", context=TranslationContext())})
    run.output_path = str(path)
    return run


def _orch(*runs, active=()):
    return SimpleNamespace(runs={r.path: r for r in runs}, active_jobs=set(active))


def _body(listing: CueList, **edits) -> CueList:
    cues = [c.model_copy(update={"text": edits.get(f"c{c.id}", c.text)}) for c in listing.cues]
    return listing.model_copy(update={"cues": cues})


# ---------------------------------------------------------------- GET

def test_get_reads_back_what_shipped_without_a_run(tmp_path):
    p = _video(tmp_path)
    got = get_cue_list(_orch(), "v1", str(p), "fr")
    assert [(c.id, c.start_ms, c.end_ms, c.text, c.source_text) for c in got.cues] == [
        (1, 500, 2000, "Bonjour.", None), (2, 2500, 4000, "Au revoir.", None), (3, 4200, 4800, "Fin.", None)]
    assert (got.origin, got.reviewed, got.language, got.video_id, got.from_language) == \
        ("translated", False, "fr", "v1", None)


def test_get_gives_each_cue_the_text_it_was_translated_from(tmp_path):
    p = _video(tmp_path)
    got = get_cue_list(_orch(_run(p)), "v1", str(p), "fr")
    assert [c.source_text for c in got.cues] == ["Hello.", "Goodbye.", "The end."] and got.from_language == "en"
    # A transcript kept in its own language has no source text.
    de = get_cue_list(_orch(_run(p)), "v1", str(p), "de")
    assert [c.source_text for c in de.cues] == [None, None] and de.from_language is None


def test_ov_reads_the_track_of_the_audio_language_and_answers_ov(tmp_path):
    p = _video(tmp_path, audio_tag="ger")
    got = get_cue_list(_orch(), "v1", str(p), "ov")
    assert got.language == "ov" and [c.text for c in got.cues] == [c.text for c in DE]
    saved = save_cue_list(_orch(), "v1", str(p), "ov", _body(got, c1="Hallo du."))
    assert saved.language == "ov" and saved.reviewed and read_track(p, "de").cues[0].text == "Hallo du."


@pytest.mark.parametrize("lang, status", [("en", 404), ("ov", 404), ("fra", 422)])   # ov: this audio is untagged
def test_get_refuses_what_it_cannot_serve(tmp_path, lang, status):
    p = _video(tmp_path)
    with pytest.raises(SubtitleError) as e:
        get_cue_list(_orch(), "v1", str(p), lang)
    assert e.value.status == status


# ---------------------------------------------------------------- PUT

def test_put_writes_the_text_marks_reviewed_and_keeps_everything_else(tmp_path):
    p = _video(tmp_path)
    run = _run(p)
    orch = _orch(run)
    listing = get_cue_list(orch, "v1", str(p), "fr")
    saved = save_cue_list(orch, "v1", str(p), "fr", _body(listing, c2="Au revoir, Sam."))

    assert [c.text for c in saved.cues] == ["Bonjour.", "Au revoir, Sam.", "Fin."]
    assert saved.reviewed and saved.video_id == "v1"                 # same languages: same name, same id
    assert [c.source_text for c in saved.cues] == ["Hello.", "Goodbye.", "The end."]
    fr, de = read_track(p, "fr"), read_track(p, "de")
    assert [(c.start_ms, c.end_ms) for c in fr.cues] == [(c.start_ms, c.end_ms) for c in FR]   # timings untouched
    assert fr.reviewed and fr.origin == "translated"
    assert [c.text for c in de.cues] == [c.text for c in DE] and not de.reviewed and de.origin == "transcribed"
    # The run kept for retry has the new text under the source ids, and knows the cell was reviewed.
    assert [(c.id, c.text) for c in run.cells["fr"].cues] == [(10, "Bonjour."), (11, "Au revoir, Sam."), (12, "Fin.")]
    assert run.cells["fr"].reviewed and not run.cells["de"].reviewed


def test_reviewed_survives_another_language_being_saved_and_a_rebuild_from_the_run(tmp_path):
    p = _video(tmp_path)
    run = _run(p)
    orch = _orch(run)
    save_cue_list(orch, "v1", str(p), "fr", _body(get_cue_list(orch, "v1", str(p), "fr"), c1="Salut."))
    save_cue_list(orch, "v1", str(p), "de", _body(get_cue_list(orch, "v1", str(p), "de"), c2="Tschüs."))
    assert read_track(p, "fr").reviewed and read_track(p, "de").reviewed     # fr's tag was copied, not lost
    # A retry rebuilds every passed cell from memory (core/output.write_run): the edits and marks stay.
    write_run(run)
    fr, de = read_track(p, "fr"), read_track(p, "de")
    assert fr.reviewed and de.reviewed
    assert fr.cues[0].text == "Salut." and de.cues[1].text == "Tschüs."


@pytest.mark.parametrize("change, status", [
    (lambda cues: cues[:2], 409),                                                   # a cue missing
    (lambda cues: [cues[0].model_copy(update={"id": 9})] + cues[1:], 409),          # an id the file does not have
    (lambda cues: [cues[0].model_copy(update={"end_ms": 2100})] + cues[1:], 409),   # a timing
    (lambda cues: [cues[0].model_copy(update={"text": "  \r\n "})] + cues[1:], 422),  # an empty cue
    (lambda cues: [cues[0].model_copy(update={"text": "x" * 501})] + cues[1:], 422),  # a pasted page
])
def test_put_refuses_anything_but_text_and_leaves_the_video_byte_identical(tmp_path, change, status):
    p = _video(tmp_path)
    before = _sha(p)
    listing = get_cue_list(_orch(), "v1", str(p), "fr")
    with pytest.raises(SubtitleError) as e:
        save_cue_list(_orch(), "v1", str(p), "fr", listing.model_copy(update={"cues": change(listing.cues)}))
    assert e.value.status == status
    assert _sha(p) == before


def test_a_save_claims_the_video_and_a_running_or_queued_job_refuses_it(tmp_path):
    p = _video(tmp_path)
    run = _run(p)
    with pytest.raises(SubtitleError) as e:                     # running: in active_jobs under its queued path
        with editing(_orch(run, active={run.path}), str(p)):
            pass
    assert e.value.status == 409
    job_registry.register(run.path)                             # queued by a batch that has not reached it yet
    try:
        with pytest.raises(SubtitleError):
            with editing(_orch(run), str(p)):
                pass
    finally:
        job_registry.release(run.path)
    orch = _orch(run)
    with editing(orch, str(p)):
        assert orch.active_jobs == {str(p)}                     # every job starter refuses while this is non-empty
    assert orch.active_jobs == set()


def test_put_refuses_while_a_job_runs_on_that_video(tmp_path, monkeypatch):
    p = _video(tmp_path)
    run = _run(p)
    monkeypatch.setattr(job_runner, "_orch", _orch(run, active={run.path}))
    vid = library.remember(str(p))
    client = TestClient(_app(routes))
    before = _sha(p)
    body = client.get(f"/api/subtitles/{vid}/fr").json()
    body["cues"][0]["text"] = "Salut."
    put = client.put(f"/api/subtitles/{vid}/fr", json=body)
    assert put.status_code == 409 and "job" in put.json()["detail"] and _sha(p) == before
    assert job_runner.orchestrator.active_jobs == {run.path}    # the refused claim took and released nothing


def test_an_id_whose_file_was_renamed_answers_404(tmp_path, monkeypatch):
    p = _video(tmp_path)
    monkeypatch.setattr(job_runner, "_orch", _orch())
    vid = library.remember(str(p))
    p.rename(p.with_name("Renamed.mkv"))
    assert TestClient(_app(routes)).get(f"/api/subtitles/{vid}/ov").status_code == 404


def test_put_with_nothing_new_on_a_reviewed_track_does_not_rewrite(tmp_path):
    p = _video(tmp_path)
    listing = get_cue_list(_orch(), "v1", str(p), "fr")
    save_cue_list(_orch(), "v1", str(p), "fr", listing)              # first save: marks it reviewed
    before = _sha(p)
    again = save_cue_list(_orch(), "v1", str(p), "fr", get_cue_list(_orch(), "v1", str(p), "fr"))
    assert again.reviewed and _sha(p) == before


def test_put_through_the_route(tmp_path, monkeypatch):
    p = _video(tmp_path)
    monkeypatch.setattr(job_runner, "_orch", _orch(_run(p)))
    vid = library.remember(str(p))
    client = TestClient(_app(routes))
    got = client.get(f"/api/subtitles/{vid}/fr")
    assert got.status_code == 200 and got.json()["cues"][0]["source_text"] == "Hello."
    assert got.json()["limits"] is not None and all(c["flags"] == [] for c in got.json()["cues"])  # flag_cues ran
    body = got.json()
    body["cues"][0]["text"] = "Salut."
    put = client.put(f"/api/subtitles/{vid}/fr", json=body)
    assert put.status_code == 200 and put.json()["reviewed"] and put.json()["cues"][0]["text"] == "Salut."
    body["cues"][0]["start_ms"] = 0
    assert client.put(f"/api/subtitles/{vid}/fr", json=body).status_code == 409
    assert client.get("/api/subtitles/unknown/fr").status_code == 404


def _app(routes_mod) -> FastAPI:
    app = FastAPI()
    app.include_router(routes_mod.router)
    return app


def _video_with_left(folder: Path) -> Path:
    raw = folder / "Show S01E02.mkv"
    ffmpeg(*_SRC, "-map", "0:v", "-map", "1:a", *_AV, str(raw))
    fr = [Cue(10, 500, 2000, "Bonjour."), Cue(11, 2500, 4000, "I am still here."), Cue(12, 4200, 4800, "Fin.")]
    return update_in_place(raw, {"fr": fr}, ["fr"], origins={"fr": "translated"}, untranslated={"fr": {11}},
                           from_languages={"fr": "en"}).path


def test_a_line_left_untranslated_is_flagged_after_a_restart_too(tmp_path, monkeypatch):
    p = _video_with_left(tmp_path)
    got = get_cue_list(_orch(), "v1", str(p), "fr")                  # no run in memory: only the file knows
    assert got.from_language == "en" and [c.untranslated_from for c in got.cues] == [None, "en", None]
    monkeypatch.setattr(job_runner, "_orch", _orch())
    vid = library.remember(str(p))
    flags = [c["flags"] for c in TestClient(_app(routes)).get(f"/api/subtitles/{vid}/fr").json()["cues"]]
    assert flags[1] == ["untranslated"] and flags[0] == [] and flags[2] == []


def test_fixing_a_line_left_untranslated_in_review_removes_it_from_the_tag(tmp_path):
    p = _video_with_left(tmp_path)
    listing = get_cue_list(_orch(), "v1", str(p), "fr")
    saved = save_cue_list(_orch(), "v1", str(p), "fr", _body(listing, c2="Je suis toujours là."))
    assert [c.untranslated_from for c in saved.cues] == [None, None, None]
    assert read_track(p, "fr").untranslated == [] and read_track(p, "fr").from_language == "en"
    # A save that leaves the line as it was keeps it listed.
    (tmp_path / "b").mkdir()
    p2 = _video_with_left(tmp_path / "b")
    again = save_cue_list(_orch(), "v2", str(p2), "fr", _body(get_cue_list(_orch(), "v2", str(p2), "fr"), c1="Salut."))
    assert [c.untranslated_from for c in again.cues] == [None, "en", None]


def test_fixing_a_refused_line_in_review_also_clears_it_from_the_run(tmp_path):
    p = _video(tmp_path)
    run = _run(p)
    run.cells["fr"].left_in_source = [11]                     # « Au revoir. » was kept as the model refused it
    orch = _orch(run)
    save_cue_list(orch, "v1", str(p), "fr", _body(get_cue_list(orch, "v1", str(p), "fr"), c2="Salut, à plus."))
    assert run.cells["fr"].left_in_source == []               # a retry rebuilding the file will not list it again
