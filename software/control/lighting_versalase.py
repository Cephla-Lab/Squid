"""
https://github.com/jmtayloruk/scripts/blob/main/versalase-usb-comms-demonstration.ipynb
"""

import usb.core
import sys, time

from squid.abc import LightSource
from control.lighting import ShutterControlMode, IntensityControlMode

import squid.logging


class VersaLase(LightSource):
    """
    Controls a Stradus VersaLase laser system via USB.

    The VersaLase can control up to 4 laser channels (a, b, c, d) with
    individual power and shutter control for each channel.
    """

    def __init__(self, vendor_id=0x201A, product_id=0x0003, **kwds):
        """
        Initialize the VersaLase controller and establish USB communication.

        Args:
            vendor_id: USB vendor ID (default: 0x201a)
            product_id: USB product ID (default: 0x0003)
        """
        self._log = squid.logging.get_logger(__name__)

        self.vendor_id = vendor_id
        self.product_id = product_id
        self.dev = None
        self.live = False
        self.laser_channels = ["a", "b", "c", "d"]
        self.active_channels = {}
        self.channel_info = {}
        self.intensity_mode = IntensityControlMode.Software
        self.shutter_mode = ShutterControlMode.Software

        # Channel mapping for common wavelengths
        self.wavelength_to_channel = {405: "d", 488: "c", 490: "c", 561: "b", 640: "a", 638: "a"}

        try:
            self.initialize()
        except Exception as e:
            self._log.error(f"Failed to initialize VersaLase: {e}")

    def initialize(self):
        """
        Initialize the connection and settings for the VersaLase.
        Returns True if successful, False otherwise.
        """
        try:
            # Find and connect to the device
            self.dev = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)
            if self.dev is None:
                raise ValueError("VersaLase device not found")

            self._log.info("Connected to VersaLase")

            # Query information about installed lasers
            for channel in self.laser_channels:
                laser_info = self._send_command(f"{channel}.?li")
                if laser_info is not None:
                    # This laser is installed
                    self.active_channels[channel] = True
                    self.channel_info[channel] = {
                        "info": laser_info,
                        "wavelength": self._parse_float_query(f"{channel}.?lw"),
                        "max_power": self._parse_float_query(f"{channel}.?maxp"),
                        "rated_power": self._parse_float_query(f"{channel}.?rp"),
                    }
                    self._log.info(f"Found laser {channel}: {laser_info}")

                    # Initialize laser to safe state
                    self._send_command(f"{channel}.le=0")  # Turn off
                    self._send_command(f"{channel}.epc=0")  # Disable external power control
                    self._send_command(f"{channel}.lp=0")  # Set power to 0
                else:
                    self.active_channels[channel] = False

            self.live = True
            return True

        except Exception as e:
            self._log.error(f"Initialization failed: {e}")
            self.live = False
            return False

    def _get_a1_response(self, timeout=2.0, min_length=2, timeout_acceptable=False):
        """Read response from A1 control transfer."""
        t0 = time.time()
        did_find = None
        first_seen = None

        while time.time() < t0 + timeout:
            result = self.dev.ctrl_transfer(0xC0, 0xA1, 0x0000, 0, 256)
            sret = "".join([chr(x) for x in result])
            if len(sret) > min_length:
                return sret
            elif len(sret) > 0:
                if first_seen is None:
                    first_seen = time.time() - t0
                did_find = sret
            time.sleep(0.01)

        if timeout_acceptable:
            return ""

        self._log.debug("Read timed out")
        if did_find is not None:
            self._log.debug(f"Did see '{did_find}' after {first_seen:.3f}s")
        raise TimeoutError("A1 response timeout")

    def _send_a0_text_command(self, cmd):
        """Send text command via A0 control transfer."""
        self.dev.ctrl_transfer(0x40, 0xA0, 0x0000, 0, cmd + "\r")

    def _get_a2(self):
        """Read status from A2 control transfer."""
        result = self.dev.ctrl_transfer(0xC0, 0xA2, 0x0000, 0, 1)
        return result[0] if len(result) == 1 else 0

    def _send_a3(self):
        """Send acknowledgment via A3 control transfer."""
        self.dev.ctrl_transfer(0x40, 0xA3, 0x0000, 0, 0)

    def _send_command(self, cmd, log_level=0):
        """
        Send a text command to the laser and receive the response.

        Args:
            cmd: Command string to send
            log_level: Logging verbosity (0=quiet, 1=normal, 2=verbose)

        Returns:
            Response string or None
        """
        if not self.live:
            return None

        result = None

        try:
            # Send command
            self._send_a0_text_command(cmd)

            # Initial A1 query (may be empty)
            resp = self._get_a1_response(min_length=0, timeout=0.5, timeout_acceptable=True)

            # Wait for response to be available
            t0 = time.time()
            initially_zero = False
            while self._get_a2() == 0:
                initially_zero = True
                if time.time() > t0 + 5:
                    self._log.debug("A2 never returned 1")
                    break

            # Read all available responses
            while self._get_a2() == 1:
                resp = self._get_a1_response(min_length=0)[2:]  # Skip \r\n
                if resp and resp != "Stradus> ":
                    result = resp
                self._send_a3()  # Acknowledge

            if log_level >= 1:
                self._log.info(f"Sent {cmd}, got response '{result}'")

            return result

        except Exception as e:
            self._log.error(f"Command failed: {cmd}, error: {e}")
            return None

    def _parse_query(self, cmd):
        """Send query command and parse response."""
        response = self._send_command(cmd)
        if response:
            return response[len(cmd) + 1 :]
        return None

    def _parse_float_query(self, cmd):
        """Send query command and parse response as float."""
        result = self._parse_query(cmd)
        return float(result) if result else 0.0

    def _parse_int_query(self, cmd):
        """Send query command and parse response as int."""
        result = self._parse_query(cmd)
        return int(result) if result else 0

    def _get_channel_for_wavelength(self, wavelength):
        """Map wavelength to channel if using wavelength-based addressing."""
        if isinstance(wavelength, (int, float)):
            return self.wavelength_to_channel.get(int(wavelength))
        return wavelength  # Assume it's already a channel letter

    def set_intensity_control_mode(self, mode):
        """
        Set intensity control mode.

        Args:
            mode: IntensityControlMode.Software or IntensityControlMode.External
        """
        self.intensity_mode = mode
        epc_value = 1 if mode == IntensityControlMode.External else 0

        for channel in self.active_channels:
            if self.active_channels[channel]:
                self._send_command(f"{channel}.epc={epc_value}")

    def get_intensity_control_mode(self):
        """
        Get current intensity control mode.

        Returns:
            IntensityControlMode enum value
        """
        return self.intensity_mode

    def set_shutter_control_mode(self, mode):
        """
        Set shutter control mode.

        Args:
            mode: ShutterControlMode enum
        """
        self.shutter_mode = mode
        # VersaLase doesn't have explicit TTL shutter control in the protocol shown
        # This would need to be implemented if the hardware supports it

    def get_shutter_control_mode(self):
        """
        Get current shutter control mode.

        Returns:
            ShutterControlMode enum value
        """
        return self.shutter_mode

    def set_shutter_state(self, channel, state):
        """
        Turn a specific channel on or off.

        Args:
            channel: Channel ID (letter or wavelength)
            state: True to turn on, False to turn off
        """
        channel = self._get_channel_for_wavelength(channel)
        if channel and channel in self.active_channels and self.active_channels[channel]:
            le_value = 1 if state else 0
            response = self._send_command(f"{channel}.le={le_value}")
            if response:
                self._log.info(f"Set channel {channel} shutter to {state}")

    def get_shutter_state(self, channel):
        """
        Get the current shutter state of a specific channel.

        Args:
            channel: Channel ID (letter or wavelength)

        Returns:
            bool: True if channel is on, False if off
        """
        channel = self._get_channel_for_wavelength(channel)
        if channel and channel in self.active_channels and self.active_channels[channel]:
            return self._parse_int_query(f"{channel}.?le") == 1
        return False

    def set_intensity(self, channel, intensity):
        """
        Set the intensity for a specific channel.

        Args:
            channel: Channel ID (letter or wavelength)
            intensity: Intensity value (0-100 percent)
        """
        channel = self._get_channel_for_wavelength(channel)
        if channel and channel in self.active_channels and self.active_channels[channel]:
            # Convert percentage to power in mW
            max_power = self.channel_info[channel]["max_power"]
            power_mw = (intensity / 100.0) * max_power

            response = self._send_command(f"{channel}.lp={power_mw:.2f}")
            if response:
                self._log.info(f"Set channel {channel} intensity to {intensity}% ({power_mw:.2f}mW)")

    def get_intensity(self, channel):
        """
        Get the current intensity of a specific channel.

        Args:
            channel: Channel ID (letter or wavelength)

        Returns:
            float: Current intensity value (0-100 percent)
        """
        channel = self._get_channel_for_wavelength(channel)
        if channel and channel in self.active_channels and self.active_channels[channel]:
            # Get the set power (lps) rather than measured power (lp)
            # as measured power might be 0 if shutter is closed
            power_mw = self._parse_float_query(f"{channel}.?lps")
            max_power = self.channel_info[channel]["max_power"]
            if max_power > 0:
                return (power_mw / max_power) * 100.0
        return 0.0

    def shut_down(self):
        """Safely shut down the VersaLase."""
        if self.live:
            self._log.info("Shutting down VersaLase")
            for channel in self.active_channels:
                if self.active_channels[channel]:
                    self.set_intensity(channel, 0)
                    self.set_shutter_state(channel, False)
            self.live = False

    def get_status(self):
        """
        Get the status of the VersaLase.

        Returns:
            bool: True if connected and operational
        """
        return self.live

    def get_channel_info(self):
        """
        Get information about all active channels.

        Returns:
            dict: Channel information including wavelength and power limits
        """
        return self.channel_info
