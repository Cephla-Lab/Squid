import abc

import serial
from serial.tools import list_ports
import time
from typing import Tuple, Optional
import struct
from control.lighting import LightSourceType, IntensityControlMode, ShutterControlMode
from control._def import *
from squid.abc import LightSource

import squid.logging

log = squid.logging.get_logger(__name__)


class SerialDevice:
    """
    General wrapper for serial devices, with
    automating device finding based on VID/PID
    or serial number.
    """

    def __init__(self, port=None, VID=None, PID=None, SN=None, baudrate=9600, read_timeout=0.1, **kwargs):
        # Initialize the serial connection
        self.port = port
        self.VID = VID
        self.PID = PID
        self.SN = SN

        self.baudrate = baudrate
        self.read_timeout = read_timeout
        self.serial_kwargs = kwargs

        self.serial = None

        if VID is not None and PID is not None:
            for d in list_ports.comports():
                if d.vid == VID and d.pid == PID:
                    self.port = d.device
                    break
        if SN is not None:
            for d in list_ports.comports():
                if d.serial_number == SN:
                    self.port = d.device
                    break

        if self.port is not None:
            self.serial = serial.Serial(self.port, baudrate=baudrate, timeout=read_timeout, **kwargs)

    def open_ser(self, SN=None, VID=None, PID=None, baudrate=None, read_timeout=None, **kwargs):
        if self.serial is not None and not self.serial.is_open:
            self.serial.open()

        if SN is None:
            SN = self.SN

        if VID is None:
            VID = self.VID

        if PID is None:
            PID = self.PID

        if baudrate is None:
            baudrate = self.baudrate

        if read_timeout is None:
            read_timeout = self.read_timeout

        for k in self.serial_kwargs.keys():
            if k not in kwargs:
                kwargs[k] = self.serial_kwargs[k]

        if self.serial is None:
            if VID is not None and PID is not None:
                for d in list_ports.comports():
                    if d.vid == VID and d.pid == PID:
                        self.port = d.device
                        break
            if SN is not None:
                for d in list_ports.comports():
                    if d.serial_number == SN:
                        self.port = d.device
                        break
            if self.port is not None:
                self.serial = serial.Serial(self.port, **kwargs)

    def write_and_check(
        self,
        command,
        expected_response,
        read_delay=0.1,
        max_attempts=5,
        attempt_delay=1,
        check_prefix=True,
        print_response=False,
    ):
        # Write a command and check the response
        for attempt in range(max_attempts):
            self.serial.write(command.encode())
            time.sleep(read_delay)  # Wait for the command to be sent/executed

            response = self.serial.readline().decode().strip()
            if print_response:
                log.info(response)

            # flush the input buffer
            while self.serial.in_waiting:
                if print_response:
                    log.info(self.serial.readline().decode().strip())
                else:
                    self.serial.readline().decode().strip()

            # check response
            if response == expected_response:
                return response
            else:
                log.warning(response)

            # check prefix if the full response does not match
            if check_prefix:
                if response.startswith(expected_response):
                    return response
            else:
                time.sleep(attempt_delay)  # Wait before retrying

        raise SerialDeviceError("Max attempts reached without receiving expected response.")

    def write_and_read(self, command, read_delay=0.1, max_attempts=3, attempt_delay=1):
        for attempt in range(max_attempts):
            self.serial.write(command.encode())
            time.sleep(read_delay)  # Wait for the command to be sent
            response = self.serial.readline().decode().strip()
            if response:
                return response
            else:
                time.sleep(attempt_delay)  # Wait before retrying

        raise SerialDeviceError("Max attempts reached without receiving response.")

    def write(self, command):
        self.serial.write(command.encode())

    def close(self):
        # Close the serial connection
        self.serial.close()


class SerialDeviceError(RuntimeError):
    pass


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

    def __init__(self, SN, sleep_time_for_wheel=0.25, disable_emission_filter_wheel=True):
        """
        Provide serial number (default is that of the device
        cephla already has) for device-finding purposes. Otherwise, all
        XLight devices should use the same serial protocol
        """
        self.log = squid.logging.get_logger(self.__class__.__name__)

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
                "Machine Configuration:\n" f"  Spinning disk motor: {self.has_spinning_disk_motor}\n",
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
        current_pos = self.serial_connection.write_and_check("rB\r", "rB", read_delay=0.01)
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
        current_pos = self.serial_connection.write_and_check("rC\r", "rC", read_delay=0.01)
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
        self.serial_connection.write_and_check("J" + value + "\r", "J" + value, read_delay=3)
        return self.illumination_iris

    def get_illumination_iris(self):
        current_pos = self.serial_connection.write_and_check("rJ\r", "rJ", read_delay=0.01)
        self.illumination_iris = int(int(current_pos[2:]) / 10)
        return self.illumination_iris

    def set_emission_iris(self, value):
        # value: 0 - 100
        self.emission_iris = value
        value = str(int(10 * value))
        self.serial_connection.write_and_check("V" + value + "\r", "V" + value, read_delay=3)
        return self.emission_iris

    def get_emission_iris(self):
        current_pos = self.serial_connection.write_and_check("rV\r", "rV", read_delay=0.01)
        self.emission_iris = int(int(current_pos[2:]) / 10)
        return self.emission_iris

    def set_filter_slider(self, position):
        if str(position) not in ["0", "1", "2", "3"]:
            raise ValueError("Invalid slider position!")
        self.slider_position = position
        position_to_write = str(position)
        position_to_read = str(position)
        self.serial_connection.write_and_check("P" + position_to_write + "\r", "V" + position_to_read, read_delay=5)
        return self.slider_position

    def get_disk_position(self):
        current_pos = self.serial_connection.write_and_check("rD\r", "rD", read_delay=0.01)
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
        current_pos = self.serial_connection.write_and_check("rN\r", "rN", read_delay=0.01)
        self.disk_motor_state = bool(int(current_pos[2]))
        return self.disk_motor_state


class Dragonfly:

    def __init__(self, SN: str):
        self.log = squid.logging.get_logger(self.__class__.__name__)
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
        self.get_config()

        # Exit standby mode
        self._send_command("AT_STANDBY,0", read_delay=10)
        self._send_command("AT_DC_SLCT,1")
        self.set_disk_speed(self.spinning_disk_max_speed)
        self.get_port_selection_dichroic()
        self.ps_info = self.get_port_selection_dichroic_info()

    def _send_command(self, command: str, read_delay: float = 0.1) -> str:
        """Send AT command and return response

        Args:
            command: Command to send (without \r)
            read_delay: Time to wait for response

        Returns:
            Response content (without suffix) on success, None on failure
        """
        response = self.serial_connection.write_and_read(command + "\r", read_delay=read_delay)

        if response.endswith(":A"):
            # Success - return the response without :A suffix
            return response[:-2]
        elif response.endswith(":N"):
            # Failure
            self.log.error(f"Command failed: {command} -> {response}")
            raise SerialDeviceError(f"Dragonfly command failed: {command} -> {response}")
        else:
            # Unexpected response format
            self.log.error(f"Unexpected response format: {command} -> {response}")
            raise SerialDeviceError(f"Dragonfly unexpected response format: {command} -> {response}")

    def get_config(self):
        """Get device configuration and capabilities"""
        self.log.info("Dragonfly configuration:")

        # Get serial number
        serial_num = self._send_command("AT_SERIAL_CSU,?")
        if serial_num:
            self.log.info(f"Serial Number: {serial_num}")

        # Get product info
        product = self._send_command("AT_PRODUCT_CSU,?")
        if product:
            self.log.info(f"Product: {product}")

        # Get version
        version = self._send_command("AT_VER,?")
        if version:
            self.log.info(f"Version: {version}")

        # Get max motor speed
        max_speed = self._send_command("AT_MS_MAX,?")
        if max_speed:
            self.spinning_disk_max_speed = int(max_speed)
            self.log.info(f"Max disk speed: {self.spinning_disk_max_speed}")

        # Get system info
        system_info = self._send_command("AT_SYSTEM,?")
        if system_info:
            self.log.info(f"System info: {system_info}")

    def set_emission_filter(self, port: int, position: int):
        """Set emission filter wheel position

        Args:
            port: Filter wheel port number (typically 1)
            position: Target position (1-8 typically)
        """
        command = f"AT_FW_POS,{port},{position}"
        self._send_command(command)

    def get_emission_filter(self, port: int) -> int:
        """Get current emission filter wheel position

        Args:
            port: Filter wheel port number (typically 1)

        Returns:
            Current position
        """
        response = self._send_command(f"AT_FW_POS,{port},?")
        if response.isdigit():
            return int(response)
        else:
            raise ValueError(f"Unknown emission filter position: {response}")

    def set_port_selection_dichroic(self, position: int) -> int:
        """Set port selection dichroic position

        Args:
            position: Target position
        """
        command = f"AT_PS_POS,1,{position}"
        response = self._send_command(command)
        return position

    def get_port_selection_dichroic(self) -> int:
        """Get current port selection dichroic position

        Returns:
            Current position
        """
        response = self._send_command("AT_PS_POS,1,?")
        if response.isdigit():
            self.current_port_selection_dichroic = int(response)
            return self.current_port_selection_dichroic
        else:
            raise ValueError(f"Unknown port selection dichroic position: {response}")

    def get_camera_port(self) -> int:
        """Get current camera port

        Returns:
            Current camera port (1 or 2)
        """
        if not self.ps_info or not (1 <= self.current_port_selection_dichroic <= len(self.ps_info)):
            raise ValueError(f"Port selection dichroic info does not match current position: {self.ps_info}")

        if self.ps_info[self.current_port_selection_dichroic - 1].endswith("100% Pass"):
            return 1
        elif self.ps_info[self.current_port_selection_dichroic - 1].endswith("100% Reflect"):
            return 2
        else:
            raise ValueError(f"Unknown camera port: {self.ps_info[self.current_port_selection_dichroic - 1]}")

    def set_modality(self, modality: str):
        """Set imaging modality

        Args:
            modality: Modality string (e.g., 'CONFOCAL', 'BF', etc.)
        """
        command = f"AT_MODALITY,{modality}"
        self._send_command(command, read_delay=2)

    def get_modality(self) -> str:
        """Get current imaging modality

        Returns:
            Current modality string
        """
        return self._send_command("AT_MODALITY,?")

    def set_disk_motor_state(self, run: bool) -> bool:
        """Start or stop the spinning disk motor

        Args:
            run: True to start, False to stop
        """
        if run:
            self._send_command("AT_MS_RUN", read_delay=2)
        else:
            self._send_command("AT_MS_STOP", read_delay=1)

    def get_disk_motor_state(self) -> bool:
        """Get spinning disk motor state

        Returns:
            True if running, False if stopped
        """
        speed = self.get_disk_speed()
        return speed > 0

    def set_disk_speed(self, speed: int):
        """Set spinning disk motor speed

        Args:
            speed: Speed in RPM (0 to stop)

        Returns:
            Set speed
        """
        command = f"AT_MS,{speed}"
        self._send_command(command, read_delay=0.1)

    def get_disk_speed(self) -> int:
        """Get current spinning disk motor speed

        Returns:
            Current speed in RPM
        """
        response = self._send_command("AT_MS,?")
        if response.isdigit():
            return int(response)
        else:
            raise ValueError(f"Unknown disk speed: {response}")

    def set_filter_wheel_speed(self, port: int, speed: int):
        """Set filter wheel rotation speed

        Args:
            port: Filter wheel port number
            speed: Speed setting
        """
        command = f"AT_FW_SPEED,{port},{speed}"
        self._send_command(command)

    def set_field_aperture_wheel_position(self, position: int):
        """Set aperture position

        Args:
            position: Target position
        """
        command = f"AT_AP_POS,1,{position}"
        self._send_command(command)

    def get_field_aperture_wheel_position(self) -> int:
        """Get current aperture position

        Returns:
            Current position
        """
        response = self._send_command(f"AT_AP_POS,1,?")
        if response.isdigit():
            return int(response)
        else:
            raise ValueError(f"Unknown aperture position: {response}")

    def _get_component_info(self, component_type: str, port: int, index: int | None = None) -> str:
        """Get information about a component

        Args:
            component_type: Component type (e.g., 'FW', 'AP', 'PS', 'DM')
            port: Port number
            index: Optional index for additional info

        Returns:
            Component info string
        """
        if index is not None:
            command = f"AT_{component_type}_INFO,{port},{index},?"
        else:
            command = f"AT_{component_type}_INFO,{port},?"

        return self._send_command(command)

    def get_emission_filter_info(self, port: int) -> list[str]:
        response = self._send_command(f"AT_FW_COMPO,{port},?")
        available = response.split(",")[1]  # Not sure about the format of the response. Need to confirm.
        if available == "0":
            return []
        else:
            info = []
            for i in range(1, 8):  # Assume there are 8 positions on the emission filter wheel
                info.append(str(i) + ":" + self._get_component_info("FW", port, i))
            return info

    def get_field_aperture_info(self) -> list[str]:
        info = []
        for i in range(1, 11):  # There are 10 positions on the field aperture wheel
            info.append(self._get_component_info("AP", 1, i))
        return info

    def get_port_selection_dichroic_info(self) -> list[str]:
        info = []
        for i in range(1, 5):  # There are 4 positions for the port selection dichroic
            info.append(self._get_component_info("PS", 1, i))
        return info

    def close(self):
        """Close serial connection"""
        if self.serial_connection:
            self.serial_connection.close()


class Dragonfly_Simulation:
    def __init__(self, SN="00000000"):
        self.log = squid.logging.get_logger(self.__class__.__name__)

        # Internal state variables
        self.emission_filter_positions = {1: 1, 2: 1}  # port -> position
        self.field_aperture_positions = {1: 1, 2: 1}  # port -> position
        self.dichroic_position = 1
        self.current_modality = "BF"  # Default to brightfield
        self.disk_speed = 0
        self.disk_motor_running = False

        # Configuration info
        self.spinning_disk_max_speed = 10000

        self.log.info("Dragonfly simulation initialized")

    def get_config(self):
        """Simulate device configuration retrieval"""
        self.log.info("Dragonfly simulation configuration:")
        self.log.info("Serial Number: SIM12345")
        self.log.info("Product: Dragonfly Simulator")
        self.log.info("Version: 1.0.0")
        self.log.info(f"Max disk speed: {self.spinning_disk_max_speed}")
        self.log.info("System info: Simulation System")

    def set_emission_filter(self, port: int, position: int):
        """Set emission filter wheel position"""
        self.emission_filter_positions[port] = position
        self.log.debug(f"Set emission filter port {port} to position {position}")

    def get_emission_filter(self, port: int) -> int:
        """Get current emission filter wheel position"""
        return self.emission_filter_positions.get(port, 1)

    def set_port_selection_dichroic(self, position: int):
        """Set port selection dichroic position"""
        self.dichroic_position = position
        self.log.debug(f"Set dichroic to position {position}")

    def get_port_selection_dichroic(self) -> int:
        """Get current port selection dichroic position"""
        return self.dichroic_position

    def get_camera_port(self) -> int:
        """Get current camera port"""
        if self.dichroic_position == 1:
            return 1
        else:
            return 2

    def set_modality(self, modality: str):
        """Set imaging modality"""
        self.current_modality = modality
        self.log.debug(f"Set modality to {modality}")

    def get_modality(self) -> str:
        """Get current imaging modality"""
        return self.current_modality

    def set_disk_motor_state(self, run: bool):
        """Start or stop the spinning disk motor"""
        if run:
            self.disk_motor_running = True
            self.disk_speed = 5000  # Default speed
            self.log.debug("Started disk motor")
            return True
        else:
            self.disk_motor_running = False
            self.disk_speed = 0
            self.log.debug("Stopped disk motor")
            return True

    def get_disk_motor_state(self) -> bool:
        """Get spinning disk motor state"""
        return self.disk_motor_running

    def set_disk_speed(self, speed: int):
        """Set spinning disk motor speed"""
        self.disk_speed = speed
        self.disk_motor_running = speed > 0
        self.log.debug(f"Set disk speed to {speed} RPM")

    def get_disk_speed(self) -> int:
        """Get current spinning disk motor speed"""
        return self.disk_speed

    def set_filter_wheel_speed(self, port: int, speed: int):
        """Set filter wheel rotation speed"""
        self.log.debug(f"Set filter wheel port {port} speed to {speed}")

    def set_field_aperture_wheel_position(self, port: int, position: int):
        """Set aperture position"""
        self.field_aperture_positions[port] = position
        self.log.debug(f"Set field aperture port {port} to position {position}")

    def get_field_aperture_wheel_position(self) -> int:
        """Get current aperture position"""
        return self.field_aperture_positions.get(1, 1)

    def _get_component_info(self, component_type: str, port: int, index: int | None = None) -> str:
        """Get information about a component"""
        return f"Component {component_type} Port {port} - Simulation"

    def get_emission_filter_info(self, port: int) -> list[str]:
        return [str(i) for i in range(1, 9)]

    def get_field_aperture_info(self) -> list[str]:
        return [str(i) for i in range(1, 11)]

    def get_port_selection_dichroic_info(self) -> list[str]:
        return [str(i) for i in range(1, 5)]

    def close(self):
        """Close the simulated connection"""
        self.log.info("Dragonfly simulation closed")


class LDI(LightSource):
    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN="00000001"):
        """
        Provide serial number
        """
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.serial_connection = SerialDevice(
            SN=SN,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.serial_connection.open_ser()
        if LDI_INTENSITY_MODE == "PC":
            self.intensity_mode = IntensityControlMode.Software
        elif LDI_INTENSITY_MODE == "EXT":
            self.intensity_mode = IntensityControlMode.SquidControllerDAC
        if LDI_SHUTTER_MODE == "PC":
            self.shutter_mode = ShutterControlMode.Software
        elif LDI_SHUTTER_MODE == "EXT":
            self.shutter_mode = ShutterControlMode.TTL

        self.channel_mappings = {
            405: 405,
            470: 470,
            488: 470,
            545: 555,
            550: 555,
            555: 555,
            561: 555,
            638: 640,
            640: 640,
            730: 730,
            735: 730,
            750: 730,
        }
        self.active_channel = None

    def initialize(self):
        self.serial_connection.write_and_check("run!\r", "ok")

    def set_shutter_control_mode(self, mode):
        if mode == ShutterControlMode.TTL:
            self.serial_connection.write_and_check("SH_MODE=EXT\r", "ok")
        elif mode == ShutterControlMode.Software:
            self.serial_connection.write_and_check("SH_MODE=PC\r", "ok")
        self.shutter_mode = mode

    def get_shutter_control_mode(self):
        pass

    def set_intensity_control_mode(self, mode):
        if mode == IntensityControlMode.SquidControllerDAC:
            self.serial_connection.write_and_check("INT_MODE=EXT\r", "ok")
        elif mode == IntensityControlMode.Software:
            self.serial_connection.write_and_check("INT_MODE=PC\r", "ok")
        self.intensity_mode = mode

    def get_intensity_control_mode(self):
        pass

    def set_intensity(self, channel, intensity):
        channel = str(channel)
        intensity = "{:.2f}".format(intensity)
        self.log.debug("set:" + channel + "=" + intensity + "\r")
        self.serial_connection.write_and_check("set:" + channel + "=" + intensity + "\r", "ok")

    def get_intensity(self, channel):
        try:
            response = self.serial_connection.write_and_read("set?\r")
            pairs = response.replace("SET:", "").split(",")
            intensities = {}
            for pair in pairs:
                channel, value = pair.split("=")
                intensities[int(channel)] = int(value)
            return intensities[channel]
        except:
            return None

    def set_shutter_state(self, channel, on):
        channel = str(channel)
        state = str(on)
        if self.active_channel is not None and channel != self.active_channel:
            self.set_active_channel_shutter(False)
        self.serial_connection.write_and_check("shutter:" + channel + "=" + state + "\r", "ok")
        if on:
            self.active_channel = channel

    def get_shutter_state(self, channel):
        try:
            response = self.serial_connection.write_and_read("shutter?" + channel + "\r")
            state = response.split("=")[1]
            return 1 if state == "OPEN" else 0
        except:
            return None

    def set_active_channel_shutter(self, state):
        channel = str(self.active_channel)
        state = str(state)
        self.log.debug("shutter:" + channel + "=" + state + "\r")
        self.serial_connection.write_and_check("shutter:" + channel + "=" + state + "\r", "ok")

    def shut_down(self):
        for ch in list(set(self.channel_mappings.values())):
            self.set_intensity(ch, 0)
            self.set_shutter_state(ch, False)
        self.serial_connection.close()


class LDI_Simulation(LightSource):
    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN="00000001"):
        """
        Provide serial number
        """
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.intensity_mode = IntensityControlMode.Software
        self.shutter_mode = ShutterControlMode.Software

        self.channel_mappings = {
            405: 405,
            470: 470,
            488: 470,
            545: 555,
            550: 555,
            555: 555,
            561: 555,
            638: 640,
            640: 640,
            730: 730,
            735: 730,
            750: 730,
        }
        self.active_channel = None

    def initialize(self):
        pass

    def set_shutter_control_mode(self, mode):
        self.shutter_mode = mode

    def get_shutter_control_mode(self):
        pass

    def set_intensity_control_mode(self, mode):
        self.intensity_mode = mode

    def get_intensity_control_mode(self):
        pass

    def set_intensity(self, channel, intensity):
        channel = str(channel)
        intensity = "{:.2f}".format(intensity)
        self.log.debug("set:" + channel + "=" + intensity + "\r")

    def get_intensity(self, channel):
        return 100

    def set_shutter_state(self, channel, on):
        channel = str(channel)
        state = str(on)
        if self.active_channel is not None and channel != self.active_channel:
            self.set_active_channel_shutter(False)
        if on:
            self.active_channel = channel

    def get_shutter_state(self, channel):
        return 1

    def set_active_channel_shutter(self, state):
        channel = str(self.active_channel)
        state = str(state)
        self.log.debug("shutter:" + channel + "=" + state + "\r")

    def shut_down(self):
        for ch in list(set(self.channel_mappings.values())):
            self.set_intensity(ch, 0)
            self.set_shutter_state(ch, False)


class SciMicroscopyLEDArray:
    """Wrapper for communicating with SciMicroscopy over serial"""

    def __init__(self, SN, array_distance=50, turn_on_delay=0.03):
        """
        Provide serial number
        """
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
        self.check_about()
        self.set_distance(array_distance)
        self.set_brightness(1)

        self.illumination = None
        self.NA = 0.5
        self.turn_on_delay = turn_on_delay

    def write(self, command):
        self.serial_connection.write_and_check(command + "\r", "", read_delay=0.01, print_response=True)

    def check_about(self):
        self.serial_connection.write_and_check("about" + "\r", "=", read_delay=0.01, print_response=True)

    def set_distance(self, array_distance):
        # array distance in mm
        array_distance = str(int(array_distance))
        self.serial_connection.write_and_check(
            "sad." + array_distance + "\r",
            "Current array distance from sample is " + array_distance + "mm",
            read_delay=0.01,
            print_response=False,
        )

    def set_NA(self, NA):
        self.NA = NA
        NA = str(int(NA * 100))
        self.serial_connection.write_and_check(
            "na." + NA + "\r", "Current NA is 0." + NA, read_delay=0.01, print_response=False
        )

    def set_color(self, color):
        # (r,g,b), 0-1
        r = int(255 * color[0])
        g = int(255 * color[1])
        b = int(255 * color[2])
        self.serial_connection.write_and_check(
            f"sc.{r}.{g}.{b}\r", f"Current color balance values are {r}.{g}.{b}", read_delay=0.01, print_response=False
        )

    def set_brightness(self, brightness):
        # 0 to 100
        brightness = str(int(255 * (brightness / 100.0)))
        self.serial_connection.write_and_check(
            f"sb.{brightness}\r", f"Current brightness value is {brightness}.", read_delay=0.01, print_response=False
        )

    def turn_on_bf(self):
        self.serial_connection.write_and_check(f"bf\r", "-==-", read_delay=0.01, print_response=False)

    def turn_on_dpc(self, quadrant):
        self.serial_connection.write_and_check(f"dpc.{quadrant[0]}\r", "-==-", read_delay=0.01, print_response=False)

    def turn_on_df(self):
        self.serial_connection.write_and_check(f"df\r", "-==-", read_delay=0.01, print_response=False)

    def set_illumination(self, illumination):
        self.illumination = illumination

    def clear(self):
        self.serial_connection.write_and_check("x\r", "-==-", read_delay=0.01, print_response=False)

    def turn_on_illumination(self):
        if self.illumination is not None:
            self.serial_connection.write_and_check(
                f"{self.illumination}\r", "-==-", read_delay=0.01, print_response=False
            )
            time.sleep(self.turn_on_delay)

    def turn_off_illumination(self):
        self.clear()


class SciMicroscopyLEDArray_Simulation:
    """Wrapper for communicating with SciMicroscopy over serial"""

    def __init__(self, SN, array_distance=50, turn_on_delay=0.03):
        """
        Provide serial number
        """
        self.serial_connection.open_ser()
        self.check_about()
        self.set_distance(array_distance)
        self.set_brightness(1)

        self.illumination = None
        self.NA = 0.5
        self.turn_on_delay = turn_on_delay

    def write(self, command):
        pass

    def check_about(self):
        pass

    def set_distance(self, array_distance):
        # array distance in mm
        array_distance = str(int(array_distance))

    def set_NA(self, NA):
        self.NA = NA
        NA = str(int(NA * 100))

    def set_color(self, color):
        # (r,g,b), 0-1
        r = int(255 * color[0])
        g = int(255 * color[1])
        b = int(255 * color[2])

    def set_brightness(self, brightness):
        # 0 to 100
        brightness = str(int(255 * (brightness / 100.0)))

    def turn_on_bf(self):
        pass

    def turn_on_dpc(self, quadrant):
        pass

    def turn_on_df(self):
        pass

    def set_illumination(self, illumination):
        pass

    def clear(self):
        pass

    def turn_on_illumination(self):
        pass

    def turn_off_illumination(self):
        pass


class CellX:

    VALID_MODULATIONS = ["INT", "EXT Digital", "EXT Analog", "EXT Mixed"]

    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN="", initial_modulation=CELLX_MODULATION):
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
        self.power = {}

        for channel in [1, 2, 3, 4]:
            self.set_modulation(channel, initial_modulation)
            self.turn_on(channel)

    def turn_on(self, channel):
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + ":AM:STAT ON\r", "OK", read_delay=0.01, print_response=False
        )

    def turn_off(self, channel):
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + ":AM:STAT OFF\r", "OK", read_delay=0.01, print_response=False
        )

    def set_laser_power(self, channel, power):
        if not (power >= 1 and power <= 100):
            raise ValueError(f"Power={power} not in the range 1 to 100")

        if channel not in self.power.keys() or power != self.power[channel]:
            self.serial_connection.write_and_check(
                "SOUR" + str(channel) + ":POW:LEV:IMM:AMPL " + str(power / 1000) + "\r",
                "OK",
                read_delay=0.01,
                print_response=False,
            )
            self.power[channel] = power
        else:
            pass  # power is the same

    def set_modulation(self, channel, modulation):
        if modulation not in CellX.VALID_MODULATIONS:
            raise ValueError(f"Modulation '{modulation}' not in valid modulations: {CellX.VALID_MODULATIONS}")
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + ":AM:" + modulation + "\r", "OK", read_delay=0.01, print_response=False
        )

    def close(self):
        self.serial_connection.close()


class CellX_Simulation:
    """Wrapper for communicating with LDI over serial"""

    def __init__(self, SN=""):
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
        self.power = {}

    def turn_on(self, channel):
        pass

    def turn_off(self, channel):
        pass

    def set_laser_power(self, channel, power):
        if not (power >= 1 and power <= 100):
            raise ValueError(f"Power={power} not in the range 1 to 100")

        if channel not in self.power.keys() or power != self.power[channel]:
            self.power[channel] = power
        else:
            pass  # power is the same

    def set_modulation(self, channel, modulation):
        if modulation not in CellX.VALID_MODULATIONS:
            raise ValueError(f"modulation '{modulation}' not in valid choices: {CellX.VALID_MODULATIONS}")
        self.serial_connection.write_and_check(
            "SOUR" + str(channel) + "AM:" + modulation + "\r", "OK", read_delay=0.01, print_response=False
        )

    def close(self):
        pass


class FilterDeviceInfo:
    """
    keep filter device information
    """

    # default: 7.36
    firmware_version = ""
    # default: 250000
    maxspeed = 0
    # default: 900
    accel = 0


class FilterController_Simulation:
    """
    controller of filter device
    """

    def __init__(self, _baudrate, _bytesize, _parity, _stopbits):
        self.each_hole_microsteps = 4800
        self.current_position = 0
        self.current_index = 1
        """
        the variable be used to keep current offset of wheel
        it could be used by get the index of wheel position, the index could be '1', '2', '3' ... 
        """
        self.offset_position = 0

        self.deviceinfo = FilterDeviceInfo()

    def __del__(self):
        pass

    def do_homing(self):
        self.current_position = 0
        self.offset_position = 1100

    def wait_homing_finish(self):
        pass

    def set_emission_filter(self, index):
        self.current_index = index
        pass

    def get_emission_filter(self):
        return 1

    def start_homing(self):
        pass

    def complete_homing_sequence(self):
        pass

    def wait_for_homing_complete(self):
        pass


class FilterControllerError(Exception):
    """Custom exception for FilterController errors."""

    pass


class FilterController:
    """Controller for filter device."""

    MICROSTEPS_PER_HOLE = 4800
    OFFSET_POSITION = -8500
    VALID_POSITIONS = set(range(1, 8))
    MAX_RETRIES = 3
    COMMAND_TIMEOUT = 1  # seconds

    def __init__(self, serial_number: str, baudrate: int, bytesize: int, parity: str, stopbits: int):
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.current_position = 0
        self.current_index = 1
        self.serial = self._initialize_serial(serial_number, baudrate, bytesize, parity, stopbits)
        self._configure_device()

    def _initialize_serial(
        self, serial_number: str, baudrate: int, bytesize: int, parity: str, stopbits: int
    ) -> serial.Serial:
        ports = [p.device for p in list_ports.comports() if serial_number == p.serial_number]
        if not ports:
            raise ValueError(f"No device found with serial number: {serial_number}")
        return serial.Serial(
            ports[0],
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=self.COMMAND_TIMEOUT,
        )

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

    def set_emission_filter(self, index: int, blocking: bool = True, timeout: int = 5):
        """
        Set the emission filter to the specified position.

        Args:
            position (int): The desired filter position (1-7).
            blocking (bool): If True, wait for the movement to complete. If False, return immediately.
            timeout (int): Maximum time to wait for the movement to complete (in seconds).

        Raises:
            ValueError: If the position is invalid.
            FilterControllerError: If the command fails to initiate movement.
            TimeoutError: If the movement doesn't complete within the specified timeout (only in blocking mode).
        """
        if index not in self.VALID_POSITIONS:
            raise ValueError(f"Invalid emission filter wheel index position: {index}")

        target_position = self.OFFSET_POSITION + (index - 1) * self.MICROSTEPS_PER_HOLE
        success, _ = self._send_command(f"/move abs {target_position}")

        if not success:
            raise FilterControllerError("Failed to initiate filter movement")

        if blocking:
            self._wait_for_position(target_position, index, timeout)
        else:
            # Update the current position without waiting
            self.current_position = target_position
            self.current_index = index

    def _wait_for_position(self, target_position: int, target_index: int, timeout: int):
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

    def get_emission_filter_position(self) -> int:
        return self.calculate_filter_index() + 1

    def start_homing(self):
        """
        Start the homing sequence for the filter device.

        This function initiates the homing process but does not wait for it to complete.
        Use wait_for_homing_complete() to wait for the homing process to finish.

        Raises:
            FilterControllerError: If the homing command fails to initiate.
        """
        success, _ = self._send_command("/home")
        if not success:
            raise FilterControllerError("Failed to initiate homing sequence")

    def wait_for_homing_complete(self, timeout: int = 50) -> bool:
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

    def complete_homing_sequence(self, timeout: int = 50):
        """
        Perform a complete homing sequence.

        This method starts the homing sequence and waits for it to complete.

        Args:
            timeout (int): Maximum time to wait for homing to complete, in seconds.

        Raises:
            FilterControllerError: If homing fails to start or complete.
            TimeoutError: If homing doesn't complete within the specified timeout.
        """
        self.start_homing()
        if not self.wait_for_homing_complete(timeout):
            raise TimeoutError("Filter device homing failed")


class Optospin:
    def __init__(self, SN, baudrate=115200, timeout=1, max_retries=3, retry_delay=0.5):
        self.log = squid.logging.get_logger(self.__class__.__name__)

        optospin_port = [p.device for p in serial.tools.list_ports.comports() if SN == p.serial_number]
        self.ser = serial.Serial(optospin_port[0], baudrate=baudrate, timeout=timeout)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.current_index = 1

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
                    raise Exception(f"Command failed after {self.max_retries} attempts: {str(e)}")

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

    def _usb_go(self, rotor1_pos, rotor2_pos=0, rotor3_pos=0, rotor4_pos=0):
        data = bytes([rotor1_pos | (rotor2_pos << 4), rotor3_pos | (rotor4_pos << 4)])
        self._send_command(0x0088, data)

    def set_emission_filter(self, index):
        self._usb_go(int(index))
        self.current_index = int(index)

    def get_rotor_positions(self):
        result = self._send_command(0x0098)
        rotor1 = result[0] & 0x07
        rotor2 = (result[0] >> 4) & 0x07
        rotor3 = result[1] & 0x07
        rotor4 = (result[1] >> 4) & 0x07
        return rotor1, rotor2, rotor3, rotor4

    def measure_temperatures(self):
        self._send_command(0x00A8)

    def read_temperatures(self):
        result = self._send_command(0x00AC)
        return struct.unpack(">BBBB", result)

    def close(self):
        self.ser.close()


class Optospin_Simulation:
    def __init__(self, SN, baudrate=115200, timeout=1, max_retries=3, retry_delay=0.5):
        self.current_index = 1
        pass

    def _send_command(self, command, data=None):
        pass

    def get_version(self):
        pass

    def set_speed(self, speed):
        pass

    def spin_rotors(self):
        pass

    def stop_rotors(self):
        pass

    def _usb_go(self, rotor1_pos, rotor2_pos=0, rotor3_pos=0, rotor4_pos=0):
        pass

    def set_emission_filter(self, index):
        self.current_index = index
        pass

    def get_rotor_positions(self):
        return 0, 0, 0, 0

    def measure_temperatures(self):
        pass

    def read_temperatures(self):
        pass

    def close(self):
        pass
