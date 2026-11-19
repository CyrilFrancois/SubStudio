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
from core.translators import make_translator
from core.subtitles import TranslationContext, detect_language, load_file, parse, shift, to_srt, wrap_lines
from core.cells import Cell, CellState, Source, VideoRun, produce_cell
from core.acquire.types import Rung
from core.muxer import VideoMuxer
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
    transcriptionEngine: str = "turbo"
    generateSRT: bool = True
    muxIntoMkv: bool = True
    cleanUp: bool = False

class ProcessRequest(BaseModel):
    videos: List[VideoJob]
    globalOptions: GlobalOptions

# --- ORCHESTRATOR ---

class PipelineOrchestrator:
    def __init__(self):
        self.scanner = VideoScanner(base_path=MEDIA_ROOT)
        self.transcriber = VideoTranscriber()
        self.translator = make_translator()
        # A batch and a retry can run at once; they share one model server. Without this lock one video's
        # unload() kills the server under the other's request, and its retry reloads it (D-0001).
        self._translate_lock = asyncio.Lock()
        self.muxer = VideoMuxer()
        
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
            # We no longer re-init self.transcriber here.
            # The lazy-loader inside transcriber.py handles the swap.
            for idx, video in enumerate(queued):
                await self.execute_pipeline(video, opts, idx, len(queued))
        finally:
            logger.info("🏁 BATCH PROCESSING FINISHED")
            event_manager.emit("system_events", "batch_done", 100, "All tasks completed")

    @asynccontextmanager
    async def _translation(self):
        """One video's translations at a time; the model is freed afterwards, success or not, so it never shares
        RAM with the next transcription (D-0001)."""
        async with self._translate_lock:
            try:
                yield
            finally:
                await asyncio.to_thread(self.translator.unload)

    async def _run_cell(self, run: VideoRun, lang_code: str, job, p: str):
        job.raise_if_aborted()
        cell = run.cells[lang_code]
        event_manager.emit(run.path, "translating", 50, f"{p} Step 4/5: {lang_code.upper()}...")

        def on_batch(done: int, total_cues: int):
            event_manager.emit(run.path, "translating", 40 + int(done / total_cues * 40),
                               f"{p} Step 4/5: Translating {lang_code.upper()} ({done}/{total_cues} lines)")

        cell.state = CellState.RUNNING
        event_manager.emit_cells(run.path, run.to_json())
        try:
            await asyncio.to_thread(produce_cell, cell, run.source, self.translator,
                                    on_batch=on_batch, check_abort=job.raise_if_aborted)
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

    async def _write_outputs(self, run: VideoRun, video: "VideoJob", opts: "GlobalOptions", job, p: str,
                             index: int, total: int):
        passed = run.passed()
        if not passed:
            return
        if opts.generateSRT:
            for lang_code, cues in passed.items():
                with open(Path(run.path).with_suffix(f".{lang_code}.srt"), "w", encoding="utf-8") as f:
                    f.write(to_srt(cues))
        if opts.muxIntoMkv:
            job.raise_if_aborted()
            event_manager.emit(run.path, "processing", 90, f"{p} Muxing into MKV...")
            await asyncio.to_thread(
                self.muxer.mux,
                video_path=run.path,
                srts=passed,
                current_file=index + 1,
                total_files=total,
                strip_existing=video.stripExistingSubs,
                # Never delete the source while a language failed: it is what a retry starts from.
                cleanup_original=opts.cleanUp and run.all_passed(),
                job=job
            )

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
            async with self._translation():
                await self._run_cell(run, lang_code, job, p)
            await self._write_outputs(run, video, opts, job, p, 0, 1)
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
            # A language the muxer cannot title must fail now, not after a 40-minute transcription.
            if opts.muxIntoMkv:
                for lang_code in video.out:
                    substudio_track_title(lang_code.lower())

            # STEP 1: CONTEXT
            job.raise_if_aborted()
            event_manager.emit(fid, "processing", 5, f"{p} Step 1/5: Analyzing context...")
            context = await asyncio.to_thread(self.translator.get_context_profile, video.name)

            # STEP 2: SOURCE (Transcription or SRT Import) — from here on, a list of cues, never SRT text
            cues = None
            is_whisper = True
            spoken_lang = None

            if video.workflowMode in ["srt", "hybrid"]:
                potential_paths = []
                if video.selectedSrtPath:
                    potential_paths.append(Path(video.selectedSrtPath))
                potential_paths.append(Path(video.path).with_suffix(".srt"))

                found_path = next((p for p in potential_paths if p.exists() and p.is_file()), None)
                if found_path:
                    logger.info(f"🔍 Found SRT at: {found_path}")
                    event_manager.emit(fid, "processing", 10, f"{p} Found SRT {found_path.name}")
                    # An unreadable or empty sidecar raises: it is never silently replaced by a transcription.
                    cues = await asyncio.to_thread(load_file, found_path)
                    is_whisper = False

            if cues is None:
                event_manager.emit(fid, "processing", 15, f"{p} Transcribing with Whisper...")
                asr = await asyncio.to_thread(
                    self.transcriber.transcribe,
                    video_path=video.path,
                    file_id=fid,
                    on_progress=event_manager.emit,
                    current_file=index + 1,
                    total_files=total,
                    model_size=opts.transcriptionEngine,
                    job=job
                )
                # The transcriber raises a named error on no speech, so an empty list cannot reach here.
                cues = asr.to_cues()
                # Whisper hears the language; text detection only guesses it. Trusted when Whisper is sure.
                if asr.language_probability >= 0.9:
                    spoken_lang = asr.language

            # STEP 3: SYNC
            if video.syncOffset != 0:
                logger.info(f"⏱️ Adjusting timing by {video.syncOffset}s")
                cues = shift(cues, video.syncOffset)

            # STEP 4: ONE CELL PER LANGUAGE (D-0002) — each passes or fails alone, with its origin or its reason.
            source_lang = spoken_lang or await asyncio.to_thread(detect_language, cues)  # CPU work: off the loop
            source = Source(
                cues=cues, rung=Rung.ASR if is_whisper else Rung.TRANSLATE, language=source_lang,
                context=TranslationContext(title=video.name, notes=context, source_is_asr=is_whisper,
                                           source_lang=source_lang),
            )
            run = VideoRun(path=fid, source=source, cells={lang: Cell(lang) for lang in video.out})
            self.runs[fid] = run
            event_manager.emit_cells(fid, run.to_json())
            async with self._translation():
                for lang_code in video.out:
                    await self._run_cell(run, lang_code, job, p)

            # STEP 5: OUTPUT — only the cells that passed
            await self._write_outputs(run, video, opts, job, p, index, total)
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
    if not Path(fid).is_file():
        raise HTTPException(status_code=409, detail="The source video is no longer there (removed by cleanUp?)")
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