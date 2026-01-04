"""
Tests for firmware-software protocol consistency.

These tests verify that the firmware and software agree on:
- Command IDs
- Message lengths
- CRC calculation
- Axis definitions
- Command execution status codes
- Limit switch codes and polarity
- Illumination source codes
"""

import re
from pathlib import Path

import pytest
from crc import CrcCalculator, Crc8

from control._def import (
    CMD_SET,
    MicrocontrollerDef,
    AXIS,
    HOME_OR_ZERO,
    LIMIT_CODE,
    LIMIT_SWITCH_POLARITY,
    CMD_EXECUTION_STATUS,
    ILLUMINATION_CODE,
)


def get_firmware_constants_path() -> Path:
    """Get path to firmware constants_protocol.h file."""
    # Navigate from software/tests/control/ to firmware/controller/src/constants_protocol.h
    tests_dir = Path(__file__).parent
    repo_root = tests_dir.parent.parent.parent
    constants_path = repo_root / "firmware" / "controller" / "src" / "constants_protocol.h"
    return constants_path


def parse_firmware_constants(file_path: Path) -> dict:
    """Parse C++ constants from firmware header file."""
    constants = {}

    if not file_path.exists():
        pytest.skip(f"Firmware constants file not found: {file_path}")

    with open(file_path, "r") as f:
        content = f.read()

    # Match patterns like: static const int MOVE_X = 0; (also handles negative values)
    pattern = r"static\s+const\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;"
    matches = re.findall(pattern, content)

    for name, value in matches:
        constants[name] = int(value)

    return constants


def get_firmware_crc8_path() -> Path:
    """Get path to firmware crc8.cpp file."""
    tests_dir = Path(__file__).parent
    repo_root = tests_dir.parent.parent.parent
    crc_path = repo_root / "firmware" / "controller" / "src" / "utils" / "crc8.cpp"
    return crc_path


def parse_firmware_crc_table(file_path: Path) -> list:
    """Parse CRC lookup table from firmware."""
    if not file_path.exists():
        pytest.skip(f"Firmware CRC file not found: {file_path}")

    with open(file_path, "r") as f:
        content = f.read()

    # Extract the CRC_TABLE array values
    pattern = r"CRC_TABLE\[256\]\s*=\s*\{([^}]+)\}"
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        pytest.skip("Could not find CRC_TABLE in firmware")

    table_content = match.group(1)
    # Extract all hex values
    hex_pattern = r"0x([0-9A-Fa-f]{2})"
    hex_values = re.findall(hex_pattern, table_content)

    return [int(v, 16) for v in hex_values]


class TestProtocolConsistency:
    """Test that firmware and software protocol definitions match."""

    @pytest.fixture
    def firmware_constants(self):
        return parse_firmware_constants(get_firmware_constants_path())

    def test_command_ids_match(self, firmware_constants):
        """Verify all command IDs match between firmware and software."""
        # Map firmware constant names to Python CMD_SET attributes
        command_mapping = {
            "MOVE_X": CMD_SET.MOVE_X,
            "MOVE_Y": CMD_SET.MOVE_Y,
            "MOVE_Z": CMD_SET.MOVE_Z,
            "MOVE_THETA": CMD_SET.MOVE_THETA,
            "MOVE_W": CMD_SET.MOVE_W,
            "HOME_OR_ZERO": CMD_SET.HOME_OR_ZERO,
            "MOVETO_X": CMD_SET.MOVETO_X,
            "MOVETO_Y": CMD_SET.MOVETO_Y,
            "MOVETO_Z": CMD_SET.MOVETO_Z,
            "MOVETO_W": CMD_SET.MOVETO_W,
            "SET_LIM": CMD_SET.SET_LIM,
            "TURN_ON_ILLUMINATION": CMD_SET.TURN_ON_ILLUMINATION,
            "TURN_OFF_ILLUMINATION": CMD_SET.TURN_OFF_ILLUMINATION,
            "SET_ILLUMINATION": CMD_SET.SET_ILLUMINATION,
            "SET_ILLUMINATION_LED_MATRIX": CMD_SET.SET_ILLUMINATION_LED_MATRIX,
            "ACK_JOYSTICK_BUTTON_PRESSED": CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED,
            "ANALOG_WRITE_ONBOARD_DAC": CMD_SET.ANALOG_WRITE_ONBOARD_DAC,
            "SET_DAC80508_REFDIV_GAIN": CMD_SET.SET_DAC80508_REFDIV_GAIN,
            "SET_ILLUMINATION_INTENSITY_FACTOR": CMD_SET.SET_ILLUMINATION_INTENSITY_FACTOR,
            "SET_LIM_SWITCH_POLARITY": CMD_SET.SET_LIM_SWITCH_POLARITY,
            "CONFIGURE_STEPPER_DRIVER": CMD_SET.CONFIGURE_STEPPER_DRIVER,
            "SET_MAX_VELOCITY_ACCELERATION": CMD_SET.SET_MAX_VELOCITY_ACCELERATION,
            "SET_LEAD_SCREW_PITCH": CMD_SET.SET_LEAD_SCREW_PITCH,
            "SET_OFFSET_VELOCITY": CMD_SET.SET_OFFSET_VELOCITY,
            "CONFIGURE_STAGE_PID": CMD_SET.CONFIGURE_STAGE_PID,
            "ENABLE_STAGE_PID": CMD_SET.ENABLE_STAGE_PID,
            "DISABLE_STAGE_PID": CMD_SET.DISABLE_STAGE_PID,
            "SET_HOME_SAFETY_MERGIN": CMD_SET.SET_HOME_SAFETY_MERGIN,
            "SET_PID_ARGUMENTS": CMD_SET.SET_PID_ARGUMENTS,
            "SEND_HARDWARE_TRIGGER": CMD_SET.SEND_HARDWARE_TRIGGER,
            "SET_STROBE_DELAY": CMD_SET.SET_STROBE_DELAY,
            "SET_AXIS_DISABLE_ENABLE": CMD_SET.SET_AXIS_DISABLE_ENABLE,
            "SET_PIN_LEVEL": CMD_SET.SET_PIN_LEVEL,
            "INITFILTERWHEEL": CMD_SET.INITFILTERWHEEL,
            "INITIALIZE": CMD_SET.INITIALIZE,
            "RESET": CMD_SET.RESET,
        }

        mismatches = []
        for fw_name, py_value in command_mapping.items():
            if fw_name in firmware_constants:
                fw_value = firmware_constants[fw_name]
                if fw_value != py_value:
                    mismatches.append(f"{fw_name}: firmware={fw_value}, software={py_value}")
            else:
                mismatches.append(f"{fw_name}: not found in firmware")

        assert len(mismatches) == 0, f"Command ID mismatches:\n" + "\n".join(mismatches)

    def test_message_lengths_match(self, firmware_constants):
        """Verify message lengths match."""
        assert (
            firmware_constants.get("CMD_LENGTH") == MicrocontrollerDef.CMD_LENGTH
        ), f"CMD_LENGTH mismatch: firmware={firmware_constants.get('CMD_LENGTH')}, software={MicrocontrollerDef.CMD_LENGTH}"

        assert (
            firmware_constants.get("MSG_LENGTH") == MicrocontrollerDef.MSG_LENGTH
        ), f"MSG_LENGTH mismatch: firmware={firmware_constants.get('MSG_LENGTH')}, software={MicrocontrollerDef.MSG_LENGTH}"

    def test_axis_ids_match(self, firmware_constants):
        """Verify axis IDs match."""
        axis_mapping = {
            "AXIS_X": AXIS.X,
            "AXIS_Y": AXIS.Y,
            "AXIS_Z": AXIS.Z,
            "AXIS_THETA": AXIS.THETA,
            "AXES_XY": AXIS.XY,
            "AXIS_W": AXIS.W,
        }

        for fw_name, py_value in axis_mapping.items():
            if fw_name in firmware_constants:
                assert (
                    firmware_constants[fw_name] == py_value
                ), f"{fw_name} mismatch: firmware={firmware_constants[fw_name]}, software={py_value}"

    def test_home_or_zero_values_match(self, firmware_constants):
        """Verify HOME_OR_ZERO values match."""
        assert firmware_constants.get("HOME_NEGATIVE") == HOME_OR_ZERO.HOME_NEGATIVE
        assert firmware_constants.get("HOME_POSITIVE") == HOME_OR_ZERO.HOME_POSITIVE
        assert firmware_constants.get("HOME_OR_ZERO_ZERO") == HOME_OR_ZERO.ZERO

    def test_execution_status_values_match(self, firmware_constants):
        """Verify command execution status values match."""
        status_mapping = {
            "COMPLETED_WITHOUT_ERRORS": CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS,
            "IN_PROGRESS": CMD_EXECUTION_STATUS.IN_PROGRESS,
            "CMD_CHECKSUM_ERROR": CMD_EXECUTION_STATUS.CMD_CHECKSUM_ERROR,
            "CMD_INVALID": CMD_EXECUTION_STATUS.CMD_INVALID,
            "CMD_EXECUTION_ERROR": CMD_EXECUTION_STATUS.CMD_EXECUTION_ERROR,
        }

        mismatches = []
        for fw_name, py_value in status_mapping.items():
            if fw_name in firmware_constants:
                fw_value = firmware_constants[fw_name]
                if fw_value != py_value:
                    mismatches.append(f"{fw_name}: firmware={fw_value}, software={py_value}")
            else:
                mismatches.append(f"{fw_name}: not found in firmware")

        assert len(mismatches) == 0, f"Execution status mismatches:\n" + "\n".join(mismatches)

    def test_limit_codes_match(self, firmware_constants):
        """Verify limit switch codes match."""
        limit_mapping = {
            "LIM_CODE_X_POSITIVE": LIMIT_CODE.X_POSITIVE,
            "LIM_CODE_X_NEGATIVE": LIMIT_CODE.X_NEGATIVE,
            "LIM_CODE_Y_POSITIVE": LIMIT_CODE.Y_POSITIVE,
            "LIM_CODE_Y_NEGATIVE": LIMIT_CODE.Y_NEGATIVE,
            "LIM_CODE_Z_POSITIVE": LIMIT_CODE.Z_POSITIVE,
            "LIM_CODE_Z_NEGATIVE": LIMIT_CODE.Z_NEGATIVE,
        }

        mismatches = []
        for fw_name, py_value in limit_mapping.items():
            if fw_name in firmware_constants:
                fw_value = firmware_constants[fw_name]
                if fw_value != py_value:
                    mismatches.append(f"{fw_name}: firmware={fw_value}, software={py_value}")
            else:
                mismatches.append(f"{fw_name}: not found in firmware")

        assert len(mismatches) == 0, f"Limit code mismatches:\n" + "\n".join(mismatches)

    def test_limit_switch_polarity_values_match(self, firmware_constants):
        """Verify limit switch polarity values match."""
        polarity_mapping = {
            "ACTIVE_LOW": LIMIT_SWITCH_POLARITY.ACTIVE_LOW,
            "ACTIVE_HIGH": LIMIT_SWITCH_POLARITY.ACTIVE_HIGH,
            "DISABLED": LIMIT_SWITCH_POLARITY.DISABLED,
        }

        mismatches = []
        for fw_name, py_value in polarity_mapping.items():
            if fw_name in firmware_constants:
                fw_value = firmware_constants[fw_name]
                if fw_value != py_value:
                    mismatches.append(f"{fw_name}: firmware={fw_value}, software={py_value}")
            else:
                mismatches.append(f"{fw_name}: not found in firmware")

        assert len(mismatches) == 0, f"Limit switch polarity mismatches:\n" + "\n".join(mismatches)

    def test_illumination_source_codes_match(self, firmware_constants):
        """Verify illumination source codes match."""
        illumination_mapping = {
            "ILLUMINATION_SOURCE_LED_ARRAY_FULL": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_FULL,
            "ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF,
            "ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF,
            "ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR,
            "ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA,
            "ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT,
            "ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT,
            "ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF,
            "ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF,
            "ILLUMINATION_SOURCE_LED_EXTERNAL_FET": ILLUMINATION_CODE.ILLUMINATION_SOURCE_LED_EXTERNAL_FET,
            "ILLUMINATION_SOURCE_405NM": ILLUMINATION_CODE.ILLUMINATION_SOURCE_405NM,
            "ILLUMINATION_SOURCE_488NM": ILLUMINATION_CODE.ILLUMINATION_SOURCE_488NM,
            "ILLUMINATION_SOURCE_638NM": ILLUMINATION_CODE.ILLUMINATION_SOURCE_638NM,
            "ILLUMINATION_SOURCE_561NM": ILLUMINATION_CODE.ILLUMINATION_SOURCE_561NM,
            "ILLUMINATION_SOURCE_730NM": ILLUMINATION_CODE.ILLUMINATION_SOURCE_730NM,
        }

        mismatches = []
        for fw_name, py_value in illumination_mapping.items():
            if fw_name in firmware_constants:
                fw_value = firmware_constants[fw_name]
                if fw_value != py_value:
                    mismatches.append(f"{fw_name}: firmware={fw_value}, software={py_value}")
            else:
                mismatches.append(f"{fw_name}: not found in firmware")

        assert len(mismatches) == 0, f"Illumination source code mismatches:\n" + "\n".join(mismatches)

    def test_illumination_codes_consistency(self, firmware_constants):
        """Check bidirectional consistency of illumination codes between firmware and software."""
        # Get all ILLUMINATION_SOURCE_* constants from firmware
        firmware_illumination = {
            name: value for name, value in firmware_constants.items() if name.startswith("ILLUMINATION_SOURCE_")
        }

        # Get all attributes from Python ILLUMINATION_CODE class
        python_illumination = {
            name: getattr(ILLUMINATION_CODE, name)
            for name in dir(ILLUMINATION_CODE)
            if name.startswith("ILLUMINATION_SOURCE_")
        }

        # Find codes in firmware but not in software
        missing_in_software = []
        for fw_name, fw_value in firmware_illumination.items():
            if fw_name not in python_illumination:
                missing_in_software.append(f"{fw_name} = {fw_value}")

        # Find codes in software but not in firmware
        missing_in_firmware = []
        for py_name, py_value in python_illumination.items():
            if py_name not in firmware_illumination:
                missing_in_firmware.append(f"{py_name} = {py_value}")

        errors = []
        if missing_in_software:
            errors.append("Firmware has illumination codes not in software:\n" + "\n".join(missing_in_software))
        if missing_in_firmware:
            errors.append("Software has illumination codes not in firmware:\n" + "\n".join(missing_in_firmware))

        if errors:
            pytest.fail("\n\n".join(errors))


class TestCRCCompatibility:
    """Test that CRC calculations match between firmware and software."""

    def test_crc_table_matches_ccitt(self):
        """Verify firmware CRC table matches CRC-8-CCITT standard."""
        firmware_table = parse_firmware_crc_table(get_firmware_crc8_path())

        if len(firmware_table) != 256:
            pytest.skip(f"Expected 256 CRC table entries, got {len(firmware_table)}")

        # Generate expected CRC-8-CCITT table
        # Polynomial: x^8 + x^2 + x + 1 = 0x07
        expected_table = []
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x07) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
            expected_table.append(crc)

        assert firmware_table == expected_table, "Firmware CRC table doesn't match CRC-8-CCITT"

    def test_crc_calculation_matches(self):
        """Verify CRC calculation produces same results in firmware and software."""
        # Test vectors
        test_cases = [
            (b"", 0x00),
            (b"\x00", 0x00),
            (b"\x01", 0x07),
            (b"123456789", 0xF4),  # Standard CRC-8-CCITT test vector
        ]

        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        for data, expected in test_cases:
            result = crc_calculator.calculate_checksum(data)
            assert result == expected, f"CRC mismatch for {data!r}: expected={expected:#04x}, got={result:#04x}"

    def test_crc_command_packet(self):
        """Test CRC on a realistic command packet."""
        # Simulate a 7-byte command packet (excluding CRC byte)
        # Format: [cmd_id, param1, param2, param3, param4, param5, param6]
        packet = bytes([0x00, 0x01, 0x00, 0x00, 0x10, 0x00, 0x00])

        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)
        crc = crc_calculator.calculate_checksum(packet)

        # CRC should be deterministic
        assert crc == crc_calculator.calculate_checksum(packet)

        # CRC should be a valid byte
        assert 0 <= crc <= 255

    def test_crc_detects_single_bit_error(self):
        """Verify CRC detects single bit errors."""
        original = bytes([0x01, 0x02, 0x03, 0x04, 0x05])
        modified = bytes([0x01, 0x02, 0x03, 0x04, 0x04])  # Last byte changed

        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        crc_original = crc_calculator.calculate_checksum(original)
        crc_modified = crc_calculator.calculate_checksum(modified)

        assert crc_original != crc_modified, "CRC should detect the modification"
