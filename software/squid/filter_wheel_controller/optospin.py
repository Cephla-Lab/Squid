import time
import struct
import serial
import serial.tools.list_ports
from typing import List, Dict, Optional

from control._def import OPTOSPIN_EMISSION_FILTER_WHEEL_SPEED_HZ
import squid.logging
from squid.abc import AbstractFilterWheelController, FilterWheelInfo, FilterControllerError


class Optospin(AbstractFilterWheelController):
    def __init__(self, SN, baudrate=115200, timeout=1, max_retries=3, retry_delay=0.5):
        self.log = squid.logging.get_logger(self.__class__.__name__)

        optospin_port = [p.device for p in serial.tools.list_ports.comports() if SN == p.serial_number]
        self.ser = serial.Serial(optospin_port[0], baudrate=baudrate, timeout=timeout)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._available_filter_wheels = []

    def _send_command(self, command, data=None):
        if data is None:
            data = []
        full_command = struct.pack(">H", command) + bytes(data)

        for attempt in range(self.max_retries):
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
                if attempt < self.max_retries - 1:
                    self.log.error(f"Retrying in {self.retry_delay} seconds...")
                    time.sleep(self.retry_delay)
                else:
                    raise FilterControllerError(f"Command failed after {self.max_retries} attempts: {str(e)}")

    def initialize(self, filter_wheel_indices: List[int]):
        self._available_filter_wheels = filter_wheel_indices
        self.set_speed(OPTOSPIN_EMISSION_FILTER_WHEEL_SPEED_HZ)

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._available_filter_wheels

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        if index not in self._available_filter_wheels:
            raise ValueError(f"Filter wheel index {index} not found")
        return FilterWheelInfo(index=index, number_of_slots=8, slot_names=[str(i) for i in range(1, 9)])

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
        rotor_positions = [0] * 4
        for k, v in positions.items():
            if k not in self._available_filter_wheels:
                raise ValueError(f"Filter wheel index {k} not found")
            rotor_positions[k - 1] = v
        self._usb_go(*rotor_positions)

    def get_filter_wheel_position(self):
        result = self.get_rotor_positions()
        result_dict = {}
        for i in self._available_filter_wheels:
            result_dict[i] = result[i - 1]
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

    def close(self):
        self.ser.close()
