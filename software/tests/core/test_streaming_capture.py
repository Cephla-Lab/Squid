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
