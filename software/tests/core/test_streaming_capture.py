import numpy as np

from control.core.streaming_capture import CountStop, RecordingRouter, StreamingCapture


def test_count_stop():
    s = CountStop(3)
    assert not s.met(2)
    assert s.met(3)
    assert s.met(4)


def test_recording_router_downsamples_and_indexes():
    r = RecordingRouter(fps=10.0)  # min spacing 0.1 s
    assert r.route(100.00) == (0, 0, 0)  # first frame always emits
    assert r.route(100.05) is None  # 50 ms later -> skip
    assert r.route(100.10) == (1, 0, 0)  # 100 ms later -> emit, t=1
    assert r.route(100.30) == (2, 0, 0)  # emit, t=2


def test_recording_writer_roundtrip(tmp_path):
    import pytest

    pytest.importorskip("tensorstore")  # optional dep; real ZarrWriter needs it
    T, Y, X = 4, 16, 12
    from control.core.zarr_writer import ZarrAcquisitionConfig
    from control.core.streaming_capture import RecordingWriter

    cfg = ZarrAcquisitionConfig(
        output_path=str(tmp_path / "rec.ome.zarr"),
        shape=(T, 1, 1, Y, X),
        dtype=np.uint16,
        pixel_size_um=1.0,
        z_step_um=None,
        time_increment_s=0.1,
        channel_names=["BF"],
        channel_colors=["#FFFFFF"],
        channel_wavelengths=[None],
        is_hcs=False,
    )
    w = RecordingWriter(cfg)
    w.start()
    for t in range(T):
        w.enqueue(np.full((Y, X), t + 1, dtype=np.uint16), t, 0, 0)
    w.finalize()
    import tensorstore as ts

    ds = ts.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": cfg.output_path}}).result()
    assert tuple(ds.shape) == (T, 1, 1, Y, X)
    assert int(ds[2, 0, 0, 0, 0].read().result()) == 3


# ---------------------------------------------------------------------------
# Task C3: StreamingCapture + ContinuousFrameSource tests (fake source)
# ---------------------------------------------------------------------------


class _FakeFrame:
    def __init__(self, ts, arr):
        self.timestamp = ts
        self.frame = arr


class _FakeSource:
    """Delivers N frames synchronously when started."""

    def __init__(self, frames):
        self._frames = frames
        self._cb = None

    def start(self, on_frame):
        self._cb = on_frame
        for f in self._frames:
            self._cb(f)

    def stop(self):
        pass


class _ListWriter:
    def __init__(self):
        self.writes = []

    def start(self):
        pass

    def enqueue(self, frame, t, c, z):
        self.writes.append((t, c, z))

    def finalize(self):
        self.finalized = True

    def abort(self):
        pass


def test_streaming_capture_counts_and_downsamples():
    frames = [_FakeFrame(100.0 + i * 0.05, np.zeros((4, 4), np.uint16)) for i in range(20)]
    w = _ListWriter()
    cap = StreamingCapture(
        _FakeSource(frames),
        RecordingRouter(fps=10.0),
        CountStop(5),
        w,
        abort_fn=lambda: False,
    )
    emitted = cap.run()
    assert emitted == 5
    assert w.writes == [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0)]
    assert getattr(w, "finalized", False) is True


# ---------------------------------------------------------------------------
# Fix-batch tests: start() error path, OOB gating, abort path, partial warning
# ---------------------------------------------------------------------------


def test_recording_writer_start_failure_propagates_without_join_crash(tmp_path, monkeypatch):
    """If ZarrWriter.initialize() raises, start() propagates the original error and
    finalize()/abort() must NOT crash trying to join an unstarted thread."""
    from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    cfg = ZarrAcquisitionConfig(
        output_path=str(tmp_path / "rec.ome.zarr"),
        shape=(2, 1, 1, 4, 4),
        dtype=np.uint16,
        pixel_size_um=1.0,
        z_step_um=None,
        time_increment_s=0.1,
        channel_names=["BF"],
        channel_colors=["#FFFFFF"],
        channel_wavelengths=[None],
        is_hcs=False,
    )

    sentinel_error = RuntimeError("boom from initialize")

    def boom(self):
        raise sentinel_error

    monkeypatch.setattr(ZarrWriter, "initialize", boom)

    w = RecordingWriter(cfg)
    import pytest

    with pytest.raises(RuntimeError, match="boom from initialize"):
        w.start()

    assert w._started is False
    # finalize() and abort() must be safe no-ops (no "cannot join thread" crash).
    w.finalize()
    w.abort()


def test_recording_writer_aborts_writer_when_thread_start_fails(tmp_path, monkeypatch):
    """If initialize() succeeds but the drain thread fails to start, start() must
    abort the already-opened ZarrWriter (no leak) and propagate the error."""
    import pytest
    from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    cfg = ZarrAcquisitionConfig(
        output_path=str(tmp_path / "rec.ome.zarr"),
        shape=(2, 1, 1, 4, 4),
        dtype=np.uint16,
        pixel_size_um=1.0,
        z_step_um=None,
        time_increment_s=0.1,
        channel_names=["BF"],
        channel_colors=["#FFFFFF"],
        channel_wavelengths=[None],
        is_hcs=False,
    )

    # initialize() succeeds (opens the writer); the drain thread then fails to start.
    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)
    aborted = {"called": False}
    monkeypatch.setattr(ZarrWriter, "abort", lambda self: aborted.__setitem__("called", True))

    w = RecordingWriter(cfg)

    class _BoomThread:
        def start(self):
            raise RuntimeError("boom from thread start")

    w._thread = _BoomThread()

    with pytest.raises(RuntimeError, match="boom from thread start"):
        w.start()

    # The writer that initialize() opened must be released, not leaked.
    assert aborted["called"] is True
    assert w._started is False
    # subsequent finalize()/abort() stay safe no-ops.
    w.finalize()
    w.abort()


class _CountingFakeSource:
    """Delivers all frames synchronously, even past the stop count, to exercise the
    out-of-bounds guard in _on_frame."""

    def __init__(self, frames):
        self._frames = frames

    def start(self, on_frame):
        for f in self._frames:
            on_frame(f)

    def stop(self):
        pass


def test_streaming_capture_no_enqueue_past_T_with_extra_frames():
    """Frames arriving after the stop count is met must never be enqueued (would be
    out of bounds for a (T, ...)-shaped dataset)."""
    # No downsampling (fps=0 -> emit every frame); 10 frames but T=3.
    frames = [_FakeFrame(100.0 + i, np.zeros((4, 4), np.uint16)) for i in range(10)]
    w = _ListWriter()
    cap = StreamingCapture(
        _CountingFakeSource(frames),
        RecordingRouter(fps=0.0),
        CountStop(3),
        w,
        abort_fn=lambda: False,
    )
    emitted = cap.run()
    assert emitted == 3
    # Only t-indices 0..2 — nothing at or beyond T=3.
    assert w.writes == [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
    assert all(t < 3 for (t, _, _) in w.writes)


class _RecordingStubWriter:
    """Records which of finalize()/abort() was called."""

    def __init__(self):
        self.writes = []
        self.finalized = False
        self.aborted = False

    def start(self):
        pass

    def enqueue(self, frame, t, c, z):
        self.writes.append((t, c, z))

    def finalize(self):
        self.finalized = True

    def abort(self):
        self.aborted = True


def test_streaming_capture_abort_calls_writer_abort_not_finalize():
    frames = [_FakeFrame(100.0 + i, np.zeros((4, 4), np.uint16)) for i in range(10)]
    w = _RecordingStubWriter()
    cap = StreamingCapture(
        _CountingFakeSource(frames),
        RecordingRouter(fps=0.0),
        CountStop(100),  # never reached
        w,
        abort_fn=lambda: True,  # abort on first frame
    )
    emitted = cap.run()
    assert emitted == 0
    assert w.aborted is True
    assert w.finalized is False


def test_streaming_capture_complete_calls_finalize_not_abort():
    frames = [_FakeFrame(100.0 + i, np.zeros((4, 4), np.uint16)) for i in range(5)]
    w = _RecordingStubWriter()
    cap = StreamingCapture(
        _CountingFakeSource(frames),
        RecordingRouter(fps=0.0),
        CountStop(3),
        w,
        abort_fn=lambda: False,
    )
    emitted = cap.run()
    assert emitted == 3
    assert w.finalized is True
    assert w.aborted is False


def test_streaming_capture_partial_warns(caplog):
    """Fewer frames than T -> finalize + a loud WARNING about partial capture."""
    import logging

    frames = [_FakeFrame(100.0 + i, np.zeros((4, 4), np.uint16)) for i in range(2)]
    w = _RecordingStubWriter()
    cap = StreamingCapture(
        _CountingFakeSource(frames),
        RecordingRouter(fps=0.0),
        CountStop(5),  # expect 5, only 2 delivered
        w,
        abort_fn=lambda: False,
    )
    with caplog.at_level(logging.WARNING):
        emitted = cap.run(timeout=0.1)
    assert emitted == 2
    assert w.finalized is True
    assert w.aborted is False
    assert any("incomplete" in rec.getMessage() and "2/5" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Fix-batch5: dropped_count accessor + summary log
# ---------------------------------------------------------------------------


def test_recording_writer_dropped_count_accessor(tmp_path):
    """dropped_count property returns the number of frames dropped due to a full queue."""
    import pytest

    pytest.importorskip("tensorstore")  # optional dep; real ZarrWriter needs it
    from control.core.zarr_writer import ZarrAcquisitionConfig
    from control.core.streaming_capture import RecordingWriter

    cfg = ZarrAcquisitionConfig(
        output_path=str(tmp_path / "rec.ome.zarr"),
        shape=(4, 1, 1, 4, 4),
        dtype=np.uint16,
        pixel_size_um=1.0,
        z_step_um=None,
        time_increment_s=0.1,
        channel_names=["BF"],
        channel_colors=["#FFFFFF"],
        channel_wavelengths=[None],
        is_hcs=False,
    )
    # Use a queue of size 1 so extra enqueues are dropped immediately.
    w = RecordingWriter(cfg, max_queue=1)
    assert w.dropped_count == 0
    w.start()
    # Fill the queue slot with first frame, then overflow with two more.
    frame = np.zeros((4, 4), dtype=np.uint16)
    w.enqueue(frame, 0, 0, 0)
    w.enqueue(frame, 1, 0, 0)  # may or may not drop depending on drain speed
    w.enqueue(frame, 2, 0, 0)  # likely dropped
    w.finalize()
    # At least one drop should have occurred; exact count is timing-dependent.
    # Just verify the property exists and returns an int.
    assert isinstance(w.dropped_count, int)


def test_streaming_capture_logs_dropped_summary(caplog):
    """When frames are dropped, StreamingCapture logs a summary WARNING at end."""
    import logging

    class _DroppingWriter:
        """Writer that pretends to drop every frame (dropped_count always > 0)."""

        dropped_count = 3

        def start(self):
            pass

        def enqueue(self, frame, t, c, z):
            pass

        def finalize(self):
            pass

        def abort(self):
            pass

    frames = [_FakeFrame(100.0 + i, np.zeros((4, 4), np.uint16)) for i in range(3)]
    w = _DroppingWriter()
    cap = StreamingCapture(
        _CountingFakeSource(frames),
        RecordingRouter(fps=0.0),
        CountStop(3),
        w,
        abort_fn=lambda: False,
    )
    with caplog.at_level(logging.WARNING):
        cap.run()

    assert any("dropped" in rec.getMessage() and "3" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Review-fix tests: fail-loud drain (F2), bounded finalize (F3), abort wake (F4)
# ---------------------------------------------------------------------------


def _stub_zarr_cfg(tmp_path):
    from control.core.zarr_writer import ZarrAcquisitionConfig

    return ZarrAcquisitionConfig(
        output_path=str(tmp_path / "rec.ome.zarr"),
        shape=(4, 1, 1, 4, 4),
        dtype=np.uint16,
        pixel_size_um=1.0,
        z_step_um=None,
        time_increment_s=0.1,
        channel_names=["BF"],
        channel_colors=["#FFFFFF"],
        channel_wavelengths=[None],
        is_hcs=False,
    )


def test_recording_writer_write_errors_seal_incomplete(tmp_path, monkeypatch):
    """If write_frame fails, the store must NOT be sealed acquisition_complete=True:
    the drain thread must count the failures and seal via abort() instead."""
    from control.core.zarr_writer import ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    calls = []
    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)

    def boom(self, image, t, c, z, fov=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ZarrWriter, "write_frame", boom)
    monkeypatch.setattr(ZarrWriter, "finalize", lambda self: calls.append("finalize"))
    monkeypatch.setattr(ZarrWriter, "abort", lambda self: calls.append("abort"))

    rw = RecordingWriter(_stub_zarr_cfg(tmp_path))
    rw.start()
    rw.enqueue(np.zeros((4, 4), np.uint16), 0, 0, 0)
    rw.enqueue(np.zeros((4, 4), np.uint16), 1, 0, 0)
    rw.finalize()

    assert rw.write_error_count == 2
    assert calls == ["abort"], f"expected incomplete seal via abort(), got {calls}"


def test_recording_writer_finalize_bounded_when_drain_wedged(tmp_path, monkeypatch):
    """finalize() must not block forever pushing the sentinel onto a full queue
    while the drain thread is wedged inside a stalled write."""
    import threading
    from control.core.zarr_writer import ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    release = threading.Event()
    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "write_frame", lambda self, image, t, c, z, fov=None: release.wait(20))
    monkeypatch.setattr(ZarrWriter, "finalize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "abort", lambda self: None)

    rw = RecordingWriter(_stub_zarr_cfg(tmp_path), max_queue=2)
    rw.start()
    # First frame wedges the drain thread inside write_frame; two more fill the queue.
    for i in range(3):
        rw.enqueue(np.zeros((4, 4), np.uint16), i, 0, 0)

    t = threading.Thread(target=lambda: rw.finalize(timeout_s=1.0), daemon=True)
    t.start()
    t.join(timeout=5.0)
    still_stuck = t.is_alive()
    release.set()  # let the drain thread exit before asserting
    assert not still_stuck, "finalize() deadlocked on the full bounded queue"


def test_streaming_capture_abort_wakes_without_frames():
    """Stop/abort must work even when the camera delivers no frames at all:
    run() must poll abort_fn rather than sampling it only in the frame callback."""
    import time as _time

    class _NoFrameSource:
        def start(self, on_frame):
            pass

        def stop(self):
            pass

    w = _RecordingStubWriter()
    cap = StreamingCapture(
        _NoFrameSource(),
        RecordingRouter(fps=10.0),
        CountStop(5),
        w,
        abort_fn=lambda: True,  # user pressed Stop
    )
    t0 = _time.monotonic()
    emitted = cap.run(timeout=10.0)
    took = _time.monotonic() - t0

    assert emitted == 0
    assert took < 2.0, f"abort took {took:.1f}s — run() ignored abort while no frames arrived"
    assert w.aborted is True, "aborted capture must be sealed as aborted"
    assert w.finalized is False, "aborted capture must not be sealed as complete"


def test_recording_router_tolerates_delivery_jitter():
    """F5: with the camera running AT the target rate, ms-level host delivery
    jitter must not reject frames — anchoring to the previous emission made
    every slightly-early frame fail the gate and halved the effective rate."""
    r = RecordingRouter(fps=10.0)
    # 10 fps arrivals with a 1 ms early wobble on every other frame.
    stamps = [100.0 + i * 0.1 - (0.001 if i % 2 else 0.0) for i in range(10)]
    accepted = [s for s in stamps if r.route(s) is not None]
    assert len(accepted) == 10, f"only {len(accepted)}/10 at-rate frames accepted (jitter rejected frames)"


def test_recording_router_still_downsamples_faster_camera():
    """The jitter fix must not break downsampling: a camera at 2x the target
    rate should still have roughly half its frames rejected."""
    r = RecordingRouter(fps=10.0)
    stamps = [100.0 + i * 0.05 for i in range(20)]  # 20 fps camera, 10 fps target
    accepted = [s for s in stamps if r.route(s) is not None]
    assert len(accepted) == 10, f"expected 10/20 accepted, got {len(accepted)}"


def test_recording_writer_byte_bound_drops(tmp_path, monkeypatch):
    """F7: the queue must bound MEMORY, not just frame count — 256 full-res
    16-bit frames is ~13 GB.  Frames beyond max_bytes drop like a full queue."""
    import threading
    from control.core.zarr_writer import ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    release = threading.Event()
    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "write_frame", lambda self, image, t, c, z, fov=None: release.wait(20))
    monkeypatch.setattr(ZarrWriter, "finalize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "abort", lambda self: None)

    frame = np.zeros((100, 100), np.uint16)  # 20 kB each
    rw = RecordingWriter(_stub_zarr_cfg(tmp_path), max_queue=256, max_bytes=50_000)
    rw.start()
    for i in range(6):  # 120 kB total >> 50 kB cap; first frame wedges in write
        rw.enqueue(frame, i, 0, 0)
    dropped = rw.dropped_count
    release.set()
    rw.finalize(timeout_s=2.0)
    assert dropped >= 3, f"byte cap not enforced: only {dropped} frames dropped"


def test_dropped_frames_seal_store_incomplete(tmp_path, monkeypatch):
    """Round-2: frames dropped by backpressure leave fill-value holes, so the
    store must NOT be sealed acquisition_complete=True (CountStop still counts
    routed frames, so the completeness attribute was lying)."""
    import threading
    from control.core.zarr_writer import ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    release = threading.Event()
    calls = []
    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "write_frame", lambda self, image, t, c, z, fov=None: release.wait(10))
    monkeypatch.setattr(ZarrWriter, "finalize", lambda self: calls.append("finalize"))
    monkeypatch.setattr(ZarrWriter, "abort", lambda self: calls.append("abort"))

    rw = RecordingWriter(_stub_zarr_cfg(tmp_path), max_queue=1)
    rw.start()
    for i in range(4):  # first wedges in write, second queues, rest drop
        rw.enqueue(np.zeros((4, 4), np.uint16), i, 0, 0)
    assert rw.dropped_count > 0
    release.set()
    rw.finalize(timeout_s=5.0)

    assert calls == ["abort"], f"store with dropped frames sealed via {calls}, expected incomplete seal"
    assert rw.finalize_wedged is False


def test_finalize_wedged_flag_feeds_fail_fast(tmp_path, monkeypatch):
    """Round-2: when finalize() takes the wedged-drain fallback it returns with
    the drain thread still running — the caller's write_error_count check reads
    0 and the fail-fast never fires.  The writer must expose the wedged state."""
    import threading
    from control.core.zarr_writer import ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    release = threading.Event()
    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "write_frame", lambda self, image, t, c, z, fov=None: release.wait(20))
    monkeypatch.setattr(ZarrWriter, "finalize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "abort", lambda self: None)

    rw = RecordingWriter(_stub_zarr_cfg(tmp_path), max_queue=2)
    rw.start()
    for i in range(3):
        rw.enqueue(np.zeros((4, 4), np.uint16), i, 0, 0)
    rw.finalize(timeout_s=1.0)  # sentinel can't be queued -> wedged fallback
    still_wedged = rw.finalize_wedged
    release.set()
    assert still_wedged is True


def test_finalize_total_time_respects_timeout_budget(tmp_path, monkeypatch):
    """Round-2: the sentinel put and the join must share ONE timeout budget —
    a late-accepted sentinel followed by a full-length join blocked callers
    for up to ~2x timeout_s."""
    import time as _time
    from control.core.zarr_writer import ZarrWriter
    from control.core.streaming_capture import RecordingWriter

    monkeypatch.setattr(ZarrWriter, "initialize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "write_frame", lambda self, image, t, c, z, fov=None: _time.sleep(0.95))
    monkeypatch.setattr(ZarrWriter, "finalize", lambda self: None)
    monkeypatch.setattr(ZarrWriter, "abort", lambda self: None)

    rw = RecordingWriter(_stub_zarr_cfg(tmp_path), max_queue=2)
    rw.start()
    # f0 wedges the drain mid-write; f1+f2 then fill the queue, so the sentinel
    # is accepted LATE (~0.75s into the 1.0s put deadline) — the join must use
    # only the remaining budget, not a fresh full timeout.
    rw.enqueue(np.zeros((4, 4), np.uint16), 0, 0, 0)
    _time.sleep(0.2)  # let the drain thread take f0 and start the slow write
    rw.enqueue(np.zeros((4, 4), np.uint16), 1, 0, 0)
    rw.enqueue(np.zeros((4, 4), np.uint16), 2, 0, 0)
    t0 = _time.monotonic()
    rw.finalize(timeout_s=1.0)
    took = _time.monotonic() - t0
    assert took < 1.5, f"finalize(timeout_s=1.0) blocked {took:.1f}s — put+join must share one budget"
