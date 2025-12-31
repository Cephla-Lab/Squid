"""
Integration tests for microcontroller communication.

These tests verify that:
- Command packets are correctly structured (8 bytes with CRC)
- Response packets are correctly parsed (24 bytes)
- The Microcontroller class correctly uses SimSerial
- Payload encoding/decoding works for signed integers
"""

import struct

from crc import CrcCalculator, Crc8

from control._def import (
    CMD_SET,
    MicrocontrollerDef,
    AXIS,
    HOME_OR_ZERO,
    CMD_EXECUTION_STATUS,
    BIT_POS_JOYSTICK_BUTTON,
    BIT_POS_SWITCH,
)
from control.microcontroller import SimSerial, Microcontroller


class TestCommandPacketStructure:
    """Test that command packets are correctly structured."""

    def test_command_length_is_8_bytes(self):
        """Command packets should be exactly 8 bytes."""
        assert MicrocontrollerDef.CMD_LENGTH == 8

    def test_response_length_is_24_bytes(self):
        """Response packets should be exactly 24 bytes."""
        assert MicrocontrollerDef.MSG_LENGTH == 24

    def test_command_packet_format(self):
        """Test command packet byte layout: [cmd_id, cmd_code, params..., crc]"""
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        # Build a command packet manually
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1  # command ID
        cmd[1] = CMD_SET.MOVE_X  # command code
        cmd[2] = 0x00  # param bytes (position high)
        cmd[3] = 0x00
        cmd[4] = 0x10  # 4096 microsteps
        cmd[5] = 0x00
        cmd[6] = 0x00  # reserved
        cmd[7] = crc_calculator.calculate_checksum(cmd[:-1])

        assert len(cmd) == 8
        assert cmd[0] == 1  # cmd_id in byte 0
        assert cmd[1] == CMD_SET.MOVE_X  # cmd_code in byte 1
        # CRC should be valid
        assert crc_calculator.calculate_checksum(cmd[:-1]) == cmd[-1]


class TestResponsePacketParsing:
    """Test that response packets are correctly parsed."""

    def test_response_packet_structure(self):
        """Test response packet byte layout matches firmware spec."""
        # Response format from firmware:
        # - command ID (1 byte)
        # - execution status (1 byte)
        # - X pos (4 bytes, signed big-endian)
        # - Y pos (4 bytes)
        # - Z pos (4 bytes)
        # - Theta (4 bytes)
        # - buttons and switches (1 byte)
        # - reserved (4 bytes)
        # - CRC (1 byte)
        # Total: 24 bytes

        response = SimSerial.response_bytes_for(
            command_id=5,
            execution_status=CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS,
            x=1000,
            y=2000,
            z=-500,
            theta=0,
            joystick_button=False,
            switch=False,
        )

        assert len(response) == MicrocontrollerDef.MSG_LENGTH
        assert response[0] == 5  # command ID
        assert response[1] == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS

    def test_response_packet_crc(self):
        """Test that response packets have valid CRC."""
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        response = SimSerial.response_bytes_for(
            command_id=1,
            execution_status=CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS,
            x=0,
            y=0,
            z=0,
            theta=0,
            joystick_button=False,
            switch=False,
        )

        # CRC should be valid
        calculated_crc = crc_calculator.calculate_checksum(response[:-1])
        assert calculated_crc == response[-1]

    def test_response_position_encoding(self):
        """Test that positions are correctly encoded in response."""
        response = SimSerial.response_bytes_for(
            command_id=1,
            execution_status=CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS,
            x=1000,
            y=-2000,
            z=500,
            theta=-100,
            joystick_button=False,
            switch=False,
        )

        # Parse positions using struct (big-endian signed int)
        x = struct.unpack(">i", bytes(response[2:6]))[0]
        y = struct.unpack(">i", bytes(response[6:10]))[0]
        z = struct.unpack(">i", bytes(response[10:14]))[0]
        theta = struct.unpack(">i", bytes(response[14:18]))[0]

        assert x == 1000
        assert y == -2000
        assert z == 500
        assert theta == -100

    def test_response_button_state_encoding(self):
        """Test button state encoding in response."""
        # Test joystick button pressed
        response = SimSerial.response_bytes_for(
            command_id=1,
            execution_status=CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS,
            x=0,
            y=0,
            z=0,
            theta=0,
            joystick_button=True,
            switch=False,
        )
        button_byte = response[18]
        assert (button_byte & (1 << BIT_POS_JOYSTICK_BUTTON)) != 0

        # Test switch on
        response = SimSerial.response_bytes_for(
            command_id=1,
            execution_status=CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS,
            x=0,
            y=0,
            z=0,
            theta=0,
            joystick_button=False,
            switch=True,
        )
        button_byte = response[18]
        assert (button_byte & (1 << BIT_POS_SWITCH)) != 0


class TestPayloadEncoding:
    """Test payload encoding/decoding for signed integers."""

    def test_int_to_payload_positive(self):
        """Test encoding positive integers."""
        # 4-byte positive number
        payload = Microcontroller._int_to_payload(1000, 4)
        assert payload == 1000

    def test_int_to_payload_negative(self):
        """Test encoding negative integers using two's complement."""
        # 4-byte negative number (-1000)
        payload = Microcontroller._int_to_payload(-1000, 4)
        # Two's complement: 2^32 - 1000
        expected = 2**32 - 1000
        assert payload == expected

    def test_payload_to_int_positive(self):
        """Test decoding positive integers from bytes."""
        # 1000 as 4 big-endian bytes
        payload = [0x00, 0x00, 0x03, 0xE8]
        result = Microcontroller._payload_to_int(payload, 4)
        assert result == 1000

    def test_payload_to_int_negative(self):
        """Test decoding negative integers from two's complement bytes."""
        # -1000 as 4 big-endian bytes (two's complement)
        # -1000 = 0xFFFFFC18
        payload = [0xFF, 0xFF, 0xFC, 0x18]
        result = Microcontroller._payload_to_int(payload, 4)
        assert result == -1000

    def test_payload_roundtrip(self):
        """Test that encode/decode is reversible."""
        test_values = [0, 1, -1, 1000, -1000, 2**31 - 1, -(2**31)]

        for value in test_values:
            payload = Microcontroller._int_to_payload(value, 4)
            # Convert payload to bytes
            payload_bytes = [
                (payload >> 24) & 0xFF,
                (payload >> 16) & 0xFF,
                (payload >> 8) & 0xFF,
                payload & 0xFF,
            ]
            decoded = Microcontroller._payload_to_int(payload_bytes, 4)
            assert decoded == value, f"Roundtrip failed for {value}"


class TestSimSerialIntegration:
    """Test integration with SimSerial (simulated microcontroller)."""

    def test_simserial_responds_to_write(self):
        """Test that SimSerial generates response after write."""
        sim = SimSerial()
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        # Build a command
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1  # cmd_id
        cmd[1] = CMD_SET.MOVE_X
        cmd[7] = crc_calculator.calculate_checksum(cmd[:-1])

        # Write command
        sim.write(cmd)

        # Should have response bytes available
        assert sim.bytes_available() == MicrocontrollerDef.MSG_LENGTH

    def test_simserial_move_updates_position(self):
        """Test that move commands update SimSerial position."""
        sim = SimSerial()
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        # Initial position should be 0
        assert sim.x == 0

        # Build MOVE_X command with position delta
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.MOVE_X
        # Encode +1000 microsteps
        payload = Microcontroller._int_to_payload(1000, 4)
        cmd[2] = (payload >> 24) & 0xFF
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        cmd[7] = crc_calculator.calculate_checksum(cmd[:-1])

        sim.write(cmd)

        # Position should be updated
        assert sim.x == 1000

    def test_simserial_moveto_sets_position(self):
        """Test that moveto commands set absolute position."""
        sim = SimSerial()
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        # Set initial position
        sim.x = 5000

        # Build MOVETO_X command
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.MOVETO_X
        # Encode target position 2000
        payload = Microcontroller._int_to_payload(2000, 4)
        cmd[2] = (payload >> 24) & 0xFF
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        cmd[7] = crc_calculator.calculate_checksum(cmd[:-1])

        sim.write(cmd)

        # Position should be set to absolute value
        assert sim.x == 2000

    def test_simserial_home_zeros_position(self):
        """Test that home command zeros position."""
        sim = SimSerial()
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        # Set initial positions
        sim.x = 5000
        sim.y = 3000

        # Build HOME_OR_ZERO command for X axis
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.X
        cmd[3] = HOME_OR_ZERO.HOME_NEGATIVE
        cmd[7] = crc_calculator.calculate_checksum(cmd[:-1])

        sim.write(cmd)

        # X should be zeroed, Y unchanged
        assert sim.x == 0
        assert sim.y == 3000

    def test_simserial_response_contains_correct_positions(self):
        """Test that response contains updated positions."""
        sim = SimSerial()
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        # Set positions
        sim.x = 100
        sim.y = 200
        sim.z = 300

        # Send any command
        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc_calculator.calculate_checksum(cmd[:-1])

        sim.write(cmd)

        # Read response
        response = sim.read(MicrocontrollerDef.MSG_LENGTH)

        # Parse positions
        x = struct.unpack(">i", bytes(response[2:6]))[0]
        y = struct.unpack(">i", bytes(response[6:10]))[0]
        z = struct.unpack(">i", bytes(response[10:14]))[0]

        assert x == 100
        assert y == 200
        assert z == 300


class TestExecutionStatus:
    """Test command execution status handling."""

    def test_completed_without_errors(self):
        """Test COMPLETED_WITHOUT_ERRORS status value."""
        assert CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS == 0

    def test_in_progress(self):
        """Test IN_PROGRESS status value."""
        assert CMD_EXECUTION_STATUS.IN_PROGRESS == 1

    def test_checksum_error(self):
        """Test CMD_CHECKSUM_ERROR status value."""
        assert CMD_EXECUTION_STATUS.CMD_CHECKSUM_ERROR == 2

    def test_simserial_returns_completed_status(self):
        """Test that SimSerial returns COMPLETED_WITHOUT_ERRORS."""
        sim = SimSerial()
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        cmd = bytearray(MicrocontrollerDef.CMD_LENGTH)
        cmd[0] = 1
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        cmd[7] = crc_calculator.calculate_checksum(cmd[:-1])

        sim.write(cmd)
        response = sim.read(MicrocontrollerDef.MSG_LENGTH)

        # Byte 1 is execution status
        assert response[1] == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
