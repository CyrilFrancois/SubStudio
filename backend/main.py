# main.py — FastAPI app + PipelineOrchestrator: scan, process, SSE events. Exports: app.

import logging
import os
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Dict, List, Optional, Any, Set

# Core Imports
from core.scanner import VideoScanner
from core.transcriber import VideoTranscriber
from core.translator import GeminiTranslator
from core.subtitles import TranslationContext, wrap_lines
from core.cells import Cell, CellState, VideoRun
from core.acquire import plan
from core.acquire.existing import audio_language, find_existing, label_by_text, picked_sidecar
from core.acquire.plan import OV_TAGS
from core.ladder import Ladder, LadderDeps
from core.context import get_context
from core.subtitle_processor import SubtitleProcessor
from core.output import current_path, write_run
from core.provenance import substudio_track_title
from core.events import event_manager, setup_logging_bridge
from core.jobs import job_registry, JobAborted
from api import router as api_router

# /data inside Docker; set SUBSTUDIO_MEDIA_ROOT (e.g. C:/Videos) when the backend runs natively.
MEDIA_ROOT = os.getenv("SUBSTUDIO_MEDIA_ROOT", "/data")

# --- LOGGING CONFIGURATION ---
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("SubStudio.Main")

# --- DATA MODELS ---

class VideoJob(BaseModel):
    name: str 
    path: str 
    selectedSrtPath: Optional[str] = None 
    src: Optional[Any] = "auto"
    out: List[str] = ["fr"]
    workflowMode: str = "pure"
    syncOffset: float = 0.0
    stripExistingSubs: bool = False

class GlobalOptions(BaseModel):
    transcriptionEngine: Optional[str] = None  # None: the transcriber's own default model (turbo)
    # generateSRT / muxIntoMkv / cleanUp are gone (D-0002 §4): the video itself is updated by the verified swap.

class ProcessRequest(BaseModel):
    videos: List[VideoJob]
    globalOptions: GlobalOptions

class _OfflineLookup:
    """Stands in for the TVmaze client when online lookups are off: get_context records why nothing was looked up."""
    def series(self, title, year):
        return None, "online lookups are off (SUBSTUDIO_DOWNLOADS=0)"

# --- ORCHESTRATOR ---

class PipelineOrchestrator:
    def __init__(self):
        self.scanner = VideoScanner(base_path=MEDIA_ROOT)
        self.transcriber = VideoTranscriber()
        self.translator = GeminiTranslator()
        self.processor = SubtitleProcessor()
        # Rung 2 asks subtitle sites over the network; SUBSTUDIO_DOWNLOADS=0 keeps a run (or the tests) offline.
        self.downloads_enabled = os.getenv("SUBSTUDIO_DOWNLOADS", "1") != "0"
        # « Count subtitles not made by this SubStudio » (CLAUDE.md « Phase »): OFF until the quality gate exists.
        self.count_existing = False

        self.active_jobs: Set[str] = set()
        # The last run of each video, so one failed language can be retried without redoing its source.
        self.runs: Dict[str, VideoRun] = {}
        # Cache keyed BY PATH: a single timestamp meant two scans of different folders
        # within the debounce window both returned the first folder's contents.
        self._scan_cache: Dict[str, tuple] = {}

    def get_files(self, target_path: str):
        now = time.time()
        cached = self._scan_cache.get(target_path)
        if cached and now - cached[0] < 2.0:
            return cached[1]

        files = self.scanner.scan(target_path)
        self._scan_cache[target_path] = (now, files)
        return files

    async def run_batch(self, videos: List[VideoJob], opts: GlobalOptions):
        total = len(videos)
        queued = []
        for video in videos:
            if video.path in self.active_jobs:
                logger.warning(f"⚠️ [Skip] {video.name} is already in the pipeline.")
                continue
            # Registered before the loop starts so an abort_all also cancels files that have
            # not begun yet, instead of only whichever one happens to be running.
            job_registry.register(video.path)
            queued.append(video)
            event_manager.emit(video.path, "queued", 0, "Queued")

        try:
            for idx, video in enumerate(queued):
                await self.execute_pipeline(video, opts, idx, len(queued))
        finally:
            logger.info("🏁 BATCH PROCESSING FINISHED")
            event_manager.emit("system_events", "batch_done", 100, "All tasks completed")

    def _context(self, video_path: str, cues=None) -> TranslationContext:
        """The translation context of a video, with the names found in `cues` (the source being translated).
        Offline (SUBSTUDIO_DOWNLOADS=0) the lookup is skipped and the context says so."""
        return get_context(video_path, cues, client=None if self.downloads_enabled else _OfflineLookup())

    def _ladder(self, run: VideoRun, video: "VideoJob", opts: "GlobalOptions", job, p: str, index: int, total: int,
                preloaded: Optional[Dict[str, list]] = None, unlabelled: Optional[list] = None) -> Ladder:
        # A retry reads the video where it is NOW: after a swap it carries its (Sub-…) name, not video.path.
        def transcribe():
            asr = self.transcriber.transcribe(
                video_path=str(current_path(run)), file_id=run.path, on_progress=event_manager.emit,
                current_file=index + 1, total_files=total, model_size=opts.transcriptionEngine, job=job)
            # Whisper hears the language; text detection only guesses it. Trusted when Whisper is sure.
            return asr.to_cues(), (asr.language if asr.language_probability >= 0.9 else None)

        return Ladder(run, LadderDeps(
            translator=self.translator, transcribe=transcribe,
            load_embedded=lambda i: self.processor.extract_embedded_subs(str(current_path(run)), i, job=job),
            downloads_enabled=self.downloads_enabled, count_existing=self.count_existing,
            preloaded=preloaded or {}, unlabelled=unlabelled, check_abort=job.raise_if_aborted,
            context=lambda cues: self._context(run.path, cues),
            progress=lambda status, pct, msg: event_manager.emit(run.path, status, pct, f"{p} {msg}")))

    async def _run_cell(self, run: VideoRun, ladder: Ladder, lang_code: str, job, p: str):
        job.raise_if_aborted()
        cell = run.cells[lang_code]
        if cell.state == CellState.PASSED:  # kept or downloaded at rungs 1–2: a human text, left as written
            logger.info(f"✅ {p} {cell.summary()}")
            return
        event_manager.emit(run.path, "translating", 50, f"{p} Step 4/5: {lang_code.upper()}...")

        def on_batch(done: int, total_cues: int):
            event_manager.emit(run.path, "translating", 40 + int(done / total_cues * 40),
                               f"{p} Step 4/5: Translating {lang_code.upper()} ({done}/{total_cues} lines)")

        cell.state = CellState.RUNNING
        event_manager.emit_cells(run.path, run.to_json())
        try:
            await ladder.fill(lang_code, on_batch=on_batch)
        except BaseException as e:
            # A cell never stays « running »: an abort or a crash ends it failed, with its reason, then propagates.
            cell.state = CellState.FAILED
            cell.reason = "cancelled by the user" if isinstance(e, JobAborted) else f"unexpected error: {e}"
            cell.cues = None
            event_manager.emit_cells(run.path, run.to_json())
            raise
        if cell.state == CellState.PASSED:
            cell.cues = wrap_lines(cell.cues, 50)
            logger.info(f"✅ {p} {cell.summary()}")
        else:
            logger.error(f"❌ {p} {cell.summary()}")
        event_manager.emit_cells(run.path, run.to_json())

    async def _write_outputs(self, run: VideoRun, job, p: str):
        """STEP 5: the passed cells go INTO the video by the verified swap (D-0002 §4); kept-only → untouched."""
        job.raise_if_aborted()
        event_manager.emit(run.path, "processing", 90, f"{p} Step 5/5: Writing subtitles into the video...")
        result = await asyncio.to_thread(write_run, run, job)
        if result is not None:
            logger.info(f"{p} {result.status}: {result.path.name}")

    def _emit_outcome(self, run: VideoRun, p: str, name: str):
        summary = run.summary()
        if run.all_passed():
            event_manager.emit(run.path, "done", 100, f"Processing Complete: {summary}")
            logger.info(f"✅ {p} COMPLETED: {name} ({summary})")
        else:
            # Partly done is not done: the passed languages are in the output, the video still reads as failed.
            event_manager.emit(run.path, "error", 0, summary)
            logger.error(f"❌ {p} NOT COMPLETE: {name} ({summary})")

    async def retry_cell(self, video: "VideoJob", lang_code: str, opts: "GlobalOptions"):
        """Re-runs one language of a video from its kept source, then rebuilds the output from every passed cell."""
        fid = video.path
        run = self.runs[fid]
        self.active_jobs.add(fid)
        job = job_registry.register(fid)
        log_handler = setup_logging_bridge(fid)
        p = "[retry]"
        try:
            cell = run.cells[lang_code]
            # Rungs 1–2 ran for every language and their sources are kept: a retry redoes rungs 3–4 only.
            cell.state, cell.reason, cell.cues = CellState.PENDING, "", None
            cell.attempts = [a for a in cell.attempts if a.startswith(("existing", "download"))]
            await self._run_cell(run, self._ladder(run, video, opts, job, p, 0, 1), lang_code, job, p)
            await self._write_outputs(run, job, p)
            self._emit_outcome(run, p, video.name)
        except JobAborted:
            event_manager.emit(fid, "cancelled", 0, "Cancelled")
        except Exception as e:
            logger.error(f"❌ {p} ERROR: {e}")
            event_manager.emit(fid, "error", 0, str(e))
        finally:
            self.active_jobs.discard(fid)
            job_registry.release(fid)
            logging.getLogger().removeHandler(log_handler)

    async def execute_pipeline(self, video: VideoJob, opts: GlobalOptions, index: int, total: int):
        fid = video.path
        self.active_jobs.add(fid)
        job = job_registry.get(fid) or job_registry.register(fid)
        
        log_handler = setup_logging_bridge(fid)
        temp_audio = Path(video.path).with_suffix(".tmp.wav")
        p = f"[{index + 1}/{total} Files]"
        
        logger.info(f"{p} STARTING: {video.name}")

        try:
            # STEP 1: CONTEXT — looked up (filename, TVmaze), never invented; names from the source join it later.
            job.raise_if_aborted()
            event_manager.emit(fid, "processing", 5, f"{p} Step 1/5: Looking up the episode...")
            context = await asyncio.to_thread(self._context, video.path)

            # STEP 2: WHAT IS ALREADY THERE — tracks and sidecars, plus the sidecar the user picked (trusted: his
            # choice), and the audio language that « OV » stands for.
            existing, preloaded = await asyncio.to_thread(
                label_by_text, await asyncio.to_thread(find_existing, video.path),
                lambda i: self.processor.extract_embedded_subs(video.path, i, job=job))
            picked = await asyncio.to_thread(picked_sidecar, video.path, video.workflowMode, video.selectedSrtPath,
                                             video.syncOffset)
            unlabelled = None
            if picked and picked[0].language:
                existing = [e for e in existing if e.ref != picked[0].ref] + [picked[0]]
                preloaded[picked[0].ref] = picked[1]
            elif picked:
                unlabelled = picked[1]
            original = await asyncio.to_thread(audio_language, video.path)

            # STEP 3: ONE PLAN AND ONE CELL PER LANGUAGE (D-0002) — each passes or fails alone, with its origin.
            run = VideoRun(path=fid, cells={}, context=context)
            ladder = self._ladder(run, video, opts, job, p, index, total, preloaded, unlabelled)
            if original is None and any(o.strip().lower() in OV_TAGS for o in video.out):
                # Untagged audio: Whisper identifies the language on a sample, without a full transcription.
                original, prob = await asyncio.to_thread(self.transcriber.detect_video_language, video.path, job)
                logger.info(f"{p} OV detected as {original} (probability {prob:.2f})")
            plans = plan(video.out, existing, original, downloads_enabled=self.downloads_enabled)
            run.plans = {lp.language: lp for lp in plans}
            run.cells = {lp.language: Cell(lp.language) for lp in plans if not lp.source_only}
            # A language the output cannot title must fail now, not after a 40-minute transcription.
            for lang_code in run.cells:
                substudio_track_title(lang_code)
            self.runs[fid] = run
            event_manager.emit_cells(fid, run.to_json())

            # STEP 4: RUNGS 1–2 FOR EVERY LANGUAGE, THEN 3–4 PER CELL
            await ladder.human()
            event_manager.emit_cells(fid, run.to_json())
            for lang_code in run.cells:
                await self._run_cell(run, ladder, lang_code, job, p)

            # STEP 5: OUTPUT — only the cells that passed
            await self._write_outputs(run, job, p)
            self._emit_outcome(run, p, video.name)

        except JobAborted:
            logger.warning(f"🛑 {p} CANCELLED: {video.name}")
            event_manager.emit(fid, "cancelled", 0, "Cancelled")
        except Exception as e:
            logger.error(f"❌ {p} ERROR: {str(e)}")
            event_manager.emit(fid, "error", 0, str(e))
        finally:
            self.active_jobs.discard(fid)
            job_registry.release(fid)
            # Audio cleanup is handled inside the transcriber now, but safe to keep here
            if temp_audio.exists():
                try: temp_audio.unlink()
                except: pass
            logging.getLogger().removeHandler(log_handler)

# --- LIFESPAN ---
orchestrator = None
_jobs: Set[asyncio.Task] = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    orchestrator = PipelineOrchestrator()
    # The pipeline runs off-loop; EventManager needs this loop to hand SSE data back safely.
    event_manager.bind_loop(asyncio.get_running_loop())
    yield

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