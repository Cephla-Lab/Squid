"""Qt-free capture orchestration for the hot-pixel tool. Drives any AbstractCamera."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np

from squid.abc import AbstractCamera
from squid.config import CameraPixelFormat
from squid.camera import hot_pixels


@dataclass
class DarkStack:
    mean: np.ndarray
    min_proj: np.ndarray
    max_proj: np.ndarray
    n_frames: int


def _trigger_and_read(camera: AbstractCamera, max_none_retries: int) -> Optional[np.ndarray]:
    for _ in range(max_none_retries + 1):
        if camera.get_ready_for_trigger():
            camera.send_trigger()
        frame = camera.read_camera_frame()
        if frame is not None:
            return frame.frame
    return None


def capture_dark_stack(
    camera: AbstractCamera,
    exposure_ms: float,
    n_frames: int,
    warmup_frames: int = 2,
    max_none_retries: int = 5,
    should_stop: Optional[Callable[[], bool]] = None,
    on_frame: Optional[Callable[[int], None]] = None,
) -> Optional[DarkStack]:
    camera.set_exposure_time(exposure_ms)

    for _ in range(warmup_frames):
        if should_stop and should_stop():
            return None
        _trigger_and_read(camera, max_none_retries)

    acc: Optional[np.ndarray] = None
    min_proj: Optional[np.ndarray] = None
    max_proj: Optional[np.ndarray] = None
    captured = 0
    while captured < n_frames:
        if should_stop and should_stop():
            return None
        frame = _trigger_and_read(camera, max_none_retries)
        if frame is None:
            continue
        f64 = frame.astype(np.float64)
        if acc is None:
            acc = f64.copy()
            min_proj = frame.copy()
            max_proj = frame.copy()
        else:
            acc += f64
            np.minimum(min_proj, frame, out=min_proj)
            np.maximum(max_proj, frame, out=max_proj)
        captured += 1
        if on_frame:
            on_frame(captured)

    return DarkStack(mean=acc / captured, min_proj=min_proj, max_proj=max_proj, n_frames=captured)
