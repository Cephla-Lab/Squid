from typing import Optional
import serial
import serial.tools.list_ports
import threading
import time
import re

from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig


class ASIStage(AbstractStage):
    POS_POLLING_PERIOD = 0.25

    def __init__(self, sn: str, baudrate: int = 115200, stage_config: StageConfig = None):
        # We are not using StageConfig for ASI stage now. Waiting for further update/clarification of this part
        super().__init__(stage_config)

        port = [p.device for p in serial.tools.list_ports.comports() if sn == p.serial_number]
        self.serial = serial.Serial(port[0], baudrate=baudrate, timeout=0.1)
        self.current_baudrate = baudrate

        # Position information (in ASI units)
        self.x_pos = 0
        self.y_pos = 0
        self.z_pos = 0
        self.theta_pos = 0  # Always 0 for ASI MS2000

        # ASI MS2000-specific properties
        self.stage_microsteps_per_mm = 10000  # ASI uses 1/10 micron units
        self.user_unit = self.stage_microsteps_per_mm  # Direct conversion
        self.stage_model = None
        self.stage_limits = None
        self.resolution = 0.1  # 0.1 micron
        self.x_direction = 1  # 1 or -1
        self.y_direction = 1  # 1 or -1
        self.speed = 5  # mm/s
        self.acceleration = 75  # ms

        self.serial_lock = threading.Lock()
        self.is_busy = False

        self._pos_polling_thread: Optional[threading.Timer] = None

        self._initialize()

    def _pos_polling_thread_fn(self):
        last_poll = time.time()
        self._get_pos_poll_stage()
        # We launch this as a Daemon, and have a mechanism for restarting it if needed.  So, just do a while True
        # and do not worry about exceptions.
        while True:
            time_since_last = time.time() - last_poll
            time_left = ASIStage.POS_POLLING_PERIOD - time_since_last
            if time_left > 0:
                time.sleep(time_left)

            self._get_pos_poll_stage()
            last_poll = time.time()

    def _ensure_pos_polling_thread(self):
        if self._pos_polling_thread and self._pos_polling_thread.is_alive():
            return
        self._log.info("Starting position polling thread.")
        self._pos_polling_thread = threading.Thread(
            target=self._pos_polling_thread_fn, daemon=True, name="asi-pos-polling"
        )
        self._pos_polling_thread.start()

    def _initialize(self):
        # Initialize MS2000 - use stage's existing settings
        # Only set essential parameters for operation
        self._send_command("B X=0.04 Y=0.04 Z=0.04")  # Set backlash compensation
        self._send_command("PC X=0.001 Y=0.001 Z=0.001")  # Set finish error (tolerance)

        # Get initial position
        self._get_pos_poll_stage()
        self._ensure_pos_polling_thread()

    def _send_command(self, command: str) -> str:
        with self.serial_lock:
            self.serial.write(f"{command}\r".encode())
            time.sleep(0.05)  # Small delay for command processing
            response = self.serial.readline().decode("ascii", errors="ignore").strip()

            # Check for MS2000 error responses
            if response.startswith(":N"):
                error_code = response[2:] if len(response) > 2 else "?"
                raise Exception(f"MS2000 error: {response}")

            return response

    def _mm_to_steps(self, mm: float):
        """Convert mm to ASI units (1/10 microns)"""
        return int(mm * self.stage_microsteps_per_mm)

    def _steps_to_mm(self, steps: int):
        """Convert ASI units to mm"""
        return steps / self.stage_microsteps_per_mm

    def x_mm_to_usteps(self, mm: float):
        return self._mm_to_steps(mm)

    def y_mm_to_usteps(self, mm: float):
        return self._mm_to_steps(mm)

    def z_mm_to_usteps(self, mm: float):
        return self._mm_to_steps(mm)

    def move_x(self, rel_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(rel_mm)
        steps = steps * self.x_direction
        self._send_command(f"R X={steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_y(self, rel_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(rel_mm)
        steps = steps * self.y_direction
        self._send_command(f"R Y={steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_z(self, rel_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(rel_mm)
        self._send_command(f"R Z={steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(abs_mm)
        steps = steps * self.x_direction
        self._send_command(f"M X={steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(abs_mm)
        steps = steps * self.y_direction
        self._send_command(f"M Y={steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        steps = self._mm_to_steps(abs_mm)
        self._send_command(f"M Z={steps}")
        if blocking:
            self.wait_for_stop()
        else:
            threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def _get_pos_poll_stage(self):
        """Get position using WHERE command"""
        try:
            # Query each axis position
            x_response = self._send_command("W X")
            y_response = self._send_command("W Y")
            z_response = self._send_command("W Z")

            # Parse responses - extract numbers from responses
            # Response format can be ":A 12345" or just "12345"
            import re

            x_match = re.search(r"-?\d+", x_response)
            if x_match:
                self.x_pos = int(x_match.group())

            y_match = re.search(r"-?\d+", y_response)
            if y_match:
                self.y_pos = int(y_match.group())

            z_match = re.search(r"-?\d+", z_response)
            if z_match:
                self.z_pos = int(z_match.group())

        except Exception as e:
            self._log.warning(f"Position polling failed: {e}")

    def get_pos(self) -> Pos:
        self._ensure_pos_polling_thread()
        x_mm = self._steps_to_mm(self.x_pos * self.x_direction)
        y_mm = self._steps_to_mm(self.y_pos * self.y_direction)
        z_mm = self._steps_to_mm(self.z_pos)
        return Pos(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, theta_rad=0)

    def get_state(self) -> StageStage:
        return StageStage(busy=self.is_busy)

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        """Move specified axes to origin (0,0,0) - matches HOME button behavior"""
        # Build command with axes to move to 0
        cmd_parts = []
        if x:
            cmd_parts.append("X=0")
        if y:
            cmd_parts.append("Y=0")
        if z:
            cmd_parts.append("Z=0")

        if cmd_parts:
            # Use MOVE command to go to origin
            cmd = "M " + " ".join(cmd_parts)
            self._send_command(cmd)

            if blocking:
                self.wait_for_stop()
            else:
                threading.Thread(target=self.wait_for_stop, daemon=True).start()

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        """Zero specified axes at current position"""
        if x:
            self._send_command("H X=0")
            self.x_pos = 0
        if y:
            self._send_command("H Y=0")
            self.y_pos = 0
        if z:
            self._send_command("H Z=0")
            self.z_pos = 0

    def wait_for_stop(self):
        """Wait for stage to stop moving"""
        self.is_busy = True
        while True:
            try:
                status = self._send_command("/")
                # Check if not busy (N) vs busy (B)
                if "N" in status:
                    self._get_pos_poll_stage()
                    self.is_busy = False
                    break
            except:
                # If command fails, assume not busy
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
        """Set software limits for axes"""
        if x_pos_mm is not None:
            self._send_command(f"SU X={x_pos_mm}")
        if x_neg_mm is not None:
            self._send_command(f"SL X={x_neg_mm}")

        if y_pos_mm is not None:
            self._send_command(f"SU Y={y_pos_mm}")
        if y_neg_mm is not None:
            self._send_command(f"SL Y={y_neg_mm}")

        if z_pos_mm is not None:
            self._send_command(f"SU Z={z_pos_mm}")
        if z_neg_mm is not None:
            self._send_command(f"SL Z={z_neg_mm}")

    def get_config(self) -> StageConfig:
        return super().get_config()

    def get_stage_info(self):
        """Get stage information from MS2000"""
        try:
            # Get build info
            build_info = self._send_command("BU")
            self.stage_model = build_info

            # Get WHO info
            who_info = self._send_command("N")
            self._log.info(f"Stage info: {who_info}")

        except Exception as e:
            self._log.warning(f"Could not get stage info: {e}")
