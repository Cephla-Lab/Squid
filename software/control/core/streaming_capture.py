import queue
import threading
from typing import Callable, Optional, Tuple

import numpy as np

import squid.logging
from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter
from squid.abc import CameraAcquisitionMode

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


# ---------------------------------------------------------------------------
# Task C3: ContinuousFrameSource + StreamingCapture
# ---------------------------------------------------------------------------


class ContinuousFrameSource:
    """Wraps a camera and delivers frames via callback.

    Calls set_frame_rate, set_acquisition_mode(CONTINUOUS), registers a frame
    callback, and starts/stops streaming.
    """

    def __init__(self, camera, fps: float):
        self._camera = camera
        self._fps = fps
        self._cb_id: Optional[int] = None

    def start(self, on_frame: Callable) -> None:
        self._camera.set_frame_rate(self._fps)
        self._camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
        self._cb_id = self._camera.add_frame_callback(on_frame)
        self._camera.start_streaming()

    def stop(self) -> None:
        self._camera.stop_streaming()
        if self._cb_id is not None:
            self._camera.remove_frame_callback(self._cb_id)
            self._cb_id = None


class StreamingCapture:
    """Orchestrates a frame source, router, stop condition, and writer.

    ``run()`` starts the source, routes each incoming frame through the router,
    enqueues accepted frames to the writer, and stops when the stop condition is
    met or ``abort_fn`` returns True.

    The ``_on_frame`` callback runs on the hot camera thread — it must stay cheap
    (route + enqueue only, no blocking I/O).

    Args:
        frame_source: Any object with ``start(on_frame)`` / ``stop()`` interface.
        router: ``RecordingRouter`` (or compatible) — maps timestamps to (t,c,z).
        stop_condition: ``CountStop`` (or compatible) — ``met(emitted)`` returns bool.
        writer: Object with ``start()``, ``enqueue(frame,t,c,z)``, ``finalize()``, ``abort()``.
        abort_fn: Zero-argument callable; returns True to abort early.
        timeout: Optional seconds to wait for completion.  If the source does not
            trigger the done event within this time ``run()`` still stops and
            finalizes (returns frames emitted so far).  None means wait forever.
    """

    def __init__(self, frame_source, router, stop_condition, writer, abort_fn: Callable[[], bool]):
        self._source = frame_source
        self._router = router
        self._stop = stop_condition
        self._writer = writer
        self._abort_fn = abort_fn
        self._emitted = 0
        self._done = threading.Event()

    def _on_frame(self, camera_frame) -> None:
        """Hot-thread callback: route + enqueue only.  Must not block."""
        if self._done.is_set():
            return
        if self._abort_fn():
            self._done.set()
            return
        idx = self._router.route(camera_frame.timestamp)
        if idx is not None:
            self._writer.enqueue(camera_frame.frame, *idx)
            self._emitted += 1
            if self._stop.met(self._emitted):
                self._done.set()

    def run(self, timeout: Optional[float] = None) -> int:
        """Start capture, block until done (or timeout), and return emitted count."""
        self._writer.start()
        try:
            self._source.start(self._on_frame)
            self._done.wait(timeout)  # FakeSource sets _done synchronously; real camera via callback
        finally:
            # Assumes source.stop() quiesces the camera delivery thread. With cameras
            # that don't join their callback thread on stop, a final in-flight frame may
            # reach writer.enqueue after finalize — harmless with RecordingWriter (the
            # drain thread has exited, so the put times out and the frame is logged as
            # dropped, not corrupted).
            self._source.stop()
            self._writer.finalize()
        return self._emitted
