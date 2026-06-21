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

    After `start()` the drain thread is the SOLE owner of the ZarrWriter: only it
    calls `write_frame`, `finalize`, and `abort`.  The main thread only calls
    `initialize()` (before the thread starts) and then enqueues items / signals stop.
    This prevents the data race where `abort()` used to call `self._writer.abort()`
    concurrently with the drain thread still inside `write_frame`.
    """

    def __init__(self, config: ZarrAcquisitionConfig, max_queue: int = 64):
        self._writer = ZarrWriter(config)
        self._q: "queue.Queue" = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._dropped = 0
        self._abort_requested = threading.Event()

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
        """Background thread: sole owner of ZarrWriter after start().

        Reads the queue with a short timeout so it can notice an abort between
        frames.  On exit, calls writer.abort() or writer.finalize() as appropriate.
        """
        try:
            while True:
                if self._abort_requested.is_set():
                    break
                try:
                    item = self._q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is _SENTINEL:
                    break
                frame, t, c, z = item
                try:
                    self._writer.write_frame(frame, t=t, c=c, z=z)
                except Exception as e:
                    _log.error(f"recording write_frame failed t={t}: {e}")
        finally:
            if self._abort_requested.is_set():
                self._writer.abort()
            else:
                self._writer.finalize()

    def finalize(self) -> None:
        """Flush the queue, join the drain thread (which finalizes the ZarrWriter)."""
        self._q.put(_SENTINEL)
        self._thread.join(timeout=30.0)
        if self._thread.is_alive():
            _log.warning("drain thread still alive after finalize() join timeout")

    def abort(self) -> None:
        """Signal the drain thread to stop (which aborts the ZarrWriter)."""
        self._abort_requested.set()
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            _log.warning("drain thread still alive after abort() join timeout")
