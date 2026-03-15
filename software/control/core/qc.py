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
