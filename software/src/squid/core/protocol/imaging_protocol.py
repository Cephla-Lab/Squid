"""
Imaging protocol for V2 protocol schema.

Defines reusable imaging protocols that can be referenced by name
from ImagingStep in rounds. An imaging protocol specifies the per-tile
acquisition procedure: which channels to use and in what order, z-stack
parameters, z/channel interleaving, and autofocus strategy.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


class ChannelConfigOverride(BaseModel):
    """Per-channel overrides for an imaging protocol.

    Allows overriding exposure, gain, intensity, and z-offset for specific channels.

    Example:
        name: Cy5
        exposure_time_ms: 200
        illumination_intensity: 80
    """

    name: str
    exposure_time_ms: Optional[float] = None
    analog_gain: Optional[float] = None
    illumination_intensity: Optional[float] = None
    z_offset_um: float = 0.0

    @field_validator("exposure_time_ms")
    @classmethod
    def validate_exposure_time(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("exposure_time_ms must be > 0")
        return v

    @field_validator("illumination_intensity")
    @classmethod
    def validate_illumination_intensity(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v < 0 or v > 100):
            raise ValueError("illumination_intensity must be between 0 and 100")
        return v


class ZStackConfig(BaseModel):
    """Z-stack configuration for imaging.

    Attributes:
        planes: Number of z-planes to acquire
        step_um: Step size between planes in microns
        direction: Stacking direction relative to focus position
    """

    planes: int = 1
    step_um: float = 0.5
    direction: Literal["from_center", "from_bottom", "from_top"] = "from_center"

    @field_validator("planes")
    @classmethod
    def validate_planes(cls, v: int) -> int:
        if v < 1:
            raise ValueError("planes must be >= 1")
        return v

    @field_validator("step_um")
    @classmethod
    def validate_step_um(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("step_um must be > 0")
        return v


class FocusConfig(BaseModel):
    """Focus settings for imaging.

    Focus method semantics:
    - "laser": Uses hardware laser autofocus (do_reflection_af=True)
    - "contrast": Uses software contrast-based autofocus (do_contrast_af=True)
    - "none": No autofocus

    Attributes:
        enabled: Whether autofocus is enabled
        method: Autofocus method to use
        channel: Channel to use for contrast AF (if method="contrast")
        interval_fovs: Run autofocus every N FOVs (passed to AutofocusExecutor.configure)
    """

    enabled: bool = False
    method: Literal["laser", "contrast", "none"] = "laser"
    channel: Optional[str] = None  # For contrast AF
    interval_fovs: int = 1  # Passed to AutofocusExecutor.configure(fovs_per_af=...)

    @field_validator("interval_fovs")
    @classmethod
    def validate_interval_fovs(cls, v: int) -> int:
        if v < 1:
            raise ValueError("interval_fovs must be >= 1")
        return v


class ImagingProtocol(BaseModel):
    """Named imaging protocol.

    Defines the per-tile acquisition procedure for a step, including which
    channels to use and in what order, z-stack parameters, z/channel
    interleaving, and autofocus strategy.

    The channels list order defines the acquisition sequence (e.g.,
    730->638->561->405 for photobleaching reasons).

    Attributes:
        description: Human-readable description
        channels: Ordered list of channel names or ChannelConfigOverride objects.
            Order IS acquisition order.
        z_stack: Z-stack configuration
        acquisition_order: Channel/z interleaving mode.
            "channel_first" = all channels per z-plane (default).
            "z_first" = all z-planes per channel.
        focus: Autofocus configuration
        skip_saving: Whether to skip saving images to disk

    Example:
        fish_standard:
          description: "Standard FISH imaging"
          channels:
            - "Fluorescence 730 nm Ex"
            - "Fluorescence 638 nm Ex"
            - name: Cy5
              exposure_time_ms: 200
          z_stack:
            planes: 5
            step_um: 0.5
          acquisition_order: channel_first
          focus:
            enabled: true
            method: laser
    """

    description: str = ""
    channels: List[Union[str, ChannelConfigOverride]] = Field(default_factory=list)
    z_stack: ZStackConfig = Field(default_factory=ZStackConfig)
    acquisition_order: Literal["channel_first", "z_first"] = "channel_first"
    focus: FocusConfig = Field(default_factory=FocusConfig)
    skip_saving: bool = False
    save_format: Optional[str] = None

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, v: List[Union[str, ChannelConfigOverride]]) -> List[Union[str, ChannelConfigOverride]]:
        if not v:
            raise ValueError("channels must not be empty")
        return v

    def get_channel_names(self) -> List[str]:
        """Get list of channel names from channels list."""
        return [ch if isinstance(ch, str) else ch.name for ch in self.channels]

    def get_channel_overrides(self) -> List[ChannelConfigOverride]:
        """Get list of channel overrides (only entries with explicit overrides)."""
        return [ch for ch in self.channels if isinstance(ch, ChannelConfigOverride)]
