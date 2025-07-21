from enum import Enum
import json
import os
import numpy as np
import pandas as pd
from pathlib import Path

from control.microcontroller import Microcontroller


class LightSourceType(Enum):
    SquidLED = 0
    SquidLaser = 1
    LDI = 2
    CELESTA = 3
    VersaLase = 4
    SCI = 5


class IntensityControlMode(Enum):
    SquidControllerDAC = 0
    Software = 1


class ShutterControlMode(Enum):
    TTL = 0
    Software = 1


class IlluminationController:
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
        # Default channel mappings
        default_mappings = {
            405: 11,
            470: 12,
            488: 12,
            545: 14,
            550: 14,
            555: 14,
            561: 14,
            638: 13,
            640: 13,
            730: 15,
            735: 15,
            750: 15,
        }

        # Try to load mappings from file
        self.channel_mappings_TTL = self._load_channel_mappings(default_mappings)

        self.channel_mappings_software = {}
        self.is_on = {}
        self.intensity_settings = {}
        self.current_channel = None
        self.intensity_luts = {}  # Store LUTs for each wavelength
        self.max_power = {}  # Store max power for each wavelength

        if self.light_source_type is not None:
            self._configure_light_source()

        if self.light_source_type is None and self.disable_intensity_calibration is False:
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
                    return {int(k): v for k, v in mappings["Illumination Code Map"].items()}
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

        self.is_on[channel] = True

    def turn_off_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        if self.shutter_control_mode == ShutterControlMode.Software:
            self.light_source.set_shutter_state(self.channel_mappings_software[channel], on=False)
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            self.microcontroller.turn_off_illumination()

        self.is_on[channel] = False

    def _load_intensity_calibrations(self):
        """Load intensity calibrations for all available wavelengths."""
        calibrations_dir = Path(__file__).parent.parent / "intensity_calibrations"
        if not calibrations_dir.exists():
            return

        for calibration_file in calibrations_dir.glob("*.csv"):
            try:
                wavelength = int(calibration_file.stem)  # Filename should be wavelength.csv
                calibration_data = pd.read_csv(calibration_file)
                if "DAC Percent" in calibration_data.columns and "Optical Power (mW)" in calibration_data.columns:
                    # Store max power for this wavelength
                    self.max_power[wavelength] = calibration_data["Optical Power (mW)"].max()
                    # Create normalized power values (0-100%)
                    normalized_power = calibration_data["Optical Power (mW)"] / self.max_power[wavelength] * 100
                    # Ensure DAC values are in range 0-100
                    dac_percent = np.clip(calibration_data["DAC Percent"].values, 0, 100)
                    self.intensity_luts[wavelength] = {
                        "power_percent": normalized_power.values,
                        "dac_percent": dac_percent,
                    }
            except (ValueError, KeyError) as e:
                print(f"Warning: Could not load calibration from {calibration_file}: {e}")

    def _apply_lut(self, channel, intensity_percent):
        """Convert desired power percentage to DAC value (0-100) using LUT."""
        lut = self.intensity_luts[channel]
        # Ensure intensity is within bounds
        intensity_percent = np.clip(intensity_percent, 0, 100)
        # Interpolate to get DAC value
        dac_percent = np.interp(intensity_percent, lut["power_percent"], lut["dac_percent"])
        # Ensure DAC value is in range 0-100
        return np.clip(dac_percent, 0, 100)

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
            if channel in self.intensity_luts:
                # Apply LUT to convert power percentage to DAC percent (0-100)
                dac_percent = self._apply_lut(channel, intensity)
                self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], dac_percent)
            else:
                self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], intensity)
            self.intensity_settings[channel] = intensity

    def get_shutter_state(self):
        return self.is_on

    def close(self):
        if self.light_source is not None:
            self.light_source.shut_down()
