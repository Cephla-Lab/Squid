import queue
import threading
import time
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

    def expected(self) -> Optional[int]:
        """Expected total frame count, used for partial-capture warnings."""
        return self.target


class RecordingRouter:
    """Maps incoming frames to (t,c,z)=(slot,0,0), downsampling to `fps`.

    Each accepted frame goes to the slot NEAREST its arrival time relative to
    the first frame (``slot = round(elapsed / period)``, ties rounding down);
    a frame whose nearest slot is already filled is rejected.  This anchors
    pacing absolutely (host-delivery jitter around the period cannot reject
    at-rate frames or halve the capture rate) and, after a delivery stall, a
    burst of frames does NOT back-fill the missed slots — the stall stays in
    the data as fill-value holes, keeping the time axis honest (the store is
    then sealed incomplete by the under-delivery path).
    """

    def __init__(self, fps: float):
        self._period = 1.0 / fps if fps and fps > 0 else 0.0
        self._t_index = 0  # next unfilled slot
        self._first_ts: Optional[float] = None

    def route(self, timestamp: float) -> Optional[Tuple[int, int, int]]:
        if self._first_ts is None:
            self._first_ts = timestamp
            slot = 0
        elif self._period > 0:
            # Nearest slot; the epsilon makes exact half-period ties round DOWN
            # so a 2x-rate camera still has alternate frames rejected.
            slot = int((timestamp - self._first_ts) / self._period + 0.5 - 1e-9)
            if slot < self._t_index:
                return None
        else:
            slot = self._t_index
        idx = (slot, 0, 0)
        self._t_index = slot + 1
        return idx


class RecordingWriter:
    """Bounded-queue writer that drains frames to a ZarrWriter on a background thread.

    The hot camera callback calls `enqueue` (truly non-blocking); the background
    thread calls `ZarrWriter.write_frame` which may block on I/O.  The queue is
    bounded so that a slow disk eventually fills it: when full, `enqueue` drops the
    frame immediately (never blocks the camera delivery thread) and logs a warning.

    After `start()` the drain thread is the SOLE owner of the ZarrWriter: only it
    calls `write_frame`, `finalize`, and `abort`.  The main thread only calls
    `initialize()` (before the thread starts) and then enqueues items / signals stop.
    This prevents the data race where `abort()` used to call `self._writer.abort()`
    concurrently with the drain thread still inside `write_frame`.
    """

    def __init__(self, config: ZarrAcquisitionConfig, max_queue: int = 256, max_bytes: int = 2 * 1024**3):
        self._writer = ZarrWriter(config)
        self._q: "queue.Queue" = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._dropped = 0
        self._write_errors = 0
        # Byte cap alongside the count cap: 256 full-resolution 16-bit frames is
        # ~13 GB, so a count-only bound can OOM the process on a slow disk.
        self._max_bytes = max_bytes
        self._held_bytes = 0
        self._bytes_lock = threading.Lock()
        self._abort_requested = threading.Event()
        # True when finalize() gave up on a wedged drain thread: write errors
        # may still be accruing after finalize() returns, so callers must not
        # trust write_error_count == 0 as "healthy" — check this flag too.
        self._finalize_wedged = False
        # Set instead of the abort flag when finalize() gives up on a wedged
        # drain: the drain writes the remaining CAPTURED frames whenever the
        # stall clears, then exits and seals (abort would discard them).
        self._flush_and_exit = threading.Event()
        # (captured, expected) reported by the capture when frames never
        # arrived (camera stall) — drops/errors are counted here, but only the
        # capture knows about frames it expected and never saw.
        self._incomplete_info: Optional[Tuple[int, int]] = None
        # True only once the drain thread has actually been started.  finalize()/
        # abort() must not join (or push the sentinel to) a thread that never
        # started, otherwise a failure in start()'s initialize() would surface as
        # "cannot join thread before it is started" and mask the real error.
        self._started = False

    def start(self) -> None:
        """Initialize the underlying ZarrWriter and start the drain thread.

        ``initialize()`` runs BEFORE the thread starts.  If it raises, the thread
        is never started and ``_started`` stays False, so a later finalize()/abort()
        cleanly no-ops the join and the original ``initialize()`` error propagates.
        """
        self._writer.initialize()
        try:
            self._thread.start()
        except Exception:
            # initialize() already opened the writer, but the drain thread (its
            # sole owner after start()) will never run to close it — release it
            # here before propagating so we don't leak the ZarrWriter.
            self._writer.abort()
            raise
        self._started = True

    def enqueue(self, frame: np.ndarray, t: int, c: int, z: int) -> None:
        """Truly non-blocking enqueue: drops the frame on a full queue.

        Runs on the hot camera delivery thread, so it must never block.  When the
        bounded queue is full (drain thread cannot keep up with disk I/O) the frame
        is dropped and counted rather than waiting for space.
        """
        nbytes = int(getattr(frame, "nbytes", 0))
        with self._bytes_lock:
            over_cap = self._held_bytes + nbytes > self._max_bytes
            if not over_cap:
                self._held_bytes += nbytes
        if over_cap:
            self._dropped += 1
            # Rate-limited: this runs on the hot camera delivery thread, and a
            # stalled disk would otherwise log (and do handler I/O) at fps rate.
            if self._dropped == 1 or self._dropped % 100 == 0:
                _log.warning(
                    f"recording byte cap reached ({self._max_bytes} B); dropped frame t={t} "
                    f"(total dropped={self._dropped})"
                )
            return
        try:
            self._q.put_nowait((frame, t, c, z))
        except queue.Full:
            with self._bytes_lock:
                self._held_bytes -= nbytes
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
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
                    if self._flush_and_exit.is_set():
                        break  # backlog flushed after a wedged finalize()
                    continue
                if item is _SENTINEL:
                    break
                frame, t, c, z = item
                try:
                    self._writer.write_frame(frame, t=t, c=c, z=z)
                except Exception as e:
                    self._write_errors += 1
                    _log.error(f"recording write_frame failed t={t}: {e}")
                finally:
                    with self._bytes_lock:
                        self._held_bytes -= int(getattr(frame, "nbytes", 0))
        finally:
            if self._abort_requested.is_set():
                self._writer.abort()
            elif self._write_errors > 0 or self._dropped > 0 or self._incomplete_info is not None:
                # Never stamp acquisition_complete=True on a store with failed
                # writes, dropped frames, or frames that never arrived: some
                # planes are silent fill values.  Seal acquisition_complete=False
                # with the counts, but do NOT mark it "aborted" — this was not a
                # user abort, and the queue was drained so no captured data is
                # lost.
                extra = {
                    "incomplete": True,
                    "write_errors": self._write_errors,
                    "dropped_frames": self._dropped,
                }
                if self._incomplete_info is not None:
                    extra["captured_frames"], extra["expected_frames"] = self._incomplete_info
                _log.error(f"recording store sealed INCOMPLETE: {extra}")
                self._writer.abort(mark_aborted=False, extra_attrs=extra)
            else:
                self._writer.finalize()

    @property
    def dropped_count(self) -> int:
        """Total frames dropped due to a full queue (diagnosable in slow-disk runs)."""
        return self._dropped

    @property
    def write_error_count(self) -> int:
        """Total write_frame failures on the drain thread (0 on a healthy run)."""
        return self._write_errors

    @property
    def finalize_wedged(self) -> bool:
        """True if finalize() gave up on a wedged drain thread.

        In that state write_error_count may still be 0 (the errors happen after
        finalize() returned), so fail-fast callers must treat wedged as failure.
        """
        return self._finalize_wedged

    def mark_incomplete(self, captured: int, expected: int) -> None:
        """Record that the capture under-delivered (frames never arrived).

        Called by StreamingCapture before finalize() when the stop condition
        was not met with no user abort — drops and write errors are counted
        internally, but only the capture knows about frames it expected and
        never saw.  Makes the drain seal the store acquisition_complete=False.
        """
        self._incomplete_info = (int(captured), int(expected))

    def finalize(self, timeout_s: float = 30.0) -> None:
        """Flush the queue, join the drain thread (which seals the ZarrWriter).

        The sentinel push is bounded: with the drain thread wedged inside a
        stalled write and the queue full, a bare ``put`` would block the
        acquisition thread forever (the join timeout below would never be
        reached).  After ``timeout_s`` of no queue space, fall back to the
        abort path and return — the daemon drain thread seals the store
        whenever the stalled write finally returns.
        """
        if not self._started:
            # start() never got the thread running (e.g. initialize() raised).
            # Nothing to flush or join; let the original error propagate.
            return
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                self._q.put(_SENTINEL, timeout=min(1.0, max(0.1, timeout_s)))
                break
            except queue.Full:
                if time.monotonic() >= deadline:
                    _log.error(
                        f"drain thread wedged (queue full for {timeout_s:.0f}s); giving up the wait — "
                        f"the captured backlog will be flushed and sealed whenever the stall clears"
                    )
                    self._finalize_wedged = True
                    # Flush-and-exit, NOT abort: the queued frames are captured
                    # data; the daemon drain writes them once the stalled write
                    # returns, then exits and seals the store.
                    self._flush_and_exit.set()
                    return
        # The put and the join share ONE budget: a sentinel accepted late must
        # not be followed by a fresh full-length join (callers would block for
        # up to ~2x timeout_s).
        self._thread.join(timeout=max(0.1, deadline - time.monotonic()))
        if self._thread.is_alive():
            # Slow but progressing backlog — NOT wedged (flagging it would
            # fail-fast whole multi-well runs over stores that finish fine).
            # The daemon drain seals the store when it finishes.
            _log.warning("drain thread still writing after finalize() join timeout; store seals when it finishes")

    def abort(self) -> None:
        """Signal the drain thread to stop (which aborts the ZarrWriter)."""
        if not self._started:
            # start() never got the thread running (e.g. initialize() raised).
            self._abort_requested.set()
            return
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

    Calls set_acquisition_mode(CONTINUOUS), set_frame_rate, registers a frame
    callback, and starts/stops streaming.
    """

    def __init__(self, camera, fps: float, already_configured: bool = False):
        self._camera = camera
        self._fps = fps
        # True when the caller already set CONTINUOUS mode + frame rate (the
        # achievable-fps probe in record() does exactly that): skip repeating
        # both, which on toupcam costs a mode switch plus a strobe/exposure
        # re-send per FOV.
        self._already_configured = already_configured
        self._cb_id: Optional[int] = None

    def start(self, on_frame: Callable) -> None:
        # Order matters: switching to CONTINUOUS resets the frame-rate strategy to
        # MAX on toupcam, wiping any earlier fps hint.  Set the mode FIRST, then the
        # frame rate, so the PRECISE_FRAMERATE hint survives the mode switch.
        if not self._already_configured:
            self._camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
            self._camera.set_frame_rate(self._fps)
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
        self._aborted = False

    def _on_frame(self, camera_frame) -> None:
        """Hot-thread callback: route + enqueue only.  Must not block."""
        if self._done.is_set():
            return
        if self._abort_fn():
            self._aborted = True
            self._done.set()
            return
        # Out-of-bounds guard: if the stop condition is already met for the current
        # emitted count, do not route/enqueue.  A frame that arrives in-flight after
        # CountStop(T) is satisfied would otherwise route to t-index == T and enqueue
        # into a (T, ...)-shaped dataset (out of bounds).  Re-check here, not just at
        # entry, so once _emitted == T no further frame is ever emitted.
        if self._stop.met(self._emitted):
            self._done.set()
            return
        idx = self._router.route(camera_frame.timestamp)
        if idx is not None:
            expected = self._stop.expected() if hasattr(self._stop, "expected") else None
            if expected is not None and idx[0] >= expected:
                # The router's slot ran past the dataset (a delivery stall
                # pushed the timeline beyond T): nothing left to record into.
                self._done.set()
                return
            self._writer.enqueue(camera_frame.frame, *idx)
            self._emitted += 1
            if self._stop.met(self._emitted):
                self._done.set()

    def run(self, timeout: Optional[float] = None) -> int:
        """Start capture, block until done (or timeout), and return emitted count."""
        self._writer.start()
        try:
            self._source.start(self._on_frame)
            # Poll abort_fn while waiting: the frame callback also samples it, but
            # if the camera delivers no frames at all (stall, misconfigured
            # trigger) the callback never runs and a bare wait(timeout) would
            # ignore Stop for the full timeout — and then seal the store as
            # complete.  (FakeSource sets _done synchronously; the first wait()
            # returns immediately in that case.)
            deadline = (time.monotonic() + timeout) if timeout is not None else None
            while not self._done.wait(0.2):
                if self._abort_fn():
                    self._aborted = True
                    self._done.set()
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    break
        finally:
            # Assumes source.stop() quiesces the camera delivery thread. With cameras
            # that don't join their callback thread on stop, a final in-flight frame may
            # reach writer.enqueue after finalize — harmless with RecordingWriter (the
            # drain thread has exited, so the put times out and the frame is logged as
            # dropped, not corrupted).
            self._source.stop()
            # source.stop() above quiesces the camera delivery thread, so reading
            # self._emitted here is safe without a lock: no callback thread mutates
            # it after this point (and CPython int load/store is atomic anyway).
            expected = self._stop.expected() if hasattr(self._stop, "expected") else None
            if self._aborted:
                # Aborted mid-capture: seal the recording as incomplete, not complete.
                self._writer.abort()
            else:
                if expected is not None and self._emitted < expected:
                    # Stop condition was not met (slow camera / stall / timeout):
                    # some zarr planes are fill values, not real data.  Tell the
                    # writer so the store is sealed acquisition_complete=False.
                    _log.warning(
                        f"streaming capture incomplete: captured {self._emitted}/{expected} "
                        f"frames; missing planes are blank fill"
                    )
                    if hasattr(self._writer, "mark_incomplete"):
                        self._writer.mark_incomplete(self._emitted, expected)
                self._writer.finalize()
            # Surface total dropped frames so slow-disk runs are diagnosable without
            # grepping individual per-frame warnings.
            dropped = self._writer.dropped_count if hasattr(self._writer, "dropped_count") else 0
            if dropped > 0:
                _log.warning(
                    f"streaming capture finished: {dropped} frame(s) dropped total " f"(queue full / slow disk)"
                )
        return self._emitted
