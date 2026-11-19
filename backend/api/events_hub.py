# events_hub.py — One SSE broadcast for every browser tab (GET /api/events). Exports: hub, EventHub.

import asyncio
import logging
from typing import Callable, Iterable, List, Optional, Set

from .schemas import EventFrame

logger = logging.getLogger("SubStudio.EventHub")

KEEPALIVE_S = 15.0
QUEUE_MAX = 500


class EventHub:
    """publish() may be called from a worker thread: frames are handed to the loop thread-safely,
    because asyncio.Queue is not thread-safe (same rule as core/events.py)."""

    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def publish(self, frame: EventFrame):
        loop = self._loop
        if loop is None:
            return  # nobody has ever subscribed: nothing to deliver to
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(frame)
            return
        try:
            loop.call_soon_threadsafe(self._deliver, frame)
        except RuntimeError:
            # The loop that last served a subscriber is closed (server restarted under --reload):
            # nobody can be listening on it, and the job that published must not fail for that.
            self._loop = None

    def _deliver(self, frame: EventFrame):
        for q in list(self._subscribers):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                # A tab too slow to drain 500 frames is cut off; EventSource reconnects and gets a
                # fresh snapshot, which is correct, instead of silently missing frames forever.
                logger.warning("SSE subscriber fell behind; disconnecting it so it resyncs")
                self._subscribers.discard(q)
                q.get_nowait()       # make room for the end-of-stream marker
                q.put_nowait(None)

    async def stream(self, snapshot: Callable[[], Iterable[EventFrame]]):
        self._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subscribers.add(q)
        try:
            # Snapshot after subscribing, so no frame published in between is lost (a duplicate
            # job frame is harmless: job frames are full snapshots).
            for frame in snapshot():
                yield f"data: {frame.model_dump_json()}\n\n"
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if frame is None:
                    return
                yield f"data: {frame.model_dump_json()}\n\n"
        finally:
            self._subscribers.discard(q)


hub = EventHub()
