# stub.py — Fake job store behind SUBSTUDIO_API_STUB=1, so the UI can be built before the real one. Exports: stub_jobs.
#
# Nothing here touches a file: jobs advance on a timer and write nothing. The UI shows a « stub »
# banner whenever /api/system says stub=true, so this can never pass for real progress.

import asyncio
import itertools
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .events_hub import hub
from .schemas import (Cue, CueList, Job, JobCreate, JobCreateResponse, JobEvent, LibraryResponse,
                      LogEvent, StageProgress)

STAGES = ("check", "download", "transcribe", "correct", "translate", "write")
TICK_S = 0.5
TICKS_PER_STAGE = 8


def _now():
    return datetime.now(timezone.utc)


class StubJobs:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._runner: Optional[asyncio.Task] = None

    def create(self, req: JobCreate, library: Optional[LibraryResponse]) -> JobCreateResponse:
        videos = {v.id: v for v in (library.videos if library else [])}
        created, skipped = [], []
        for vid in req.video_ids:
            v = videos.get(vid)
            if v is None:
                skipped.append(vid)
                continue
            missing = [c.language for c in v.coverage if c.state in ("missing", "failed", "unknown")]
            langs = req.languages if req.languages is not None else missing
            busy = {j.video_id for j in self.jobs.values() if j.state in ("queued", "running")}
            if not langs or vid in busy:
                skipped.append(vid)
                continue
            job = Job(id=f"stub-{next(self._ids)}", video_id=vid, video_name=v.name, languages=langs,
                      state="queued", stages=[StageProgress(stage=s, state="pending") for s in STAGES],
                      created_at=_now())
            self.jobs[job.id] = job
            created.append(job)
        self._renumber()
        for j in created:
            hub.publish(JobEvent(job=self.jobs[j.id]))
        if created and (self._runner is None or self._runner.done()):
            self._runner = asyncio.create_task(self._run())
        return JobCreateResponse(jobs=[self.jobs[j.id] for j in created], skipped=skipped)

    def list(self) -> List[Job]:
        order = {"running": 0, "queued": 1}
        return sorted(self.jobs.values(), key=lambda j: (
            order.get(j.state, 2), j.position if j.position is not None else 0,
            -(j.finished_at or j.created_at).timestamp()))

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.state in ("queued", "running"):
            job = self._set(job, state="cancelled", finished_at=_now(), position=None)
            self._renumber()
        return job

    def overlay(self, library: LibraryResponse) -> LibraryResponse:
        """Put queued/running/failed job states into the coverage cells they will change (one rule: job_store)."""
        from .job_store import overlay
        return overlay(library, self.jobs.values())

    def cues(self, video_id: str, lang: str) -> CueList:
        lines = [("Where are you going?", "Où vas-tu ?"), ("Upstairs. Don't wait for me.", "En haut. Ne m'attends pas.")]
        return CueList(video_id=video_id, language=lang, origin="translated", cues=[
            Cue(id=i + 1, start_ms=1000 + i * 2500, end_ms=3000 + i * 2500, text=t, source_text=s)
            for i, (s, t) in enumerate(lines)])

    # --- internals ---

    def _set(self, job: Job, **update) -> Job:
        job = job.model_copy(update=update)
        self.jobs[job.id] = job
        hub.publish(JobEvent(job=job))
        return job

    def _renumber(self):
        queued = sorted((j for j in self.jobs.values() if j.state == "queued"), key=lambda j: j.created_at)
        for pos, j in enumerate(queued):
            if j.position != pos:
                self.jobs[j.id] = j.model_copy(update={"position": pos})

    async def _run(self):
        while True:
            queued = [j for j in self.jobs.values() if j.state == "queued"]
            if not queued:
                return
            job = min(queued, key=lambda j: j.position or 0)
            job = self._set(job, state="running", started_at=_now(), position=None)
            self._renumber()
            for si, stage in enumerate(STAGES):
                for t in range(1, TICKS_PER_STAGE + 1):
                    await asyncio.sleep(TICK_S)
                    job = self.jobs[job.id]
                    if job.state == "cancelled":
                        break
                    stages = list(job.stages)
                    stages[si] = StageProgress(stage=stage, state="running", progress=t / TICKS_PER_STAGE,
                                               eta_s=(TICKS_PER_STAGE - t) * TICK_S)
                    job = self._set(job, stages=stages)
                if self.jobs[job.id].state == "cancelled":
                    break
                stages = list(job.stages)
                stages[si] = StageProgress(stage=stage, state="done", progress=1.0)
                job = self._set(job, stages=stages)
                hub.publish(LogEvent(job_id=job.id, ts=_now(), level="INFO", message=f"[stub] {stage} done"))
            if self.jobs[job.id].state == "running":
                self._set(self.jobs[job.id], state="done", finished_at=_now())


stub_jobs = StubJobs()
