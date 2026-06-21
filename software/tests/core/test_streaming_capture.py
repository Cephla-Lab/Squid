from control.core.streaming_capture import CountStop, RecordingRouter


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
