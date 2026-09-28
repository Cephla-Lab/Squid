"""Pixel size and pixel->stage matrix from stage moves (AI-docs pixel-size design §5).

M maps a pixel offset from the image centre to the stage move that centres that point, so after a
stage move D the content that was centred appears at p with D = -M p. A mosaic-consistent camera
has M = +pixel_size * I.
"""

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np

from squid.objective_calibration.hardware import CalibrationError, check_xy_target
from squid.objective_calibration.registration import overlap_crops, phase_shift

APPROACH_MARGIN_UM = 50.0
RUNG1_FRACTION = 0.25
RUNG2_SAFETY = 0.95
MIN_RESPONSE = 0.15
MIN_PEAK_RATIO = 2.0
RUNG2_TOLERANCE_PX = 5.0
RUNG2_TOLERANCE_FRACTION = 0.05
MAX_ANISOTROPY = 0.01


class PixelSizeError(CalibrationError):
    pass


@dataclass
class PixelSizeResult:
    matrix_um_per_px: np.ndarray
    pixel_size_um: float
    rotation_deg: float
    flip: np.ndarray
    orientation_matches_mosaic: bool
    anisotropy: float
    fit_residual_um: float
    drift_um: float


def approach_xy(hw, x_um: float, y_um: float, margin_um: float = APPROACH_MARGIN_UM) -> None:
    """Reach (x, y) from the same side every time: XY has no host-side backlash compensation."""
    check_xy_target(hw, x_um - margin_um, y_um - margin_um)
    check_xy_target(hw, x_um, y_um)
    hw.move_xy_to_um(x_um - margin_um, y_um - margin_um)
    hw.move_xy_to_um(x_um, y_um)


def fit_matrix(displacements_um, shifts_px) -> np.ndarray:
    d = np.asarray(displacements_um, dtype=float)
    p = np.asarray(shifts_px, dtype=float)
    m_transposed, *_ = np.linalg.lstsq(-p, d, rcond=None)
    return m_transposed.T


def decompose(m) -> Tuple[float, float, np.ndarray, float]:
    """M = s * R(theta) * F, with F a signed permutation chosen from M's dominant entries."""
    m = np.asarray(m, dtype=float)
    s = math.sqrt(abs(np.linalg.det(m)))
    if abs(m[0, 0]) + abs(m[1, 1]) >= abs(m[0, 1]) + abs(m[1, 0]):
        flip = np.array([[1 if m[0, 0] >= 0 else -1, 0], [0, 1 if m[1, 1] >= 0 else -1]])
    else:
        flip = np.array([[0, 1 if m[0, 1] >= 0 else -1], [1 if m[1, 0] >= 0 else -1, 0]])
    r = m @ flip.T / s
    theta = math.degrees(math.atan2(r[1, 0] - r[0, 1], r[0, 0] + r[1, 1]))
    singular = np.linalg.svd(m, compute_uv=False)
    return s, theta, flip, float(singular[0] / singular[1])


def _register(ref, img):
    shift = phase_shift(ref, img)
    if shift.response < MIN_RESPONSE or shift.peak_ratio < MIN_PEAK_RATIO:
        raise PixelSizeError("Stage-move images don't match; check the sample (textured, not periodic) and focus.")
    return shift


def measure_pixel_size(hw, *, objective: str, channel: str, nominal_px_um: float) -> PixelSizeResult:
    sx, sy = hw.get_xy_um()
    approach_xy(hw, sx, sy)
    x0, y0 = hw.get_xy_um()
    ref = hw.snap(objective, channel)
    h, w = ref.shape[:2]
    displacements, shifts = [], []

    d1 = RUNG1_FRACTION * min(w, h) * nominal_px_um
    for dx, dy in ((d1, 0.0), (-d1, 0.0), (0.0, d1), (0.0, -d1)):
        approach_xy(hw, x0 + dx, y0 + dy)
        shift = _register(ref, hw.snap(objective, channel))
        x, y = hw.get_xy_um()
        displacements.append((x - x0, y - y0))
        shifts.append((shift.dx, shift.dy))
    m1_inverse = np.linalg.inv(fit_matrix(displacements, shifts))

    for axis in (0, 1):
        unit = np.zeros(2)
        unit[axis] = 1.0
        per_um = -m1_inverse @ unit  # predicted content shift (px) per um of stage motion
        reach = [0.5 * dim / abs(c) for dim, c in ((w, per_um[0]), (h, per_um[1])) if abs(c) > 1e-9]
        d2 = RUNG2_SAFETY * min(reach)
        for sign in (1.0, -1.0):
            approach_xy(hw, x0 + sign * d2 * unit[0], y0 + sign * d2 * unit[1])
            img = hw.snap(objective, channel)
            x, y = hw.get_xy_um()
            d = np.array([x - x0, y - y0])
            predicted = -m1_inverse @ d
            q = (int(round(predicted[0])), int(round(predicted[1])))
            ref_crop, img_crop = overlap_crops(ref, img, q)
            residual = _register(ref_crop, img_crop)
            p = np.array([q[0] + residual.dx, q[1] + residual.dy])
            tolerance = max(RUNG2_TOLERANCE_PX, RUNG2_TOLERANCE_FRACTION * float(np.linalg.norm(predicted)))
            if float(np.linalg.norm(p - predicted)) > tolerance:
                raise PixelSizeError(
                    "Stage moves disagree with the image (backlash, missed steps or a periodic sample)."
                )
            displacements.append(tuple(d))
            shifts.append(tuple(p))

    approach_xy(hw, x0, y0)
    back = phase_shift(ref, hw.snap(objective, channel))
    m = fit_matrix(displacements, shifts)
    pixel_size, theta, flip, anisotropy = decompose(m)
    tolerance_um = max(0.5 * pixel_size, 2 * hw.xy_microstep_um())
    drift_um = math.hypot(back.dx, back.dy) * pixel_size
    if drift_um > tolerance_um:
        raise PixelSizeError("Image drifted during the measurement; let the system settle and retry.")
    residuals = np.asarray(displacements) + np.asarray(shifts) @ m.T
    rms_um = float(np.sqrt(np.mean(np.sum(residuals**2, axis=1))))
    if rms_um > tolerance_um:
        raise PixelSizeError("Stage moves are inconsistent (backlash or stage error).")
    if abs(anisotropy - 1.0) > MAX_ANISOTROPY:
        raise PixelSizeError(
            f"Pixel scale differs between x and y by {anisotropy - 1.0:.1%}; "
            "check the camera binning, or correct SCREW_PITCH_X/Y_MM."
        )
    return PixelSizeResult(m, pixel_size, theta, flip, bool((flip == np.eye(2)).all()), anisotropy, rms_um, drift_um)
