"""Coarse-to-fine contrast focus sweep (AI-docs objective-offset design §6.3, normative).

Micro-Manager JAF(H&P) (Autofocus.java, UCSF 2007): a coarse grid over ±search range around the
operator's hand focus, then finer grids around the best sample. The two refusals, a flat curve and a
peak at the edge of the range, follow OpenFlexure check_stack_result (openflexure-microscope-server,
things/autofocus.py).

Contract. Precondition: the operator has hand-focused on the sample, and each objective's focus lies
within ±range of that Z. Guarantee: a returned Z is the maximum of the contrast curve measured over
that range, inside it with EDGE_SAMPLES on each side; otherwise a FocusError states what was observed
(flat curve, peak at the edge, too few samples inside the Z limits, peak moved between levels). Not
promised: that the peak is the sample rather than dust or a coverslip.

Hardware verification so far: an H&E section in bright-field only (Squid+, 4x/10x/20x, 2026-10-05
and the PR #683 bench). Fluorescence, unstained bright-field (phase objects: contrast is lowest at
focus), sparse fields, thick samples and NA > 0.8 are untested; PEAK_SIGMAS and the single-peak
assumption were checked only against that slide and synthetic noise.

TODO before this becomes the acquisition's contrast autofocus (bench 2026-10-08, Squid+ 4x/10x/20x):
- Entry for routine use: start at a fine level of about ±3 DOF around the current Z (10-15 frames)
  and run the 41-sample coarse pass only when that level ends at its edge. Today every call is the
  full ladder: 88 frames / 16 s at 20x, 55 / 11 s at 10x, 41 / 9 s at 4x, at 0.17-0.28 s per frame
  with the software trigger. Measure the frame time with the hardware trigger; it sets the budget.
- Refusals must come back as a result, not FocusError: an acquisition keeps the last good Z or the
  focus-plane fit and goes on. "Peak at the edge" should widen once, then keep Z. FocusError is the
  right contract for the dialog, where an operator can refocus.
- Decide what "no peak" means in a well. The noise-relative test accepts the well-bottom surface
  (a 3-18 % bump on blank glass passed it on every objective): right for adherent cells, so do not
  add a contrast floor; define the empty-well policy instead.
- Flat fields: the first 3-4 coarse samples after the descent to the first target read 2-4 % high
  and decay (stage/illumination settling), which turns "no peak" into "at the edge" there. Frames
  at a fixed Z agree to 0.1 %, so a warm-up frame does not help; a settle after the pre-move would.
- Z creeps 0.04-0.07 um/min on this unit (4 DOF per hour at 20x): per-FOV or periodic refocus.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

import cv2
import numpy as np

from squid.objective_calibration.hardware import CalibrationError
from squid.objective_calibration.registration import high_pass

LAMBDA_UM = 0.55  # green light; depth of field = LAMBDA / NA^2 sets the fine step
EDGE_SAMPLES = 2  # OpenFlexure edge_size: a peak this close to either end of a sweep is not trusted
MIN_IN_LIMIT_SAMPLES = 2 * EDGE_SAMPLES + 1  # the fewest samples that can hold a peak with its margins
COARSE_SAMPLES = 41  # JAF "1st step number": fixed grid; every passing 10x/20x bench run used 41
DEFAULT_RANGE_UM = 100.0  # ±range around the hand focus; measured parfocality offsets reach 70 um
# A coarse sweep counts as a peak when its maximum stands this many noise sigmas above its floor. Pure
# noise over 41 samples scores about 3.5 (the largest of 41 draws is ~2.2 sigma above the mean, the
# lowest-quartile mean ~1.3 below), 3.2-4.9 measured over 11 noise-only sweeps; the weakest real peak
# measured was 8.5, tissue 19-330. The number comes from order statistics of the grid, not the sample.
PEAK_SIGMAS = 6.0
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
    search_range_um: float
    step_um: float


def plan_coarse_level(na: float, range_um: float, center_um: float) -> Level:
    """COARSE_SAMPLES evenly spaced from -search_range to +search_range (JAF "1st step size" x
    "1st step number"). The range is at least 3 DOF so a narrow user range still spans the focus peak."""
    search_range_um = max(range_um, 3 * depth_of_field_um(na))
    step_um = 2 * search_range_um / (COARSE_SAMPLES - 1)
    return Level(center_um, search_range_um, step_um)


def plan_next_level(prev: Level, best_um: float, na: float) -> Optional[Level]:
    fine = fine_step_um(na)
    if prev.step_um <= fine + 1e-9:
        return None
    return Level(best_um, max(2 * prev.step_um, 1.5 * depth_of_field_um(na)), max(prev.step_um / 4, fine))


def level_targets(level: Level) -> np.ndarray:
    n = int(round(2 * level.search_range_um / level.step_um)) + 1
    return level.center_um + np.linspace(-level.search_range_um, level.search_range_um, n)


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


def peak_sigmas(values: List[float]) -> float:
    """Peak height over the floor (mean of the lowest quartile) in units of the sweep's own noise sigma,
    estimated from first differences (MAD / 0.6745 / sqrt 2, the usual robust estimator)."""
    s = np.asarray(values, dtype=float)
    d = np.diff(s)
    sigma = float(np.median(np.abs(d - np.median(d))) / 0.6745 / np.sqrt(2))
    floor = float(np.mean(np.sort(s)[: max(1, len(s) // 4)]))
    return (float(s.max()) - floor) / max(sigma, _EPS)


def _is_peak(values: List[float]) -> bool:
    """Does the sweep rise above its own noise? Nothing here depends on the sample's contrast."""
    return peak_sigmas(values) > PEAK_SIGMAS


def _peak_at_edge(values: List[float]) -> bool:
    """OpenFlexure check_stack_result edge rule: the sharpest sample lies within EDGE_SAMPLES of either
    end of the sweep, so the true peak may be outside it and a parabola through the neighbours cannot
    be trusted. A plateau counts by its last sample as well as its first."""
    s = np.asarray(values, dtype=float)
    top = np.flatnonzero(s == s.max())
    return int(top[0]) < EDGE_SAMPLES or int(top[-1]) >= len(s) - EDGE_SAMPLES


def _run_level(hw, level, *, coarse, objective, channel, na, square_px, fine_metric) -> SweepLevel:
    low, high = hw.z_limits_um()
    targets = [z for z in level_targets(level) if low <= z <= high]
    if len(targets) < MIN_IN_LIMIT_SAMPLES:
        raise FocusError("Search range hits the Z limit; refocus the starting objective or narrow the range.")
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
    """Coarse sweep over ±range_um around center_um, then the fine ladder. With range_is_computed (C1's
    pass 2, offsets.py: the centre is a predicted focus and the range max(3 DOF, 5 um + 2 % of the
    displacement), spec C §6.2) a coarse peak at the edge doubles the range once about the same
    centre; a second edge hit fails as "too uneven" (spec C §6.3 amendment, 2026-09-28)."""
    run = dict(objective=objective, channel=channel, na=na, square_px=square_px, fine_metric=fine_metric)
    levels: List[SweepLevel] = []

    # The coarse sweep is scored with the high-passed std: the fine metric is flat noise a few DOF
    # from focus, so it can neither see a focus across the range nor refuse a blank field.
    level = plan_coarse_level(na, range_um, center_um)
    for widened in (False, True):
        swept = _run_level(hw, level, coarse=True, **run)
        levels.append(swept)
        # Reported, not gated on: (max - floor) / floor with the floor the mean of the lowest quartile.
        ordered = np.sort(np.asarray(swept.values))
        floor = float(np.mean(ordered[: max(1, len(ordered) // 4)]))
        peak_rise = (float(ordered[-1]) - floor) / max(floor, _EPS)
        if not _is_peak(swept.values):  # before the edge test: a flat curve has its maximum anywhere
            raise FocusError(
                f"No focus peak within ±{level.search_range_um:g} µm of {level.center_um:.1f} µm "
                f"(contrast rise {peak_rise:.0%}); move to a textured area, or refocus and widen the range."
            )
        best = int(np.argmax(swept.values))
        if not _peak_at_edge(swept.values):
            break
        if not range_is_computed:
            raise FocusError(
                f"Sharpest sample at the edge of ±{level.search_range_um:g} µm, {swept.z_um[best]:.1f} µm; "
                "refocus by hand, or widen the range."
            )
        if widened:
            raise FocusError(
                "Focus is outside the computed search range; the target is too uneven. Use a flatter target."
            )
        level = plan_coarse_level(na, 2 * level.search_range_um, center_um)

    current, current_best = level, best
    nxt = plan_next_level(current, swept.z_um[best], na)
    while nxt is not None:
        recentred = False
        while True:
            swept = _run_level(hw, nxt, coarse=nxt.step_um > 2 * depth_of_field_um(na), **run)
            levels.append(swept)
            current_best = int(np.argmax(swept.values))
            if not _peak_at_edge(swept.values):
                break
            if recentred:
                raise FocusError("Focus moved during the sweep (drift or backlash); retry.")
            recentred = True
            nxt = Level(swept.z_um[current_best], nxt.search_range_um, nxt.step_um)
        current = nxt
        nxt = plan_next_level(current, swept.z_um[current_best], na)

    z_best = _vertex(swept, current_best)
    low, _ = hw.z_limits_um()
    pre = z_best - max(2 * current.step_um, 1.0)
    if pre >= low:
        hw.move_z_to_um(pre)
    hw.move_z_to_um(z_best)
    return FocusResult(z_best, levels, peak_rise)


def laplacian_energy(image: np.ndarray) -> float:
    """Mean squared Laplacian (LAPE) in float32; the fine metric when the caller supplies none."""
    img = np.asarray(image, dtype=np.float32)
    if img.ndim == 3:
        img = img.mean(axis=2)
    lap = cv2.Laplacian(img, cv2.CV_32F)
    return float(np.mean(lap * lap))


def autofocus(
    hw,
    *,
    objective: str,
    channel: str,
    na: float,
    range_um: float = DEFAULT_RANGE_UM,
    square_px: Optional[float] = None,
    fine_metric: Optional[Callable[[np.ndarray], float]] = None,
) -> FocusResult:
    """Focus from wherever Z is now (Micro-Manager AutofocusPlugin.fullFocus): focus_sweep centred on
    the current Z, over ±range_um, scoring a central square of 40 % of the frame by default."""
    if square_px is None:
        square_px = 0.4 * min(hw.frame_shape(channel))
    return focus_sweep(
        hw,
        objective=objective,
        channel=channel,
        na=na,
        center_um=hw.get_z_um(),
        range_um=range_um,
        square_px=square_px,
        fine_metric=fine_metric or laplacian_energy,
    )
