"""Immutable acquisition configuration dataclasses.

These frozen dataclasses provide a single source of truth for acquisition parameters,
replacing the scattered state variables in MultiPointController with a structured,
immutable configuration that can be validated and passed atomically.
"""

from dataclasses import dataclass, replace
from typing import Optional, Tuple

import _def


@dataclass(frozen=True)
class GridConfig:
    """Configuration for XY grid acquisition.

    Attributes:
        nx: Number of FOVs in X direction.
        ny: Number of FOVs in Y direction.
        dx_mm: Step size in X direction (millimeters).
        dy_mm: Step size in Y direction (millimeters).
    """

    nx: int = 1
    ny: int = 1
    dx_mm: float = _def.Acquisition.DX
    dy_mm: float = _def.Acquisition.DY

    def __post_init__(self) -> None:
        """Validate grid configuration."""
        if self.nx < 1:
            raise ValueError(f"nx must be >= 1, got {self.nx}")
        if self.ny < 1:
            raise ValueError(f"ny must be >= 1, got {self.ny}")
        if self.dx_mm <= 0:
            raise ValueError(f"dx_mm must be > 0, got {self.dx_mm}")
        if self.dy_mm <= 0:
            raise ValueError(f"dy_mm must be > 0, got {self.dy_mm}")

    @property
    def total_positions(self) -> int:
        """Total number of positions in the grid."""
        return self.nx * self.ny


@dataclass(frozen=True)
class ZStackConfig:
    """Configuration for Z-stack acquisition.

    Attributes:
        nz: Number of Z slices.
        delta_z_um: Step size in Z direction (micrometers).
        stacking_direction: Direction of Z-stack ("FROM BOTTOM", "FROM CENTER", "FROM TOP").
        z_range: Optional (min_z, max_z) range constraints in millimeters.
        use_piezo: Whether to use piezo for Z movement.
    """

    nz: int = 1
    delta_z_um: float = _def.Acquisition.DZ
    stacking_direction: str = _def.Z_STACKING_CONFIG
    z_range: Optional[Tuple[float, float]] = None
    use_piezo: bool = _def.MULTIPOINT_USE_PIEZO_FOR_ZSTACKS

    def __post_init__(self) -> None:
        """Validate Z-stack configuration."""
        if self.nz < 1:
            raise ValueError(f"nz must be >= 1, got {self.nz}")
        if self.delta_z_um <= 0:
            raise ValueError(f"delta_z_um must be > 0, got {self.delta_z_um}")
        valid_directions = ("FROM BOTTOM", "FROM CENTER", "FROM TOP")
        if self.stacking_direction not in valid_directions:
            raise ValueError(
                f"stacking_direction must be one of {valid_directions}, "
                f"got '{self.stacking_direction}'"
            )
        if self.z_range is not None:
            if len(self.z_range) != 2:
                raise ValueError("z_range must be a tuple of (min_z, max_z)")
            if self.z_range[0] > self.z_range[1]:
                raise ValueError(
                    f"z_range min ({self.z_range[0]}) must be <= max ({self.z_range[1]})"
                )

    @property
    def delta_z_mm(self) -> float:
        """Step size in millimeters."""
        return self.delta_z_um / 1000.0

    @property
    def total_range_um(self) -> float:
        """Total Z range in micrometers."""
        if self.nz <= 1:
            return 0.0
        return self.delta_z_um * (self.nz - 1)


@dataclass(frozen=True)
class TimingConfig:
    """Configuration for time-lapse acquisition.

    Attributes:
        nt: Number of time points.
        dt_s: Time interval between time points (seconds).
    """

    nt: int = 1
    dt_s: float = 0.0

    def __post_init__(self) -> None:
        """Validate timing configuration."""
        if self.nt < 1:
            raise ValueError(f"nt must be >= 1, got {self.nt}")
        if self.dt_s < 0:
            raise ValueError(f"dt_s must be >= 0, got {self.dt_s}")

    @property
    def is_time_lapse(self) -> bool:
        """Whether this is a time-lapse acquisition."""
        return self.nt > 1

    @property
    def total_duration_s(self) -> float:
        """Estimated total duration in seconds (excludes capture time)."""
        if self.nt <= 1:
            return 0.0
        return self.dt_s * (self.nt - 1)


@dataclass(frozen=True)
class FocusConfig:
    """Configuration for autofocus during acquisition.

    Attributes:
        do_contrast_af: Enable software contrast-based autofocus.
        do_reflection_af: Enable laser/reflection-based autofocus.
        gen_focus_map: Generate focus map before acquisition.
        use_manual_focus_map: Use a pre-defined manual focus map.
        focus_map_dx_mm: X spacing for auto-generated focus map.
        focus_map_dy_mm: Y spacing for auto-generated focus map.
    """

    do_contrast_af: bool = False
    do_reflection_af: bool = False
    gen_focus_map: bool = False
    use_manual_focus_map: bool = False
    focus_map_dx_mm: float = 3.0
    focus_map_dy_mm: float = 3.0

    def __post_init__(self) -> None:
        """Validate focus configuration."""
        if self.focus_map_dx_mm <= 0:
            raise ValueError(f"focus_map_dx_mm must be > 0, got {self.focus_map_dx_mm}")
        if self.focus_map_dy_mm <= 0:
            raise ValueError(f"focus_map_dy_mm must be > 0, got {self.focus_map_dy_mm}")

    @property
    def any_autofocus_enabled(self) -> bool:
        """Whether any autofocus method is enabled."""
        return self.do_contrast_af or self.do_reflection_af


@dataclass(frozen=True)
class AcquisitionConfig:
    """Complete acquisition configuration combining all sub-configs.

    This is the single source of truth for acquisition parameters.
    It is immutable - to change settings, create a new instance using
    the `with_updates()` method.

    Attributes:
        grid: XY grid configuration.
        zstack: Z-stack configuration.
        timing: Time-lapse timing configuration.
        focus: Autofocus configuration.
        selected_channels: Tuple of channel names to acquire.
        display_resolution_scaling: Scaling factor for display images.
        skip_saving: Whether to skip saving images to disk.
        use_fluidics: Whether to use fluidics for buffer changes.
        xy_mode: Position mode ("Current Position", "Select Wells", etc.).
    """

    grid: GridConfig = None  # type: ignore[assignment]
    zstack: ZStackConfig = None  # type: ignore[assignment]
    timing: TimingConfig = None  # type: ignore[assignment]
    focus: FocusConfig = None  # type: ignore[assignment]
    selected_channels: Tuple[str, ...] = ()
    display_resolution_scaling: float = _def.Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
    skip_saving: bool = False
    use_fluidics: bool = False
    xy_mode: str = "Current Position"
    acquisition_order: str = "channel_first"  # "channel_first" or "z_first"

    def __post_init__(self) -> None:
        """Initialize default sub-configs if not provided."""
        # Use object.__setattr__ to work around frozen dataclass
        if self.grid is None:
            object.__setattr__(self, "grid", GridConfig())
        if self.zstack is None:
            object.__setattr__(self, "zstack", ZStackConfig())
        if self.timing is None:
            object.__setattr__(self, "timing", TimingConfig())
        if self.focus is None:
            object.__setattr__(self, "focus", FocusConfig())

    def validate(self) -> None:
        """Validate all configuration constraints.

        Raises:
            ValueError: If any configuration is invalid.
        """
        # Sub-configs validate themselves in __post_init__
        # Check cross-config constraints here
        if self.display_resolution_scaling <= 0 or self.display_resolution_scaling > 1:
            raise ValueError(
                f"display_resolution_scaling must be in (0, 1], "
                f"got {self.display_resolution_scaling}"
            )

        # Warn about potentially conflicting settings
        if self.focus.do_reflection_af and self.focus.do_contrast_af:
            # Both can be used together, but log at debug level
            pass

        if self.focus.gen_focus_map and self.focus.use_manual_focus_map:
            raise ValueError(
                "Cannot both generate focus map and use manual focus map"
            )

    def with_updates(self, **kwargs) -> "AcquisitionConfig":
        """Create a new config with updated values.

        Supports nested updates using dot notation keys:
        - "grid.nx" updates grid.nx
        - "zstack.nz" updates zstack.nz

        Args:
            **kwargs: Key-value pairs to update.

        Returns:
            New AcquisitionConfig with updated values.

        Example:
            >>> config = AcquisitionConfig()
            >>> new_config = config.with_updates(
            ...     **{"grid.nx": 5, "zstack.nz": 10}
            ... )
        """
        # Collect updates for each sub-config
        grid_updates = {}
        zstack_updates = {}
        timing_updates = {}
        focus_updates = {}
        top_level_updates = {}

        for key, value in kwargs.items():
            if "." in key:
                prefix, suffix = key.split(".", 1)
                if prefix == "grid":
                    grid_updates[suffix] = value
                elif prefix == "zstack":
                    zstack_updates[suffix] = value
                elif prefix == "timing":
                    timing_updates[suffix] = value
                elif prefix == "focus":
                    focus_updates[suffix] = value
                else:
                    raise ValueError(f"Unknown config prefix: {prefix}")
            else:
                top_level_updates[key] = value

        # Create updated sub-configs
        new_grid = replace(self.grid, **grid_updates) if grid_updates else self.grid
        new_zstack = replace(self.zstack, **zstack_updates) if zstack_updates else self.zstack
        new_timing = replace(self.timing, **timing_updates) if timing_updates else self.timing
        new_focus = replace(self.focus, **focus_updates) if focus_updates else self.focus

        # Create new config
        return replace(
            self,
            grid=new_grid,
            zstack=new_zstack,
            timing=new_timing,
            focus=new_focus,
            **top_level_updates,
        )

    @property
    def total_images(self) -> int:
        """Estimate total number of images to capture."""
        num_channels = len(self.selected_channels)
        return (
            self.grid.total_positions
            * self.zstack.nz
            * self.timing.nt
            * max(1, num_channels)
        )

    @classmethod
    def from_defaults(cls) -> "AcquisitionConfig":
        """Create a config with all default values."""
        return cls(
            grid=GridConfig(),
            zstack=ZStackConfig(),
            timing=TimingConfig(),
            focus=FocusConfig(),
        )
