# main.py — FastAPI app + PipelineOrchestrator: scan, process, SSE events. Exports: app.

import logging
import time
import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Dict, List, Optional, Any, Set

# Core Imports
from core.scanner import VideoScanner
from core.subtitle_processor import SubtitleProcessor
from core.transcriber import VideoTranscriber
from core.translator import SubtitleTranslator
from core.muxer import VideoMuxer
from core.events import event_manager, setup_logging_bridge
from core.jobs import job_registry, JobAborted

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
    transcriptionEngine: str = "medium"
    generateSRT: bool = True
    muxIntoMkv: bool = True
    cleanUp: bool = False

class ProcessRequest(BaseModel):
    videos: List[VideoJob]
    globalOptions: GlobalOptions

# --- ORCHESTRATOR ---

class PipelineOrchestrator:
    def __init__(self):
        self.scanner = VideoScanner(base_path="/data")
        self.processor = SubtitleProcessor()
        self.transcriber = VideoTranscriber(model_size="medium")
        self.translator = SubtitleTranslator()
        self.muxer = VideoMuxer()
        
        self.active_jobs: Set[str] = set()
        # Cache keyed BY PATH: a single timestamp meant two scans of different folders
        # within the debounce window both returned the first folder's contents.
        self._scan_cache: Dict[str, tuple] = {}

    def split_long_lines(self, srt_text: str, max_chars: int = 50) -> str:
        """Adds \n to subtitle lines that are too long."""
        def process_block(match):
            index, times, text = match.groups()
            lines = text.strip().split('\n')
            new_lines = []
            
            for line in lines:
                if len(line) > max_chars:
                    mid = len(line) // 2
                    space_indices = [i for i, char in enumerate(line) if char == ' ']
                    if space_indices:
                        best_space = min(space_indices, key=lambda x: abs(x - mid))
                        line = line[:best_space] + '\n' + line[best_space+1:]
                new_lines.append(line)
            
            joined_content = '\n'.join(new_lines)
            return f"{index}\n{times}\n{joined_content}\n\n"

        pattern = re.compile(r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3})\n([\s\S]*?)(?:\n\n|\Z)")
        return pattern.sub(process_block, srt_text)

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

    async def execute_pipeline(self, video: VideoJob, opts: GlobalOptions, index: int, total: int):
        fid = video.path
        self.active_jobs.add(fid)
        job = job_registry.get(fid) or job_registry.register(fid)
        
        log_handler = setup_logging_bridge(fid)
        temp_audio = Path(video.path).with_suffix(".tmp.wav")
        p = f"[{index + 1}/{total} Files]"
        
        logger.info(f"{p} STARTING: {video.name}")

        try:
            # STEP 1: CONTEXT
            job.raise_if_aborted()
            event_manager.emit(fid, "processing", 5, f"{p} Step 1/5: Analyzing context...")
            context = await asyncio.to_thread(self.translator.get_context_profile, video.name)

            # STEP 2: SOURCE (Transcription or SRT Import)
            srt_content = ""
            is_whisper = True
            
            if video.workflowMode in ["srt", "hybrid"]:
                potential_paths = []
                if video.selectedSrtPath:
                    potential_paths.append(Path(video.selectedSrtPath))
                
                video_path = Path(video.path)
                potential_paths.append(video_path.with_suffix(".srt"))
                
                found_path = next((p for p in potential_paths if p.exists() and p.is_file()), None)

                if found_path:
                    logger.info(f"🔍 Found SRT at: {found_path}")
                    event_manager.emit(fid, "processing", 10, f"{p} Found SRT {found_path.name}")
                    with open(found_path, "r", encoding="utf-8", errors="ignore") as f:
                        srt_content = f.read()
                    is_whisper = False

            # If no SRT found or mode is 'pure', run transcription
            if not srt_content:
                event_manager.emit(fid, "processing", 15, f"{p} Transcribing with Whisper...")
                
                # UPDATED: We pass both the model_size and the context.
                # The transcriber will check if 'opts.transcriptionEngine' is already loaded.
                srt_content = await asyncio.to_thread(
                    self.transcriber.transcribe,
                    video_path=video.path,
                    file_id=fid,
                    on_progress=event_manager.emit,
                    current_file=index + 1,
                    total_files=total,
                    model_size=opts.transcriptionEngine,
                    context_prompt=context,
                    job=job
                )

            # STEP 3: SYNC
            if video.syncOffset != 0:
                srt_content = self.processor.apply_offset(srt_content, video.syncOffset)

            # STEP 4: TRANSLATION & REFINING
            translated_map = {}
            for lang_code in video.out:
                job.raise_if_aborted()
                event_manager.emit(fid, "processing", 50, f"{p} Translating to {lang_code}...")
                translation = await asyncio.to_thread(
                    self.translator.refine_and_translate,
                    srt_content=srt_content,
                    target_lang=lang_code,
                    file_id=fid,
                    on_progress=event_manager.emit,
                    job=job,
                    context_profile=context,
                    current_file=index + 1,
                    total_files=total,
                    is_whisper_source=is_whisper
                )
                
                translation = self.split_long_lines(translation)
                translated_map[lang_code] = translation
                
                if opts.generateSRT:
                    out_srt = Path(video.path).with_suffix(f".{lang_code}.srt")
                    with open(out_srt, "w", encoding="utf-8") as f:
                        f.write(translation)

            # STEP 5: MUXING
            if opts.muxIntoMkv:
                job.raise_if_aborted()
                event_manager.emit(fid, "processing", 90, f"{p} Muxing into MKV...")
                await asyncio.to_thread(
                    self.muxer.mux,
                    video_path=video.path,
                    srts=translated_map,
                    current_file=index + 1,
                    total_files=total,
                    strip_existing=video.stripExistingSubs,
                    cleanup_original=opts.cleanUp,
                    job=job
                )

            event_manager.emit(fid, "done", 100, "Processing Complete")
            logger.info(f"✅ {p} COMPLETED: {video.name}")

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/api/scan")
async def scan(target_path: str = Query("/data")):
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