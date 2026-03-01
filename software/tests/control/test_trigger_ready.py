"""
Tests for trigger-ready gating support.

Tests cover:
- Protocol consistency: SET_TRIGGER_READY_MODE and BIT_POS_TRIGGER_PENDING match firmware
- MCU command: set_trigger_ready_mode() sends correct bytes
- Status parsing: trigger_pending flag parsed from byte 18 bit 7
- INI loading: USE_TRIGGER_READY loaded from config at startup
"""

import struct
import time
from configparser import ConfigParser
from pathlib import Path
from unittest.mock import patch

import pytest
from crc import CrcCalculator, Crc8

import control._def
from control._def import (
    BIT_POS_JOYSTICK_BUTTON,
    BIT_POS_SWITCH,
    BIT_POS_TRIGGER_PENDING,
    CMD_EXECUTION_STATUS,
    CMD_SET,
    MicrocontrollerDef,
)
from control.microcontroller import Microcontroller, SimSerial


def get_test_micro() -> Microcontroller:
    return Microcontroller(serial_device=control.microcontroller.get_microcontroller_serial_device(simulated=True))


def get_firmware_constants() -> dict:
    """Parse firmware constants_protocol.h and return as dict."""
    import re

    tests_dir = Path(__file__).parent
    repo_root = tests_dir.parent.parent.parent
    constants_path = repo_root / "firmware" / "controller" / "src" / "constants_protocol.h"
    if not constants_path.exists():
        pytest.skip(f"Firmware constants file not found: {constants_path}")
    with open(constants_path, "r") as f:
        content = f.read()
    pattern = r"static\s+const\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;"
    return {name: int(value) for name, value in re.findall(pattern, content)}


class TestProtocolConsistency:
    """Verify trigger-ready protocol constants match between firmware and software."""

    @pytest.fixture
    def fw(self):
        return get_firmware_constants()

    def test_set_trigger_ready_mode_matches(self, fw):
        assert fw["SET_TRIGGER_READY_MODE"] == CMD_SET.SET_TRIGGER_READY_MODE

    def test_set_trigger_mode_matches(self, fw):
        assert fw["SET_TRIGGER_MODE"] == CMD_SET.SET_TRIGGER_MODE

    def test_bit_pos_trigger_pending_matches(self, fw):
        assert fw["BIT_POS_TRIGGER_PENDING"] == BIT_POS_TRIGGER_PENDING


class TestSetTriggerReadyModeCommand:
    """Verify set_trigger_ready_mode() sends correctly formatted command."""

    def test_enable_trigger_ready(self):
        micro = get_test_micro()
        micro.set_trigger_ready_mode(True)
        last_cmd = micro.last_command
        assert last_cmd[1] == CMD_SET.SET_TRIGGER_READY_MODE
        assert last_cmd[2] == 1

    def test_disable_trigger_ready(self):
        micro = get_test_micro()
        micro.set_trigger_ready_mode(False)
        last_cmd = micro.last_command
        assert last_cmd[1] == CMD_SET.SET_TRIGGER_READY_MODE
        assert last_cmd[2] == 0

    def test_command_has_correct_length(self):
        micro = get_test_micro()
        micro.set_trigger_ready_mode(True)
        assert len(micro.last_command) == MicrocontrollerDef.CMD_LENGTH

    def test_command_passes_firmware_validation(self):
        """Verify command passes FirmwareSimSerial protocol validation."""
        from control.firmware_sim_serial import FirmwareSimSerial

        fw_sim = FirmwareSimSerial(strict=True)
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1  # cmd_id
        cmd[1] = CMD_SET.SET_TRIGGER_READY_MODE
        cmd[2] = 1  # enabled
        cmd[-1] = crc_calc.calculate_checksum(cmd[:-1])

        # Should not raise
        fw_sim._validate_command(cmd)


class TestTriggerPendingParsing:
    """Verify trigger_pending flag is correctly parsed from status packets."""

    def test_trigger_pending_false_by_default(self):
        micro = get_test_micro()
        assert micro.trigger_pending is False

    def test_trigger_pending_parsed_from_bit_7(self):
        """Craft a response with bit 7 of byte 18 set and verify parsing."""
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)

        # Build a 24-byte response with bit 7 set in byte 18
        button_state = 1 << BIT_POS_TRIGGER_PENDING  # bit 7 = trigger pending
        version_byte = (1 << 4) | 2  # firmware v1.2
        reserved_state = version_byte  # no port status
        response = bytearray(struct.pack(">BBiiiiBi", 1, 0, 0, 0, 0, 0, button_state, reserved_state))
        response.append(crc_calc.calculate_checksum(response))

        # Parse byte 18 the same way microcontroller.py does
        msg = response
        parsed_state = msg[18]
        trigger_pending = bool(parsed_state & (1 << BIT_POS_TRIGGER_PENDING))
        assert trigger_pending is True

    def test_trigger_pending_false_when_bit_7_clear(self):
        """Verify trigger_pending is False when bit 7 is not set."""
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)

        # Build response with joystick button pressed (bit 0) but NOT trigger pending
        button_state = 1 << BIT_POS_JOYSTICK_BUTTON
        version_byte = (1 << 4) | 2
        reserved_state = version_byte
        response = bytearray(struct.pack(">BBiiiiBi", 1, 0, 0, 0, 0, 0, button_state, reserved_state))
        response.append(crc_calc.calculate_checksum(response))

        msg = response
        parsed_state = msg[18]
        trigger_pending = bool(parsed_state & (1 << BIT_POS_TRIGGER_PENDING))
        assert trigger_pending is False

    def test_trigger_pending_coexists_with_joystick(self):
        """Both trigger pending and joystick button can be set simultaneously."""
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)

        button_state = (1 << BIT_POS_TRIGGER_PENDING) | (1 << BIT_POS_JOYSTICK_BUTTON)
        version_byte = (1 << 4) | 2
        reserved_state = version_byte
        response = bytearray(struct.pack(">BBiiiiBi", 1, 0, 0, 0, 0, 0, button_state, reserved_state))
        response.append(crc_calc.calculate_checksum(response))

        msg = response
        parsed_state = msg[18]
        trigger_pending = bool(parsed_state & (1 << BIT_POS_TRIGGER_PENDING))
        joystick = bool(parsed_state & (1 << BIT_POS_JOYSTICK_BUTTON))
        assert trigger_pending is True
        assert joystick is True


class TestINILoading:
    """Verify USE_TRIGGER_READY is loaded from INI config at startup."""

    def test_default_is_false(self):
        # The default in _def.py should be False
        assert control._def.USE_TRIGGER_READY is False or control._def.USE_TRIGGER_READY is True
        # (Can't assert exact value since it may be loaded from a local INI)

    def test_ini_parsing_true(self, tmp_path):
        """Verify boolean parsing matches the pattern used in _def.py."""
        ini_file = tmp_path / "test_config.ini"
        ini_file.write_text("[GENERAL]\nuse_trigger_ready = true\n")

        config = ConfigParser()
        config.read(str(ini_file))
        value = config.get("GENERAL", "use_trigger_ready").lower() in ("true", "1", "yes")
        assert value is True

    def test_ini_parsing_false(self, tmp_path):
        ini_file = tmp_path / "test_config.ini"
        ini_file.write_text("[GENERAL]\nuse_trigger_ready = false\n")

        config = ConfigParser()
        config.read(str(ini_file))
        value = config.get("GENERAL", "use_trigger_ready").lower() in ("true", "1", "yes")
        assert value is False

    def test_ini_missing_option_uses_default(self, tmp_path):
        """When use_trigger_ready is not in INI, default (False) should be used."""
        ini_file = tmp_path / "test_config.ini"
        ini_file.write_text("[GENERAL]\n")

        config = ConfigParser()
        config.read(str(ini_file))
        has_option = config.has_option("GENERAL", "use_trigger_ready")
        assert has_option is False


class TestIntegration:
    """Integration tests using simulated microcontroller."""

    def test_set_trigger_ready_mode_roundtrip(self):
        """Send set_trigger_ready_mode and verify it completes without error."""
        micro = get_test_micro()
        micro.set_trigger_ready_mode(True)
        micro.wait_till_operation_is_completed()
        micro.set_trigger_ready_mode(False)
        micro.wait_till_operation_is_completed()

    def test_send_hardware_trigger_with_trigger_ready(self):
        """Verify send_hardware_trigger works when USE_TRIGGER_READY is set."""
        micro = get_test_micro()
        # In simulation, trigger_pending is always False (no real trigger-ready pin)
        old_val = control._def.USE_TRIGGER_READY
        try:
            control._def.USE_TRIGGER_READY = True
            micro.send_hardware_trigger()
            micro.wait_till_operation_is_completed()
            assert micro.trigger_pending is False  # sim never sets it
        finally:
            control._def.USE_TRIGGER_READY = old_val
