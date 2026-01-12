"""
Acquisition channel configuration models.

These models define user-facing acquisition settings. They are organized as:
- general.yaml: Shared settings across all objectives
- {objective}.yaml: Objective-specific overrides with optional confocal_override

The merge logic combines these two configs:
- From general.yaml: illumination_channels, display_color, emission_filter_wheel_position, z_offset_um
- From objective.yaml: intensity, exposure_time_ms, gain_mode, pixel_format, confocal_override
"""

import logging
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from control.models.illumination_config import IlluminationChannelConfig

logger = logging.getLogger(__name__)


class CameraSettings(BaseModel):
    """Per-camera settings in an acquisition channel."""

    display_color: str = Field("#FFFFFF", description="Color for display/visualization")
    exposure_time_ms: float = Field(..., description="Exposure time in milliseconds")
    gain_mode: float = Field(
        ...,
        description="Gain setting (currently analog gain value, may become enum in future)",
    )
    pixel_format: Optional[str] = Field(None, description="Pixel format (e.g., 'Mono12')")

    model_config = {"extra": "forbid"}


class ConfocalSettings(BaseModel):
    """Confocal-specific settings in an acquisition channel."""

    filter_wheel_id: int = Field(1, description="Filter wheel ID (default: 1)")
    emission_filter_wheel_position: int = Field(1, description="Filter slot position (default: 1)")
    illumination_iris: Optional[float] = Field(None, description="Illumination iris setting (objective-specific)")
    emission_iris: Optional[float] = Field(None, description="Emission iris setting (objective-specific)")

    model_config = {"extra": "forbid"}


class IlluminationSettings(BaseModel):
    """Illumination configuration for an acquisition channel.

    Note: illumination_channels is required in general.yaml but should be
    omitted in objective-specific files (which only contain overrides).
    """

    illumination_channels: Optional[List[str]] = Field(
        None, description="Names of illumination channels from illumination_channel_config (only in general.yaml)"
    )
    intensity: Dict[str, float] = Field(..., description="Channel name -> intensity percentage mapping")
    z_offset_um: float = Field(0.0, description="Z offset in micrometers")

    model_config = {"extra": "forbid"}


class AcquisitionChannelOverride(BaseModel):
    """
    Override settings for confocal mode (objective-specific).

    When confocal mode is active, these settings override the base settings.
    """

    illumination_settings: Optional[IlluminationSettings] = Field(
        None, description="Override illumination settings for confocal mode"
    )
    camera_settings: Optional[Dict[str, CameraSettings]] = Field(
        None, description="Override camera settings for confocal mode"
    )
    confocal_settings: Optional[ConfocalSettings] = Field(None, description="Override confocal settings")

    model_config = {"extra": "forbid"}


class AcquisitionChannel(BaseModel):
    """A single acquisition channel configuration."""

    name: str = Field(..., description="Display name for this acquisition channel")
    illumination_settings: IlluminationSettings = Field(..., description="Illumination configuration")
    camera_settings: Dict[str, CameraSettings] = Field(
        default_factory=dict, description="Camera ID -> settings mapping"
    )
    emission_filter_wheel_position: Optional[Dict[int, int]] = Field(
        None, description="Body filter wheel: wheel_id -> position (if available)"
    )
    confocal_settings: Optional[ConfocalSettings] = Field(
        None, description="Confocal settings (only if confocal in light path)"
    )
    # Objective-specific override for confocal mode
    confocal_override: Optional[AcquisitionChannelOverride] = Field(
        None, description="Settings to use when in confocal mode"
    )

    model_config = {"extra": "forbid"}

    # ─────────────────────────────────────────────────────────────────────────────
    # Convenience properties for single-camera, single-illumination access
    # ─────────────────────────────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """Unique identifier derived from channel name (for UI compatibility)."""
        import hashlib

        return hashlib.sha256(self.name.encode()).hexdigest()[:16]

    @property
    def exposure_time(self) -> float:
        """Primary camera exposure time in ms."""
        camera = next(iter(self.camera_settings.values()), None)
        return camera.exposure_time_ms if camera else 20.0

    @exposure_time.setter
    def exposure_time(self, value: float) -> None:
        """Set primary camera exposure time in ms."""
        if self.camera_settings:
            camera_id = next(iter(self.camera_settings.keys()))
            self.camera_settings[camera_id].exposure_time_ms = value

    @property
    def analog_gain(self) -> float:
        """Primary camera analog gain."""
        camera = next(iter(self.camera_settings.values()), None)
        return camera.gain_mode if camera else 0.0

    @analog_gain.setter
    def analog_gain(self, value: float) -> None:
        """Set primary camera analog gain."""
        if self.camera_settings:
            camera_id = next(iter(self.camera_settings.keys()))
            self.camera_settings[camera_id].gain_mode = value

    @property
    def display_color(self) -> str:
        """Primary camera display color as hex string."""
        camera = next(iter(self.camera_settings.values()), None)
        return camera.display_color if camera else "#FFFFFF"

    @property
    def illumination_intensity(self) -> float:
        """Primary illumination channel intensity."""
        if self.illumination_settings.illumination_channels:
            ch_name = self.illumination_settings.illumination_channels[0]
            return self.illumination_settings.intensity.get(ch_name, 20.0)
        # Fall back to first intensity value if no channels specified
        if self.illumination_settings.intensity:
            return next(iter(self.illumination_settings.intensity.values()))
        return 20.0

    @illumination_intensity.setter
    def illumination_intensity(self, value: float) -> None:
        """Set primary illumination channel intensity."""
        if self.illumination_settings.illumination_channels:
            ch_name = self.illumination_settings.illumination_channels[0]
            self.illumination_settings.intensity[ch_name] = value
        elif self.illumination_settings.intensity:
            # Update first intensity value
            first_key = next(iter(self.illumination_settings.intensity.keys()))
            self.illumination_settings.intensity[first_key] = value

    @property
    def primary_illumination_channel(self) -> Optional[str]:
        """Name of the primary illumination channel."""
        if self.illumination_settings.illumination_channels:
            return self.illumination_settings.illumination_channels[0]
        return None

    @property
    def z_offset(self) -> float:
        """Z offset in micrometers."""
        return self.illumination_settings.z_offset_um

    @property
    def emission_filter_position(self) -> int:
        """Primary emission filter wheel position."""
        if self.emission_filter_wheel_position:
            return next(iter(self.emission_filter_wheel_position.values()), 1)
        return 1

    def get_illumination_source_code(self, illumination_config: "IlluminationChannelConfig") -> int:
        """Get the illumination source code for the primary illumination channel.

        Args:
            illumination_config: The machine's illumination channel configuration.

        Returns:
            Source code (int) for the primary illumination channel, or 0 if not found.
        """
        ill_channel_name = self.primary_illumination_channel
        if not ill_channel_name:
            return 0
        ill_channel = illumination_config.get_channel_by_name(ill_channel_name)
        if not ill_channel:
            return 0
        return illumination_config.get_source_code(ill_channel)

    def get_illumination_wavelength(self, illumination_config: "IlluminationChannelConfig") -> Optional[int]:
        """Get the wavelength for the primary illumination channel.

        Args:
            illumination_config: The machine's illumination channel configuration.

        Returns:
            Wavelength in nm, or None if not a fluorescence channel.
        """
        ill_channel_name = self.primary_illumination_channel
        if not ill_channel_name:
            return None
        ill_channel = illumination_config.get_channel_by_name(ill_channel_name)
        if not ill_channel:
            return None
        return ill_channel.wavelength_nm

    def get_effective_settings(self, confocal_mode: bool) -> "AcquisitionChannel":
        """
        Get effective settings based on confocal mode.

        If confocal_mode is True and confocal_override exists, merge the
        override settings with the base settings.
        """
        if not confocal_mode or not self.confocal_override:
            return self

        # Create a copy with overrides applied
        merged_illumination = self.illumination_settings
        if self.confocal_override.illumination_settings:
            merged_illumination = self.confocal_override.illumination_settings

        merged_camera = dict(self.camera_settings)
        if self.confocal_override.camera_settings:
            merged_camera.update(self.confocal_override.camera_settings)

        merged_confocal = self.confocal_settings
        if self.confocal_override.confocal_settings:
            merged_confocal = self.confocal_override.confocal_settings

        return AcquisitionChannel(
            name=self.name,
            illumination_settings=merged_illumination,
            camera_settings=merged_camera,
            emission_filter_wheel_position=self.emission_filter_wheel_position,
            confocal_settings=merged_confocal,
            confocal_override=None,  # Already applied
        )


class GeneralChannelConfig(BaseModel):
    """
    general.yaml - shared settings across all objectives.

    This file defines the base acquisition channels that are available.
    Objective-specific files can override these settings.
    """

    version: int = Field(1, description="Configuration format version")
    channels: List[AcquisitionChannel] = Field(default_factory=list, description="List of acquisition channels")

    model_config = {"extra": "forbid"}

    def get_channel_by_name(self, name: str) -> Optional[AcquisitionChannel]:
        """Get an acquisition channel by name."""
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None


class ObjectiveChannelConfig(BaseModel):
    """
    {objective}.yaml - objective-specific overrides.

    This file contains objective-specific settings that override the
    general.yaml settings. It can also include confocal_override sections.
    """

    version: int = Field(1, description="Configuration format version")
    channels: List[AcquisitionChannel] = Field(
        default_factory=list, description="List of acquisition channel overrides"
    )

    model_config = {"extra": "forbid"}

    def get_channel_by_name(self, name: str) -> Optional[AcquisitionChannel]:
        """Get an acquisition channel override by name."""
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None


def merge_channel_configs(
    general: GeneralChannelConfig,
    objective: ObjectiveChannelConfig,
) -> List[AcquisitionChannel]:
    """
    Merge general.yaml and objective.yaml into final acquisition channels.

    The merge takes:
    - From general: name, illumination_channels, display_color, emission_filter_wheel_position,
                    z_offset_um, base confocal_settings (filter_wheel_id, emission_filter_wheel_position)
    - From objective: intensity, exposure_time_ms, gain_mode, pixel_format,
                      confocal iris settings, confocal_override

    Args:
        general: General channel configuration (defines channel identity)
        objective: Objective-specific configuration (defines per-objective settings)

    Returns:
        List of merged AcquisitionChannel objects ready for use
    """
    merged_channels = []

    for gen_channel in general.channels:
        # Find matching objective channel by name
        obj_channel = objective.get_channel_by_name(gen_channel.name)

        if obj_channel is None:
            # No objective override - use general settings as-is
            merged_channels.append(gen_channel)
            continue

        # Merge illumination settings
        # general: illumination_channels, z_offset_um
        # objective: intensity
        merged_illumination = IlluminationSettings(
            illumination_channels=gen_channel.illumination_settings.illumination_channels,
            intensity=obj_channel.illumination_settings.intensity,
            z_offset_um=gen_channel.illumination_settings.z_offset_um,  # From general
        )

        # Merge camera settings
        # general: display_color
        # objective: exposure_time_ms, gain_mode, pixel_format
        merged_camera: Dict[str, CameraSettings] = {}
        for camera_id, gen_cam in gen_channel.camera_settings.items():
            obj_cam = obj_channel.camera_settings.get(camera_id)
            if obj_cam:
                merged_camera[camera_id] = CameraSettings(
                    display_color=gen_cam.display_color,
                    exposure_time_ms=obj_cam.exposure_time_ms,
                    gain_mode=obj_cam.gain_mode,
                    pixel_format=obj_cam.pixel_format,
                )
            else:
                # No objective camera settings - use general
                merged_camera[camera_id] = gen_cam

        # Merge confocal settings
        # general: filter_wheel_id, emission_filter_wheel_position
        # objective: illumination_iris, emission_iris
        merged_confocal = None
        if gen_channel.confocal_settings or obj_channel.confocal_settings:
            gen_confocal = gen_channel.confocal_settings or ConfocalSettings()
            obj_confocal = obj_channel.confocal_settings or ConfocalSettings()
            merged_confocal = ConfocalSettings(
                filter_wheel_id=gen_confocal.filter_wheel_id,
                emission_filter_wheel_position=gen_confocal.emission_filter_wheel_position,
                illumination_iris=obj_confocal.illumination_iris,
                emission_iris=obj_confocal.emission_iris,
            )

        merged_channel = AcquisitionChannel(
            name=gen_channel.name,
            illumination_settings=merged_illumination,
            camera_settings=merged_camera,
            emission_filter_wheel_position=gen_channel.emission_filter_wheel_position,
            confocal_settings=merged_confocal,
            confocal_override=obj_channel.confocal_override,  # From objective only
        )
        merged_channels.append(merged_channel)

    return merged_channels


def validate_illumination_references(
    config: GeneralChannelConfig,
    illumination_config: "IlluminationChannelConfig",
) -> List[str]:
    """
    Validate that all illumination_channels references in acquisition config
    exist in illumination_channel_config.yaml.

    Args:
        config: Acquisition channel configuration to validate
        illumination_config: Illumination channel configuration with available channels

    Returns:
        List of error messages. Empty list if all references are valid.
    """
    errors = []

    # Build set of valid illumination channel names
    valid_names: Set[str] = {ch.name for ch in illumination_config.channels}

    for acq_channel in config.channels:
        ill_channels = acq_channel.illumination_settings.illumination_channels
        if ill_channels:
            for ill_name in ill_channels:
                if ill_name not in valid_names:
                    errors.append(
                        f"Acquisition channel '{acq_channel.name}' references "
                        f"illumination channel '{ill_name}' which does not exist in "
                        f"illumination_channel_config.yaml"
                    )

        # Also validate intensity dict keys
        for intensity_key in acq_channel.illumination_settings.intensity.keys():
            if intensity_key not in valid_names:
                errors.append(
                    f"Acquisition channel '{acq_channel.name}' has intensity for "
                    f"'{intensity_key}' which does not exist in illumination_channel_config.yaml"
                )

    return errors


def get_illumination_channel_names(config: GeneralChannelConfig) -> Set[str]:
    """
    Get all unique illumination channel names referenced in a config.

    Args:
        config: Acquisition channel configuration

    Returns:
        Set of illumination channel names
    """
    names: Set[str] = set()
    for acq_channel in config.channels:
        if acq_channel.illumination_settings.illumination_channels:
            names.update(acq_channel.illumination_settings.illumination_channels)
        names.update(acq_channel.illumination_settings.intensity.keys())
    return names


class AcquisitionOutputConfig(BaseModel):
    """
    Output format for acquisition settings saved alongside acquired images.

    This is written to acquisition_channels.yaml in the experiment output directory
    to record what settings were used during acquisition.
    """

    version: int = Field(1, description="Configuration format version")
    objective: str = Field(..., description="Objective used for acquisition")
    confocal_mode: bool = Field(False, description="Whether confocal mode was active")
    channels: List[AcquisitionChannel] = Field(default_factory=list, description="List of acquisition channels used")

    model_config = {"extra": "forbid"}
