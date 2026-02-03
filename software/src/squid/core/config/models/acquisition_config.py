"""
Acquisition channel configuration models (v1.0 schema).

These models define user-facing acquisition settings organized as:
- general.yaml: Shared settings across all objectives
- {objective}.yaml: Objective-specific overrides with optional confocal_override

Ported from upstream commit 171aed9b with import path adjustments.
"""

import logging
from enum import Enum
from typing import List, Optional, Set, Union, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from squid.core.config.models.illumination_config import IlluminationChannelConfig

logger = logging.getLogger(__name__)


class CameraSettings(BaseModel):
    """Per-camera settings in an acquisition channel."""

    exposure_time_ms: float = Field(..., gt=0, description="Exposure time in milliseconds")
    gain_mode: float = Field(
        ...,
        ge=0,
        description="Gain setting (currently analog gain value)",
    )
    pixel_format: Optional[str] = Field(None, description="Pixel format (e.g., 'Mono12')")

    model_config = {"extra": "forbid"}


class ConfocalSettings(BaseModel):
    """Confocal-specific settings for objective-specific tuning."""

    illumination_iris: Optional[float] = Field(
        None, ge=0, le=100, description="Illumination iris aperture percentage (0-100)"
    )
    emission_iris: Optional[float] = Field(None, ge=0, le=100, description="Emission iris aperture percentage (0-100)")

    model_config = {"extra": "forbid"}


class IlluminationSettings(BaseModel):
    """Illumination configuration for an acquisition channel."""

    illumination_channel: Optional[str] = Field(
        None, description="Illumination channel name from illumination_channel_config (only in general.yaml)"
    )
    intensity: float = Field(..., ge=0, le=100, description="Illumination intensity percentage (0-100)")

    model_config = {"extra": "forbid"}


class AcquisitionChannelOverride(BaseModel):
    """Override settings for confocal mode (objective-specific)."""

    illumination_settings: Optional[IlluminationSettings] = Field(
        None, description="Override illumination settings for confocal mode"
    )
    camera_settings: Optional[CameraSettings] = Field(None, description="Override camera settings for confocal mode")
    confocal_settings: Optional[ConfocalSettings] = Field(None, description="Override confocal settings")

    model_config = {"extra": "forbid"}


class AcquisitionChannel(BaseModel):
    """A single acquisition channel configuration (v1.0 schema).

    Key design:
    - camera field is integer ID (references cameras.yaml), null for single-camera
    - filter_wheel resolved via hardware_bindings.yaml based on camera ID
    - z_offset_um at channel level
    - confocal_settings in confocal_override only
    """

    name: str = Field(..., min_length=1, description="Display name for this acquisition channel")
    enabled: bool = Field(True, description="Whether channel is enabled for selection in UI")
    display_color: str = Field(
        "#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$", description="Hex color for UI visualization"
    )

    # Camera assignment (optional for single-camera systems)
    camera: Optional[int] = Field(
        None, ge=1, description="Camera ID (references cameras.yaml). Null for single-camera systems."
    )
    camera_settings: CameraSettings = Field(..., description="Camera settings for this channel")

    # Filter wheel
    filter_wheel: Optional[str] = Field(
        None,
        description="Filter wheel override. 'auto' = use camera's hardware binding.",
    )
    filter_position: Optional[int] = Field(None, ge=1, description="Position in filter wheel")

    # Z offset
    z_offset_um: float = Field(0.0, description="Z offset in micrometers")

    # Illumination
    illumination_settings: IlluminationSettings = Field(..., description="Illumination configuration")

    # Confocal override
    confocal_override: Optional[AcquisitionChannelOverride] = Field(
        None, description="Confocal iris settings (objective-specific)"
    )

    model_config = {"extra": "forbid"}

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience properties for backward compatibility with ChannelMode API
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """Unique identifier derived from channel name."""
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
        return self.z_offset_um

    @property
    def emission_filter_position(self) -> Optional[int]:
        """Body filter wheel position (for backward compatibility)."""
        return self.filter_position

    @property
    def selected(self) -> bool:
        """Whether this channel is selected (maps to enabled)."""
        return self.enabled

    @property
    def color(self) -> str:
        """Display color (alias for display_color)."""
        return self.display_color

    @property
    def is_rgb(self) -> bool:
        """Whether this is an RGB channel."""
        return self.name.endswith("_RGB")

    @property
    def camera_sn(self) -> Optional[str]:
        """Camera serial number placeholder (for backward compatibility)."""
        return None

    def get_illumination_source_code(self, illumination_config: "IlluminationChannelConfig") -> int:
        """Get the illumination source code for the primary illumination channel."""
        ill_channel_name = self.primary_illumination_channel
        if not ill_channel_name:
            # Fall back to illumination_source if injected
            return getattr(self, "_illumination_source", 0)
        ill_channel = illumination_config.get_channel_by_name(ill_channel_name)
        if not ill_channel:
            return 0
        return illumination_config.get_source_code(ill_channel)

    def get_illumination_wavelength(self, illumination_config: "IlluminationChannelConfig") -> Optional[int]:
        """Get the wavelength for the primary illumination channel."""
        ill_channel_name = self.primary_illumination_channel
        if not ill_channel_name:
            return None
        ill_channel = illumination_config.get_channel_by_name(ill_channel_name)
        if not ill_channel:
            return None
        return ill_channel.wavelength_nm

    def get_effective_settings(self, confocal_mode: bool) -> "AcquisitionChannel":
        """Get effective settings based on confocal mode."""
        if not confocal_mode or not self.confocal_override:
            return self

        merged_illumination = self.illumination_settings
        if self.confocal_override.illumination_settings:
            merged_illumination = self.confocal_override.illumination_settings

        merged_camera = self.camera_settings
        if self.confocal_override.camera_settings:
            merged_camera = self.confocal_override.camera_settings

        return AcquisitionChannel(
            name=self.name,
            enabled=self.enabled,
            display_color=self.display_color,
            camera=self.camera,
            camera_settings=merged_camera,
            filter_wheel=self.filter_wheel,
            filter_position=self.filter_position,
            z_offset_um=self.z_offset_um,
            illumination_settings=merged_illumination,
            confocal_override=self.confocal_override,
        )


class GeneralChannelConfig(BaseModel):
    """general.yaml - shared settings across all objectives."""

    version: Union[int, float] = Field(1, description="Configuration format version")
    channels: List[AcquisitionChannel] = Field(default_factory=list, description="List of acquisition channels")
    channel_groups: List["ChannelGroup"] = Field(
        default_factory=list, description="Channel groups for multi-camera acquisition (v1.0+)"
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
    """{objective}.yaml - objective-specific overrides."""

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
    """Merge general.yaml and objective.yaml into final acquisition channels."""
    merged_channels = []

    for gen_channel in general.channels:
        obj_channel = objective.get_channel_by_name(gen_channel.name)

        if obj_channel is None:
            merged_channels.append(gen_channel)
            continue

        merged_illumination = IlluminationSettings(
            illumination_channel=gen_channel.illumination_settings.illumination_channel,
            intensity=obj_channel.illumination_settings.intensity,
        )

        merged_camera = CameraSettings(
            exposure_time_ms=obj_channel.camera_settings.exposure_time_ms,
            gain_mode=obj_channel.camera_settings.gain_mode,
            pixel_format=obj_channel.camera_settings.pixel_format,
        )

        merged_channel = AcquisitionChannel(
            name=gen_channel.name,
            enabled=gen_channel.enabled,
            display_color=gen_channel.display_color,
            camera=gen_channel.camera,
            camera_settings=merged_camera,
            filter_wheel=gen_channel.filter_wheel,
            filter_position=gen_channel.filter_position,
            z_offset_um=gen_channel.z_offset_um,
            illumination_settings=merged_illumination,
            confocal_override=obj_channel.confocal_override,
        )
        merged_channels.append(merged_channel)

    return merged_channels


def validate_illumination_references(
    config: GeneralChannelConfig,
    illumination_config: "IlluminationChannelConfig",
) -> List[str]:
    """Validate that all illumination_channel references exist in illumination config."""
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
    """Get all unique illumination channel names referenced in a config."""
    names: Set[str] = set()
    for acq_channel in config.channels:
        if acq_channel.illumination_settings.illumination_channel:
            names.add(acq_channel.illumination_settings.illumination_channel)
    return names


class AcquisitionOutputConfig(BaseModel):
    """Output format for acquisition settings saved alongside acquired images."""

    version: Union[int, float] = Field(1, description="Configuration format version")
    objective: str = Field(..., description="Objective used for acquisition")
    confocal_mode: bool = Field(False, description="Whether confocal mode was active")
    channels: List[AcquisitionChannel] = Field(default_factory=list, description="List of acquisition channels used")

    model_config = {"extra": "forbid"}


# ─────────────────────────────────────────────────────────────────────────────
# Channel Groups (v1.0)
# ─────────────────────────────────────────────────────────────────────────────


class SynchronizationMode(str, Enum):
    """Synchronization mode for channel groups."""

    SIMULTANEOUS = "simultaneous"
    SEQUENTIAL = "sequential"


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
    """A group of channels to be acquired together."""

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
        """Get channels sorted by trigger offset."""
        return sorted(self.channels, key=lambda c: c.offset_us)


def validate_channel_group(
    group: ChannelGroup,
    channels: List[AcquisitionChannel],
) -> List[str]:
    """Validate channel group configuration."""
    errors = []
    cameras_used: List[Optional[int]] = []

    for entry in group.channels:
        channel = next((c for c in channels if c.name == entry.name), None)
        if channel is None:
            errors.append(f"Channel '{entry.name}' not found in channels list")
            continue

        cameras_used.append(channel.camera)

        if group.synchronization == SynchronizationMode.SIMULTANEOUS and channel.camera is None:
            errors.append(f"Channel '{entry.name}' has no camera ID but is in simultaneous group '{group.name}'")

        if group.synchronization == SynchronizationMode.SEQUENTIAL and entry.offset_us != 0:
            errors.append(
                f"Channel '{entry.name}' has offset_us={entry.offset_us} "
                f"but group '{group.name}' is sequential (offset will be ignored)"
            )

    if group.synchronization == SynchronizationMode.SIMULTANEOUS:
        non_null_cameras = [c for c in cameras_used if c is not None]
        if len(non_null_cameras) != len(set(non_null_cameras)):
            duplicate_cameras = [c for c in set(non_null_cameras) if non_null_cameras.count(c) > 1]
            errors.append(
                f"Group '{group.name}' uses simultaneous mode but has "
                f"multiple channels on same camera ID: {duplicate_cameras}"
            )

    return errors
