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
from typing import Dict, List, Optional, Tuple

import numpy as np

from squid.objective_calibration.hardware import CalibrationError
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
