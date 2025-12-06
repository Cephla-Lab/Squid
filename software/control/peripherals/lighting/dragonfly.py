import serial

from control.peripherals.serial_base import SerialDevice, SerialDeviceError

import squid.logging


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
        response = self.serial_connection.write_and_read(
            command + "\r", read_delay=read_delay
        )

        if response.endswith(":A"):
            # Success - return the response without :A suffix
            return response[:-2]
        elif response.endswith(":N"):
            # Failure
            self.log.error(f"Command failed: {command} -> {response}")
            raise SerialDeviceError(
                f"Dragonfly command failed: {command} -> {response}"
            )
        else:
            # Unexpected response format
            self.log.error(f"Unexpected response format: {command} -> {response}")
            raise SerialDeviceError(
                f"Dragonfly unexpected response format: {command} -> {response}"
            )

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
        self._send_command(command)
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
        if not self.ps_info or not (
            1 <= self.current_port_selection_dichroic <= len(self.ps_info)
        ):
            raise ValueError(
                f"Port selection dichroic info does not match current position: {self.ps_info}"
            )

        if self.ps_info[self.current_port_selection_dichroic - 1].endswith("100% Pass"):
            return 1
        elif self.ps_info[self.current_port_selection_dichroic - 1].endswith(
            "100% Reflect"
        ):
            return 2
        else:
            raise ValueError(
                f"Unknown camera port: {self.ps_info[self.current_port_selection_dichroic - 1]}"
            )

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
        response = self._send_command("AT_AP_POS,1,?")
        if response.isdigit():
            return int(response)
        else:
            raise ValueError(f"Unknown aperture position: {response}")

    def _get_component_info(
        self, component_type: str, port: int, index: int | None = None
    ) -> str:
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
        available = response.split(",")[
            1
        ]  # Not sure about the format of the response. Need to confirm.
        if available == "0":
            return []
        else:
            info = []
            for i in range(
                1, 8
            ):  # Assume there are 8 positions on the emission filter wheel
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

    def _get_component_info(
        self, component_type: str, port: int, index: int | None = None
    ) -> str:
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
