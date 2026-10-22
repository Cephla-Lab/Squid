"""Slit oscillation (jump and sweep) on the AION internal (slit) stage, against a fake SmarAct SCU serial port."""

import threading
import time

import pytest

import control.NL5 as NL5
from control.NL5 import SLIT_POSITIONS_UM, RCMInternalStage, SlitOscillation


class FakeSerial:
    """Records every command written, with the time it was written, and answers GP0 like the SCU.

    The stage reaches each MPA target `arrive_after_s` after the command (never, if None).
    """

    def __init__(self, write_timeout=None, **kwargs):
        self.is_open = True
        self.dtr = True
        self.writes = []
        self._lock = threading.Lock()
        self.write_timeout = write_timeout
        self.unstalled = threading.Event()
        self.unstalled.set()
        self.arrive_after_s = 0.0
        self._position = 0.0
        self._target = 0.0
        self._arrives_at = 0.0
        self._asked = False

    def reset_input_buffer(self):
        self._asked = False

    def write(self, data):
        # like pyserial: a stalled port blocks the write until write_timeout (forever without one), then raises
        if not self.unstalled.wait(self.write_timeout):
            raise NL5.serial.SerialTimeoutException("Write timeout")
        command = data.decode("ascii").strip()
        now = time.perf_counter()
        with self._lock:
            self.writes.append((now, command))
            self._settle(now)
            if command.startswith(":MPA0P"):
                self._target = float(command[len(":MPA0P") :].split("H")[0])
                self._arrives_at = float("inf") if self.arrive_after_s is None else now + self.arrive_after_s
            elif command == ":GP0":
                self._asked = True

    def _settle(self, now):
        if now >= self._arrives_at:
            self._position = self._target

    def readline(self):
        with self._lock:
            if not self._asked:
                return b""
            self._asked = False
            self._settle(time.perf_counter())
            return f":P0P{self._position:.1f}".encode("ascii") + b"\n"

    def close(self):
        self.is_open = False

    def commands(self):
        with self._lock:
            return [c for _, c in self.writes]

    def clear(self):
        with self._lock:
            self.writes.clear()


def jump(plus, minus, frequency_hz):
    return SlitOscillation(plus_um=plus, minus_um=minus, mode="jump", frequency_hz=frequency_hz)


def sweep(plus, minus, speed_um_s):
    return SlitOscillation(plus_um=plus, minus_um=minus, mode="sweep", speed_um_s=speed_um_s)


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
    stage.start_oscillation(jump(30, 20, 2.5))
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
    stage.start_oscillation(jump(30, 30, 2.5))
    time.sleep(0.7)
    stage.stop_oscillation()

    times = [t for t, c in stage.ser.writes][:-1]  # without the move back to the centre
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(gaps) >= 3
    # timed against absolute deadlines, so the moves do not drift even if one is late
    assert (times[-1] - times[0]) / len(gaps) == pytest.approx(0.2, abs=0.02), gaps
    assert all(g == pytest.approx(0.2, abs=0.05) for g in gaps), gaps


def test_no_moves_after_stop(stage):
    stage.start_oscillation(jump(30, 30, 2.5))
    time.sleep(0.25)
    stage.stop_oscillation()
    count = len(stage.ser.writes)
    time.sleep(0.45)
    assert len(stage.ser.writes) == count


def test_changing_slit_or_offset_moves_the_oscillation_without_stopping_it(stage):
    stage.start_oscillation(jump(30, 30, 2.5))
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
    stage.start_oscillation(jump(30, 30, 2.5))
    thread = stage._oscillation_thread
    time.sleep(0.1)  # first move (+) sent, the next (-) is due at 200 ms
    stage.start_oscillation(jump(100, 40, 2.0))
    assert stage._oscillation_thread is thread
    time.sleep(0.6)
    stage.stop_oscillation()

    *oscillation, back = moves(stage.ser.commands())
    # the cycle carries on from - with the new positions and hold (500 ms at 2 Hz)
    assert oscillation[:3] == [(centre + 30, 400), (centre - 40, 500), (centre + 100, 500)]
    assert back == (centre, RCMInternalStage.DEFAULT_HOLD_MS)


def test_home_pauses_and_resumes_the_oscillation(stage, monkeypatch):
    monkeypatch.setattr(RCMInternalStage, "HOMING_WAIT_S", 0.1)
    stage.start_oscillation(jump(30, 30, 2.5))
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
    "oscillation",
    [
        jump(-1, 10, 1.0),
        jump(10, -1, 1.0),
        jump(NL5.SLIT_OSCILLATION_MAX_UM + 1, 10, 1.0),
        jump(10, NL5.SLIT_OSCILLATION_MAX_UM + 1, 1.0),
        jump(10, 10, 0.0),
        jump(10, 10, NL5.SLIT_OSCILLATION_MAX_HZ + 1),
        sweep(10, 10, NL5.SLIT_SWEEP_MIN_UM_S - 1),
        sweep(10, 10, NL5.SLIT_SWEEP_MAX_UM_S + 1),
        SlitOscillation(mode="wobble"),
    ],
)
def test_rejects_invalid_parameters(stage, oscillation):
    with pytest.raises(ValueError):
        stage.start_oscillation(oscillation)
    assert not stage.is_oscillating()
    assert stage.ser.commands() == []


def test_invalid_change_keeps_the_running_oscillation(stage):
    stage.start_oscillation(jump(30, 30, 2.5))
    with pytest.raises(ValueError):
        stage.start_oscillation(jump(30, 30, NL5.SLIT_OSCILLATION_MAX_HZ + 1))
    assert stage.is_oscillating()
    assert stage.oscillation == jump(30, 30, 2.5)


def test_serial_error_ends_the_oscillation_and_slit_moves_work_again(stage, caplog):
    def unplugged(data):
        raise OSError("device disconnected")

    stage.ser.write = unplugged
    stage.start_oscillation(jump(30, 30, 2.5))
    stage._oscillation_thread.join(1)
    assert not stage.is_oscillating()
    assert "Slit oscillation stopped by an error" in caplog.text

    del stage.ser.write  # plugged back in
    stage.set_slit_width(88)
    assert moves(stage.ser.commands()) == [(SLIT_POSITIONS_UM[88] + 10, RCMInternalStage.DEFAULT_HOLD_MS)]
    stage.start_oscillation(jump(30, 30, 2.5))  # and it can be started again
    assert stage.is_oscillating()


def test_stop_returns_when_the_port_stalls(stage):
    stage.ser.write_timeout = 0.2  # opened with RCMInternalStage.WRITE_TIMEOUT_S; shortened for the test
    stage.start_oscillation(jump(30, 30, 2.5))
    stage.ser.unstalled.clear()  # the USB-serial adapter stops taking data

    result = {}

    def stop():
        try:
            stage.stop_oscillation()
        except Exception as e:
            result["error"] = e

    stopper = threading.Thread(target=stop, daemon=True)
    stopper.start()
    stopper.join(5)
    assert not stopper.is_alive(), "stop_oscillation hung on the stalled port"
    # the oscillation thread ended on the timeout, then the move back to the centre timed out too
    assert not stage.is_oscillating()
    assert isinstance(result.get("error"), NL5.serial.SerialTimeoutException)
    stage.ser.unstalled.set()


def test_the_port_is_opened_with_a_write_timeout(stage):
    assert stage.ser.write_timeout == RCMInternalStage.WRITE_TIMEOUT_S


def test_close_closes_the_port_even_if_the_slit_cannot_be_centred(stage):
    def unplugged(data):
        raise OSError("device disconnected")

    stage.start_oscillation(jump(30, 30, 2.5))
    stage.ser.write = unplugged
    stage._oscillation_thread.join(1)
    with pytest.raises(OSError):
        stage.close()
    assert not stage.ser.is_open


def test_close_stops_the_oscillation(stage):
    stage.start_oscillation(jump(30, 30, 2.5))
    stage.close()
    assert not stage.is_oscillating()
    assert not stage.ser.is_open


def mpa_moves(writes):
    """(time, target, command) for every MPA write."""
    return [(t, int(c[len(":MPA0P") :].split("H")[0]), c) for t, c in writes if c.startswith(":MPA0P")]


def test_sweep_travels_at_the_set_speed_and_turns_round_at_each_end(stage):
    centre = SLIT_POSITIONS_UM[44] + 10
    stage.start_oscillation(sweep(30, 20, 200))
    time.sleep(0.6)
    stage.stop_oscillation()

    writes = list(stage.ser.writes)
    *sent, back = mpa_moves(writes)
    targets = [target - centre for _, target, _ in sent]
    assert all(c.endswith(f"H{RCMInternalStage.SWEEP_HOLD_MS}") for _, _, c in sent)
    top = targets.index(30)
    bottom = targets.index(-20)
    assert 0 < top < bottom
    assert targets[: top + 1] == sorted(targets[: top + 1])  # up from the centre ...
    assert targets[top : bottom + 1] == sorted(targets[top : bottom + 1], reverse=True)  # ... then down
    # 30 µm at 200 µm/s takes 150 ms, with a setpoint every 20 ms (~4 µm)
    assert sent[top][0] - sent[0][0] == pytest.approx(0.15, abs=0.08)
    assert max(b - a for a, b in zip(targets, targets[1 : top + 1])) <= 12
    # at the end it reads the position back before turning round
    assert ":GP0" in [c for t, c in writes if sent[top][0] <= t <= sent[top + 1][0]]
    assert back[2] == f":MPA0P{centre}H{RCMInternalStage.DEFAULT_HOLD_MS}"


def test_sweep_waits_at_the_end_until_the_stage_is_there(stage):
    centre = SLIT_POSITIONS_UM[44] + 10
    stage.ser.arrive_after_s = 0.15
    stage.start_oscillation(sweep(10, 10, 500))
    time.sleep(0.5)
    stage.stop_oscillation()

    sent = mpa_moves(stage.ser.writes)
    top = next(i for i, (_, target, _) in enumerate(sent) if target == centre + 10)
    # turned round only once the stage reported the end, 150 ms after it was sent there
    assert sent[top + 1][0] - sent[top][0] >= 0.15


def test_sweep_turns_round_after_a_timeout_if_the_stage_never_gets_there(stage, monkeypatch, caplog):
    centre = SLIT_POSITIONS_UM[44] + 10
    monkeypatch.setattr(RCMInternalStage, "ARRIVE_TIMEOUT_S", 0.1)
    stage.ser.arrive_after_s = None  # stuck
    stage.start_oscillation(sweep(10, 10, 500))
    time.sleep(0.4)
    stage.stop_oscillation()

    targets = [target - centre for _, target, _ in mpa_moves(stage.ser.writes)]
    assert 10 in targets and -10 in targets  # kept going
    assert "Slit sweep turned round" in caplog.text


def test_sweep_without_a_swing_does_not_flood_the_port(stage):
    stage.start_oscillation(sweep(0, 0, 200))
    time.sleep(0.3)
    stage.stop_oscillation()
    # at most a move and a position read per 20 ms update
    assert len(stage.ser.writes) <= 2 * 0.3 / RCMInternalStage.SWEEP_UPDATE_S + 4


def test_slit_change_during_the_end_wait_goes_to_the_new_end(stage, caplog):
    stage.ser.arrive_after_s = None  # never there, so it waits at the end
    stage.start_oscillation(sweep(10, 10, 500))
    time.sleep(0.1)  # 10 µm at 500 µm/s takes 20 ms: now waiting at +10
    stage.ser.clear()
    stage.set_slit_width(88)
    time.sleep(0.1)
    commands = stage.ser.commands()
    stage.stop_oscillation()
    new_end = SLIT_POSITIONS_UM[88] + 10 + 10
    assert f":MPA0P{new_end}H{RCMInternalStage.SWEEP_HOLD_MS}" in commands


def test_stop_during_the_end_wait_is_prompt(stage):
    stage.ser.arrive_after_s = None
    stage.start_oscillation(sweep(10, 10, 500))
    time.sleep(0.1)
    t0 = time.perf_counter()
    stage.stop_oscillation()
    assert time.perf_counter() - t0 < 0.3


def test_restart_after_a_dead_thread_starts_from_the_centre(stage):
    def unplugged(data):
        raise OSError("device disconnected")

    centre = SLIT_POSITIONS_UM[44] + 10
    stage.start_oscillation(sweep(30, 30, 200))
    time.sleep(0.1)
    stage.ser.write = unplugged
    stage._oscillation_thread.join(1)
    del stage.ser.write  # plugged back in
    stage.ser.clear()

    stage.start_oscillation(sweep(30, 30, 200))
    time.sleep(0.05)
    sent = moves(stage.ser.commands())
    stage.stop_oscillation()
    # back to the centre first, then the sweep starts from there
    assert sent[0] == (centre, RCMInternalStage.DEFAULT_HOLD_MS)
    assert all(abs(target - centre) <= 12 for target, _ in sent[1:3])


def test_updating_the_settings_never_starts_an_oscillation(stage):
    stage.update_oscillation(sweep(30, 30, 200))
    assert not stage.is_oscillating()
    assert stage.ser.commands() == []


def test_mode_can_change_while_it_runs(stage):
    stage.start_oscillation(jump(30, 30, 2.5))
    thread = stage._oscillation_thread
    time.sleep(0.1)
    stage.start_oscillation(sweep(30, 30, 200))
    assert stage._oscillation_thread is thread
    stage.ser.clear()
    time.sleep(0.3)
    holds = [h for _, h in moves(stage.ser.commands())]
    stage.stop_oscillation()
    assert holds and set(holds) == {RCMInternalStage.SWEEP_HOLD_MS}


def test_sweep_frequency_follows_speed_and_swing():
    assert sweep(50, 50, 200).sweep_frequency_hz() == pytest.approx(1.0)
    assert sweep(30, 20, 100).sweep_frequency_hz() == pytest.approx(1.0)
    assert sweep(0, 0, 100).sweep_frequency_hz() == 0.0


def test_nl5_keeps_the_settings_and_updates_a_running_oscillation(stage):
    nl5 = NL5.NL5.__new__(NL5.NL5)  # without the RCM
    nl5.internal_stage = stage
    assert nl5.slit_oscillation == SlitOscillation()

    nl5.set_slit_oscillation(jump(30, 30, 2.5))  # not running: kept for the next start
    assert not stage.is_oscillating()
    nl5.start_slit_oscillation()
    assert stage.oscillation == jump(30, 30, 2.5)

    nl5.set_slit_oscillation(sweep(40, 10, 100))  # running: applied
    assert stage.oscillation == sweep(40, 10, 100)
    with pytest.raises(ValueError):
        nl5.set_slit_oscillation(sweep(40, 10, 0))
    assert nl5.slit_oscillation == sweep(40, 10, 100)
    nl5.stop_slit_oscillation()


class FakeNL5(NL5.NL5_Simulation):
    """Simulation with the slit controls shown, recording oscillation calls."""

    def __init__(self, fail_start=False):
        super().__init__()
        self.internal_stage = object()
        self.slit_width_um = 44
        self.calls = []
        self.oscillating = False
        self.fail_start = fail_start

    def save_settings(self):
        pass  # keep NL5_settings.json out of the test

    def home_internal_stage(self):
        self.calls.append(("home",))

    def set_slit_width(self, width):
        self.calls.append(("width", width))

    def set_slit_offset(self, offset_um):
        self.slit_offset_um = offset_um

    def start_slit_oscillation(self):
        if self.fail_start:
            raise RuntimeError("serial port gone")
        self.calls.append(("start", self.slit_oscillation))
        self.oscillating = True

    def stop_slit_oscillation(self):
        self.calls.append(("stop",))
        self.oscillating = False

    def is_slit_oscillating(self):
        return self.oscillating


def test_widget_toggle_starts_and_stops_the_oscillation(qtbot):
    from control.NL5Widget import NL5Widget

    nl5 = FakeNL5()
    widget = NL5Widget(nl5)
    qtbot.addWidget(widget)
    assert not hasattr(widget, "slit_osc_plus_input")  # the settings are in the dialog

    widget.slit_osc_button.setChecked(True)
    assert widget.slit_osc_sync_timer.isActive()
    widget.slit_osc_button.setChecked(False)
    assert nl5.calls == [("start", SlitOscillation()), ("stop",)]
    assert not widget.slit_osc_sync_timer.isActive()


def test_dialog_edits_the_oscillation_settings(qtbot):
    from control.NL5Widget import NL5SettingsDialog

    nl5 = FakeNL5()
    dialog = NL5SettingsDialog(nl5)
    qtbot.addWidget(dialog)

    # jump by default: frequency on, speed off
    assert dialog.slit_osc_freq_input.isEnabled() and not dialog.slit_osc_speed_input.isEnabled()
    dialog.slit_osc_plus_input.setValue(40)
    dialog.slit_osc_minus_input.setValue(10)
    dialog.slit_osc_mode_combo.setCurrentIndex(dialog.slit_osc_mode_combo.findData("sweep"))
    dialog.slit_osc_speed_input.setValue(150)
    assert dialog.slit_osc_speed_input.isEnabled() and not dialog.slit_osc_freq_input.isEnabled()
    assert "1.50 Hz" in dialog.slit_osc_sweep_hz_label.text()  # 150 µm/s over 2 x 50 µm

    dialog.accept()
    assert nl5.slit_oscillation == SlitOscillation(plus_um=40, minus_um=10, mode="sweep", speed_um_s=150)
    assert nl5.calls == []  # not running: nothing started


def test_dialog_has_no_oscillation_rows_without_the_slit_stage(qtbot):
    from control.NL5Widget import NL5SettingsDialog

    nl5 = FakeNL5()
    nl5.internal_stage = None
    dialog = NL5SettingsDialog(nl5)
    qtbot.addWidget(dialog)
    assert not hasattr(dialog, "slit_osc_plus_input")
    dialog.accept()
    assert nl5.slit_oscillation == SlitOscillation()


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
    assert nl5.calls == [("start", SlitOscillation()), ("stop",)]  # the sync puts the slit back at its centre
