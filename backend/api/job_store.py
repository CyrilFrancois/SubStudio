# job_store.py — The job queue, its history, per-job logs and the quality-gate verdicts, in one SQLite file that survives a restart. Exports: JobStore, job_store, use_store, overlay, STAGES.
"""What the Queue screen needs to survive a reload or a backend restart (ux-review §2, D-0002 §5):

- jobs     one row per video × requested languages: state, stages (JSON), times, one-sentence error;
- logs     every line a job emitted, with the backend's timestamp, so a fresh tab shows the history;
- verdicts the quality gate's verdict on each pre-existing subtitle, per exact file (path + mtime + size): its
           origin when accepted (« kept »), provenance.REJECTED when refused; read by api/library._verdicts() so
           a kept subtitle counts as coverage after a rescan and a refused one says why.

Every change of a job publishes a full JobEvent on api.events_hub.hub. A job that was running when the backend
stopped is marked failed on the next start (recover()): it never looks « running » forever.
SQLite with one connection behind a lock: writes come from the event loop and from pipeline threads.
"""

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import STATE_DIR
from .events_hub import hub
from .schemas import Job, JobCreate, JobCreateResponse, JobEvent, JobOptions, LibraryChangedEvent, LibraryResponse, \
    LogEvent, StageProgress

logger = logging.getLogger("SubStudio.JobStore")

# No « correct »: speech-to-text ships without a correction pass (D-0005); a stage that never runs would keep the
# bar under 100 %. Jobs stored with it are read without it (_row_to_job).
STAGES = ("check", "download", "transcribe", "translate", "write")
LOG_KEEP_PER_JOB = 2000            # a 45-min episode emits a few hundred lines; the cap only stops a runaway loop
RESTART_REASON = "Interrupted: the backend stopped while this job was running. Start it again."

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, video_id TEXT NOT NULL, video_path TEXT NOT NULL, video_name TEXT NOT NULL,
    languages TEXT NOT NULL, options TEXT NOT NULL, state TEXT NOT NULL, stages TEXT NOT NULL,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created_at);
CREATE TABLE IF NOT EXISTS logs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, ts TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS logs_job ON logs(job_id, seq);
CREATE TABLE IF NOT EXISTS verdicts (
    path_key TEXT NOT NULL, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, track_key TEXT NOT NULL,
    origin TEXT NOT NULL, PRIMARY KEY (path_key, track_key));
"""


def overlay(library: LibraryResponse, jobs: Iterable[Job]) -> LibraryResponse:
    """Puts queued/running/failed job states into the coverage cells they will change (stub and store alike)."""
    latest: Dict[tuple, Job] = {}
    for j in sorted(jobs, key=lambda j: j.created_at):
        for lang in j.languages:
            latest[(j.video_id, lang)] = j
    for v in library.videos:
        for i, c in enumerate(v.coverage):
            j = latest.get((v.id, c.language))
            if j and j.state in ("queued", "running", "failed") and c.state != "passed":
                state = "pending" if j.state == "queued" else j.state
                v.coverage[i] = c.model_copy(update={"state": state, "job_id": j.id, "reason": j.error})
    return library


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            cols = {r["name"] for r in self._db.execute("PRAGMA table_info(jobs)")}
            if "batch_id" not in cols:   # a store created before batches: add the column, old jobs have none
                self._db.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")

    # ------------------------------------------------------------ reading

    def _row_to_job(self, r: sqlite3.Row, position: Optional[int]) -> Job:
        return Job(id=r["id"], video_id=r["video_id"], video_name=r["video_name"],
                   languages=json.loads(r["languages"]), state=r["state"], position=position,
                   batch_id=r["batch_id"],
                   stages=[StageProgress(**s) for s in json.loads(r["stages"]) if s["stage"] in STAGES],
                   created_at=r["created_at"], started_at=r["started_at"], finished_at=r["finished_at"],
                   error=r["error"])

    def _positions(self) -> Dict[str, int]:
        rows = self._db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created_at, rowid").fetchall()
        return {r["id"]: i for i, r in enumerate(rows)}

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            r = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row_to_job(r, self._positions().get(job_id)) if r else None

    def video_path(self, job_id: str) -> Optional[str]:
        with self._lock:
            r = self._db.execute("SELECT video_path FROM jobs WHERE id=?", (job_id,)).fetchone()
            return r["video_path"] if r else None

    def options(self, job_id: str) -> JobOptions:
        with self._lock:
            r = self._db.execute("SELECT options FROM jobs WHERE id=?", (job_id,)).fetchone()
        return JobOptions(**json.loads(r["options"])) if r else JobOptions()

    def list(self) -> List[Job]:
        """Running first, then queued by position, then history newest first (schemas.JobList)."""
        with self._lock:
            pos = self._positions()
            rows = self._db.execute("SELECT * FROM jobs").fetchall()
            jobs = [self._row_to_job(r, pos.get(r["id"])) for r in rows]
        rank = {"running": 0, "queued": 1}
        return sorted(jobs, key=lambda j: (rank.get(j.state, 2),
                                           j.position if j.position is not None else 0,
                                           -(j.finished_at or j.created_at).timestamp()))

    def next_queued(self) -> Optional[Job]:
        with self._lock:
            r = self._db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            return self._row_to_job(r, 0) if r else None

    def logs(self, job_id: str, limit: int = 500) -> List[LogEvent]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM (SELECT * FROM logs WHERE job_id=? ORDER BY seq DESC LIMIT ?) "
                                    "ORDER BY seq", (job_id, limit)).fetchall()
        return [LogEvent(job_id=r["job_id"], ts=r["ts"], level=r["level"], message=r["message"]) for r in rows]

    def active(self) -> List[Job]:
        return [j for j in self.list() if j.state in ("queued", "running")]

    # ------------------------------------------------------------ writing

    def _publish(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        if job is not None:
            hub.publish(JobEvent(job=job))
        return job

    def _republish_queue(self):
        # Positions shift when a job leaves the queue: every queued job's frame is a full snapshot.
        for j in self.active():
            if j.state == "queued":
                hub.publish(JobEvent(job=j))

    def create(self, req: JobCreate, library: Optional[LibraryResponse]) -> JobCreateResponse:
        """One job per video with something to do. « Fill missing » (languages=None) takes the cells that are not
        passed; a video already queued or running is skipped, never queued twice."""
        videos = {v.id: v for v in (library.videos if library else [])}
        created, skipped = [], []
        batch = uuid.uuid4().hex[:12]   # one per request: the Queue's « x of N » counts its jobs, finished ones too
        with self._lock:
            busy = {r["video_id"] for r in self._db.execute(
                "SELECT video_id FROM jobs WHERE state IN ('queued','running')").fetchall()}
            for vid in req.video_ids:
                v = videos.get(vid)
                if v is None or vid in busy:
                    skipped.append(vid)
                    continue
                todo = [c.language for c in v.coverage if c.state != "passed"]
                langs = [l for l in (req.languages if req.languages is not None else todo)]
                if not langs:
                    skipped.append(vid)
                    continue
                job_id = uuid.uuid4().hex[:12]
                stages = [StageProgress(stage=s, state="pending").model_dump() for s in STAGES]
                self._db.execute(
                    "INSERT INTO jobs (id, video_id, video_path, video_name, languages, options, state, stages, "
                    "created_at, batch_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (job_id, vid, v.path, v.name, json.dumps(langs), req.options.model_dump_json(), "queued",
                     json.dumps(stages), _iso(_now()), batch))
                busy.add(vid)
                created.append(job_id)
        jobs = [self._publish(j) for j in created]
        return JobCreateResponse(jobs=jobs, skipped=skipped)

    def _update(self, job_id: str, **fields) -> Optional[Job]:
        if not fields:
            return self.get(job_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        return self._publish(job_id)

    def start(self, job_id: str) -> Optional[Job]:
        with self._lock:
            n = self._db.execute("UPDATE jobs SET state='running', started_at=? WHERE id=? AND state='queued'",
                                 (_iso(_now()), job_id)).rowcount
        if not n:
            return None   # cancelled in the meantime
        job = self._publish(job_id)
        self._republish_queue()
        return job

    def set_stage(self, job_id: str, stage: str, state: str, progress: float = 0.0,
                  eta_s: Optional[float] = None, detail: Optional[str] = None) -> Optional[Job]:
        """Moves the stepper: earlier running stages become done, earlier pending ones skipped (or done when they
        had started). One stage runs at a time: the ladder may transcribe after a translation began, so a later
        running stage pauses back to pending, keeping its progress. The bar of a stage never goes backwards
        (D-0002 §5): a lower progress for the same running stage is ignored."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage {stage!r}")
        with self._lock:
            r = self._db.execute("SELECT stages, state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if r is None or r["state"] != "running":
                return None
            stages = json.loads(r["stages"])
            stages = [s for s in stages if s["stage"] in STAGES]   # a job stored with « correct » (before D-0005)
            target = [s["stage"] for s in stages].index(stage)
            for i, s in enumerate(stages):
                if i < target and (s["state"] == "running" or (s["state"] == "pending" and s["progress"] > 0)):
                    s.update(state="done", progress=1.0, eta_s=None)
                elif i < target and s["state"] == "pending":
                    s.update(state="skipped")
                elif i > target and s["state"] == "running":
                    s.update(state="pending", eta_s=None)
            cur = stages[target]
            if state == "running" and cur["state"] in ("running", "pending") and progress < cur["progress"]:
                progress = cur["progress"]
            cur.update(state=state, progress=max(0.0, min(1.0, progress)), eta_s=eta_s,
                       detail=detail if detail is not None else cur.get("detail"))
            self._db.execute("UPDATE jobs SET stages=? WHERE id=?", (json.dumps(stages), job_id))
        return self._publish(job_id)

    def finish(self, job_id: str, state: str, error: Optional[str] = None) -> Optional[Job]:
        """done | failed | cancelled. Open stages close: running → done (or failed), pending → skipped. A stage
        that reported 100 % is done whatever the job's end: S05E12 (2026-11-19) wrote EN, failed FR, and read
        « write failed » although the file was written."""
        if state not in ("done", "failed", "cancelled"):
            raise ValueError(f"Not a final state: {state!r}")
        with self._lock:
            r = self._db.execute("SELECT stages, state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if r is None or r["state"] in ("done", "failed", "cancelled"):
                return self.get(job_id)
            stages = [s for s in json.loads(r["stages"]) if s["stage"] in STAGES]
            for s in stages:
                if s["state"] == "pending" and s["progress"] > 0 and state == "done":
                    s.update(state="done", progress=1.0, eta_s=None)   # paused by a later frame, then finished
                elif s["state"] == "running" and s["progress"] >= 1.0:
                    s.update(state="done", eta_s=None)
                elif s["state"] == "running":
                    s.update(state="done" if state == "done" else "failed" if state == "failed" else "skipped",
                             eta_s=None, progress=1.0 if state == "done" else s["progress"])
                elif s["state"] == "pending":
                    s.update(state="skipped")
            self._db.execute("UPDATE jobs SET state=?, stages=?, finished_at=?, error=? WHERE id=?",
                             (state, json.dumps(stages), _iso(_now()), error, job_id))
            vid = self._db.execute("SELECT video_id FROM jobs WHERE id=?", (job_id,)).fetchone()["video_id"]
        job = self._publish(job_id)
        self._republish_queue()
        if state == "done" or state == "failed":
            hub.publish(LibraryChangedEvent(video_ids=[vid]))   # a passed language may have been written
        return job

    def cancel_queued(self, job_id: str) -> Optional[Job]:
        """A queued job is cancelled at once; a running one is the runner's to stop (it calls finish())."""
        with self._lock:
            r = self._db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r is None:
            return None
        if r["state"] == "queued":
            return self.finish(job_id, "cancelled")
        return self.get(job_id)

    def log(self, job_id: Optional[str], level: str, message: str) -> LogEvent:
        level = level if level in ("DEBUG", "INFO", "WARNING", "ERROR") else ("ERROR" if level == "CRITICAL" else "INFO")
        ev = LogEvent(job_id=job_id, ts=_now(), level=level, message=message)
        with self._lock:
            self._db.execute("INSERT INTO logs (job_id, ts, level, message) VALUES (?,?,?,?)",
                             (job_id, _iso(ev.ts), level, message))
            if job_id is not None:
                self._db.execute("DELETE FROM logs WHERE job_id=? AND seq <= (SELECT seq FROM logs WHERE job_id=? "
                                 "ORDER BY seq DESC LIMIT 1 OFFSET ?)", (job_id, job_id, LOG_KEEP_PER_JOB))
        hub.publish(ev)
        return ev

    def recover(self) -> List[str]:
        """At start-up: a job left « running » by a stopped backend is failed with a sentence saying so.
        Queued jobs stay queued and run again."""
        with self._lock:
            ids = [r["id"] for r in self._db.execute("SELECT id FROM jobs WHERE state='running'").fetchall()]
        for job_id in ids:
            self.finish(job_id, "failed", RESTART_REASON)
            logger.warning("Job %s was running when the backend stopped: marked failed", job_id)
        return ids

    # ------------------------------------------------------------ verdicts

    def record_verdicts(self, path: str, st: os.stat_result, verdicts: Dict[str, str]):
        """{track_key: origin} for subtitles of this exact file the quality gate accepted and left untouched,
        {track_key: provenance.REJECTED} for those it refused (output/run.verdict_record). Replaces the file's
        earlier record: a new verdict is about the file as it is now."""
        key = _path_key(path)
        with self._lock:
            self._db.execute("DELETE FROM verdicts WHERE path_key=?", (key,))
            self._db.executemany("INSERT INTO verdicts VALUES (?,?,?,?,?)",
                                 [(key, st.st_mtime_ns, st.st_size, k, o) for k, o in verdicts.items()])

    def verdicts(self, path: str, st: os.stat_result) -> Dict[str, str]:
        """The record for this file only if it is still the exact same file (mtime and size): a changed file has
        no verdict, so nothing unmarked counts by accident."""
        with self._lock:
            rows = self._db.execute("SELECT track_key, origin, mtime_ns, size FROM verdicts WHERE path_key=?",
                                    (_path_key(path),)).fetchall()
        return {r["track_key"]: r["origin"] for r in rows
                if r["mtime_ns"] == st.st_mtime_ns and r["size"] == st.st_size}

    def close(self):
        with self._lock:
            self._db.close()


_store: Optional[JobStore] = None
_store_lock = threading.Lock()


def job_store() -> JobStore:
    """The process's store, opened on first use (never at import: a test or a tool importing the API must not
    create the state file)."""
    global _store
    with _store_lock:
        if _store is None:
            _store = JobStore(STATE_DIR / "jobs.sqlite3")
        return _store


def use_store(store: Optional[JobStore]) -> None:
    """Tests: point the API at a temporary store (None resets to the default on next use)."""
    global _store
    with _store_lock:
        _store = store
