# jobs.py — Cooperative cancellation for running pipeline jobs. Exports: JobAborted, JobHandle, JobRegistry, job_registry.

import logging
import subprocess
import threading
from typing import Dict, List, Optional

logger = logging.getLogger("SubStudio.Jobs")


class JobAborted(Exception):
    def __init__(self, file_id: str):
        super().__init__(f"Job aborted: {file_id}")
        self.file_id = file_id


class JobHandle:
    """
    One per video in flight. The pipeline runs in worker threads while aborts arrive on the
    event loop, so every field here is guarded: `_aborted` is an Event and the tracked
    subprocess sits behind a lock.
    """

    def __init__(self, file_id: str):
        self.file_id = file_id
        self._aborted = threading.Event()
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None

    @property
    def is_aborted(self) -> bool:
        return self._aborted.is_set()

    def raise_if_aborted(self):
        if self._aborted.is_set():
            raise JobAborted(self.file_id)

    def track(self, process: subprocess.Popen):
        """Register the running FFmpeg/ffprobe child so abort() can kill it mid-encode."""
        with self._lock:
            self._process = process
            # abort() may have fired between the check and the spawn; kill immediately.
            if self._aborted.is_set():
                self._kill_locked()

    def untrack(self):
        with self._lock:
            self._process = None

    def abort(self):
        self._aborted.set()
        with self._lock:
            self._kill_locked()

    def _kill_locked(self):
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.kill()
            logger.warning(f"Killed subprocess {process.pid} for {self.file_id}")
        except Exception as e:
            logger.error(f"Could not kill subprocess for {self.file_id}: {e}")


class JobRegistry:
    def __init__(self):
        self._jobs: Dict[str, JobHandle] = {}
        self._lock = threading.Lock()

    def register(self, file_id: str) -> JobHandle:
        """Called for every video in a batch UP FRONT, so abort_all also cancels queued ones."""
        with self._lock:
            handle = JobHandle(file_id)
            self._jobs[file_id] = handle
            return handle

    def get(self, file_id: str) -> Optional[JobHandle]:
        with self._lock:
            return self._jobs.get(file_id)

    def release(self, file_id: str):
        with self._lock:
            self._jobs.pop(file_id, None)

    def abort(self, file_id: str) -> bool:
        with self._lock:
            handle = self._jobs.get(file_id)
        if handle is None:
            return False
        handle.abort()
        return True

    def abort_all(self) -> List[str]:
        with self._lock:
            handles = list(self._jobs.values())
        for handle in handles:
            handle.abort()
        return [h.file_id for h in handles]

    def active_ids(self) -> List[str]:
        with self._lock:
            return list(self._jobs.keys())


job_registry = JobRegistry()
