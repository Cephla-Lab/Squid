import pytest

pytest.importorskip("fluidics")

from control.core.fluidics_protocol.sensor_recorder import SensorRecorder


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
