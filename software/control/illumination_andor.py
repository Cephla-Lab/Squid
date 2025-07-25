import hid
import struct
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import IntEnum


class LaserCommands(IntEnum):
    """Command codes for laser control protocol"""

    SET_SHUTTER_STATE = 0x01
    SET_TRANSMISSION = 0x04
    READ_TRANSMISSION = 0x05
    ERROR_RESPONSE = 0xFF


@dataclass
class LaserUnit:
    """Represents a single laser unit device"""

    vendor_id: int
    product_id: int
    serial_number: str  # Required to distinguish between units
    device_handle: Optional[hid.device] = None
    line_to_wavelength: Dict[int, int] = {}  # {line_number: wavelength}


class AndorLaserController:
    """
    Controller class for managing Andor HLE laser units connected via USB HID.

    Supports multiple units, each with multiple laser lines.
    Units are distinguished by serial number (required when VID/PID are identical).
    Intensity (transmission) values range from 0-1000 (0.0% - 100.0%).

    We use TTL to control on/off. The on/off state is OR with what is set via the computer,
    so all laser lines are set to off (0 intensity) on initialization.
    """

    def __init__(self, debug: bool = False):
        """
        Initialize the laser controller

        Args:
            debug: Enable debug output for HID communications
        """
        self.units: Dict[str, LaserUnit] = {}
        self.connected = False
        self.debug = debug

    def list_devices(self) -> List[Dict]:
        """
        List all available HID devices.

        Returns:
            List of device information dictionaries
        """
        devices = []
        for device in hid.enumerate():
            devices.append(
                {
                    "vendor_id": device["vendor_id"],
                    "product_id": device["product_id"],
                    "serial_number": device["serial_number"] or "N/A",
                    "manufacturer": device["manufacturer_string"],
                    "product": device["product_string"],
                    "path": device["path"],
                }
            )
        return devices

    def find_laser_devices(self, vendor_id: int, product_id: int) -> List[str]:
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

    def add_unit(
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

    def connect(self) -> Dict[str, bool]:
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
                if self._initialize_unit(unit_id):
                    results[unit_id] = True
                else:
                    print(f"Warning: Unit {unit_id} connected but initialization failed")
                    results[unit_id] = True  # Still mark as connected

            except Exception as e:
                print(f"Failed to connect to unit {unit_id}: {e}")
                results[unit_id] = False

        self.connected = any(results.values())
        return results

    def disconnect(self):
        """Disconnect from all laser units"""
        for unit in self.units.values():
            if unit.device_handle:
                try:
                    unit.device_handle.close()
                except:
                    pass
                unit.device_handle = None
        self.connected = False

    def _send_command(self, unit: LaserUnit, command: bytes) -> bool:
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

        if self.debug:
            print(f"Sending: {' '.join(f'0x{b:02x}' for b in command)}")

        try:
            unit.device_handle.write(command)
            return True
        except Exception as e:
            print(f"Error sending command: {e}")
            return False

    def _read_response(self, unit: LaserUnit, expected_length: int, timeout: float = 0.5) -> Optional[bytes]:
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
                if self.debug:
                    # Show all received bytes for debugging
                    print(f"Received ({len(data)} bytes): {' '.join(f'0x{b:02x}' for b in data)}")

                # The device always sends Report ID 0 as first byte
                # Check if first byte is 0x00 (Report ID)
                if len(data) > 0 and data[0] == 0x00:
                    # Skip the Report ID and return the actual data
                    actual_data = data[1 : expected_length + 1]
                    if self.debug:
                        print(f"Actual response: {' '.join(f'0x{b:02x}' for b in actual_data)}")
                    return bytes(actual_data)
                else:
                    # No Report ID or unexpected format
                    actual_data = data[:expected_length]
                    if self.debug:
                        print(f"Response (no Report ID): {' '.join(f'0x{b:02x}' for b in actual_data)}")
                    return bytes(actual_data)
            time.sleep(0.001)

        if self.debug:
            print(f"Timeout waiting for response")
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
        if not self._send_command(unit, command):
            return False

        # Read response
        response = self._read_response(unit, 1)
        if response:
            if response[0] == LaserCommands.SET_SHUTTER_STATE:
                return True
            elif response[0] == LaserCommands.ERROR_RESPONSE:
                print(f"Error response during initialization of unit {unit_id}")
                return False

        print(f"Timeout waiting for initialization response from unit {unit_id}")
        return False

    def initialize_unit(self, unit_id: str) -> bool:
        """
        Manually initialize a laser unit by setting all lines to off.

        Args:
            unit_id: ID of the laser unit

        Returns:
            True if initialization was successful
        """
        return self._set_lines_to_off(unit_id)

    def initialize_all_units(self) -> Dict[str, bool]:
        """
        Initialize all connected units by setting all lines to off.

        Returns:
            Dictionary mapping unit_id to initialization success status
        """
        results = {}
        for unit_id in self.units:
            results[unit_id] = self._set_lines_to_off(unit_id)
        return results

    def set_intensity(self, unit_id: str, line: int, intensity: float) -> bool:
        """
        Set laser intensity for a specific line.

        Args:
            unit_id: ID of the laser unit
            line: Zero-based line number
            intensity: Intensity percentage (0.0 - 100.0)

        Returns:
            True if command was successful
        """
        if unit_id not in self.units:
            raise ValueError(f"Unknown unit ID: {unit_id}")

        unit = self.units[unit_id]
        if not unit.device_handle:
            raise RuntimeError(f"Unit {unit_id} is not connected")

        if line < 0 or line >= unit.num_lines:
            raise ValueError(f"Invalid line number {line}. Must be 0-{unit.num_lines-1}")

        if intensity < 0.0 or intensity > 100.0:
            raise ValueError(f"Invalid intensity {intensity}. Must be 0.0-100.0")

        # Convert percentage to transmission value (0-1000)
        transmission = int(intensity * 10)

        # Build command: 0x04 + line_number + transmission_high + transmission_low
        command = struct.pack(">BBH", LaserCommands.SET_TRANSMISSION, line, transmission)

        # Send command
        if not self._send_command(unit, command):
            return False

        # Read response
        response = self._read_response(unit, 1)
        if response:
            if response[0] == LaserCommands.SET_TRANSMISSION:
                return True
            elif response[0] == LaserCommands.ERROR_RESPONSE:
                print(f"Error response from unit {unit_id}")
                return False

        print(f"Timeout or invalid response from unit {unit_id}")
        return False

    def get_intensity(self, unit_id: str, line: int) -> Optional[float]:
        """
        Read current laser intensity for a specific line.

        Args:
            unit_id: ID of the laser unit
            line: Zero-based line number

        Returns:
            Intensity percentage (0.0 - 100.0) or None if error
        """
        if unit_id not in self.units:
            raise ValueError(f"Unknown unit ID: {unit_id}")

        unit = self.units[unit_id]
        if not unit.device_handle:
            raise RuntimeError(f"Unit {unit_id} is not connected")

        if line < 0 or line >= unit.num_lines:
            raise ValueError(f"Invalid line number {line}. Must be 0-{unit.num_lines-1}")

        # Build command: 0x05 + line_number
        command = struct.pack(">BB", LaserCommands.READ_TRANSMISSION, line)

        # Send command
        if not self._send_command(unit, command):
            return None

        # Read response (3 bytes: command + 2 data bytes)
        response = self._read_response(unit, 3)
        if response and len(response) >= 3:
            # Note: Response should echo the command (0x05), not 0x04
            if response[0] == LaserCommands.READ_TRANSMISSION:
                # Extract transmission value (big-endian)
                transmission = (response[1] << 8) | response[2]
                # Convert to percentage
                return transmission / 10.0
            elif response[0] == LaserCommands.ERROR_RESPONSE:
                print(f"Error response from unit {unit_id}")
                return None

        print(f"Timeout or invalid response from unit {unit_id}")
        return None

    def set_all_intensities(self, unit_id: str, intensity: float) -> Dict[int, bool]:
        """
        Set all laser lines to the same intensity.

        Args:
            unit_id: ID of the laser unit
            intensity: Intensity percentage (0.0 - 100.0)

        Returns:
            Dictionary mapping line number to success status
        """
        if unit_id not in self.units:
            raise ValueError(f"Unknown unit ID: {unit_id}")

        unit = self.units[unit_id]
        results = {}

        for line in range(unit.num_lines):
            results[line] = self.set_intensity(unit_id, line, intensity)

        return results

    def get_all_intensities(self, unit_id: str) -> Dict[int, Optional[float]]:
        """
        Read intensities for all laser lines.

        Args:
            unit_id: ID of the laser unit

        Returns:
            Dictionary mapping line number to intensity percentage
        """
        if unit_id not in self.units:
            raise ValueError(f"Unknown unit ID: {unit_id}")

        unit = self.units[unit_id]
        intensities = {}

        for line in range(unit.num_lines):
            intensities[line] = self.get_intensity(unit_id, line)

        return intensities
