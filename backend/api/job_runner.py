# job_runner.py — Runs the stored job queue through the real pipeline, one job at a time (D-0001), and turns the pipeline's events into job stages and logs. Exports: JobRunner, job_runner, stage_of.
"""main.py connects it once at start-up (lifespan):  job_runner.connect(orchestrator, VideoJob, GlobalOptions).
Until then the API refuses to queue anything (a queue nobody runs would look like « waiting », a lie).

The pipeline still speaks the per-file events of core/events (status, percent, message). stage_of() maps them to the
UI stepper (D-0002 §5) from the statuses and percents the pipeline actually emits (main.py, core/ladder.py,
core/transcriber.py, read 2026-11-19). The pipeline reporting its stage itself is the better end state: TODOLIST.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from .job_store import job_store
from .schemas import Job

logger = logging.getLogger("SubStudio.JobRunner")
BUSY_RETRY_S = 5.0


def stage_of(status: str, pct: int) -> Optional[Tuple[str, float]]:
    """(stage, progress within it) for one pipeline status frame; None for a frame that closes the job."""
    if status == "transcribing":
        return "transcribe", max(0.0, min(1.0, pct / 100))
    if status == "translating":
        return "translate", max(0.0, min(1.0, (pct - 40) / 40))
    if status == "processing":
        if pct >= 90:
            return "write", 0.0
        if pct >= 15:
            return "transcribe", 0.0          # ladder rung 4 starts: « Transcribing with Whisper... »
        if pct >= 12:
            return "download", 0.0            # ladder rung 2: « Searching online ... »
        return "check", 0.0                   # context and rung 1: what is already there
    return None


class JobRunner:
    def __init__(self):
        self._orch = None
        self._video_job_cls = None
        self._options_cls = None
        self._task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        self._current: Optional[Tuple[str, str]] = None       # (job id, video path) while one runs
        self._cancelled: set = set()

    @property
    def connected(self) -> bool:
        return self._orch is not None

    def connect(self, orchestrator, video_job_cls, options_cls, *, start: bool = True):
        """Called once by main.py's lifespan. Marks jobs a stopped backend left running as failed, then runs the
        queue (queued jobs from before the restart included)."""
        self._orch, self._video_job_cls, self._options_cls = orchestrator, video_job_cls, options_cls
        job_store().recover()
        from core.events import event_manager
        event_manager.add_listener(self._on_event)
        if start:
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._loop())
            self._wake.set()

    async def stop(self):
        from core.events import event_manager
        event_manager.remove_listener(self._on_event)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._orch = None

    def kick(self):
        """New work was queued: wake the loop."""
        if self._wake is not None:
            self._wake.set()

    def cancel(self, job_id: str) -> Optional[Job]:
        """Queued: cancelled at once. Running: the pipeline is aborted; its « cancelled » frame closes the job."""
        store = job_store()
        job = store.get(job_id)
        if job is None:
            return None
        if job.state == "queued":
            return store.cancel_queued(job_id)
        if job.state == "running" and self._current and self._current[0] == job_id:
            from core.jobs import job_registry
            self._cancelled.add(job_id)
            job_registry.abort(self._current[1])   # registered before the pipeline started: always found
        return store.get(job_id)

    # ------------------------------------------------------------ the loop

    async def _loop(self):
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                job = job_store().next_queued()
                if job is None:
                    break
                if self._busy():
                    # A legacy /api/process batch holds the machine (D-0001: one heavy job): wait, never overlap.
                    await asyncio.sleep(BUSY_RETRY_S)
                    continue
                await self.run_one(job)

    def _busy(self) -> bool:
        from core.jobs import job_registry
        return bool(getattr(self._orch, "active_jobs", None)) or bool(job_registry.active_ids())

    async def run_one(self, job: Job):
        from core.jobs import job_registry
        store = job_store()
        path = store.video_path(job.id)
        if store.start(job.id) is None:
            return                                   # cancelled between « next » and « start »
        self._current = (job.id, path)
        # Registered BEFORE the pipeline starts, as run_batch does, so a Cancel in the first second is never lost.
        job_registry.register(path)
        opts = store.options(job.id)
        video = self._video_job_cls(name=job.video_name, path=path, out=list(job.languages),
                                    syncOffset=opts.sync_offset_ms / 1000,
                                    workflowMode="whisper" if opts.force_asr else "hybrid",
                                    selectedSrtPath=opts.source_track if opts.source_track and
                                    not opts.source_track.startswith("stream:") else None)
        store.set_stage(job.id, "check", "running")
        try:
            await self._orch.execute_pipeline(video, self._options_cls(), 0, 1)
        except Exception as e:                       # execute_pipeline catches its own errors; this is a bug guard
            logger.exception("Pipeline crashed for job %s", job.id)
            store.finish(job.id, "failed", f"The pipeline crashed: {e}")
        finally:
            self._current = None
            job_registry.release(path)
        final = store.get(job.id)
        if final is not None and final.state == "running":
            # The pipeline ended without a closing frame: never leave a job « running » forever.
            store.finish(job.id, "failed", "The pipeline stopped without saying how it ended.")

    # ------------------------------------------------------------ pipeline events → job

    def _on_event(self, file_id: str, frame: Dict[str, Any]):
        cur = self._current
        if cur is None or file_id != cur[1]:
            return
        job_id = cur[0]
        store = job_store()
        kind = frame.get("type")
        if kind == "log":
            store.log(job_id, frame.get("level", "INFO"), frame.get("message", ""))
            return
        if kind != "status":
            return
        status, pct, msg = frame.get("status"), int(frame.get("progress") or 0), frame.get("message") or ""
        if status == "done":
            store.finish(job_id, "done")
        elif status == "error":
            # Partly done is not done (main._emit_outcome): the message is the per-language summary.
            store.finish(job_id, "failed", msg or "The job failed.")
        elif status == "cancelled":
            store.finish(job_id, "cancelled")
        else:
            mapped = stage_of(status, pct)
            if mapped:
                stage, progress = mapped
                store.set_stage(job_id, stage, "running", progress, detail=msg[:200] or None)


job_runner = JobRunner()
