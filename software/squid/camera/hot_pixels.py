"""Pure analysis for the hot-pixel characterization tool.

No Qt and no hardware imports at module top level. matplotlib is imported lazily
inside the render functions so this module imports cleanly in headless contexts.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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


def darkness_check(
    mean_frame: np.ndarray, black_level: float, max_value: int, max_fraction: float = 0.25
) -> Optional[str]:
    """Warn if the averaged frame is probably not dark (likely a light leak)."""
    median = float(np.median(mean_frame))
    threshold = black_level + max_fraction * max_value
    if median > threshold:
        return (
            f"Dark-frame median {median:.1f} DN exceeds {threshold:.1f} DN "
            f"(black level {black_level:.1f} + {max_fraction:.0%} of full scale {max_value}). "
            "The sensor may not be in the dark — block all light before running a hot-pixel test."
        )
    return None


@dataclass
class DefectResult:
    stats: FrameStats
    thresholds: DefectThresholds
    max_value: int
    masks: Dict[DefectType, np.ndarray]
    statistical_threshold_dn: float
    flagged_values: Dict[Tuple[int, int], float] = field(default_factory=dict)

    @property
    def combined_mask(self) -> np.ndarray:
        out = None
        for m in self.masks.values():
            out = m.copy() if out is None else (out | m)
        return out

    def coords(self, defect_type: DefectType) -> np.ndarray:
        ys, xs = np.where(self.masks[defect_type])
        return np.column_stack([xs, ys])  # (N, 2) as (x, y)

    def count(self, defect_type: DefectType) -> int:
        return int(self.masks[defect_type].sum())

    def combined_count(self) -> int:
        return int(self.combined_mask.sum())


def detect_defects(
    mean_frame: np.ndarray,
    min_proj: np.ndarray,
    max_proj: np.ndarray,
    max_value: int,
    thresholds: DefectThresholds,
    black_level: float = 0.0,
) -> DefectResult:
    mean_frame = np.asarray(mean_frame, dtype=np.float64)
    min_proj = np.asarray(min_proj)
    max_proj = np.asarray(max_proj)

    stats = compute_frame_stats(mean_frame)
    stat_thresh = stats.median + thresholds.sigma_n * stats.sigma_robust

    hot_statistical = mean_frame > stat_thresh
    if thresholds.abs_threshold_dn is not None:
        hot_absolute = mean_frame > thresholds.abs_threshold_dn
    else:
        hot_absolute = np.zeros(mean_frame.shape, dtype=bool)
    stuck_high = min_proj >= thresholds.stuck_high_frac * max_value
    # Only meaningful when there is a real dark floor above the dead threshold.
    dead_low = (max_proj <= thresholds.dead_max_dn) & (stats.median > thresholds.dead_max_dn)

    masks = {
        DefectType.HOT_STATISTICAL: hot_statistical,
        DefectType.HOT_ABSOLUTE: hot_absolute,
        DefectType.STUCK_HIGH: stuck_high,
        DefectType.DEAD_LOW: dead_low,
    }

    combined = hot_statistical | hot_absolute | stuck_high | dead_low
    ys, xs = np.where(combined)
    flagged_values = {(int(x), int(y)): float(mean_frame[y, x]) for x, y in zip(xs, ys)}

    return DefectResult(
        stats=stats,
        thresholds=thresholds,
        max_value=max_value,
        masks=masks,
        statistical_threshold_dn=stat_thresh,
        flagged_values=flagged_values,
    )


@dataclass
class ConditionResult:
    temperature_c: Optional[float]
    actual_temperature_c: Optional[float]
    exposure_ms: float
    n_frames: int
    result: DefectResult


@dataclass
class PixelDefect:
    x: int
    y: int
    types: List[str]
    mean_dark_dn: float
    conditions: List[str]


@dataclass
class SweepSummary:
    pixels: List[PixelDefect]
    per_condition: List[dict]


def condition_label(temperature_c: Optional[float], exposure_ms: float) -> str:
    temp = "ambient" if temperature_c is None else f"{temperature_c:g}C"
    return f"T={temp},exp={exposure_ms:g}ms"


def aggregate_sweep(results: List[ConditionResult]) -> SweepSummary:
    pixel_map: Dict[Tuple[int, int], dict] = {}
    per_condition: List[dict] = []

    for c in results:
        label = condition_label(c.temperature_c, c.exposure_ms)
        per_condition.append(
            {
                "label": label,
                "temperature_c": c.temperature_c,
                "actual_temperature_c": c.actual_temperature_c,
                "exposure_ms": c.exposure_ms,
                "n_frames": c.n_frames,
                "counts": {dt.value: c.result.count(dt) for dt in DefectType},
                "combined_count": c.result.combined_count(),
                "median_dn": c.result.stats.median,
                "sigma_robust_dn": c.result.stats.sigma_robust,
                "min_dn": c.result.stats.min,
                "max_dn": c.result.stats.max,
                "statistical_threshold_dn": c.result.statistical_threshold_dn,
            }
        )
        for dt in DefectType:
            for x, y in c.result.coords(dt):
                key = (int(x), int(y))
                entry = pixel_map.setdefault(key, {"types": set(), "conditions": [], "dn": 0.0})
                entry["types"].add(dt.value)
                if label not in entry["conditions"]:
                    entry["conditions"].append(label)
                entry["dn"] = max(entry["dn"], c.result.flagged_values.get(key, 0.0))

    pixels = [
        PixelDefect(
            x=x,
            y=y,
            types=sorted(e["types"]),
            mean_dark_dn=e["dn"],
            conditions=e["conditions"],
        )
        for (x, y), e in sorted(pixel_map.items())
    ]
    return SweepSummary(pixels=pixels, per_condition=per_condition)
