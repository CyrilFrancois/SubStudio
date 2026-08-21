# events.py — EventManager: thread-safe SSE broadcast per file_id. Exports: event_manager, setup_logging_bridge.

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("SubStudio.Events")


class SSELogHandler(logging.Handler):
    def __init__(self, event_manager: 'EventManager', file_id: str):
        super().__init__()
        self.event_manager = event_manager
        self.file_id = file_id

    def emit(self, record):
        try:
            if "SubStudio.Events" in record.name:
                return
            self.event_manager.emit_log(self.file_id, self.format(record), record.levelname)
        except Exception:
            self.handleError(record)


class EventManager:
    def __init__(self):
        self.listeners: Dict[str, List[asyncio.Queue]] = {}
        self.state_cache: Dict[str, Dict[str, Any]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def emit(self, file_id: str, status: str, progress: int, message: str):
        data = {
            "type": "status",
            "fileId": file_id,
            "status": status,
            "progress": progress,
            "message": message
        }
        self.state_cache[file_id] = data
        self._broadcast(file_id, data)

    def emit_log(self, file_id: str, message: str, level: str = "INFO"):
        self._broadcast(file_id, {
            "type": "log",
            "fileId": file_id,
            "level": level,
            "message": message
        })

    def _broadcast(self, file_id: str, data: Dict[str, Any]):
        if file_id not in self.listeners:
            return

        # The pipeline runs in worker threads (asyncio.to_thread), and asyncio.Queue is NOT
        # thread-safe: put_nowait from off-loop can drop the wakeup and stall the stream.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if self._loop is not None and running is not self._loop:
            self._loop.call_soon_threadsafe(self._deliver, file_id, data)
        else:
            self._deliver(file_id, data)

    def _deliver(self, file_id: str, data: Dict[str, Any]):
        for queue in list(self.listeners.get(file_id, [])):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, file_id: str):
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        if file_id in self.state_cache:
            yield f"data: {json.dumps(self.state_cache[file_id])}\n\n"

        self.listeners.setdefault(file_id, []).append(queue)
        logger.debug(f"New SSE subscriber for: {file_id}")

        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(data)}\n\n"
        except asyncio.CancelledError:
            logger.debug(f"SSE subscriber disconnected: {file_id}")
            raise
        finally:
            if file_id in self.listeners:
                if queue in self.listeners[file_id]:
                    self.listeners[file_id].remove(queue)
                if not self.listeners[file_id]:
                    del self.listeners[file_id]


event_manager = EventManager()


def setup_logging_bridge(file_id: str) -> SSELogHandler:
    handler = SSELogHandler(event_manager, file_id)
    handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)
    return handler
