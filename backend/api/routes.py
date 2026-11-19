# routes.py — FastAPI routes of the UI contract (ux-review §3): library, jobs, events, subtitles, settings, system. Exports: router.

import asyncio
import logging
import os
from typing import Iterable, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from . import library, subtitles
from .config import MEDIA_ROOT, STUB
from .events_hub import hub
from .review import flag_cues
from .job_runner import job_runner
from .job_store import job_store, overlay
from .schemas import (CueList, EventFrame, EventFrameDoc, Job, JobCreate, JobCreateResponse, JobEvent,
                      JobList, LibraryResponse, LogEvent, Settings, SettingsResponse, SystemStatus)
from .settings_store import load_settings, provider_status, save_settings

logger = logging.getLogger("SubStudio.API")
router = APIRouter(prefix="/api", tags=["ui"])

# A queue nobody runs would look like « waiting »: until main.py has connected the runner, refuse to queue.
NOT_CONNECTED = "The pipeline is not connected to the job queue yet (backend still starting?); try again in a moment."

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
    except FileNotFoundError as e:  # only raised for the media root itself
        raise HTTPException(status_code=503, detail=str(e))
    except OSError as e:
        logger.error(f"Library scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read the media folder: {e.strerror or e}")
    _last_library = lib
    return _stub().overlay(lib) if STUB else overlay(lib, job_store().list())


# --- Jobs ------------------------------------------------------------------------------------

@router.post("/jobs", response_model=JobCreateResponse)
async def create_jobs(req: JobCreate):
    if STUB:
        lib = _last_library or await get_library()
        return _stub().create(req, lib)
    if not job_runner.connected:
        raise HTTPException(status_code=503, detail=NOT_CONNECTED)
    lib = _last_library or await get_library()
    created = await asyncio.to_thread(job_store().create, req, lib)
    job_runner.kick()
    return created


@router.get("/jobs", response_model=JobList)
async def list_jobs():
    return JobList(jobs=_stub().list() if STUB else await asyncio.to_thread(job_store().list))


@router.delete("/jobs/{job_id}", response_model=Job)
async def cancel_job(job_id: str):
    job = _stub().cancel(job_id) if STUB else job_runner.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return job


@router.get("/jobs/{job_id}/logs", response_model=List[LogEvent])
async def job_logs(job_id: str, limit: int = 500):
    """A job's log lines with the backend's timestamps, oldest first, so a fresh tab shows its history."""
    if STUB:
        return []
    if job_store().get(job_id) is None:
        raise HTTPException(status_code=404, detail="No such job")
    return await asyncio.to_thread(job_store().logs, job_id, max(1, min(limit, 2000)))


# --- Events ----------------------------------------------------------------------------------

def _snapshot() -> Iterable[EventFrame]:
    jobs = _stub().list() if STUB else job_store().active()
    return [JobEvent(job=j) for j in jobs if j.state in ("queued", "running")]


@router.get("/events", response_model=None, responses={200: {
    "model": EventFrameDoc,
    "description": "text/event-stream; each `data:` line is EventFrameDoc.frame (discriminated by `kind`). "
                   "On connect: one job frame per unfinished job, then live frames.",
}})
async def events():
    return StreamingResponse(hub.stream(_snapshot), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Subtitles -------------------------------------------------------------------------------

def _video(video_id: str) -> str:
    path = library.video_path(video_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Unknown video id; reload the library")
    return path


async def _cues(fn, *args) -> CueList:
    """Runs one api/subtitles call off the event loop (ffprobe, ffmpeg, the swap) and turns its refusal into HTTP."""
    if not job_runner.connected:
        raise HTTPException(status_code=503, detail=NOT_CONNECTED)
    try:
        return await asyncio.to_thread(fn, job_runner.orchestrator, *args)
    except subtitles.SubtitleError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.get("/subtitles/{video_id}/{lang}", response_model=CueList)
async def get_subtitles(video_id: str, lang: str):
    """The video's SubStudio track in `lang` ('ov' = the audio's language): cues by position, with the text each was
    translated from while the run that made it is still in memory."""
    path = _video(video_id)
    if STUB:
        return flag_cues(_stub().cues(video_id, lang))
    # Flags come from the gate's rules here, whatever store served the cues.
    return flag_cues(await _cues(subtitles.get_cue_list, video_id, path, lang))


@router.put("/subtitles/{video_id}/{lang}", response_model=CueList)
async def put_subtitles(video_id: str, lang: str, body: CueList):
    """Saves the edited text of every cue (ids and timings must be the file's: 409 otherwise) by the verified swap,
    and marks the track reviewed. Answers the saved list, checked again; the video id changes only if the file was
    renamed. flags and limits in the body are ignored."""
    path = _video(video_id)
    if STUB:
        raise HTTPException(status_code=501, detail="Saving is not simulated in stub mode: nothing would be written.")
    return flag_cues(await _cues(subtitles.save_cue_list, video_id, path, lang, body))


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
