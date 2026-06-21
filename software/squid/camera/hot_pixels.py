"""Pure analysis for the hot-pixel characterization tool.

No Qt and no hardware imports at module top level. matplotlib is imported lazily
inside the render functions so this module imports cleanly in headless contexts.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

import numpy as np

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


@dataclass(frozen=True)
class FrameStats:
    mean: float
    median: float
    sigma_robust: float
    min: float
    max: float


def compute_frame_stats(mean_frame: np.ndarray) -> FrameStats:
    """Robust statistics of an averaged dark frame.

    Uses median + 1.4826*MAD so a population of hot pixels cannot inflate the scale
    and hide itself above a plain mean+std threshold.
    """
    flat = np.asarray(mean_frame, dtype=np.float64).ravel()
    median = float(np.median(flat))
    mad = float(np.median(np.abs(flat - median)))
    return FrameStats(
        mean=float(flat.mean()),
        median=median,
        sigma_robust=1.4826 * mad,
        min=float(flat.min()),
        max=float(flat.max()),
    )
