import queue
import threading
from typing import Optional, Tuple

import numpy as np

import squid.logging
from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

_log = squid.logging.get_logger("RecordingWriter")
_SENTINEL = object()


class CountStop:
    def __init__(self, target: int):
        self.target = target

    def met(self, emitted: int) -> bool:
        return emitted >= self.target


class RecordingRouter:
    """Maps incoming frames to (t,c,z)=(t_index,0,0), downsampling to `fps`."""

    def __init__(self, fps: float):
        self._min_period = 1.0 / fps if fps and fps > 0 else 0.0
        self._t_index = 0
        self._last_emit_ts: Optional[float] = None

    def route(self, timestamp: float) -> Optional[Tuple[int, int, int]]:
        if self._last_emit_ts is not None and (timestamp - self._last_emit_ts) < self._min_period - 1e-9:
            return None
        idx = (self._t_index, 0, 0)
        self._t_index += 1
        self._last_emit_ts = timestamp
        return idx


class RecordingWriter:
    """Bounded-queue writer that drains frames to a ZarrWriter on a background thread.

    The hot camera callback calls `enqueue` (non-blocking); the background thread
    calls `ZarrWriter.write_frame` which may block on I/O.  The queue is bounded so
    that a slow disk eventually provides backpressure: `enqueue` will block for up
    to 0.5 s before logging a drop and returning.
    """

    def __init__(self, config: ZarrAcquisitionConfig, max_queue: int = 64):
        self._writer = ZarrWriter(config)
        self._q: "queue.Queue" = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._dropped = 0

    def start(self) -> None:
        """Initialize the underlying ZarrWriter and start the drain thread."""
        self._writer.initialize()
        self._thread.start()

    def enqueue(self, frame: np.ndarray, t: int, c: int, z: int) -> None:
        """Non-blocking enqueue.  Blocks briefly as backpressure; drops on full."""
        try:
            self._q.put((frame, t, c, z), timeout=0.5)
        except queue.Full:
            self._dropped += 1
            _log.warning(f"recording queue full; dropped frame t={t} (total dropped={self._dropped})")

    def _drain(self) -> None:
        """Background thread: pulls items and writes them via ZarrWriter."""
        while True:
            item = self._q.get()
            if item is _SENTINEL:
                return
            frame, t, c, z = item
            try:
                self._writer.write_frame(frame, t=t, c=c, z=z)
            except Exception as e:
                _log.error(f"recording write_frame failed t={t}: {e}")

    def finalize(self) -> None:
        """Flush the queue, join the drain thread, and finalize the ZarrWriter."""
        self._q.put(_SENTINEL)
        self._thread.join()
        self._writer.finalize()

    def abort(self) -> None:
        """Signal the drain thread to stop, then abort the ZarrWriter."""
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        self._thread.join(timeout=2.0)
        self._writer.abort()
