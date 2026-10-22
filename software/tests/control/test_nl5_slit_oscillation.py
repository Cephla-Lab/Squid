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
    stage.start_oscillation(30, 20, 2.5)
    time.sleep(0.7)
    stage.stop_oscillation()

    sent = moves(stage.ser.commands())
    *oscillation, back = sent
    assert len(oscillation) >= 3
    # alternates +, -, +, ... and holds each end for two half periods (400 ms)
    assert [t for t, _ in oscillation] == [centre + 30 if i % 2 == 0 else centre - 20 for i in range(len(oscillation))]
    assert all(hold == 400 for _, hold in oscillation)
    # stopping returns the slit to its centre with the normal hold
    assert back == (centre, RCMInternalStage.DEFAULT_HOLD_MS)
    assert not stage.is_oscillating()


def test_moves_are_half_a_period_apart(stage):
    stage.start_oscillation(30, 30, 2.5)
    time.sleep(0.7)
    stage.stop_oscillation()

    times = [t for t, c in stage.ser.writes][:-1]  # without the move back to the centre
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(gaps) >= 3
    # timed against absolute deadlines, so the moves do not drift even if one is late
    assert (times[-1] - times[0]) / len(gaps) == pytest.approx(0.2, abs=0.02), gaps
    assert all(g == pytest.approx(0.2, abs=0.05) for g in gaps), gaps


def test_no_moves_after_stop(stage):
    stage.start_oscillation(30, 30, 2.5)
    time.sleep(0.25)
    stage.stop_oscillation()
    count = len(stage.ser.writes)
    time.sleep(0.45)
    assert len(stage.ser.writes) == count


def test_changing_slit_or_offset_moves_the_oscillation_without_stopping_it(stage):
    stage.start_oscillation(30, 30, 2.5)
    time.sleep(0.25)
    stage.set_slit_width(88)
    stage.set_offset(-5)
    stage.ser.clear()
    time.sleep(0.45)
    sent = moves(stage.ser.commands())
    stage.stop_oscillation()

    new_centre = SLIT_POSITIONS_UM[88] - 5
    assert not stage.is_oscillating()
    assert sent and {t for t, _ in sent} <= {new_centre + 30, new_centre - 30}


def test_start_again_changes_parameters_without_restarting_the_cycle(stage):
    centre = SLIT_POSITIONS_UM[44] + 10
    stage.start_oscillation(30, 30, 2.5)
    thread = stage._oscillation_thread
    time.sleep(0.1)  # first move (+) sent, the next (-) is due at 200 ms
    stage.start_oscillation(100, 40, 2.0)
    assert stage._oscillation_thread is thread
    time.sleep(0.6)
    stage.stop_oscillation()

    *oscillation, back = moves(stage.ser.commands())
    # the cycle carries on from - with the new positions and hold (500 ms at 2 Hz)
    assert oscillation[:3] == [(centre + 30, 400), (centre - 40, 500), (centre + 100, 500)]
    assert back == (centre, RCMInternalStage.DEFAULT_HOLD_MS)


def test_home_pauses_and_resumes_the_oscillation(stage, monkeypatch):
    monkeypatch.setattr(RCMInternalStage, "HOMING_WAIT_S", 0.1)
    stage.start_oscillation(30, 30, 2.5)
    time.sleep(0.25)
    stage.ser.clear()
    stage.home()
    assert stage.is_oscillating()
    time.sleep(0.3)
    stage.stop_oscillation()
    commands = stage.ser.commands()
    homed = commands.index(":MTR0Z1")
    assert commands[commands.index(":CS0") : homed + 1] == [":CS0", ":MTR0Z1"]  # nothing sent while homing
    # back to the centre, then oscillating again
    # pausing for homing sends no move back to the centre first
    assert RCMInternalStage.DEFAULT_HOLD_MS not in [hold for _, hold in moves(commands[: commands.index(":CS0")])]
    holds = [hold for _, hold in moves(commands[homed:])]
    assert holds[0] == RCMInternalStage.DEFAULT_HOLD_MS
    assert holds[1:-1] and set(holds[1:-1]) == {400}


@pytest.mark.parametrize(
    "plus, minus, freq",
    [
        (-1, 10, 1.0),
        (10, -1, 1.0),
        (NL5.SLIT_OSCILLATION_MAX_UM + 1, 10, 1.0),
        (10, NL5.SLIT_OSCILLATION_MAX_UM + 1, 1.0),
        (10, 10, 0.0),
        (10, 10, NL5.SLIT_OSCILLATION_MAX_HZ + 1),
    ],
)
def test_rejects_invalid_parameters(stage, plus, minus, freq):
    with pytest.raises(ValueError):
        stage.start_oscillation(plus, minus, freq)
    assert not stage.is_oscillating()
    assert stage.ser.commands() == []


def test_invalid_change_keeps_the_running_oscillation(stage):
    stage.start_oscillation(30, 30, 2.5)
    with pytest.raises(ValueError):
        stage.start_oscillation(30, 30, NL5.SLIT_OSCILLATION_MAX_HZ + 1)
    assert stage.is_oscillating()
    assert stage.oscillation == (30, 30, 2.5)


def test_serial_error_ends_the_oscillation_and_slit_moves_work_again(stage, caplog):
    def unplugged(data):
        raise OSError("device disconnected")

    stage.ser.write = unplugged
    stage.start_oscillation(30, 30, 2.5)
    stage._oscillation_thread.join(1)
    assert not stage.is_oscillating()
    assert "Slit oscillation stopped by an error" in caplog.text

    del stage.ser.write  # plugged back in
    stage.set_slit_width(88)
    assert moves(stage.ser.commands()) == [(SLIT_POSITIONS_UM[88] + 10, RCMInternalStage.DEFAULT_HOLD_MS)]
    stage.start_oscillation(30, 30, 2.5)  # and it can be started again
    assert stage.is_oscillating()


def test_close_closes_the_port_even_if_the_slit_cannot_be_centred(stage):
    def unplugged(data):
        raise OSError("device disconnected")

    stage.start_oscillation(30, 30, 2.5)
    stage.ser.write = unplugged
    stage._oscillation_thread.join(1)
    with pytest.raises(OSError):
        stage.close()
    assert not stage.ser.is_open


def test_close_stops_the_oscillation(stage):
    stage.start_oscillation(30, 30, 2.5)
    stage.close()
    assert not stage.is_oscillating()
    assert not stage.ser.is_open


class FakeNL5(NL5.NL5_Simulation):
    """Simulation with the slit controls shown, recording oscillation calls."""

    def __init__(self, fail_start=False):
        super().__init__()
        self.internal_stage = object()
        self.slit_width_um = 44
        self.calls = []
        self.oscillating = False
        self.fail_start = fail_start

    def set_slit_width(self, width):
        self.calls.append(("width", width))

    def start_slit_oscillation(self, plus, minus, freq):
        if self.fail_start:
            raise RuntimeError("serial port gone")
        self.calls.append(("start", plus, minus, freq))
        self.oscillating = True

    def stop_slit_oscillation(self):
        self.calls.append(("stop",))
        self.oscillating = False

    def is_slit_oscillating(self):
        return self.oscillating


def test_widget_starts_updates_and_stops_the_oscillation(qtbot):
    from control.NL5Widget import NL5Widget

    nl5 = FakeNL5()
    widget = NL5Widget(nl5)
    qtbot.addWidget(widget)

    widget.slit_osc_minus_input.setValue(25)  # not running: nothing sent
    widget.slit_osc_button.setChecked(True)
    assert widget.slit_osc_sync_timer.isActive()
    widget.slit_osc_freq_input.setValue(2.5)  # running: new frequency sent
    widget.slit_osc_button.setChecked(False)
    assert nl5.calls == [("start", 50, 25, 1.0), ("start", 50, 25, 2.5), ("stop",)]
    assert not widget.slit_osc_sync_timer.isActive()


def test_widget_unchecks_when_the_oscillation_fails_to_start(qtbot):
    from control.NL5Widget import NL5Widget

    widget = NL5Widget(FakeNL5(fail_start=True))
    qtbot.addWidget(widget)

    widget.slit_osc_button.setChecked(True)
    assert not widget.slit_osc_button.isChecked()


def test_widget_unchecks_when_the_oscillation_ends_on_its_own(qtbot):
    from control.NL5Widget import NL5Widget

    nl5 = FakeNL5()
    widget = NL5Widget(nl5)
    qtbot.addWidget(widget)
    widget.slit_osc_sync_timer.setInterval(10)

    widget.slit_osc_button.setChecked(True)
    nl5.oscillating = False  # e.g. the serial port went away
    qtbot.waitUntil(lambda: not widget.slit_osc_button.isChecked(), timeout=1000)
    assert nl5.calls == [("start", 50, 50, 1.0), ("stop",)]  # the sync puts the slit back at its centre
