"""
Tests for FirmwareSimSerial - the firmware-validating serial simulator.

These tests verify that FirmwareSimSerial:
- Correctly parses firmware constants
- Validates commands against firmware protocol
- Catches protocol mismatches
- Works as a drop-in replacement for SimSerial
"""

import itertools
import struct
import pytest
from crc import CrcCalculator, Crc8

from control._def import (
    CMD_SET,
    AXIS,
    MicrocontrollerDef,
    CMD_EXECUTION_STATUS,
    LIMIT_SWITCH_CONFIG_MIN_FIRMWARE,
    RESPONSE_BYTE_FIRMWARE_VERSION,
)
from control.firmware_sim_serial import (
    FirmwareSimSerial,
    FirmwareConstants,
    FirmwareProtocolError,
)
from control.microcontroller import Microcontroller


class TestFirmwareConstants:
    """Test that FirmwareConstants correctly parses firmware source."""

    def test_parses_command_ids(self):
        """Test that command IDs are parsed from firmware."""
        fw = FirmwareConstants()

        # Verify some known command IDs
        assert fw.MOVE_X == 0
        assert fw.MOVE_Y == 1
        assert fw.MOVE_Z == 2
        assert fw.HOME_OR_ZERO == 5
        assert fw.INITIALIZE == 254
        assert fw.RESET == 255

    def test_parses_message_lengths(self):
        """Test that message lengths match MicrocontrollerDef."""
        fw = FirmwareConstants()

        assert fw.CMD_LENGTH == MicrocontrollerDef.CMD_LENGTH
        assert fw.MSG_LENGTH == MicrocontrollerDef.MSG_LENGTH

    def test_parses_axis_ids(self):
        """Test that axis IDs are parsed."""
        fw = FirmwareConstants()

        assert fw.AXIS_X == AXIS.X
        assert fw.AXIS_Y == AXIS.Y
        assert fw.AXIS_Z == AXIS.Z
        assert fw.AXIS_THETA == AXIS.THETA
        assert fw.AXES_XY == AXIS.XY
        assert fw.AXIS_W == AXIS.W

    def test_parses_execution_status(self):
        """Test that execution status codes are parsed."""
        fw = FirmwareConstants()

        assert fw.COMPLETED_WITHOUT_ERRORS == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
        assert fw.IN_PROGRESS == CMD_EXECUTION_STATUS.IN_PROGRESS
        assert fw.CMD_CHECKSUM_ERROR == CMD_EXECUTION_STATUS.CMD_CHECKSUM_ERROR

    def test_command_ids_property(self):
        """Test that command_ids returns all command IDs."""
        fw = FirmwareConstants()
        cmd_ids = fw.command_ids

        assert "MOVE_X" in cmd_ids
        assert "RESET" in cmd_ids
        assert cmd_ids["MOVE_X"] == 0
        assert cmd_ids["RESET"] == 255

    def test_firmware_constants_match_python_def(self):
        """Verify firmware constants match Python _def.py."""
        fw = FirmwareConstants()

        # Command IDs
        assert fw.MOVE_X == CMD_SET.MOVE_X
        assert fw.MOVE_Y == CMD_SET.MOVE_Y
        assert fw.MOVETO_X == CMD_SET.MOVETO_X
        assert fw.HOME_OR_ZERO == CMD_SET.HOME_OR_ZERO
        assert fw.SET_ILLUMINATION == CMD_SET.SET_ILLUMINATION


class TestFirmwareSimSerialValidation:
    """Test that FirmwareSimSerial validates commands correctly."""

    def test_accepts_valid_command(self):
        """Test that valid commands are accepted."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1  # cmd_id
        cmd[1] = CMD_SET.MOVE_X
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        # Should not raise
        sim.write(cmd)
        assert sim.commands_validated == 1

    def test_rejects_wrong_length(self):
        """Test that wrong-length commands are rejected."""
        sim = FirmwareSimSerial(strict=True)

        cmd = bytearray(5)  # Wrong length
        cmd[0] = 1
        cmd[1] = CMD_SET.MOVE_X

        with pytest.raises(FirmwareProtocolError, match="Command length"):
            sim.write(cmd)

    def test_rejects_bad_crc(self):
        """Test that commands with bad CRC are rejected."""
        sim = FirmwareSimSerial(strict=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.MOVE_X
        cmd[7] = 0xFF  # Bad CRC

        with pytest.raises(FirmwareProtocolError, match="CRC mismatch"):
            sim.write(cmd)

    def test_rejects_unknown_command(self):
        """Test that unknown command codes are rejected."""
        sim = FirmwareSimSerial(strict=True)
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = 199  # Unknown command code
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        with pytest.raises(FirmwareProtocolError, match="Unknown command code"):
            sim.write(cmd)

    def test_non_strict_mode_logs_warnings(self):
        """Test that non-strict mode logs warnings instead of raising."""
        sim = FirmwareSimSerial(strict=False)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.MOVE_X
        cmd[7] = 0xFF  # Bad CRC

        # Should not raise, but should record error
        sim.write(cmd)
        assert len(sim.validation_errors) > 0
        assert "CRC mismatch" in sim.validation_errors[0]


class TestFirmwareSimSerialBehavior:
    """Test that FirmwareSimSerial simulates firmware behavior correctly."""

    def test_move_updates_position(self):
        """Test that move commands update position."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        # Build MOVE_X command
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.MOVE_X
        # Encode +1000 microsteps (big-endian)
        pos = struct.pack(">i", 1000)
        cmd[2:6] = pos
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        assert sim.x == 1000

    def test_moveto_sets_absolute_position(self):
        """Test that moveto commands set absolute position."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        sim.x = 5000  # Initial position

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.MOVETO_X
        pos = struct.pack(">i", 2000)
        cmd[2:6] = pos
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        assert sim.x == 2000

    def test_home_zeros_position(self):
        """Test that home command zeros position."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        sim.x = 5000
        sim.y = 3000

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.X
        cmd[3] = 1  # HOME_NEGATIVE
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        assert sim.x == 0
        assert sim.y == 3000  # Unchanged

    def test_response_has_correct_length(self):
        """Test that response matches MSG_LENGTH."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)

        assert sim.bytes_available() == MicrocontrollerDef.MSG_LENGTH

    def test_response_has_valid_crc(self):
        """Test that response has valid CRC."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        response = sim.read(MicrocontrollerDef.MSG_LENGTH)

        # Verify CRC
        calculated = crc.calculate_checksum(response[:-1])
        assert calculated == response[-1]

    def test_response_contains_positions(self):
        """Test that response contains current positions."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        sim.x = 100
        sim.y = 200
        sim.z = -300

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 5
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        response = sim.read(MicrocontrollerDef.MSG_LENGTH)

        # Parse positions
        x = struct.unpack(">i", bytes(response[2:6]))[0]
        y = struct.unpack(">i", bytes(response[6:10]))[0]
        z = struct.unpack(">i", bytes(response[10:14]))[0]

        assert x == 100
        assert y == 200
        assert z == -300

    def test_response_contains_cmd_id(self):
        """Test that response echoes command ID."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 42  # Command ID
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        response = sim.read(MicrocontrollerDef.MSG_LENGTH)

        assert response[0] == 42

    def test_response_has_completed_status(self):
        """Test that response has COMPLETED_WITHOUT_ERRORS status."""
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        response = sim.read(MicrocontrollerDef.MSG_LENGTH)

        assert response[1] == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS

    def test_w_axis_not_in_response(self):
        """Test that W axis position is tracked but NOT included in response.

        Per firmware protocol specification, the response packet only includes
        X, Y, Z, and Theta positions. W axis is intentionally excluded.
        """
        sim = FirmwareSimSerial()
        crc = CrcCalculator(Crc8.CCITT, table_based=True)

        # Get response with W at default (0)
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc.calculate_checksum(cmd[:-1])
        sim.write(cmd)
        response_before = sim.read(MicrocontrollerDef.MSG_LENGTH)

        # Change W axis position
        sim.w = 12345

        # Get another response - should be identical since W is not in response
        cmd[0] = 2  # Different cmd_id
        cmd[7] = crc.calculate_checksum(cmd[:-1])
        sim.write(cmd)
        response_after = sim.read(MicrocontrollerDef.MSG_LENGTH)

        # Responses should differ only in cmd_id (byte 0) and CRC (byte 23)
        # All position bytes (2-17) should be identical
        assert response_before[2:18] == response_after[2:18]

        # Verify W is actually tracked internally
        assert sim.w == 12345


class TestFirmwareSimSerialWithMicrocontroller:
    """Test FirmwareSimSerial as drop-in replacement with Microcontroller class."""

    def test_microcontroller_commands_pass_validation(self):
        """Test that Microcontroller builds valid commands."""
        sim = FirmwareSimSerial(strict=True)
        mcu = Microcontroller(sim, reset_and_initialize=False)

        # These should all pass validation
        mcu.move_x_usteps(1000)
        mcu.move_y_usteps(-500)
        mcu.turn_on_illumination()
        mcu.turn_off_illumination()

        # Close to stop background thread
        mcu.close()

        # All commands should have been validated
        assert sim.commands_validated >= 4
        assert len(sim.validation_errors) == 0

    def test_home_command_passes_validation(self):
        """Test that home commands pass validation."""
        sim = FirmwareSimSerial(strict=True)
        mcu = Microcontroller(sim, reset_and_initialize=False)

        mcu.home_x()
        mcu.zero_y()

        mcu.close()

        assert sim.commands_validated >= 2
        assert len(sim.validation_errors) == 0

    def test_position_tracking(self):
        """Test that positions are tracked correctly."""
        sim = FirmwareSimSerial(strict=True)
        mcu = Microcontroller(sim, reset_and_initialize=False)

        mcu.move_x_usteps(1000)
        mcu.move_y_usteps(2000)
        mcu.move_z_usteps(-500)

        mcu.close()

        assert sim.x == 1000
        assert sim.y == 2000
        assert sim.z == -500


# The version just before the first one that ships SET_LIMIT_SWITCH_CONFIG.
OLD_FIRMWARE = (LIMIT_SWITCH_CONFIG_MIN_FIRMWARE[0], LIMIT_SWITCH_CONFIG_MIN_FIRMWARE[1] - 1)
STOCK_LIMIT_SWITCHES = {AXIS.X: 0x03, AXIS.Y: 0x03, AXIS.Z: 0x03}
# (protocol axis, nibble) that firmware with SET_LIMIT_SWITCH_CONFIG refuses: not X/Y/Z, or wider than a nibble.
REFUSED_LIMIT_SWITCH_CONFIGS = [(AXIS.THETA, 0x03), (AXIS.W, 0x03), (AXIS.W2, 0x03), (255, 0x03), (AXIS.Z, 0x10)]


def _send(sim, cmd_code, *payload):
    """Send one command (payload from byte 2) to the sim and return its response packet."""
    cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
    cmd[0] = 1
    cmd[1] = cmd_code
    cmd[2 : 2 + len(payload)] = bytes(payload)
    cmd[-1] = CrcCalculator(Crc8.CCITT, table_based=True).calculate_checksum(cmd[:-1])
    sim.write(cmd)
    return sim.read(MicrocontrollerDef.MSG_LENGTH)


class TestFirmwareSimSerialLimitSwitches:
    """Firmware version byte, SET_LIMIT_SWITCH_CONFIG (64) and the Z limit bits of byte 18."""

    @pytest.mark.parametrize("version", [OLD_FIRMWARE, LIMIT_SWITCH_CONFIG_MIN_FIRMWARE, (2, 15)])
    def test_byte_22_carries_the_firmware_version(self, version):
        sim = FirmwareSimSerial(strict=True, firmware_version=version)
        response = _send(sim, CMD_SET.TURN_OFF_ALL_PORTS)
        assert response[RESPONSE_BYTE_FIRMWARE_VERSION] == (version[0] << 4) | version[1]

    def test_default_firmware_version_is_legacy(self):
        sim = FirmwareSimSerial(strict=True)
        assert _send(sim, CMD_SET.TURN_OFF_ALL_PORTS)[RESPONSE_BYTE_FIRMWARE_VERSION] == 0

    def test_limit_switch_config_starts_stock(self):
        assert FirmwareSimSerial(strict=True).limit_switch_config == STOCK_LIMIT_SWITCHES

    def test_limit_switch_config_stores_xyz_nibbles(self):
        sim = FirmwareSimSerial(strict=True, firmware_version=LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
        for axis, nibble in [(AXIS.X, 0x01), (AXIS.Y, 0x02), (AXIS.Z, 0x0F)]:
            response = _send(sim, CMD_SET.SET_LIMIT_SWITCH_CONFIG, axis, nibble)
            assert response[1] == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
        assert sim.limit_switch_config == {AXIS.X: 0x01, AXIS.Y: 0x02, AXIS.Z: 0x0F}
        assert sim.validation_errors == []

    @pytest.mark.parametrize("axis, nibble", REFUSED_LIMIT_SWITCH_CONFIGS)
    def test_limit_switch_config_refuses_other_axes_and_wide_nibbles(self, axis, nibble):
        sim = FirmwareSimSerial(strict=True, firmware_version=LIMIT_SWITCH_CONFIG_MIN_FIRMWARE)
        response = _send(sim, CMD_SET.SET_LIMIT_SWITCH_CONFIG, axis, nibble)  # must not raise
        assert response[1] == CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR
        assert sim.limit_switch_config == STOCK_LIMIT_SWITCHES
        assert sim.validation_errors == []

    @pytest.mark.parametrize("version", [None, OLD_FIRMWARE])
    @pytest.mark.parametrize("axis, nibble", [(AXIS.X, 0x0F), (AXIS.Z, 0x0F)] + REFUSED_LIMIT_SWITCH_CONFIGS)
    def test_limit_switch_config_is_a_no_op_before_its_firmware(self, version, axis, nibble):
        # Older firmware has no handler for 64: callback_default answers success and does nothing.
        if version is None:
            sim = FirmwareSimSerial(strict=True)  # the default, (0, 0)
        else:
            sim = FirmwareSimSerial(strict=True, firmware_version=version)
        response = _send(sim, CMD_SET.SET_LIMIT_SWITCH_CONFIG, axis, nibble)
        assert response[1] == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
        assert sim.limit_switch_config == STOCK_LIMIT_SWITCHES
        assert sim.validation_errors == []

    @pytest.mark.parametrize("button, switch, left, right", list(itertools.product([False, True], repeat=4)))
    def test_z_limits_set_byte_18_bits_2_and_3(self, button, switch, left, right):
        sim = FirmwareSimSerial(strict=True)
        sim.joystick_button, sim.switch, sim.z_limit_left, sim.z_limit_right = button, switch, left, right
        response = _send(sim, CMD_SET.TURN_OFF_ALL_PORTS)
        assert response[18] == button | switch << 1 | left << 2 | right << 3

    def test_z_limits_default_clear(self):
        sim = FirmwareSimSerial(strict=True)
        assert (sim.z_limit_left, sim.z_limit_right) == (False, False)
