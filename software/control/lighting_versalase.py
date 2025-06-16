import time

from laser_sdk import LaserSDK

from squid.abc import LightSource
from control.lighting import ShutterControlMode, IntensityControlMode

import squid.logging


class VersaLase(LightSource):
    def __init__(self, **kwds):
        self._log = squid.logging.get_logger(__name__)

        self.sdk = LaserSDK()
        self.sdk.discover()

        self.intensity_mode = IntensityControlMode.Software
        self.shutter_mode = ShutterControlMode.Software

        self.channel_mappings = {
            405: None,
            470: None,
            488: None,
            545: None,
            550: None,
            555: None,
            561: None,
            638: None,
            640: None,
            730: None,
            735: None,
            750: None,
        }

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
            # Query information about installed lasers
            for laser in self.sdk.get_lasers():
                self.wavelength_to_laser[laser.wavelength] = laser
                self._log.info(f"Found laser {laser.wavelength}: {laser.max_power}")
                laser.disable()
                if laser.wavelength == 405:
                    self.channel_mappings[405] = laser.id
                elif laser.wavelength == 488:
                    self.channel_mappings[470] = laser.id
                    self.channel_mappings[488] = laser.id
                elif laser.wavelength == 545:
                    self.channel_mappings[545] = laser.id
                    self.channel_mappings[550] = laser.id
                    self.channel_mappings[555] = laser.id
                    self.channel_mappings[561] = laser.id
                elif laser.wavelength == 638:
                    self.channel_mappings[638] = laser.id
                    self.channel_mappings[640] = laser.id
            return True

        except Exception as e:
            self._log.error(f"Initialization failed: {e}")
            return False

    def set_intensity_control_mode(self, mode):
        """
        Set intensity control mode.

        Args:
            mode: IntensityControlMode.Software or IntensityControlMode.External
        """
        raise NotImplementedError("Only software intensity control is supported for VersaLase")

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
        for laser in self.sdk.get_lasers():
            laser.set_digital_mode(mode == ShutterControlMode.TTL)

        self.shutter_mode = mode

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
