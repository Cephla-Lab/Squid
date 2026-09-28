"""Coarse-to-fine contrast focus sweep (AI-docs objective-offset design §6.3, normative)."""

from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

from squid.objective_calibration.hardware import CalibrationError
from squid.objective_calibration.registration import high_pass

LAMBDA_UM = 0.55
MIN_IN_LIMIT_SAMPLES = 5
PEAK_RISE_MIN = 0.2
_EPS = 1e-12


class FocusError(CalibrationError):
    pass


def depth_of_field_um(na: float) -> float:
    return LAMBDA_UM / na**2


def fine_step_um(na: float) -> float:
    return max(depth_of_field_um(na) / 4, 0.1)


@dataclass(frozen=True)
class Level:
    center_um: float
    half_span_um: float
    step_um: float


def plan_coarse_level(na: float, range_um: float, center_um: float) -> Level:
    dof = depth_of_field_um(na)
    r_eff = max(range_um, 3 * dof)
    step = min(max(0.7 * dof, 2 * r_eff / 40), 2 * r_eff / 6)
    return Level(center_um, r_eff, step)


def plan_next_level(prev: Level, best_um: float, na: float) -> Optional[Level]:
    fine = fine_step_um(na)
    if prev.step_um <= fine + 1e-9:
        return None
    return Level(best_um, max(2 * prev.step_um, 1.5 * depth_of_field_um(na)), max(prev.step_um / 4, fine))


def level_targets(level: Level) -> np.ndarray:
    n = int(round(2 * level.half_span_um / level.step_um)) + 1
    return level.center_um + np.linspace(-level.half_span_um, level.half_span_um, n)


def focus_crop(image: np.ndarray, square_px: float) -> np.ndarray:
    h, w = image.shape[:2]
    side = max(8, min(int(round(square_px)), h, w))
    r0, c0 = (h - side) // 2, (w - side) // 2
    return image[r0 : r0 + side, c0 : c0 + side]


def coarse_metric(crop: np.ndarray) -> float:
    return float(np.std(high_pass(crop)))


@dataclass
class SweepLevel:
    metric: str
    z_um: List[float]
    values: List[float]


@dataclass
class FocusResult:
    z_best_um: float
    levels: List[SweepLevel]
    peak_rise: float


def _at_edge(index: int, n: int) -> bool:
    return index < 2 or index > n - 3


def _run_level(hw, level, *, objective, channel, na, square_px, fine_metric) -> SweepLevel:
    low, high = hw.z_limits_um()
    targets = [z for z in level_targets(level) if low <= z <= high]
    if len(targets) < MIN_IN_LIMIT_SAMPLES:
        raise FocusError("Search range hits the Z limit; refocus the starting objective or narrow the range.")
    coarse = level.step_um > 2 * depth_of_field_um(na)
    pre = targets[0] - max(2 * level.step_um, 1.0)
    if pre >= low:
        hw.move_z_to_um(pre)
    zs, values = [], []
    for z in targets:
        hw.move_z_to_um(z)
        crop = focus_crop(hw.snap(objective, channel), square_px)
        zs.append(hw.get_z_um())
        values.append(coarse_metric(crop) if coarse else float(fine_metric(crop)))
    return SweepLevel("highpass_std" if coarse else "fine", zs, values)


def _vertex(level: SweepLevel, index: int) -> float:
    z = np.asarray(level.z_um[index - 1 : index + 2])
    y = np.log(np.asarray(level.values[index - 1 : index + 2]) + _EPS)
    z0 = z[1]  # fit around the peak sample: at a bench Z of ~5000 um the raw fit is poorly conditioned
    a, b, _ = np.polyfit(z - z0, y, 2)
    if a >= 0:
        return float(level.z_um[index])
    vertex = z0 - b / (2 * a)
    if not z[0] <= vertex <= z[-1]:
        raise FocusError("Focus moved during the sweep (drift or backlash); retry.")
    return float(vertex)


def focus_sweep(
    hw,
    *,
    objective: str,
    channel: str,
    na: float,
    center_um: float,
    range_um: float,
    square_px: float,
    fine_metric: Callable[[np.ndarray], float],
    range_is_computed: bool = False,
) -> FocusResult:
    run = dict(objective=objective, channel=channel, na=na, square_px=square_px, fine_metric=fine_metric)
    levels: List[SweepLevel] = []

    level = plan_coarse_level(na, range_um, center_um)
    widened = False
    while True:
        swept = _run_level(hw, level, **run)
        levels.append(swept)
        lowest = min(swept.values)
        peak_rise = (max(swept.values) - lowest) / max(lowest, _EPS)
        if peak_rise < PEAK_RISE_MIN:  # before the edge gate: a flat curve has its maximum anywhere
            raise FocusError(
                f"No focus peak found within ±{level.half_span_um:g} µm "
                f"(contrast rise {peak_rise:.0%}, need {PEAK_RISE_MIN:.0%}); "
                "move to a textured area, or refocus and widen the range."
            )
        best = int(np.argmax(swept.values))
        if not _at_edge(best, len(swept.values)):
            break
        if range_is_computed and not widened:
            widened = True
            level = plan_coarse_level(na, 2 * level.half_span_um, center_um)
            continue
        if range_is_computed:
            raise FocusError(
                "Focus is outside the computed search range; the target is too uneven. Use a flatter target."
            )
        raise FocusError(f"Focus peak at the edge of the ±{level.half_span_um:g} µm search; widen the range.")

    current, current_best = level, best
    nxt = plan_next_level(current, swept.z_um[best], na)
    while nxt is not None:
        recentred = False
        while True:
            swept = _run_level(hw, nxt, **run)
            levels.append(swept)
            current_best = int(np.argmax(swept.values))
            if not _at_edge(current_best, len(swept.values)):
                break
            if recentred:
                raise FocusError("Focus moved during the sweep (drift or backlash); retry.")
            recentred = True
            nxt = Level(swept.z_um[current_best], nxt.half_span_um, nxt.step_um)
        current = nxt
        nxt = plan_next_level(current, swept.z_um[current_best], na)

    z_best = _vertex(swept, current_best)
    low, _ = hw.z_limits_um()
    pre = z_best - max(2 * current.step_um, 1.0)
    if pre >= low:
        hw.move_z_to_um(pre)
    hw.move_z_to_um(z_best)
    return FocusResult(z_best, levels, peak_rise)
