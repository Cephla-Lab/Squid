import enum
import time
import threading
from typing import Callable

import numpy as np
from crc import CrcCalculator, Crc8

import squid.logging
from control._def import *
from control.stage.serial import (
    AbstractCephlaMicroSerial,
    SimSerial,
    MicrocontrollerSerial,
    get_microcontroller_serial_device,
)


# add user to the dialout group to avoid the need to use sudo

# done (7/20/2021) - remove the time.sleep in all functions (except for __init__) to
# make all callable functions nonblocking, instead, user should check use is_busy() to
# check if the microcontroller has finished executing the more recent command

# We have a few top level functions here, so we have this module level log instance.  Classes should make their own!
_log = squid.logging.get_logger("microcontroller")


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
_default_theta_homing_direction = movement_sign_to_homing_direction(STAGE_MOVEMENT_SIGN_THETA)
_default_w_homing_direction = movement_sign_to_homing_direction(STAGE_MOVEMENT_SIGN_W)


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

    def __init__(self, serial_device: AbstractCephlaMicroSerial, reset_and_initialize=True):
        self.log = squid.logging.get_logger(self.__class__.__name__)

        if not serial_device:
            raise ValueError("You must pass in an AbstractCephlaSerial device for the microcontroller instance to use.")

        self._serial = serial_device

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

        self.x_pos = 0  # unit: microstep or encoder resolution
        self.y_pos = 0  # unit: microstep or encoder resolution
        self.z_pos = 0  # unit: microstep or encoder resolution
        self.w_pos = 0  # unit: microstep or encoder resolution
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

        self.last_command = None
        self.last_command_send_timestamp = time.time()
        self.last_command_aborted_error = None

        self.crc_calculator = CrcCalculator(Crc8.CCITT, table_based=True)
        self.retry = 0

        self.new_packet_callback_external = None
        self.terminate_reading_received_packet_thread = False
        self._received_packet_cv = threading.Condition()
        self.thread_read_received_packet = threading.Thread(target=self.read_received_packet, daemon=True)
        self.thread_read_received_packet.start()

        if reset_and_initialize:
            self.log.debug("Resetting and initializing microcontroller.")
            self.reset()
            time.sleep(0.5)
            self.initialize_drivers()
            time.sleep(0.5)
            self.configure_actuators()
            self.set_dac80508_scaling_factor_for_illumination(ILLUMINATION_INTENSITY_FACTOR)
            time.sleep(0.5)

    def _warn_if_reads_stale(self):
        now = time.time()
        last_read = float(
            self._last_successful_read_time
        )  # Just in case it gets update, capture it for printing below.
        if now - last_read > Microcontroller.STALE_READ_TIMEOUT:
            self.log.warning(
                f"Read thread is stale, it has been {now - last_read} [s] since a valid packet. Last cmd id from the mcu was {self._cmd_id_mcu}, our last sent cmd id was {self._cmd_id}"
            )

    def close(self):
        self.terminate_reading_received_packet_thread = True
        self.thread_read_received_packet.join()
        self._serial.close()

    def add_joystick_button_listener(self, listener: Callable[[bool], None]):
        try:
            next_id = max(t[0] for t in self.joystick_event_listeners) + 1
        except ValueError as e:
            next_id = 1
        self.log.debug(f"Adding joystick button listener with id={next_id}")
        self.joystick_event_listeners.append((next_id, listener))

    def remove_joystick_button_listener(self, id_to_remove):
        try:
            idx = [t[0] for t in self.joystick_event_listeners].index(id_to_remove)
            self.log.debug(f"Removing joystick button listener id={id_to_remove} at idx={idx}")
            del self.joystick_event_listeners[idx]
        except ValueError as e:
            self.log.warning(
                f"Asked to remove joystick button listener {id_to_remove}, but it is not a known listener id"
            )

    def enable_joystick(self, enabled: bool):
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

    def initialize_drivers(self):
        self._cmd_id = 0
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.INITIALIZE
        self.send_command(cmd)
        self.log.debug("initialize the drivers")

    def init_filter_wheel(self):
        self._cmd_id = 0
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.INITFILTERWHEEL
        self.send_command(cmd)
        print("initialize filter wheel")  # debug

    def turn_on_illumination(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.TURN_ON_ILLUMINATION
        self.send_command(cmd)

    def turn_off_illumination(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.TURN_OFF_ILLUMINATION
        self.send_command(cmd)

    def set_illumination(self, illumination_source, intensity):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_ILLUMINATION
        cmd[2] = illumination_source
        cmd[3] = int((intensity / 100) * 65535) >> 8
        cmd[4] = int((intensity / 100) * 65535) & 0xFF
        self.send_command(cmd)

    def set_illumination_led_matrix(self, illumination_source, r, g, b):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_ILLUMINATION_LED_MATRIX
        cmd[2] = illumination_source
        cmd[3] = min(int(g * 255), 255)
        cmd[4] = min(int(r * 255), 255)
        cmd[5] = min(int(b * 255), 255)
        self.send_command(cmd)

    def send_hardware_trigger(self, control_illumination=False, illumination_on_time_us=0, trigger_output_ch=0):
        illumination_on_time_us = int(illumination_on_time_us)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SEND_HARDWARE_TRIGGER
        cmd[2] = (control_illumination << 7) + trigger_output_ch  # MSB: whether illumination is controlled
        cmd[3] = illumination_on_time_us >> 24
        cmd[4] = (illumination_on_time_us >> 16) & 0xFF
        cmd[5] = (illumination_on_time_us >> 8) & 0xFF
        cmd[6] = illumination_on_time_us & 0xFF
        self.send_command(cmd)

    def set_strobe_delay_us(self, strobe_delay_us, camera_channel=0):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_STROBE_DELAY
        cmd[2] = camera_channel
        cmd[3] = strobe_delay_us >> 24
        cmd[4] = (strobe_delay_us >> 16) & 0xFF
        cmd[5] = (strobe_delay_us >> 8) & 0xFF
        cmd[6] = strobe_delay_us & 0xFF
        self.send_command(cmd)

    def set_axis_enable_disable(self, axis, status):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_AXIS_DISABLE_ENABLE
        cmd[2] = axis
        cmd[3] = status
        self.send_command(cmd)

    def _move_axis_usteps(self, usteps, axis_command_code):
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

    def move_x_usteps(self, usteps):
        self._move_axis_usteps(usteps, CMD_SET.MOVE_X)

    def move_x_to_usteps(self, usteps):
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

    def move_y_to_usteps(self, usteps):
        payload = self._int_to_payload(usteps, 4)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.MOVETO_Y
        cmd[2] = payload >> 24
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def move_z_usteps(self, usteps):
        self._move_axis_usteps(usteps, CMD_SET.MOVE_Z)

    def move_z_to_usteps(self, usteps):
        payload = self._int_to_payload(usteps, 4)
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.MOVETO_Z
        cmd[2] = payload >> 24
        cmd[3] = (payload >> 16) & 0xFF
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def move_theta_usteps(self, usteps):
        self._move_axis_usteps(usteps, CMD_SET.MOVE_THETA)

    def move_w_usteps(self, usteps):
        self._move_axis_usteps(usteps, CMD_SET.MOVE_W)

    def set_off_set_velocity_x(self, off_set_velocity):
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

    def set_off_set_velocity_y(self, off_set_velocity):
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

    def home_x(self, homing_direction: HomingDirection = _default_x_homing_direction):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.X
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_y(self, homing_direction: HomingDirection = _default_y_homing_direction):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Y
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_z(self, homing_direction: HomingDirection = _default_z_homing_direction):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Z
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_theta(self, homing_direction: HomingDirection = _default_theta_homing_direction):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = 3
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def home_xy(
        self,
        homing_direction_x: HomingDirection = _default_x_homing_direction,
        homing_direction_y: HomingDirection = _default_y_homing_direction,
    ):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.XY
        cmd[3] = homing_direction_x.value
        cmd[4] = homing_direction_y.value
        self.send_command(cmd)

    def home_w(self, homing_direction: HomingDirection = _default_w_homing_direction):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.W
        cmd[3] = homing_direction.value
        self.send_command(cmd)

    def zero_x(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.X
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_y(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Y
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_z(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.Z
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_w(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.W
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def zero_theta(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.HOME_OR_ZERO
        cmd[2] = AXIS.THETA
        cmd[3] = HOME_OR_ZERO.ZERO
        self.send_command(cmd)

    def configure_stage_pid(self, axis, transitions_per_revolution, flip_direction=False):
        if not isinstance(transitions_per_revolution, int):
            self.log.warning(f"transitions_per_revolution must be an integer, truncating: {transitions_per_revolution}")

        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.CONFIGURE_STAGE_PID
        cmd[2] = axis
        cmd[3] = int(flip_direction)
        payload = self._int_to_payload(transitions_per_revolution, 2)
        cmd[4] = (payload >> 8) & 0xFF
        cmd[5] = payload & 0xFF
        self.send_command(cmd)

    def turn_on_stage_pid(self, axis):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.ENABLE_STAGE_PID
        cmd[2] = axis
        self.send_command(cmd)

    def turn_off_stage_pid(self, axis):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.DISABLE_STAGE_PID
        cmd[2] = axis
        self.send_command(cmd)

    def turn_off_all_pid(self):
        for primary_axis_id in [AXIS.X, AXIS.Y, AXIS.Z]:
            self.turn_off_stage_pid(primary_axis_id)
            self.wait_till_operation_is_completed()

    def set_pid_arguments(self, axis, pid_p, pid_i, pid_d):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_PID_ARGUMENTS
        cmd[2] = int(axis)

        cmd[3] = (int(pid_p) >> 8) & 0xFF
        cmd[4] = int(pid_p) & 0xFF

        cmd[5] = int(pid_i)
        cmd[6] = int(pid_d)
        self.send_command(cmd)

    def set_lim(self, limit_code, usteps):
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

    def set_limit_switch_polarity(self, axis, polarity):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_LIM_SWITCH_POLARITY
        cmd[2] = axis
        cmd[3] = polarity
        self.send_command(cmd)

    def set_home_safety_margin(self, axis, margin):
        margin = abs(margin)
        if margin > 0xFFFF:
            margin = 0xFFFF
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_HOME_SAFETY_MERGIN
        cmd[2] = axis
        cmd[3] = (margin >> 8) & 0xFF
        cmd[4] = (margin) & 0xFF
        self.send_command(cmd)

    def configure_motor_driver(self, axis, microstepping, current_rms, I_hold):
        # current_rms in mA
        # I_hold 0.0-1.0
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.CONFIGURE_STEPPER_DRIVER
        cmd[2] = axis
        if microstepping == 1:
            cmd[3] = 0
        elif microstepping == 256:
            cmd[3] = 255  # max of uint8 is 255 - will be changed to 255 after received by the MCU
        else:
            cmd[3] = microstepping
        cmd[4] = current_rms >> 8
        cmd[5] = current_rms & 0xFF
        cmd[6] = int(I_hold * 255)
        self.send_command(cmd)

    def set_max_velocity_acceleration(self, axis, velocity, acceleration):
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

    def set_leadscrew_pitch(self, axis, pitch_mm):
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
        self.configure_motor_driver(AXIS.X, MICROSTEPPING_DEFAULT_X, X_MOTOR_RMS_CURRENT_mA, X_MOTOR_I_HOLD)
        self.wait_till_operation_is_completed()
        self.configure_motor_driver(AXIS.Y, MICROSTEPPING_DEFAULT_Y, Y_MOTOR_RMS_CURRENT_mA, Y_MOTOR_I_HOLD)
        self.wait_till_operation_is_completed()
        self.configure_motor_driver(AXIS.Z, MICROSTEPPING_DEFAULT_Z, Z_MOTOR_RMS_CURRENT_mA, Z_MOTOR_I_HOLD)
        self.wait_till_operation_is_completed()
        # max velocity and acceleration
        self.set_max_velocity_acceleration(AXIS.X, MAX_VELOCITY_X_mm, MAX_ACCELERATION_X_mm)
        self.wait_till_operation_is_completed()
        self.set_max_velocity_acceleration(AXIS.Y, MAX_VELOCITY_Y_mm, MAX_ACCELERATION_Y_mm)
        self.wait_till_operation_is_completed()
        self.set_max_velocity_acceleration(AXIS.Z, MAX_VELOCITY_Z_mm, MAX_ACCELERATION_Z_mm)
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

    def configure_squidfilter(self):
        self.set_leadscrew_pitch(AXIS.W, SCREW_PITCH_W_MM)
        self.wait_till_operation_is_completed()
        self.configure_motor_driver(AXIS.W, MICROSTEPPING_DEFAULT_W, W_MOTOR_RMS_CURRENT_mA, W_MOTOR_I_HOLD)
        self.wait_till_operation_is_completed()
        self.set_max_velocity_acceleration(AXIS.W, MAX_VELOCITY_W_mm, MAX_ACCELERATION_W_mm)
        self.wait_till_operation_is_completed()

    def ack_joystick_button_pressed(self):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.ACK_JOYSTICK_BUTTON_PRESSED
        self.send_command(cmd)

    def analog_write_onboard_DAC(self, dac, value):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.ANALOG_WRITE_ONBOARD_DAC
        cmd[2] = dac
        cmd[3] = (value >> 8) & 0xFF
        cmd[4] = value & 0xFF
        self.send_command(cmd)

    def set_piezo_um(self, z_piezo_um):
        dac = int(65535 * (z_piezo_um / OBJECTIVE_PIEZO_RANGE_UM))
        dac = 65535 - dac if OBJECTIVE_PIEZO_FLIP_DIR else dac
        self.analog_write_onboard_DAC(7, dac)

    def configure_dac80508_refdiv_and_gain(self, div, gains):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_DAC80508_REFDIV_GAIN
        cmd[2] = div
        cmd[3] = gains
        self.send_command(cmd)

    def set_pin_level(self, pin, level):
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_PIN_LEVEL
        cmd[2] = pin
        cmd[3] = level
        self.send_command(cmd)

    def turn_on_AF_laser(self):
        self.set_pin_level(MCU_PINS.AF_LASER, 1)

    def turn_off_AF_laser(self):
        self.set_pin_level(MCU_PINS.AF_LASER, 0)

    def send_command(self, command):
        self._cmd_id = (self._cmd_id + 1) % 256
        command[0] = self._cmd_id
        command[-1] = self.crc_calculator.calculate_checksum(command[:-1])
        self._serial.write(command, reconnect_tries=Microcontroller.MAX_RECONNECT_COUNT)
        self.mcu_cmd_execution_in_progress = True
        self.last_command = command
        self.last_command_send_timestamp = time.time()
        self.retry = 0

        if self.last_command_aborted_error is not None:
            self.log.warning(
                "Last command aborted and not cleared before new command sent!", self.last_command_aborted_error
            )
        self.last_command_aborted_error = None

        self._warn_if_reads_stale()

    def abort_current_command(self, reason):
        self.log.error(f"Command id={self._cmd_id} aborted for reason='{reason}'")
        self.last_command_aborted_error = CommandAborted(reason=reason, command_id=self._cmd_id)
        self.mcu_cmd_execution_in_progress = False

    def acknowledge_aborted_command(self):
        if self.last_command_aborted_error is None:
            self.log.warning("Request to ack aborted command, but there is no aborted command.")

        self.last_command_aborted_error = None

    def resend_last_command(self):
        if self.last_command is not None:
            self._serial.write(self.last_command, reconnect_tries=Microcontroller.MAX_RECONNECT_COUNT)
            self.mcu_cmd_execution_in_progress = True
            # We use the retry count for both checksum errors, and to keep track of
            # timeout re-attempts.
            self.last_command_send_timestamp = time.time()
            self.retry = self.retry + 1
        else:
            self.log.warning("resend requested with no last_command, something is wrong!")
            self.abort_current_command("Resend last requested with no last command")

    def read_received_packet(self):
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
                    if time.time() - last_watchdog_fail_report > watchdog_fail_report_period:
                        last_watchdog_fail_report = time.time()
                        self._warn_if_reads_stale()
                    # Sleep a negligible amount of time just to give other threads time to run.  Otherwise,
                    # we run the rise of spinning forever here and not letting progress happen elsewhere.
                    time.sleep(0.0001)
                    if not self._serial.is_open():
                        if not self._serial.reconnect(attempts=Microcontroller.MAX_RECONNECT_COUNT):
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
                            self.log.warning(f"Bad checksum {checksum} for packet '{maybe_msg}, tossing first byte'")
                            maybe_msg.pop(0)
                    return None

                msg = get_msg_with_good_checksum()

                if msg is None:
                    self.log.warning("Back checksums found, skipping.")
                    continue

                # parse the message
                """
                - command ID (1 byte)
                - execution status (1 byte)
                - X pos (4 bytes)
                - Y pos (4 bytes)
                - Z pos (4 bytes)
                - Theta (4 bytes)
                - buttons and switches (1 byte)
                - reserved (4 bytes)
                - CRC (1 byte)
                """
                self._last_successful_read_time = time.time()
                self._cmd_id_mcu = msg[0]
                self._cmd_execution_status = msg[1]
                if (self._cmd_id_mcu == self._cmd_id) and (
                    self._cmd_execution_status == CMD_EXECUTION_STATUS.COMPLETED_WITHOUT_ERRORS
                ):
                    if self.mcu_cmd_execution_in_progress:
                        self.mcu_cmd_execution_in_progress = False
                        self.log.debug("mcu command " + str(self._cmd_id) + " complete")
                elif (
                    self.mcu_cmd_execution_in_progress
                    and self._cmd_id_mcu != self._cmd_id
                    and time.time() - self.last_command_send_timestamp > self.LAST_COMMAND_ACK_TIMEOUT
                    and self.last_command is not None
                ):
                    if self.retry > self.MAX_RETRY_COUNT:
                        self.abort_current_command(
                            reason=f"Command timed out without an ack after {self.LAST_COMMAND_ACK_TIMEOUT} [s], and {self.retry} retries"
                        )
                    else:
                        self.log.debug(
                            f"command timed out without an ack after {self.LAST_COMMAND_ACK_TIMEOUT} [s], resending command"
                        )
                        self.resend_last_command()
                elif (
                    self.mcu_cmd_execution_in_progress
                    and self._cmd_execution_status == CMD_EXECUTION_STATUS.CMD_CHECKSUM_ERROR
                ):
                    if self.retry > self.MAX_RETRY_COUNT:
                        self.abort_current_command(reason=f"Checksum error and 10 retries for {self._cmd_id}")
                    else:
                        self.log.error("cmd checksum error, resending command")
                        self.resend_last_command()

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

                with self._received_packet_cv:
                    self._received_packet_cv.notify_all()

                if self.new_packet_callback_external is not None:
                    self.new_packet_callback_external(self)
            except Exception as e:
                self.log.error("Read loop failed, continuing to loop to see if anything can recover.", exc_info=e)

    def get_pos(self):
        return self.x_pos, self.y_pos, self.z_pos, self.theta_pos

    def get_button_and_switch_state(self):
        return self.button_and_switch_state

    def is_busy(self):
        return self.mcu_cmd_execution_in_progress

    def set_callback(self, function):
        self.new_packet_callback_external = function

    def wait_till_operation_is_completed(self, timeout_limit_s=5):
        """
        Wait for the current command to complete.  If the wait times out, the current command isn't touched.  To
        abort it, you should call the abort_current_command(...) method.
        """
        with self._received_packet_cv:

            def still_busy():
                return self.is_busy() and self.last_command_aborted_error is None

            self._received_packet_cv.wait_for(lambda: not still_busy(), timeout=timeout_limit_s)

            if still_busy():
                raise TimeoutError(f"Current mcu operation timed out after {timeout_limit_s} [s].")

            if self.last_command_aborted_error is not None:
                raise self.last_command_aborted_error

    @staticmethod
    def _int_to_payload(signed_int, number_of_bytes) -> int:
        actually_signed_int = int(round(signed_int))
        if actually_signed_int >= 0:
            payload = actually_signed_int
        else:
            payload = 2 ** (8 * number_of_bytes) + actually_signed_int  # find two's complement
        return int(payload)

    @staticmethod
    def _payload_to_int(payload, number_of_bytes) -> int:
        signed = 0
        for i in range(number_of_bytes):
            signed = signed + int(payload[i]) * (256 ** (number_of_bytes - 1 - i))
        if signed >= 256**number_of_bytes / 2:
            signed = signed - 256**number_of_bytes
        return int(signed)

    def set_dac80508_scaling_factor_for_illumination(self, illumination_intensity_factor):
        if illumination_intensity_factor > 1:
            illumination_intensity_factor = 1

        if illumination_intensity_factor < 0:
            illumination_intensity_factor = 0.01

        factor = round(illumination_intensity_factor, 2) * 100
        cmd = bytearray(self.tx_buffer_length)
        cmd[1] = CMD_SET.SET_ILLUMINATION_INTENSITY_FACTOR
        cmd[2] = int(factor)
        self.send_command(cmd)
