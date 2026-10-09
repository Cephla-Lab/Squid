"""Illumination power calibration: the fit, the lookup, and the calibration file.

A calibration maps a requested intensity (% of a channel's maximum power) to the DAC command that gives it. This
module is what the runtime needs (control/lighting.py imports it); running a calibration with a power meter lives in
squid/intensity_calibration_run.py. No Qt.

Design: AI-docs Squid/to-do/2026-10-08-power-linearization-gui-design.md (§5.2 the fit, §6 the files).
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

ROLLOVER_FRACTION = 0.01
SMOOTHING_WINDOW = 5
ZERO_LEVEL_SIGMAS = 5.0
ZERO_LEVEL_FRACTION = 1e-3
VERIFY_SETPOINTS = (1, 2, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
VERIFY_MIN_GATED_PERCENT = 10
VERIFY_REL_TOL = 0.05
MIN_POINTS = 3


class CalibrationError(ValueError):
    """The measurements cannot make a calibration (too few points, no light)."""


class CalibrationFileError(ValueError):
    """A calibration file exists but cannot be used."""


def isotonic_fit(y: Sequence[float]) -> np.ndarray:
    """Pool-adjacent-violators: the non-decreasing sequence closest to y in least squares (equal weights)."""
    values: List[float] = []
    counts: List[int] = []
    for value in np.asarray(y, dtype=float):
        values.append(float(value))
        counts.append(1)
        while len(values) > 1 and values[-2] > values[-1]:
            n = counts[-2] + counts[-1]
            merged = (values[-2] * counts[-2] + values[-1] * counts[-1]) / n
            values[-2:] = [merged]
            counts[-2:] = [n]
    return np.repeat(values, counts)


def running_median(y: Sequence[float], window: int = SMOOTHING_WINDOW) -> np.ndarray:
    """Median over a window centred on each point, shrunk symmetrically at the ends. It returns a monotone sequence
    unchanged and removes isolated spikes."""
    y = np.asarray(y, dtype=float)
    half = window // 2
    out = np.empty(y.size)
    for i in range(y.size):
        h = min(half, i, y.size - 1 - i)
        out[i] = np.median(y[i - h : i + h + 1])
    return out


def find_rollover_peak(
    power: Sequence[float], sigma_dark: float = 0.0, window: int = SMOOTHING_WINDOW
) -> Optional[int]:
    """Index of the power peak when the output falls at the top (e.g. LED thermal rollover), else None.

    The peak of the running median must lie before the last `window` points, and the median of the last `window`
    raw points must be below it by more than max(ROLLOVER_FRACTION x peak, 3 sigma_dark). A flat top is not
    rollover; neither is a dip in the middle of the curve.
    """
    power = np.asarray(power, dtype=float)
    if power.size <= window:
        return None
    smoothed = running_median(power, window)
    i_peak = int(np.argmax(smoothed))
    if i_peak >= power.size - window:
        return None
    drop = smoothed[i_peak] - float(np.median(power[-window:]))
    return i_peak if drop > max(ROLLOVER_FRACTION * smoothed[i_peak], 3.0 * sigma_dark) else None


def build_anchors(x: Sequence[float], fitted: Sequence[float], zero_level: float) -> Tuple[np.ndarray, np.ndarray]:
    """Inversion anchors (power, x) from a monotone fit: power strictly increasing, x non-decreasing.

    The first anchor is (0, x of the LAST point whose fitted power <= zero_level): the end of the dead zone below a
    lasing threshold, so a small request lands just above threshold instead of inside the dead zone. Each later run
    of equal fitted power contributes (that power, x of its FIRST point): on a plateau, the lowest drive that reaches
    it, so no current is spent for no light.
    """
    x = np.asarray(x, dtype=float)
    fitted = np.asarray(fitted, dtype=float)
    dead = np.nonzero(fitted <= zero_level)[0]
    powers = [0.0]
    xs = [float(x[dead[-1]]) if dead.size else 0.0]
    i = int(dead[-1]) + 1 if dead.size else 0
    while i < x.size:
        j = i
        while j + 1 < x.size and fitted[j + 1] == fitted[i]:
            j += 1
        powers.append(float(fitted[i]))
        xs.append(float(x[i]))
        i = j + 1
    return np.array(powers), np.array(xs)


@dataclass(frozen=True, eq=False)
class CurveFit:
    fitted_mw: np.ndarray  # per point; NaN above the calibrated top
    anchor_power_mw: np.ndarray
    anchor_x: np.ndarray
    p_max_mw: float  # calibrated 100 %
    top_index: int  # the last point of the calibrated range
    zero_level_mw: float
    rollover: Optional[str]  # a sentence for the user, or None


def fit_curve(dac_percent, x, power_mw, sigma_dark: float = 0.0) -> CurveFit:
    """Fit a measured curve (design §5.2): rollover cut, running median, monotone fit, anchors.

    Args:
        dac_percent: commanded DAC % per point, increasing (used in messages).
        x: what the lookup returns, per point, increasing (fraction of the DAC's full scale).
        power_mw: dark-subtracted power per point.
        sigma_dark: the meter's dark noise in mW; 0 when unknown.
    """
    dac_percent = np.asarray(dac_percent, dtype=float)
    x = np.asarray(x, dtype=float)
    power = np.asarray(power_mw, dtype=float)
    if power.size < MIN_POINTS:
        raise CalibrationError(f"only {power.size} points; at least {MIN_POINTS} are needed")
    i_peak = find_rollover_peak(power, sigma_dark)
    top = power.size - 1 if i_peak is None else i_peak
    # Smooth the whole curve once, then cut: smoothing only the kept part would make the peak an end point, where
    # the median window is a single reading and a spike there would set P_max.
    fitted_part = isotonic_fit(running_median(power)[: top + 1])
    p_max = float(fitted_part[-1])
    if not p_max > 0:
        raise CalibrationError("no light measured: the fitted power never rises above zero")
    zero_level = max(ZERO_LEVEL_SIGMAS * sigma_dark, ZERO_LEVEL_FRACTION * p_max)
    anchor_power, anchor_x = build_anchors(x[: top + 1], fitted_part, zero_level)
    if anchor_power.size < 2:
        raise CalibrationError("no light above the dark noise: check the sensor position and the meter's wavelength")
    fitted = np.full(power.size, np.nan)
    fitted[: top + 1] = fitted_part
    rollover = None
    if i_peak is not None:
        end_mw = float(np.median(power[-SMOOTHING_WINDOW:]))
        rollover = (
            f"output peaks at DAC {dac_percent[top]:.1f} % ({p_max:.4g} mW) and falls to {end_mw:.4g} mW at "
            f"DAC {dac_percent[-1]:.1f} %; calibrated range ends at DAC {dac_percent[top]:.1f} %"
        )
    return CurveFit(fitted, anchor_power, anchor_x, p_max, top, zero_level, rollover)


def x_for_power_fraction(anchor_power_mw, anchor_x, p_max_mw: float, fraction: float) -> float:
    """The abscissa that gives `fraction` of p_max_mw: 0 (source off) for fraction <= 0, the top for >= 1."""
    if fraction <= 0:
        return 0.0
    return float(np.interp(min(fraction, 1.0) * p_max_mw, anchor_power_mw, anchor_x))
