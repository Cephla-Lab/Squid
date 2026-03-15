"""Quality Control system for acquisition.

Collects per-FOV metrics during acquisition, stores them per-timepoint,
and applies configurable policies to flag FOVs and optionally pause.
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

from control.core.job_processing import CaptureInfo, Job, JobImage


def calculate_focus_score(image: np.ndarray, method: str = "laplacian_variance") -> float:
    """Calculate focus score for an image.

    Args:
        image: 2D grayscale or multichannel image (first channel used if multichannel).
        method: One of "laplacian_variance", "normalized_variance",
                "gradient_magnitude", "fft_high_freq".

    Returns:
        Focus score — higher means more in focus.
    """
    if image.ndim == 3:
        image = image[:, :, 0]

    if method == "laplacian_variance":
        laplacian = cv2.Laplacian(image, cv2.CV_64F)
        return float(laplacian.var())

    elif method == "normalized_variance":
        mean = image.mean()
        if mean == 0:
            return 0.0
        return float(image.var() / mean)

    elif method == "gradient_magnitude":
        img_f = image.astype(np.float64)
        gy = np.gradient(img_f, axis=0)
        gx = np.gradient(img_f, axis=1)
        return float(np.sqrt(gx**2 + gy**2).mean())

    elif method == "fft_high_freq":
        fft = np.fft.fft2(image.astype(np.float64))
        fft_shift = np.fft.fftshift(fft)
        h, w = image.shape[:2]
        cy, cx = h // 2, w // 2
        mask_size = min(h, w) // 8
        fft_shift[cy - mask_size : cy + mask_size, cx - mask_size : cx + mask_size] = 0
        return float(np.abs(fft_shift).mean())

    else:
        raise ValueError(f"Unknown focus method: {method}")


@dataclass(frozen=True)
class FOVIdentifier:
    """Identifies a single FOV within an acquisition."""

    region_id: str
    fov_index: int


@dataclass
class FOVMetrics:
    """QC metrics for a single FOV."""

    fov_id: FOVIdentifier
    timestamp: float
    z_position_um: float

    focus_score: Optional[float] = None
    laser_af_displacement_um: Optional[float] = None
    z_diff_from_last_timepoint_um: Optional[float] = None


@dataclass
class QCConfig:
    """Configuration for QC metrics collection."""

    enabled: bool = False
    calculate_focus_score: bool = True
    record_laser_af_displacement: bool = False
    calculate_z_diff_from_last_timepoint: bool = False
    focus_score_method: str = "laplacian_variance"


@dataclass
class QCPolicyConfig:
    """Configuration for QC policy decisions."""

    enabled: bool = False
    check_after_timepoint: bool = True
    focus_score_min: Optional[float] = None
    z_drift_max_um: Optional[float] = None
    detect_outliers: bool = False
    outlier_metric: str = "focus_score"
    outlier_std_threshold: float = 2.0
    pause_if_any_flagged: bool = True
