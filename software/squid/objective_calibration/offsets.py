"""Objective offsets: XY by cross-objective registration (spec C §6.4-6.5).

Sign chain, pinned by the synthetic tests: the higher-magnification objective j's frame, resized to
objective i's pixel size, matches in i's image at delta_px from the centre. M_i @ delta_px is the
stage move that centres that point under i, and offset_j - offset_i = -(M_i @ delta_px). The
matrices come only from the run's CalibrationView (this cycle's pixel-size measurement, or the
saved records), never from a nominal fallback.
"""

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from squid.objective_calibration.engine import square_um
from squid.objective_calibration.focus import FocusError, depth_of_field_um, focus_sweep
from squid.objective_calibration.hardware import CalibrationError, LimitError, check_xy_target
from squid.objective_calibration.pixel_size import approach_xy
from squid.objective_calibration.registration import MAX_SELF_SIMILARITY, Match, match_template, template_shape

MIN_MATCH_SCORE = 0.5
MAX_RUNNER_UP_RATIO = 0.8
CLOSURE_MARGIN = 0.1  # of the template size: a closure pair is measured only when its template fits with this margin
CLOSURE_WARNING_UM = 2.0


class OffsetsError(CalibrationError):
    pass


@dataclass
class PairResult:
    lower: str
    higher: str
    measurable: bool
    delta_px: Optional[Tuple[float, float]] = None  # where the higher objective's centre appears in the lower's image
    offset_diff_um: Optional[Tuple[float, float]] = None  # offset_higher - offset_lower
    score: Optional[float] = None
    runner_up_ratio: Optional[float] = None
    self_similarity: Optional[float] = None  # the sample's, from the pair's frames (Match.self_similarity)
    range_um: Optional[Tuple[float, float]] = None  # the largest |dx|, |dy| the search could measure, in um
    closure_error_um: Optional[float] = None  # a closure pair's |d(ref->i) + d(i->j) - d(ref->j)|
    message: str = ""  # a closure warning, or why the pair was not measured


@dataclass
class XYRegistration:
    reference: str
    offsets_um: Dict[str, Tuple[float, float]]  # every registered objective; the reference's is (0, 0)
    pairs: List[PairResult]
    closure_error_um: Dict[str, float] = field(default_factory=dict)  # worst triple containing the objective
    warnings: List[str] = field(default_factory=list)


def _template_fits(image_shape, shape, delta_px) -> bool:
    (h, w), (th, tw) = image_shape, shape
    return abs(delta_px[0]) + tw / 2 + CLOSURE_MARGIN * tw <= w / 2 and (
        abs(delta_px[1]) + th / 2 + CLOSURE_MARGIN * th <= h / 2
    )


def match_failure(lower: str, higher: str, match: Match, px_um: float) -> Optional[str]:
    """What spec C §6.7's match gates refuse in this match, with the measured values, or None. A
    reference pair raises it; a closure pair only warns with it. px_um is the lower objective's pixel
    size, at which both frames were compared."""
    measured = f"match score {match.score:.2f}"
    if match.score < MIN_MATCH_SCORE:
        return (
            f"Images of {lower} and {higher} do not match ({measured}, need {MIN_MATCH_SCORE}); "
            "check the sample and illumination."
        )
    if match.self_similarity is None:
        return (
            f"Cannot establish a unique match between {lower} and {higher} ({measured}): their frames hold "
            "no other peak to compare it with; use a sample with finer, non-periodic texture."
        )
    if match.self_similarity.value >= MAX_SELF_SIMILARITY:
        # The reported shift is the shortest one whose peak is within 0.1 of the highest; it need not be
        # the sample's period (on stripes it can be a shift along the lines), so the text does not say so.
        shift_um = math.hypot(*match.self_similarity.shift_px) * px_um
        return (
            f"Cannot uniquely match {lower} and {higher}: the sample remains too similar after a "
            f"~{shift_um:.0f} µm shift (self-similarity {match.self_similarity.value:.2f}; "
            f"limit {MAX_SELF_SIMILARITY}). Use a sample with varied texture in both directions."
        )
    if match.open_lobe is not None:
        (x, y), axis = match.open_lobe.shift_px, "x" if match.open_lobe.shift_px[0] else "y"
        return (
            f"The sample's texture is too directional to establish a unique match between {lower} and {higher}: "
            f"along {axis} its autocorrelation does not fall to zero or a minimum within the shifts tested "
            f"(still {match.open_lobe.value:.2f} at ±{max(x, y) * px_um:.1f} µm); use a sample textured in every "
            "direction."
        )
    if match.runner_up_ratio is None:
        return (
            f"Cannot establish a unique match between {lower} and {higher} ({measured}): the search area "
            "holds no other peak to compare it with; use a sample with finer, non-periodic texture."
        )
    if match.runner_up_ratio >= MAX_RUNNER_UP_RATIO:
        return (
            f"Ambiguous match between {lower} and {higher} ({measured}, runner-up {match.runner_up_ratio:.2f} "
            f"of it, need below {MAX_RUNNER_UP_RATIO}); the sample looks periodic or featureless."
        )
    if match.at_edge:
        x_um, y_um = (px * px_um for px in match.search_px)
        return (
            f"The match between {lower} and {higher} is at the edge of the search area ({measured}): their "
            f"offset may be larger than it can measure (±{x_um:.1f} µm in x, ±{y_um:.1f} µm in y on this frame); "
            "check that both objectives are seated."
        )
    return None


def _measured(lower: str, higher: str, match: Match, diff: np.ndarray, px_um: float) -> PairResult:
    return PairResult(
        lower,
        higher,
        True,
        (match.dx, match.dy),
        (float(diff[0]), float(diff[1])),
        match.score,
        match.runner_up_ratio,
        None if match.self_similarity is None else match.self_similarity.value,
        (match.search_px[0] * px_um, match.search_px[1] * px_um),
    )


def register_offsets(images: Dict[str, np.ndarray], view, ordered: List[str]) -> XYRegistration:
    """XY offsets of every objective in `ordered` (ascending magnification; the first is the reference)
    from the in-focus images taken with the stage at one XY. Reference pairs define the offsets and
    their gates raise OffsetsError; closure pairs are measured only when their template fits and
    only warn (spec C §6.4, §6.7)."""
    reference = ordered[0]
    matrices, pixel_sizes = {}, {}
    for name in ordered:
        matrix = view.pixel_matrix(name)
        if matrix is None:
            raise OffsetsError(f"no pixel calibration for {name} in this cycle")
        matrices[name] = np.asarray(matrix, dtype=float)
        pixel_sizes[name] = float(view.pixel_size_um(name))

    offsets: Dict[str, Tuple[float, float]] = {reference: (0.0, 0.0)}
    pairs: List[PairResult] = []
    for name in ordered[1:]:
        match = match_template(images[reference], images[name], pixel_sizes[name] / pixel_sizes[reference])
        failure = match_failure(reference, name, match, pixel_sizes[reference])
        if failure:
            raise OffsetsError(failure)
        diff = -(matrices[reference] @ np.array([match.dx, match.dy]))
        offsets[name] = (float(diff[0]), float(diff[1]))
        pairs.append(_measured(reference, name, match, diff, pixel_sizes[reference]))

    result = XYRegistration(reference, offsets, pairs)
    closure_limit = max(CLOSURE_WARNING_UM, pixel_sizes[reference])
    for lower, higher in combinations(ordered[1:], 2):
        predicted_diff = np.subtract(offsets[higher], offsets[lower])
        predicted_px = -np.linalg.solve(matrices[lower], predicted_diff)
        scale = pixel_sizes[higher] / pixel_sizes[lower]
        shape = template_shape(images[lower].shape, images[higher].shape, scale)
        if not _template_fits(images[lower].shape[:2], shape, predicted_px):
            pairs.append(
                PairResult(
                    lower,
                    higher,
                    False,
                    message=f"{lower}-{higher}: not measurable ({higher}'s view lies outside {lower}'s frame)",
                )
            )
            continue
        match = match_template(images[lower], images[higher], scale)
        diff = -(matrices[lower] @ np.array([match.dx, match.dy]))
        pair = _measured(lower, higher, match, diff, pixel_sizes[lower])
        failure = match_failure(lower, higher, match, pixel_sizes[lower])
        if failure:
            pair.message = f"{lower}-{higher} (closure pair): {failure}"
        else:
            closure = float(np.linalg.norm(np.add(offsets[lower], diff) - offsets[higher]))
            pair.closure_error_um = closure
            for name in (lower, higher):
                result.closure_error_um[name] = max(result.closure_error_um.get(name, 0.0), closure)
            if closure > closure_limit:
                pair.message = f"{lower}-{higher}: closure error {closure:.1f} µm (over {closure_limit:.1f} µm)"
        if pair.message:
            result.warnings.append(pair.message)
        pairs.append(pair)
    return result


@dataclass
class ObjectiveOffset:
    """One objective's offset in one cycle, relative to the reference (spec C §4, §6.5)."""

    dx_um: float
    dy_um: float
    dz_um: float  # raw z_focus_k - z_focus_ref; the changer's own frame is subtracted at use time
    match_score: float
    runner_up_ratio: float
    focus_peak_rise: float  # the worse of the two focus sweeps
    closure_error_um: Optional[float]
    z_focus_um: float  # the aligned focus, absolute (for D's report)


@dataclass
class OffsetsCycleResult:
    cycle_index: int
    reference: str
    reference_z_focus_um: float
    offsets: Dict[str, ObjectiveOffset]  # every non-reference objective
    pairs: List[PairResult]
    warnings: List[str]


def _go_xy(hw, x_um: float, y_um: float) -> None:
    """From the same side when the pre-move fits inside the limits, else directly (the target itself
    was checked by the caller)."""
    try:
        approach_xy(hw, x_um, y_um)
    except LimitError:
        hw.move_xy_to_um(x_um, y_um)


def aligned_range_um(na: float, offset_um: Tuple[float, float]) -> float:
    """Pass 2's computed search range R2 = max(3·DOF, 5 µm + 2% of the displacement) (spec C §6.2)."""
    return max(3 * depth_of_field_um(na), 5.0 + 0.02 * math.hypot(*offset_um))


def _aligned_focus(
    hw, *, name: str, channel: str, na: float, center_um: float, range_um: float, square_px: float, fine_metric
):
    """The full sweep at the aligned position over the computed range R2 (spec C §6.2 step 4).
    focus_sweep owns the one widening: a computed range is doubled once on an edge hit, and a second
    hit fails as "too uneven" (spec C §6.3 amendment, 2026-09-28)."""
    try:
        return focus_sweep(
            hw,
            objective=name,
            channel=channel,
            na=na,
            center_um=center_um,
            range_um=range_um,
            square_px=square_px,
            fine_metric=fine_metric,
            range_is_computed=True,
        )
    except FocusError as e:
        raise OffsetsError(f"{name} at its aligned position: {e}")


class OffsetsPhase:
    """The engine's phase-2 hook (spec C §6.2 steps 3-6): register the pass-1 images, then measure
    each non-reference objective's focus with its centre on the reference's patch (pass 2). Any gate
    failure raises OffsetsError, which fails the cycle; the engine restores and continues.

    `align=False` skips pass 2 (dz from the pass-1 focus): only for the test that shows the slope
    bias pass 2 removes."""

    def __init__(self, cfg, *, fine_metric: Callable[[np.ndarray], float], align: bool = True):
        self.cfg = cfg
        self.fine_metric = fine_metric
        self.align = align
        self.results: List[OffsetsCycleResult] = []
        self._specs = {spec.name: spec for spec in cfg.objectives}
        self.ordered = [spec.name for spec in sorted(cfg.objectives, key=lambda spec: spec.magnification)]

    def __call__(self, hw, cycle, view) -> None:
        failed = [
            f"{name}: {cycle.objectives[name].error}"
            for name in self.ordered
            if name not in cycle.objectives or cycle.objectives[name].image is None
        ]
        if failed:
            raise OffsetsError("Offsets not measured in this cycle. " + "; ".join(failed))
        reference = self.ordered[0]
        xy = register_offsets({name: cycle.objectives[name].image for name in self.ordered}, view, self.ordered)
        side_um = square_um(self.cfg.objectives, hw.frame_shape(self.cfg.channel))
        z_reference = cycle.objectives[reference].focus.z_best_um
        offsets: Dict[str, ObjectiveOffset] = {}
        for name in reversed(self.ordered[1:]):  # descending magnification: the last of pass 1 is in place
            spec = self._specs[name]
            pass1 = cycle.objectives[name].focus
            dx, dy = xy.offsets_um[name]
            pair = next(p for p in xy.pairs if p.lower == reference and p.higher == name)
            if self.align:
                if hw.current_objective() != name:
                    hw.switch_objective(name)
                target = (cycle.start_xy_um[0] + dx, cycle.start_xy_um[1] + dy)
                try:
                    check_xy_target(hw, *target)
                except LimitError:
                    raise OffsetsError(
                        f"The aligned position for {name} is outside the stage limits; start closer to the centre of travel."
                    )
                _go_xy(hw, *target)
                focus = _aligned_focus(
                    hw,
                    name=name,
                    channel=self.cfg.channel,
                    na=spec.na,
                    center_um=pass1.z_best_um,
                    range_um=aligned_range_um(spec.na, (dx, dy)),
                    square_px=side_um / spec.nominal_px_um,
                    fine_metric=self.fine_metric,
                )
            else:
                focus = pass1
            offsets[name] = ObjectiveOffset(
                dx_um=dx,
                dy_um=dy,
                dz_um=focus.z_best_um - z_reference,
                match_score=pair.score,
                runner_up_ratio=pair.runner_up_ratio,
                focus_peak_rise=min(pass1.peak_rise, focus.peak_rise),
                closure_error_um=xy.closure_error_um.get(name),
                z_focus_um=focus.z_best_um,
            )
        self.results.append(OffsetsCycleResult(cycle.index, reference, z_reference, offsets, xy.pairs, xy.warnings))


@dataclass
class OffsetSummary:
    """The mean over the successful cycles, with the worst quality numbers (spec C §6.5)."""

    objective: str
    cycles: int
    dx_um: float
    dy_um: float
    dz_um: float
    std_dx_um: Optional[float]
    std_dy_um: Optional[float]
    std_dz_um: Optional[float]
    match_score: float  # worst (lowest)
    runner_up_ratio: float  # worst (highest)
    focus_peak_rise: float  # worst (lowest)
    closure_error_um: Optional[float]  # worst (highest) over the cycles that measured a closure pair


def _std(values: List[float]) -> Optional[float]:
    return float(np.std(values, ddof=1)) if len(values) > 1 else None


def summarize_offsets(results: List[OffsetsCycleResult]) -> Dict[str, OffsetSummary]:
    """Per objective, over the cycles in `results` (each cycle has every non-reference objective)."""
    if not results:
        return {}
    summaries = {}
    for name in results[0].offsets:
        per_cycle = [r.offsets[name] for r in results]
        closures = [o.closure_error_um for o in per_cycle if o.closure_error_um is not None]
        summaries[name] = OffsetSummary(
            objective=name,
            cycles=len(per_cycle),
            dx_um=float(np.mean([o.dx_um for o in per_cycle])),
            dy_um=float(np.mean([o.dy_um for o in per_cycle])),
            dz_um=float(np.mean([o.dz_um for o in per_cycle])),
            std_dx_um=_std([o.dx_um for o in per_cycle]),
            std_dy_um=_std([o.dy_um for o in per_cycle]),
            std_dz_um=_std([o.dz_um for o in per_cycle]),
            match_score=min(o.match_score for o in per_cycle),
            runner_up_ratio=max(o.runner_up_ratio for o in per_cycle),
            focus_peak_rise=min(o.focus_peak_rise for o in per_cycle),
            closure_error_um=max(closures) if closures else None,
        )
    return summaries


def _ratio(value: Optional[float]) -> str:
    return "none" if value is None else f"{value:.3f}"


def _uniqueness(pair: PairResult) -> str:
    x_um, y_um = pair.range_um
    return (
        f"runner-up {_ratio(pair.runner_up_ratio)}, self-similarity {_ratio(pair.self_similarity)}, "
        f"measurable to ±{x_um:.1f} µm in x and ±{y_um:.1f} µm in y"
    )


def offsets_report(result, cycles: List[OffsetsCycleResult]) -> List[str]:
    """Per cycle, the reference's focus and one line per pair with every measured value, or the
    cycle's failure: the offsets' bench record (the engine's cycle_report has pass 1 and pixel size)."""
    by_index = {measured.cycle_index: measured for measured in cycles}
    lines = []
    for cycle in result.cycles:
        n = cycle.index + 1
        measured = by_index.get(cycle.index)
        if measured is None:
            lines.append(f"cycle {n} offsets: {cycle.error or 'not measured'}")
            continue
        lines.append(f"cycle {n} offsets: reference {measured.reference}, focus {measured.reference_z_focus_um:.2f} µm")
        for pair in measured.pairs:
            if pair.lower == measured.reference:
                o = measured.offsets[pair.higher]
                lines.append(
                    f"cycle {n} {pair.lower}-{pair.higher}: dx {o.dx_um:+.2f} µm, dy {o.dy_um:+.2f} µm, "
                    f"dz {o.dz_um:+.2f} µm, aligned focus {o.z_focus_um:.2f} µm (rise {o.focus_peak_rise:.0%}), "
                    f"match {o.match_score:.3f}, {_uniqueness(pair)}"
                )
            elif not pair.measurable:
                lines.append(f"cycle {n} {pair.message}")
            else:
                closure = "" if pair.closure_error_um is None else f"closure {pair.closure_error_um:.2f} µm, "
                warning = f"; {pair.message}" if pair.message else ""
                lines.append(
                    f"cycle {n} {pair.lower}-{pair.higher} (closure pair): {closure}match {pair.score:.3f}, "
                    f"{_uniqueness(pair)}{warning}"
                )
    return lines
