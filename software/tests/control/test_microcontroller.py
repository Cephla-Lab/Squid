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

    micro.set_completion_window(control._def.AXIS.W, 5.0 / 360.0)  # 5 deg of a wheel turn = 139 x 1e-4 rev
    assert micro.last_command[1] == control._def.CMD_SET.SET_COMPLETION_WINDOW
    assert micro.last_command[2] == control._def.AXIS.W
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 139

    micro.set_pid_home_zone(control._def.AXIS.Z, 500)
    assert micro.last_command[1] == control._def.CMD_SET.SET_PID_HOME_ZONE
    assert micro.last_command[2] == control._def.AXIS.Z
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 500

    import pytest as _pytest

    with _pytest.raises(ValueError):
        micro.set_pid_arguments(control._def.AXIS.Z, 70000, 0, 0)  # P above 65535 is refused, not truncated

    micro.set_pid_open_above(control._def.AXIS.Z, 1.0)  # loop open above 1 mm/s = 100 x 0.01 mm/s
    assert micro.last_command[1] == control._def.CMD_SET.SET_PID_OPEN_ABOVE
    assert micro.last_command[2] == control._def.AXIS.Z
    assert (micro.last_command[3] << 8) + micro.last_command[4] == 100

    micro.close()


def _feed_status_packet(micro, msg):
    """Push one raw status packet into the simulated port and wait for the read thread to parse it.

    The read loop notifies _received_packet_cv at the end of the parse, so waiting on it means
    every field of `msg` - including the closed-loop fault bits - has been through the real
    handler rather than a copy of its logic.
    """
    deadline = time.time() + 5.0
    while micro._serial.bytes_available() and time.time() < deadline:
        time.sleep(0.005)
    with micro._received_packet_cv:
        with micro._serial._update_lock:
            micro._serial.response_buffer.extend(bytes(msg))
        assert micro._received_packet_cv.wait(timeout=5.0), "read thread never parsed the injected packet"


def test_pid_fault_bits_are_parsed_and_logged_once(caplog):
    """Byte 18 bits 4-6 carry the latched closed-loop fault per axis, in every packet.

    Firmware 1.6 sets them whether or not encoder reporting is on, so a fault with no command
    in flight still reaches the host. Each NEW fault is logged once; a packet that repeats a
    fault the host already knows about must stay quiet, or a 100 Hz packet stream would bury
    the log.
    """
    from crc import CrcCalculator, Crc8

    micro = get_test_micro()
    crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

    def packet(button_and_switch_state, z_cause=0, flags=0):
        msg = bytearray(24)
        msg[1] = control._def.CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
        msg[18] = button_and_switch_state
        # Reporting off: X / Y causes in byte 19 bits 1-3 / 4-6, Z's in byte 20 bits 0-2 (byte 19
        # bit 0 stays clear). Reporting on: byte 19 is the encoder flags and 20-21 the clipped
        # deviation, so no cause is on the wire.
        msg[19] = flags
        msg[20] = (z_cause & control._def.PID_FAULT_CAUSE.MASK) << control._def.PID_FAULT_CAUSE.Z_SHIFT
        msg[22] = (1 << 4) | 6
        msg[23] = crc_calculator.calculate_checksum(msg[:23])
        return msg

    try:
        assert micro.pid_fault_axes() == set()
        assert micro.pid_fault_cause(control._def.AXIS.Z) == control._def.PID_FAULT_CAUSE.NONE

        with caplog.at_level(logging.ERROR, logger="squid.Microcontroller"):
            caplog.clear()
            _feed_status_packet(
                micro,
                packet(
                    1 << control._def.BIT_POS_PID_FAULT_Z,
                    z_cause=control._def.PID_FAULT_CAUSE.NO_PROGRESS,
                ),
            )

            assert micro.pid_fault_axes() == {control._def.AXIS.Z}
            assert micro.pid_fault_cause(control._def.AXIS.Z) == control._def.PID_FAULT_CAUSE.NO_PROGRESS
            faults = [r for r in caplog.records if "closed-loop fault on Z" in r.message]
            assert len(faults) == 1, f"expected one ERROR for the new Z fault, got {len(faults)}"
            # "the loop faulted" is not actionable on its own; the cause byte is what tells the
            # operator whether to look at the encoder, the stage or the budget.
            assert "no progress" in faults[0].message, faults[0].message

            # The same latch reported again is not news.
            caplog.clear()
            _feed_status_packet(
                micro,
                packet(
                    1 << control._def.BIT_POS_PID_FAULT_Z,
                    z_cause=control._def.PID_FAULT_CAUSE.NO_PROGRESS,
                ),
            )

            assert micro.pid_fault_axes() == {control._def.AXIS.Z}
            assert [r for r in caplog.records if "closed-loop fault" in r.message] == []
    finally:
        micro.close()


def test_pid_fault_log_does_not_invent_a_cause_while_encoder_reporting_is_on(caplog):
    """Bytes 19-21 carry the reported axis's flags and deviation while reporting is on, so the cause
    is simply not on the wire. Saying so beats naming whichever cause those bytes happen to alias."""
    from crc import CrcCalculator, Crc8

    micro = get_test_micro()
    crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

    msg = bytearray(24)
    msg[1] = control._def.CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
    msg[18] = 1 << control._def.BIT_POS_PID_FAULT_Z
    msg[19] = (1 << control._def.ENC_FLAG.REPORTING) | (control._def.AXIS.Z << control._def.ENC_FLAG.AXIS_SHIFT)
    msg[22] = (1 << 4) | 6
    msg[23] = crc_calculator.calculate_checksum(msg[:23])

    try:
        with caplog.at_level(logging.ERROR, logger="squid.Microcontroller"):
            caplog.clear()
            _feed_status_packet(micro, msg)

        assert micro.pid_fault_axes() == {control._def.AXIS.Z}
        faults = [r for r in caplog.records if "closed-loop fault on Z" in r.message]
        assert len(faults) == 1, f"expected one ERROR for the new Z fault, got {len(faults)}"
        assert "cause not on the wire while encoder reporting is on" in faults[0].message, faults[0].message
    finally:
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
    flags = (
        (1 << control._def.ENC_FLAG.REPORTING)
        | (1 << control._def.ENC_FLAG.PID_ENABLED)
        | (control._def.AXIS.Z << control._def.ENC_FLAG.AXIS_SHIFT)
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


def test_dev32_is_encoder_minus_counter_from_one_packet():
    """The 32-bit loop error has to come out of the reader thread, not out of two reads.

    Every caller wants ENC_POS - XACTUAL at full width (the packet's own deviation field clips at
    int16), and every caller used to build it by pairing st["encoder_pos"] from one packet with a
    later bare mcu.z_pos. The reader thread writes those two attributes at different lines with no
    lock, so a read landing in between mixes packets - at 1 mm/s a 10 ms straddle is ~10 um of
    phantom error. The parser pairs them from the same packet instead.
    """
    from crc import CrcCalculator, Crc8

    micro = get_test_micro()
    crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

    def packet(z, enc, axis):
        msg = bytearray(24)
        msg[0] = 7
        msg[1] = control._def.CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
        msg[10:14] = int(z).to_bytes(4, "big", signed=True)
        msg[14:18] = int(enc).to_bytes(4, "big", signed=True)
        msg[19] = (
            (1 << control._def.ENC_FLAG.REPORTING)
            | (1 << control._def.ENC_FLAG.PID_ENABLED)
            | (axis << control._def.ENC_FLAG.AXIS_SHIFT)
        )
        msg[20:22] = (0).to_bytes(2, "big", signed=True)
        msg[22] = (1 << 4) | 6
        msg[23] = crc_calculator.calculate_checksum(msg[:23])
        return msg

    try:
        z, enc = -853333, -853333 + 40000  # 40000 usteps is far outside the int16 deviation field
        _feed_status_packet(micro, packet(z, enc, control._def.AXIS.Z))
        state = micro.get_encoder_state()
        assert state["encoder_pos"] == enc
        assert state["dev32"] == enc - z
        # the clipped field is still reported as the firmware sent it
        assert state["deviation"] == 0

        # An axis whose step counter is not in the status packet (the filter wheel) has no host-side
        # difference to report: None fails loudly rather than silently subtracting the Z counter.
        _feed_status_packet(micro, packet(z, enc, control._def.AXIS.W))
        assert micro.get_encoder_state()["dev32"] is None
    finally:
        micro.close()


def _fault_cause_packet(crc_calculator, fault_bits=0, x_cause=0, y_cause=0, z_cause=0, flags=None, dev=0, enc=0, z=0):
    """A status packet in either byte 19-21 layout.

    flags=None builds the reporting-OFF layout: byte 18 carries the latched fault bits and bytes
    19-20 the packed causes. Passing flags builds the reporting-ON layout instead, where byte 19
    is the ENC_FLAG bits and bytes 20-21 the clipped deviation.
    """
    msg = bytearray(24)
    msg[0] = 7
    msg[1] = control._def.CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
    msg[10:14] = int(z).to_bytes(4, "big", signed=True)
    msg[14:18] = int(enc).to_bytes(4, "big", signed=True)
    msg[18] = fault_bits
    if flags is None:
        cause = control._def.PID_FAULT_CAUSE
        msg[19] = ((x_cause & cause.MASK) << cause.X_SHIFT) | ((y_cause & cause.MASK) << cause.Y_SHIFT)
        msg[20] = (z_cause & cause.MASK) << cause.Z_SHIFT
    else:
        msg[19] = flags
        msg[20:22] = int(dev).to_bytes(2, "big", signed=True)
    msg[22] = (1 << 4) | 6
    msg[23] = crc_calculator.calculate_checksum(msg[:23])
    return msg


def test_enc_flag_fields_are_not_decoded_from_the_cause_layout():
    """Byte 19 is only ENC_FLAG bits while bit 0 (REPORTING) is set.

    With reporting off the firmware packs X's fault cause into bits 1-3 and Y's into bits 4-6 of
    that same byte, so decoding it as flags regardless makes an X cause of 2 (NO_PROGRESS) read
    back as pid_fault, a cause of 1 as pid_enabled, 4 as pid_zone_hold, and any Y cause as a
    reported axis. get_encoder_state() has to report no loop state at all in that layout.
    """
    from crc import CrcCalculator, Crc8

    micro = get_test_micro()
    crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

    try:
        _feed_status_packet(
            micro,
            _fault_cause_packet(
                crc_calculator,
                fault_bits=1 << control._def.BIT_POS_PID_FAULT_X,
                x_cause=control._def.PID_FAULT_CAUSE.NO_PROGRESS,  # 2 << 1 = ENC_FLAG.PID_FAULT's bit
            ),
        )
        state = micro.get_encoder_state()
        assert state["reporting"] is False
        assert state["pid_fault"] is False
        assert state["pid_enabled"] is False
        assert state["pid_zone_hold"] is False
        assert state["axis"] == 0
        # the cause itself is read from the same byte, in the layout it is actually in
        assert micro.pid_fault_cause(control._def.AXIS.X) == control._def.PID_FAULT_CAUSE.NO_PROGRESS

        # A Y cause occupies exactly the ENC_FLAG axis field.
        _feed_status_packet(
            micro,
            _fault_cause_packet(
                crc_calculator,
                fault_bits=1 << control._def.BIT_POS_PID_FAULT_Y,
                y_cause=control._def.PID_FAULT_CAUSE.REENGAGE_REFUSED,  # 5 << 4 = axis 5 (AXIS.W)
            ),
        )
        assert micro.get_encoder_state()["axis"] == 0
        assert micro.pid_fault_cause(control._def.AXIS.Y) == control._def.PID_FAULT_CAUSE.REENGAGE_REFUSED
    finally:
        micro.close()


def test_mcu_state_names_a_command_the_firmware_reports_as_still_in_progress():
    """A firmware that never finishes a move keeps answering IN_PROGRESS for the current command id,
    which matches none of the read loop's recovery branches; the timeout text must say so."""
    micro = get_test_micro()
    try:
        micro.move_z_to_usteps(100)
        micro.wait_till_operation_is_completed()
        # The simulator always completes commands, so stage the stuck-firmware state by hand.
        micro._cmd_id_mcu = micro._cmd_id
        micro._cmd_execution_status = control._def.CMD_EXECUTION_STATUS.IN_PROGRESS
        micro._last_successful_read_time = time.time()

        state = micro._mcu_state()

        assert f"Sent cmd {micro._cmd_id} (MOVETO_Z)" in state
        assert f"mcu reports cmd {micro._cmd_id} status=IN_PROGRESS" in state
        assert "Home the stage to clear it" in state
        assert "gone quiet" not in state
    finally:
        micro.close()


def test_mcu_state_reports_a_quiet_link_instead_of_a_stuck_command():
    micro = get_test_micro()
    try:
        micro.move_z_to_usteps(100)
        micro.wait_till_operation_is_completed()
        micro._cmd_id_mcu = micro._cmd_id
        micro._cmd_execution_status = control._def.CMD_EXECUTION_STATUS.IN_PROGRESS
        micro._last_successful_read_time = time.time() - 5.0

        state = micro._mcu_state()

        assert "5.0 [s] since a valid packet" in state
        assert "gone quiet - check the serial connection" in state
        assert "Home the stage" not in state
    finally:
        micro.close()


def test_wait_timeout_error_includes_mcu_state():
    micro = get_test_micro()
    try:
        micro.move_z_to_usteps(100)
        micro.wait_till_operation_is_completed()
        micro.is_busy = lambda: True  # pin the wait in the busy state so it has to time out

        with pytest.raises(TimeoutError, match=r"timed out after 0.05 \[s\].*Sent cmd \d+ \(MOVETO_Z\)"):
            micro.wait_till_operation_is_completed(0.05)
    finally:
        micro.close()
