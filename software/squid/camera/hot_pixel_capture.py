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


def settle_temperature(
    camera,
    target_c: float,
    tolerance_c: float = 1.0,
    timeout_s: float = 300.0,
    stable_reads: int = 3,
    poll_interval_s: float = 2.0,
    should_stop: Optional[Callable[[], bool]] = None,
    on_poll: Optional[Callable[[Optional[float]], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.time,
) -> Tuple[bool, Optional[float]]:
    try:
        camera.set_temperature(target_c)
    except Exception:
        return False, None

    start = now_fn()
    consecutive = 0
    last: Optional[float] = None
    while now_fn() - start < timeout_s:
        if should_stop and should_stop():
            return False, last
        last = camera.get_temperature()
        if on_poll:
            on_poll(last)
        if last is not None and abs(last - target_c) <= tolerance_c:
            consecutive += 1
            if consecutive >= stable_reads:
                return True, last
        else:
            consecutive = 0
        sleep_fn(poll_interval_s)
    return False, last


def run_sweep(
    camera,
    exposures_ms: List[float],
    temperatures_c: Optional[List[float]],
    n_frames: int,
    thresholds: hot_pixels.DefectThresholds,
    pixel_format: CameraPixelFormat,
    black_level: float = 0.0,
    should_stop: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[Optional[float], float], None]] = None,
    settle_kwargs: Optional[dict] = None,
) -> List[hot_pixels.ConditionResult]:
    max_value = hot_pixels.max_value_for_pixel_format(pixel_format)
    results: List[hot_pixels.ConditionResult] = []
    temps: List[Optional[float]] = list(temperatures_c) if temperatures_c else [None]

    for t in temps:
        actual_t: Optional[float] = None
        if t is not None:
            _, actual_t = settle_temperature(camera, t, should_stop=should_stop, **(settle_kwargs or {}))
        else:
            try:
                actual_t = camera.get_temperature()
            except Exception:
                actual_t = None

        for exposure_ms in exposures_ms:
            if should_stop and should_stop():
                return results
            if on_progress:
                on_progress(t, exposure_ms)
            stack = capture_dark_stack(camera, exposure_ms, n_frames, should_stop=should_stop)
            if stack is None:
                return results
            result = hot_pixels.detect_defects(
                stack.mean, stack.min_proj, stack.max_proj, max_value, thresholds, black_level
            )
            results.append(
                hot_pixels.ConditionResult(
                    temperature_c=t,
                    actual_temperature_c=actual_t,
                    exposure_ms=exposure_ms,
                    n_frames=stack.n_frames,
                    result=result,
                )
            )
    return results
