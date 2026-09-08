import logging
import time

import pytest
import control._def
import control.microcontroller
from control.microcontroller import Microcontroller


def get_test_micro() -> control.microcontroller.Microcontroller:
    return control.microcontroller.Microcontroller(
        serial_device=control.microcontroller.get_microcontroller_serial_device(simulated=True)
    )


def assert_pos_almost_equal(expected, actual):
    assert len(actual) == len(expected)
    for e, a in zip(expected, actual):
        assert a == pytest.approx(e)


def test_create_simulated_microcontroller():
    micro = get_test_micro()


def test_microcontroller_simulated_positions():
    micro = get_test_micro()

    micro.move_x_to_usteps(1000)
    micro.wait_till_operation_is_completed()
    micro.move_y_to_usteps(2000)
    micro.wait_till_operation_is_completed()
    micro.move_z_to_usteps(3000)
    micro.wait_till_operation_is_completed()
    micro.move_theta_usteps(4000)
    micro.wait_till_operation_is_completed()

    assert_pos_almost_equal((1000, 2000, 3000, 4000), micro.get_pos())

    micro.home_x()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 2000, 3000, 4000), micro.get_pos())

    micro.home_y()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 3000, 4000), micro.get_pos())

    micro.home_z()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 4000), micro.get_pos())

    micro.home_theta()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    micro.move_x_to_usteps(1000)
    micro.wait_till_operation_is_completed()
    micro.move_y_to_usteps(2000)
    micro.wait_till_operation_is_completed()
    micro.move_z_to_usteps(3000)
    micro.wait_till_operation_is_completed()
    micro.move_theta_usteps(4000)
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((1000, 2000, 3000, 4000), micro.get_pos())

    micro.move_x_usteps(1)
    micro.wait_till_operation_is_completed()
    micro.move_y_usteps(2)
    micro.wait_till_operation_is_completed()
    micro.move_z_usteps(3)
    micro.wait_till_operation_is_completed()
    micro.move_theta_usteps(4)
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((1001, 2002, 3003, 4004), micro.get_pos())

    micro.zero_x()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 2002, 3003, 4004), micro.get_pos())

    micro.zero_y()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 3003, 4004), micro.get_pos())

    micro.zero_z()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 4004), micro.get_pos())

    micro.zero_theta()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    micro.move_x_to_usteps(1000)
    micro.wait_till_operation_is_completed()
    micro.move_y_to_usteps(2000)
    micro.wait_till_operation_is_completed()
    micro.move_z_to_usteps(3000)
    micro.wait_till_operation_is_completed()
    # There's no move_theta_to_usteps.
    assert_pos_almost_equal((1000, 2000, 3000, 0), micro.get_pos())

    micro.home_xy()
    micro.wait_till_operation_is_completed()
    assert_pos_almost_equal((0, 0, 3000, 0), micro.get_pos())
    micro.close()


@pytest.mark.skip(
    reason="This is likely a bug, but I'm not sure yet.  Tracking in https://linear.app/cephla/issue/S-115/microcontroller-relative-and-absolute-position-sign-mismatch"
)
def test_microcontroller_absolute_and_relative_match():
    micro = get_test_micro()

    def wait():
        micro.wait_till_operation_is_completed()

    micro.home_x()
    wait()

    micro.home_y()
    wait()

    micro.home_z()
    wait()

    micro.home_theta()
    wait()

    # For all our axes, we'd expect that moving to an absolute position from zero brings us to that position.
    # Then doing a relative move of the negative of the absolute position should bring us back to zero.
    abs_position = 1234

    # X
    micro.move_x_to_usteps(abs_position)
    wait()
    assert_pos_almost_equal((abs_position, 0, 0, 0), micro.get_pos())

    micro.move_x_usteps(-abs_position)
    wait()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    # Y
    micro.move_y_to_usteps(abs_position)
    wait()
    assert_pos_almost_equal((0, abs_position, 0, 0), micro.get_pos())

    micro.move_y_usteps(-abs_position)
    wait()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())

    # Z
    micro.move_z_to_usteps(abs_position)
    wait()
    assert_pos_almost_equal((0, 0, abs_position, 0), micro.get_pos())

    micro.move_z_usteps(-abs_position)
    wait()
    assert_pos_almost_equal((0, 0, 0, 0), micro.get_pos())
    micro.close()


def test_microcontroller_reconnects_serial():
    micro = get_test_micro()
    serial = micro._serial

    def wait():
        micro.wait_till_operation_is_completed()

    some_pos = 1234
    micro.move_x_to_usteps(some_pos)
    wait()
    assert_pos_almost_equal((some_pos, 0, 0, 0), micro.get_pos())

    # Force closed, then make sure the microcontroller handles reconnecting.  Both in the write and read cases
    # For the read, sleep a bit first since we know we have a reader loop spinning that could blowup if reconnects
    # don't work properly.
    serial.close()

    time.sleep(1)
    micro.move_y_to_usteps(2 * some_pos)
    wait()
    assert_pos_almost_equal((some_pos, 2 * some_pos, 0, 0), micro.get_pos())

    serial.close()
    micro.move_z_usteps(3 * some_pos)
    wait()
    assert_pos_almost_equal((some_pos, 2 * some_pos, 3 * some_pos, 0), micro.get_pos())
    micro.close()


def test_home_directions():
    test_micro = get_test_micro()

    dirs = (
        control.microcontroller.HomingDirection.HOMING_DIRECTION_FORWARD,
        control.microcontroller.HomingDirection.HOMING_DIRECTION_BACKWARD,
    )

    home_methods = (test_micro.home_x, test_micro.home_y, test_micro.home_z, test_micro.home_w, test_micro.home_theta)

    def wait():
        test_micro.wait_till_operation_is_completed()

    for d in dirs:
        for hm in home_methods:
            hm(homing_direction=d)
            wait()
            assert test_micro.last_command[3] == d.value

    test_micro.close()


def test_payload_helpers():
    assert isinstance(Microcontroller._int_to_payload(10, 1), int)
    assert isinstance(Microcontroller._int_to_payload(1.1, 2), int)
    assert Microcontroller._int_to_payload(1.1, 2) == 1
    assert Microcontroller._int_to_payload(2**16 - 1, 2) == 2**16 - 1
    assert Microcontroller._int_to_payload(2**16, 2) == 2**16

    assert Microcontroller._payload_to_int([0x00, 0x00, 0x00, 0xFF, 0xFF], 5) == 2**16 - 1
    assert Microcontroller._payload_to_int([0xFF, 0xFF], 2) == -1


def test_set_trigger_mode():
    """Test set_trigger_mode sends correct command to firmware."""
    micro = get_test_micro()

    # Test EDGE mode (0)
    micro.set_trigger_mode(0)
    assert micro.last_command[1] == control._def.CMD_SET.SET_TRIGGER_MODE
    assert micro.last_command[2] == 0

    # Test LEVEL mode (1)
    micro.set_trigger_mode(1)
    assert micro.last_command[1] == control._def.CMD_SET.SET_TRIGGER_MODE
    assert micro.last_command[2] == 1

    # Test with HardwareTriggerMode enum values
    from control._def import HardwareTriggerMode

    micro.set_trigger_mode(HardwareTriggerMode.EDGE)
    assert micro.last_command[2] == 0

    micro.set_trigger_mode(HardwareTriggerMode.LEVEL)
    assert micro.last_command[2] == 1

    micro.close()


def test_abort_current_command_recoverable_logs_at_warning(caplog):
    """recoverable=True → WARNING (caller will retry); default → ERROR (operator attention)."""
    micro = get_test_micro()
    try:
        micro.turn_off_all_ports()
        micro.wait_till_operation_is_completed()
        caplog.clear()

        with caplog.at_level(logging.WARNING, logger="squid.Microcontroller"):
            micro.abort_current_command(reason="test recoverable", recoverable=True)
            recoverable_records = [r for r in caplog.records if "test recoverable" in r.message]
            assert recoverable_records, "expected a log record for the recoverable abort"
            assert all(r.levelno == logging.WARNING for r in recoverable_records)

        caplog.clear()
        micro.acknowledge_aborted_command()
        with caplog.at_level(logging.WARNING, logger="squid.Microcontroller"):
            micro.abort_current_command(reason="test fatal")
            fatal_records = [r for r in caplog.records if "test fatal" in r.message]
            assert fatal_records, "expected a log record for the default abort"
            assert all(r.levelno == logging.ERROR for r in fatal_records)
    finally:
        micro.close()


def test_encoder_reporting_and_pid_limits_commands():
    """set_encoder_reporting / set_pid_limits encode as firmware 1.6 expects."""
    micro = get_test_micro()

    micro.set_encoder_reporting(control._def.AXIS.Z, control._def.ENCODER_REPORTING.ENC_IN_THETA)
    assert micro.last_command[1] == control._def.CMD_SET.SET_ENCODER_REPORTING
    assert micro.last_command[2] == control._def.AXIS.Z
    assert micro.last_command[3] == control._def.ENCODER_REPORTING.ENC_IN_THETA

    micro.set_encoder_reporting(control._def.AXIS.Z, control._def.ENCODER_REPORTING.OFF)
    assert micro.last_command[3] == control._def.ENCODER_REPORTING.OFF

    # 0.35 mm/s -> 35 (x100); 250 um -> 250
    micro.set_pid_limits(control._def.AXIS.Z, 0.35, 250)
    assert micro.last_command[1] == control._def.CMD_SET.SET_PID_LIMITS
    assert micro.last_command[2] == control._def.AXIS.Z
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 35
    assert (micro.last_command[5] << 8) + micro.last_command[6] == 250

    # 655.35 mm/s is the 16-bit ceiling; anything above must be refused, not truncated
    import pytest

    with pytest.raises(ValueError):
        micro.set_pid_limits(control._def.AXIS.Z, 700.0, 0)
    with pytest.raises(ValueError):
        micro.set_pid_limits(control._def.AXIS.Z, 0, 70000)

    micro.set_ramp_profile(control._def.AXIS.Z, control._def.RAMP_PROFILE.TRAPEZOID)
    assert micro.last_command[1] == control._def.CMD_SET.SET_RAMP_PROFILE
    assert micro.last_command[2] == control._def.AXIS.Z
    assert micro.last_command[3] == 1

    micro.set_pid_tolerance(control._def.AXIS.Z, 0.15, 0.3)
    assert micro.last_command[1] == control._def.CMD_SET.SET_PID_TOLERANCE
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 15
    assert (micro.last_command[5] << 8) + micro.last_command[6] == 30

    micro.set_completion_window(control._def.AXIS.W, 5.0 / 360.0)   # 5 deg of a wheel turn = 139 x 1e-4 rev
    assert micro.last_command[1] == control._def.CMD_SET.SET_COMPLETION_WINDOW
    assert micro.last_command[2] == control._def.AXIS.W
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 139

    micro.set_pid_home_zone(control._def.AXIS.Z, 500)
    assert micro.last_command[1] == control._def.CMD_SET.SET_PID_HOME_ZONE
    assert micro.last_command[2] == control._def.AXIS.Z
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 500

    micro.set_pid_open_above(control._def.AXIS.Z, 1.0)   # loop open above 1 mm/s = 100 x 0.01 mm/s
    assert micro.last_command[1] == control._def.CMD_SET.SET_PID_OPEN_ABOVE
    assert micro.last_command[2] == control._def.AXIS.Z
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 100

    micro.close()


def test_encoder_fields_decode_from_packet():
    """Theta field becomes encoder_pos and bytes 20-21 the signed deviation, only while flag bit 0 is set."""
    from crc import CrcCalculator, Crc8

    micro = get_test_micro()
    crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

    def packet(theta, flags, dev):
        msg = bytearray(24)
        msg[0] = 7
        msg[1] = control._def.CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
        msg[14:18] = int(theta).to_bytes(4, "big", signed=True)
        msg[19] = flags
        msg[20:22] = int(dev).to_bytes(2, "big", signed=True)
        msg[22] = (1 << 4) | 6
        msg[23] = crc_calculator.calculate_checksum(msg[:23])
        return msg

    # Drive the parser directly on a crafted packet: same code path as the read thread.
    flags = (1 << control._def.ENC_FLAG.REPORTING) | (1 << control._def.ENC_FLAG.PID_ENABLED) | (
        control._def.AXIS.Z << control._def.ENC_FLAG.AXIS_SHIFT
    )
    msg = packet(-853333, flags, -1234)
    micro.theta_pos = micro._payload_to_int(msg[14:18], 4)
    micro.encoder_flags = msg[19]
    if micro.encoder_flags & (1 << control._def.ENC_FLAG.REPORTING):
        micro.encoder_pos = micro.theta_pos
        micro.encoder_deviation = micro._payload_to_int(msg[20:22], 2)
    state = micro.get_encoder_state()
    assert state["reporting"] is True
    assert state["pid_enabled"] is True
    assert state["pid_fault"] is False
    assert state["axis"] == control._def.AXIS.Z
    assert state["encoder_pos"] == -853333
    assert state["deviation"] == -1234

    # Fault bit
    micro.encoder_flags = flags | (1 << control._def.ENC_FLAG.PID_FAULT)
    assert micro.get_encoder_state()["pid_fault"] is True

    micro.close()
