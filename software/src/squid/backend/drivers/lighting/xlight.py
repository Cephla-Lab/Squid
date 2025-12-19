import serial
import time

from squid.backend.drivers.peripherals.serial_base import SerialDevice
from _def import XLIGHT_ILLUMINATION_IRIS_DEFAULT, XLIGHT_EMISSION_IRIS_DEFAULT

import squid.core.logging


class XLight_Simulation:
    def __init__(self):
        self.has_spinning_disk_motor = True
        self.has_spinning_disk_slider = True
        self.has_dichroic_filters_wheel = True
        self.has_emission_filters_wheel = True
        self.has_excitation_filters_wheel = True
        self.has_illumination_iris_diaphragm = True
        self.has_emission_iris_diaphragm = True
        self.has_dichroic_filter_slider = True
        self.has_ttl_control = True

        self.emission_wheel_pos = 1
        self.dichroic_wheel_pos = 1
        self.disk_motor_state = False
        self.spinning_disk_pos = 0
        self.illumination_iris = 0
        self.emission_iris = 0

    def set_emission_filter(self, position, extraction=False, validate=False):
        self.emission_wheel_pos = position
        return position

    def get_emission_filter(self):
        return self.emission_wheel_pos

    def set_dichroic(self, position, extraction=False):
        self.dichroic_wheel_pos = position
        return position

    def get_dichroic(self):
        return self.dichroic_wheel_pos

    def set_disk_position(self, position):
        self.spinning_disk_pos = position
        return position

    def get_disk_position(self):
        return self.spinning_disk_pos

    def set_disk_motor_state(self, state):
        self.disk_motor_state = state
        return state

    def get_disk_motor_state(self):
        return self.disk_motor_state

    def set_illumination_iris(self, value):
        # value: 0 - 100
        self.illumination_iris = value
        print("illumination_iris", self.illumination_iris)
        return self.illumination_iris

    def get_illumination_iris(self):
        self.illumination_iris = 100
        return self.illumination_iris

    def set_emission_iris(self, value):
        # value: 0 - 100
        self.emission_iris = value
        print("emission_iris", self.emission_iris)
        return self.emission_iris

    def get_emission_iris(self):
        self.emission_iris = 100
        return self.emission_iris

    def set_filter_slider(self, position):
        if str(position) not in ["0", "1", "2", "3"]:
            raise ValueError("Invalid slider position!")
        self.slider_position = position
        return self.slider_position


# CrestOptics X-Light Port specs:
# 9600 baud
# 8 data bits
# 1 stop bit
# No parity
# no flow control


class XLight:
    """Wrapper for communicating with CrestOptics X-Light devices over serial"""

    def __init__(
        self, SN, sleep_time_for_wheel=0.25, disable_emission_filter_wheel=True
    ):
        """
        Provide serial number (default is that of the device
        cephla already has) for device-finding purposes. Otherwise, all
        XLight devices should use the same serial protocol
        """
        self.log = squid.core.logging.get_logger(self.__class__.__name__)

        self.has_spinning_disk_motor = False
        self.has_spinning_disk_slider = False
        self.has_dichroic_filters_wheel = False
        self.has_emission_filters_wheel = False
        self.has_excitation_filters_wheel = False
        self.has_illumination_iris_diaphragm = False
        self.has_emission_iris_diaphragm = False
        self.has_dichroic_filter_slider = False
        self.has_ttl_control = False
        self.sleep_time_for_wheel = sleep_time_for_wheel

        self.disable_emission_filter_wheel = disable_emission_filter_wheel

        self.serial_connection = SerialDevice(
            SN=SN,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.serial_connection.open_ser()

        self.parse_idc_response(self.serial_connection.write_and_read("idc\r"))
        self.print_config()

        if self.has_illumination_iris_diaphragm:
            self.set_illumination_iris(XLIGHT_ILLUMINATION_IRIS_DEFAULT)
        if self.has_emission_iris_diaphragm:
            self.set_emission_iris(XLIGHT_EMISSION_IRIS_DEFAULT)

    def parse_idc_response(self, response):
        # Convert hexadecimal response to integer
        config_value = int(response, 16)

        # Check each bit and set the corresponding variable
        self.has_spinning_disk_motor = bool(config_value & 0x00000001)
        self.has_spinning_disk_slider = bool(config_value & 0x00000002)
        self.has_dichroic_filters_wheel = bool(config_value & 0x00000004)
        self.has_emission_filters_wheel = bool(config_value & 0x00000008)
        self.has_excitation_filters_wheel = bool(config_value & 0x00000080)
        self.has_illumination_iris_diaphragm = bool(config_value & 0x00000200)
        self.has_emission_iris_diaphragm = bool(config_value & 0x00000400)
        self.has_dichroic_filter_slider = bool(config_value & 0x00000800)
        self.has_ttl_control = bool(config_value & 0x00001000)

    def print_config(self):
        self.log.info(
            (
                "Machine Configuration:\n"
                f"  Spinning disk motor: {self.has_spinning_disk_motor}\n",
                f"  Spinning disk slider: {self.has_spinning_disk_slider}\n",
                f"  Dichroic filters wheel: {self.has_dichroic_filters_wheel}\n",
                f"  Emission filters wheel: {self.has_emission_filters_wheel}\n",
                f"  Excitation filters wheel: {self.has_excitation_filters_wheel}\n",
                f"  Illumination Iris diaphragm: {self.has_illumination_iris_diaphragm}\n",
                f"  Emission Iris diaphragm: {self.has_emission_iris_diaphragm}\n",
                f"  Dichroic filter slider: {self.has_dichroic_filter_slider}\n",
                f"  TTL control and combined commands subsystem: {self.has_ttl_control}",
            )
        )

    def set_emission_filter(self, position, extraction=False, validate=True):
        if self.disable_emission_filter_wheel:
            print("emission filter wheel disabled")
            return -1
        if str(position) not in ["1", "2", "3", "4", "5", "6", "7", "8"]:
            raise ValueError("Invalid emission filter wheel position!")
        position_to_write = str(position)
        position_to_read = str(position)
        if extraction:
            position_to_write += "m"

        if validate:
            current_pos = self.serial_connection.write_and_check(
                "B" + position_to_write + "\r", "B" + position_to_read, read_delay=0.01
            )
            self.emission_wheel_pos = int(current_pos[1])
        else:
            self.serial_connection.write("B" + position_to_write + "\r")
            time.sleep(self.sleep_time_for_wheel)
            self.emission_wheel_pos = position

        return self.emission_wheel_pos

    def get_emission_filter(self):
        current_pos = self.serial_connection.write_and_check(
            "rB\r", "rB", read_delay=0.01
        )
        self.emission_wheel_pos = int(current_pos[2])
        return self.emission_wheel_pos

    def set_dichroic(self, position, extraction=False):
        if str(position) not in ["1", "2", "3", "4", "5"]:
            raise ValueError("Invalid dichroic wheel position!")
        position_to_write = str(position)
        position_to_read = str(position)
        if extraction:
            position_to_write += "m"

        current_pos = self.serial_connection.write_and_check(
            "C" + position_to_write + "\r", "C" + position_to_read, read_delay=0.01
        )
        self.dichroic_wheel_pos = int(current_pos[1])
        return self.dichroic_wheel_pos

    def get_dichroic(self):
        current_pos = self.serial_connection.write_and_check(
            "rC\r", "rC", read_delay=0.01
        )
        self.dichroic_wheel_pos = int(current_pos[2])
        return self.dichroic_wheel_pos

    def set_disk_position(self, position):
        if str(position) not in ["0", "1", "2", "wide field", "confocal"]:
            raise ValueError("Invalid disk position!")
        if position == "wide field":
            position = "0"

        if position == "confocal":
            position = "1'"

        position_to_write = str(position)
        position_to_read = str(position)

        current_pos = self.serial_connection.write_and_check(
            "D" + position_to_write + "\r", "D" + position_to_read, read_delay=5
        )
        self.spinning_disk_pos = int(current_pos[1])
        return self.spinning_disk_pos

    def set_illumination_iris(self, value):
        # value: 0 - 100
        self.illumination_iris = value
        value = str(int(10 * value))
        self.serial_connection.write_and_check(
            "J" + value + "\r", "J" + value, read_delay=3
        )
        return self.illumination_iris

    def get_illumination_iris(self):
        current_pos = self.serial_connection.write_and_check(
            "rJ\r", "rJ", read_delay=0.01
        )
        self.illumination_iris = int(int(current_pos[2:]) / 10)
        return self.illumination_iris

    def set_emission_iris(self, value):
        # value: 0 - 100
        self.emission_iris = value
        value = str(int(10 * value))
        self.serial_connection.write_and_check(
            "V" + value + "\r", "V" + value, read_delay=3
        )
        return self.emission_iris

    def get_emission_iris(self):
        current_pos = self.serial_connection.write_and_check(
            "rV\r", "rV", read_delay=0.01
        )
        self.emission_iris = int(int(current_pos[2:]) / 10)
        return self.emission_iris

    def set_filter_slider(self, position):
        if str(position) not in ["0", "1", "2", "3"]:
            raise ValueError("Invalid slider position!")
        self.slider_position = position
        position_to_write = str(position)
        position_to_read = str(position)
        self.serial_connection.write_and_check(
            "P" + position_to_write + "\r", "V" + position_to_read, read_delay=5
        )
        return self.slider_position

    def get_disk_position(self):
        current_pos = self.serial_connection.write_and_check(
            "rD\r", "rD", read_delay=0.01
        )
        self.spinning_disk_pos = int(current_pos[2])
        return self.spinning_disk_pos

    def set_disk_motor_state(self, state):
        """Set True for ON, False for OFF"""
        if state:
            state_to_write = "1"
        else:
            state_to_write = "0"

        current_pos = self.serial_connection.write_and_check(
            "N" + state_to_write + "\r", "N" + state_to_write, read_delay=2.5
        )

        self.disk_motor_state = bool(int(current_pos[1]))

    def get_disk_motor_state(self):
        """Return True for on, Off otherwise"""
        current_pos = self.serial_connection.write_and_check(
            "rN\r", "rN", read_delay=0.01
        )
        self.disk_motor_state = bool(int(current_pos[2]))
        return self.disk_motor_state
