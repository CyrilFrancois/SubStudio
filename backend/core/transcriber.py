# transcriber.py — VideoTranscriber: Faster-Whisper (turbo, int8, VAD, 4 threads) → AsrResult, with killable audio extraction, language detection and span re-runs. Exports: VideoTranscriber, names_prompt, speech_timeline.

import os
import logging
import ffmpeg
import gc
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps
from typing import Callable, List, Optional, Sequence, Tuple
from pathlib import Path

import numpy as np

from .asr_result import AsrResult, AsrSegment, AsrWord
from .jobs import JobHandle, JobAborted

logger = logging.getLogger("SubStudio.Transcriber")

SAMPLE_RATE = 16000
# Half of this 8-thread laptop (D-0001). CTranslate2 takes its thread count from cpu_threads, not from
# OMP_NUM_THREADS alone, so the cap is passed explicitly.
ASR_THREADS = int(os.getenv("ASR_THREADS", "4"))
DEFAULT_MODEL = os.getenv("WHISPER_MODEL", "turbo")
# Whisper keeps only the last ~224 tokens of a prompt, and long prose prompts make it hallucinate: names only.
MAX_PROMPT_CHARS = 300
VAD = VadOptions(min_silence_duration_ms=500)


def names_prompt(names: Sequence[str]) -> Optional[str]:
    """A short 'Names: A, B.' prompt that biases spelling of proper nouns, or None when there are none."""
    seen, kept, length = set(), [], 0
    for n in (s.strip() for s in names):
        if not n or n.lower() in seen or length + len(n) + 2 > MAX_PROMPT_CHARS:
            continue
        seen.add(n.lower())
        kept.append(n)
        length += len(n) + 2
    return f"Names: {', '.join(kept)}." if kept else None


def speech_timeline(audio: np.ndarray) -> List[Tuple[float, float]]:
    """Silero VAD speech spans in seconds: what the quality gate measures sync and coverage against."""
    return [(c["start"] / SAMPLE_RATE, c["end"] / SAMPLE_RATE) for c in get_speech_timestamps(audio, VAD)]


class VideoTranscriber:
    def __init__(self, model_size: str = DEFAULT_MODEL):
        self.model = None
        self.current_model_size = None
        self.default_model_size = model_size

    def _get_model(self, model_size: str):
        if self.model is None or self.current_model_size != model_size:
            if self.model is not None:
                logger.warning(f"Model mismatch. Clearing [{self.current_model_size}] to load [{model_size}]...")
                self.model = None
                gc.collect()

            logger.info(f"LOADING WHISPER MODEL: [{model_size}] (CPU, int8, {ASR_THREADS} threads)")
            self.model = WhisperModel(model_size, device="cpu", compute_type="int8",
                                      cpu_threads=ASR_THREADS, num_workers=1)
            self.current_model_size = model_size
        return self.model

    def extract_audio(self, video_path: str, job: Optional[JobHandle] = None) -> str:
        audio_path = str(Path(video_path).with_suffix(".tmp.wav"))
        process = None
        try:
            # run_async (not run) so the child PID is reachable and abort() can kill it.
            process = (
                ffmpeg
                .input(video_path)
                .output(audio_path, acodec='pcm_s16le', ac=1, ar='16k', vn=None, sn=None)
                .overwrite_output()
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
            if job:
                job.track(process)
            _, stderr = process.communicate()

            if job and job.is_aborted:
                raise JobAborted(job.file_id)

            if process.returncode != 0:
                logger.error(f"❌ Audio extraction failed: {stderr.decode(errors='ignore')[:400]}")
                return ""
            return audio_path
        except JobAborted:
            raise
        except Exception as e:
            logger.error(f"❌ Audio extraction failed: {e}")
            return ""
        finally:
            if job:
                job.untrack()

    def load_audio(self, video_path: str, job: Optional[JobHandle] = None) -> np.ndarray:
        """The video's first audio track as 16 kHz mono float32. Raises on no audio track or empty audio."""
        audio_file = self.extract_audio(video_path, job=job)
        try:
            if not audio_file or not os.path.exists(audio_file):
                raise Exception("Could not extract an audio track (none in the file, or ffmpeg failed).")
            audio = decode_audio(audio_file, sampling_rate=SAMPLE_RATE)
        finally:
            if audio_file and os.path.exists(audio_file):
                try:
                    os.remove(audio_file)
                except OSError as e:
                    logger.warning(f"Could not delete temp audio {audio_file}: {e}")
        if len(audio) == 0:
            raise Exception("The audio track is empty (0 s): nothing to transcribe.")
        return audio

    def detect_language(self, audio: np.ndarray, model_size: Optional[str] = None) -> Tuple[str, float]:
        """OV: the spoken language and its probability, voted over up to 3 speech windows (not only the first 30 s,
        which is often music or a cold open)."""
        lang, prob, _ = self._get_model(model_size or self.default_model_size).detect_language(
            audio, vad_filter=True, vad_parameters=VAD, language_detection_segments=3)
        return lang, prob

    def transcribe(
        self,
        video_path: str,
        file_id: str,
        on_progress: Callable,
        current_file: int,
        total_files: int,
        model_size: Optional[str] = None,
        names: Sequence[str] = (),
        language: Optional[str] = None,
        spans: Optional[Sequence[Tuple[float, float]]] = None,
        job: Optional[JobHandle] = None
    ) -> AsrResult:
        """Transcribe the whole track, or only `spans` (seconds) for a correction re-run. `language` given (the OV
        once known) skips detection; otherwise it is detected and returned with its probability."""
        file_prefix = f"[{current_file}/{total_files} Files]"
        target_size = model_size or self.default_model_size

        try:
            on_progress(file_id, "transcribing", 5, f"{file_prefix} Step 2/5: Extracting audio...")
            audio = self.load_audio(video_path, job=job)
            duration = len(audio) / SAMPLE_RATE
            speech = speech_timeline(audio)
            if not speech:
                raise Exception(f"No speech detected in {duration:.0f} s of audio: no subtitle to make.")

            whisper = self._get_model(target_size)
            if language:
                lang_prob = 1.0
            else:
                language, lang_prob = self.detect_language(audio, target_size)
                logger.info(f"Detected audio language: {language} ({lang_prob:.0%})")

            options = dict(language=language, beam_size=5, word_timestamps=True,
                           initial_prompt=names_prompt(names), condition_on_previous_text=False)
            if spans:
                # faster-whisper ignores the VAD when clips are given: the caller chose the spans.
                options["clip_timestamps"] = [x for s in spans for x in s]
            else:
                # Silero VAD skips silence and music, the main source of invented lines on quiet passages.
                options.update(vad_filter=True, vad_parameters=VAD)
            segments, _ = whisper.transcribe(audio, **options)

            out: List[AsrSegment] = []
            last_logged_pct = -1
            for seg in segments:
                if job:
                    job.raise_if_aborted()
                out.append(AsrSegment(
                    start=seg.start, end=seg.end, text=seg.text.strip(), avg_logprob=seg.avg_logprob,
                    no_speech_prob=seg.no_speech_prob, compression_ratio=seg.compression_ratio,
                    words=[AsrWord(w.start, w.end, w.word, w.probability) for w in (seg.words or [])]))

                current_pct = min(100, int(seg.end / duration * 100))
                on_progress(file_id, "transcribing", 10 + current_pct * 80 // 100,
                            f"{file_prefix} Step 2/5: AI Transcribing ({current_pct}%)")
                if current_pct >= last_logged_pct + 5:
                    logger.info(f"Transcription Progress: {current_pct}%")
                    last_logged_pct = (current_pct // 5) * 5

            if not any(w.text.strip() for s in out for w in s.words):
                # An empty result would flow on to translation and muxing as a « done » job with blank tracks.
                raise Exception(f"No words recognised in {duration:.0f} s of audio: no subtitle to make.")

            on_progress(file_id, "transcribing", 95, f"{file_prefix} Step 2/5: Finalizing subtitles...")
            return AsrResult(model=target_size, language=language, language_probability=lang_prob,
                             duration=duration, speech=speech, segments=out)

        except JobAborted:
            logger.warning(f"🛑 {file_prefix} Transcription aborted by user.")
            raise
        except Exception as e:
            logger.error(f"❌ {file_prefix} Transcription error: {str(e)}")
            raise
