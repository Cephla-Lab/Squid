"""Pure analysis for the hot-pixel characterization tool.

No Qt and no hardware imports at module top level. matplotlib is imported lazily
inside the render functions so this module imports cleanly in headless contexts.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from squid.config import CameraPixelFormat


class DefectType(str, enum.Enum):
    HOT_STATISTICAL = "hot_statistical"
    HOT_ABSOLUTE = "hot_absolute"
    STUCK_HIGH = "stuck_high"
    DEAD_LOW = "dead_low"


@dataclass(frozen=True)
class DefectThresholds:
    sigma_n: float = 5.0
    abs_threshold_dn: Optional[int] = None
    stuck_high_frac: float = 0.99
    dead_max_dn: int = 1


_BIT_DEPTH_BY_FORMAT = {
    CameraPixelFormat.MONO8: 8,
    CameraPixelFormat.MONO10: 10,
    CameraPixelFormat.MONO12: 12,
    CameraPixelFormat.MONO14: 14,
    CameraPixelFormat.MONO16: 16,
}


def max_value_for_pixel_format(pixel_format: CameraPixelFormat) -> int:
    """Maximum DN for a MONO pixel format. Raises ValueError on color/Bayer formats."""
    if pixel_format not in _BIT_DEPTH_BY_FORMAT:
        raise ValueError(
            f"max_value_for_pixel_format only supports MONO formats, got {pixel_format}. "
            "Color/Bayer formats must be handled by the caller."
        )
    return (1 << _BIT_DEPTH_BY_FORMAT[pixel_format]) - 1
