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
from typing import Dict, List, Optional, Set, Tuple

from crc import CrcCalculator, Crc8

from control._def import (
    FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE,
    LIMIT_SWITCH_CONFIG_MIN_FIRMWARE,
    LIMIT_SWITCH_DEFAULT_CONFIG,
    STARTUP_STATE_MIN_FIRMWARE,
)
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
            "MOVE_W2",
            "HOME_OR_ZERO",
            "MOVETO_X",
            "MOVETO_Y",
            "MOVETO_Z",
            "MOVETO_W",
            "MOVETO_W2",
            "SET_LIM",
            "TURN_ON_ILLUMINATION",
            "TURN_OFF_ILLUMINATION",
            "SET_ILLUMINATION",
            "SET_ILLUMINATION_LED_MATRIX",
            "ACK_JOYSTICK_BUTTON_PRESSED",
            "ANALOG_WRITE_ONBOARD_DAC",
            "SET_DAC80508_REFDIV_GAIN",
            "SET_ILLUMINATION_INTENSITY_FACTOR",
            "SET_LIMIT_SWITCH_CONFIG",
            "SET_FOCUS_WHEEL_MODE",
            "SET_PANEL_LOCK",
            "STORE_CONFIG_SIGNATURE",
            "QUERY_STARTUP_STATE",
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
            # Multi-port illumination commands (firmware v1.0+)
            "SET_PORT_INTENSITY",
            "TURN_ON_PORT",
            "TURN_OFF_PORT",
            "SET_PORT_ILLUMINATION",
            "SET_MULTI_PORT_MASK",
            "TURN_OFF_ALL_PORTS",
        ]
        return {name: self._constants[name] for name in command_names if name in self._constants}


class HomingValidity:
    """Python twin of the firmware's homing validity state (firmware 1.9, utils/homing_validity.{h,cpp}): per-axis
    homed, armed and driver-enabled flags, one configuration signature and the covered settings' bytes as last
    received. Each method mirrors the hv_* function of the same name. Axes are protocol numbers 0..2 (X, Y, Z)."""

    AXES = (0, 1, 2)

    def __init__(self, fw: FirmwareConstants):  # hv_power_up
        self._fw = fw
        self.homed = [False] * 3
        self.armed = [False] * 3  # a home started with power good and the driver enabled
        self.driver_enabled = [True] * 3
        self.signature: Optional[Tuple[int, int]] = None  # (signature, format) while one is stored
        self.config: Dict[Tuple[int, int], bytes] = {}  # (covered command code, axis) -> bytes [3..6] last received

    def _clear_all_axes(self) -> None:
        for axis in self.AXES:
            self.invalidate_axis(axis)

    def home_start(self, axis: int, power_good: bool) -> None:
        self.homed[axis] = False
        self.armed[axis] = power_good and self.driver_enabled[axis]

    def home_complete(self, axis: int, power_good: bool) -> None:
        self.homed[axis] = self.armed[axis] and power_good and self.driver_enabled[axis]
        self.armed[axis] = False

    def invalidate_axis(self, axis: int) -> None:
        self.homed[axis] = False
        self.armed[axis] = False

    def driver_enable(self, axis: int, enabled: bool) -> None:
        self.driver_enabled[axis] = enabled
        if not enabled:
            self.invalidate_axis(axis)

    def power_good(self, power_good: bool) -> None:
        if not power_good:
            self._clear_all_axes()

    def initialize(self) -> None:
        self._clear_all_axes()
        self.signature = None
        self.driver_enabled = [True] * 3

    def store_signature(self, signature: int, signature_format: int) -> None:
        # An emptied signature counts as different: old flags are never vouched for under a changed configuration
        if self.signature != (signature, signature_format):
            self._clear_all_axes()
        self.signature = (signature, signature_format)

    def config_received(self, code: int, axis: int, data: bytes) -> None:
        if self.config.get((code, axis)) != data:  # changed, or the first time this axis's setting is seen
            self.signature = None
        self.config[(code, axis)] = data

    def verdict(
        self,
        signature: int,
        signature_format: int,
        required_mask: int,
        at_rest_mask: int,
        power_good: bool,
        tmc_config_ok: bool,
    ) -> int:
        """hv_verdict: the first failing check wins. Returns the firmware's STARTUP_VERDICT_* value."""
        if self.signature is None:
            return self._fw.STARTUP_VERDICT_NO_SIGNATURE
        if self.signature != (signature, signature_format):
            return self._fw.STARTUP_VERDICT_SIGNATURE_MISMATCH
        if not (power_good and tmc_config_ok and all(self.driver_enabled)):
            return self._fw.STARTUP_VERDICT_UNHEALTHY
        if at_rest_mask != 0b111:
            return self._fw.STARTUP_VERDICT_MOVING
        if any(required_mask & (1 << axis) and not self.homed[axis] for axis in self.AXES):
            return self._fw.STARTUP_VERDICT_NOT_HOMED
        return self._fw.STARTUP_VERDICT_WARM


class FirmwareSimSerial(AbstractCephlaMicroSerial):
    """
    A firmware-validating serial simulator.

    This class simulates firmware behavior while validating that all
    commands match the actual firmware protocol. It parses firmware
    source code to ensure Python commands will be correctly understood.

    Use this instead of SimSerial when you want to catch protocol
    mismatches between Python and firmware.
    """

    def __init__(
        self, firmware_path: Optional[Path] = None, strict: bool = True, firmware_version: Tuple[int, int] = (0, 0)
    ):
        """
        Initialize the firmware simulator.

        Args:
            firmware_path: Path to firmware constants_protocol.h. If None, uses default.
            strict: If True, raise FirmwareProtocolError on any mismatch.
                   If False, log warnings but continue.
            firmware_version: (major, minor) reported in response byte 22. (0, 0) is legacy firmware
                   that reports no version.
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
        self.w2 = 0

        self.firmware_version = tuple(firmware_version)
        # Limit-switch config nibble per protocol axis (SET_LIMIT_SWITCH_CONFIG)
        self.limit_switch_config: Dict[int, int] = {
            self.fw.get("AXIS_X", 0): LIMIT_SWITCH_DEFAULT_CONFIG,
            self.fw.get("AXIS_Y", 1): LIMIT_SWITCH_DEFAULT_CONFIG,
            self.fw.get("AXIS_Z", 2): LIMIT_SWITCH_DEFAULT_CONFIG,
        }

        # Button/switch state
        self.joystick_button = False
        self.switch = False
        # Z limit switch inputs triggered (status byte 18 bits 2-3)
        self.z_limit_left = False
        self.z_limit_right = False
        # SET_FOCUS_WHEEL_MODE: in TO_HOST the wheel's ticks (turn_focus_wheel) are reported instead of moving Z
        self.focus_wheel_mode = self.fw.get("FOCUS_WHEEL_DRIVES_Z", 0)
        self.focus_wheel_power_up_mode = self.fw.get("FOCUS_WHEEL_DRIVES_Z", 0)  # EEPROM 8-11
        self.focus_wheel_count = 0
        self.panel_locked = False  # SET_PANEL_LOCK: the control panel moves nothing

        # Test hooks. command_log: every command code received, in order. in_progress: every packet says IN_PROGRESS,
        # as the firmware's mcu_cmd_execution_in_progress does while a command (a previous session's, say) runs;
        # RESET clears it. moving_axes: protocol axes whose ramp has not stopped; INITIALIZE stops them, RESET does
        # not. tmc_config_ok: False when a stage TMC4361A lost its configuration. Power good: set_power_good().
        self.command_log: List[int] = []
        self.in_progress = False
        self.moving_axes: Set[int] = set()
        self.tmc_config_ok = True
        self._power_good = True
        # Startup state (firmware >= STARTUP_STATE_MIN_FIRMWARE). Kept on older firmware too, where nothing can read
        # it: STORE_CONFIG_SIGNATURE and QUERY_STARTUP_STATE are no-ops there.
        self.homing_validity = HomingValidity(self.fw)
        self._startup_verdict_pending = False  # the status byte carries the verdict until the next command
        self._startup_verdict = 0

        # Validation tracking
        # Note: validation_errors accumulates across commands for debugging.
        # Call clear_validation_errors() to reset between test runs if needed.
        self.commands_validated = 0
        self.validation_errors = []

    def set_power_good(self, power_good: bool) -> None:
        """The stage power supply's power-good line. Low clears every homed flag (and the query reports UNHEALTHY
        until it is high again); the flags stay cleared after it comes back."""
        self._power_good = power_good
        self.homing_validity.power_good(power_good)

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
        - byte[18]: buttons/switches, Z limit switches
        - bytes[19-21]: reserved
        - byte[22]: firmware version (high nibble major, low nibble minor)
        - byte[23]: CRC

        Note: W axis position is tracked internally (self.w) but is NOT included
        in the response packet per the firmware protocol specification.
        """
        msg_length = self.fw.get("MSG_LENGTH", 24)

        # Build response using struct for proper byte packing
        button_state = (1 if self.joystick_button else 0) << self.fw.get("BIT_POS_JOYSTICK_BUTTON", 0)
        # BIT_POS_SWITCH may not be in constants_protocol.h; default to bit position 1
        button_state |= (1 if self.switch else 0) << self.fw.get("BIT_POS_SWITCH", 1)
        button_state |= (1 if self.z_limit_left else 0) << self.fw.get("BIT_POS_Z_LIMIT_LEFT", 2)
        button_state |= (1 if self.z_limit_right else 0) << self.fw.get("BIT_POS_Z_LIMIT_RIGHT", 3)

        version_byte = (self.firmware_version[0] << 4) | (self.firmware_version[1] & 0x0F)

        # Focus wheel to host: the theta field carries the wheel count, flagged in byte 19
        theta_field, byte19 = self.theta, 0
        if self.focus_wheel_mode == self.fw.get("FOCUS_WHEEL_TO_HOST", 1):
            theta_field = self.focus_wheel_count
            byte19 = 1 << self.fw.get("FOCUS_WHEEL_FLAG_REPORTING", 7)

        # Struct format ">BBiiiiBB2xB" byte breakdown:
        #   B: cmd_id       (1 byte)
        #   B: status       (1 byte)
        #   i: x            (4 bytes)
        #   i: y            (4 bytes)
        #   i: z            (4 bytes)
        #   i: theta        (4 bytes)
        #   B: button_state (1 byte)
        #   B: byte 19      (1 byte, flags)
        #   2x: reserved    (2 bytes, zero)
        #   B: version_byte (1 byte)
        #   Total: 1+1+4+4+4+4+1+3+1 = 23 bytes (+ 1 byte CRC = 24 = MSG_LENGTH)
        response = bytearray(
            struct.pack(
                ">BBiiiiBB2xB",
                cmd_id,
                status,
                self.x,
                self.y,
                self.z,
                theta_field,
                button_state,
                byte19,
                version_byte,
            )
        )

        # Verify response payload matches firmware MSG_LENGTH - 1 (leaving room for CRC).
        # The struct format ">BBiiiiBB2xB" produces 23 bytes, matching MSG_LENGTH-1 when
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
        status = self.fw.get("COMPLETED_WITHOUT_ERRORS", 0)
        self.command_log.append(cmd_code)
        # A verdict answers only the command that asked for it (the firmware clears it before every dispatch)
        self._startup_verdict_pending = False
        startup_state = self.firmware_version >= STARTUP_STATE_MIN_FIRMWARE

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
        elif cmd_code == self.fw.get("MOVE_W2"):
            self.w2 += get_position()
        elif cmd_code == self.fw.get("MOVETO_X"):
            self.x = get_position()
        elif cmd_code == self.fw.get("MOVETO_Y"):
            self.y = get_position()
        elif cmd_code == self.fw.get("MOVETO_Z"):
            self.z = get_position()
        elif cmd_code == self.fw.get("MOVETO_W"):
            self.w = get_position()
        elif cmd_code == self.fw.get("MOVETO_W2"):
            self.w2 = get_position()
        elif cmd_code == self.fw.get("HOME_OR_ZERO"):
            self._home_or_zero(cmd[2], cmd[3])
        elif cmd_code == self.fw.get("RESET"):
            # callback_reset ends any command in progress but writes no XACTUAL and stops no ramp: positions, moving
            # axes and the startup state survive it
            self.in_progress = False
            self._enter_focus_wheel_mode(self.focus_wheel_power_up_mode)
            self.panel_locked = False
        elif cmd_code == self.fw.get("INITIALIZE"):
            # The TMC4361A soft reset of each stage axis puts XACTUAL back to 0 and stops its ramp
            self.homing_validity.initialize()
            self.x = 0
            self.y = 0
            self.z = 0
            self.moving_axes.clear()
            self._enter_focus_wheel_mode(self.focus_wheel_power_up_mode)
            self.panel_locked = False
        elif cmd_code == self.fw.get("SET_AXIS_DISABLE_ENABLE"):
            if cmd[2] in self._stage_axes:
                self.homing_validity.driver_enable(cmd[2], cmd[3] != 0)
        elif cmd_code in self._covered_settings:
            self._config_received(cmd)
        elif cmd_code == self.fw.get("SET_PANEL_LOCK"):
            if self.firmware_version >= FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE:  # older firmware: no handler, a no-op
                if cmd[2] > 1:
                    status = self.fw.get("CMD_EXECUTION_ERROR", 4)
                else:
                    self.panel_locked = bool(cmd[2])
        elif cmd_code == self.fw.get("SET_FOCUS_WHEEL_MODE"):
            if not self._set_focus_wheel_mode(cmd[2], cmd[3]):
                status = self.fw.get("CMD_EXECUTION_ERROR", 4)
        elif cmd_code == self.fw.get("SET_LIMIT_SWITCH_CONFIG"):
            if self._set_limit_switch_config(cmd[2], cmd[3]):
                self._config_received(cmd)  # covered once applied
            else:
                status = self.fw.get("CMD_EXECUTION_ERROR", 4)
        elif cmd_code == self.fw.get("STORE_CONFIG_SIGNATURE"):
            if startup_state:  # older firmware: no handler, a no-op
                self.homing_validity.store_signature(int.from_bytes(bytes(cmd[2:6]), "big"), cmd[6] >> 4)
        elif cmd_code == self.fw.get("QUERY_STARTUP_STATE"):
            if startup_state:  # older firmware: no handler, a no-op
                self._query_startup_state(cmd)

        # Build and queue response
        response = self._build_response(cmd_id, self._packet_status(status))
        self.response_buffer.extend(response)
        self._update_internal_state()

    @property
    def _stage_axes(self) -> Tuple[int, int, int]:
        """Protocol X, Y and Z: the axes the startup state covers."""
        return (self.fw.get("AXIS_X", 0), self.fw.get("AXIS_Y", 1), self.fw.get("AXIS_Z", 2))

    @property
    def _covered_settings(self) -> Tuple[int, ...]:
        """The settings whose X/Y/Z bytes [3..6] the startup state compares (SET_LIMIT_SWITCH_CONFIG only once
        applied, so it is handled with its own command)."""
        return tuple(
            self.fw.get(name)
            for name in (
                "SET_LEAD_SCREW_PITCH",
                "CONFIGURE_STEPPER_DRIVER",
                "SET_MAX_VELOCITY_ACCELERATION",
                "SET_HOME_SAFETY_MERGIN",
            )
        )

    def _config_received(self, cmd: bytearray) -> None:
        """hv_config_received for an X/Y/Z covered setting: a changed or first-seen value empties the signature.
        The filter wheels are not covered."""
        if cmd[2] in self._stage_axes:
            self.homing_validity.config_received(cmd[1], cmd[2], bytes(cmd[3:7]))

    def _home_or_zero(self, axis: int, kind: int) -> None:
        """HOME_OR_ZERO. A home completes at once; a zero (kind HOME_OR_ZERO_ZERO) re-origins the axis where it
        stands. Either way the position becomes 0. An X/Y/Z home counts as homed only if power was good and the
        driver enabled at its start and end; a zero leaves the axis not homed."""
        positions = {
            self.fw.get("AXIS_X", 0): ("x",),
            self.fw.get("AXIS_Y", 1): ("y",),
            self.fw.get("AXIS_Z", 2): ("z",),
            self.fw.get("AXIS_THETA", 3): ("theta",),
            self.fw.get("AXES_XY", 4): ("x", "y"),
            self.fw.get("AXIS_W", 5): ("w",),
            self.fw.get("AXIS_W2", 6): ("w2",),
        }.get(axis, ())
        for name in positions:
            setattr(self, name, 0)
        stage_axes = [a for a, name in zip(self._stage_axes, ("x", "y", "z")) if name in positions]
        if kind == self.fw.get("HOME_OR_ZERO_ZERO", 2):
            for a in stage_axes:
                self.homing_validity.invalidate_axis(a)
            return
        for a in stage_axes:
            self.homing_validity.home_start(a, self._power_good)
        for a in stage_axes:
            self.homing_validity.home_complete(a, self._power_good)

    def _query_startup_state(self, cmd: bytearray) -> None:
        """callback_query_startup_state: [2..5] signature, [6] (format << 4) | required axes mask. Reads, never
        stores; the verdict goes out in the status byte until the next command."""
        at_rest_mask = 0
        for a in self._stage_axes:
            if a not in self.moving_axes:
                at_rest_mask |= 1 << a
        self._startup_verdict = self.homing_validity.verdict(
            int.from_bytes(bytes(cmd[2:6]), "big"),
            cmd[6] >> 4,
            cmd[6] & 0x0F,
            at_rest_mask,
            self._power_good,
            self.tmc_config_ok,
        )
        self._startup_verdict_pending = True

    def _packet_status(self, execution_status: int) -> int:
        """hv_packet_status: a pending startup verdict, else IN_PROGRESS while in progress, else the command's
        status. (The firmware's checksum error comes first; this sim raises on a bad checksum instead.)"""
        if self._startup_verdict_pending:
            return self.fw.STARTUP_VERDICT_STATUS_BASE + self._startup_verdict
        if self.in_progress:
            return self.fw.get("IN_PROGRESS", 1)
        return execution_status

    def _set_focus_wheel_mode(self, mode: int, flags: int = 0) -> bool:
        """SET_FOCUS_WHEEL_MODE. Older firmware has no handler (callback_default: success, nothing happens); newer
        firmware refuses an unknown mode or flag, stores the power-up mode when asked, and resets the count on
        entering TO_HOST."""
        if self.firmware_version < FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE:
            return True
        drives_z, to_host = self.fw.get("FOCUS_WHEEL_DRIVES_Z", 0), self.fw.get("FOCUS_WHEEL_TO_HOST", 1)
        save = self.fw.get("FOCUS_WHEEL_SAVE_AS_POWER_UP", 1)
        if mode not in (drives_z, to_host) or flags & ~save:
            return False
        if flags & save:
            self.focus_wheel_power_up_mode = mode
        self._enter_focus_wheel_mode(mode)
        return True

    def _enter_focus_wheel_mode(self, mode: int) -> None:
        if mode == self.fw.get("FOCUS_WHEEL_TO_HOST", 1) and self.focus_wheel_mode != mode:
            self.focus_wheel_count = 0
        self.focus_wheel_mode = mode

    def turn_focus_wheel(self, ticks: int) -> None:
        """Simulate the control panel's focus wheel: moves Z in DRIVES_Z, counts in TO_HOST (wrapping int32);
        nothing while the panel is locked."""
        if self.panel_locked:
            return
        if self.focus_wheel_mode == self.fw.get("FOCUS_WHEEL_TO_HOST", 1):
            self.focus_wheel_count = ((self.focus_wheel_count + ticks + 2**31) % 2**32) - 2**31
        else:
            self.z += ticks

    def _set_limit_switch_config(self, axis: int, config: int) -> bool:
        """SET_LIMIT_SWITCH_CONFIG. Returns False for a command the firmware refuses (CMD_EXECUTION_ERROR).

        Firmware older than LIMIT_SWITCH_CONFIG_MIN_FIRMWARE has no handler for it: callback_default answers
        success for any axis and byte and does nothing. Newer firmware stores a nibble for X, Y or Z and refuses
        any other axis or a value wider than a nibble, storing nothing. Validated here rather than in
        _validate_command, whose generic axis check raises for an axis outside its list.
        """
        if self.firmware_version < LIMIT_SWITCH_CONFIG_MIN_FIRMWARE:
            return True
        if axis not in self.limit_switch_config or config > 0x0F:
            return False
        self.limit_switch_config[axis] = config
        return True

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
