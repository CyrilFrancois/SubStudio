# artifacts.py — Finished work kept across retries and restarts: a transcription keyed by the audio it heard, translated lines keyed by their source. Exports: ArtifactCache, artifacts, use_artifacts, audio_fingerprint, ASR_VERSION.
"""Never redo finished work (TODOLIST « Never redo finished work after a restart or a retry »).

- A transcription is keyed by the AUDIO it heard: a SHA-256 of the first audio stream's packets (ffmpeg's `hash`
  muxer, stream copy, no decoding). The verified swap copies the audio unchanged, so the key survives the rename
  and the new subtitle tracks (measured 2026-11-19: S05E12 before and after its swap, same 897989b9…). Plus the
  Whisper model and ASR_VERSION: a different model or transcriber setting is different work.
- Translated lines are keyed by their source (every cue id and text), the target language, the translator's name
  and the episode context. Saved after every validated batch, so a language that fails at line 272 keeps the 271
  before it, and a rerun asks the model only for what is missing (S05E12, 2026-11-19: 45 min lost on one line).

Files live in <STATE_DIR>/cache/<kind>/<key[:2]>/<key>.json (SUBSTUDIO_CACHE_DIR overrides), written atomically.
A cache that cannot be read or written never fails a job: the work is done again, and the error is logged.
"""

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .subtitles import Cue, TranslationContext

logger = logging.getLogger("SubStudio.Artifacts")

# Bump when a transcriber setting changes what Whisper writes (VAD, beam, segmentation, wrapping): old entries miss.
ASR_VERSION = 1

_FINGERPRINTS: Dict[Tuple[str, int, int], str] = {}
_FP_LOCK = threading.Lock()


def _key(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def audio_fingerprint(video_path: str) -> str:
    """SHA-256 of the first audio stream's packets. Remembered per exact file (path, mtime, size). Raises when the
    video has no readable audio: no fingerprint, no cache, never a shared key."""
    st = os.stat(video_path)
    memo = (os.path.normcase(os.path.realpath(video_path)), st.st_mtime_ns, st.st_size)
    with _FP_LOCK:
        if memo in _FINGERPRINTS:
            return _FINGERPRINTS[memo]
    cmd = ["ffmpeg", "-v", "error", "-i", str(video_path), "-map", "0:a:0", "-c", "copy",
           "-f", "hash", "-hash", "sha256", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    out = r.stdout.strip()
    if r.returncode != 0 or not out.startswith("SHA256=") or len(out) != len("SHA256=") + 64:
        raise RuntimeError(f"no audio fingerprint for {os.path.basename(video_path)}: {(r.stderr or out)[:200]}")
    fp = out.split("=", 1)[1]
    with _FP_LOCK:
        _FINGERPRINTS[memo] = fp
    return fp


class ArtifactCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, kind: str, key: str) -> Path:
        return self.root / kind / key[:2] / f"{key}.json"

    def get(self, kind: str, key: str) -> Optional[dict]:
        p = self._path(kind, key)
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            logger.error("Cached %s %s is unreadable (%s): the work is done again", kind, key[:12], e)
            return None

    def put(self, kind: str, key: str, value: dict) -> None:
        p = self._path(kind, key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(value, f, ensure_ascii=False)
                os.replace(tmp, p)   # a reader sees the old file or the new one, never half of one
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.error("Could not keep %s %s (%s): a retry will redo this work", kind, key[:12], e)

    # ------------------------------------------------------------ transcriptions

    @staticmethod
    def asr_key(fingerprint: str, model: str) -> str:
        return _key("asr", ASR_VERSION, fingerprint, model)

    def load_asr(self, key: str) -> Optional[Tuple[List[Cue], Optional[str]]]:
        v = self.get("asr", key)
        if not v:
            return None
        return [Cue(**c) for c in v["cues"]], v.get("language")

    def save_asr(self, key: str, cues: List[Cue], language: Optional[str]) -> None:
        self.put("asr", key, {"cues": [asdict(c) for c in cues], "language": language})

    # ------------------------------------------------------------ translated lines

    @staticmethod
    def lines_key(source: List[Cue], target: str, translator: str, context: TranslationContext) -> str:
        # `previous` is per batch (the three lines before it), not per video: left out so the key is the video's.
        ctx = {k: v for k, v in asdict(context).items() if k != "previous"}
        return _key("lines", [(c.id, c.text) for c in source], target, translator, ctx)

    def load_lines(self, key: str) -> Dict[int, str]:
        v = self.get("lines", key)
        return {int(k): t for k, t in (v or {}).get("texts", {}).items()}

    def save_lines(self, key: str, texts: Dict[int, str]) -> None:
        self.put("lines", key, {"texts": {str(k): t for k, t in texts.items()}})


_cache: Optional[ArtifactCache] = None


def artifacts() -> ArtifactCache:
    """The process's cache, in SubStudio's one state dir (api/config.STATE_DIR), or SUBSTUDIO_CACHE_DIR."""
    global _cache
    if _cache is None:
        root = os.getenv("SUBSTUDIO_CACHE_DIR")
        if not root:
            from api.config import STATE_DIR
            root = str(Path(STATE_DIR) / "cache")
        _cache = ArtifactCache(Path(root))
    return _cache


def use_artifacts(cache: Optional[ArtifactCache]) -> None:
    """Tests: point the process at a cache of their own (None: back to the default)."""
    global _cache
    _cache = cache
