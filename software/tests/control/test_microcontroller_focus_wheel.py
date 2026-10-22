"""SET_FOCUS_WHEEL_MODE and the focus wheel count in the status packet (firmware >= 1.8), against FirmwareSimSerial."""

import time

import pytest

import control._def
from control._def import FOCUS_WHEEL_MODE, FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE, LIMIT_SWITCH_CONFIG_MIN_FIRMWARE
from control.firmware_sim_serial import FirmwareSimSerial
from control.microcontroller import Microcontroller


def _mcu(version):
    sim = FirmwareSimSerial(strict=True, firmware_version=version)
    return Microcontroller(serial_device=sim, reset_and_initialize=False), sim


def _fresh_packet(mcu):
    mcu.turn_off_all_ports()
    mcu.wait_till_operation_is_completed()


def _wait_for(predicate, timeout_s=2.0):
    deadline = time.time() + timeout_s
    while not predicate() and time.time() < deadline:
        time.sleep(0.01)
    return predicate()


def test_wheel_count_is_reported_only_in_to_host_mode():
    mcu, sim = _mcu(FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    try:
        assert mcu.supports_focus_wheel_to_host()
        sim.turn_focus_wheel(7)  # DRIVES_Z: the wheel moves Z
        _fresh_packet(mcu)
        assert mcu.focus_wheel_count is None
        assert sim.z == 7

        mcu.set_focus_wheel_mode(FOCUS_WHEEL_MODE.TO_HOST)
        mcu.wait_till_operation_is_completed()
        sim.turn_focus_wheel(-12)
        _fresh_packet(mcu)
        assert _wait_for(lambda: mcu.focus_wheel_count == -12)
        assert sim.z == 7  # Z did not move
        theta_before = mcu.theta_pos

        mcu.set_focus_wheel_mode(FOCUS_WHEEL_MODE.DRIVES_Z)
        mcu.wait_till_operation_is_completed()
        assert _wait_for(lambda: mcu.focus_wheel_count is None)
        assert mcu.theta_pos == theta_before
    finally:
        mcu.close()


def test_re_entering_to_host_resets_the_count():
    mcu, sim = _mcu(FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    try:
        mcu.set_focus_wheel_mode(FOCUS_WHEEL_MODE.TO_HOST)
        mcu.wait_till_operation_is_completed()
        sim.turn_focus_wheel(5)
        mcu.set_focus_wheel_mode(FOCUS_WHEEL_MODE.DRIVES_Z)
        mcu.wait_till_operation_is_completed()
        mcu.set_focus_wheel_mode(FOCUS_WHEEL_MODE.TO_HOST)
        mcu.wait_till_operation_is_completed()
        assert _wait_for(lambda: mcu.focus_wheel_count == 0)
    finally:
        mcu.close()


def test_older_firmware_has_no_focus_wheel_mode():
    mcu, sim = _mcu(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
    try:
        assert not mcu.supports_focus_wheel_to_host()
        mcu.set_focus_wheel_mode(FOCUS_WHEEL_MODE.TO_HOST)  # callback_default: success, nothing happens
        mcu.wait_till_operation_is_completed()
        sim.turn_focus_wheel(3)
        _fresh_packet(mcu)
        assert mcu.focus_wheel_count is None
        assert sim.z == 3
    finally:
        mcu.close()


def test_invalid_mode_is_refused_before_sending():
    mcu, _ = _mcu(FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    try:
        with pytest.raises(ValueError):
            mcu.set_focus_wheel_mode(2)
    finally:
        mcu.close()


@pytest.mark.parametrize(
    "use_lift, wheel_drives_pi, expected",
    [
        (True, True, FOCUS_WHEEL_MODE.TO_HOST),  # a lift system: the wheel never moves the lift
        (True, False, FOCUS_WHEEL_MODE.DRIVES_Z),
        (False, True, FOCUS_WHEEL_MODE.DRIVES_Z),  # stock system: the stock wheel
    ],
)
def test_connect_saves_the_power_up_wheel_mode(monkeypatch, use_lift, wheel_drives_pi, expected):
    monkeypatch.setattr(control._def, "USE_Z_LIFT_STAGE", use_lift)
    monkeypatch.setattr(control._def, "Z_LIFT_FOCUS_WHEEL_DRIVES_PI", wheel_drives_pi)
    mcu, sim = _mcu(FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    try:
        assert sim.focus_wheel_power_up_mode == expected
        assert sim.focus_wheel_mode == expected
    finally:
        mcu.close()


@pytest.mark.parametrize("command", ["reset", "initialize_drivers"])
def test_reset_and_initialize_return_to_the_saved_mode(monkeypatch, command):
    monkeypatch.setattr(control._def, "USE_Z_LIFT_STAGE", True)
    monkeypatch.setattr(control._def, "Z_LIFT_FOCUS_WHEEL_DRIVES_PI", True)
    mcu, sim = _mcu(FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    try:
        mcu.set_focus_wheel_mode(FOCUS_WHEEL_MODE.DRIVES_Z)  # not saved
        mcu.wait_till_operation_is_completed()
        assert sim.focus_wheel_mode == FOCUS_WHEEL_MODE.DRIVES_Z
        getattr(mcu, command)()
        if command != "reset":  # RESET restarts the command ids: Microcontroller.reset() does not wait either
            mcu.wait_till_operation_is_completed()
        assert sim.focus_wheel_mode == FOCUS_WHEEL_MODE.TO_HOST  # the saved power-up mode, not the stepper
        sim.turn_focus_wheel(40)
        assert sim.z == 0  # the lift did not move
    finally:
        mcu.close()


def test_unknown_flags_are_refused_and_change_nothing():
    mcu, sim = _mcu(FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    try:
        before = (sim.focus_wheel_mode, sim.focus_wheel_power_up_mode)
        assert not sim._set_focus_wheel_mode(FOCUS_WHEEL_MODE.TO_HOST, 0x02)
        assert (sim.focus_wheel_mode, sim.focus_wheel_power_up_mode) == before
    finally:
        mcu.close()


def test_older_firmware_gets_no_power_up_mode(monkeypatch):
    monkeypatch.setattr(control._def, "USE_Z_LIFT_STAGE", True)
    monkeypatch.setattr(control._def, "Z_LIFT_FOCUS_WHEEL_DRIVES_PI", True)
    mcu, sim = _mcu(LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
    try:
        assert sim.focus_wheel_power_up_mode == FOCUS_WHEEL_MODE.DRIVES_Z  # nothing sent
    finally:
        mcu.close()
