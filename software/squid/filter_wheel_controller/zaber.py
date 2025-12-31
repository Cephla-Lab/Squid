import time
import threading
from typing import List, Tuple, Optional, Dict
import serial
from serial.tools import list_ports
import squid.logging
from squid.abc import AbstractFilterWheelController, FilterWheelInfo, FilterControllerError
from squid.config import ZaberFilterWheelConfig


class ZaberFilterController(AbstractFilterWheelController):
    """Controller for filter device."""

    # TODO: Looks like we are only controlling one filter wheel in Zaber. We might need to modify this to be able to control multiple filter wheels.

    MICROSTEPS_PER_HOLE = 4800
    OFFSET_POSITION = -8500
    VALID_POSITIONS = tuple(range(1, 8))
    MAX_RETRIES = 3
    COMMAND_TIMEOUT = 1  # seconds

    # Default serial connection parameters (not configurable via _def.py)
    DEFAULT_BAUDRATE = 115200
    DEFAULT_BYTESIZE = 8
    DEFAULT_PARITY = serial.PARITY_NONE
    DEFAULT_STOPBITS = serial.STOPBITS_ONE

    def __init__(self, config: ZaberFilterWheelConfig):
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.current_position = 0
        self.current_index = 1
        self.serial = self._initialize_serial(config.serial_number)
        self._config = config
        self._available_filter_wheels = []
        self._delay_offset_ms = 0

    def _initialize_serial(self, serial_number: str) -> serial.Serial:
        ports = [p.device for p in list_ports.comports() if serial_number == p.serial_number]
        if not ports:
            raise ValueError(f"No device found with serial number: {serial_number}")
        return serial.Serial(
            ports[0],
            baudrate=self.DEFAULT_BAUDRATE,
            bytesize=self.DEFAULT_BYTESIZE,
            parity=self.DEFAULT_PARITY,
            stopbits=self.DEFAULT_STOPBITS,
            timeout=self.COMMAND_TIMEOUT,
        )

    def initialize(self, filter_wheel_indices: List[int]):
        self._available_filter_wheels = filter_wheel_indices
        self._configure_device()

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._available_filter_wheels

    def _configure_device(self):
        time.sleep(0.2)
        self.firmware_version = self._get_device_info("/get version")
        self._send_command_with_reply("/set maxspeed 250000")
        self._send_command_with_reply("/set accel 900")
        self.maxspeed = self._get_device_info("/get maxspeed")
        self.accel = self._get_device_info("/get accel")

    def __del__(self):
        if hasattr(self, "serial") and self.serial.is_open:
            self._send_command("/stop")
            time.sleep(0.5)
            self.serial.close()

    def _send_command(self, cmd: str) -> Tuple[bool, str]:
        """
        Send a command to the device and handle the response.

        Args:
            cmd (str): The command to send.

        Returns:
            Tuple[bool, str]: A tuple containing a success flag and the response message.

        Raises:
            FilterControllerError: If the command fails after maximum retries.
        """
        if not self.serial.is_open:
            raise RuntimeError("Serial port is not open")

        for attempt in range(self.MAX_RETRIES):
            try:
                self.serial.write(f"{cmd}\n".encode("utf-8"))
                response = self.serial.readline().decode("utf-8").strip()
                success, message = self._parse_response(response)

                if success:
                    return True, message
                elif message.startswith("BUSY"):
                    time.sleep(0.1)  # Wait a bit if the device is busy
                    continue
                else:
                    # Log the error and retry
                    self.log.error(f"Command failed (attempt {attempt + 1}): {message}")
            except serial.SerialTimeoutException:
                self.log.error(f"Command timed out (attempt {attempt + 1})")

            time.sleep(0.5)  # Wait before retrying

        raise FilterControllerError(f"Command '{cmd}' failed after {self.MAX_RETRIES} attempts")

    def _parse_response(self, response: str) -> Tuple[bool, str]:
        """
        Parse the response from the device.

        Args:
            response (str): The response string from the device.

        Returns:
            Tuple[bool, str]: A tuple containing a success flag and the parsed message.
        """
        if not response:
            return False, "No response received"

        parts = response.split()
        if len(parts) < 4:
            return False, f"Invalid response format: {response}"

        if parts[0].startswith("@"):
            if parts[2] == "OK":
                return True, " ".join(parts[3:])
            else:
                return False, " ".join(parts[2:])
        elif parts[0].startswith("!"):
            return False, f"Alert: {' '.join(parts[1:])}"
        elif parts[0].startswith("#"):
            return True, f"Info: {' '.join(parts[1:])}"
        else:
            return False, f"Unknown response format: {response}"

    def _send_command_with_reply(self, cmd: str) -> bool:
        success, message = self._send_command(cmd)
        return success and (message == "IDLE" or message.startswith("BUSY"))

    def _get_device_info(self, cmd: str) -> Optional[str]:
        success, message = self._send_command(cmd)
        return message if success else None

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        if index not in self._available_filter_wheels:
            raise ValueError(f"Filter wheel index {index} not found")
        return FilterWheelInfo(
            index=index, number_of_slots=len(self.VALID_POSITIONS), slot_names=[str(i) for i in self.VALID_POSITIONS]
        )

    def get_current_position(self) -> Tuple[bool, int]:
        success, message = self._send_command("/get pos")
        if success:
            try:
                return True, int(message.split()[-1])
            except (ValueError, IndexError):
                return False, 0
        return False, 0

    def calculate_filter_index(self) -> int:
        return (self.current_position - self.OFFSET_POSITION) // self.MICROSTEPS_PER_HOLE

    def move_to_offset_position(self):
        self._move_to_absolute_position(self.OFFSET_POSITION)

    def _move_to_absolute_position(self, target_position: int, timeout: int = 5):
        success, _ = self._send_command(f"/move abs {target_position}")
        if not success:
            raise FilterControllerError("Failed to initiate filter movement")
        self._wait_for_position(target_position, target_index=None, timeout=timeout)

    def set_filter_wheel_position(self, positions: Dict[int, int], blocking: Optional[bool] = None, timeout: int = 5):
        """
        Set the emission filter to the specified position.

        Args:
            positions (Dict[int, int]): A dictionary of filter wheel index to target position.
            blocking (bool): If True, wait for the movement to complete. If False, return immediately.
            timeout (int): Maximum time to wait for the movement to complete (in seconds).

        Raises:
            ValueError: If the position is invalid.
            FilterControllerError: If the command fails to initiate movement.
            TimeoutError: If the movement doesn't complete within the specified timeout (only in blocking mode).
        """
        # TODO: support multiple filter wheels. Now only index 1 is allowed.
        if 1 not in positions:
            raise ValueError("Zaber filter wheel only supports index 1")
        pos = positions[1]
        if pos == self.current_index:
            return

        if pos is None or pos not in self.VALID_POSITIONS:
            raise ValueError(f"Invalid emission filter wheel index position: {pos}")

        target_position = self.OFFSET_POSITION + (pos - 1) * self.MICROSTEPS_PER_HOLE
        success, _ = self._send_command(f"/move abs {target_position}")

        if not success:
            raise FilterControllerError("Failed to initiate filter movement")

        if blocking or self._config.blocking_call:
            self._wait_for_position(target_position, pos, timeout)
        else:
            # delay
            time.sleep(max(0, (self._config.delay_ms + self._delay_offset_ms) / 1000))
            # Update the current position without waiting
            self.current_position = target_position
            self.current_index = pos

    def _wait_for_position(self, target_position: int, target_index: Optional[int], timeout: int):
        """
        Wait for the filter to reach the target position.

        Args:
            target_position (int): The expected final position.
            timeout (int): Maximum time to wait (in seconds).

        Raises:
            TimeoutError: If the movement doesn't complete within the specified timeout.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(0.003)
            success, position = self.get_current_position()
            if success and position == target_position:
                self.current_position = target_position
                self.current_index = target_index
                return
        raise TimeoutError(f"Filter move to position {target_position} timed out")

    def get_filter_wheel_position(self) -> Dict[int, int]:
        return {1: self.calculate_filter_index() + 1}

    def home(self, index: Optional[int] = None):
        """
        Start the homing sequence for the filter device.

        This function initiates the homing process but does not wait for it to complete.
        Use _wait_for_homing_complete() to wait for the homing process to finish.

        Raises:
            FilterControllerError: If the homing command fails to initiate.
        """
        # We will always run a complete homing sequence now, instead of initiating homing and then calling
        #  _wait_for_homing_complete(). This is for making it easier to comply with the abstract interface,
        # and we don't actually benefit much from saving the homing time.
        success, _ = self._send_command("/home")
        if not success:
            raise FilterControllerError("Failed to initiate homing sequence")
        if not self._wait_for_homing_complete():
            raise TimeoutError("Filter device homing failed")

    def _wait_for_homing_complete(self, timeout: int = 50) -> bool:
        """
        Wait for the homing sequence to complete.

        Args:
            timeout (int): Maximum time to wait for homing to complete, in seconds.

        Returns:
            bool: True if homing completed successfully, False if it timed out.

        Raises:
            FilterControllerError: If there's an error while checking the homing status.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(0.5)
            success, position = self.get_current_position()
            if not success:
                raise FilterControllerError("Failed to get current position during homing")
            if position == 0:
                self.current_position = 0
                self.move_to_offset_position()
                return True
        return False

    def set_delay_offset_ms(self, delay_offset_ms: float):
        self._delay_offset_ms = delay_offset_ms

    def get_delay_offset_ms(self) -> Optional[float]:
        return self._delay_offset_ms

    def set_delay_ms(self, delay_ms: float):
        raise NotImplementedError("Setting delay ms is not supported for Zaber filter wheel controller")

    def get_delay_ms(self) -> Optional[float]:
        return self._config.delay_ms

    def close(self):
        self.serial.close()
