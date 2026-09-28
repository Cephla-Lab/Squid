"""Same-objective image registration for the pixel-size measurement.

Sign convention (pinned by tests): phase_shift(ref, img) returns (dx, dy), the displacement of
img's content relative to ref in (column, row) pixels. Content that moved right or down is
positive. This is cv2.phaseCorrelate(ref, img)'s order. skimage.registration.phase_cross_correlation,
used elsewhere in the repo (control/utils.py), returns the opposite sign; do not mix them.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

_PEAK_EXCLUSION_PX = 5


@dataclass(frozen=True)
class Shift:
    dx: float
    dy: float
    response: float
    peak_ratio: float


def high_pass(image, sigma_px: Optional[float] = None) -> np.ndarray:
    """The image minus a wide Gaussian blur: removes vignetting and illumination gradients."""
    img = np.asarray(image, dtype=np.float32)
    if img.ndim == 3:
        img = img.mean(axis=2)
    sigma = sigma_px if sigma_px is not None else max(3.0, min(img.shape) / 16)
    return img - cv2.GaussianBlur(img, (0, 0), sigma)


def _peak_ratio(a: np.ndarray, b: np.ndarray) -> float:
    cross = np.fft.fft2(a) * np.conj(np.fft.fft2(b))
    cross /= np.abs(cross) + 1e-12
    surface = np.abs(np.fft.ifft2(cross))
    iy, ix = np.unravel_index(int(np.argmax(surface)), surface.shape)
    h, w = surface.shape
    dy = np.abs(np.arange(h) - iy)
    dx = np.abs(np.arange(w) - ix)
    near = (np.minimum(dy, h - dy)[:, None] <= _PEAK_EXCLUSION_PX) & (
        np.minimum(dx, w - dx)[None, :] <= _PEAK_EXCLUSION_PX
    )
    runner_up = float(np.where(near, 0.0, surface).max())
    return float(surface[iy, ix] / max(runner_up, 1e-12))


def phase_shift(ref, img) -> Shift:
    a = high_pass(ref)
    b = high_pass(img)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, window)
    return Shift(float(dx), float(dy), float(response), _peak_ratio(a * window, b * window))


def overlap_crops(ref, img, shift_px: Tuple[int, int]):
    """Crop ref and img to the region they share when img's content is displaced by shift_px (col, row)."""
    qx, qy = int(shift_px[0]), int(shift_px[1])
    h, w = np.asarray(ref).shape[:2]
    ref_crop = ref[max(0, -qy) : h - max(0, qy), max(0, -qx) : w - max(0, qx)]
    img_crop = img[max(0, qy) : h - max(0, -qy), max(0, qx) : w - max(0, -qx)]
    return ref_crop, img_crop
