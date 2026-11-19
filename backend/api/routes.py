# routes.py — FastAPI routes of the UI contract (ux-review §3): library, jobs, events, subtitles, settings, system. Exports: router.

import asyncio
import logging
import os
from typing import Iterable, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from . import library
from .config import MEDIA_ROOT, STUB
from .events_hub import hub
from .schemas import (CueList, EventFrame, EventFrameDoc, Job, JobCreate, JobCreateResponse, JobEvent,
                      JobList, LibraryResponse, Settings, SettingsResponse, SystemStatus)
from .settings_store import load_settings, provider_status, save_settings

logger = logging.getLogger("SubStudio.API")
router = APIRouter(prefix="/api", tags=["ui"])

# Until task 2 (job store) lands, the real backend has no job model to expose. Saying so beats an
# empty queue that looks like « nothing to do ».
NO_JOB_STORE = "The job queue is not built yet (SubStudio task « job store »); run with SUBSTUDIO_API_STUB=1 to try the screens."

_last_library: Optional[LibraryResponse] = None


def _stub():
    from .stub import stub_jobs  # imported only in stub mode: its timer task must never run for real
    return stub_jobs


def _settings() -> Settings:
    try:
        return load_settings()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Library ---------------------------------------------------------------------------------

@router.get("/library", response_model=LibraryResponse)
async def get_library():
    global _last_library
    settings = _settings()
    try:
        lib = await asyncio.to_thread(library.scan_library, settings)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    _last_library = lib
    return _stub().overlay(lib) if STUB else lib


# --- Jobs ------------------------------------------------------------------------------------

@router.post("/jobs", response_model=JobCreateResponse)
async def create_jobs(req: JobCreate):
    if not STUB:
        raise HTTPException(status_code=501, detail=NO_JOB_STORE)
    lib = _last_library or await get_library()
    return _stub().create(req, lib)


@router.get("/jobs", response_model=JobList)
async def list_jobs():
    if not STUB:
        raise HTTPException(status_code=501, detail=NO_JOB_STORE)
    return JobList(jobs=_stub().list())


@router.delete("/jobs/{job_id}", response_model=Job)
async def cancel_job(job_id: str):
    if not STUB:
        raise HTTPException(status_code=501, detail=NO_JOB_STORE)
    job = _stub().cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return job


# --- Events ----------------------------------------------------------------------------------

def _snapshot() -> Iterable[EventFrame]:
    if not STUB:
        return []
    return [JobEvent(job=j) for j in _stub().list() if j.state in ("queued", "running")]


@router.get("/events", response_model=None, responses={200: {
    "model": EventFrameDoc,
    "description": "text/event-stream; each `data:` line is EventFrameDoc.frame (discriminated by `kind`). "
                   "On connect: one job frame per unfinished job, then live frames.",
}})
async def events():
    return StreamingResponse(hub.stream(_snapshot), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Subtitles -------------------------------------------------------------------------------

@router.get("/subtitles/{video_id}/{lang}", response_model=CueList)
async def get_subtitles(video_id: str, lang: str):
    if library.video_path(video_id) is None:
        raise HTTPException(status_code=404, detail="Unknown video id; reload the library")
    if not STUB:
        raise HTTPException(status_code=501, detail="The cue editor needs the typed cue list and the job store (Phase B).")
    return _stub().cues(video_id, lang)


@router.put("/subtitles/{video_id}/{lang}", response_model=CueList)
async def put_subtitles(video_id: str, lang: str, body: CueList):
    if not STUB:
        raise HTTPException(status_code=501, detail="The cue editor needs the typed cue list and the job store (Phase B).")
    raise HTTPException(status_code=501, detail="Saving cues is not stubbed: nothing would be written.")


# --- Settings and system ---------------------------------------------------------------------

@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    return SettingsResponse(settings=_settings(), providers=provider_status())


@router.put("/settings", response_model=SettingsResponse)
async def put_settings(body: Settings):
    try:
        saved = await asyncio.to_thread(save_settings, body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except OSError as e:
        logger.error(f"Could not save settings: {e}")
        raise HTTPException(status_code=500, detail=f"Could not save settings: {e.strerror}")
    return SettingsResponse(settings=saved, providers=provider_status())


try:
    import psutil
    _proc = psutil.Process(os.getpid())
    psutil.cpu_percent(interval=None)  # primes the counter: the first call always returns 0.0
except ImportError:  # psutil is in requirements.txt; absent only in a stripped environment
    psutil = None


@router.get("/system", response_model=SystemStatus)
async def system():
    cpu = ram = None
    if psutil is not None:
        cpu = psutil.cpu_percent(interval=None)
        ram = int(_proc.memory_info().rss / (1024 * 1024))
    return SystemStatus(stub=STUB, media_root=MEDIA_ROOT, cpu_percent=cpu, ram_used_mb=ram)
