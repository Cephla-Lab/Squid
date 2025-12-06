"""
Acquisition configuration models.

Provides validated, immutable configuration objects for acquisitions.
Replaces scattered setters and mutable state with clear, typed configs.

Usage:
    from squid.config.acquisition import AcquisitionConfig, GridScanConfig

    config = AcquisitionConfig(
        experiment_id="exp_001",
        output_path="/data/experiments",
        grid=GridScanConfig(nx=10, ny=10),
        channels=[
            ChannelConfig(name="DAPI", exposure_ms=100),
            ChannelConfig(name="GFP", exposure_ms=200),
        ]
    )

    # Save for reproducibility
    config_json = config.model_dump_json(indent=2)

    # Restore from file
    config = AcquisitionConfig.model_validate_json(json_str)
"""

from typing import List, Optional
from pydantic import BaseModel, field_validator


class GridScanConfig(BaseModel):
    """
    Configuration for grid-based scanning.

    Attributes:
        nx: Number of positions in X
        ny: Number of positions in Y
        nz: Number of Z slices
        delta_x_mm: Step size in X (mm)
        delta_y_mm: Step size in Y (mm)
        delta_z_um: Step size in Z (um)
    """

    nx: int = 1
    ny: int = 1
    nz: int = 1
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    delta_z_um: float = 1.5

    model_config = {"frozen": True}

    @field_validator("nx", "ny", "nz")
    @classmethod
    def must_be_positive(cls, v, info):
        if v < 1:
            raise ValueError(f"{info.field_name} must be at least 1")
        return v


class TimelapseConfig(BaseModel):
    """
    Configuration for timelapse acquisition.

    Attributes:
        n_timepoints: Number of timepoints
        interval_seconds: Time between timepoints
    """

    n_timepoints: int = 1
    interval_seconds: float = 0

    model_config = {"frozen": True}

    @field_validator("n_timepoints")
    @classmethod
    def must_be_positive(cls, v):
        if v < 1:
            raise ValueError("n_timepoints must be at least 1")
        return v

    @field_validator("interval_seconds")
    @classmethod
    def must_be_non_negative(cls, v):
        if v < 0:
            raise ValueError("interval_seconds must be non-negative")
        return v


class ChannelConfig(BaseModel):
    """
    Configuration for a single acquisition channel.

    Attributes:
        name: Channel name (e.g., "DAPI", "GFP")
        exposure_ms: Exposure time in milliseconds
        analog_gain: Optional analog gain
        illumination_source: Optional illumination source name
        z_offset_um: Z offset for this channel (um)
    """

    name: str
    exposure_ms: float
    analog_gain: Optional[float] = None
    illumination_source: Optional[str] = None
    z_offset_um: float = 0

    model_config = {"frozen": True}

    @field_validator("exposure_ms")
    @classmethod
    def exposure_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("exposure_ms must be positive")
        return v


class AutofocusConfig(BaseModel):
    """
    Configuration for autofocus.

    Attributes:
        enabled: Whether autofocus is enabled
        algorithm: Autofocus algorithm name
        n_steps: Number of z steps to scan
        step_size_um: Step size in um
        every_n_fovs: Run autofocus every N FOVs (0 = only at start)
    """

    enabled: bool = False
    algorithm: str = "brenner_gradient"
    n_steps: int = 10
    step_size_um: float = 1.5
    every_n_fovs: int = 3

    model_config = {"frozen": True}


class AcquisitionConfig(BaseModel):
    """
    Complete acquisition configuration.

    This replaces the scattered configuration across AcquisitionParameters,
    _def.py globals, and controller setters with a single, validated,
    immutable configuration object.

    Attributes:
        experiment_id: Unique identifier for this experiment
        output_path: Directory to save images
        grid: Grid scanning configuration
        timelapse: Timelapse configuration
        channels: List of channels to acquire
        autofocus: Optional autofocus configuration
    """

    experiment_id: str
    output_path: str
    grid: GridScanConfig
    timelapse: TimelapseConfig
    channels: List[ChannelConfig]
    autofocus: Optional[AutofocusConfig] = None

    model_config = {"frozen": True}

    @field_validator("channels")
    @classmethod
    def must_have_channels(cls: type, v: List[ChannelConfig]) -> List[ChannelConfig]:
        if not v:
            raise ValueError("Must have at least one channel")
        return v

    def total_images(self) -> int:
        """Calculate total number of images in acquisition."""
        return (
            self.grid.nx
            * self.grid.ny
            * self.grid.nz
            * self.timelapse.n_timepoints
            * len(self.channels)
        )
