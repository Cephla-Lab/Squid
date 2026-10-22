"""Slit oscillation on the AION internal (slit) stage, against a fake SmarAct SCU serial port."""

import threading
import time

import pytest

import control.NL5 as NL5
from control.NL5 import SLIT_POSITIONS_UM, RCMInternalStage


class FakeSerial:
    """Records every command written, with the time it was written."""

    def __init__(self, **kwargs):
        self.is_open = True
        self.dtr = True
        self.writes = []
        self._lock = threading.Lock()

    def reset_input_buffer(self):
        pass

    def write(self, data):
        with self._lock:
            self.writes.append((time.perf_counter(), data.decode("ascii").strip()))

    def close(self):
        self.is_open = False

    def commands(self):
        with self._lock:
            return [c for _, c in self.writes]

    def clear(self):
        with self._lock:
            self.writes.clear()


@pytest.fixture
def stage(monkeypatch):
    monkeypatch.setattr(NL5.serial, "Serial", FakeSerial)
    monkeypatch.setattr(RCMInternalStage, "_find_port", staticmethod(lambda sn: "COM_FAKE"))
    monkeypatch.setattr(RCMInternalStage, "HOMING_WAIT_S", 0)
    monkeypatch.setattr(RCMInternalStage, "REFERENCE_MOVE_WAIT_S", 0)
    monkeypatch.setattr(RCMInternalStage, "COMMAND_GAP_S", 0)
    s = RCMInternalStage("FAKE_SN")
    s.set_offset(10)
    s.set_slit_width(44)
    s.ser.clear()
    yield s
    s.close()


def moves(commands):
    """(target, hold_ms) for every MPA command."""
    out = []
    for c in commands:
        if c.startswith(":MPA0P"):
            target, hold = c[len(":MPA0P") :].split("H")
            out.append((int(target), int(hold)))
    return out


def test_oscillates_between_plus_and_minus_around_the_slit_centre(stage):
    centre = SLIT_POSITIONS_UM[44] + 10
    stage.start_oscillation(30, 20, 5.0)
    time.sleep(0.45)
    stage.stop_oscillation()

    sent = moves(stage.ser.commands())
    *oscillation, back = sent
    assert len(oscillation) >= 5
    # alternates +, -, +, ... and holds each end for half a period (100 ms)
    assert [t for t, _ in oscillation] == [centre + 30 if i % 2 == 0 else centre - 20 for i in range(len(oscillation))]
    assert all(hold == 100 for _, hold in oscillation)
    # stopping returns the slit to its centre with the normal hold
    assert back == (centre, RCMInternalStage.DEFAULT_HOLD_MS)
    assert stage.oscillation is None


def test_moves_are_half_a_period_apart(stage):
    stage.start_oscillation(30, 30, 5.0)
    time.sleep(0.45)
    stage.stop_oscillation()

    times = [t for t, c in stage.ser.writes][:-1]  # without the move back to the centre
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(gaps) >= 3
    assert all(g == pytest.approx(0.1, abs=0.004) for g in gaps), gaps


def test_no_moves_after_stop(stage):
    stage.start_oscillation(30, 30, 5.0)
    time.sleep(0.15)
    stage.stop_oscillation()
    count = len(stage.ser.writes)
    time.sleep(0.25)
    assert len(stage.ser.writes) == count


def test_changing_slit_or_offset_moves_the_oscillation_without_stopping_it(stage):
    stage.start_oscillation(30, 30, 5.0)
    time.sleep(0.15)
    stage.set_slit_width(88)
    stage.set_offset(-5)
    stage.ser.clear()
    time.sleep(0.25)
    sent = moves(stage.ser.commands())
    stage.stop_oscillation()

    new_centre = SLIT_POSITIONS_UM[88] - 5
    assert stage.oscillation is None
    assert sent and {t for t, _ in sent} <= {new_centre + 30, new_centre - 30}


def test_start_again_changes_parameters(stage):
    stage.start_oscillation(30, 30, 5.0)
    time.sleep(0.15)
    stage.start_oscillation(100, 0, 5.0)
    stage.ser.clear()
    time.sleep(0.25)
    sent = moves(stage.ser.commands())
    stage.stop_oscillation()

    centre = SLIT_POSITIONS_UM[44] + 10
    assert sent and {t for t, _ in sent} <= {centre + 100, centre}
    assert stage._oscillation_thread is None


def test_home_pauses_and_resumes_the_oscillation(stage, monkeypatch):
    monkeypatch.setattr(RCMInternalStage, "HOMING_WAIT_S", 0.1)
    stage.start_oscillation(30, 30, 5.0)
    time.sleep(0.15)
    stage.ser.clear()
    stage.home()
    assert stage.oscillation == (30, 30, 5.0)
    time.sleep(0.15)
    commands = stage.ser.commands()
    homed = commands.index(":MTR0Z1")
    assert commands[commands.index(":CS0") : homed + 1] == [":CS0", ":MTR0Z1"]  # nothing sent while homing
    # back to the centre, then oscillating again
    assert [hold for _, hold in moves(commands[homed:])] == [RCMInternalStage.DEFAULT_HOLD_MS, 100, 100]


@pytest.mark.parametrize(
    "plus, minus, freq",
    [(-1, 10, 1.0), (10, -1, 1.0), (10, 10, 0.0), (10, 10, NL5.SLIT_OSCILLATION_MAX_HZ + 1)],
)
def test_rejects_invalid_parameters(stage, plus, minus, freq):
    with pytest.raises(ValueError):
        stage.start_oscillation(plus, minus, freq)
    assert stage.oscillation is None
    assert stage.ser.commands() == []


def test_serial_error_ends_the_oscillation_thread(stage, caplog):
    def unplugged(data):
        raise OSError("device disconnected")

    stage.ser.write = unplugged
    stage.start_oscillation(30, 30, 5.0)
    stage._oscillation_thread.join(1)
    assert not stage._oscillation_thread.is_alive()
    assert "Slit oscillation stopped by an error" in caplog.text
    del stage.ser.write  # plugged back in, so the fixture can close the stage


def test_close_stops_the_oscillation(stage):
    stage.start_oscillation(30, 30, 5.0)
    stage.close()
    assert stage.oscillation is None
    assert not stage.ser.is_open


def test_widget_starts_restarts_and_stops_the_oscillation(qtbot):
    from control.NL5Widget import NL5Widget

    calls = []
    nl5 = NL5.NL5_Simulation()
    nl5.internal_stage = object()  # show the slit controls
    nl5.slit_width_um = 44
    nl5.set_slit_width = lambda width: calls.append(("width", width))
    nl5.start_slit_oscillation = lambda plus, minus, freq: calls.append(("start", plus, minus, freq))
    nl5.stop_slit_oscillation = lambda: calls.append(("stop",))
    widget = NL5Widget(nl5)
    qtbot.addWidget(widget)

    widget.slit_osc_minus_input.setValue(25)  # not running: nothing sent
    widget.slit_osc_button.setChecked(True)
    widget.slit_osc_freq_input.setValue(5)  # running: restarted with the new frequency
    widget.slit_osc_button.setChecked(False)
    assert calls == [("start", 50, 25, 1.0), ("start", 50, 25, 5.0), ("stop",)]


def test_widget_unchecks_when_the_oscillation_fails(qtbot):
    from control.NL5Widget import NL5Widget

    def fail(*args):
        raise RuntimeError("serial port gone")

    nl5 = NL5.NL5_Simulation()
    nl5.internal_stage = object()
    nl5.slit_width_um = 44
    nl5.start_slit_oscillation = fail
    widget = NL5Widget(nl5)
    qtbot.addWidget(widget)

    widget.slit_osc_button.setChecked(True)
    assert not widget.slit_osc_button.isChecked()
