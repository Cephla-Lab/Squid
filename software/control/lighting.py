from enum import Enum
from pathlib import Path

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import squid.logging
from control.models.illumination_config import IlluminationChannel
from squid.intensity_calibration import (
    CALIBRATIONS_DIR_NAME,
    Calibration,
    CalibrationFileError,
    load_calibration,
    resolve_calibration_path,
)

from control.microcontroller import Microcontroller
from control.core.config import ConfigRepository
from control._def import ILLUMINATION_CODE

# Number of illumination ports supported (matches firmware)
NUM_ILLUMINATION_PORTS = 16

# Default wavelength -> source code mappings for TTL control, used when no
# illumination channel config is available. Multiple wavelengths can map to
# the same port (e.g., 470nm and 488nm both to D2).
_DEFAULT_CHANNEL_MAPPINGS_TTL = {
    405: ILLUMINATION_CODE.ILLUMINATION_D1,
    470: ILLUMINATION_CODE.ILLUMINATION_D2,
    488: ILLUMINATION_CODE.ILLUMINATION_D2,
    545: ILLUMINATION_CODE.ILLUMINATION_D3,
    550: ILLUMINATION_CODE.ILLUMINATION_D3,
    555: ILLUMINATION_CODE.ILLUMINATION_D3,
    561: ILLUMINATION_CODE.ILLUMINATION_D3,
    638: ILLUMINATION_CODE.ILLUMINATION_D4,
    640: ILLUMINATION_CODE.ILLUMINATION_D4,
    730: ILLUMINATION_CODE.ILLUMINATION_D5,
    735: ILLUMINATION_CODE.ILLUMINATION_D5,
    750: ILLUMINATION_CODE.ILLUMINATION_D5,
}


class LightSourceType(Enum):
    SquidLED = 0
    SquidLaser = 1
    LDI = 2
    CELESTA = 3
    VersaLase = 4
    SCI = 5
    AndorLaser = 6


class IntensityControlMode(Enum):
    SquidControllerDAC = 0
    Software = 1


class ShutterControlMode(Enum):
    TTL = 0
    Software = 1


@dataclass(frozen=True)
class CalibrationLookup:
    """Which calibration applies to a wavelength and what loading it gave (design §6.1)."""

    file_name: Optional[str] = None
    calibration: Optional[Calibration] = None
    error: Optional[str] = None


_NO_CALIBRATION = CalibrationLookup()


class IlluminationController:
    """Controls microscope illumination (LEDs, lasers, LED matrix).

    Two API styles are available:

    **Legacy API (single-channel, any firmware):**
        Use when only ONE illumination source is needed at a time.
        This is the standard acquisition workflow.

        - set_intensity(wavelength, intensity) - Set intensity by wavelength
        - turn_on_illumination() / turn_off_illumination() - Control current source

        Example:
            controller.set_intensity(488, 50)  # 488nm at 50%
            controller.turn_on_illumination()
            # ... acquire image ...
            controller.turn_off_illumination()

    **Multi-port API (firmware v1.0+):**
        Use when MULTIPLE illumination sources must be ON simultaneously.
        Requires firmware v1.0 or later (checked automatically).

        - set_port_intensity(port_index, intensity) - Set intensity by port (0=D1, 1=D2, ...)
        - turn_on_port(port_index) / turn_off_port(port_index) - Control specific port
        - turn_on_multiple_ports([port_indices]) - Turn on multiple ports at once
        - turn_off_all_ports() - Turn off all ports

        Example:
            controller.set_port_intensity(0, 50)  # D1 at 50%
            controller.set_port_intensity(1, 30)  # D2 at 30%
            controller.turn_on_multiple_ports([0, 1])  # Both ON simultaneously
            # ... acquire image ...
            controller.turn_off_all_ports()

    Note: The two APIs share underlying hardware state. Using legacy commands
    will update the corresponding port state, and vice versa.
    """

    def __init__(
        self,
        microcontroller: Microcontroller,
        intensity_control_mode=IntensityControlMode.SquidControllerDAC,
        shutter_control_mode=ShutterControlMode.TTL,
        light_source_type=None,
        light_source=None,
        disable_intensity_calibration=False,
        config_repo: Optional[ConfigRepository] = None,
    ):
        """
        Args:
            microcontroller: MCU interface for hardware communication
            intensity_control_mode: How intensity is controlled (DAC or software)
            shutter_control_mode: How shutter is controlled (TTL or software)
            light_source_type: Type of light source (SquidLED, SquidLaser, etc.)
            light_source: External light source object (for software control)
            disable_intensity_calibration: Set True to control LED/laser current directly
            config_repo: Shared configuration repository. Pass the microscope's repo so
                port-mapping edits saved from the GUI take effect without a restart.
        """
        self.microcontroller = microcontroller
        self.config_repo = config_repo if config_repo is not None else ConfigRepository()
        self.intensity_control_mode = intensity_control_mode
        self.shutter_control_mode = shutter_control_mode
        self.light_source_type = light_source_type
        self.light_source = light_source
        self.disable_intensity_calibration = disable_intensity_calibration
        self.channel_mappings_software = {}
        self.is_on = {}
        self.intensity_settings = {}
        self.current_channel = None
        self._log = squid.logging.get_logger(self.__class__.__name__)
        # path -> (mtime, calibration or None, error or None). Re-read when the file changes, so a Save from the
        # calibration dialog, a hand edit or a channel-editor change takes effect on the next set_intensity.
        self._calibration_cache: Dict[Path, Tuple[float, Optional[Calibration], Optional[str]]] = {}
        self._logged_messages = set()

        # Multi-port illumination state tracking (16 ports max)
        self.port_is_on = {i: False for i in range(NUM_ILLUMINATION_PORTS)}
        self.port_intensity = {i: 0.0 for i in range(NUM_ILLUMINATION_PORTS)}

        if self.light_source_type is not None:
            self._configure_light_source()

    @property
    def channel_mappings_TTL(self) -> Dict[int, int]:
        """Wavelength (nm) -> source code mapping for TTL control.

        Computed from the shared config repository on every access so that
        port-mapping edits saved from the GUI take effect immediately.
        """
        return self._load_channel_mappings()

    def _load_channel_mappings(self) -> Dict[int, int]:
        """Load channel mappings from illumination_channel_config.yaml, fallback to default if not found.

        Returns:
            Dict mapping wavelength (nm) to source_code for TTL control.
        """
        default_mappings = _DEFAULT_CHANNEL_MAPPINGS_TTL
        try:
            illumination_config = self.config_repo.get_illumination_config()

            if illumination_config is None:
                return default_mappings

            # Build wavelength -> source_code mapping from YAML config
            mappings = {}
            for channel in illumination_config.channels:
                if channel.wavelength_nm is not None:
                    # Use get_source_code to resolve from controller_port_mapping
                    source_code = illumination_config.get_source_code(channel)
                    mappings[channel.wavelength_nm] = source_code

            return mappings if mappings else default_mappings
        except Exception:
            return default_mappings

    def _configure_light_source(self):
        self.light_source.initialize()
        self._set_intensity_control_mode(self.intensity_control_mode)
        self._set_shutter_control_mode(self.shutter_control_mode)
        self.channel_mappings_software = self.light_source.channel_mappings
        for ch in self.channel_mappings_software:
            self.intensity_settings[ch] = self.get_intensity(ch)
            self.is_on[ch] = self.light_source.get_shutter_state(self.channel_mappings_software[ch])

    def _set_intensity_control_mode(self, mode):
        self.light_source.set_intensity_control_mode(mode)
        self.intensity_control_mode = mode

    def _set_shutter_control_mode(self, mode):
        self.light_source.set_shutter_control_mode(mode)
        self.shutter_control_mode = mode

    def get_intensity(self, channel):
        if self.intensity_control_mode == IntensityControlMode.Software:
            intensity = self.light_source.get_intensity(self.channel_mappings_software[channel])
            self.intensity_settings[channel] = intensity
            return intensity  # 0 - 100

    def turn_on_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        if self.shutter_control_mode == ShutterControlMode.Software:
            self.light_source.set_shutter_state(self.channel_mappings_software[channel], on=True)
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            # self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], self.intensity_settings[channel])
            self.microcontroller.turn_on_illumination()
            self.microcontroller.wait_till_operation_is_completed()

        self.is_on[channel] = True

    def turn_off_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        if self.shutter_control_mode == ShutterControlMode.Software:
            self.light_source.set_shutter_state(self.channel_mappings_software[channel], on=False)
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            self.microcontroller.turn_off_illumination()
            self.microcontroller.wait_till_operation_is_completed()

        self.is_on[channel] = False

    def _log_once(self, level: int, message: str) -> None:
        if message not in self._logged_messages:
            self._logged_messages.add(message)
            self._log.log(level, message)

    def _uses_dac_calibration(self) -> bool:
        return (
            self.light_source_type is None
            and self.intensity_control_mode == IntensityControlMode.SquidControllerDAC
            and not self.disable_intensity_calibration
        )

    def _channel_for_wavelength(self, wavelength) -> Optional[IlluminationChannel]:
        """The channel set_intensity drives for this wavelength: like channel_mappings_TTL, the last one."""
        config = self.config_repo.get_illumination_config()
        if config is None:
            return None
        match = None
        for channel in config.channels:
            if channel.wavelength_nm == wavelength:
                match = channel
        return match

    def _max_output(self, wavelength) -> float:
        channel = self._channel_for_wavelength(wavelength)
        return channel.max_output if channel is not None else 1.0

    def _dac_factor(self) -> float:
        return self.microcontroller.illumination_intensity_factor

    def lookup_calibration(self, wavelength) -> CalibrationLookup:
        """The calibration that applies to `wavelength` (design §6.1); empty when none does."""
        if not self._uses_dac_calibration():
            return _NO_CALIBRATION
        channel = self._channel_for_wavelength(wavelength)
        referenced = channel.intensity_calibration_file if channel is not None else None
        calibrations_dir = self.config_repo.machine_configs_path / CALIBRATIONS_DIR_NAME
        path = resolve_calibration_path(calibrations_dir, referenced, wavelength)
        if path is None:
            if referenced:
                self._log.debug(f"{referenced} (referenced for {wavelength} nm) does not exist: uncalibrated")
            return _NO_CALIBRATION
        mtime = path.stat().st_mtime
        cached = self._calibration_cache.get(path)
        if cached is None or cached[0] != mtime:
            try:
                cached = (mtime, load_calibration(path), None)
            except CalibrationFileError as e:
                cached = (mtime, None, str(e))
                self._log_once(
                    logging.ERROR, f"illumination calibration not used, {wavelength} nm runs uncalibrated: {e}"
                )
            self._calibration_cache[path] = cached
        return CalibrationLookup(path.name, cached[1], cached[2])

    def get_intensity_cap_percent(self, wavelength, max_output: float) -> float:
        """The highest intensity % the GUI offers: 100 for a new calibration (the ceiling is inside the lookup),
        max_output x 100 otherwise (as before)."""
        lookup = self.lookup_calibration(wavelength)
        if lookup.calibration is not None:
            return lookup.calibration.cap_percent(max_output)
        return max_output * 100.0

    def describe_intensity(self, wavelength) -> Dict[str, object]:
        """What this wavelength's intensity % means, for the GUI and acquisition metadata (design §9)."""
        if self.light_source_type is not None or self.intensity_control_mode != IntensityControlMode.SquidControllerDAC:
            return {"intensity_unit": "source_percent"}
        factor = self._dac_factor()
        lookup = self.lookup_calibration(wavelength)
        if lookup.calibration is None:
            return {"intensity_unit": "dac_percent", "illumination_intensity_factor": factor}
        return {**lookup.calibration.describe(), "illumination_intensity_factor": factor}

    def set_intensity(self, channel, intensity):
        # initialize intensity setting for this channel if it doesn't exist
        if channel not in self.intensity_settings:
            self.intensity_settings[channel] = -1
        if self.intensity_control_mode == IntensityControlMode.Software:
            if intensity != self.intensity_settings[channel]:
                self.light_source.set_intensity(self.channel_mappings_software[channel], intensity)
                self.intensity_settings[channel] = intensity
            if self.shutter_control_mode == ShutterControlMode.TTL:
                # This is needed, because we select the channel in microcontroller set_illumination().
                # Otherwise, the wrong channel will be opened when turn_on_illumination() is called.
                self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], intensity)
        else:
            lookup = self.lookup_calibration(channel)
            if lookup.calibration is not None:
                factor = self._dac_factor()
                max_output = self._max_output(channel)
                for note in lookup.calibration.notes(factor, max_output):
                    self._log_once(logging.WARNING, f"{lookup.file_name}: {note}")
                commanded, clamped = lookup.calibration.commanded_percent(intensity, factor, max_output)
                if clamped:
                    self._log_once(
                        logging.WARNING,
                        f"{lookup.file_name}: intensity clamped at Max Output ({max_output * 100:g} % DAC)",
                    )
                self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], commanded)
            else:
                self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], intensity)
            self.intensity_settings[channel] = intensity

    def get_shutter_state(self):
        return self.is_on

    # Multi-port illumination methods (firmware v1.0+)
    # These allow multiple ports to be ON simultaneously with independent intensities

    def _check_multi_port_support(self):
        """Check if firmware supports multi-port commands, raise if not."""
        if not self.microcontroller.supports_multi_port():
            raise RuntimeError(
                "Firmware does not support multi-port illumination commands. "
                "Update firmware to version 1.0 or later."
            )

    def set_port_intensity(self, port_index: int, intensity: float):
        """Set intensity for a specific port without changing on/off state.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
            intensity: Intensity percentage (0-100)
        """
        self._check_multi_port_support()
        if port_index < 0 or port_index >= NUM_ILLUMINATION_PORTS:
            raise ValueError(f"Invalid port index: {port_index}")
        self.microcontroller.set_port_intensity(port_index, intensity)
        self.microcontroller.wait_till_operation_is_completed()
        self.port_intensity[port_index] = intensity

    def turn_on_port(self, port_index: int):
        """Turn on a specific illumination port.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
        """
        self._check_multi_port_support()
        if port_index < 0 or port_index >= NUM_ILLUMINATION_PORTS:
            raise ValueError(f"Invalid port index: {port_index}")
        self.microcontroller.turn_on_port(port_index)
        self.microcontroller.wait_till_operation_is_completed()
        self.port_is_on[port_index] = True

    def turn_off_port(self, port_index: int):
        """Turn off a specific illumination port.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
        """
        self._check_multi_port_support()
        if port_index < 0 or port_index >= NUM_ILLUMINATION_PORTS:
            raise ValueError(f"Invalid port index: {port_index}")
        self.microcontroller.turn_off_port(port_index)
        self.microcontroller.wait_till_operation_is_completed()
        self.port_is_on[port_index] = False

    def set_port_illumination(self, port_index: int, intensity: float, turn_on: bool):
        """Set intensity and on/off state for a specific port in one command.

        Args:
            port_index: Port index (0=D1, 1=D2, etc.)
            intensity: Intensity percentage (0-100)
            turn_on: Whether to turn the port on
        """
        self._check_multi_port_support()
        if port_index < 0 or port_index >= NUM_ILLUMINATION_PORTS:
            raise ValueError(f"Invalid port index: {port_index}")
        self.microcontroller.set_port_illumination(port_index, intensity, turn_on)
        self.microcontroller.wait_till_operation_is_completed()
        self.port_intensity[port_index] = intensity
        self.port_is_on[port_index] = turn_on

    def turn_on_multiple_ports(self, port_indices: List[int]):
        """Turn on multiple ports simultaneously.

        Args:
            port_indices: List of port indices to turn on (0=D1, 1=D2, etc.)
        """
        if not port_indices:
            return

        self._check_multi_port_support()
        # Build port mask and on mask
        port_mask = 0
        on_mask = 0
        for port_index in port_indices:
            if port_index < 0 or port_index >= NUM_ILLUMINATION_PORTS:
                raise ValueError(f"Invalid port index: {port_index}")
            port_mask |= 1 << port_index
            on_mask |= 1 << port_index

        self.microcontroller.set_multi_port_mask(port_mask, on_mask)
        self.microcontroller.wait_till_operation_is_completed()
        for port_index in port_indices:
            self.port_is_on[port_index] = True

    def turn_off_all_ports(self):
        """Turn off all illumination ports."""
        self._check_multi_port_support()
        self.microcontroller.turn_off_all_ports()
        self.microcontroller.wait_till_operation_is_completed()
        for i in range(NUM_ILLUMINATION_PORTS):
            self.port_is_on[i] = False

    def get_active_ports(self) -> List[int]:
        """Get list of currently active (on) port indices.

        Returns:
            List of port indices that are currently on.
        """
        return [i for i in range(NUM_ILLUMINATION_PORTS) if self.port_is_on[i]]

    def close(self):
        if self.light_source is not None:
            self.light_source.shut_down()
