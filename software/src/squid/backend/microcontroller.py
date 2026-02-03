import enum
import time
import threading
from typing import Callable

import numpy as np
from crc import CrcCalculator, Crc8

import squid.core.logging
from _def import (
    AXIS,
    BIT_POS_JOYSTICK_BUTTON,
    BIT_POS_SWITCH,
    BUFFER_SIZE_LIMIT,
    CMD_EXECUTION_STATUS,
    CMD_SET,
    HOME_OR_ZERO,
    ILLUMINATION_INTENSITY_FACTOR,
    ILLUMINATION_TIMEOUT_S,
    MAX_ACCELERATION_W_mm,
    RESPONSE_BYTE_FIRMWARE_VERSION,
    RESPONSE_BYTE_PORT_STATUS,
    MAX_ACCELERATION_X_mm,
    MAX_ACCELERATION_Y_mm,
    MAX_ACCELERATION_Z_mm,
    MAX_ILLUMINATION_TIMEOUT_MS,
    MAX_VELOCITY_W_mm,
    MAX_VELOCITY_X_mm,
    MAX_VELOCITY_Y_mm,
    MAX_VELOCITY_Z_mm,
    MCU_PINS,
    MICROSTEPPING_DEFAULT_W,
    MICROSTEPPING_DEFAULT_X,
    MICROSTEPPING_DEFAULT_Y,
    MICROSTEPPING_DEFAULT_Z,
    MicrocontrollerDef,
    NUM_TIMEOUT_PORTS,
    OBJECTIVE_PIEZO_FLIP_DIR,
    OBJECTIVE_PIEZO_RANGE_UM,
    SCREW_PITCH_W_MM,
    SCREW_PITCH_X_MM,
    SCREW_PITCH_Y_MM,
    SCREW_PITCH_Z_MM,
    STAGE_MOVEMENT_SIGN_THETA,
    STAGE_MOVEMENT_SIGN_W,
    STAGE_MOVEMENT_SIGN_W2,
    STAGE_MOVEMENT_SIGN_X,
    STAGE_MOVEMENT_SIGN_Y,
    STAGE_MOVEMENT_SIGN_Z,
    W_MOTOR_I_HOLD,
    W_MOTOR_RMS_CURRENT_mA,
    X_HOME_SAFETY_MARGIN_UM,
    X_HOME_SWITCH_POLARITY,
    X_MOTOR_I_HOLD,
    X_MOTOR_RMS_CURRENT_mA,
    Y_HOME_SAFETY_MARGIN_UM,
    Y_HOME_SWITCH_POLARITY,
    Y_MOTOR_I_HOLD,
    Y_MOTOR_RMS_CURRENT_mA,
    Z_HOME_SAFETY_MARGIN_UM,
    Z_HOME_SWITCH_POLARITY,
    Z_MOTOR_I_HOLD,
    Z_MOTOR_RMS_CURRENT_mA,
)
from squid.backend.drivers.stages.serial import (
    AbstractCephlaMicroSerial,
    SimSerial,
    get_microcontroller_serial_device,
)


# add user to the dialout group to avoid the need to use sudo

# done (7/20/2021) - remove the time.sleep in all functions (except for __init__) to
# make all callable functions nonblocking, instead, user should check use is_busy() to
# check if the microcontroller has finished executing the more recent command

# We have a few top level functions here, so we have this module level log instance.  Classes should make their own!
_log = squid.core.logging.get_logger("microcontroller")

# Mapping of command type bytes to human-readable names for logging
_CMD_NAMES = {
    CMD_SET.MOVE_X: "MOVE_X",
    CMD_SET.MOVE_Y: "MOVE_Y",
    CMD_SET.MOVE_Z: "MOVE_Z",
    CMD_SET.MOVE_THETA: "MOVE_THETA",
    CMD_SET.MOVE_W: "MOVE_W",
    CMD_SET.MOVE_W2: "MOVE_W2",
    CMD_SET.HOME_OR_ZERO: "HOME_OR_ZERO",
    CMD_SET.MOVETO_X: "MOVETO_X",
    CMD_SET.MOVETO_Y: "MOVETO_Y",
    CMD_SET.MOVETO_Z: "MOVETO_Z",
    CMD_SET.SET_LIM: "SET_LIM",
    CMD_SET.TURN_ON_ILLUMINATION: "TURN_ON_ILLUMINATION",
    CMD_SET.TURN_OFF_ILLUMINATION: "TURN_OFF_ILLUMINATION",
    CMD_SET.SET_ILLUMINATION: "SET_ILLUMINATION",
    CMD_SET.SET_ILLUMINATION_LED_MATRIX: "SET_ILLUMINATION_LED_MATRIX",
    CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED: "ACK_JOYSTICK_BUTTON_PRESSED",
    CMD_SET.ANALOG_WRITE_ONBOARD_DAC: "ANALOG_WRITE_ONBOARD_DAC",
    CMD_SET.SET_DAC80508_REFDIV_GAIN: "SET_DAC80508_REFDIV_GAIN",
    CMD_SET.SET_ILLUMINATION_INTENSITY_FACTOR: "SET_ILLUMINATION_INTENSITY_FACTOR",
    CMD_SET.MOVETO_W: "MOVETO_W",
    CMD_SET.SET_LIM_SWITCH_POLARITY: "SET_LIM_SWITCH_POLARITY",
    CMD_SET.CONFIGURE_STEPPER_DRIVER: "CONFIGURE_STEPPER_DRIVER",
    CMD_SET.SET_MAX_VELOCITY_ACCELERATION: "SET_MAX_VELOCITY_ACCELERATION",
    CMD_SET.SET_LEAD_SCREW_PITCH: "SET_LEAD_SCREW_PITCH",
    CMD_SET.SET_OFFSET_VELOCITY: "SET_OFFSET_VELOCITY",
    CMD_SET.CONFIGURE_STAGE_PID: "CONFIGURE_STAGE_PID",
    CMD_SET.ENABLE_STAGE_PID: "ENABLE_STAGE_PID",
    CMD_SET.DISABLE_STAGE_PID: "DISABLE_STAGE_PID",
    CMD_SET.SET_HOME_SAFETY_MERGIN: "SET_HOME_SAFETY_MERGIN",
    CMD_SET.SET_PID_ARGUMENTS: "SET_PID_ARGUMENTS",
    CMD_SET.SEND_HARDWARE_TRIGGER: "SEND_HARDWARE_TRIGGER",
    CMD_SET.SET_STROBE_DELAY: "SET_STROBE_DELAY",
    CMD_SET.SET_AXIS_DISABLE_ENABLE: "SET_AXIS_DISABLE_ENABLE",
    CMD_SET.SET_PIN_LEVEL: "SET_PIN_LEVEL",
    # Multi-port illumination commands (firmware v1.0+)
    CMD_SET.SET_PORT_INTENSITY: "SET_PORT_INTENSITY",
    CMD_SET.TURN_ON_PORT: "TURN_ON_PORT",
    CMD_SET.TURN_OFF_PORT: "TURN_OFF_PORT",
    CMD_SET.SET_PORT_ILLUMINATION: "SET_PORT_ILLUMINATION",
    CMD_SET.SET_MULTI_PORT_MASK: "SET_MULTI_PORT_MASK",
    CMD_SET.TURN_OFF_ALL_PORTS: "TURN_OFF_ALL_PORTS",
    CMD_SET.SET_ILLUMINATION_TIMEOUT: "SET_ILLUMINATION_TIMEOUT",
    CMD_SET.INITFILTERWHEEL_W2: "INITFILTERWHEEL_W2",
    CMD_SET.INITFILTERWHEEL: "INITFILTERWHEEL",
    CMD_SET.INITIALIZE: "INITIALIZE",
    CMD_SET.RESET: "RESET",
    CMD_SET.SET_TRIGGER_MODE: "SET_TRIGGER_MODE",
}


# "move backward" if SIGN is 1, "move forward" if SIGN is -1
class HomingDirection(enum.Enum):
    HOMING_DIRECTION_FORWARD = 0
    HOMING_DIRECTION_BACKWARD = 1


def movement_sign_to_homing_direction(sign: int) -> HomingDirection:
    if sign not in (-1, 1):
        raise ValueError("Only -1 and 1 are valid movement signs.")
    return HomingDirection(int((sign + 1) / 2))


_default_x_homing_direction = movement_sign_to_homing_direction(STAGE_MOVEMENT_SIGN_X)
_default_y_homing_direction = movement_sign_to_homing_direction(STAGE_MOVEMENT_SIGN_Y)
_default_z_homing_direction = movement_sign_to_homing_direction(STAGE_MOVEMENT_SIGN_Z)
_default_theta_homing_direction = movement_sign_to_homing_direction(
    STAGE_MOVEMENT_SIGN_THETA
)
_default_w_homing_direction = movement_sign_to_homing_direction(STAGE_MOVEMENT_SIGN_W)
_default_w2_homing_direction = movement_sign_to_homing_direction(STAGE_MOVEMENT_SIGN_W2)


# to do (7/28/2021) - add functions for configuring the stepper motors
class CommandAborted(RuntimeError):
    """
    If we send a command and it needs to abort for any reason (too many retries,
    timeout waiting for the mcu to acknowledge, etc), the Microcontroller class will throw this
    for wait and progress check operations until a new command is started.

    This does mean that if you don't check for command completion, you may miss these errors!
    """

    def __init__(self, command_id, reason):
        super().__init__(reason)
        self.command_id = command_id


class Microcontroller:
    LAST_COMMAND_ACK_TIMEOUT = 0.5
    MAX_RETRY_COUNT = 5
    MAX_RECONNECT_COUNT = 3
    # The micro has an update time it tries to keep to.  This must be > that time.  As of 2025-04-28, it's 10ms
    # on the micro.  So 0.1 is 10x that.
    STALE_READ_TIMEOUT = 0.1

    def __init__(
        self,
        serial_device: AbstractCephlaMicroSerial,
        reset_and_initialize: bool = True,
    ) -> None:
        self.log = squid.core.logging.get_logger(self.__class__.__name__)

        if not serial_device:
            raise ValueError(
                "You must pass in an AbstractCephlaSerial device for the microcontroller instance to use."
            )

        self._serial = serial_device
        self._is_simulated = isinstance(serial_device, SimSerial)

        self.tx_buffer_length = MicrocontrollerDef.CMD_LENGTH
        self.rx_buffer_length = MicrocontrollerDef.MSG_LENGTH

        self._cmd_id = 0
        self._cmd_id_mcu = None  # command id of mcu's last received command
        self._cmd_execution_status = None
        self.mcu_cmd_execution_in_progress = False

        # This is a sentinel/watchdog of sorts.  Every time we receive a valid packet from the micro, we update this.
        # The micro should be sending packets once very ~10 ms, so if we go much longer than that without something
        # is likely wrong.  See def _warn_if_reads_stale() and the read loop.
        self._last_successful_read_time = time.time()
        self._stale_warning_issued = False

        self.x_pos = 0  # unit: microstep or encoder resolution
        self.y_pos = 0  # unit: microstep or encoder resolution
        self.z_pos = 0  # unit: microstep or encoder resolution
        self.w_pos = 0  # unit: microstep or encoder resolution
        self.w2_pos = 0  # unit: microstep or encoder resolution
        self.theta_pos = 0  # unit: microstep or encoder resolution
        self.button_and_switch_state = 0
        self.joystick_button_pressed = 0
        # This is used to keep track of whether or not we should emit joystick events to the joystick listeners,
        # and can be changed with enable_joystick(...)
        self.joystick_listener_events_enabled = False
        # This is a list of (id, functions) to call when we get a joystick event.  The functions are called
        # with the new state of the button (True -> pressed, False -> not pressed).
        #
        # The id in the tuple is an internally defined unique ID that callers to add_joystick_button_listener() get back,
        # and which can be used to remove the listener with remove_joystick_button_listener.
        #
        # These are called in our busy loop, and so should return immediately!
        self.joystick_event_listeners = []
        self.switch_state = 0

        # Firmware version (major, minor) - detected from response byte 22
        # (0, 0) indicates legacy firmware without version reporting
        self.firmware_version = (0, 0)

        # Illumination port on/off state from firmware (byte 19)
        # Updated from periodic position updates, reflects actual hardware state
        # Index 0-4 = ports D1-D5, matching firmware NUM_TIMEOUT_PORTS
        self.illumination_port_is_on = [False] * NUM_TIMEOUT_PORTS

        self.last_command = None
        self.last_command_send_timestamp = time.time()
        self.last_command_aborted_error = None

        self.crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)
        self.retry = 0

        self.new_packet_callback_external = None
        self.terminate_reading_received_packet_thread = False
        self._received_packet_cv = threading.Condition()
        self.thread_read_received_packet = threading.Thread(
            target=self.read_received_packet, daemon=True
        )
        self.thread_read_received_packet.start()

        if reset_and_initialize:
            self.log.debug("Resetting and initializing microcontroller.")
            self.reset()
            time.sleep(0.5)
            self.initialize_drivers()
            time.sleep(0.5)
            self.configure_actuators()
            self.set_dac80508_scaling_factor_for_illumination(
                ILLUMINATION_INTENSITY_FACTOR
            )
            time.sleep(0.5)

        # Detect firmware version early by sending a harmless command
        # This ensures supports_multi_port() returns accurate results immediately
        self._detect_firmware_version()

        # Configure illumination auto-shutoff timeout (safety feature, firmware v1.1+)
        if self.firmware_version >= (1, 1):
            self.set_illumination_timeout(ILLUMINATION_TIMEOUT_S)
            self.wait_till_operation_is_completed()
            self.log.info(f"Illumination timeout configured: {ILLUMINATION_TIMEOUT_S}s")

    def _warn_if_reads_stale(self) -> None:
        # Skip this warning in simulation - the simulated serial doesn't behave the same
        if self._is_simulated:
            return
        now = time.time()
        last_read = float(
            self._last_successful_read_time
        )  # Just in case it gets update, capture it for printing below.
        if now - last_read > Microcontroller.STALE_READ_TIMEOUT:
            if not self._stale_warning_issued:
                self._stale_warning_issued = True
                self.log.warning(
                    f"Read thread is stale, it has been {now - last_read} [s] since a valid packet. Last cmd id from the mcu was {self._cmd_id_mcu}, our last sent cmd id was {self._cmd_id}"
                )

    def close(self) -> None:
        self.terminate_reading_received_packet_thread = True
        self.thread_read_received_packet.join()
        self._serial.close()

    def add_joystick_button_listener(self, listener: Callable[[bool], None]) -> None:
        try:
            next_id = max(t[0] for t in self.joystick_event_listeners) + 1
        except ValueError:
            next_id = 1
        self.log.debug(f"Adding joystick button listener with id={next_id}")
        self.joystick_event_listeners.append((next_id, listener))

    def remove_joystick_button_listener(self, id_to_remove: int) -> None:
        try:
            idx = [t[0] for t in self.joystick_event_listeners].index(id_to_remove)
            self.log.debug(
                f"Removing joystick button listener id={id_to_remove} at idx={idx}"
            )
            del self.joystick_event_listeners[idx]
        except ValueError:
            self.log.warning(
                f"Asked to remove joystick button listener {id_to_remove}, but it is not a known listener id"
            )

    def enable_joystick(self, enabled: bool) -> None:
        self.joystick_listener_events_enabled = enabled

    def reset(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.RESET
        self.log.debug("reset the microcontroller")
        self.send_command(cmd)
        # On the microcontroller side, reset forces the command Id back to 0
        # so any responses will look like they are for command id 0.  Force that
        # here.
        self._cmd_id = 0

    def initialize_drivers(self) -> None:
        self._cmd_id = 0
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.INITIALIZE
        self.send_command(cmd)
        self.log.debug("initialize the drivers")

    def init_filter_wheel(self, axis=AXIS.W) -> None:
        """Initialize a filter wheel axis.

        Args:
            axis: The axis to initialize (AXIS.W or AXIS.W2). Defaults to AXIS.W.
        """
        cmd_map = {AXIS.W: CMD_SET.INITFILTERWHEEL, AXIS.W2: CMD_SET.INITFILTERWHEEL_W2}
        if axis not in cmd_map:
            raise ValueError(f"Unsupported filter wheel axis: {axis}. Expected AXIS.W or AXIS.W2.")
        self._cmd_id = 0
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = cmd_map[axis]
        self.send_command(cmd)

    def turn_on_illumination(self) -> None:
        self.log.debug("[MCU] turn_on_illumination")
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        self.send_command(cmd)

    def turn_off_illumination(self) -> None:
        self.log.debug("[MCU] turn_off_illumination")
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.TURN_OFF_ILLUMINATION
        self.send_command(cmd)

    def set_illumination(self, illumination_source: int, intensity: float) -> None:
        self.log.debug(f"[MCU] set_illumination: source={illumination_source}, intensity={intensity}")
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_ILLUMINATION
        cmd[2] = illumination_source
        cmd[3] = int((intensity / 100) * 65535) >> 8
        cmd[4] = int((intensity / 100) * 65535) & 0xFF
        self.send_command(cmd)

    def set_illumination_led_matrix(
        self, illumination_source: int, r: float, g: float, b: float
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_ILLUMINATION_LED_MATRIX
        cmd[2] = illumination_source
        cmd[3] = min(int(g * 255), 255)
        cmd[4] = min(int(r * 255), 255)
        cmd[5] = min(int(b * 255), 255)
        self.send_command(cmd)

    # Multi-port illumination commands (firmware v1.0+)
    # These allow multiple ports to be ON simultaneously with independent intensities
    _MAX_ILLUMINATION_PORTS = 16

    def _validate_port_index(self, port_index: int):
        """Validate port index is in valid range (0-15)."""
        if not isinstance(port_index, int):
            raise TypeError(f"port_index must be an integer, got {type(port_index).__name__}")
        if port_index < 0 or port_index >= self._MAX_ILLUMINATION_PORTS:
            raise ValueError(f"Invalid port_index {port_index}, must be 0-{self._MAX_ILLUMINATION_PORTS - 1}")

    def set_port_intensity(self, port_index: int, intensity: float) -> None:
        """Set DAC intensity for a specific port without changing on/off state.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
            intensity: Intensity percentage (0-100), clamped to valid range
        """
        self._validate_port_index(port_index)
        self.log.debug(f"[MCU] set_port_intensity: port={port_index}, intensity={intensity}")
        intensity = max(0, min(100, intensity))
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_PORT_INTENSITY
        cmd[2] = port_index
        intensity_value = int((intensity / 100) * 65535)
        cmd[3] = intensity_value >> 8
        cmd[4] = intensity_value & 0xFF
        self.send_command(cmd)

    def turn_on_port(self, port_index: int) -> None:
        """Turn on a specific illumination port.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
        """
        self._validate_port_index(port_index)
        self.log.debug(f"[MCU] turn_on_port: port={port_index}")
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.TURN_ON_PORT
        cmd[2] = port_index
        self.send_command(cmd)

    def turn_off_port(self, port_index: int) -> None:
        """Turn off a specific illumination port.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
        """
        self._validate_port_index(port_index)
        self.log.debug(f"[MCU] turn_off_port: port={port_index}")
        # Update local state immediately to prevent spurious auto-shutoff warning
        if port_index < len(self.illumination_port_is_on):
            self.illumination_port_is_on[port_index] = False
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.TURN_OFF_PORT
        cmd[2] = port_index
        self.send_command(cmd)

    def set_port_illumination(self, port_index: int, intensity: float, turn_on: bool) -> None:
        """Set intensity and on/off state for a specific port in one command.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
            intensity: Intensity percentage (0-100), clamped to valid range
            turn_on: Whether to turn the port on
        """
        self._validate_port_index(port_index)
        self.log.debug(f"[MCU] set_port_illumination: port={port_index}, intensity={intensity}, on={turn_on}")
        # Update local state immediately to prevent spurious auto-shutoff warning
        if not turn_on and port_index < len(self.illumination_port_is_on):
            self.illumination_port_is_on[port_index] = False
        intensity = max(0, min(100, intensity))
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_PORT_ILLUMINATION
        cmd[2] = port_index
        intensity_value = int((intensity / 100) * 65535)
        cmd[3] = intensity_value >> 8
        cmd[4] = intensity_value & 0xFF
        cmd[5] = 1 if turn_on else 0
        self.send_command(cmd)

    def set_multi_port_mask(self, port_mask: int, on_mask: int) -> None:
        """Set on/off state for multiple ports using masks.

        Args:
            port_mask: 16-bit mask of which ports to update (bit 0=D1, bit 15=D16)
            on_mask: 16-bit mask of on/off state for selected ports (1=on, 0=off)

        Example:
            set_multi_port_mask(0x0007, 0x0003)  # port_mask=D1|D2|D3, on_mask=D1|D2
        """
        self.log.debug(f"[MCU] set_multi_port_mask: port_mask=0x{port_mask:04X}, on_mask=0x{on_mask:04X}")
        # Update local state immediately for ports being turned off to prevent spurious warnings
        for i in range(len(self.illumination_port_is_on)):
            if port_mask & (1 << i) and not (on_mask & (1 << i)):
                self.illumination_port_is_on[i] = False
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_MULTI_PORT_MASK
        cmd[2] = (port_mask >> 8) & 0xFF
        cmd[3] = port_mask & 0xFF
        cmd[4] = (on_mask >> 8) & 0xFF
        cmd[5] = on_mask & 0xFF
        self.send_command(cmd)

    def turn_off_all_ports(self) -> None:
        """Turn off all illumination ports."""
        self.log.debug("[MCU] turn_off_all_ports")
        # Update local state immediately to prevent spurious auto-shutoff warnings
        for i in range(len(self.illumination_port_is_on)):
            self.illumination_port_is_on[i] = False
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.TURN_OFF_ALL_PORTS
        self.send_command(cmd)

    def set_illumination_timeout(self, timeout_s: float) -> None:
        """Set firmware illumination auto-shutoff timeout.

        The firmware will automatically turn off any illumination port that has
        been continuously on for longer than this timeout. This is a safety feature
        to protect against software crashes or bugs that leave lasers on.

        Args:
            timeout_s: Timeout in seconds. Valid range is 0 to 3600 (1 hour).
                A value of 0 tells the firmware to use its default timeout (3s).
        """
        timeout_ms = int(timeout_s * 1000)
        original_ms = timeout_ms
        timeout_ms = max(0, min(timeout_ms, MAX_ILLUMINATION_TIMEOUT_MS))

        if timeout_ms != original_ms:
            max_s = MAX_ILLUMINATION_TIMEOUT_MS / 1000.0
            self.log.warning(
                f"[MCU] set_illumination_timeout: requested {timeout_s}s clamped to "
                f"{timeout_ms / 1000.0}s (valid range: 0-{max_s}s)"
            )

        self.log.debug(f"[MCU] set_illumination_timeout: {timeout_s}s ({timeout_ms}ms)")

        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_ILLUMINATION_TIMEOUT
        cmd[2] = (timeout_ms >> 24) & 0xFF
        cmd[3] = (timeout_ms >> 16) & 0xFF
        cmd[4] = (timeout_ms >> 8) & 0xFF
        cmd[5] = timeout_ms & 0xFF
        self.send_command(cmd)

    def send_hardware_trigger(
        self,
        control_illumination: bool = False,
        illumination_on_time_us: int = 0,
        trigger_output_ch: int = 0,
    ) -> None:
        illumination_on_time_us = int(illumination_on_time_us)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SEND_HARDWARE_TRIGGER
        cmd[2] = (
            control_illumination << 7
        ) + trigger_output_ch  # MSB: whether illumination is controlled
        cmd[3] = illumination_on_time_us >> 24
        cmd[4] = (illumination_on_time_us >> 16) & 0xFF
        cmd[5] = (illumination_on_time_us >> 8) & 0xFF
        cmd[6] = illumination_on_time_us & 0xFF
        self.send_command(cmd)

    def set_strobe_delay_us(
        self, strobe_delay_us: int, camera_channel: int = 0
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_STROBE_DELAY
        cmd[2] = camera_channel
        cmd[3] = strobe_delay_us >> 24
        cmd[4] = (strobe_delay_us >> 16) & 0xFF
        cmd[5] = (strobe_delay_us >> 8) & 0xFF
        cmd[6] = strobe_delay_us & 0xFF
        self.send_command(cmd)

    def set_axis_enable_disable(self, axis: int, status: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_AXIS_DISABLE_ENABLE
        cmd[2] = axis
        cmd[3] = status
        self.send_command(cmd)

    def set_trigger_mode(self, mode: int) -> None:
        """Set the hardware trigger mode.

        Args:
            mode: 0 for edge trigger (fixed pulse width), 1 for level trigger
                  (variable pulse width based on illumination time)
        """
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_TRIGGER_MODE
        cmd[2] = mode
        self.send_command(cmd)

    def _move_axis_usteps(self, usteps: int, axis_command_code: int) -> None:
        direction = np.sign(usteps)
        n_microsteps_abs = abs(usteps)
        # if n_microsteps_abs exceed the max value that can be sent in one go
        while n_microsteps_abs >= (2**32) / 2:
            n_microsteps_partial_abs = (2**32) / 2 - 1
            n_microsteps_partial = direction * n_microsteps_partial_abs
            payload = self._int_to_payload(n_microsteps_partial, 4)
            cmd = bytearray(self.tx_buffer_length)
            cmd[1] = axis_command_code
            cmd[2] = payload >> 24
            cmd[3] = (payload >> 16) & 0xFF
            cmd[4] = (payload >> 8) & 0xFF
            cmd[5] = payload & 0xFF
            # TODO(imo): Since this issues multiple commands, there's no way to check for and abort failed
            # ones mid-move.
            self.send_command(cmd)
            n_microsteps_abs = n_microsteps_abs - n_microsteps_partial_abs
        n_microsteps = direction * n_microsteps_abs
        payload = self._int_to_payload(n_microsteps, 4)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = axis_command_code
        cmd[2] = payload >> 24
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def move_x_usteps(self, usteps: int) -> None:
        self._move_axis_usteps(usteps, CMD_SET.MOVE_X)

    def move_x_to_usteps(self, usteps: int) -> None:
        payload = self._int_to_payload(usteps, 4)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.MOVETO_X
        cmd[2] = payload >> 24
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def move_y_usteps(self, usteps):
        self._move_axis_usteps(usteps, CMD_SET.MOVE_Y)

    def move_y_to_usteps(self, usteps: int) -> None:
        payload = self._int_to_payload(usteps, 4)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.MOVETO_Y
        cmd[2] = payload >> 24
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def move_z_usteps(self, usteps: int) -> None:
        self._move_axis_usteps(usteps, CMD_SET.MOVE_Z)

    def move_z_to_usteps(self, usteps: int) -> None:
        payload = self._int_to_payload(usteps, 4)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.MOVETO_Z
        cmd[2] = payload >> 24
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def move_theta_usteps(self, usteps: int) -> None:
        self._move_axis_usteps(usteps, CMD_SET.MOVE_THETA)

    def move_w_usteps(self, usteps: int):
        self._move_axis_usteps(usteps, CMD_SET.MOVE_W)

    def move_w2_usteps(self, usteps: int):
        self._move_axis_usteps(usteps, CMD_SET.MOVE_W2)

    def set_off_set_velocity_x(self, off_set_velocity: float) -> None:
        # off_set_velocity is in mm/s
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_OFFSET_VELOCITY
        cmd[2] = AXIS.X
        off_set_velocity = off_set_velocity * 1000000
        payload = self._int_to_payload(off_set_velocity, 4)
        cmd[3] = payload >> 24
        cmd[4] = (payload >> 16) & 0xFF
        cmd[5] = (payload >> 8) & 0xFF
        cmd[6] = payload & 0xFF
        self.send_command(cmd)

    def set_off_set_velocity_y(self, off_set_velocity: float) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_OFFSET_VELOCITY
        cmd[2] = AXIS.Y
        off_set_velocity = off_set_velocity * 1000000
        payload = self._int_to_payload(off_set_velocity, 4)
        cmd[3] = payload >> 24
        cmd[4] = (payload >> 16) & 0xFF
        cmd[5] = (payload >> 8) & 0xFF
        cmd[6] = payload & 0xFF
        self.send_command(cmd)

    def home_x(
        self, homing_direction: HomingDirection = _default_x_homing_direction
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.X
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_y(
        self, homing_direction: HomingDirection = _default_y_homing_direction
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Y
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_z(
        self, homing_direction: HomingDirection = _default_z_homing_direction
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Z
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_theta(
        self, homing_direction: HomingDirection = _default_theta_homing_direction
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = 3
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_xy(
        self,
        homing_direction_x: HomingDirection = _default_x_homing_direction,
        homing_direction_y: HomingDirection = _default_y_homing_direction,
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.XY
        cmd[3] = homing_direction_x.value
        cmd[4] = homing_direction_y.value
        self.send_command(cmd)

    def home_w(
        self, homing_direction: HomingDirection = _default_w_homing_direction
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.W
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_w2(
        self, homing_direction: HomingDirection = _default_w2_homing_direction
    ) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.W2
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def zero_x(self) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.X
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_y(self) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Y
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_z(self) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Z
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_w(self) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.W
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_w2(self) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.W2
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_theta(self) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.THETA
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def configure_stage_pid(
        self, axis: int, transitions_per_revolution: int, flip_direction: bool = False
    ) -> None:
        if not isinstance(transitions_per_revolution, int):
            self.log.warning(
                f"transitions_per_revolution must be an integer, truncating: {transitions_per_revolution}"
            )

        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.CONFIGURE_STAGE_PID
        cmd[2] = axis
        cmd[3] = int(flip_direction)
        payload = self._int_to_payload(transitions_per_revolution, 2)
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def turn_on_stage_pid(self, axis: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.ENABLE_STAGE_PID
        cmd[2] = axis
        self.send_command(cmd)

    def turn_off_stage_pid(self, axis: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.DISABLE_STAGE_PID
        cmd[2] = axis
        self.send_command(cmd)

    def turn_off_all_pid(self) -> None:
        for primary_axis_id in [AXIS.X, AXIS.Y, AXIS.Z]:
            self.turn_off_stage_pid(primary_axis_id)
            self.wait_till_operation_is_completed()

    def set_pid_arguments(self, axis: int, pid_p: int, pid_i: int, pid_d: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_PID_ARGUMENTS
        cmd[2] = int(axis)

        cmd[3] = (int(pid_p) >> 8) & 0xFF
        cmd[4] = int(pid_p) & 0xFF

        cmd[5] = int(pid_i)
        cmd[6] = int(pid_d)
        self.send_command(cmd)

    def set_lim(self, limit_code: int, usteps: int) -> None:
        self.log.info(f"Set lim: {limit_code=}, {usteps=}")
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_LIM
        cmd[2] = limit_code
        payload = self._int_to_payload(usteps, 4)
        cmd[3] = payload >> 24
        cmd[4] = (payload >> 16) & 0xFF
        cmd[5] = (payload >> 8) & 0xFF
        cmd[6] = payload & 0xFF
        self.send_command(cmd)

    def set_limit_switch_polarity(self, axis: int, polarity: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_LIM_SWITCH_POLARITY
        cmd[2] = axis
        cmd[3] = polarity
        self.send_command(cmd)

    def set_home_safety_margin(self, axis: int, margin: int) -> None:
        margin = abs(margin)
        if margin > 0xFFFF:
            margin = 0xFFFF
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_HOME_SAFETY_MERGIN
        cmd[2] = axis
        cmd[3] = (margin >> 8) & 0xFF
        cmd[4] = (margin) & 0xFF
        self.send_command(cmd)

    def configure_motor_driver(
        self, axis: int, microstepping: int, current_rms: int, I_hold: float
    ) -> None:
        # current_rms in mA
        # I_hold 0.0-1.0
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.CONFIGURE_STEPPER_DRIVER
        cmd[2] = axis
        if microstepping == 1:
            cmd[3] = 0
        elif microstepping == 256:
            cmd[3] = (
                255  # max of uint8 is 255 - will be changed to 255 after received by the MCU
            )
        else:
            cmd[3] = microstepping
        cmd[4] = current_rms >> 8
        cmd[5] = current_rms & 0xFF
        cmd[6] = int(I_hold * 255)
        self.send_command(cmd)

    def set_max_velocity_acceleration(
        self, axis: int, velocity: float, acceleration: float
    ) -> None:
        # velocity: max 65535/100 mm/s
        # acceleration: max 65535/10 mm/s^2
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_MAX_VELOCITY_ACCELERATION
        cmd[2] = axis
        cmd[3] = int(velocity * 100) >> 8
        cmd[4] = int(velocity * 100) & 0xFF
        cmd[5] = int(acceleration * 10) >> 8
        cmd[6] = int(acceleration * 10) & 0xFF
        self.send_command(cmd)

    def set_leadscrew_pitch(self, axis: int, pitch_mm: float) -> None:
        # pitch: max 65535/1000 = 65.535 (mm)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_LEAD_SCREW_PITCH
        cmd[2] = axis
        cmd[3] = int(pitch_mm * 1000) >> 8
        cmd[4] = int(pitch_mm * 1000) & 0xFF
        self.send_command(cmd)

    def configure_actuators(self):
        # lead screw pitch
        self.set_leadscrew_pitch(AXIS.X, SCREW_PITCH_X_MM)
        self.wait_till_operation_is_completed()
        self.set_leadscrew_pitch(AXIS.Y, SCREW_PITCH_Y_MM)
        self.wait_till_operation_is_completed()
        self.set_leadscrew_pitch(AXIS.Z, SCREW_PITCH_Z_MM)
        self.wait_till_operation_is_completed()
        # stepper driver (microstepping,rms current and I_hold)
        self.configure_motor_driver(
            AXIS.X, MICROSTEPPING_DEFAULT_X, X_MOTOR_RMS_CURRENT_mA, X_MOTOR_I_HOLD
        )
        self.wait_till_operation_is_completed()
        self.configure_motor_driver(
            AXIS.Y, MICROSTEPPING_DEFAULT_Y, Y_MOTOR_RMS_CURRENT_mA, Y_MOTOR_I_HOLD
        )
        self.wait_till_operation_is_completed()
        self.configure_motor_driver(
            AXIS.Z, MICROSTEPPING_DEFAULT_Z, Z_MOTOR_RMS_CURRENT_mA, Z_MOTOR_I_HOLD
        )
        self.wait_till_operation_is_completed()
        # max velocity and acceleration
        self.set_max_velocity_acceleration(
            AXIS.X, MAX_VELOCITY_X_mm, MAX_ACCELERATION_X_mm
        )
        self.wait_till_operation_is_completed()
        self.set_max_velocity_acceleration(
            AXIS.Y, MAX_VELOCITY_Y_mm, MAX_ACCELERATION_Y_mm
        )
        self.wait_till_operation_is_completed()
        self.set_max_velocity_acceleration(
            AXIS.Z, MAX_VELOCITY_Z_mm, MAX_ACCELERATION_Z_mm
        )
        self.wait_till_operation_is_completed()
        # home switch
        self.set_limit_switch_polarity(AXIS.X, X_HOME_SWITCH_POLARITY)
        self.wait_till_operation_is_completed()
        self.set_limit_switch_polarity(AXIS.Y, Y_HOME_SWITCH_POLARITY)
        self.wait_till_operation_is_completed()
        self.set_limit_switch_polarity(AXIS.Z, Z_HOME_SWITCH_POLARITY)
        self.wait_till_operation_is_completed()
        # home safety margin
        self.set_home_safety_margin(AXIS.X, int(X_HOME_SAFETY_MARGIN_UM))
        self.wait_till_operation_is_completed()
        self.set_home_safety_margin(AXIS.Y, int(Y_HOME_SAFETY_MARGIN_UM))
        self.wait_till_operation_is_completed()
        self.set_home_safety_margin(AXIS.Z, int(Z_HOME_SAFETY_MARGIN_UM))
        self.wait_till_operation_is_completed()

    def configure_squidfilter(self, axis=AXIS.W):
        """Configure a filter wheel motor.

        Args:
            axis: The axis to configure (AXIS.W or AXIS.W2). Defaults to AXIS.W.
        """
        if axis not in (AXIS.W, AXIS.W2):
            raise ValueError(f"Unsupported filter wheel axis: {axis}. Expected AXIS.W or AXIS.W2.")
        self.set_leadscrew_pitch(axis, SCREW_PITCH_W_MM)
        self.wait_till_operation_is_completed()
        self.configure_motor_driver(
            axis, MICROSTEPPING_DEFAULT_W, W_MOTOR_RMS_CURRENT_mA, W_MOTOR_I_HOLD
        )
        self.wait_till_operation_is_completed()
        self.set_max_velocity_acceleration(
            axis, MAX_VELOCITY_W_mm, MAX_ACCELERATION_W_mm
        )
        self.wait_till_operation_is_completed()

    def ack_joystick_button_pressed(self) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED
        self.send_command(cmd)

    def analog_write_onboard_DAC(self, dac: int, value: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.ANALOG_WRITE_ONBOARD_DAC
        cmd[2] = dac
        cmd[3] = (value >> 8) & 0xFF
        cmd[4] = value & 0xFF
        self.send_command(cmd)

    def set_piezo_um(self, z_piezo_um: float) -> None:
        dac = int(65535 * (z_piezo_um / OBJECTIVE_PIEZO_RANGE_UM))
        dac = 65535 - dac if OBJECTIVE_PIEZO_FLIP_DIR else dac
        self.analog_write_onboard_DAC(7, dac)

    def configure_dac80508_refdiv_and_gain(self, div: int, gains: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_DAC80508_REFDIV_GAIN
        cmd[2] = div
        cmd[3] = gains
        self.send_command(cmd)

    def set_pin_level(self, pin: int, level: int) -> None:
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_PIN_LEVEL
        cmd[2] = pin
        cmd[3] = level
        self.send_command(cmd)

    def turn_on_AF_laser(self) -> None:
        self.set_pin_level(MCU_PINS.AF_LASER, 1)

    def turn_off_AF_laser(self) -> None:
        self.set_pin_level(MCU_PINS.AF_LASER, 0)

    def send_command(self, command: bytearray) -> None:
        self._cmd_id = (self._cmd_id + 1) % 256
        command[0] = self._cmd_id
        command[-1] = self.crc_calculator.calculate_checksum(command[:-1])
        cmd_type = command[1]
        cmd_name = _CMD_NAMES.get(cmd_type, f"UNKNOWN({cmd_type})")
        self.log.debug(f"[MCU] >>> sending command {self._cmd_id}, type={cmd_name}")
        self._serial.write(command, reconnect_tries=Microcontroller.MAX_RECONNECT_COUNT)
        self.mcu_cmd_execution_in_progress = True
        self.last_command = command
        self.last_command_send_timestamp = time.time()
        self.retry = 0

        if self.last_command_aborted_error is not None:
            self.log.warning(
                "Last command aborted and not cleared before new command sent!",
                self.last_command_aborted_error,
            )
        self.last_command_aborted_error = None

        self._warn_if_reads_stale()

    def abort_current_command(self, reason: str) -> None:
        cmd_type = self.last_command[1] if self.last_command is not None else -1
        cmd_name = _CMD_NAMES.get(cmd_type, f"UNKNOWN({cmd_type})")
        self.log.error(f"[MCU] !!! Command {self._cmd_id} ({cmd_name}) ABORTED: {reason}")
        self.last_command_aborted_error = CommandAborted(
            reason=reason, command_id=self._cmd_id
        )
        self.mcu_cmd_execution_in_progress = False

    def acknowledge_aborted_command(self) -> None:
        if self.last_command_aborted_error is None:
            self.log.warning(
                "Request to ack aborted command, but there is no aborted command."
            )

        self.last_command_aborted_error = None

    def resend_last_command(self) -> None:
        if self.last_command is not None:
            self._serial.write(
                self.last_command, reconnect_tries=Microcontroller.MAX_RECONNECT_COUNT
            )
            self.mcu_cmd_execution_in_progress = True
            # We use the retry count for both checksum errors, and to keep track of
            # timeout re-attempts.
            self.last_command_send_timestamp = time.time()
            self.retry = self.retry + 1
        else:
            self.log.warning(
                "resend requested with no last_command, something is wrong!"
            )
            self.abort_current_command("Resend last requested with no last command")

    def read_received_packet(self) -> None:
        crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)
        last_watchdog_fail_report = time.time()
        watchdog_fail_report_period = 5.0

        while not self.terminate_reading_received_packet_thread:
            try:
                # If anything hangs, we may fall way behind reading the serial buffer.  In that case, toss everything
                # in the read buffer and start over.  This should always be safe to do because the micro sends updates
                # periodically without prompting, and so we'll always get more updates on the current micro state.
                if self._serial.bytes_available() >= BUFFER_SIZE_LIMIT:
                    self._serial.reset_input_buffer()
                    continue

                # If we don't at least have enough bytes for a full packet (rx_buffer_length), there's no reason to
                # waste our time looking for a valid message.  So wait here until we have at least that many bytes.
                if self._serial.bytes_available() < self.rx_buffer_length:
                    if (
                        time.time() - last_watchdog_fail_report
                        > watchdog_fail_report_period
                    ):
                        last_watchdog_fail_report = time.time()
                        self._warn_if_reads_stale()
                    # Sleep a negligible amount of time just to give other threads time to run.  Otherwise,
                    # we run the rise of spinning forever here and not letting progress happen elsewhere.
                    time.sleep(0.0001)
                    if not self._serial.is_open():
                        if not self._serial.reconnect(
                            attempts=Microcontroller.MAX_RECONNECT_COUNT
                        ):
                            self.log.error(
                                "In read loop, serial device failed to reconnect.  Microcontroller is defunct!"
                            )
                    continue

                # This helper reads bytes in the order received by serial, and checks that packet-sized chunks
                # have valid checksums.  IF the first packet size chunk does not have a valid checksum, it tosses
                # the first byte and keeps going until it either finds a valid checksum packet or runs out of bytes.
                def get_msg_with_good_checksum():
                    maybe_msg = []
                    while self._serial.bytes_available() > 0:
                        maybe_msg.append(ord(self._serial.read()))
                        if len(maybe_msg) < self.rx_buffer_length:
                            continue

                        checksum = crc_calculator.calculate_checksum(maybe_msg[:-1])
                        # NOTE(imo): Before April 2025, we didn't send the crc from the micro.  This is
                        # here to support firmware that still sends 0 as the checksum.  This means for
                        # the firmware that does support checksums, we can get fooled by zeros!
                        if checksum == maybe_msg[-1] or maybe_msg[-1] == 0:
                            return maybe_msg
                        else:
                            self.log.warning(
                                f"Bad checksum {checksum} for packet '{maybe_msg}, tossing first byte'"
                            )
                            maybe_msg.pop(0)
                    return None

                msg = get_msg_with_good_checksum()

                if msg is None:
                    self.log.warning("Back checksums found, skipping.")
                    continue

                # parse the message
                """
                - byte 0: command ID (1 byte)
                - byte 1: execution status (1 byte)
                - bytes 2-5: X pos (4 bytes)
                - bytes 6-9: Y pos (4 bytes)
                - bytes 10-13: Z pos (4 bytes)
                - bytes 14-17: Theta (4 bytes)
                - byte 18: buttons and switches (1 byte)
                - byte 19: illumination port status, bits 0-4 = D1-D5 (firmware v1.1+)
                - bytes 20-21: reserved (2 bytes)
                - byte 22: firmware version, high nibble = major, low nibble = minor
                - byte 23: CRC (1 byte)
                """
                self._last_successful_read_time = time.time()
                self._stale_warning_issued = False
                self._cmd_id_mcu = msg[0]
                self._cmd_execution_status = msg[1]
                if (self._cmd_id_mcu == self._cmd_id) and (
                    self._cmd_execution_status
                    == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
                ):
                    if self.mcu_cmd_execution_in_progress:
                        self.mcu_cmd_execution_in_progress = False
                        elapsed_ms = (time.time() - self.last_command_send_timestamp) * 1000
                        cmd_type = self.last_command[1] if self.last_command is not None else -1
                        cmd_name = _CMD_NAMES.get(cmd_type, f"UNKNOWN({cmd_type})")
                        self.log.debug(
                            f"[MCU] <<< command {self._cmd_id} ({cmd_name}) complete (took {elapsed_ms:.1f}ms)"
                        )
                elif (
                    self.mcu_cmd_execution_in_progress
                    and self._cmd_id_mcu != self._cmd_id
                    and time.time() - self.last_command_send_timestamp
                    > self.LAST_COMMAND_ACK_TIMEOUT
                    and self.last_command is not None
                ):
                    if self.retry > self.MAX_RETRY_COUNT:
                        self.abort_current_command(
                            reason=f"Command timed out without an ack after {self.LAST_COMMAND_ACK_TIMEOUT} [s], and {self.retry} retries"
                        )
                    else:
                        self.log.debug(
                            f"[MCU] !!! command timed out without ack after {self.LAST_COMMAND_ACK_TIMEOUT}s, resending command"
                        )
                        self.resend_last_command()
                elif (
                    self.mcu_cmd_execution_in_progress
                    and self._cmd_execution_status
                    == CMD_EXECUTION_STATUS.CMD_CHECKSUM_ERROR
                ):
                    if self.retry > self.MAX_RETRY_COUNT:
                        self.abort_current_command(
                            reason=f"Checksum error and 10 retries for {self._cmd_id}"
                        )
                    else:
                        self.log.error("[MCU] !!! checksum error, resending command")
                        self.resend_last_command()
                elif (
                    self.mcu_cmd_execution_in_progress
                    and self._cmd_id_mcu != self._cmd_id
                    and self._cmd_execution_status == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
                ):
                    # Log when we receive an ACK for a different command than we're waiting for
                    self.log.debug(
                        f"[MCU] !!! received ack for command {self._cmd_id_mcu}, but waiting for command {self._cmd_id}"
                    )

                self.x_pos = self._payload_to_int(
                    msg[2:6], MicrocontrollerDef.N_BYTES_POS
                )  # unit: microstep or encoder resolution
                self.y_pos = self._payload_to_int(
                    msg[6:10], MicrocontrollerDef.N_BYTES_POS
                )  # unit: microstep or encoder resolution
                self.z_pos = self._payload_to_int(
                    msg[10:14], MicrocontrollerDef.N_BYTES_POS
                )  # unit: microstep or encoder resolution
                self.theta_pos = self._payload_to_int(
                    msg[14:18], MicrocontrollerDef.N_BYTES_POS
                )  # unit: microstep or encoder resolution

                self.button_and_switch_state = msg[18]
                # joystick button
                tmp = self.button_and_switch_state & (1 << BIT_POS_JOYSTICK_BUTTON)
                joystick_button_pressed = tmp > 0
                if self.joystick_button_pressed != joystick_button_pressed:
                    if self.joystick_listener_events_enabled:
                        for _, listener_fn in self.joystick_event_listeners:
                            listener_fn(joystick_button_pressed)

                    # The microcontroller wants us to send an ack back only when we see a False -> True
                    # transition. handle that here.
                    if joystick_button_pressed:
                        self.ack_joystick_button_pressed()
                self.joystick_button_pressed = joystick_button_pressed

                # switch
                tmp = self.button_and_switch_state & (1 << BIT_POS_SWITCH)
                self.switch_state = tmp > 0

                # Firmware version: high nibble = major, low nibble = minor
                # Legacy firmware (pre-v1.0) sends 0x00, which gives version (0, 0)
                version_byte = msg[RESPONSE_BYTE_FIRMWARE_VERSION]
                self.firmware_version = (version_byte >> 4, version_byte & 0x0F)

                # Illumination port status (firmware v1.1+)
                # Bits 0-4 = D1-D5 on/off state
                port_status_byte = msg[RESPONSE_BYTE_PORT_STATUS]
                for i in range(NUM_TIMEOUT_PORTS):
                    new_state = bool(port_status_byte & (1 << i))
                    old_state = self.illumination_port_is_on[i]
                    if old_state and not new_state:
                        # Port was on but firmware reports it off — likely auto-shutoff
                        self.log.warning(
                            f"Illumination port {i} turned off by firmware (auto-shutoff timeout)"
                        )
                    self.illumination_port_is_on[i] = new_state

                with self._received_packet_cv:
                    self._received_packet_cv.notify_all()

                if self.new_packet_callback_external is not None:
                    self.new_packet_callback_external(self)
            except Exception as e:
                self.log.error(
                    "Read loop failed, continuing to loop to see if anything can recover.",
                    exc_info=e,
                )

    def get_pos(self) -> tuple:
        return self.x_pos, self.y_pos, self.z_pos, self.theta_pos

    def _detect_firmware_version(self):
        """Detect firmware version by sending a harmless command.

        Sends TURN_OFF_ALL_PORTS (a safe no-op if ports are already off)
        to trigger a response from which we can read the firmware version.
        """
        self.turn_off_all_ports()
        self.wait_till_operation_is_completed()
        self.log.debug(f"Detected firmware version: {self.firmware_version}")

    def supports_multi_port(self) -> bool:
        """Check if firmware supports multi-port illumination commands.

        Multi-port illumination was added in firmware version 1.0.

        Returns:
            True if firmware version >= 1.0, False otherwise.
        """
        return self.firmware_version >= (1, 0)

    def get_button_and_switch_state(self) -> int:
        return self.button_and_switch_state

    def is_busy(self) -> bool:
        return self.mcu_cmd_execution_in_progress

    def set_callback(self, function: callable) -> None:
        self.new_packet_callback_external = function

    def wait_till_operation_is_completed(self, timeout_limit_s: float = 5) -> None:
        """
        Wait for the current command to complete.  If the wait times out, the current command isn't touched.  To
        abort it, you should call the abort_current_command(...) method.
        """
        with self._received_packet_cv:

            def still_busy():
                return self.is_busy() and self.last_command_aborted_error is None

            self._received_packet_cv.wait_for(
                lambda: not still_busy(), timeout=timeout_limit_s
            )

            if still_busy():
                raise TimeoutError(
                    f"Current mcu operation timed out after {timeout_limit_s} [s]."
                )

            if self.last_command_aborted_error is not None:
                raise self.last_command_aborted_error

    @staticmethod
    def _int_to_payload(signed_int: int, number_of_bytes: int) -> int:
        actually_signed_int = int(round(signed_int))
        if actually_signed_int >= 0:
            payload = actually_signed_int
        else:
            payload = (
                2 ** (8 * number_of_bytes) + actually_signed_int
            )  # find two's complement
        return int(payload)

    @staticmethod
    def _payload_to_int(payload: bytes, number_of_bytes: int) -> int:
        signed = 0
        for i in range(number_of_bytes):
            signed = signed + int(payload[i]) * (256 ** (number_of_bytes - 1 - i))
        if signed >= 256**number_of_bytes / 2:
            signed = signed - 256**number_of_bytes
        return int(signed)

    def set_dac80508_scaling_factor_for_illumination(
        self, illumination_intensity_factor
    ):
        if illumination_intensity_factor > 1:
            illumination_intensity_factor = 1

        if illumination_intensity_factor < 0:
            illumination_intensity_factor = 0.01

        factor = round(illumination_intensity_factor, 2) * 100
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_ILLUMINATION_INTENSITY_FACTOR
        cmd[2] = int(factor)
        self.send_command(cmd)
