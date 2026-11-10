"""Core data types for analysis-driven acquisition.

No Qt, no hardware. These types are the contract between the three stages of the
pipeline: reading a finished acquisition (:mod:`source`), analysing each FOV
(:mod:`analyzers`), and emitting a new acquisition (:mod:`emit`).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class FovRecord:
    """One field of view of a completed acquisition, with everything needed to
    place its pixels in stage space.

    ``x_mm``/``y_mm``/``z_mm`` are the *actual* stage position recorded at capture
    time (from the per-timepoint ``coordinates.csv``), not the planned one.
    """

    region: str
    fov: int
    time_point: int
    z_level: int
    x_mm: float
    y_mm: float
    z_mm: Optional[float]
    pixel_size_um: float
    width_px: int
    height_px: int

    @property
    def key(self) -> Tuple[int, str, int, int]:
        return (self.time_point, self.region, self.fov, self.z_level)


@dataclass(frozen=True)
class PixelDetection:
    """Something an analyzer found, in image pixel coordinates.

    Analyzers never do coordinate math -- they return these and
    :mod:`geometry` converts them. ``measured`` is False when the detection was
    inferred rather than directly observed (e.g. a missing node filled in from a
    fitted lattice).
    """

    px: float
    py: float
    radius_px: float = 0.0
    score: float = 1.0
    measured: bool = True
    label: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    """A stage coordinate to image next."""

    x_mm: float
    y_mm: float
    z_mm: Optional[float]
    label: str
    score: float = 1.0
    source_region: str = ""
    source_fov: Optional[int] = None
    measured: bool = True
    metrics: Dict[str, Any] = field(default_factory=dict)

    def as_fov(self) -> List[float]:
        """The [x, y] or [x, y, z] list shape used by ScanCoordinates and coordinates.csv."""
        if self.z_mm is None:
            return [float(self.x_mm), float(self.y_mm)]
        return [float(self.x_mm), float(self.y_mm), float(self.z_mm)]
