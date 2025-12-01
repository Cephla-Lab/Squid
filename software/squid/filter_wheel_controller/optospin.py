import time
import struct
import serial
import serial.tools.list_ports
from typing import List, Dict, Optional

import squid.logging
from squid.abc import AbstractFilterWheelController, FilterWheelInfo, FilterControllerError
from squid.config import OptospinFilterWheelConfig


class Optospin(AbstractFilterWheelController):
    # Default connection parameters (not configurable via _def.py)
    DEFAULT_BAUDRATE = 115200
    DEFAULT_TIMEOUT = 1
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY = 0.5
    NUM_FILTER_SLOTS = 6

    def __init__(self, config: OptospinFilterWheelConfig):
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self._config = config

        optospin_port = [
            p.device for p in serial.tools.list_ports.comports() if config.serial_number == p.serial_number
        ]
        if not optospin_port:
            raise ValueError(f"No Optospin device found with serial number: {config.serial_number}")
        self.ser = serial.Serial(optospin_port[0], baudrate=self.DEFAULT_BAUDRATE, timeout=self.DEFAULT_TIMEOUT)
        self._available_filter_wheels = []
        self._delay_offset_ms = 0
        self._current_pos = {}

    def _send_command(self, command, data=None):
        if data is None:
            data = []
        full_command = struct.pack(">H", command) + bytes(data)

        for attempt in range(self.DEFAULT_MAX_RETRIES):
            try:
                self.ser.write(full_command)
                response = self.ser.read(2)

                if len(response) != 2:
                    raise serial.SerialTimeoutException("Timeout: No response from device")

                status, length = struct.unpack(">BB", response)

                if status != 0xFF:
                    raise Exception(f"Command failed with status: {status}")

                if length > 0:
                    additional_data = self.ser.read(length)
                    if len(additional_data) != length:
                        raise serial.SerialTimeoutException("Timeout: Incomplete additional data")
                    return additional_data
                return None

            except (serial.SerialTimeoutException, Exception) as e:
                self.log.error(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < self.DEFAULT_MAX_RETRIES - 1:
                    self.log.error(f"Retrying in {self.DEFAULT_RETRY_DELAY} seconds...")
                    time.sleep(self.DEFAULT_RETRY_DELAY)
                else:
                    raise FilterControllerError(f"Command failed after {self.DEFAULT_MAX_RETRIES} attempts: {str(e)}")

    def initialize(self, filter_wheel_indices: List[int]):
        self._available_filter_wheels = filter_wheel_indices
        self.set_speed(self._config.speed_hz)

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._available_filter_wheels

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        if index not in self._available_filter_wheels:
            raise ValueError(f"Filter wheel index {index} not found")
        return FilterWheelInfo(
            index=index,
            number_of_slots=self.NUM_FILTER_SLOTS,
            slot_names=[str(i) for i in range(1, self.NUM_FILTER_SLOTS + 1)],
        )

    def home(self, index: Optional[int] = None):
        pass

    def get_version(self):
        result = self._send_command(0x0040)
        return struct.unpack(">BB", result)

    def set_speed(self, speed):
        speed_int = int(speed * 100)
        self._send_command(0x0048, struct.pack("<H", speed_int))

    def spin_rotors(self):
        self._send_command(0x0060)

    def stop_rotors(self):
        self._send_command(0x0064)

    def _usb_go(self, rotor1_pos, rotor2_pos, rotor3_pos, rotor4_pos):
        data = bytes([rotor1_pos | (rotor2_pos << 4), rotor3_pos | (rotor4_pos << 4)])
        self._send_command(0x0088, data)

    def set_filter_wheel_position(self, positions: Dict[int, int]):
        if self._config.ttl_trigger:
            return
        rotor_positions = [0] * 4
        for k, v in positions.items():
            if k not in self._available_filter_wheels:
                raise ValueError(f"Filter wheel index {k} not found")
            if v == self._current_pos[k]:
                continue
            rotor_positions[k - 1] = v
        if rotor_positions == [0, 0, 0, 0]:  # no change
            return

        self._usb_go(*rotor_positions)
        for fw in self._available_filter_wheels:
            self._current_pos[fw] = rotor_positions[fw - 1]

        # delay
        time.sleep(max(0, (self._config.delay_ms + self._delay_offset_ms) / 1000))

    def get_filter_wheel_position(self):
        result = self.get_rotor_positions()
        result_dict = {}
        for i in self._available_filter_wheels:
            result_dict[i] = result[i - 1]
        self._current_pos = result_dict
        return result_dict

    def get_rotor_positions(self):
        result = self._send_command(0x0098)
        rotor1 = result[0] & 0x07
        rotor2 = (result[0] >> 4) & 0x07
        rotor3 = result[1] & 0x07
        rotor4 = (result[1] >> 4) & 0x07
        return rotor1, rotor2, rotor3, rotor4

    def measure_temperatures(self):
        self._send_command(0x00A8)

    def get_temperature(self):
        self.measure_temperatures()
        result = self._send_command(0x00AC)
        return struct.unpack(">BBBB", result)

    def set_delay_offset_ms(self, delay_offset_ms: float):
        self._delay_offset_ms = delay_offset_ms

    def get_delay_offset_ms(self) -> Optional[float]:
        return self._delay_offset_ms

    def set_delay_ms(self, delay_ms: float):
        raise NotImplementedError("Setting delay ms is not supported for Optospin filter wheel controller")

    def get_delay_ms(self) -> Optional[float]:
        return self._config.delay_ms

    def close(self):
        self.ser.close()
