import threading

from control.core.fluidics_protocol.sensor_recorder import SensorRecorder, SensorSeries


def test_series_window_trims_by_seconds():
    series = SensorSeries()
    for i in range(10):
        series.append(float(i), t=100.0 + i)
    ts, vs = series.window()
    assert len(ts) == 10 and vs[0] == 0.0
    ts, vs = series.window(seconds=3)
    assert ts == [106.0, 107.0, 108.0, 109.0] and vs == [6.0, 7.0, 8.0, 9.0]


def test_recorder_writes_csv_only_while_recording(tmp_path):
    recorder = SensorRecorder()
    recorder.record("channel_1", 20.0, t=1.0)
    assert not recorder.recording

    path = tmp_path / "t.csv"
    assert recorder.start_recording(str(path))
    recorder.set_step_label("R01 hyb")
    recorder.record("channel_1", 21.0, t=2.0)
    recorder.stop_recording()
    recorder.record("channel_1", 22.0, t=3.0)

    lines = path.read_text().strip().splitlines()
    assert lines[0] == "time,channel,value,step"
    assert lines[1] == "2.000,channel_1,21.0,R01 hyb"
    assert len(lines) == 2
    assert len(recorder.channel("channel_1").window()[0]) == 3


def test_recorder_is_thread_safe():
    recorder = SensorRecorder()

    def pump(name):
        for i in range(1000):
            recorder.record(name, float(i))

    threads = [threading.Thread(target=pump, args=(f"c{k}",)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(recorder.channel("c0").window()[0]) == 1000
    assert len(recorder.channel("c1").window()[0]) == 1000
