from enum import Enum
import json
from typing import List

import numpy as np
import pandas as pd
from pathlib import Path

from _def import ILLUMINATION_CODE
from squid.backend.microcontroller import Microcontroller

# Number of illumination ports supported (matches firmware)
NUM_ILLUMINATION_PORTS = 16


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


class IlluminationController:
    """Controls illumination hardware via the microcontroller.

    Supports both legacy single-source illumination (SET_ILLUMINATION / TURN_ON / TURN_OFF)
    and multi-port illumination (firmware v1.0+) where multiple ports can be independently
    controlled with per-port intensity and on/off state.

    Multi-port methods require firmware v1.0+ and will raise RuntimeError if the firmware
    does not support them. Use microcontroller.supports_multi_port() to check.
    """

    def __init__(
        self,
        microcontroller: Microcontroller,
        intensity_control_mode=IntensityControlMode.SquidControllerDAC,
        shutter_control_mode=ShutterControlMode.TTL,
        light_source_type=None,
        light_source=None,
        disable_intensity_calibration=False,
    ):
        """
        disable_intensity_calibration: for Squid LEDs and lasers only - set to True to control LED/laser current directly
        """
        self.microcontroller = microcontroller
        self.intensity_control_mode = intensity_control_mode
        self.shutter_control_mode = shutter_control_mode
        self.light_source_type = light_source_type
        self.light_source = light_source
        self.disable_intensity_calibration = disable_intensity_calibration
        # Default channel mappings: wavelength -> illumination source code
        default_mappings = {
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

        # Try to load mappings from file
        self.channel_mappings_TTL = self._load_channel_mappings(default_mappings)

        self.channel_mappings_software = {}
        self.is_on = {}
        self.intensity_settings = {}
        self.current_channel = None
        self.intensity_luts = {}  # Store LUTs for each wavelength
        self.max_power = {}  # Store max power for each wavelength

        # Multi-port illumination state tracking (16 ports max)
        self.port_is_on = {i: False for i in range(NUM_ILLUMINATION_PORTS)}
        self.port_intensity = {i: 0.0 for i in range(NUM_ILLUMINATION_PORTS)}

        if self.light_source_type is not None:
            self._configure_light_source()

        if (
            self.light_source_type is None
            and self.disable_intensity_calibration is False
        ):
            self._load_intensity_calibrations()

    def _load_channel_mappings(self, default_mappings):
        """Load channel mappings from JSON file, fallback to default if file not found."""
        try:
            # Get the parent directory of the current file
            current_dir = Path(__file__).parent.parent
            mapping_file = current_dir / "channel_mappings.json"

            if mapping_file.exists():
                with open(mapping_file, "r") as f:
                    mappings = json.load(f)
                    # Convert string keys to integers
                    return {
                        int(k): v for k, v in mappings["Illumination Code Map"].items()
                    }
            return default_mappings
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            return default_mappings

    def _configure_light_source(self):
        self.light_source.initialize()
        self._set_intensity_control_mode(self.intensity_control_mode)
        self._set_shutter_control_mode(self.shutter_control_mode)
        self.channel_mappings_software = self.light_source.channel_mappings
        for ch in self.channel_mappings_software:
            self.intensity_settings[ch] = self.get_intensity(ch)
            self.is_on[ch] = self.light_source.get_shutter_state(
                self.channel_mappings_software[ch]
            )

    def _set_intensity_control_mode(self, mode):
        self.light_source.set_intensity_control_mode(mode)
        self.intensity_control_mode = mode

    def _set_shutter_control_mode(self, mode):
        self.light_source.set_shutter_control_mode(mode)
        self.shutter_control_mode = mode

    def get_intensity(self, channel):
        if self.intensity_control_mode == IntensityControlMode.Software:
            intensity = self.light_source.get_intensity(
                self.channel_mappings_software[channel]
            )
            self.intensity_settings[channel] = intensity
            return intensity  # 0 - 100

    def turn_on_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        # Check if channel is mapped
        if channel not in self.channel_mappings_TTL:
            return

        if self.shutter_control_mode == ShutterControlMode.Software:
            if channel in self.channel_mappings_software:
                self.light_source.set_shutter_state(
                    self.channel_mappings_software[channel], on=True
                )
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            # self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], self.intensity_settings[channel])
            self.microcontroller.turn_on_illumination()
            self.microcontroller.wait_till_operation_is_completed()

        self.is_on[channel] = True

    def turn_off_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        # Check if channel is mapped
        if channel not in self.channel_mappings_TTL:
            return

        if self.shutter_control_mode == ShutterControlMode.Software:
            if channel in self.channel_mappings_software:
                self.light_source.set_shutter_state(
                    self.channel_mappings_software[channel], on=False
                )
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            self.microcontroller.turn_off_illumination()
            self.microcontroller.wait_till_operation_is_completed()

        self.is_on[channel] = False

    def _load_intensity_calibrations(self):
        """Load intensity calibrations for all available wavelengths."""
        calibrations_dir = Path(__file__).parent.parent / "intensity_calibrations"
        if not calibrations_dir.exists():
            return

        for calibration_file in calibrations_dir.glob("*.csv"):
            try:
                wavelength = int(
                    calibration_file.stem
                )  # Filename should be wavelength.csv
                calibration_data = pd.read_csv(calibration_file)
                if (
                    "DAC Percent" in calibration_data.columns
                    and "Optical Power (mW)" in calibration_data.columns
                ):
                    # Store max power for this wavelength
                    self.max_power[wavelength] = calibration_data[
                        "Optical Power (mW)"
                    ].max()
                    # Create normalized power values (0-100%)
                    normalized_power = (
                        calibration_data["Optical Power (mW)"]
                        / self.max_power[wavelength]
                        * 100
                    )
                    # Ensure DAC values are in range 0-100
                    dac_percent = np.clip(
                        calibration_data["DAC Percent"].values, 0, 100
                    )
                    self.intensity_luts[wavelength] = {
                        "power_percent": normalized_power.values,
                        "dac_percent": dac_percent,
                    }
            except (ValueError, KeyError) as e:
                print(
                    f"Warning: Could not load calibration from {calibration_file}: {e}"
                )

    def _apply_lut(self, channel, intensity_percent):
        """Convert desired power percentage to DAC value (0-100) using LUT."""
        lut = self.intensity_luts[channel]
        # Ensure intensity is within bounds
        intensity_percent = np.clip(intensity_percent, 0, 100)
        # Interpolate to get DAC value
        dac_percent = np.interp(
            intensity_percent, lut["power_percent"], lut["dac_percent"]
        )
        # Ensure DAC value is in range 0-100
        return np.clip(dac_percent, 0, 100)

    def set_intensity(self, channel, intensity):
        # Check if channel is mapped before attempting to set intensity
        if channel not in self.channel_mappings_TTL:
            # Channel not configured - log warning and return silently
            # This can happen when illumination_source is an index (0, 1, 2)
            # rather than a wavelength (405, 488, etc.)
            return

        # initialize intensity setting for this channel if it doesn't exist
        if channel not in self.intensity_settings:
            self.intensity_settings[channel] = -1
        if self.intensity_control_mode == IntensityControlMode.Software:
            if intensity != self.intensity_settings[channel]:
                self.light_source.set_intensity(
                    self.channel_mappings_software[channel], intensity
                )
                self.intensity_settings[channel] = intensity
            if self.shutter_control_mode == ShutterControlMode.TTL:
                # This is needed, because we select the channel in microcontroller set_illumination().
                # Otherwise, the wrong channel will be opened when turn_on_illumination() is called.
                self.microcontroller.set_illumination(
                    self.channel_mappings_TTL[channel], intensity
                )
        else:
            if channel in self.intensity_luts:
                # Apply LUT to convert power percentage to DAC percent (0-100)
                dac_percent = self._apply_lut(channel, intensity)
                self.microcontroller.set_illumination(
                    self.channel_mappings_TTL[channel], dac_percent
                )
            else:
                self.microcontroller.set_illumination(
                    self.channel_mappings_TTL[channel], intensity
                )
            self.intensity_settings[channel] = intensity

    def get_shutter_state(self):
        return self.is_on

    # Multi-port illumination methods (firmware v1.0+)

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
        """Get list of currently active (on) port indices."""
        return [i for i in range(NUM_ILLUMINATION_PORTS) if self.port_is_on[i]]

    def close(self):
        if self.light_source is not None:
            self.light_source.shut_down()
