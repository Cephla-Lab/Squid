"""Tests for multi-port illumination and filter wheel W2 protocol support.

Covers:
- Port-based naming constants and mapping functions
- SimSerial multi-port command handling
- Microcontroller multi-port methods
- Firmware version detection
- Illumination timeout configuration
- Response byte named constants
- Filter wheel W2 constants and SimSerial handling
"""

import pytest

from _def import (
    AXIS,
    CMD_SET,
    ILLUMINATION_CODE,
    ILLUMINATION_PORT,
    NUM_TIMEOUT_PORTS,
    RESPONSE_BYTE_FIRMWARE_VERSION,
    RESPONSE_BYTE_PORT_STATUS,
    source_code_to_port_index,
    port_index_to_source_code,
)
from squid.backend.drivers.stages.serial import SimSerial


class TestPortBasedNaming:
    """Test port-based illumination constants and mapping."""

    def test_d1_through_d5_aliases_exist(self):
        assert ILLUMINATION_CODE.ILLUMINATION_D1 == 11
        assert ILLUMINATION_CODE.ILLUMINATION_D2 == 12
        assert ILLUMINATION_CODE.ILLUMINATION_D3 == 14  # non-sequential!
        assert ILLUMINATION_CODE.ILLUMINATION_D4 == 13  # non-sequential!
        assert ILLUMINATION_CODE.ILLUMINATION_D5 == 15

    def test_legacy_wavelength_aliases_match(self):
        assert ILLUMINATION_CODE.ILLUMINATION_SOURCE_405NM == ILLUMINATION_CODE.ILLUMINATION_D1
        assert ILLUMINATION_CODE.ILLUMINATION_SOURCE_488NM == ILLUMINATION_CODE.ILLUMINATION_D2
        assert ILLUMINATION_CODE.ILLUMINATION_SOURCE_561NM == ILLUMINATION_CODE.ILLUMINATION_D3
        assert ILLUMINATION_CODE.ILLUMINATION_SOURCE_638NM == ILLUMINATION_CODE.ILLUMINATION_D4
        assert ILLUMINATION_CODE.ILLUMINATION_SOURCE_730NM == ILLUMINATION_CODE.ILLUMINATION_D5

    def test_port_indices(self):
        assert ILLUMINATION_PORT.D1 == 0
        assert ILLUMINATION_PORT.D2 == 1
        assert ILLUMINATION_PORT.D3 == 2
        assert ILLUMINATION_PORT.D4 == 3
        assert ILLUMINATION_PORT.D5 == 4

    def test_source_code_to_port_index(self):
        assert source_code_to_port_index(11) == 0  # D1
        assert source_code_to_port_index(12) == 1  # D2
        assert source_code_to_port_index(14) == 2  # D3 (non-sequential)
        assert source_code_to_port_index(13) == 3  # D4 (non-sequential)
        assert source_code_to_port_index(15) == 4  # D5
        assert source_code_to_port_index(99) == -1  # unknown

    def test_port_index_to_source_code(self):
        assert port_index_to_source_code(0) == 11  # D1
        assert port_index_to_source_code(1) == 12  # D2
        assert port_index_to_source_code(2) == 14  # D3
        assert port_index_to_source_code(3) == 13  # D4
        assert port_index_to_source_code(4) == 15  # D5
        assert port_index_to_source_code(99) == -1  # invalid

    def test_roundtrip_mapping(self):
        """Verify source_code -> port_index -> source_code roundtrip."""
        for source_code in [11, 12, 13, 14, 15]:
            port_index = source_code_to_port_index(source_code)
            assert port_index >= 0
            assert port_index_to_source_code(port_index) == source_code


class TestSimSerialMultiPort:
    """Test SimSerial handles multi-port illumination commands."""

    def _make_command(self, cmd_id, cmd_type, *data_bytes):
        """Build a command bytearray for SimSerial."""
        from crc import CrcCalculator, Crc8
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)
        cmd = bytearray(8)
        cmd[0] = cmd_id
        cmd[1] = cmd_type
        for i, b in enumerate(data_bytes):
            cmd[2 + i] = b
        cmd[-1] = crc_calc.calculate_checksum(cmd[:-1])
        return cmd

    def test_firmware_version_in_response(self):
        sim = SimSerial()
        sim.write(self._make_command(1, CMD_SET.TURN_OFF_ALL_PORTS))
        # Read 24-byte response
        response = bytearray()
        for _ in range(24):
            response.extend(sim.read(1))
        # Byte 22 should contain firmware version nibble-encoded
        version_byte = response[22]
        major = version_byte >> 4
        minor = version_byte & 0x0F
        assert major == 1
        assert minor == 1

    def test_turn_on_port(self):
        sim = SimSerial()
        assert not sim.port_is_on[0]
        sim.write(self._make_command(1, CMD_SET.TURN_ON_PORT, 0))
        # Drain response
        while sim.bytes_available():
            sim.read(1)
        assert sim.port_is_on[0]

    def test_turn_off_port(self):
        sim = SimSerial()
        sim.port_is_on[2] = True
        sim.write(self._make_command(1, CMD_SET.TURN_OFF_PORT, 2))
        while sim.bytes_available():
            sim.read(1)
        assert not sim.port_is_on[2]

    def test_set_port_intensity(self):
        sim = SimSerial()
        # Set port 1 to intensity value 0x8000
        sim.write(self._make_command(1, CMD_SET.SET_PORT_INTENSITY, 1, 0x80, 0x00))
        while sim.bytes_available():
            sim.read(1)
        assert sim.port_intensity[1] == 0x8000

    def test_set_port_illumination(self):
        sim = SimSerial()
        # Set port 0: intensity=0x4000, turn_on=1
        sim.write(self._make_command(1, CMD_SET.SET_PORT_ILLUMINATION, 0, 0x40, 0x00, 1))
        while sim.bytes_available():
            sim.read(1)
        assert sim.port_is_on[0]
        assert sim.port_intensity[0] == 0x4000

    def test_set_multi_port_mask(self):
        sim = SimSerial()
        # port_mask=0x0007 (D1,D2,D3), on_mask=0x0005 (D1,D3 on, D2 off)
        sim.write(self._make_command(1, CMD_SET.SET_MULTI_PORT_MASK, 0x00, 0x07, 0x00, 0x05))
        while sim.bytes_available():
            sim.read(1)
        assert sim.port_is_on[0]  # D1 on
        assert not sim.port_is_on[1]  # D2 off
        assert sim.port_is_on[2]  # D3 on

    def test_turn_off_all_ports(self):
        sim = SimSerial()
        sim.port_is_on[0] = True
        sim.port_is_on[3] = True
        sim.write(self._make_command(1, CMD_SET.TURN_OFF_ALL_PORTS))
        while sim.bytes_available():
            sim.read(1)
        assert all(not on for on in sim.port_is_on)

    def test_legacy_set_illumination_syncs_port_state(self):
        sim = SimSerial()
        # Legacy SET_ILLUMINATION: source=11 (D1), intensity=0x8000
        sim.write(self._make_command(1, CMD_SET.SET_ILLUMINATION, 11, 0x80, 0x00))
        while sim.bytes_available():
            sim.read(1)
        assert sim.port_intensity[0] == 0x8000  # D1 = port index 0

    def test_legacy_turn_on_syncs_port_state(self):
        sim = SimSerial()
        # First set the source via SET_ILLUMINATION
        sim.write(self._make_command(1, CMD_SET.SET_ILLUMINATION, 11, 0x80, 0x00))
        while sim.bytes_available():
            sim.read(1)
        # Then turn on
        sim.write(self._make_command(2, CMD_SET.TURN_ON_ILLUMINATION))
        while sim.bytes_available():
            sim.read(1)
        assert sim.port_is_on[0]  # D1 = port index 0

    def test_port_status_in_response(self):
        sim = SimSerial()
        sim.port_is_on[0] = True
        sim.port_is_on[2] = True
        sim.write(self._make_command(1, CMD_SET.TURN_OFF_ALL_PORTS))
        # After TURN_OFF_ALL, ports should be off, so port_status = 0
        response = bytearray()
        for _ in range(24):
            response.extend(sim.read(1))
        # Byte 19 = port status
        assert response[19] == 0

    def test_port_status_reflects_on_ports(self):
        sim = SimSerial()
        # Turn on D1 (port 0) and D3 (port 2)
        sim.write(self._make_command(1, CMD_SET.TURN_ON_PORT, 0))
        while sim.bytes_available():
            sim.read(1)
        sim.write(self._make_command(2, CMD_SET.TURN_ON_PORT, 2))
        response = bytearray()
        for _ in range(24):
            response.extend(sim.read(1))
        # port_status should have bits 0 and 2 set = 0b00000101 = 5
        assert response[19] == 5

    def test_illumination_timeout_command(self):
        sim = SimSerial()
        # Set timeout to 5000ms (0x00001388)
        sim.write(self._make_command(1, CMD_SET.SET_ILLUMINATION_TIMEOUT, 0x00, 0x00, 0x13, 0x88))
        while sim.bytes_available():
            sim.read(1)
        assert sim.illumination_timeout_ms == 5000


class TestCmdSetConstants:
    """Test that new CMD_SET constants exist with expected values."""

    def test_multi_port_cmd_values(self):
        assert CMD_SET.SET_PORT_INTENSITY == 34
        assert CMD_SET.TURN_ON_PORT == 35
        assert CMD_SET.TURN_OFF_PORT == 36
        assert CMD_SET.SET_PORT_ILLUMINATION == 37
        assert CMD_SET.SET_MULTI_PORT_MASK == 38
        assert CMD_SET.TURN_OFF_ALL_PORTS == 39
        assert CMD_SET.SET_ILLUMINATION_TIMEOUT == 40
        # Verify no collision with existing commands
        assert CMD_SET.SET_PIN_LEVEL == 41

    def test_w2_cmd_values(self):
        assert CMD_SET.MOVE_W2 == 19
        assert CMD_SET.INITFILTERWHEEL_W2 == 252

    def test_w2_axis_constant(self):
        assert AXIS.W2 == 6


class TestResponseByteConstants:
    """Test that response byte index constants are correct."""

    def test_response_byte_port_status(self):
        assert RESPONSE_BYTE_PORT_STATUS == 19

    def test_response_byte_firmware_version(self):
        assert RESPONSE_BYTE_FIRMWARE_VERSION == 22

    def test_simserial_uses_correct_bytes(self):
        """Verify SimSerial response encodes version and port status at correct offsets."""
        sim = SimSerial()
        sim.port_is_on[0] = True  # D1 on
        sim.write(self._make_command(1, CMD_SET.TURN_ON_PORT, 0))
        response = bytearray()
        for _ in range(24):
            response.extend(sim.read(1))
        # Port status at RESPONSE_BYTE_PORT_STATUS
        assert response[RESPONSE_BYTE_PORT_STATUS] & 1 == 1
        # Firmware version at RESPONSE_BYTE_FIRMWARE_VERSION
        version_byte = response[RESPONSE_BYTE_FIRMWARE_VERSION]
        assert version_byte >> 4 >= 1  # major >= 1

    def _make_command(self, cmd_id, cmd_type, *data_bytes):
        from crc import CrcCalculator, Crc8
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)
        cmd = bytearray(8)
        cmd[0] = cmd_id
        cmd[1] = cmd_type
        for i, b in enumerate(data_bytes):
            cmd[2 + i] = b
        cmd[-1] = crc_calc.calculate_checksum(cmd[:-1])
        return cmd


class TestSimSerialFilterWheelW2:
    """Test SimSerial handles filter wheel W2 commands."""

    def _make_command(self, cmd_id, cmd_type, *data_bytes):
        from crc import CrcCalculator, Crc8
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)
        cmd = bytearray(8)
        cmd[0] = cmd_id
        cmd[1] = cmd_type
        for i, b in enumerate(data_bytes):
            cmd[2 + i] = b
        cmd[-1] = crc_calc.calculate_checksum(cmd[:-1])
        return cmd

    def _make_move_command(self, cmd_id, cmd_type, usteps):
        """Build a move command with 4-byte signed position payload."""
        import struct
        from crc import CrcCalculator, Crc8
        crc_calc = CrcCalculator(Crc8.CCITT, table_based=True)
        cmd = bytearray(8)
        cmd[0] = cmd_id
        cmd[1] = cmd_type
        # Pack as unsigned 32-bit big-endian (two's complement for negative)
        if usteps < 0:
            usteps = (1 << 32) + usteps
        cmd[2] = (usteps >> 24) & 0xFF
        cmd[3] = (usteps >> 16) & 0xFF
        cmd[4] = (usteps >> 8) & 0xFF
        cmd[5] = usteps & 0xFF
        cmd[-1] = crc_calc.calculate_checksum(cmd[:-1])
        return cmd

    def test_move_w2_positive(self):
        sim = SimSerial()
        assert sim.w2 == 0
        sim.write(self._make_move_command(1, CMD_SET.MOVE_W2, 1000))
        while sim.bytes_available():
            sim.read(1)
        assert sim.w2 == 1000

    def test_move_w2_negative(self):
        sim = SimSerial()
        sim.w2 = 5000
        sim.write(self._make_move_command(1, CMD_SET.MOVE_W2, -2000))
        while sim.bytes_available():
            sim.read(1)
        assert sim.w2 == 3000

    def test_move_w_independent_of_w2(self):
        sim = SimSerial()
        sim.write(self._make_move_command(1, CMD_SET.MOVE_W, 500))
        while sim.bytes_available():
            sim.read(1)
        sim.write(self._make_move_command(2, CMD_SET.MOVE_W2, 1000))
        while sim.bytes_available():
            sim.read(1)
        assert sim.w == 500
        assert sim.w2 == 1000

    def test_home_w2(self):
        sim = SimSerial()
        sim.w2 = 5000
        # HOME_OR_ZERO with axis=W2 (6), direction=1 (backward)
        sim.write(self._make_command(1, CMD_SET.HOME_OR_ZERO, AXIS.W2, 1))
        while sim.bytes_available():
            sim.read(1)
        assert sim.w2 == 0

    def test_zero_w2(self):
        sim = SimSerial()
        sim.w2 = 3000
        # HOME_OR_ZERO with axis=W2, mode=ZERO(2)
        sim.write(self._make_command(1, CMD_SET.HOME_OR_ZERO, AXIS.W2, 2))
        while sim.bytes_available():
            sim.read(1)
        assert sim.w2 == 0

    def test_home_w_does_not_affect_w2(self):
        sim = SimSerial()
        sim.w = 1000
        sim.w2 = 2000
        sim.write(self._make_command(1, CMD_SET.HOME_OR_ZERO, AXIS.W, 1))
        while sim.bytes_available():
            sim.read(1)
        assert sim.w == 0
        assert sim.w2 == 2000
