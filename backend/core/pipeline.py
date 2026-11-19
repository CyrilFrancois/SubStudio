# pipeline.py — PipelineOrchestrator: one video through Cyril's ladder (plan → existing/download → translate/ASR → gate → in-place update), plus the job models. Exports: PipelineOrchestrator, VideoJob, GlobalOptions, MEDIA_ROOT.

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

from .acquire import plan
from .acquire.existing import audio_language, find_existing, label_by_text, picked_sidecar
from .acquire.plan import OV_TAGS
from .artifacts import artifacts, audio_fingerprint
from .cells import Cell, CellState, VideoRun
from .context import get_context
from .events import event_manager, setup_logging_bridge
from .gate import detect_speech, evaluate, read_audio
from .gate.speech import SAMPLE_RATE
from .jobs import JobAborted, job_registry
from .ladder import Ladder, LadderDeps
from .output import current_path, subtitle_streams, verdict_record, write_run
from .provenance import substudio_track_title
from .scanner import VideoScanner
from .subtitle_processor import SubtitleProcessor
from .subtitles import TranslationContext
from .transcriber import VideoTranscriber
from .translators import make_translator

# /data inside Docker; set SUBSTUDIO_MEDIA_ROOT (e.g. C:/Videos) when the backend runs natively.
MEDIA_ROOT = os.getenv("SUBSTUDIO_MEDIA_ROOT", "/data")
# How sure the spoken-language detection must be before SubStudio trusts it: for a transcript's language, and for
# the tag it writes on an untagged audio stream (a wrong tag would make « OV » name the wrong language for good).
OV_TAG_MIN_PROBABILITY = 0.9

logger = logging.getLogger("SubStudio.Pipeline")


def _speech(video_path: str):
    """The gate's speech spans (ms) for a video and its audio length (ms): core/gate's own detector, never Whisper's."""
    audio = read_audio(video_path)
    return detect_speech(audio), int(len(audio) * 1000 / SAMPLE_RATE)


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

class _OfflineLookup:
    """Stands in for the TVmaze client when online lookups are off: get_context records why nothing was looked up."""
    def series(self, title, year):
        return None, "online lookups are off (SUBSTUDIO_DOWNLOADS=0)"

# --- ORCHESTRATOR ---

class PipelineOrchestrator:
    def __init__(self):
        self.scanner = VideoScanner(base_path=MEDIA_ROOT)
        self.transcriber = VideoTranscriber()
        self.translator = make_translator()
        # A batch and a retry can run at once; they share one model server. Without this lock one video's
        # unload() kills the server under the other's request, and its retry reloads it (D-0001).
        self._translate_lock = asyncio.Lock()
        self.processor = SubtitleProcessor()
        self.gate = evaluate  # the quality gate (core/gate, doc/user-process.md §3)
        # Rung 2 asks subtitle sites over the network; SUBSTUDIO_DOWNLOADS=0 keeps a run (or the tests) offline.
        self.downloads_enabled = os.getenv("SUBSTUDIO_DOWNLOADS", "1") != "0"

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

    @asynccontextmanager
    async def _translation(self):
        """One video's rungs 3-4 at a time; the model is freed afterwards, success or not (D-0001)."""
        async with self._translate_lock:
            try:
                yield
            finally:
                await asyncio.to_thread(self.translator.unload)

    @property
    def count_existing(self) -> bool:
        """« Count subtitles not made by this SubStudio » (Settings.count_existing, read at each run so the UI's toggle
        applies). On by default (Cyril, 2026-11-19, D-0002): such a subtitle still counts only when the gate passes it."""
        from api.settings_store import load_settings
        return load_settings().count_existing

    def _context(self, video_path: str, cues=None) -> TranslationContext:
        """The translation context of a video, with the names found in `cues` (the source being translated).
        Offline (SUBSTUDIO_DOWNLOADS=0) the lookup is skipped and the context says so."""
        return get_context(video_path, cues, client=None if self.downloads_enabled else _OfflineLookup())

    def _ladder(self, run: VideoRun, video: "VideoJob", opts: "GlobalOptions", job, p: str, index: int, total: int,
                preloaded: Optional[Dict[str, list]] = None, unlabelled: Optional[list] = None) -> Ladder:
        # A retry reads the video where it is NOW: after a swap it carries its (Sub-…) name, not video.path.
        def transcribe():
            # A transcription of this exact audio with this model is never redone (core/artifacts): a retry or a
            # restart reuses it, the swap and its rename included.
            key = None
            try:
                key = artifacts().asr_key(audio_fingerprint(str(current_path(run))), opts.transcriptionEngine)
                kept = artifacts().load_asr(key)
            except Exception as e:   # no fingerprint: transcribe, as without a cache
                logger.error(f"{p} No audio fingerprint, the transcription will not be kept: {e}")
                kept = None
            if kept:
                logger.info(f"{p} Step 2/5: transcription reused ({len(kept[0])} lines, same audio and model)")
                event_manager.emit(run.path, "transcribing", 100, f"{p} Step 2/5: transcription reused")
                if kept[1] and run.audio_tag is None:   # the cached language was stored only when Whisper was sure
                    run.audio_tag = kept[1]
                return kept
            # Rung 4 can transcribe between two languages' translations: the translation model is freed first,
            # so Whisper and the LLM are never in RAM together (D-0001), whatever order the ladder takes.
            self.translator.unload()
            asr = self.transcriber.transcribe(
                video_path=str(current_path(run)), file_id=run.path, on_progress=event_manager.emit,
                current_file=index + 1, total_files=total, model_size=opts.transcriptionEngine, job=job)
            # Whisper hears the language; text detection only guesses it. Trusted when Whisper is sure.
            sure = asr.language_probability >= OV_TAG_MIN_PROBABILITY
            if sure and run.audio_tag is None:
                run.audio_tag = asr.language
            result = asr.to_cues(), (asr.language if sure else None)
            if key:
                artifacts().save_asr(key, *result)
            return result

        return Ladder(run, LadderDeps(
            translator=self.translator, transcribe=transcribe,
            load_embedded=lambda i: self.processor.extract_embedded_subs(str(current_path(run)), i, job=job),
            downloads_enabled=self.downloads_enabled, count_existing=self.count_existing,
            gate=self.gate, speech=lambda: _speech(str(current_path(run))),
            preloaded=preloaded or {}, unlabelled=unlabelled, check_abort=job.raise_if_aborted,
            context=lambda cues: self._context(run.path, cues), artifacts=artifacts(),
            progress=lambda status, pct, msg: event_manager.emit(run.path, status, pct, f"{p} {msg}")))

    async def _run_cell(self, run: VideoRun, ladder: Ladder, lang_code: str, job, p: str):
        job.raise_if_aborted()
        cell = run.cells[lang_code]
        if cell.state == CellState.PASSED:  # kept or downloaded at rungs 1–2: a human text, left as written
            logger.info(f"✅ {p} {cell.summary()}")
            return
        # No « translating » frame yet: the ladder may still transcribe for this cell, and the stepper shows the stage
        # that really runs (the first batch, or the transcriber's own frames).
        logger.info(f"{p} Step 4/5: {lang_code.upper()}...")

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
        if cell.state == CellState.PASSED:  # wrapped and gated by the ladder
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
        # The write is finished: the stepper shows it done even if a language failed earlier (the job then fails).
        event_manager.emit(run.path, "processing", 100, f"{p} Step 5/5: written")
        await asyncio.to_thread(self._record_verdicts, run, result)

    @staticmethod
    def _record_verdicts(run: VideoRun, result) -> None:
        """The gate's verdicts on the pre-existing subtitles, recorded for the file as it is now (after the swap:
        new name, mtime and size), so the Library shows them kept or rejected instead of « not checked yet »."""
        if not run.judged:
            return
        from api.job_store import job_store
        path = current_path(run)
        try:
            subs = result.subs if result is not None and result.status == "updated" else subtitle_streams(path)
            job_store().record_verdicts(str(path), os.stat(path), verdict_record(run, subs))
        except Exception as e:
            # The video is written; only the Library's « kept / rejected » label is lost, and a rerun re-checks.
            logger.error(f"Could not record the quality verdicts of {path.name}: {e}")

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
            cell.state, cell.reason, cell.cues, cell.reviewed = CellState.PENDING, "", None, False
            cell.left_in_source = []
            cell.attempts = [a for a in cell.attempts if a.startswith(("existing", "download"))]
            async with self._translation():
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
                if prob >= OV_TAG_MIN_PROBABILITY:
                    run.audio_tag = original    # written on the untagged audio by the swap (core/output)
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
            async with self._translation():
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
