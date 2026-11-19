# main.py — FastAPI app: the routes, the lifespan that builds the PipelineOrchestrator (core/pipeline.py), SSE events. Exports: app.

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Set

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.pipeline import MEDIA_ROOT, GlobalOptions, PipelineOrchestrator, VideoJob
from core.output import current_path
from core.events import event_manager
from core.jobs import job_registry
from api import router as api_router
from api.job_runner import job_runner

# --- LOGGING CONFIGURATION ---
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("SubStudio.Main")

# --- REQUEST MODELS ---

class ProcessRequest(BaseModel):
    videos: List[VideoJob]
    globalOptions: GlobalOptions

# --- LIFESPAN ---
orchestrator = None
_jobs: Set[asyncio.Task] = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    orchestrator = PipelineOrchestrator()
    # The pipeline runs off-loop; EventManager needs this loop to hand SSE data back safely.
    event_manager.bind_loop(asyncio.get_running_loop())
    job_runner.connect(orchestrator, VideoJob, GlobalOptions)  # the stored queue (/api/jobs) runs through this pipeline
    yield
    await job_runner.stop()

# --- API ---
app = FastAPI(title="SubStudio Pro", lifespan=lifespan)

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/api/scan")
async def scan(target_path: str = Query(MEDIA_ROOT)):
    # scanner.scan() shells out to ffprobe per file; keep it off the event loop.
    files = await asyncio.to_thread(orchestrator.get_files, target_path)
    return {"files": files}

@app.post("/api/process")
async def process(request: ProcessRequest):
    # One job at a time on this PC (D-0001: one heavy model), and a queued video counts as busy too.
    if orchestrator.active_jobs or job_registry.active_ids():
        raise HTTPException(status_code=409, detail="Another job is running: start again once it has finished")
    # NOT BackgroundTasks: uvicorn withholds the response until those finish, which would hang
    # this POST for the whole batch. create_task returns to the client immediately.
    task = asyncio.create_task(orchestrator.run_batch(request.videos, request.globalOptions))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    task.add_done_callback(_log_job_result)
    return {"status": "accepted", "count": len(request.videos)}

class RetryRequest(BaseModel):
    video: VideoJob
    language: str
    globalOptions: GlobalOptions

@app.post("/api/retry")
async def retry(request: RetryRequest):
    """Retry one video × language (D-0002): needs that video's last run in this backend's memory."""
    fid = request.video.path
    run = orchestrator.runs.get(fid)
    if run is None or request.language not in run.cells:
        raise HTTPException(status_code=404, detail="No earlier run of this video and language: process it first")
    # One job at a time on this PC (D-0001: one heavy model), and a queued video counts as busy too.
    if orchestrator.active_jobs or job_registry.active_ids():
        raise HTTPException(status_code=409, detail="Another job is running: retry once it has finished")
    if not current_path(run).is_file():
        raise HTTPException(status_code=409, detail=f"The video is no longer there: {current_path(run).name}")
    orchestrator.active_jobs.add(fid)  # claimed before returning: a second retry in the same instant gets 409
    task = asyncio.create_task(orchestrator.retry_cell(request.video, request.language, request.globalOptions))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    task.add_done_callback(_log_job_result)
    return {"status": "accepted", "file": fid, "language": request.language}

@app.get("/api/cells/{file_id:path}")
async def cells(file_id: str):
    run = orchestrator.runs.get(file_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No run of this video yet")
    return {"fileId": file_id, "cells": run.to_json()}

@app.get("/api/events/{file_id:path}")
async def events(file_id: str):
    return StreamingResponse(event_manager.subscribe(file_id), media_type="text/event-stream")

@app.post("/api/abort")
async def abort_all():
    aborted = job_registry.abort_all()
    logger.warning(f"🛑 Global abort requested — {len(aborted)} job(s) signalled")
    return {"status": "aborting", "count": len(aborted), "files": aborted}

@app.delete("/api/abort/{file_id:path}")
async def abort_one(file_id: str):
    if not job_registry.abort(file_id):
        raise HTTPException(status_code=404, detail="No such job in flight")
    logger.warning(f"🛑 Abort requested for {file_id}")
    return {"status": "aborting", "file": file_id}

def _log_job_result(task: asyncio.Task):
    if task.cancelled():
        logger.warning("Batch task was cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"Batch task crashed: {exc!r}", exc_info=exc)


@app.get("/health")
async def health():
    return {"status": "online" if orchestrator else "initializing"}