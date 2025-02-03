from typing import Optional
import serial
import threading
import time
import re

from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig

class PriorStage(AbstractStage):
    def __init__(self, sn: str, baudrate: int = 115200, stage_config: StageConfig = None):
        # We are not using StageConfig for Prior stage now. Waiting for further update/clarification of this part
        super().__init__(stage_config)

        port = [p.device for p in serial.tools.list_ports.comports() if sn == p.serial_number]
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
        self.speed = 100
        self.acceleration = 100

        self.serial_lock = threading.Lock()
        self.is_busy = False

        self.set_baudrate(baudrate)

        self._initialize()

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
        self.set_acceleration(self.acceleration)
        self.set_max_speed(self.speed)
        self._get_pos_poll_stage()

    def _send_command(self, command: str) -> str:
        with self.serial_lock:
            self.serial.write(f"{command}\r".encode())
            response = self.serial.readline().decode().strip()
            if response.startswith("E"):
                raise Exception(f"Error from controller: {response}")
            return response

    def get_stage_info(self):
        stage_info = self._send_command("STAGE")
        self.stage_model = re.search(r"STAGE\s*=\s*(\S+)", stage_info).group(1)
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
        while True:
            status = int(self._send_command("$,S"))
            if status == 0:
                self._get_pos_poll_stage()
                # print("xy position: ", self.x_pos, self.y_pos)
                self.is_busy = False
                break
            time.sleep(0.05)

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
