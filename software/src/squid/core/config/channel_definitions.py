"""
Channel definition models for the two-tier channel configuration system.

This module provides Pydantic models for global channel definitions and per-objective settings.
The two-tier architecture eliminates duplication:
  - Tier 1: Global channel definitions (shared across all objectives)
  - Tier 2: Per-objective settings (exposure, gain, etc.)

Usage:
    from squid.core.config.channel_definitions import (
        ChannelType,
        ChannelDefinition,
        ObjectiveChannelSettings,
        ChannelDefinitionsConfig,
    )

    # Load channel definitions
    config = ChannelDefinitionsConfig.load(Path("channel_definitions.json"))

    # Get enabled channels
    enabled = config.get_enabled_channels()

    # Create per-objective settings
    settings = ObjectiveChannelSettings(
        exposure_time=25.0,
        analog_gain=0.0,
        illumination_intensity=20.0,
    )
"""

from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import json
from pydantic import BaseModel, field_validator, model_validator


class ChannelType(str, Enum):
    """Type of imaging channel."""

    FLUORESCENCE = "fluorescence"
    LED_MATRIX = "led_matrix"


class NumericChannelMapping(BaseModel):
    """Mapping from numeric channel to illumination source and excitation wavelength."""

    illumination_source: int
    ex_wavelength: int


# Channel name constraints (also enforced in UI, but validated here for direct JSON edits)
CHANNEL_NAME_MAX_LENGTH = 64
CHANNEL_NAME_INVALID_CHARS = r'<>:"/\|?*' + "\0"


class ChannelDefinition(BaseModel):
    """Definition of a single imaging channel.

    Attributes:
        name: Human-readable channel name
        type: Either 'fluorescence' or 'led_matrix'
        emission_filter_position: Position in emission filter wheel (default 1)
        display_color: Hex color string for display (e.g., '#FF0000')
        enabled: Whether this channel is available for use
        numeric_channel: For fluorescence channels, maps to numeric channel (1-N)
        illumination_source: For LED matrix channels, direct illumination source
        ex_wavelength: Excitation wavelength (for fluorescence, derived from numeric_channel_mapping)
    """

    name: str
    type: ChannelType
    emission_filter_position: int = 1
    display_color: str = "#FFFFFF"
    enabled: bool = True
    # For fluorescence channels: maps to numeric channel (1-N)
    numeric_channel: Optional[int] = None
    # For LED matrix channels: direct illumination source
    illumination_source: Optional[int] = None
    # Excitation wavelength (for fluorescence, derived from numeric_channel_mapping)
    ex_wavelength: Optional[int] = None

    @field_validator("name", mode="after")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate channel name constraints."""
        if not v or not v.strip():
            raise ValueError("Channel name cannot be empty")
        if len(v) > CHANNEL_NAME_MAX_LENGTH:
            raise ValueError(f"Channel name exceeds maximum length of {CHANNEL_NAME_MAX_LENGTH} characters")
        invalid_found = [c for c in CHANNEL_NAME_INVALID_CHARS if c in v]
        if invalid_found:
            raise ValueError(f"Channel name contains invalid characters: {invalid_found}")
        return v

    @field_validator("display_color", mode="before")
    @classmethod
    def convert_color(cls, v):
        """Convert integer color to hex string if needed."""
        if isinstance(v, int):
            return f"#{v:06X}"
        return v

    @model_validator(mode="after")
    def validate_channel_type_fields(self):
        """Validate that required fields are set based on channel type."""
        if self.type == ChannelType.FLUORESCENCE and self.numeric_channel is None:
            raise ValueError(f"Fluorescence channel '{self.name}' must have numeric_channel set")
        if self.type == ChannelType.LED_MATRIX and self.illumination_source is None:
            raise ValueError(f"LED matrix channel '{self.name}' must have illumination_source set")
        return self

    def get_illumination_source(self, numeric_channel_mapping: Dict[str, NumericChannelMapping]) -> int:
        """Get the illumination source for this channel."""
        if self.type == ChannelType.LED_MATRIX:
            if self.illumination_source is None:
                raise ValueError(f"LED matrix channel '{self.name}' has no illumination_source")
            return self.illumination_source
        else:
            # Fluorescence: look up from numeric channel mapping
            mapping = numeric_channel_mapping.get(str(self.numeric_channel))
            if mapping:
                return mapping.illumination_source
            raise ValueError(
                f"Fluorescence channel '{self.name}' has no numeric_channel_mapping entry "
                f"for numeric_channel {self.numeric_channel}. "
                f"Check your numeric_channel_mapping configuration and add a mapping for this channel."
            )

    def get_ex_wavelength(self, numeric_channel_mapping: Dict[str, NumericChannelMapping]) -> Optional[int]:
        """Get the excitation wavelength for this channel."""
        if self.type == ChannelType.LED_MATRIX:
            return None
        else:
            mapping = numeric_channel_mapping.get(str(self.numeric_channel))
            if mapping:
                return mapping.ex_wavelength
            return self.ex_wavelength


class ConfocalOverrides(BaseModel):
    """Optional overrides for confocal mode.

    Only specify values that differ from widefield defaults.
    None values inherit from base settings.
    """

    exposure_time: Optional[float] = None
    analog_gain: Optional[float] = None
    illumination_intensity: Optional[float] = None
    z_offset: Optional[float] = None


class ObjectiveChannelSettings(BaseModel):
    """Per-objective settings for a channel.

    Base settings are used for widefield mode (or when confocal is not enabled).
    Optional confocal overrides specify only values that differ in confocal mode.

    Attributes:
        exposure_time: Exposure time in milliseconds
        analog_gain: Camera analog gain
        illumination_intensity: Illumination intensity (0-100%)
        z_offset: Z offset in micrometers
        confocal: Optional confocal-specific overrides (only store differences)
    """

    exposure_time: float = 25.0
    analog_gain: float = 0.0
    illumination_intensity: float = 20.0
    z_offset: float = 0.0

    # Optional confocal-specific overrides (only store differences)
    confocal: Optional[ConfocalOverrides] = None

    def get_effective_settings(self, confocal_mode: bool = False) -> "ObjectiveChannelSettings":
        """Get effective settings with confocal overrides applied if applicable.

        Args:
            confocal_mode: Whether the system is in confocal mode

        Returns:
            A new ObjectiveChannelSettings with effective values
        """
        if not confocal_mode or self.confocal is None:
            return ObjectiveChannelSettings(
                exposure_time=self.exposure_time,
                analog_gain=self.analog_gain,
                illumination_intensity=self.illumination_intensity,
                z_offset=self.z_offset,
                confocal=self.confocal,
            )

        # Apply confocal overrides (use override if set, otherwise use base)
        return ObjectiveChannelSettings(
            exposure_time=(self.confocal.exposure_time if self.confocal.exposure_time is not None else self.exposure_time),
            analog_gain=self.confocal.analog_gain if self.confocal.analog_gain is not None else self.analog_gain,
            illumination_intensity=(
                self.confocal.illumination_intensity
                if self.confocal.illumination_intensity is not None
                else self.illumination_intensity
            ),
            z_offset=self.confocal.z_offset if self.confocal.z_offset is not None else self.z_offset,
            confocal=self.confocal,
        )


# Default channel colors (from _def.CHANNEL_COLORS_MAP)
DEFAULT_CHANNEL_COLORS = {
    "405": "#20ADF8",
    "488": "#1FFF00",
    "561": "#FFCF00",
    "638": "#FF0000",
    "730": "#770000",
}


class ChannelDefinitionsConfig(BaseModel):
    """Root configuration for channel definitions.

    Attributes:
        max_fluorescence_channels: Maximum number of fluorescence channels supported
        channels: List of all channel definitions
        numeric_channel_mapping: Mapping from numeric channel ID to illumination source
    """

    max_fluorescence_channels: int = 5
    channels: List[ChannelDefinition] = []
    numeric_channel_mapping: Dict[str, NumericChannelMapping] = {}

    @model_validator(mode="after")
    def validate_channel_mappings(self):
        """Validate that all fluorescence channels have valid numeric_channel mappings.

        This catches configuration errors at startup rather than during use.
        """
        for channel in self.channels:
            if channel.type == ChannelType.FLUORESCENCE and channel.numeric_channel is not None:
                if str(channel.numeric_channel) not in self.numeric_channel_mapping:
                    available = list(self.numeric_channel_mapping.keys()) or ["(none defined)"]
                    raise ValueError(
                        f"Fluorescence channel '{channel.name}' references numeric_channel "
                        f"{channel.numeric_channel}, but no mapping exists for it. "
                        f"Available mappings: {available}. "
                        f"Add a mapping for '{channel.numeric_channel}' in numeric_channel_mapping."
                    )
        return self

    def get_enabled_channels(self) -> List[ChannelDefinition]:
        """Get list of enabled channels only."""
        return [ch for ch in self.channels if ch.enabled]

    def get_channel_by_name(self, name: str) -> Optional[ChannelDefinition]:
        """Get a channel by its name."""
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def save(self, path: Path) -> None:
        """Save configuration to JSON file."""
        try:
            with open(path, "w") as f:
                json.dump(self.model_dump(), f, indent=2)
        except (IOError, PermissionError) as e:
            raise IOError(f"Failed to save channel definitions to {path}: {e}")

    @classmethod
    def load(cls, path: Path) -> "ChannelDefinitionsConfig":
        """Load configuration from JSON file."""
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls(**data)
        except FileNotFoundError:
            raise IOError(
                f"Channel definitions file not found: {path}. "
                f"Delete any partial config files and restart to regenerate defaults."
            )
        except json.JSONDecodeError as e:
            raise IOError(
                f"Invalid JSON in channel definitions file {path}: {e}. "
                f"Check the file for syntax errors or restore from channel_definitions.default.json."
            )
        except PermissionError:
            raise IOError(f"Permission denied reading {path}. " "Check file permissions and ensure the file is not locked.")

    @classmethod
    def generate_default(cls) -> "ChannelDefinitionsConfig":
        """Generate default channel definitions."""
        channels = [
            ChannelDefinition(
                name="BF LED matrix full",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=True,
            ),
            ChannelDefinition(
                name="DF LED matrix",
                type=ChannelType.LED_MATRIX,
                illumination_source=3,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 405 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=1,
                emission_filter_position=1,
                display_color=DEFAULT_CHANNEL_COLORS.get("405", "#20ADF8"),
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 488 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=2,
                emission_filter_position=1,
                display_color=DEFAULT_CHANNEL_COLORS.get("488", "#1FFF00"),
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 561 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=3,
                emission_filter_position=1,
                display_color=DEFAULT_CHANNEL_COLORS.get("561", "#FFCF00"),
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 638 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=4,
                emission_filter_position=1,
                display_color=DEFAULT_CHANNEL_COLORS.get("638", "#FF0000"),
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 730 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=5,
                emission_filter_position=1,
                display_color=DEFAULT_CHANNEL_COLORS.get("730", "#770000"),
                enabled=True,
            ),
            ChannelDefinition(
                name="BF LED matrix low NA",
                type=ChannelType.LED_MATRIX,
                illumination_source=4,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=True,
            ),
            ChannelDefinition(
                name="BF LED matrix left half",
                type=ChannelType.LED_MATRIX,
                illumination_source=1,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix right half",
                type=ChannelType.LED_MATRIX,
                illumination_source=2,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix top half",
                type=ChannelType.LED_MATRIX,
                illumination_source=7,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix bottom half",
                type=ChannelType.LED_MATRIX,
                illumination_source=8,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_R",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#FF0000",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_G",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#00FF00",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_B",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#0000FF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_RGB",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
        ]

        numeric_channel_mapping = {
            "1": NumericChannelMapping(illumination_source=11, ex_wavelength=405),
            "2": NumericChannelMapping(illumination_source=12, ex_wavelength=488),
            "3": NumericChannelMapping(illumination_source=14, ex_wavelength=561),
            "4": NumericChannelMapping(illumination_source=13, ex_wavelength=638),
            "5": NumericChannelMapping(illumination_source=15, ex_wavelength=730),
        }

        return cls(
            max_fluorescence_channels=5,
            channels=channels,
            numeric_channel_mapping=numeric_channel_mapping,
        )
