# speech.py — Where people speak in a video: decode its audio with ffmpeg and run the Silero VAD bundled with faster-whisper. Exports: Span, read_audio, detect_speech, SpeechError.
"""The only part of the gate that touches a file or a model; every check works on the spans it returns."""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import List, Tuple

import numpy as np

logger = logging.getLogger("SubStudio.Gate.Speech")

SAMPLE_RATE = 16_000
Span = Tuple[int, int]  # (start_ms, end_ms)


class SpeechError(RuntimeError):
    """The audio could not be decoded, or holds no sample."""


def read_audio(media_path: str, stream: int = 0) -> np.ndarray:
    """Audio stream `stream` of a video (or an audio file) as 16 kHz mono float32 in [-1, 1]."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SpeechError("ffmpeg is not on PATH: cannot read the audio")
    # One decoding thread: the gate runs beside a loaded model and must stay inside D-0001's budget.
    cmd = [ffmpeg, "-v", "error", "-threads", "1", "-i", media_path, "-threads", "1", "-map", f"0:a:{stream}", "-vn", "-ac", "1",
           "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        logger.error(f"ffmpeg could not read the audio: {tail[0]}")
        raise SpeechError(f"cannot read the audio: {tail[0]}")
    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        raise SpeechError("the audio track is empty")
    return audio


def detect_speech(audio: np.ndarray) -> List[Span]:
    """Speech spans in ms. Short silences (≥ 300 ms) split spans, so a gap between two lines is visible to the
    sync checks; faster-whisper's own defaults (2 s) are tuned for transcription and would merge them."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(threshold=0.5, min_speech_duration_ms=200, min_silence_duration_ms=300, speech_pad_ms=100)
    per_ms = SAMPLE_RATE // 1000
    return [(t["start"] // per_ms, t["end"] // per_ms) for t in get_speech_timestamps(audio, opts)]
