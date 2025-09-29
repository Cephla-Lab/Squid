import hid
import struct
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import IntEnum

from squid.abc import LightSource
from control.lighting import IntensityControlMode, ShutterControlMode
import squid.logging


log = squid.logging.get_logger(__name__)


class LaserCommands(IntEnum):
    """Command codes for laser control protocol"""

    SET_SHUTTER_STATE = 0x01
    READ_SHUTTER_STATE = 0x02
    SET_TRANSMISSION = 0x04
    READ_TRANSMISSION = 0x05
    GET_LASER_LINE_SETUP = 0x08
    ERROR_RESPONSE = 0xFF


@dataclass
class LaserUnit:
    """Represents a single laser unit device"""

    vendor_id: int
    product_id: int
    serial_number: str  # Required to distinguish between units
    device_handle: Optional[hid.device] = None
    line_to_wavelength: Dict[int, int] = field(default_factory=dict)  # {line_number: wavelength}


class AndorLaser(LightSource):
    """
    Controller class for managing Andor HLE laser units connected via USB HID.

    Supports multiple units, each with multiple laser lines.
    Units are distinguished by serial number (required when VID/PID are identical).
    Intensity (transmission) values range from 0-1000 (0.0% - 100.0%).

    We use TTL to control on/off. The on/off state is OR with what is set via the computer,
    so all laser lines are set to off (0 intensity) on initialization.
    """

    def __init__(self, vid: int = 0x1BDB, pid: int = 0x0300, debug: bool = False):
        """
        Initialize the laser controller

        Args:
            debug: Enable debug output for HID communications
        """
        self.vid = vid
        self.pid = pid
        self.units: Dict[str, LaserUnit] = {}
        self.intensity_control_mode = IntensityControlMode.Software
        self.shutter_control_mode = ShutterControlMode.TTL
        self.debug = debug

    @staticmethod
    def _find_laser_devices(vendor_id: int, product_id: int) -> List[str]:
        """
        Find all devices with specific vendor/product ID and return their serial numbers.

        Args:
            vendor_id: USB vendor ID to search for
            product_id: USB product ID to search for

        Returns:
            List of serial numbers for matching devices
        """
        serial_numbers = []
        for device in hid.enumerate(vendor_id, product_id):
            if device["serial_number"]:
                serial_numbers.append(device["serial_number"])
        return serial_numbers

    def _add_unit(
        self, unit_id: str, vendor_id: int, product_id: int, serial_number: str, line_to_wavelength: Dict[int, int]
    ) -> bool:
        """
        Add a laser unit to be controlled.

        Args:
            unit_id: Unique identifier for this unit
            vendor_id: USB vendor ID
            product_id: USB product ID
            serial_number: Serial number to identify specific device (required)
            line_to_wavelength: Dictionary mapping line number to wavelength

        Returns:
            True if unit was added successfully
        """
        if not serial_number:
            raise ValueError("Serial number is required to distinguish between units")

        unit = LaserUnit(
            vendor_id=vendor_id,
            product_id=product_id,
            serial_number=serial_number,
            line_to_wavelength=line_to_wavelength,
        )
        self.units[unit_id] = unit
        return True

    def _connect(
        self,
    ) -> Dict[str, bool]:
        """
        Connect to all configured laser units.

        Uses vendor_id, product_id, and serial_number to identify each unit uniquely.

        Returns:
            Dictionary mapping unit_id to connection success status
        """
        results = {}

        for unit_id, unit in self.units.items():
            try:
                device = hid.device()
                # Serial number is required to distinguish between identical devices
                device.open(unit.vendor_id, unit.product_id, unit.serial_number)
                device.set_nonblocking(1)
                unit.device_handle = device

                # Initialize unit with all lasers off
                if self._set_lines_to_off(unit_id):
                    results[unit_id] = True
                else:
                    log.warning(f"Warning: Unit {unit_id} connected but initialization failed")
                    results[unit_id] = True  # Still mark as connected
            except Exception as e:
                log.error(f"Failed to connect to unit {unit_id}: {e}")
                results[unit_id] = False

        return results

    def _disconnect(self):
        """Disconnect from all laser units"""
        for unit in self.units.values():
            if unit.device_handle:
                try:
                    unit.device_handle.close()
                except:
                    log.error(f"Failed to disconnect from unit {unit.serial_number}")
                unit.device_handle = None

    @staticmethod
    def _send_command(unit: LaserUnit, command: bytes, debug: bool = False) -> bool:
        """
        Send a command to the device, handling Report ID if needed.

        Args:
            unit: The laser unit to send to
            command: Command bytes to send

        Returns:
            True if send was successful
        """
        # Prepend Report ID 0
        command = b"\x00" + command

        if debug:
            log.debug(f"Sending: {' '.join(f'0x{b:02x}' for b in command)}")

        try:
            unit.device_handle.write(command)
            return True
        except Exception as e:
            log.error(f"Error sending command: {e}")
            return False

    @staticmethod
    def _read_response(
        unit: LaserUnit, expected_length: int, timeout: float = 0.5, debug: bool = False
    ) -> Optional[bytes]:
        """
        Read response from device, handling Report ID if present.

        Args:
            unit: The laser unit to read from
            expected_length: Expected response length (excluding Report ID)
            timeout: Read timeout in seconds

        Returns:
            Response bytes (without Report ID) or None if error/timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Read full HID report (64 bytes is common for many HID devices)
            data = unit.device_handle.read(64)
            if data:
                if debug:
                    # Show all received bytes for debugging
                    log.debug(f"Received ({len(data)} bytes): {' '.join(f'0x{b:02x}' for b in data)}")

                # The device always sends Report ID 0 as first byte
                # Check if first byte is 0x00 (Report ID)
                if len(data) > 0 and data[0] == 0x00:
                    # Skip the Report ID and return the actual data
                    actual_data = data[1 : expected_length + 1]
                    if debug:
                        log.debug(f"Actual response: {' '.join(f'0x{b:02x}' for b in actual_data)}")
                    return bytes(actual_data)
                else:
                    # No Report ID or unexpected format
                    actual_data = data[:expected_length]
                    if debug:
                        log.debug(f"Response (no Report ID): {' '.join(f'0x{b:02x}' for b in actual_data)}")
                    return bytes(actual_data)
            time.sleep(0.001)

        log.error("Timeout waiting for response")
        return None

    def _set_lines_to_off(self, unit_id: str) -> bool:
        """
        Set all lines in a laser unit to off.

        Args:
            unit_id: ID of the laser unit

        Returns:
            True if all lines were set to off
        """
        if unit_id not in self.units:
            return False

        unit = self.units[unit_id]
        if not unit.device_handle:
            return False

        # Send initialize command: 0x0100
        command = struct.pack(">BB", LaserCommands.SET_SHUTTER_STATE, 0x00)

        # Send command
        if not AndorLaser._send_command(unit, command):
            return False

        # Read response
        response = AndorLaser._read_response(unit, 1)
        if response:
            if response[0] == LaserCommands.SET_SHUTTER_STATE:
                return True
            elif response[0] == LaserCommands.ERROR_RESPONSE:
                log.error(f"Error response during initialization of unit {unit_id}")
                return False

        log.error(f"Timeout waiting for initialization response from unit {unit_id}")
        return False

    def _get_laser_line_setup(self, unit_id: str) -> Dict[int, int]:
        """
        Get laser line setup from a unit.

        Returns a mapping of line number to wavelength in nm.
        Lines with wavelength < 100 nm are control lines and are excluded.

        Args:
            unit_id: ID of the laser unit

        Returns:
            Dictionary mapping line number to wavelength (nm)
        """
        if unit_id not in self.units:
            raise ValueError(f"Unit {unit_id} not found")

        unit = self.units[unit_id]
        if not unit.device_handle:
            raise RuntimeError(f"Unit {unit_id} is not connected")

        # Send GET_LASER_LINE_SETUP command: 0x08
        command = struct.pack(">B", LaserCommands.GET_LASER_LINE_SETUP)

        if not AndorLaser._send_command(unit, command):
            raise RuntimeError(f"Failed to send get laser line setup command to unit {unit_id}")

        # Read response - minimum 13 bytes (1 command + 6 pairs), maximum 17 bytes (1 command + 8 pairs)
        response = AndorLaser._read_response(unit, 17, timeout=1.0)
        if not response or len(response) < 13:
            raise RuntimeError(f"Invalid or timeout response from unit {unit_id}")

        if response[0] != LaserCommands.GET_LASER_LINE_SETUP:
            if response[0] == LaserCommands.ERROR_RESPONSE:
                raise RuntimeError(f"Error response from unit {unit_id} for get laser line setup")
            else:
                raise RuntimeError(f"Unexpected response command 0x{response[0]:02x} from unit {unit_id}")

        # Parse wavelength pairs (HH LL format)
        line_to_wavelength = {}
        line_number = 0

        # Start from byte 1 (skip command byte), read pairs until we hit a zero or run out of data
        for i in range(1, len(response), 2):
            if i + 1 >= len(response):
                break

            # Extract wavelength (big-endian): HH LL -> wavelength in nm * 10
            wavelength_raw = (response[i] << 8) | response[i + 1]

            if wavelength_raw == 0:
                line_number += 1
                continue

            # Convert from (nm * 10) to nm
            wavelength = wavelength_raw // 10

            # Skip control lines (wavelength < 100 nm)
            if wavelength >= 100:
                line_to_wavelength[line_number] = wavelength

            line_number += 1

        if self.debug:
            log.debug(f"Unit {unit_id} line setup: {line_to_wavelength}")

        return line_to_wavelength

    def initialize(self):
        """
        Initialize laser units by discovering all devices with matching VID/PID
        and querying their line configurations.
        """

        # Find all devices with the specified VID/PID
        serial_numbers = AndorLaser._find_laser_devices(self.vid, self.pid)

        if not serial_numbers:
            raise RuntimeError(f"No laser devices found with VID:PID {self.vid:04x}:{self.pid:04x}")

        log.info(f"Found {len(serial_numbers)} laser units: {serial_numbers}")

        # Add each unit and connect to query line setup
        for serial_number in serial_numbers:
            # Use serial number as unit ID since it's unique
            unit_id = serial_number

            # Add unit with empty line_to_wavelength (will be populated after connection)
            self._add_unit(unit_id, self.vid, self.pid, serial_number, {})

        # Connect to all units
        connection_results = self._connect()

        # Query line setup for each connected unit
        for unit_id, connected in connection_results.items():
            if not connected:
                log.error(f"Failed to connect to unit {unit_id}")
                raise RuntimeError(f"Failed to connect to unit {unit_id}")

            try:
                # Query and populate line setup
                line_to_wavelength = self._get_laser_line_setup(unit_id)
                self.units[unit_id].line_to_wavelength = line_to_wavelength
                log.info(f"Unit {unit_id} configured with lines: {line_to_wavelength}")
            except Exception as e:
                log.error(f"Failed to get line setup for unit {unit_id}: {e}")
                raise RuntimeError(f"Failed to get line setup for unit {unit_id}")

        # Build channel mappings from all available lines
        # Define channel groups - channels in same group map to same laser line
        channel_groups = [
            [405],  # Group 1
            [470, 488],  # Group 2
            [545, 550, 555, 561],  # Group 3
            [638, 640],  # Group 4
            [730, 735, 750],  # Group 5
        ]

        # Initialize all channels as unmapped
        self.channel_mappings = {wl: None for group in channel_groups for wl in group}

        # Map available lines to channel groups
        for unit_id, unit in self.units.items():
            if not unit.device_handle:
                continue

            for line, wavelength in unit.line_to_wavelength.items():
                # Find the group containing this wavelength
                group = next((g for g in channel_groups if wavelength in g), None)

                if group:
                    # Map all channels in this group to the same line
                    for wl in group:
                        self.channel_mappings[wl] = (unit_id, line)
                    log.info(f"Mapped channels {group} to unit {unit_id}, line {line}")
                else:
                    log.error(f"Wavelength {wavelength}nm from unit {unit_id} not in supported channels")

        # Show final channel mapping
        active_channels = {ch: mapping for ch, mapping in self.channel_mappings.items() if mapping is not None}
        log.info(f"Active channels: {active_channels}")

        # Initialize all connected units (set all lines to off)
        for unit_id in self.units:
            if self.units[unit_id].device_handle:
                if not self._set_lines_to_off(unit_id):
                    raise RuntimeError(f"Failed to initialize shutter state for unit {unit_id}")

    def set_intensity(self, channel: Tuple[int, int], intensity: float) -> bool:
        """
        Set laser intensity for a specific line.

        Args:
            channel: tuple of (unit_id, line)
            intensity: Intensity percentage (0.0 - 100.0)

        Returns:
            True if command was successful
        """
        unit_id, line = channel

        unit = self.units[unit_id]
        if not unit.device_handle:
            raise RuntimeError(f"Unit {unit_id} is not connected")

        if intensity < 0.0 or intensity > 100.0:
            raise ValueError(f"Invalid intensity {intensity}. Must be 0.0-100.0")

        # Convert percentage to transmission value (0-1000)
        transmission = int(intensity * 10)

        # Build command: 0x04 + line_number + transmission_high + transmission_low
        command = struct.pack(">BBH", LaserCommands.SET_TRANSMISSION, line, transmission)

        # Send command
        if not AndorLaser._send_command(unit, command):
            log.error(f"Failed to send command to unit {unit_id}")
            raise RuntimeError(f"Failed to send command to unit {unit_id}")

        # Read response
        response = AndorLaser._read_response(unit, 1)
        if response:
            if response[0] == LaserCommands.SET_TRANSMISSION:
                return True
            elif response[0] == LaserCommands.ERROR_RESPONSE:
                log.error(f"Error response from unit {unit_id}")
                raise RuntimeError(f"Error response from unit {unit_id}")

        log.error(f"Timeout or invalid response from unit {unit_id}")
        raise RuntimeError(f"Timeout or invalid response from unit {unit_id}")

    def get_intensity(self, channel: Tuple[int, int]) -> float:
        """
        Read current laser intensity for a specific line.

        Args:
            channel: tuple of (unit_id, line)

        Returns:
            Intensity percentage (0.0 - 100.0) or None if error
        """
        unit_id, line = channel

        unit = self.units[unit_id]
        if not unit.device_handle:
            raise RuntimeError(f"Unit {unit_id} is not connected")

        # Build command: 0x05 + line_number
        command = struct.pack(">BB", LaserCommands.READ_TRANSMISSION, line)

        # Send command
        if not AndorLaser._send_command(unit, command):
            raise RuntimeError(f"Failed to send command to unit {unit_id}")

        # Read response (3 bytes: command + 2 data bytes)
        response = AndorLaser._read_response(unit, 3)
        if response and len(response) >= 3:
            # Note: Response should echo the command (0x05), not 0x04
            if response[0] == LaserCommands.READ_TRANSMISSION:
                # Extract transmission value (big-endian)
                transmission = (response[1] << 8) | response[2]
                # Convert to percentage
                return transmission / 10.0
            elif response[0] == LaserCommands.ERROR_RESPONSE:
                log.error(f"Error response from unit {unit_id}")
                raise RuntimeError(f"Error response from unit {unit_id}")

        log.error(f"Timeout or invalid response from unit {unit_id}")
        raise RuntimeError(f"Timeout or invalid response from unit {unit_id}")

    def set_intensity_control_mode(self, mode: IntensityControlMode):
        if mode != IntensityControlMode.Software:
            raise NotImplementedError("Changing intensity control mode is not supported for Andor laser units")

    def get_intensity_control_mode(self) -> IntensityControlMode:
        return self.intensity_control_mode

    def set_shutter_control_mode(self, mode: ShutterControlMode):
        if mode != ShutterControlMode.TTL:
            raise NotImplementedError("Changing shutter control mode is not supported for Andor laser units")

    def get_shutter_control_mode(self) -> ShutterControlMode:
        return self.shutter_control_mode

    def set_shutter_state(self, channel: Tuple[int, int], state: bool):
        """
        We will use TTL to control on/off so we don't need to do anything here.
        """
        raise NotImplementedError("Setting shutter state in software is not supported for Andor laser units")

    def get_shutter_state(self, channel: Tuple[int, int]) -> bool:
        """
        Get the shutter state for a specific channel.

        Args:
            channel: tuple of (unit_id, line)

        Returns:
            True if the shutter is open, False if it is closed
        """
        unit_id, line = channel

        unit = self.units[unit_id]
        if not unit.device_handle:
            raise RuntimeError(f"Unit {unit_id} is not connected")

        # Send READ_SHUTTER_STATE command: 0x02
        command = struct.pack(">B", LaserCommands.READ_SHUTTER_STATE)

        # Send command
        if not AndorLaser._send_command(unit, command):
            raise RuntimeError(f"Failed to send read shutter state command to unit {unit_id}")

        # Read response (2 bytes: command + status byte)
        response = AndorLaser._read_response(unit, 2)
        if not response or len(response) < 2:
            raise RuntimeError(f"Invalid or timeout response from unit {unit_id}")

        if response[0] != LaserCommands.READ_SHUTTER_STATE:
            if response[0] == LaserCommands.ERROR_RESPONSE:
                raise RuntimeError(f"Error response from unit {unit_id} for read shutter state")
            else:
                raise RuntimeError(f"Unexpected response command 0x{response[0]:02x} from unit {unit_id}")

        # Extract status byte - each bit represents a line position
        status_byte = response[1]

        # Check the bit for this specific line (line numbers are 0-based)
        line_bit = (status_byte >> line) & 0x01

        if self.debug:
            log.debug(f"Unit {unit_id} shutter status byte: 0x{status_byte:02x}, line {line} state: {bool(line_bit)}")

        return bool(line_bit)

    def shut_down(self):
        for unit_id in self.units:
            for wavelength in self.units[unit_id].line_to_wavelength.values():
                self.set_intensity(wavelength, 0.0)

        self._disconnect()
