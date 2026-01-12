"""
Protocol v2.0 implementation for Python software.

Handles packet building, CRC-16 calculation, and response parsing for
communication with the v2 firmware protocol.
"""

import enum
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import squid.logging

_log = squid.logging.get_logger(__name__)


# =============================================================================
# CRC-16 CCITT Implementation
# =============================================================================

# Pre-computed CRC-16 CCITT lookup table (polynomial 0x1021, initial 0xFFFF)
CRC16_TABLE = [
    0x0000,
    0x1021,
    0x2042,
    0x3063,
    0x4084,
    0x50A5,
    0x60C6,
    0x70E7,
    0x8108,
    0x9129,
    0xA14A,
    0xB16B,
    0xC18C,
    0xD1AD,
    0xE1CE,
    0xF1EF,
    0x1231,
    0x0210,
    0x3273,
    0x2252,
    0x52B5,
    0x4294,
    0x72F7,
    0x62D6,
    0x9339,
    0x8318,
    0xB37B,
    0xA35A,
    0xD3BD,
    0xC39C,
    0xF3FF,
    0xE3DE,
    0x2462,
    0x3443,
    0x0420,
    0x1401,
    0x64E6,
    0x74C7,
    0x44A4,
    0x5485,
    0xA56A,
    0xB54B,
    0x8528,
    0x9509,
    0xE5EE,
    0xF5CF,
    0xC5AC,
    0xD58D,
    0x3653,
    0x2672,
    0x1611,
    0x0630,
    0x76D7,
    0x66F6,
    0x5695,
    0x46B4,
    0xB75B,
    0xA77A,
    0x9719,
    0x8738,
    0xF7DF,
    0xE7FE,
    0xD79D,
    0xC7BC,
    0x48C4,
    0x58E5,
    0x6886,
    0x78A7,
    0x0840,
    0x1861,
    0x2802,
    0x3823,
    0xC9CC,
    0xD9ED,
    0xE98E,
    0xF9AF,
    0x8948,
    0x9969,
    0xA90A,
    0xB92B,
    0x5AF5,
    0x4AD4,
    0x7AB7,
    0x6A96,
    0x1A71,
    0x0A50,
    0x3A33,
    0x2A12,
    0xDBFD,
    0xCBDC,
    0xFBBF,
    0xEB9E,
    0x9B79,
    0x8B58,
    0xBB3B,
    0xAB1A,
    0x6CA6,
    0x7C87,
    0x4CE4,
    0x5CC5,
    0x2C22,
    0x3C03,
    0x0C60,
    0x1C41,
    0xEDAE,
    0xFD8F,
    0xCDEC,
    0xDDCD,
    0xAD2A,
    0xBD0B,
    0x8D68,
    0x9D49,
    0x7E97,
    0x6EB6,
    0x5ED5,
    0x4EF4,
    0x3E13,
    0x2E32,
    0x1E51,
    0x0E70,
    0xFF9F,
    0xEFBE,
    0xDFDD,
    0xCFFC,
    0xBF1B,
    0xAF3A,
    0x9F59,
    0x8F78,
    0x9188,
    0x81A9,
    0xB1CA,
    0xA1EB,
    0xD10C,
    0xC12D,
    0xF14E,
    0xE16F,
    0x1080,
    0x00A1,
    0x30C2,
    0x20E3,
    0x5004,
    0x4025,
    0x7046,
    0x6067,
    0x83B9,
    0x9398,
    0xA3FB,
    0xB3DA,
    0xC33D,
    0xD31C,
    0xE37F,
    0xF35E,
    0x02B1,
    0x1290,
    0x22F3,
    0x32D2,
    0x4235,
    0x5214,
    0x6277,
    0x7256,
    0xB5EA,
    0xA5CB,
    0x95A8,
    0x8589,
    0xF56E,
    0xE54F,
    0xD52C,
    0xC50D,
    0x34E2,
    0x24C3,
    0x14A0,
    0x0481,
    0x7466,
    0x6447,
    0x5424,
    0x4405,
    0xA7DB,
    0xB7FA,
    0x8799,
    0x97B8,
    0xE75F,
    0xF77E,
    0xC71D,
    0xD73C,
    0x26D3,
    0x36F2,
    0x0691,
    0x16B0,
    0x6657,
    0x7676,
    0x4615,
    0x5634,
    0xD94C,
    0xC96D,
    0xF90E,
    0xE92F,
    0x99C8,
    0x89E9,
    0xB98A,
    0xA9AB,
    0x5844,
    0x4865,
    0x7806,
    0x6827,
    0x18C0,
    0x08E1,
    0x3882,
    0x28A3,
    0xCB7D,
    0xDB5C,
    0xEB3F,
    0xFB1E,
    0x8BF9,
    0x9BD8,
    0xABBB,
    0xBB9A,
    0x4A75,
    0x5A54,
    0x6A37,
    0x7A16,
    0x0AF1,
    0x1AD0,
    0x2AB3,
    0x3A92,
    0xFD2E,
    0xED0F,
    0xDD6C,
    0xCD4D,
    0xBDAA,
    0xAD8B,
    0x9DE8,
    0x8DC9,
    0x7C26,
    0x6C07,
    0x5C64,
    0x4C45,
    0x3CA2,
    0x2C83,
    0x1CE0,
    0x0CC1,
    0xEF1F,
    0xFF3E,
    0xCF5D,
    0xDF7C,
    0xAF9B,
    0xBFBA,
    0x8FD9,
    0x9FF8,
    0x6E17,
    0x7E36,
    0x4E55,
    0x5E74,
    0x2E93,
    0x3EB2,
    0x0ED1,
    0x1EF0,
]


def crc16_ccitt(data: bytes) -> int:
    """Calculate CRC-16 CCITT over a data buffer."""
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) ^ CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc


# =============================================================================
# Protocol Constants
# =============================================================================

PACKET_HEADER_0 = 0xAA
PACKET_HEADER_1 = 0xBB
PACKET_MAX_PAYLOAD = 506
PACKET_MAX_SIZE = 512
PACKET_OVERHEAD = 6  # 2 header + 2 length + 2 CRC


class CommandType(enum.IntEnum):
    """V2 protocol command types."""

    # Motion (0x01-0x0F)
    CMD_MOVE_AXIS = 0x01
    CMD_MOVE_RELATIVE = 0x02
    CMD_HOME_AXIS = 0x03
    CMD_STOP_AXIS = 0x04
    CMD_STOP_ALL = 0x05
    CMD_ENABLE_AXIS = 0x06
    CMD_INIT_FILTER_WHEEL = 0x07

    # Configuration (0x10-0x1F)
    CMD_SET_AXIS_PARAMS = 0x10
    CMD_GET_AXIS_PARAMS = 0x11
    CMD_SET_CAMERA_PARAMS = 0x12
    CMD_SET_PID_PARAMS = 0x13
    CMD_ENABLE_PID = 0x14
    CMD_DISABLE_PID = 0x15

    # Analog/Digital Output (0x20-0x2F)
    CMD_SET_DAC = 0x20
    CMD_SET_TTL = 0x21
    CMD_CONFIG_GPIO = 0x22
    CMD_WRITE_GPIO = 0x23
    CMD_READ_GPIO = 0x24
    CMD_SET_DAC_GAIN = 0x25

    # Illumination (0x30-0x3F)
    CMD_SET_ILLUMINATION = 0x30
    CMD_SET_LED_MATRIX = 0x31
    CMD_PULSE_ILLUMINATION = 0x32

    # Camera (0x40-0x4F)
    CMD_TRIGGER_CAMERA = 0x40

    # HSA (0x50-0x5F)
    CMD_HSA_UPLOAD_HEADER = 0x50
    CMD_HSA_UPLOAD_ACTIONS = 0x51
    CMD_HSA_UPLOAD_TRIGGER_PROFILE = 0x52
    CMD_HSA_UPLOAD_INTENSITY = 0x53
    CMD_HSA_START = 0x54
    CMD_HSA_CANCEL = 0x55

    # System (0xF0-0xFF)
    CMD_GET_STATE = 0xF0
    CMD_ACK_ERROR = 0xF1
    CMD_GET_VERSION = 0xF2
    CMD_INITIALIZE = 0xFE
    CMD_RESET = 0xFF


class ResponseStatus(enum.IntEnum):
    """Response status codes."""

    STATUS_OK = 0x00
    STATUS_ACCEPTED = 0x01
    STATUS_REJECTED = 0x02
    STATUS_ERROR = 0x03


class ErrorCode(enum.IntEnum):
    """Error codes returned in responses."""

    ERR_NONE = 0x00
    ERR_INVALID_CMD = 0x01
    ERR_INVALID_AXIS = 0x02
    ERR_AXIS_BUSY = 0x03
    ERR_AXIS_NOT_HOMED = 0x04
    ERR_LIMIT_REACHED = 0x05
    ERR_CHECKSUM = 0x06
    ERR_PACKET_TOO_SHORT = 0x07
    ERR_PACKET_TOO_LONG = 0x08
    ERR_SYSTEM_IN_ERROR = 0x09
    ERR_HSA_RUNNING = 0x0A
    ERR_INTERLOCK = 0x0B


class AxisId(enum.IntEnum):
    """V2 protocol axis IDs."""

    AXIS_X = 0
    AXIS_Y = 1
    AXIS_Z = 2
    AXIS_FILTER1 = 3
    AXIS_TURRET = 4
    AXIS_FILTER2 = 5  # W axis in current firmware
    AXIS_AUX1 = 6
    AXIS_AUX2 = 7


class AxisState(enum.IntEnum):
    """Axis state values."""

    AXIS_IDLE = 0
    AXIS_MOVING = 1
    AXIS_HOMING = 2
    AXIS_ERROR = 3


class SystemMode(enum.IntEnum):
    """System mode values."""

    MODE_NORMAL = 0
    MODE_HSA = 1
    MODE_ERROR = 2


# =============================================================================
# Response Data Structures
# =============================================================================


@dataclass
class AxisStatus:
    """Status of a single axis."""

    position_usteps: int  # Current position in microsteps
    target_usteps: int  # Target position
    state: AxisState  # Current state
    error_code: int  # Axis-specific error
    homed: bool  # Whether axis is homed


@dataclass
class ResponsePacket:
    """Parsed response packet from firmware."""

    # Command acknowledgment
    cmd_id: int
    status: ResponseStatus
    error_code: ErrorCode

    # System state
    system_mode: SystemMode

    # Axis states (4 axes: X, Y, Z, W)
    axes: List[AxisStatus]

    # DAC values (8 channels)
    dac_values: List[int]

    # Illumination state
    illum_on_mask: int
    led_pattern: int

    # Joystick state
    joystick_delta_x: int
    joystick_delta_y: int
    buttons: int

    @property
    def x_pos(self) -> int:
        """X axis position in microsteps."""
        return self.axes[0].position_usteps if len(self.axes) > 0 else 0

    @property
    def y_pos(self) -> int:
        """Y axis position in microsteps."""
        return self.axes[1].position_usteps if len(self.axes) > 1 else 0

    @property
    def z_pos(self) -> int:
        """Z axis position in microsteps."""
        return self.axes[2].position_usteps if len(self.axes) > 2 else 0

    @property
    def w_pos(self) -> int:
        """W axis position in microsteps."""
        return self.axes[3].position_usteps if len(self.axes) > 3 else 0

    @property
    def joystick_button_pressed(self) -> bool:
        """Whether joystick button is pressed."""
        return bool(self.buttons & 0x01)


# Response packet size (must match firmware)
RESPONSE_SIZE = 78


def parse_response(data: bytes) -> Optional[ResponsePacket]:
    """
    Parse a response packet payload (without header/length/CRC).

    Args:
        data: The payload bytes (78 bytes expected)

    Returns:
        ResponsePacket if valid, None if data is wrong size
    """
    if len(data) != RESPONSE_SIZE:
        _log.warning(f"Response packet wrong size: {len(data)} != {RESPONSE_SIZE}")
        return None

    # Unpack command acknowledgment (3 bytes)
    cmd_id = data[0]
    status = ResponseStatus(data[1])
    error_code = ErrorCode(data[2])

    # System mode (1 byte)
    system_mode = SystemMode(data[3])

    # Axis states (4 axes × 12 bytes = 48 bytes)
    axes = []
    offset = 4
    for i in range(4):
        # Each axis: int32 position, int32 target, uint8 state, uint8 error, uint8 homed, uint8 reserved
        pos, target = struct.unpack_from("<ii", data, offset)
        axis_state = AxisState(data[offset + 8])
        axis_error = data[offset + 9]
        homed = bool(data[offset + 10])
        axes.append(
            AxisStatus(
                position_usteps=pos,
                target_usteps=target,
                state=axis_state,
                error_code=axis_error,
                homed=homed,
            )
        )
        offset += 12

    # DAC values (8 × 2 = 16 bytes)
    dac_values = list(struct.unpack_from("<8H", data, offset))
    offset += 16

    # Illumination (2 bytes)
    illum_on_mask = data[offset]
    led_pattern = data[offset + 1]
    offset += 2

    # Joystick state (5 bytes)
    joystick_delta_x, joystick_delta_y = struct.unpack_from("<hh", data, offset)
    buttons = data[offset + 4]

    return ResponsePacket(
        cmd_id=cmd_id,
        status=status,
        error_code=error_code,
        system_mode=system_mode,
        axes=axes,
        dac_values=dac_values,
        illum_on_mask=illum_on_mask,
        led_pattern=led_pattern,
        joystick_delta_x=joystick_delta_x,
        joystick_delta_y=joystick_delta_y,
        buttons=buttons,
    )


# =============================================================================
# Packet Building
# =============================================================================


def build_packet(cmd_id: int, cmd_type: CommandType, payload: bytes = b"") -> bytes:
    """
    Build a complete v2 protocol packet.

    Args:
        cmd_id: Command ID (0-255, wrapping)
        cmd_type: Command type enum
        payload: Additional payload bytes after cmd_id and cmd_type

    Returns:
        Complete packet bytes ready to send
    """
    # Build payload: cmd_id + cmd_type + extra payload
    full_payload = bytes([cmd_id, cmd_type]) + payload
    payload_length = len(full_payload)

    if payload_length > PACKET_MAX_PAYLOAD:
        raise ValueError(f"Payload too large: {payload_length} > {PACKET_MAX_PAYLOAD}")

    # Build packet
    packet = bytearray()

    # Header
    packet.append(PACKET_HEADER_0)
    packet.append(PACKET_HEADER_1)

    # Length (little-endian)
    packet.append(payload_length & 0xFF)
    packet.append((payload_length >> 8) & 0xFF)

    # Payload
    packet.extend(full_payload)

    # CRC over length + payload
    crc_data = bytes([payload_length & 0xFF, (payload_length >> 8) & 0xFF]) + full_payload
    crc = crc16_ccitt(crc_data)

    # CRC (little-endian)
    packet.append(crc & 0xFF)
    packet.append((crc >> 8) & 0xFF)

    return bytes(packet)


# =============================================================================
# Receive State Machine
# =============================================================================


class RxState(enum.IntEnum):
    """Receive state machine states."""

    RX_WAIT_HEADER_0 = 0
    RX_WAIT_HEADER_1 = 1
    RX_WAIT_LENGTH_0 = 2
    RX_WAIT_LENGTH_1 = 3
    RX_WAIT_PAYLOAD = 4
    RX_WAIT_CRC_0 = 5
    RX_WAIT_CRC_1 = 6


class PacketReceiver:
    """
    State machine for receiving v2 protocol packets.

    Processes bytes one at a time and emits complete packets.
    """

    def __init__(self, on_packet: Callable[[bytes], None]):
        """
        Args:
            on_packet: Callback called with complete packet payload when received
        """
        self._on_packet = on_packet
        self._state = RxState.RX_WAIT_HEADER_0
        self._payload_length = 0
        self._payload_received = 0
        self._crc_received = 0
        self._buffer = bytearray(PACKET_MAX_PAYLOAD)

    def reset(self):
        """Reset the receiver state machine."""
        self._state = RxState.RX_WAIT_HEADER_0
        self._payload_length = 0
        self._payload_received = 0
        self._crc_received = 0

    def process_byte(self, byte: int):
        """
        Process a single received byte.

        Args:
            byte: The byte value (0-255)
        """
        if self._state == RxState.RX_WAIT_HEADER_0:
            if byte == PACKET_HEADER_0:
                self._state = RxState.RX_WAIT_HEADER_1

        elif self._state == RxState.RX_WAIT_HEADER_1:
            if byte == PACKET_HEADER_1:
                self._state = RxState.RX_WAIT_LENGTH_0
            elif byte == PACKET_HEADER_0:
                # Could be start of new header
                self._state = RxState.RX_WAIT_HEADER_1
            else:
                self._state = RxState.RX_WAIT_HEADER_0

        elif self._state == RxState.RX_WAIT_LENGTH_0:
            self._payload_length = byte
            self._state = RxState.RX_WAIT_LENGTH_1

        elif self._state == RxState.RX_WAIT_LENGTH_1:
            self._payload_length |= byte << 8

            if self._payload_length == 0 or self._payload_length > PACKET_MAX_PAYLOAD:
                # Invalid length
                self._state = RxState.RX_WAIT_HEADER_0
            else:
                self._payload_received = 0
                self._state = RxState.RX_WAIT_PAYLOAD

        elif self._state == RxState.RX_WAIT_PAYLOAD:
            self._buffer[self._payload_received] = byte
            self._payload_received += 1
            if self._payload_received >= self._payload_length:
                self._state = RxState.RX_WAIT_CRC_0

        elif self._state == RxState.RX_WAIT_CRC_0:
            self._crc_received = byte
            self._state = RxState.RX_WAIT_CRC_1

        elif self._state == RxState.RX_WAIT_CRC_1:
            self._crc_received |= byte << 8

            # Validate CRC
            payload = bytes(self._buffer[: self._payload_length])
            crc_data = bytes([self._payload_length & 0xFF, (self._payload_length >> 8) & 0xFF]) + payload
            calculated_crc = crc16_ccitt(crc_data)

            if calculated_crc == self._crc_received:
                # Valid packet
                self._on_packet(payload)
            else:
                _log.warning(f"CRC mismatch: calculated={calculated_crc:04x}, received={self._crc_received:04x}")

            # Reset for next packet
            self._state = RxState.RX_WAIT_HEADER_0

    def process_bytes(self, data: bytes):
        """Process multiple bytes."""
        for byte in data:
            self.process_byte(byte)


# =============================================================================
# Protocol V2 Microcontroller Interface
# =============================================================================


class MicrocontrollerV2:
    """
    V2 protocol interface for the microcontroller.

    This class provides a high-level interface for sending commands and
    receiving responses using the v2 protocol format.
    """

    STALE_READ_TIMEOUT = 0.1
    MAX_RECONNECT_COUNT = 3
    COMMAND_TIMEOUT = 0.5

    def __init__(self, serial_device, reset_and_initialize: bool = True):
        """
        Args:
            serial_device: AbstractCephlaMicroSerial device for communication
            reset_and_initialize: Whether to reset and initialize on startup
        """
        self.log = squid.logging.get_logger(self.__class__.__name__)

        if not serial_device:
            raise ValueError("serial_device is required")

        self._serial = serial_device
        self._cmd_id = 0

        # Current system state (updated from responses)
        self._last_response: Optional[ResponsePacket] = None
        self._last_response_time = time.time()
        self._response_lock = threading.Lock()
        self._response_cv = threading.Condition(self._response_lock)

        # Command tracking
        self._pending_cmd_id: Optional[int] = None
        self._cmd_send_time = 0.0

        # Packet receiver
        self._receiver = PacketReceiver(self._on_packet_received)

        # Read thread
        self._terminate_read_thread = False
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()

        # Joystick event listeners
        self.joystick_listener_events_enabled = False
        self._joystick_listeners: List[Tuple[int, Callable[[bool], None]]] = []
        self._last_joystick_button_state = False

        if reset_and_initialize:
            self.log.debug("Resetting and initializing microcontroller (v2 protocol)")
            time.sleep(0.5)
            self.reset()
            time.sleep(0.5)

    def close(self):
        """Close the connection and stop the read thread."""
        self._terminate_read_thread = True
        self._read_thread.join(timeout=1.0)
        self._serial.close()

    def _on_packet_received(self, payload: bytes):
        """Called by PacketReceiver when a complete packet is received."""
        response = parse_response(payload)
        if response is None:
            return

        with self._response_lock:
            self._last_response = response
            self._last_response_time = time.time()

            # Check if this acknowledges our pending command
            if self._pending_cmd_id is not None and response.cmd_id == self._pending_cmd_id:
                self._pending_cmd_id = None
                self._response_cv.notify_all()

            # Handle joystick button events
            button_pressed = response.joystick_button_pressed
            if button_pressed != self._last_joystick_button_state:
                self._last_joystick_button_state = button_pressed
                if self.joystick_listener_events_enabled:
                    for _, listener in self._joystick_listeners:
                        try:
                            listener(button_pressed)
                        except Exception as e:
                            self.log.error(f"Joystick listener error: {e}")

    def _read_loop(self):
        """Background thread for reading serial data."""
        while not self._terminate_read_thread:
            try:
                available = self._serial.bytes_available()
                if available > 0:
                    data = self._serial.read(available, reconnect_tries=self.MAX_RECONNECT_COUNT)
                    self._receiver.process_bytes(data)
                else:
                    time.sleep(0.001)  # Avoid busy spinning
            except Exception as e:
                self.log.error(f"Read loop error: {e}")
                time.sleep(0.1)

    def _send_command(self, cmd_type: CommandType, payload: bytes = b"") -> int:
        """
        Send a command and return the command ID.

        Args:
            cmd_type: The command type
            payload: Additional payload bytes

        Returns:
            The command ID used
        """
        self._cmd_id = (self._cmd_id + 1) % 256
        packet = build_packet(self._cmd_id, cmd_type, payload)

        with self._response_lock:
            self._pending_cmd_id = self._cmd_id
            self._cmd_send_time = time.time()

        self._serial.write(bytearray(packet), reconnect_tries=self.MAX_RECONNECT_COUNT)

        return self._cmd_id

    def wait_till_operation_is_completed(self, timeout_limit_s: float = 5.0):
        """
        Wait for the current command to complete.

        Args:
            timeout_limit_s: Maximum time to wait in seconds

        Raises:
            TimeoutError: If the command times out
        """
        with self._response_cv:
            if self._pending_cmd_id is None:
                return

            result = self._response_cv.wait_for(lambda: self._pending_cmd_id is None, timeout=timeout_limit_s)

            if not result:
                raise TimeoutError(f"Command timed out after {timeout_limit_s} [s]")

    def is_busy(self) -> bool:
        """Check if a command is pending acknowledgment."""
        with self._response_lock:
            return self._pending_cmd_id is not None

    # =========================================================================
    # System Commands
    # =========================================================================

    def get_state(self) -> Optional[ResponsePacket]:
        """
        Request current system state.

        Returns:
            The response packet, or None if no response received
        """
        self._send_command(CommandType.CMD_GET_STATE)
        try:
            self.wait_till_operation_is_completed(timeout_limit_s=1.0)
        except TimeoutError:
            self.log.warning("get_state timed out")
        return self._last_response

    def reset(self):
        """Reset the firmware state."""
        self._send_command(CommandType.CMD_RESET)
        self._cmd_id = 0  # Reset also resets the firmware's command ID tracking

    def get_version(self) -> Optional[ResponsePacket]:
        """
        Request firmware version information.

        Returns:
            The response packet
        """
        self._send_command(CommandType.CMD_GET_VERSION)
        try:
            self.wait_till_operation_is_completed(timeout_limit_s=1.0)
        except TimeoutError:
            self.log.warning("get_version timed out")
        return self._last_response

    # =========================================================================
    # Position Properties (for compatibility with existing code)
    # =========================================================================

    @property
    def x_pos(self) -> int:
        """Current X position in microsteps."""
        with self._response_lock:
            return self._last_response.x_pos if self._last_response else 0

    @property
    def y_pos(self) -> int:
        """Current Y position in microsteps."""
        with self._response_lock:
            return self._last_response.y_pos if self._last_response else 0

    @property
    def z_pos(self) -> int:
        """Current Z position in microsteps."""
        with self._response_lock:
            return self._last_response.z_pos if self._last_response else 0

    @property
    def w_pos(self) -> int:
        """Current W position in microsteps."""
        with self._response_lock:
            return self._last_response.w_pos if self._last_response else 0

    @property
    def theta_pos(self) -> int:
        """Current theta position (not yet supported in v2, returns 0)."""
        return 0

    @property
    def joystick_button_pressed(self) -> bool:
        """Whether joystick button is pressed."""
        with self._response_lock:
            return self._last_response.joystick_button_pressed if self._last_response else False

    def get_pos(self) -> Tuple[int, int, int, int]:
        """Get all axis positions."""
        with self._response_lock:
            if self._last_response:
                return (
                    self._last_response.x_pos,
                    self._last_response.y_pos,
                    self._last_response.z_pos,
                    0,  # theta not in v2 response
                )
            return (0, 0, 0, 0)

    # =========================================================================
    # Joystick Listener Support
    # =========================================================================

    def add_joystick_button_listener(self, listener: Callable[[bool], None]) -> int:
        """
        Add a listener for joystick button events.

        Args:
            listener: Callback function that receives button state (True=pressed)

        Returns:
            Listener ID for later removal
        """
        try:
            next_id = max(t[0] for t in self._joystick_listeners) + 1
        except ValueError:
            next_id = 1
        self._joystick_listeners.append((next_id, listener))
        return next_id

    def remove_joystick_button_listener(self, listener_id: int):
        """Remove a joystick button listener by ID."""
        self._joystick_listeners = [(lid, fn) for lid, fn in self._joystick_listeners if lid != listener_id]

    def enable_joystick(self, enabled: bool):
        """Enable or disable joystick event dispatching."""
        self.joystick_listener_events_enabled = enabled
