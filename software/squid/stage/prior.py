from typing import Optional
import serial
import threading
import time
import re

import squid.ftdi
from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig
from squid.exceptions import SquidError, SquidTimeout


class PriorStage(AbstractStage):
    POS_POLLING_PERIOD = 0.25
    # How often wait_for_stop() asks the controller whether the move is done. With replies read up to their CR
    # terminator a "$,S" round trip is a few ms, so polling this fast costs nothing and cuts dead time after a move.
    MOVE_POLLING_PERIOD = 0.01
    # wait_for_stop() raises when "$,S" has gone this long without a status reply. A busy status resets the clock,
    # so long moves are fine; this catches a controller that stopped answering while its port stays open (each
    # unanswered poll costs the 0.1 s read timeout).
    STATUS_REPLY_TIMEOUT_S = 1.0

    def __init__(
        self,
        sn: str,
        baudrate: int = 115200,
        stage_config: StageConfig = None,
        *,
        use_encoders: Optional[bool] = None,
        max_speed: int = 100,
        acceleration: int = 100,
    ):
        """max_speed (SMS) and acceleration (SAS) are in the controller's 1-1000 scale. On the H117(1MM) stage
        100 is 6 mm/s and 28.6 mm/s^2, 1000 is 60 mm/s and 285.5 mm/s^2 (read back with "SMS,u" / "SAS,u")."""
        # We are not using StageConfig for Prior stage now. Waiting for further update/clarification of this part
        super().__init__(stage_config)

        port = [p.device for p in serial.tools.list_ports.comports() if sn == p.serial_number]
        # Every move is several short request/reply exchanges, so the FTDI latency timer sets their cost
        # (16 ms default vs ~2 ms at 1 ms).
        squid.ftdi.warn_if_latency_timer_high(port[0], "Prior stage", self._log)
        self.serial = serial.Serial(port[0], baudrate=baudrate, timeout=0.1)
        self.current_baudrate = baudrate

        # Position information
        self.x_pos = 0
        self.y_pos = 0
        self.z_pos = 0  # Always 0 for Prior stage
        self.theta_pos = 0  # Always 0 for Prior stage

        # Button and switch state
        self.button_and_switch_state = 0
        self.joystick_button_pressed = 0
        self.signal_joystick_button_pressed_event = False
        self.switch_state = 0
        self.joystick_enabled = False

        # Prior-specific properties
        self.stage_microsteps_per_mm = 100000  # Stage property
        self.user_unit = None
        self.stage_model = None
        self.stage_limits = None
        self.resolution = 0.1
        self.x_direction = 1  # 1 or -1
        self.y_direction = 1  # 1 or -1
        self.speed = max_speed
        self.acceleration = acceleration

        self.serial_lock = threading.Lock()
        self.is_busy = False
        # True/False switches the stage's encoders on/off at start-up; None leaves them as the controller has them
        self.use_encoders = PriorStage.parse_use_encoders(use_encoders)
        self.encoder_axes: Optional[int] = None

        self.set_baudrate(baudrate)

        self._pos_polling_thread: Optional[threading.Timer] = None

        self._initialize()

    def _pos_polling_thread_fn(self):
        last_poll = time.time()
        self._get_pos_poll_stage()
        # We launch this as a Daemon, and have a mechanism for restarting it if needed.  So, just do a while True
        # and do not worry about exceptions.
        while True:
            time_since_last = time.time() - last_poll
            time_left = PriorStage.POS_POLLING_PERIOD - time_since_last
            if time_left > 0:
                time.sleep(time_left)

            self._get_pos_poll_stage()
            last_poll = time.time()

    def _ensure_pos_polling_thread(self):
        if self._pos_polling_thread and self._pos_polling_thread.is_alive():
            return
        self._log.info("Starting position polling thread.")
        self._pos_polling_thread = threading.Thread(
            target=self._pos_polling_thread_fn, daemon=True, name="prior-pos-polling"
        )
        self._pos_polling_thread.start()

    def set_baudrate(self, baud: int):
        allowed_baudrates = {9600: "96", 19200: "19", 38400: "38", 115200: "115"}
        if baud not in allowed_baudrates:
            print("Baudrate not allowed. Setting baudrate to 9600")
            baud_command = "BAUD 96"
        else:
            baud_command = "BAUD " + allowed_baudrates[baud]
        print(baud_command)

        for bd in allowed_baudrates:
            self.serial.baudrate = bd
            self.serial.write(b"\r")
            time.sleep(0.1)
            self.serial.flushInput()

            self._send_command(baud_command)

            self.serial.baudrate = baud

            try:
                test_response = self._send_command("$")  # Send a simple query command
                if not test_response:
                    raise Exception("No response received after changing baud rate")
                else:
                    self.current_baudrate = baud
                    print(f"Baud rate successfully changed to {baud}")
                    return
            except Exception as e:
                # If verification fails, try to revert to the original baud rate
                self.serial.baudrate = self.current_baudrate
                print(f"Serial baudrate: {bd}")
                print(f"Failed to verify communication at new baud rate: {e}")

        raise Exception("Failed to set baudrate.")

    def _initialize(self):
        self._send_command("COMP 0")  # Set to standard mode
        self._send_command("BLSH 1")  # Enable backlash correction
        self._send_command("RES,S," + str(self.resolution))  # Set resolution
        self._send_command("XD -1")  # Set direction of X axis move
        self._send_command("YD -1")  # Set direction of Y axis move
        self._send_command("H 0")  # Joystick enabled
        self.joystick_enabled = True
        self.user_unit = self.stage_microsteps_per_mm * self.resolution
        self.get_stage_info()
        self._apply_encoder_setting()  # once the controller has shown it is a ProScan with a stage
        self.set_acceleration(self.acceleration)
        self.set_max_speed(self.speed)
        self._get_pos_poll_stage()
        self._ensure_pos_polling_thread()

    def _read_reply_line(self) -> str:
        # The ProScan terminates every reply with a bare CR. readline() waits for LF, which never comes, so it
        # always ran out the full serial timeout (~110 ms per command, several commands per axis move).
        return self.serial.read_until(b"\r").decode(errors="replace").strip()

    def _send_command(self, command: str) -> str:
        with self.serial_lock:
            # Drop anything stale (e.g. a reply that arrived after an earlier read timed out) so it can't be taken
            # as this command's answer.
            self.serial.reset_input_buffer()
            self.serial.write(f"{command}\r".encode())
            response = self._read_reply_line()
            if response.startswith("E"):
                raise Exception(f"Error from controller: {response}")
            return response

    def _send_multiline_command(self, command: str) -> list:
        """For commands like STAGE whose reply is several CR-terminated lines closed by an END line."""
        with self.serial_lock:
            self.serial.reset_input_buffer()
            self.serial.write(f"{command}\r".encode())
            lines = []
            while True:
                line = self._read_reply_line()
                if not line:
                    break
                if line.startswith("E") and not lines:
                    raise Exception(f"Error from controller: {line}")
                lines.append(line)
                if line == "END":
                    break
            return lines

    def get_stage_info(self):
        lines = self._send_multiline_command("STAGE")
        match = re.search(r"STAGE\s*=\s*(\S+)", "\n".join(lines))
        if match is None:
            raise SquidError(f"Prior stage did not report its model in reply to STAGE: {lines!r}")
        self.stage_model = match.group(1)
        print("Stage model: ", self.stage_model)

    def set_max_speed(self, speed=1000):
        """Set the maximum speed of the stage. Range is 1 to 1000."""
        if 1 <= speed <= 1000:
            response = self._send_command(f"SMS {speed}")
            print(f"Maximum speed set to {speed}. Response: {response}")
        else:
            raise ValueError("Speed must be between 1 and 1000")

    def get_max_speed(self):
        """Get the current maximum speed setting."""
        response = self._send_command("SMS")
        print(f"Current maximum speed: {response}")
        return int(response)

    def set_acceleration(self, acceleration=1000):
        """Set the acceleration of the stage. Range is 1 to 1000."""
        if 1 <= acceleration <= 1000:
            response = self._send_command(f"SAS {acceleration}")
            self.acceleration = acceleration
            print(f"Acceleration set to {acceleration}. Response: {response}")
        else:
            raise ValueError("Acceleration must be between 1 and 1000")

    def enable_joystick(self):
        self._send_command("J")
        self.joystick_enabled = True

    def disable_joystick(self):
        self._send_command("H")
        self.joystick_enabled = False

    def get_encoder_axes(self) -> Optional[int]:
        """The axes moving under encoder control, as the controller's bit mask (X=1, Y=2, Z=4; 0 = none). None if
        the controller doesn't say."""
        try:
            return int(self._send_command("ENCODER"))
        except Exception as e:
            self._log.debug(f"Prior stage gave no usable reply to 'ENCODER': {e}")
            return None

    @staticmethod
    def parse_use_encoders(value) -> Optional[bool]:
        """The prior_stage_use_encoders setting as True, False or None. The .ini loader passes text such as 'off' or
        'none' through as a non-empty (truthy) string, which would switch the encoders the wrong way; anything that is
        not clearly one of the three is refused."""
        if value is None or isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("", "none", "null"):
            return None
        if text in ("true", "on", "yes", "1"):
            return True
        if text in ("false", "off", "no", "0"):
            return False
        raise ValueError(f"prior_stage_use_encoders must be True, False or None (leave as is), not {value!r}")

    def _apply_encoder_setting(self):
        """Switch the X/Y encoders on or off as configured; with use_encoders None nothing is sent at all.

        A ProScan enables the encoders it detects at power-up, which makes every move closed loop. On an H117 that
        made 0.2 mm moves ~3x slower (0.55 s vs 0.17 s), and a stall still ended a move short (9 mm) rather than being
        recovered. SENCODER, unlike ENCODER, makes the step and encoder counts equal when switching, so the position
        doesn't jump. With the encoders off they still count: "P,e" reads where the stage really is. The controller
        keeps the switched state until it is power-cycled. The result is read back and checked for X and Y (ENCODER
        reports the axes under encoder control: 3 after power-up with X/Y encoders, 0 after SENCODER,0)."""
        if self.use_encoders is None:
            return
        wanted = "on" if self.use_encoders else "off"
        try:
            reply = self._send_command(f"SENCODER,{1 if self.use_encoders else 0}")
        except Exception as e:
            reply = str(e)
        if reply != "0":
            self._log.warning(f"Prior stage: switching the encoders {wanted} was answered {reply!r}")
        self.encoder_axes = self.get_encoder_axes()
        if self.encoder_axes is None:
            self._log.warning(f"Prior stage: could not read back the encoders after switching them {wanted}")
            return
        xy = self.encoder_axes & 0b11  # X=1, Y=2; the stage has no other axes (Z=4 would be a focus drive)
        axes = "+".join(name for bit, name in ((1, "X"), (2, "Y")) if xy & bit)
        if bool(xy) != self.use_encoders:
            self._log.warning(f"Prior stage encoders: asked for {wanted}, but X/Y read back {axes or 'off'}")
        else:
            self._log.info(f"Prior stage encoders: {axes + ' (closed loop)' if xy else 'off (open loop)'}")

    def get_acceleration(self):
        """Get the current acceleration setting."""
        response = self._send_command("SAS")
        print(f"Current acceleration: {response}")
        return int(response)

    def _mm_to_steps(self, mm: float):
        return int(mm * self.user_unit)

    def _steps_to_mm(self, steps: int):
        return steps / self.user_unit

    def x_mm_to_usteps(self, mm: float):
        return self._mm_to_steps(mm)

    def y_mm_to_usteps(self, mm: float):
        return self._mm_to_steps(mm)

    def z_mm_to_usteps(self, mm: float):
        return 0

    def move_x(self, rel_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(rel_mm)
        steps = steps * self.x_direction
        self._send_command(f"GR {steps},0")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_y(self, rel_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(rel_mm)
        steps = steps * self.y_direction
        self._send_command(f"GR 0,{steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_z(self, rel_mm: float, blocking: bool = True):
        pass

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(abs_mm)
        steps = steps * self.x_direction
        self._send_command(f"GX {steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(abs_mm)
        steps = steps * self.y_direction
        self._send_command(f"GY {steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        pass

    def _get_pos_poll_stage(self):
        response = self._send_command("P")
        x, y, z = map(int, response.split(","))
        self.x_pos = x
        self.y_pos = y

    def get_pos(self) -> Pos:
        self._ensure_pos_polling_thread()
        x_mm = self._steps_to_mm(self.x_pos)
        y_mm = self._steps_to_mm(self.y_pos)
        return Pos(x_mm=x_mm, y_mm=y_mm, z_mm=0, theta_rad=0)

    def get_state(self) -> StageStage:
        return StageStage(busy=self.is_busy)

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        self._send_command("SIS")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

        # We are not using the following for Prior stage yet
        """
        if z:
            self._microcontroller.home_z()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(z_timeout)

        if theta:
            self._microcontroller.home_theta()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(theta_timeout)
        """

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        if x:
            self._send_command(f"PX 0")
            self.x_pos = 0
        if y:
            self._send_command(f"PY 0")
            self.y_pos = 0

    def wait_for_stop(self):
        self.is_busy = True
        last_status_time = time.monotonic()
        while True:
            status = self._send_command("$,S")
            if status.isdigit():
                if int(status) == 0:
                    self._get_pos_poll_stage()
                    # print("xy position: ", self.x_pos, self.y_pos)
                    self.is_busy = False
                    break
                last_status_time = time.monotonic()
            elif time.monotonic() - last_status_time > PriorStage.STATUS_REPLY_TIMEOUT_S:
                # A stray line or one missed reply is just asked again; this long without a status, it is gone.
                message = (
                    f"Prior stage gave no status reply to '$,S' for {PriorStage.STATUS_REPLY_TIMEOUT_S} s "
                    f"(last reply {status!r})"
                )
                self._log.error(message)
                raise SquidTimeout(message)
            time.sleep(PriorStage.MOVE_POLLING_PERIOD)

    def set_limits(
        self,
        x_pos_mm: Optional[float] = None,
        x_neg_mm: Optional[float] = None,
        y_pos_mm: Optional[float] = None,
        y_neg_mm: Optional[float] = None,
        z_pos_mm: Optional[float] = None,
        z_neg_mm: Optional[float] = None,
        theta_pos_rad: Optional[float] = None,
        theta_neg_rad: Optional[float] = None,
    ):
        pass

    def get_config(self) -> StageConfig:
        return super().get_config()
