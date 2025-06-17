from laser_sdk import LaserSDK

from squid.abc import LightSource
from control.lighting import ShutterControlMode, IntensityControlMode

import squid.logging


class VersaLase(LightSource):
    def __init__(self, **kwds):
        self._log = squid.logging.get_logger(__name__)

        self.sdk = LaserSDK()
        self.sdk.discover()

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
            self._log.error(f"Failed to initialize Vortran laser: {e}")

    def initialize(self) -> bool:
        """
        Initialize the connection and settings for the Vortran laser.
        Returns True if successful, False otherwise.
        """
        try:
            # Query information about installed lasers
            for laser in self.sdk.get_lasers():
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

    def set_intensity_control_mode(self, mode: IntensityControlMode):
        """
        Set intensity control mode. Only software intensity control is supported for Vortran laser.

        Args:
            mode: IntensityControlMode.Software or IntensityControlMode.External
        """
        self._log.debug("Only software intensity control is supported for Vortran laser")
        pass

    def get_intensity_control_mode(self) -> IntensityControlMode:
        """
        Get current intensity control mode. Only software intensity control is supported for Vortran laser.

        Returns:
            IntensityControlMode enum value
        """
        return IntensityControlMode.Software

    def set_shutter_control_mode(self, mode: ShutterControlMode):
        """
        Set shutter control mode for all lasers.

        Args:
            mode: ShutterControlMode enum
        """
        for laser in self.sdk.get_lasers():
            laser.set_digital_mode(mode == ShutterControlMode.TTL)

        self.shutter_mode = mode

    def get_shutter_control_mode(self) -> ShutterControlMode:
        """
        Get current shutter control mode.

        Returns:
            ShutterControlMode enum value
        """
        # The lasers in the VersaLase may have different shutter control states.
        # We call set_shutter_control_mode() on initialize so they should all be the same.
        # Raise an error here if they are not.
        digital_mode = None
        for laser in self.sdk.get_lasers():
            if digital_mode is None:
                digital_mode = laser.digital_mode
            elif digital_mode != laser.digital_mode:
                raise ValueError("Laser shutter control modes are not consistent")
        if digital_mode is None:
            raise ValueError("No lasers found")

        return ShutterControlMode.TTL if digital_mode else ShutterControlMode.Software

    def set_shutter_state(self, channel: int, state: bool):
        """
        Turn a specific channel on or off.

        Args:
            channel: Channel ID (letter or wavelength)
            state: True to turn on, False to turn off
        """
        laser = self.sdk.get_laser_by_id(self.channel_mappings[channel])
        laser.enable(state)

    def get_shutter_state(self, channel: int) -> bool:
        """
        Get the current shutter state of a specific channel.

        Args:
            channel: Channel ID (letter or wavelength)

        Returns:
            bool: True if channel is on, False if off
        """
        laser = self.sdk.get_laser_by_id(self.channel_mappings[channel])
        return laser.get_emission_status()

    def set_intensity(self, channel: int, intensity: float):
        """
        Set the intensity for a specific channel.

        Args:
            channel: Channel ID (letter or wavelength)
            intensity: Intensity value (0-100 percent)
        """
        laser = self.sdk.get_laser_by_id(self.channel_mappings[channel])
        laser.set_power(laser.max_power * intensity / 100.0)

    def get_intensity(self, channel: int) -> float:
        """
        Get the current intensity of a specific channel.

        Args:
            channel: Channel ID (letter or wavelength)

        Returns:
            float: Current intensity value (0-100 percent)
        """
        # For Vortran laser, we are able to get the actual intensity of the lasers.
        # To keep consistency with other light sources, we return the set power/intensity here.
        laser = self.sdk.get_laser_by_id(self.channel_mappings[channel])
        laser_info = laser.get_op2()
        return laser_info["LaserSetPower"] / laser.max_power * 100.0

    def shut_down(self):
        """Safely shut down the Vortran laser."""
        for laser in self.sdk.get_lasers():
            laser.disable()
            laser.disconnect()
