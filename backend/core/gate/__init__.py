# gate — The quality gate of doc/user-process.md §3: is a subtitle synced to the speech and a good file? Exports: evaluate, Verdict, Check, THRESHOLDS, read_audio, detect_speech, SpeechError, measure_sync, measure_text.

from .speech import SpeechError, detect_speech, read_audio
from .sync import measure_sync
from .text import measure_text
from .verdict import THRESHOLDS, Check, Verdict, evaluate

__all__ = ["evaluate", "Verdict", "Check", "THRESHOLDS", "read_audio", "detect_speech", "SpeechError",
           "measure_sync", "measure_text"]
