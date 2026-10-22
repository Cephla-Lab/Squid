"""SET_FOCUS_WHEEL_MODE and the focus wheel count in the status packet (firmware >= 1.8), against FirmwareSimSerial."""

import time

import pytest

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
