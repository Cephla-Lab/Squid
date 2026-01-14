"""
Acquisition channel configuration models (v1.1 schema).

These models define user-facing acquisition settings. They are organized as:
- general.yaml: Shared settings across all objectives
- {objective}.yaml: Objective-specific overrides with optional confocal_override

The merge logic combines these two configs:
- From general.yaml: name, enabled, display_color, camera, illumination_channel, filter_wheel,
                     filter_position, z_offset_um, confocal_settings (confocal_filter_wheel, confocal_filter_position)
- From objective.yaml: intensity, exposure_time_ms, gain_mode, pixel_format, confocal_override (iris settings)

Schema versions:
- v1.0: Original schema with camera_settings as Dict[str, CameraSettings], display_color in CameraSettings
- v1.1: Single camera per channel, display_color at channel level, filter_wheel by name, channel groups
- v1.2: Single illumination_channel per acquisition channel (was list), intensity as float (was dict)
"""

import logging
from enum import Enum
from typing import Dict, List, Optional, Set, Union, TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from control.models.illumination_config import IlluminationChannelConfig

logger = logging.getLogger(__name__)


class CameraSettings(BaseModel):
    """Per-camera settings in an acquisition channel.

    Note: In v1.1, display_color moved to AcquisitionChannel level.
    """

    exposure_time_ms: float = Field(..., gt=0, description="Exposure time in milliseconds")
    gain_mode: float = Field(
        ...,
        ge=0,
        description="Gain setting (currently analog gain value, may become enum in future)",
    )
    pixel_format: Optional[str] = Field(None, description="Pixel format (e.g., 'Mono12')")

    model_config = {"extra": "forbid"}


class ConfocalSettings(BaseModel):
    """Confocal-specific settings (part of confocal unit hardware).

    The confocal unit has its own filter wheel, separate from body-level filter wheels.
    Filter settings here apply only when confocal is in the light path.
    """

    # Confocal unit filter wheel (v1.1: by name instead of ID)
    confocal_filter_wheel: Optional[str] = Field(
        None,
        description="Confocal filter wheel name (references filter_wheels.yaml). "
        "Use 'auto' to automatically use the single available wheel.",
    )
    confocal_filter_position: Optional[int] = Field(None, ge=1, description="Position in confocal filter wheel")
    # Iris settings (objective-specific), 0-100% of aperture
    illumination_iris: Optional[float] = Field(
        None, ge=0, le=100, description="Illumination iris aperture percentage (0-100, objective-specific)"
    )
    emission_iris: Optional[float] = Field(
        None, ge=0, le=100, description="Emission iris aperture percentage (0-100, objective-specific)"
    )

    model_config = {"extra": "forbid"}


class IlluminationSettings(BaseModel):
    """Illumination configuration for an acquisition channel.

    Note: illumination_channel is required in general.yaml but should be
    omitted in objective-specific files (which only contain overrides).
    """

    illumination_channel: Optional[str] = Field(
        None, description="Illumination channel name from illumination_channel_config (only in general.yaml)"
    )
    intensity: float = Field(..., ge=0, le=100, description="Illumination intensity percentage (0-100)")
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
    camera_settings: Optional[CameraSettings] = Field(None, description="Override camera settings for confocal mode")
    confocal_settings: Optional[ConfocalSettings] = Field(None, description="Override confocal settings")

    model_config = {"extra": "forbid"}


class AcquisitionChannel(BaseModel):
    """A single acquisition channel configuration (v1.1 schema).

    Key changes from v1.0:
    - display_color moved from camera_settings to channel level
    - camera field added for camera name reference
    - camera_settings is now a single object, not Dict
    - filter_wheel/filter_position replace emission_filter_wheel_position
    """

    name: str = Field(..., min_length=1, description="Display name for this acquisition channel")
    enabled: bool = Field(True, description="Whether channel is enabled for selection in UI")
    display_color: str = Field(
        "#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$", description="Hex color for UI visualization (e.g., '#FF0000')"
    )

    # Camera assignment (optional for single-camera systems)
    camera: Optional[str] = Field(
        None, description="Camera name (references cameras.yaml). Optional for single-camera systems."
    )
    camera_settings: CameraSettings = Field(..., description="Camera settings for this channel")

    # Body-level filter wheel (separate from confocal filter wheel)
    # "auto" sentinel is resolved by UI at runtime for single-wheel systems
    filter_wheel: Optional[str] = Field(
        None,
        description="Body filter wheel name (references filter_wheels.yaml). "
        "Use 'auto' to automatically use the single available wheel.",
    )
    filter_position: Optional[int] = Field(None, ge=1, description="Position in body filter wheel")

    # Illumination
    illumination_settings: IlluminationSettings = Field(..., description="Illumination configuration")

    # Confocal (has its own filter wheel in confocal_settings)
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
        """Camera exposure time in ms."""
        return self.camera_settings.exposure_time_ms

    @exposure_time.setter
    def exposure_time(self, value: float) -> None:
        """Set camera exposure time in ms."""
        self.camera_settings.exposure_time_ms = value

    @property
    def analog_gain(self) -> float:
        """Camera analog gain."""
        return self.camera_settings.gain_mode

    @analog_gain.setter
    def analog_gain(self, value: float) -> None:
        """Set camera analog gain."""
        self.camera_settings.gain_mode = value

    # Note: display_color is now a field, not a property

    @property
    def illumination_intensity(self) -> float:
        """Illumination intensity percentage."""
        return self.illumination_settings.intensity

    @illumination_intensity.setter
    def illumination_intensity(self, value: float) -> None:
        """Set illumination intensity percentage."""
        self.illumination_settings.intensity = value

    @property
    def primary_illumination_channel(self) -> Optional[str]:
        """Name of the illumination channel."""
        return self.illumination_settings.illumination_channel

    @property
    def z_offset(self) -> float:
        """Z offset in micrometers."""
        return self.illumination_settings.z_offset_um

    @property
    def emission_filter_position(self) -> Optional[int]:
        """Body filter wheel position (for backward compatibility)."""
        return self.filter_position

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

        # For v1.1, camera_settings is a single object
        merged_camera = self.camera_settings
        if self.confocal_override.camera_settings:
            merged_camera = self.confocal_override.camera_settings

        merged_confocal = self.confocal_settings
        if self.confocal_override.confocal_settings:
            merged_confocal = self.confocal_override.confocal_settings

        return AcquisitionChannel(
            name=self.name,
            enabled=self.enabled,
            display_color=self.display_color,
            camera=self.camera,
            camera_settings=merged_camera,
            filter_wheel=self.filter_wheel,
            filter_position=self.filter_position,
            illumination_settings=merged_illumination,
            confocal_settings=merged_confocal,
            confocal_override=None,  # Already applied
        )


class GeneralChannelConfig(BaseModel):
    """
    general.yaml - shared settings across all objectives.

    This file defines the base acquisition channels that are available.
    Objective-specific files can override these settings.

    v1.1 adds channel_groups for multi-camera acquisition support.
    """

    version: Union[int, float] = Field(1, description="Configuration format version")
    channels: List[AcquisitionChannel] = Field(default_factory=list, description="List of acquisition channels")
    channel_groups: List["ChannelGroup"] = Field(
        default_factory=list, description="Channel groups for multi-camera acquisition (v1.1+)"
    )

    model_config = {"extra": "forbid"}

    def get_channel_by_name(self, name: str) -> Optional[AcquisitionChannel]:
        """Get an acquisition channel by name."""
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def get_group_by_name(self, name: str) -> Optional["ChannelGroup"]:
        """Get a channel group by name."""
        for group in self.channel_groups:
            if group.name == name:
                return group
        return None

    def get_group_names(self) -> List[str]:
        """Get list of all channel group names."""
        return [group.name for group in self.channel_groups]


class ObjectiveChannelConfig(BaseModel):
    """
    {objective}.yaml - objective-specific overrides.

    This file contains objective-specific settings that override the
    general.yaml settings. It can also include confocal_override sections.

    Note: channel_groups are NOT included here - they are defined only in general.yaml.
    """

    version: Union[int, float] = Field(1, description="Configuration format version")
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
    Merge general.yaml and objective.yaml into final acquisition channels (v1.1 schema).

    The merge takes:
    - From general: name, display_color, camera, illumination_channel, filter_wheel, filter_position,
                    z_offset_um, base confocal_settings (confocal_filter_wheel, confocal_filter_position)
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
        # general: illumination_channel, z_offset_um
        # objective: intensity
        merged_illumination = IlluminationSettings(
            illumination_channel=gen_channel.illumination_settings.illumination_channel,
            intensity=obj_channel.illumination_settings.intensity,
            z_offset_um=gen_channel.illumination_settings.z_offset_um,  # From general
        )

        # Merge camera settings (v1.1: single object, not Dict)
        # general: (nothing - display_color is now at channel level)
        # objective: exposure_time_ms, gain_mode, pixel_format
        merged_camera = CameraSettings(
            exposure_time_ms=obj_channel.camera_settings.exposure_time_ms,
            gain_mode=obj_channel.camera_settings.gain_mode,
            pixel_format=obj_channel.camera_settings.pixel_format,
        )

        # Merge confocal settings
        # general: confocal_filter_wheel, confocal_filter_position
        # objective: illumination_iris, emission_iris
        merged_confocal = None
        if gen_channel.confocal_settings or obj_channel.confocal_settings:
            gen_confocal = gen_channel.confocal_settings or ConfocalSettings()
            obj_confocal = obj_channel.confocal_settings or ConfocalSettings()
            merged_confocal = ConfocalSettings(
                confocal_filter_wheel=gen_confocal.confocal_filter_wheel,
                confocal_filter_position=gen_confocal.confocal_filter_position,
                illumination_iris=obj_confocal.illumination_iris,
                emission_iris=obj_confocal.emission_iris,
            )

        merged_channel = AcquisitionChannel(
            name=gen_channel.name,
            enabled=gen_channel.enabled,  # From general
            display_color=gen_channel.display_color,  # From general
            camera=gen_channel.camera,  # From general
            camera_settings=merged_camera,
            filter_wheel=gen_channel.filter_wheel,  # From general
            filter_position=gen_channel.filter_position,  # From general
            illumination_settings=merged_illumination,
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
    Validate that all illumination_channel references in acquisition config
    exist in illumination_channel_config.yaml.

    Args:
        config: Acquisition channel configuration to validate
        illumination_config: Illumination channel configuration with available channels

    Returns:
        List of error messages. Empty list if all references are valid.
    """
    errors = []
    valid_names: Set[str] = {ch.name for ch in illumination_config.channels}

    for acq_channel in config.channels:
        ill_channel = acq_channel.illumination_settings.illumination_channel
        if ill_channel and ill_channel not in valid_names:
            errors.append(
                f"Acquisition channel '{acq_channel.name}' references "
                f"illumination channel '{ill_channel}' which does not exist in "
                f"illumination_channel_config.yaml"
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
        if acq_channel.illumination_settings.illumination_channel:
            names.add(acq_channel.illumination_settings.illumination_channel)
    return names


class AcquisitionOutputConfig(BaseModel):
    """
    Output format for acquisition settings saved alongside acquired images.

    This is written to acquisition_channels.yaml in the experiment output directory
    to record what settings were used during acquisition.
    """

    version: Union[int, float] = Field(1, description="Configuration format version")
    objective: str = Field(..., description="Objective used for acquisition")
    confocal_mode: bool = Field(False, description="Whether confocal mode was active")
    channels: List[AcquisitionChannel] = Field(default_factory=list, description="List of acquisition channels used")

    model_config = {"extra": "forbid"}


# ─────────────────────────────────────────────────────────────────────────────
# Channel Groups (v1.1)
# ─────────────────────────────────────────────────────────────────────────────


class SynchronizationMode(str, Enum):
    """Synchronization mode for channel groups."""

    SIMULTANEOUS = "simultaneous"  # Multi-camera parallel capture with timing offsets
    SEQUENTIAL = "sequential"  # Channels captured one after another


class ChannelGroupEntry(BaseModel):
    """A channel entry within a channel group."""

    name: str = Field(..., min_length=1, description="Channel name (must exist in channels list)")
    offset_us: float = Field(
        0.0,
        ge=0,
        description="Trigger offset in microseconds (only used for simultaneous mode)",
    )

    model_config = {"extra": "forbid"}


class ChannelGroup(BaseModel):
    """
    A group of channels to be acquired together.

    Channel groups define how multiple channels are acquired:
    - simultaneous: Multiple cameras trigger at the same time (with optional offsets)
    - sequential: Channels are captured one after another

    For simultaneous mode, each channel must use a different camera.
    """

    name: str = Field(..., min_length=1, description="Group name for UI")
    synchronization: SynchronizationMode = Field(
        SynchronizationMode.SEQUENTIAL,
        description="Capture mode: simultaneous or sequential",
    )
    channels: List[ChannelGroupEntry] = Field(..., min_length=1, description="Channels in this group")

    model_config = {"extra": "forbid"}

    def get_channel_names(self) -> List[str]:
        """Get list of channel names in this group."""
        return [entry.name for entry in self.channels]

    def get_channel_offset(self, channel_name: str) -> float:
        """Get offset for a channel in microseconds."""
        for entry in self.channels:
            if entry.name == channel_name:
                return entry.offset_us
        return 0.0

    def get_channels_sorted_by_offset(self) -> List[ChannelGroupEntry]:
        """Get channels sorted by trigger offset (for simultaneous mode)."""
        return sorted(self.channels, key=lambda c: c.offset_us)


def validate_channel_group(
    group: ChannelGroup,
    channels: List[AcquisitionChannel],
) -> List[str]:
    """
    Validate channel group configuration.

    Args:
        group: Channel group to validate
        channels: List of available channels

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Track cameras used (v1.1: camera field on channel)
    cameras_used = []
    for entry in group.channels:
        channel = next((c for c in channels if c.name == entry.name), None)
        if channel is None:
            errors.append(f"Channel '{entry.name}' not found in channels list")
            continue

        # Get camera from channel (v1.1 schema)
        cameras_used.append(channel.camera)

        # Warn if channel in simultaneous mode has no camera assigned
        if group.synchronization == SynchronizationMode.SIMULTANEOUS and channel.camera is None:
            errors.append(f"Channel '{entry.name}' has no camera assigned but is in simultaneous group '{group.name}'")

        # Warn if offset specified for sequential mode
        if group.synchronization == SynchronizationMode.SEQUENTIAL and entry.offset_us != 0:
            errors.append(
                f"Channel '{entry.name}' has offset_us={entry.offset_us} "
                f"but group '{group.name}' is sequential (offset will be ignored)"
            )

    # For simultaneous mode, all cameras must be different (excluding None which is already warned)
    if group.synchronization == SynchronizationMode.SIMULTANEOUS:
        non_null_cameras = [c for c in cameras_used if c is not None]
        if len(non_null_cameras) != len(set(non_null_cameras)):
            duplicate_cameras = [c for c in set(non_null_cameras) if non_null_cameras.count(c) > 1]
            errors.append(
                f"Group '{group.name}' uses simultaneous mode but has "
                f"multiple channels on same camera: {duplicate_cameras}"
            )

    return errors
