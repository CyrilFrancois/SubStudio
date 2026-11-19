# llama_server.py — LlamaServer: starts a native llama.cpp server on demand, capped at 4 threads, and stops it. Exports: LlamaServer.
"""
One heavy model at a time (D-0001): the translation model is loaded only while a video is being translated and
unloaded before the next transcription, so Whisper and the LLM never sit in RAM together.

The server is a child process of the backend. A failed start raises with the tail of its log, never « no answer ».
"""

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("SubStudio.LlamaServer")

THREADS = 4          # half of the Ryzen 5 7520U's 8 threads (D-0001)
CONTEXT = 4096       # a 15-cue batch plus the prompt is under 1 500 tokens; 4096 leaves room for long cues
START_TIMEOUT_S = 180


class LlamaServer:
    def __init__(self, binary: str, model: str, port: int, gpu_layers: int, log_dir: Path):
        self.binary, self.model, self.port, self.gpu_layers = binary, model, port, gpu_layers
        self.url = f"http://127.0.0.1:{port}"
        self.log_path = log_dir / "llama-server.log"
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def ensure_running(self) -> str:
        """Start the server if it is not up; return its base URL. Raises RuntimeError when it cannot start."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return self.url
            for path, what in ((self.binary, "LLAMA_SERVER_BIN"), (self.model, "LLAMA_MODEL_PATH")):
                if not path or not os.path.isfile(path):
                    raise RuntimeError(f"{what} does not point to a file: {path!r}")
            if self._answers():
                # An orphan (a crashed backend's server, a bench run) would answer our /health and translate with
                # whatever model it holds, while our own process fails to bind and exits.
                raise RuntimeError(f"port {self.port} is already served by another process: stop it or set LLAMA_PORT")
            args = [self.binary, "-m", self.model, "--host", "127.0.0.1", "--port", str(self.port),
                    "--threads", str(THREADS), "--threads-batch", str(THREADS), "-c", str(CONTEXT),
                    "-np", "1", "-ngl", str(self.gpu_layers), "--no-webui"]
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log = open(self.log_path, "w", encoding="utf-8")
            logger.info(f"Loading translation model {Path(self.model).name} (llama.cpp, {THREADS} threads, "
                        f"{self.gpu_layers} layers on the GPU)")
            t0 = time.time()
            self._proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
            log.close()
            while time.time() - t0 < START_TIMEOUT_S:
                if self._proc.poll() is not None:
                    raise RuntimeError(f"llama-server exited with code {self._proc.returncode}: {self._log_tail()}")
                if self._answers():
                    logger.info(f"Translation model ready in {time.time() - t0:.0f} s")
                    return self.url
                time.sleep(0.5)
            self._kill()
            raise RuntimeError(f"llama-server not ready after {START_TIMEOUT_S} s: {self._log_tail()}")

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                logger.info("Unloading translation model")
            self._kill()

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(15)
        self._proc = None

    def _answers(self) -> bool:
        try:
            return httpx.get(f"{self.url}/health", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def _log_tail(self) -> str:
        try:
            return " | ".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:])
        except OSError as e:
            return f"(no log: {e})"
