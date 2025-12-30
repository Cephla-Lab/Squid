"""
Firmware-validating serial simulator.

This module provides FirmwareSimSerial, a serial simulator that validates
commands against actual firmware source code. Unlike SimSerial (which is
a Python-only mock), this class parses firmware constants_protocol.h to ensure
the Python software sends commands that firmware will correctly understand.

Use this for integration testing to catch protocol drift between
Python software and C++ firmware.
"""

import re
import struct
import threading
from pathlib import Path
from typing import Optional

from crc import CrcCalculator, Crc8

from control.microcontroller import AbstractCephlaMicroSerial


class FirmwareProtocolError(Exception):
    """Raised when Python sends a command that doesn't match firmware expectations."""

    pass


class FirmwareConstants:
    """
    Parses and holds constants from firmware source code.

    This class reads the actual firmware constants_protocol.h file to extract
    command IDs, message lengths, axis definitions, etc.
    """

    def __init__(self, firmware_path: Optional[Path] = None):
        if firmware_path is None:
            # Default path relative to this file
            this_file = Path(__file__).parent
            firmware_path = this_file.parent.parent / "firmware" / "controller" / "src" / "constants_protocol.h"

        self.firmware_path = firmware_path
        self._constants = {}
        self._parse_firmware()

    def _parse_firmware(self):
        """Parse C++ constants from firmware header file."""
        if not self.firmware_path.exists():
            raise FileNotFoundError(f"Firmware constants file not found: {self.firmware_path}")

        with open(self.firmware_path, "r") as f:
            content = f.read()

        # Match patterns like: static const int MOVE_X = 0;
        pattern = r"static\s+const\s+int\s+(\w+)\s*=\s*(-?\d+)\s*;"
        matches = re.findall(pattern, content)

        for name, value in matches:
            self._constants[name] = int(value)

    def get(self, name: str, default=None):
        return self._constants.get(name, default)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._constants:
            return self._constants[name]
        raise AttributeError(f"Firmware constant '{name}' not found")

    @property
    def command_ids(self) -> dict:
        """Get all command IDs as a dict."""
        # Command names that are command IDs (not axis IDs, etc.)
        command_names = [
            "MOVE_X",
            "MOVE_Y",
            "MOVE_Z",
            "MOVE_THETA",
            "MOVE_W",
            "HOME_OR_ZERO",
            "MOVETO_X",
            "MOVETO_Y",
            "MOVETO_Z",
            "MOVETO_W",
            "SET_LIM",
            "TURN_ON_ILLUMINATION",
            "TURN_OFF_ILLUMINATION",
            "SET_ILLUMINATION",
            "SET_ILLUMINATION_LED_MATRIX",
            "ACK_JOYSTICK_BUTTON_PRESSED",
            "ANALOG_WRITE_ONBOARD_DAC",
            "SET_DAC80508_REFDIV_GAIN",
            "SET_ILLUMINATION_INTENSITY_FACTOR",
            "SET_LIM_SWITCH_POLARITY",
            "CONFIGURE_STEPPER_DRIVER",
            "SET_MAX_VELOCITY_ACCELERATION",
            "SET_LEAD_SCREW_PITCH",
            "SET_OFFSET_VELOCITY",
            "CONFIGURE_STAGE_PID",
            "ENABLE_STAGE_PID",
            "DISABLE_STAGE_PID",
            "SET_HOME_SAFETY_MERGIN",
            "SET_PID_ARGUMENTS",
            "SEND_HARDWARE_TRIGGER",
            "SET_STROBE_DELAY",
            "SET_AXIS_DISABLE_ENABLE",
            "SET_PIN_LEVEL",
            "INITFILTERWHEEL",
            "INITIALIZE",
            "RESET",
        ]
        return {name: self._constants[name] for name in command_names if name in self._constants}


class FirmwareSimSerial(AbstractCephlaMicroSerial):
    """
    A firmware-validating serial simulator.

    This class simulates firmware behavior while validating that all
    commands match the actual firmware protocol. It parses firmware
    source code to ensure Python commands will be correctly understood.

    Use this instead of SimSerial when you want to catch protocol
    mismatches between Python and firmware.
    """

    def __init__(self, firmware_path: Optional[Path] = None, strict: bool = True):
        """
        Initialize the firmware simulator.

        Args:
            firmware_path: Path to firmware constants_protocol.h. If None, uses default.
            strict: If True, raise FirmwareProtocolError on any mismatch.
                   If False, log warnings but continue.
        """
        super().__init__()
        self.fw = FirmwareConstants(firmware_path)
        self.strict = strict
        self.crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)

        self._update_lock = threading.Lock()
        self._in_waiting = 0
        self.response_buffer = []
        self._closed = False

        # Position state (in microsteps)
        # Note: These are Python ints (unbounded), unlike firmware's int32.
        # For simulation purposes this is acceptable; real firmware would
        # overflow at INT32_MIN/MAX (-2147483648 to 2147483647).
        self.x = 0
        self.y = 0
        self.z = 0
        self.theta = 0
        self.w = 0

        # Button/switch state
        self.joystick_button = False
        self.switch = False

        # Validation tracking
        # Note: validation_errors accumulates across commands for debugging.
        # Call clear_validation_errors() to reset between test runs if needed.
        self.commands_validated = 0
        self.validation_errors = []

    def clear_validation_errors(self) -> None:
        """Clear accumulated validation errors. Call between test runs if needed."""
        self.validation_errors = []
        self.commands_validated = 0

    def _validate_command(self, cmd: bytearray) -> None:
        """
        Validate a command against firmware expectations.

        Raises FirmwareProtocolError if validation fails and strict=True.
        """
        errors = []

        # Check command length
        expected_len = self.fw.get("CMD_LENGTH", 8)
        if len(cmd) != expected_len:
            errors.append(f"Command length {len(cmd)} != firmware CMD_LENGTH {expected_len}")

        # Check CRC
        if len(cmd) >= 2:
            calculated_crc = self.crc_calculator.calculate_checksum(cmd[:-1])
            if calculated_crc != cmd[-1]:
                errors.append(f"CRC mismatch: calculated {calculated_crc:#04x}, got {cmd[-1]:#04x}")

        # Check command ID is valid
        if len(cmd) >= 2:
            cmd_code = cmd[1]
            valid_cmd_ids = set(self.fw.command_ids.values())
            if cmd_code not in valid_cmd_ids:
                errors.append(f"Unknown command code {cmd_code}, not in firmware command IDs")

        # Validate axis parameter for commands that use it
        if len(cmd) >= 3:
            cmd_code = cmd[1]
            # Filter out None values to prevent false matches if constants are missing
            axis_commands = [
                cmd_id
                for cmd_id in [
                    self.fw.get("HOME_OR_ZERO"),
                    self.fw.get("SET_LIM"),
                    self.fw.get("SET_LIM_SWITCH_POLARITY"),
                    self.fw.get("CONFIGURE_STEPPER_DRIVER"),
                    self.fw.get("SET_MAX_VELOCITY_ACCELERATION"),
                    self.fw.get("SET_LEAD_SCREW_PITCH"),
                    self.fw.get("SET_OFFSET_VELOCITY"),
                    self.fw.get("CONFIGURE_STAGE_PID"),
                    self.fw.get("ENABLE_STAGE_PID"),
                    self.fw.get("DISABLE_STAGE_PID"),
                    self.fw.get("SET_HOME_SAFETY_MERGIN"),
                    self.fw.get("SET_PID_ARGUMENTS"),
                    self.fw.get("SET_AXIS_DISABLE_ENABLE"),
                ]
                if cmd_id is not None
            ]
            if cmd_code in axis_commands:
                axis = cmd[2]
                # Filter None to avoid false positives if constants are missing
                valid_axes = [
                    axis_id
                    for axis_id in [
                        self.fw.get("AXIS_X"),
                        self.fw.get("AXIS_Y"),
                        self.fw.get("AXIS_Z"),
                        self.fw.get("AXIS_THETA"),
                        self.fw.get("AXES_XY"),
                        self.fw.get("AXIS_W"),
                    ]
                    if axis_id is not None
                ]
                # SET_LIM uses limit codes (not axis IDs) for the axis parameter,
                # so we replace valid_axes with the valid limit codes
                if cmd_code == self.fw.get("SET_LIM"):
                    valid_axes = [
                        lim_code
                        for lim_code in [
                            self.fw.get("LIM_CODE_X_POSITIVE"),
                            self.fw.get("LIM_CODE_X_NEGATIVE"),
                            self.fw.get("LIM_CODE_Y_POSITIVE"),
                            self.fw.get("LIM_CODE_Y_NEGATIVE"),
                            self.fw.get("LIM_CODE_Z_POSITIVE"),
                            self.fw.get("LIM_CODE_Z_NEGATIVE"),
                        ]
                        if lim_code is not None
                    ]
                # Skip validation if no valid axes/codes defined (missing firmware constants)
                # rather than incorrectly flagging all values as invalid
                if valid_axes and axis not in valid_axes:
                    errors.append(f"Invalid axis {axis} for command {cmd_code}. Valid: {valid_axes}")

        if errors:
            self.validation_errors.extend(errors)
            if self.strict:
                raise FirmwareProtocolError("\n".join(errors))
            else:
                for err in errors:
                    self._log.warning(f"Firmware validation: {err}")

        self.commands_validated += 1

    def _build_response(self, cmd_id: int, status: int) -> bytes:
        """
        Build a response packet matching firmware format.

        Response format (from firmware serial_communication.cpp):
        - byte[0]: cmd_id
        - byte[1]: execution_status
        - bytes[2-5]: X position (big-endian signed 32-bit)
        - bytes[6-9]: Y position
        - bytes[10-13]: Z position
        - bytes[14-17]: Theta position
        - byte[18]: buttons/switches
        - bytes[19-22]: reserved
        - byte[23]: CRC

        Note: W axis position is tracked internally (self.w) but is NOT included
        in the response packet per the firmware protocol specification.
        """
        msg_length = self.fw.get("MSG_LENGTH", 24)

        # Build response using struct for proper byte packing
        button_state = (1 if self.joystick_button else 0) << self.fw.get("BIT_POS_JOYSTICK_BUTTON", 0)
        # BIT_POS_SWITCH may not be in constants_protocol.h; default to bit position 1
        button_state |= (1 if self.switch else 0) << self.fw.get("BIT_POS_SWITCH", 1)

        reserved = 0

        # Struct format ">BBiiiiBi" byte breakdown:
        #   B: cmd_id       (1 byte)
        #   B: status       (1 byte)
        #   i: x            (4 bytes)
        #   i: y            (4 bytes)
        #   i: z            (4 bytes)
        #   i: theta        (4 bytes)
        #   B: button_state (1 byte)
        #   i: reserved     (4 bytes)
        #   Total: 1+1+4+4+4+4+1+4 = 23 bytes (+ 1 byte CRC = 24 = MSG_LENGTH)
        response = bytearray(
            struct.pack(
                ">BBiiiiBi",
                cmd_id,
                status,
                self.x,
                self.y,
                self.z,
                self.theta,
                button_state,
                reserved,
            )
        )

        # Verify response payload matches firmware MSG_LENGTH - 1 (leaving room for CRC).
        # The struct format ">BBiiiiBi" produces 23 bytes, matching MSG_LENGTH-1 when
        # MSG_LENGTH == 24. If firmware changes MSG_LENGTH, this assertion will fail
        # and the struct format must be updated to match the new protocol.
        expected_payload_len = msg_length - 1
        if len(response) != expected_payload_len:
            raise FirmwareProtocolError(
                f"Response payload size ({len(response)}) does not match "
                f"MSG_LENGTH-1 ({expected_payload_len}). Update struct format in "
                f"FirmwareSimSerial._build_response to match firmware protocol."
            )

        # Add CRC
        response.append(self.crc_calculator.calculate_checksum(response))

        return bytes(response)

    def _process_command(self, cmd: bytearray) -> None:
        """Process a validated command and update state."""
        cmd_id = cmd[0]
        cmd_code = cmd[1]

        # Extract 4-byte position from bytes 2-5
        def get_position() -> int:
            return struct.unpack(">i", bytes(cmd[2:6]))[0]

        # Handle commands
        if cmd_code == self.fw.get("MOVE_X"):
            self.x += get_position()
        elif cmd_code == self.fw.get("MOVE_Y"):
            self.y += get_position()
        elif cmd_code == self.fw.get("MOVE_Z"):
            self.z += get_position()
        elif cmd_code == self.fw.get("MOVE_THETA"):
            self.theta += get_position()
        elif cmd_code == self.fw.get("MOVE_W"):
            self.w += get_position()
        elif cmd_code == self.fw.get("MOVETO_X"):
            self.x = get_position()
        elif cmd_code == self.fw.get("MOVETO_Y"):
            self.y = get_position()
        elif cmd_code == self.fw.get("MOVETO_Z"):
            self.z = get_position()
        elif cmd_code == self.fw.get("MOVETO_W"):
            self.w = get_position()
        elif cmd_code == self.fw.get("HOME_OR_ZERO"):
            axis = cmd[2]
            # home_type at cmd[3] indicates HOME_NEGATIVE, HOME_POSITIVE, or ZERO
            # In simulation, all variants simply zero the position
            # Zero or home sets position to 0
            if axis == self.fw.get("AXIS_X", 0):
                self.x = 0
            elif axis == self.fw.get("AXIS_Y", 1):
                self.y = 0
            elif axis == self.fw.get("AXIS_Z", 2):
                self.z = 0
            elif axis == self.fw.get("AXIS_THETA", 3):
                self.theta = 0
            elif axis == self.fw.get("AXES_XY", 4):
                self.x = 0
                self.y = 0
            elif axis == self.fw.get("AXIS_W", 5):
                self.w = 0
        elif cmd_code == self.fw.get("RESET"):
            # Reset clears positions
            self.x = 0
            self.y = 0
            self.z = 0
            self.theta = 0
            self.w = 0

        # Build and queue response
        status = self.fw.get("COMPLETED_WITHOUT_ERRORS", 0)
        response = self._build_response(cmd_id, status)
        self.response_buffer.extend(response)
        self._update_internal_state()

    def _update_internal_state(self, clear_buffer: bool = False):
        if clear_buffer:
            self.response_buffer.clear()
        self._in_waiting = len(self.response_buffer)

    # AbstractCephlaMicroSerial implementation

    def close(self) -> None:
        with self._update_lock:
            self._closed = True
            self._update_internal_state(clear_buffer=True)

    def reset_input_buffer(self) -> bool:
        with self._update_lock:
            self._update_internal_state(clear_buffer=True)
            return True

    def write(self, data: bytearray, reconnect_tries: int = 0) -> int:
        if self._closed:
            if not self.reconnect(reconnect_tries):
                raise IOError("Closed")

        with self._update_lock:
            # Validate against firmware
            self._validate_command(data)
            # Process if valid
            self._process_command(data)
            return len(data)

    def read(self, count: int = 1, reconnect_tries: int = 0) -> bytes:
        if self._closed:
            if not self.reconnect(reconnect_tries):
                raise IOError("Closed")

        with self._update_lock:
            response = bytearray()
            for i in range(count):
                if not self.response_buffer:
                    break
                response.append(self.response_buffer.pop(0))
            self._update_internal_state()
            return bytes(response)

    def bytes_available(self) -> int:
        with self._update_lock:
            self._update_internal_state()
            return self._in_waiting

    def is_open(self) -> bool:
        with self._update_lock:
            return not self._closed

    def reconnect(self, attempts: int) -> bool:
        with self._update_lock:
            self._update_internal_state()
            if not attempts:
                return not self._closed
            if self._closed:
                self._log.warning("Reconnect required, succeeded.")
                self._update_internal_state(clear_buffer=True)
                self._closed = False
        return True
