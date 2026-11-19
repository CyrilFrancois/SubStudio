# test_jobstore.py — The job store survives a restart, keeps the queue order and the stepper honest, records verdicts per exact file; the runner drives the real event path. Exports: none (pytest).

import asyncio
import os
from datetime import datetime, timezone

import pytest

from api import job_runner as runner_mod
from api.job_runner import JobRunner, stage_of
from api.job_store import RESTART_REASON, STAGES, JobStore, overlay, use_store
from api.schemas import JobCreate, JobOptions, LanguageCoverage, LibraryResponse, LibraryVideo
from core.events import event_manager
from core.jobs import JobAborted, job_registry


def video(vid, states, path=None):
    return LibraryVideo(
        id=vid, path=path or f"/data/{vid}.mkv", name=f"{vid}.mkv", folder="", size_bytes=1,
        mtime=datetime.now(timezone.utc), validated=False, tracks=[],
        coverage=[LanguageCoverage(language=l, state=s) for l, s in states.items()])


def library(*videos):
    return LibraryResponse(root="/data", languages=["ov", "en", "fr"], scanned_at=datetime.now(timezone.utc),
                           videos=list(videos), warnings=[])


@pytest.fixture
def store(tmp_path):
    s = JobStore(tmp_path / "jobs.sqlite3")
    use_store(s)
    yield s
    use_store(None)
    s.close()


def stage_states(job):
    return {s.stage: s.state for s in job.stages}


# ---------------------------------------------------------------- store

def test_fill_missing_takes_unpassed_cells_and_never_queues_a_video_twice(store):
    lib = library(video("a", {"ov": "passed", "en": "missing", "fr": "failed"}),
                  video("b", {"ov": "passed", "en": "passed", "fr": "passed"}))
    r = store.create(JobCreate(video_ids=["a", "b", "zz"]), lib)
    assert [j.languages for j in r.jobs] == [["en", "fr"]] and sorted(r.skipped) == ["b", "zz"]
    again = store.create(JobCreate(video_ids=["a"], languages=["fr"]), lib)
    assert again.jobs == [] and again.skipped == ["a"]          # already queued


def test_queue_order_positions_and_list_order(store):
    lib = library(*(video(v, {"fr": "missing"}) for v in "abc"))
    ids = [j.id for j in store.create(JobCreate(video_ids=["a", "b", "c"]), lib).jobs]
    assert [store.get(i).position for i in ids] == [0, 1, 2]
    store.start(ids[0])
    store.finish(ids[0], "done")
    store.start(ids[1])
    listed = store.list()
    assert [j.id for j in listed] == [ids[1], ids[2], ids[0]]   # running, queued, history
    assert listed[1].position == 0 and listed[0].position is None


def test_stepper_moves_forward_never_backwards_and_closes_on_finish(store):
    job = store.create(JobCreate(video_ids=["a"]), library(video("a", {"fr": "missing"}))).jobs[0]
    store.start(job.id)
    store.set_stage(job.id, "check", "running")
    store.set_stage(job.id, "translate", "running", 0.5)
    store.set_stage(job.id, "translate", "running", 0.2)       # a late, lower frame never moves the bar back
    j = store.get(job.id)
    assert stage_states(j) == {"check": "done", "download": "skipped", "transcribe": "skipped",
                               "translate": "running", "write": "pending"}
    assert [s.progress for s in j.stages if s.stage == "translate"] == [0.5]
    j = store.finish(job.id, "failed", "FR failed: no subtitle anywhere.")
    assert j.state == "failed" and j.error == "FR failed: no subtitle anywhere." and j.finished_at
    assert stage_states(j)["translate"] == "failed" and stage_states(j)["write"] == "skipped"
    assert store.set_stage(job.id, "write", "running") is None   # a finished job is never reopened


def test_one_stage_runs_at_a_time_when_the_ladder_transcribes_after_a_translation_began(store):
    # Seen on S05E12, 2026-11-19: translate « running » and transcribe « running » together.
    job = store.create(JobCreate(video_ids=["a"]), library(video("a", {"fr": "missing"}))).jobs[0]
    store.start(job.id)
    store.set_stage(job.id, "translate", "running", 0.3)
    store.set_stage(job.id, "transcribe", "running", 0.1)
    j = store.get(job.id)
    assert [s.stage for s in j.stages if s.state == "running"] == ["transcribe"]
    assert [(s.state, s.progress) for s in j.stages if s.stage == "translate"] == [("pending", 0.3)]
    store.set_stage(job.id, "translate", "running", 0.2)       # resumes; its bar never goes back
    assert [(s.state, s.progress) for s in store.get(job.id).stages if s.stage == "translate"] == [("running", 0.3)]
    store.set_stage(job.id, "transcribe", "running", 0.5)
    store.set_stage(job.id, "write", "running")                 # a stage paused with progress closes as done
    assert stage_states(store.get(job.id)) == {"check": "skipped", "download": "skipped", "transcribe": "done",
                                               "translate": "done", "write": "running"}


def test_no_correct_stage_and_old_jobs_read_without_it(tmp_path):
    import json as _json
    import sqlite3
    db = tmp_path / "jobs.sqlite3"
    con = sqlite3.connect(db)                                  # a store from before batch_id and D-0005
    con.executescript("""CREATE TABLE jobs (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, video_path TEXT NOT NULL,
        video_name TEXT NOT NULL, languages TEXT NOT NULL, options TEXT NOT NULL, state TEXT NOT NULL,
        stages TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT);""")
    old = [{"stage": s, "state": "pending", "progress": 0.0} for s in
           ("check", "download", "transcribe", "correct", "translate", "write")]
    con.execute("INSERT INTO jobs VALUES ('old','a','/data/a.mkv','a.mkv',?,'{}','running',?,"
                "'2026-11-19T10:00:00+00:00',NULL,NULL,NULL)", (_json.dumps(["fr"]), _json.dumps(old)))
    con.commit()
    con.close()
    s = JobStore(db)
    assert "correct" not in STAGES and s.get("old").batch_id is None
    assert [st.stage for st in s.get("old").stages] == list(STAGES)
    s.set_stage("old", "translate", "running", 0.5)            # by name, not by position in the old list
    assert stage_states(s.get("old"))["translate"] == "running"
    s.close()


def test_one_request_is_one_batch(store):
    lib = library(*(video(v, {"fr": "missing"}) for v in "abc"))
    first = store.create(JobCreate(video_ids=["a", "b"]), lib).jobs
    second = store.create(JobCreate(video_ids=["c"]), lib).jobs
    assert first[0].batch_id and first[0].batch_id == first[1].batch_id != second[0].batch_id
    store.start(first[0].id)
    store.finish(first[0].id, "done")
    assert store.get(first[0].id).batch_id == first[0].batch_id    # finished jobs still count in « x of N »


def test_log_lines_carry_the_level_once():
    import logging
    from core.events import setup_logging_bridge
    seen = []
    hook = lambda fid, payload: seen.append(payload)
    event_manager.add_listener(hook)
    h = setup_logging_bridge("/data/x.mkv")
    try:
        logging.getLogger("SubStudio.test").error("boom")
    finally:
        logging.getLogger().removeHandler(h)
        event_manager.remove_listener(hook)
    logs = [p for p in seen if p.get("type") == "log"]
    assert [(p["level"], p["message"]) for p in logs] == [("ERROR", "boom")]   # not « ERROR: boom »


def test_restart_keeps_history_and_queue_and_fails_what_was_running(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    s1 = JobStore(db)
    lib = library(video("a", {"fr": "missing"}), video("b", {"fr": "missing"}))
    a, b = s1.create(JobCreate(video_ids=["a", "b"]), lib).jobs
    s1.start(a.id)
    s1.log(a.id, "INFO", "Translating FR (10/40 lines)")
    s1.close()                                                   # the backend stops mid-job

    s2 = JobStore(db)
    assert s2.recover() == [a.id]
    ja, jb = s2.get(a.id), s2.get(b.id)
    assert (ja.state, ja.error) == ("failed", RESTART_REASON)
    assert (jb.state, jb.position) == ("queued", 0)               # still waiting, runs after the restart
    assert [l.message for l in s2.logs(a.id)] == ["Translating FR (10/40 lines)"]
    s2.close()


def test_cancel_queued_and_unknown(store):
    job = store.create(JobCreate(video_ids=["a"]), library(video("a", {"fr": "missing"}))).jobs[0]
    assert store.cancel_queued(job.id).state == "cancelled"
    assert store.cancel_queued("nope") is None


def test_logs_keep_backend_time_and_are_capped(store, monkeypatch):
    import api.job_store as js
    monkeypatch.setattr(js, "LOG_KEEP_PER_JOB", 3)
    job = store.create(JobCreate(video_ids=["a"]), library(video("a", {"fr": "missing"}))).jobs[0]
    for i in range(5):
        store.log(job.id, "WARNING", f"line {i}")
    logs = store.logs(job.id)
    assert [l.message for l in logs] == ["line 2", "line 3", "line 4"] and all(l.ts.tzinfo for l in logs)
    assert store.log(job.id, "CRITICAL", "x").level == "ERROR"


def test_verdicts_hold_only_for_the_exact_file(store, tmp_path):
    f = tmp_path / "Ep.mkv"
    f.write_bytes(b"12345")
    store.record_verdicts(str(f), os.stat(f), {"stream:2": "kept"})
    assert store.verdicts(str(f), os.stat(f)) == {"stream:2": "kept"}
    f.write_bytes(b"123456")                                     # the file changed: the verdict no longer applies
    assert store.verdicts(str(f), os.stat(f)) == {}


def test_library_reads_the_recorded_verdicts(store, tmp_path):
    from api import library as lib_mod
    f = tmp_path / "Ep.mkv"
    f.write_bytes(b"x")
    assert lib_mod._verdicts(str(f), os.stat(f)) == {}
    store.record_verdicts(str(f), os.stat(f), {"stream:3": "kept"})
    assert lib_mod._verdicts(str(f), os.stat(f)) == {"stream:3": "kept"}


def test_overlay_marks_cells_a_job_will_change(store):
    lib = library(video("a", {"en": "passed", "fr": "missing"}))
    job = store.create(JobCreate(video_ids=["a"]), lib).jobs[0]
    out = overlay(library(video("a", {"en": "passed", "fr": "missing"})), store.list())
    assert [(c.language, c.state, c.job_id) for c in out.videos[0].coverage] == [("en", "passed", None),
                                                                                  ("fr", "pending", job.id)]


# ---------------------------------------------------------------- stage mapping (what the pipeline emits)

@pytest.mark.parametrize("status, pct, expected", [
    ("processing", 5, ("check", 0.0)),          # main.py: Step 1/5 context
    ("processing", 12, ("download", 0.0)),      # ladder: Searching online
    ("processing", 15, ("transcribe", 0.0)),    # ladder: Transcribing with Whisper
    ("transcribing", 50, ("transcribe", 0.5)),  # transcriber 5..95
    ("translating", 60, ("translate", 0.5)),    # main.py 40..80
    ("processing", 90, ("write", 0.0)),         # Step 5/5
    ("processing", 100, ("write", 1.0)),        # Step 5/5: written
    ("done", 100, None), ("error", 0, None), ("cancelled", 0, None),
])
def test_stage_of(status, pct, expected):
    assert stage_of(status, pct) == expected


# ---------------------------------------------------------------- runner on the real event path

class FakeVideoJob:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeOptions:
    pass


class FakeOrchestrator:
    """Emits what execute_pipeline emits, through the real event_manager."""

    def __init__(self, ending="done", wait_for_abort=False):
        self.ending, self.wait_for_abort, self.active_jobs, self.seen = ending, wait_for_abort, set(), []

    async def execute_pipeline(self, video, opts, index, total):
        fid = video.path
        self.seen.append((video.out, video.workflowMode, video.syncOffset))
        handle = job_registry.get(fid)
        assert handle is not None                                # registered by the runner before the start
        event_manager.emit(fid, "processing", 5, "[1/1 Files] Step 1/5: Analyzing context...")
        event_manager.emit_log(fid, "INFO: two subtitles found", "INFO")
        event_manager.emit(fid, "translating", 60, "[1/1 Files] Step 4/5: Translating FR (20/40 lines)")
        if self.wait_for_abort:
            for _ in range(200):
                if handle.is_aborted:
                    event_manager.emit(fid, "cancelled", 0, "Cancelled")
                    return
                await asyncio.sleep(0.01)
        event_manager.emit(fid, "processing", 90, "[1/1 Files] Step 5/5: Writing subtitles into the video...")
        if self.ending == "done":
            event_manager.emit(fid, "done", 100, "Processing Complete: FR ✓ translated from en")
        elif self.ending == "error":
            event_manager.emit(fid, "error", 0, "FR failed: no subtitle anywhere.")


def run_job(store, orch, options=JobOptions(), cancel=False):
    job = store.create(JobCreate(video_ids=["a"], options=options), library(video("a", {"fr": "missing"}))).jobs[0]
    r = JobRunner()
    r.connect(orch, FakeVideoJob, FakeOptions, start=False)

    async def go():
        task = asyncio.create_task(r.run_one(store.next_queued()))
        if cancel:
            await asyncio.sleep(0.05)
            r.cancel(job.id)
        await task

    try:
        asyncio.run(go())
    finally:
        event_manager.remove_listener(r._on_event)
    return store.get(job.id)


def test_runner_runs_a_job_to_done_with_stages_and_logs(store):
    orch = FakeOrchestrator()
    job = run_job(store, orch, JobOptions(force_asr=True, sync_offset_ms=250))
    assert job.state == "done" and job.error is None
    assert stage_states(job) == {"check": "done", "download": "skipped", "transcribe": "skipped",
                                 "translate": "done", "write": "done"}
    assert orch.seen == [(["fr"], "whisper", 0.25)]                # options reach the pipeline
    assert [l.message for l in store.logs(job.id)] == ["INFO: two subtitles found"]
    assert job_registry.get("/data/a.mkv") is None                  # released after the run


def test_a_written_video_reads_write_done_when_a_language_failed(store):
    # S05E12, 2026-11-19: EN written, FR failed; the stepper said « write failed » about a file that was written.
    job = store.create(JobCreate(video_ids=["a"]), library(video("a", {"fr": "missing"}))).jobs[0]
    store.start(job.id)
    store.set_stage(job.id, "write", "running", 1.0)
    j = store.finish(job.id, "failed", "EN ✓ transcribed · FR failed: cue 272")
    assert j.state == "failed" and stage_states(j)["write"] == "done"
    other = store.create(JobCreate(video_ids=["b"]), library(video("b", {"fr": "missing"}))).jobs[0]
    store.start(other.id)
    store.set_stage(other.id, "write", "running", 0.0)        # crashed while writing: that stage did fail
    assert stage_states(store.finish(other.id, "failed", "swap check failed"))["write"] == "failed"


def test_runner_failure_carries_the_pipeline_sentence(store):
    job = run_job(store, FakeOrchestrator(ending="error"))
    assert (job.state, job.error) == ("failed", "FR failed: no subtitle anywhere.")


def test_runner_never_leaves_a_job_running_without_a_closing_frame(store):
    job = run_job(store, FakeOrchestrator(ending="silent"))
    assert job.state == "failed" and "without saying" in job.error


def test_cancel_stops_the_running_pipeline(store):
    job = run_job(store, FakeOrchestrator(wait_for_abort=True), cancel=True)
    assert job.state == "cancelled"
    assert stage_states(job)["write"] == "skipped"


# ---------------------------------------------------------------- routes

def test_routes_use_the_store(store):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as client:
        assert client.get("/api/jobs").json() == {"jobs": []}
        assert client.delete("/api/jobs/nope").status_code == 404
        assert client.get("/api/jobs/nope/logs").status_code == 404
        job = store.create(JobCreate(video_ids=["a"]), library(video("a", {"fr": "missing"}))).jobs[0]
        store.cancel_queued(job.id)
        store.log(job.id, "INFO", "hello")
        assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [job.id]
        assert client.get(f"/api/jobs/{job.id}/logs").json()[0]["message"] == "hello"
