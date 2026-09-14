"""The Z encoder tuner's host guard when encoder reporting is OFF.

On the 2240 bench (2026-09-12) a script moved before reporting was on and the guard died with a
TypeError from int(None) instead of guarding. Without an encoder reading the loop-error leg is
blind: with no loop requested it has nothing to watch; with one requested (the tool's loop_on:
ENABLE acknowledged and no DISABLE since, whether or not the firmware has it engaged) it must
refuse to run blind. Byte 18's fault bits arrive in every packet, so a latched fault is still
seen either way.
"""

import importlib.util
import pathlib
import types

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "z_encoder_pid_tuner.py"


@pytest.fixture(scope="module")
def tuner_mod():
    spec = importlib.util.spec_from_file_location("z_encoder_pid_tuner_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# get_encoder_state() with reporting off: the ENC_FLAG bits are held at 0 and there is no reading.
BLIND = {
    "reporting": False,
    "pid_enabled": False,
    "pid_fault": False,
    "pid_zone_hold": False,
    "axis": 0,
    "encoder_pos": None,
    "deviation": None,
    "dev32": None,
}


class FakeMcu:
    def __init__(self, state, fault_axes=()):
        self.state = dict(state)
        self.fault_axes = set(fault_axes)
        self.z_pos = 0
        self.packets_stopped_at = None  # when set, no status packet is newer than this
        self._cmd_id = 0  # the id send_command took last; the tool matches captures/snapshots against it
        self.pid_off_calls = 0
        self.calls = []

    @property
    def _last_successful_read_time(self):
        # like Microcontroller: the arrival time of the last status packet; the stream runs every 10 ms
        import time as _time

        return _time.time() if self.packets_stopped_at is None else self.packets_stopped_at

    def get_encoder_state(self):
        return dict(self.state)

    def pid_fault_axes(self):
        return set(self.fault_axes)

    def pid_fault_cause(self, axis):
        return 0

    def set_encoder_reporting(self, axis, mode):
        self.calls.append(("reporting", axis, mode))

    def set_max_velocity_acceleration(self, axis, vel, accel):
        self.calls.append(("velocity", axis))

    def turn_off_stage_pid(self, axis):
        self.pid_off_calls += 1

    def wait_till_operation_is_completed(self, timeout=None):
        pass

    def close(self):
        self.calls.append(("close",))


def make_tuner(tuner_mod, tmp_path, mcu, loop_on):
    args = types.SimpleNamespace(out=str(tmp_path), max_dev_um=200.0, depth_max=2.0)
    t = tuner_mod.ZTuner(args)
    t.mcu = mcu
    t.loop_on = loop_on
    return t


def test_reporting_off_and_open_loop_is_not_an_error(tuner_mod, tmp_path):
    # No loop requested and no reading: the only leg that can be judged is the depth, and 0 is fine.
    t = make_tuner(tuner_mod, tmp_path, FakeMcu(BLIND), loop_on=False)
    t.guard()


def test_reporting_off_with_a_loop_requested_refuses_to_run_blind(tuner_mod, tmp_path):
    mcu = FakeMcu(BLIND)
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    with pytest.raises(RuntimeError, match="reporting"):
        t.guard()
    assert mcu.pid_off_calls == 1, "the guard opens the loop before it raises, like its other aborts"


def test_reporting_off_still_sees_a_latched_fault(tuner_mod, tmp_path):
    # ENC_FLAG.PID_FAULT is only in the reporting-on layout; byte 18's bit is in every packet.
    mcu = FakeMcu(BLIND, fault_axes={tuner_mod.AXIS.Z})
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    with pytest.raises(RuntimeError, match="PID_FAULT.*explicit recovery"):
        t.guard()
    assert mcu.pid_off_calls == 0, "a latched fault is left for explicit recovery, not DISABLEd away"
    assert t.loop_on is False


# ---------------------------------------------------------------- a latched fault survives the tool
# Post-fault contract (PR 645, 2026-09-14): a fault opens the loop and latches its cause; nothing on the
# host acknowledges it automatically. DISABLE_STAGE_PID clears the latch and the cause with it, so the
# tool's own loop-off - its aborts, its cleanup - must not send it while a fault is latched. Recovery is
# a deliberate relaunch (RESET + INITIALIZE + homing) or a deliberate DISABLE by the operator.


def test_loop_off_preserves_a_latched_fault(tuner_mod, tmp_path):
    mcu = FakeMcu(BLIND, fault_axes={tuner_mod.AXIS.Z})
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    t.loop_off()
    assert mcu.pid_off_calls == 0
    assert t.loop_on is False, "the firmware opened the loop; the tool's own bookkeeping follows it"


class LateFaultMcu(FakeMcu):
    """The fault bits are read from the LAST status packet. The packet a host-side abort acted on was
    emitted before the firmware's own check in that pass (send_position_update runs before
    check_closed_loop), so a watchdog trip can be ~one packet behind the deviation the host saw: this
    fake's fault bit appears 15 ms after construction, like the next packet would carry it."""

    def __init__(self):
        super().__init__(BLIND)
        import time as _time

        self.born = _time.monotonic()
        self.clock = _time

    def pid_fault_axes(self):
        return {2} if self.clock.monotonic() - self.born > 0.015 else set()


def test_loop_off_waits_for_a_fresh_packet_before_trusting_the_fault_bits(tuner_mod, tmp_path):
    mcu = LateFaultMcu()
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    t.loop_off()
    assert mcu.pid_off_calls == 0, "the DISABLE would have arrived after the trip and erased the fault"


class StatusUnavailableMcu(FakeMcu):
    def pid_fault_axes(self):
        raise RuntimeError("serial read failed")


def test_loop_off_with_unreadable_fault_status_keeps_the_latch(tuner_mod, tmp_path):
    # unknown is not "no fault": a DISABLE on an unknown state could be the acknowledgment of a real one
    mcu = StatusUnavailableMcu(BLIND)
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    t.loop_off()
    assert mcu.pid_off_calls == 0
    assert t.loop_on is False


def test_loop_off_without_a_fresh_packet_keeps_the_latch(tuner_mod, tmp_path):
    # the stream stopped (USB stalled, reader thread down): the cached bits prove nothing about now
    import time as _time

    mcu = FakeMcu(BLIND)
    mcu.packets_stopped_at = _time.time() - 1.0
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    t.loop_off()
    assert mcu.pid_off_calls == 0


def test_a_fault_once_observed_is_never_disabled_even_if_a_later_packet_shows_it_clear(tuner_mod, tmp_path):
    # the tool saw the fault (and read its cause); a later packet without the bit - stale, or another
    # client's acknowledgment - must not turn the cleanup into a DISABLE
    mcu = FakeMcu(BLIND, fault_axes={tuner_mod.AXIS.Z})
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    with pytest.raises(RuntimeError, match="PID_FAULT"):
        t.guard()
    assert t.fault_observed is True
    mcu.fault_axes.clear()
    t.loop_on = True
    t.loop_off()
    t.shutdown()
    assert mcu.pid_off_calls == 0


def test_loop_off_disables_when_no_fault_is_latched(tuner_mod, tmp_path):
    mcu = FakeMcu(BLIND)
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    t.loop_off()
    assert mcu.pid_off_calls == 1


def test_shutdown_with_a_latched_fault_keeps_it_and_still_makes_the_board_safe(tuner_mod, tmp_path):
    mcu = FakeMcu(BLIND, fault_axes={tuner_mod.AXIS.Z})
    t = make_tuner(tuner_mod, tmp_path, mcu, loop_on=True)
    t.shutdown()
    assert mcu.pid_off_calls == 0
    assert ("reporting", tuner_mod.AXIS.Z, tuner_mod.ENCODER_REPORTING.OFF) in mcu.calls
    assert ("velocity", tuner_mod.AXIS.Z) in mcu.calls
    assert (tmp_path / "summary.json").exists()


# ---------------------------------------------------------------- enableprobe: an explicit ENABLE from rest
# The 2026-09-14 finish bench faulted (NO_PROGRESS) at the ENABLE after a rest, untraced. One rep of
# enableprobe is that ENABLE, watched from the host: what the error was before it, whether the loop
# engaged, how long the error took to come inside the tolerance, and - if the firmware trips - the
# cause, read before anything could clear it, with the latch left in place.

REPORTING = {
    "reporting": True,
    "pid_enabled": False,
    "pid_fault": False,
    "pid_zone_hold": False,
    "axis": 2,
    "encoder_pos": 100,
    "deviation": 1,
    "dev32": 1,
}


class EnableFakeMcu(FakeMcu):
    """ENABLE is accepted and the loop engages; `fault_after` status reads later the firmware trips."""

    def __init__(self, state, fault_after=None, refuse=None):
        super().__init__(state)
        self.fault_after = fault_after
        self.refuse = refuse
        self.reads = 0
        self.last_command_aborted_error = None

    def get_encoder_state(self):
        self.reads += 1
        if self.fault_after is not None and self.reads > self.fault_after:
            self.state.update(pid_enabled=False, pid_fault=True)
            self.fault_axes.add(2)
        return dict(self.state)

    def pid_fault_cause(self, axis):
        self.calls.append(("cause", axis))
        return 2 if 2 in self.fault_axes else 0  # NO_PROGRESS, as on the bench

    def turn_on_stage_pid(self, axis):
        self.calls.append(("enable", axis))
        if self.refuse is not None:
            self.last_command_aborted_error = RuntimeError(self.refuse)
            return
        self.state["pid_enabled"] = True

    def set_encoder_reporting(self, axis, mode):
        # like Microcontroller.send_command: a new command clears (and warns about) a pending abort
        self.last_command_aborted_error = None
        super().set_encoder_reporting(axis, mode)

    def wait_till_operation_is_completed(self, timeout=None):
        if self.last_command_aborted_error is not None:
            raise self.last_command_aborted_error

    def acknowledge_aborted_command(self):
        self.calls.append(("ack_abort",))
        self.last_command_aborted_error = None


def make_enable_tuner(tuner_mod, tmp_path, mcu):
    args = types.SimpleNamespace(
        out=str(tmp_path), max_dev_um=200.0, depth_max=2.0, tol_um=0.0, enable_watch_ms=40, rest_s=0.0
    )
    t = tuner_mod.ZTuner(args)
    t.mcu = mcu
    return t


def test_enable_from_rest_records_the_engage_and_the_time_to_tolerance(tuner_mod, tmp_path):
    mcu = EnableFakeMcu(REPORTING)
    t = make_enable_tuner(tuner_mod, tmp_path, mcu)
    row, abort = t._enableprobe_rep(1, tol_usteps=2.0)
    assert abort is None
    assert ("enable", tuner_mod.AXIS.Z) in mcu.calls
    assert row["dev32_before_enable_usteps"] == 1
    assert row["pid_enabled_after_ack"] is True and row["engaged_throughout"] is True
    assert row["time_to_within_tolerance_ms"] == 0.0
    assert row["faults_in_window"] == 0 and row["fault_cause"] == 0
    assert row["window_samples"] > 1
    assert t.loop_on is True and mcu.pid_off_calls == 0


def test_a_fault_during_the_watch_keeps_the_row_reads_the_cause_and_preserves_the_latch(tuner_mod, tmp_path):
    mcu = EnableFakeMcu(REPORTING, fault_after=2)
    t = make_enable_tuner(tuner_mod, tmp_path, mcu)
    row, abort = t._enableprobe_rep(1, tol_usteps=2.0)
    assert isinstance(abort, RuntimeError) and "PID_FAULT" in str(abort)
    assert row["faults_in_window"] >= 1 and row["engaged_throughout"] is False
    assert row["fault_cause"] == 2 and "no progress" in row["fault_cause_name"]
    assert mcu.pid_off_calls == 0, "the latch is the evidence; nothing here acknowledges it"
    assert sum(1 for c in mcu.calls if c[0] == "cause") == 1
    assert t.loop_on is False


def test_a_refused_enable_is_a_row_with_its_cause_read_before_the_abort_is_acknowledged(tuner_mod, tmp_path):
    mcu = EnableFakeMcu(REPORTING, refuse="CMD_EXECUTION_ERROR on ENABLE_STAGE_PID")
    t = make_enable_tuner(tuner_mod, tmp_path, mcu)
    row, abort = t._enableprobe_rep(1, tol_usteps=2.0)
    assert isinstance(abort, RuntimeError) and "not accepted" in str(abort)
    assert row["pid_enabled_after_ack"] is False and row["window_samples"] == 1
    order = [c[0] for c in mcu.calls if c[0] in ("cause", "ack_abort")]
    assert order == ["ack_abort", "cause"], (
        "acknowledging the abort is host bookkeeping (nothing goes to the controller); doing it first keeps "
        "the cause read's own commands from tripping over a pending abort. The cause stays on the wire "
        "until the latch is cleared, which nothing here does"
    )
    assert mcu.pid_off_calls == 0 and t.loop_on is False


# ---------------------------------------------------------------- effective completion bound
# The firmware acknowledges a closed-loop move against max(window if set else target tolerance,
# deadband) (pid_completion_encoder_ok in pid_policy.h). The ack ladder's record has to state that
# effective bound, not the requested numbers, or a window below the deadband reads as tighter than
# what was actually enforced.


def test_completion_bound_is_the_target_tolerance_without_a_window(tuner_mod):
    assert tuner_mod.completion_bound_um(0.0, 0.2, 0.2) == 0.2
    assert tuner_mod.completion_bound_um(0.0, 0.5, 0.2) == 0.5


def test_completion_bound_is_the_window_when_set_even_below_the_target(tuner_mod):
    assert tuner_mod.completion_bound_um(0.3, 0.5, 0.2) == 0.3


def test_completion_bound_is_never_below_the_deadband(tuner_mod):
    assert tuner_mod.completion_bound_um(0.1, 0.2, 0.2) == 0.2
    assert tuner_mod.completion_bound_um(0.0, 0.1, 0.2) == 0.2
