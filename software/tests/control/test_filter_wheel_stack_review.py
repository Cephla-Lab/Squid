"""Regression tests from the review of the combined stack (#657 -> #662 -> #664, 2026-09-20). Each of the first five
reproduced a defect before its fix; the rest pin the behaviour added around those fixes. No real hardware."""

import threading
from unittest.mock import MagicMock
import pytest
import control.microcontroller
import control._def as defs
import squid.filter_wheel_controller.cephla as cephla
import squid.filter_wheel_tuning as tuning
from squid.config import SquidFilterWheelConfig
from control.widgets_filter_wheel_tuning import FilterWheelTuningDialog
from qtpy.QtCore import Qt

PROFILE = dict(
    microstepping_default_w=8,
    max_velocity_w_mm=6.0,
    max_acceleration_w_mm=250.0,
    stall_edge_found=False,
    moves_confirmed=96,
)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(cephla, "_WHEEL_CACHE_PATH", str(tmp_path / "wheel.json"))
    for key, value in [
        ("MICROSTEPPING_DEFAULT_W", 64),
        ("MAX_VELOCITY_W_mm", 3.19),
        ("MAX_ACCELERATION_W_mm", 300),
        ("SQUID_FILTERWHEEL_COMPLETION_WINDOW_DEG", 0.0),
    ]:
        monkeypatch.setattr(defs, key, value)
    mc = MagicMock()
    mc.firmware_version = (1, 6)
    mc.is_simulated = False
    mc.last_command_aborted_error = None
    configs = {
        i: SquidFilterWheelConfig(
            max_index=8, min_index=1, offset=0.008, motor_slot_index=i + 2, transitions_per_revolution=4000
        )
        for i in (1, 2)
    }
    cephla.cache_wheel_state({i: cephla.WheelRecord(5, 0, cephla.host_motion_config()) for i in configs})
    wheel = cephla.SquidFilterWheel(mc, configs, skip_init=True)
    wheel.initialize([1, 2])
    mc.reset_mock()
    return mc, wheel


def test_apply_keeps_second_wheel_microstepping_in_sync(rig, tmp_path):
    mc, wheel = rig
    ini = tmp_path / "machine.ini"
    ini.write_text("[GENERAL]\nmicrostepping_default_w = 64\n")
    tuning.WheelTuningSession(mc, wheel).apply(PROFILE, ini_path=str(ini))
    assert (
        wheel._configured[2]["microstepping"] == defs.MICROSTEPPING_DEFAULT_W
    ), "W2 driver remains at 64 while all slot calculations now use 8"


def test_failed_reconfiguration_invalidates_position_before_touching_driver(rig):
    mc, wheel = rig
    mc.configure_squidfilter.side_effect = TimeoutError("lost configuration acknowledgement")
    with pytest.raises(TimeoutError):
        wheel.reconfigure_driver(1, return_to_slot=5)
    assert not wheel.position_is_known(1), "partially reconfigured wheel is still reported known"
    assert 1 not in cephla.load_cached_wheel_state()


def test_tuning_withdraws_cache_before_direct_motion(rig, monkeypatch):
    mc, wheel = rig
    observed = {}

    class ProbeTuner:
        def __init__(self, *args, **kwargs):
            self.summary = {}
            self.cancelled = False

        def run(self):
            observed["known"] = wheel.position_is_known(1)
            observed["cache"] = cephla.load_cached_wheel_state()

    monkeypatch.setattr(tuning, "WheelTuner", ProbeTuner)
    tuning.WheelTuningSession(mc, wheel).run("tune")
    assert (
        not observed["known"] and 1 not in observed["cache"]
    ), "the old configuration/position record remains trusted while tuning changes both"


def test_setup_clears_existing_completion_window_when_tuning(rig, monkeypatch):
    mc, _ = rig
    monkeypatch.setattr(defs, "SQUID_FILTERWHEEL_COMPLETION_WINDOW_DEG", 5.0)
    params = tuning.tuner_params("tune")
    assert params.window_deg == 0.0
    monkeypatch.setattr(tuning, "Sampler", MagicMock())
    tuner = tuning.WheelTuner(params, mcu=mc, log_fn=lambda _: None)
    tuner.setup(False)
    mc.set_completion_window.assert_called_once_with(defs.AXIS.W, 0.0)


def test_escape_cannot_release_modal_dialog_while_worker_is_running(rig, qtbot):
    mc, wheel = rig
    dialog = FilterWheelTuningDialog(mc, wheel)
    qtbot.addWidget(dialog)
    started = threading.Event()
    release = threading.Event()

    def run(action):
        started.set()
        release.wait(10)
        return {"cancelled": True}

    dialog.session.run = run
    dialog.show()
    dialog.button_tune.click()
    worker = dialog.worker
    try:
        qtbot.waitUntil(started.is_set)
        assert dialog.isVisible()
        qtbot.keyClick(dialog, Qt.Key_Escape)
        assert dialog.isVisible(), "Escape bypasses closeEvent while tuning still owns the hardware"
    finally:
        release.set()
        worker.wait(5000)
        qtbot.waitUntil(lambda: dialog.worker is None)


def test_a_home_configures_the_driver_first_when_its_configuration_is_unknown(rig):
    # after a failed re-configuration (or a release for direct control) nothing may anchor a coordinate on a driver
    # whose microstepping the host is not sure of
    mc, wheel = rig
    wheel.release_for_direct_control(1)
    mc.reset_mock()
    wheel.home(1)
    names = [c[0] for c in mc.method_calls]
    assert "configure_squidfilter" in names and names.index("configure_squidfilter") < names.index("home_w")
    assert wheel.position_is_known(1) and wheel._configured[1] == cephla.host_motion_config()


def test_apply_goes_on_to_the_other_wheel_when_one_fails_and_then_reports_it(rig, tmp_path):
    mc, wheel = rig
    ini = tmp_path / "machine.ini"
    ini.write_text("[GENERAL]" + chr(10) + "microstepping_default_w = 64" + chr(10))
    real = wheel.reconfigure_driver
    seen = []

    def flaky(wheel_id, return_to_slot=None):
        seen.append(wheel_id)
        if wheel_id == 1:
            wheel.release_for_direct_control(1)
            raise TimeoutError("no acknowledgement")
        return real(wheel_id, return_to_slot=return_to_slot)

    wheel.reconfigure_driver = flaky
    with pytest.raises(RuntimeError, match="wheel 1"):
        tuning.WheelTuningSession(mc, wheel).apply(PROFILE, ini_path=str(ini))
    assert seen == [1, 2]  # wheel 2 was still brought onto the new microstepping
    assert wheel._configured[2]["microstepping"] == 8 and not wheel.position_is_known(1)
    assert wheel.get_filter_wheel_position()[2] == 5  # and returned to the slot it was on


def test_shutdown_restores_the_microstepping_before_what_is_stored_in_microsteps(rig, monkeypatch):
    # the controller converts velocity, acceleration and the window to microsteps on receipt
    mc, _ = rig
    monkeypatch.setattr(tuning, "Sampler", MagicMock())
    tuner = tuning.WheelTuner(tuning.tuner_params("tune"), mcu=mc, log_fn=lambda _: None)
    tuner.shutdown()
    names = [c[0] for c in mc.method_calls]
    driver = names.index("configure_motor_driver")
    assert driver < names.index("set_max_velocity_acceleration") < names.index("set_completion_window")
    assert driver < names.index("set_ramp_profile")
