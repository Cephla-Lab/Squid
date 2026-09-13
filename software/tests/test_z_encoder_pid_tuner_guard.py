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
        self.pid_off_calls = 0

    def get_encoder_state(self):
        return dict(self.state)

    def pid_fault_axes(self):
        return set(self.fault_axes)

    def pid_fault_cause(self, axis):
        return 0

    def set_encoder_reporting(self, axis, mode):
        pass

    def turn_off_stage_pid(self, axis):
        self.pid_off_calls += 1

    def wait_till_operation_is_completed(self, timeout=None):
        pass


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
    with pytest.raises(RuntimeError, match="PID_FAULT"):
        t.guard()
    assert mcu.pid_off_calls == 1


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
