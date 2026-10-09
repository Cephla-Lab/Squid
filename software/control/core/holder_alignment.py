"""Holder-alignment wizard state: wells, touches, fit, gates, save.

Pure Python (no Qt, no hardware) so the wizard's logic is testable against the
fit module directly; the dialog is a thin view over this session. Design:
AI-docs 2026-08-14-calibration-gui-design.md ("Holder rotation mode") and the
spec's Step 3.

The session measures ONE thing: the holder's rotation. Its fitted translation
is never persisted anywhere - a1 always comes from the per-format calibration -
which is exactly what makes the one-corner-per-well method valid: a constant
same-feature offset cancels out of the centered rotation estimate and lands in
the discarded translation.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

import control._def
import control.utils
from control.core.mosaic_utils import format_well_id
from control.core.plate_fit import circumcenter as _circumcenter, fit_plate_placement, PlateFitError, PlateFitResult
from control.core.plate_transform import has_well_grid, PlateTransform, plate_transform_for, resolve_rotation_deg
from control.models.sample_format_config import (
    load_user_sample_formats,
    load_user_sample_formats_for_edit,
    save_user_sample_formats,
)
from control.models.plate_holder import (
    clear_plate_holder,
    HolderMeasuredPoint,
    HolderMeasurement,
    load_plate_holder,
    plate_holder_record_exists,  # re-exported: the dialog's one import for the saved angle
    PlateHolder,
    save_plate_holder,
)
import squid.logging

log = squid.logging.get_logger(__name__)


# Same-corner features the square-well method may nominate (shown once,
# applied to every well - mixing corners would break the constant-offset
# cancellation the method depends on).
CORNER_FEATURES = ("corner_top_left", "corner_top_right", "corner_bottom_left", "corner_bottom_right")

# The dialog has this many reference-well rows; the session fills as many as
# the plate can offer. Four is the default and three the accepted fallback;
# two would fit but leaves the mis-click test without residual freedom.
REFERENCE_WELL_SLOTS = 4
MIN_WELLS_TO_FIT = 3


class SessionError(ValueError):
    """The session cannot proceed as asked; .args[0] is user-facing copy."""


def circumcenter(p1, p2, p3) -> Tuple[float, float, float]:
    """plate_fit.circumcenter with the wizard's rim-touch copy on failure."""
    try:
        return _circumcenter(p1, p2, p3)
    except PlateFitError:
        raise SessionError(
            "These three rim touches are (nearly) in a line - they don't define a circle. "
            "Re-touch the rim at three well-separated points."
        )


@dataclass
class ReferenceWell:
    well_id: str
    row: int
    col: int
    touches: List[Tuple[float, float]] = field(default_factory=list)
    # The point the fit consumes: the circumcenter (round wells) or the single
    # corner touch (square wells). None until enough touches are recorded.
    point_mm: Optional[Tuple[float, float]] = None
    fitted_radius_mm: Optional[float] = None  # round wells only; QC display


class HolderAlignmentSession:
    """One run of the holder-rotation mode, on whichever plate is loaded."""

    def __init__(self, format_: str):
        settings = control._def.get_wellplate_settings(format_)
        if not has_well_grid(settings):
            raise SessionError(f"{format_} anchors at the current stage position - there is no grid to calibrate.")
        self.format = format_
        self.pitch_x_mm = settings["well_spacing_x_mm"]
        self.pitch_y_mm = settings["well_spacing_y_mm"]
        self.well_size_mm = settings["well_size_mm"]
        self.rows = settings["rows"]
        self.cols = settings["cols"]
        self.skip = settings["number_of_skip"]
        # Per-well method follows well_shape, never asked. The eyeballed-center
        # variant is deliberately not represented here at all.
        self.touches_per_well = 3 if settings["well_shape"] == "circle" else 1
        self.feature = "center" if self.touches_per_well == 3 else CORNER_FEATURES[0]
        self.reference_wells: List[ReferenceWell] = [
            self._make_well(r, c) for (r, c) in self._default_reference_indices()
        ]

    # ------------------------------------------------------------------ wells

    def _make_well(self, row: int, col: int) -> ReferenceWell:
        return ReferenceWell(well_id=format_well_id(row, col), row=row, col=col)

    def _in_window(self, row: int, col: int) -> bool:
        return self.skip <= row <= self.rows - 1 - self.skip and self.skip <= col <= self.cols - 1 - self.skip

    def _reachable(self, transform: PlateTransform, row: int, col: int) -> bool:
        """Can the stage drive to this well? (Transform hoisted by callers:
        resolving it is file IO, and the reference/query scans loop all wells.)"""
        return control.utils.within_travel(*transform.well_center_mm(row, col))

    def _default_reference_indices(self) -> List[Tuple[int, int]]:
        """The extreme reachable corners of the skip window - computed, never
        hardcoded: there is no clamp on the move path, so a hardcoded corner
        (e.g. AF48 on 1536) would command an out-of-limit move."""
        transform = plate_transform_for(self.format)
        candidates = [
            (r, c)
            for r in range(self.rows)
            for c in range(self.cols)
            if self._in_window(r, c) and self._reachable(transform, r, c)
        ]
        if len(candidates) < MIN_WELLS_TO_FIT:
            raise SessionError(
                f"Fewer than {MIN_WELLS_TO_FIT} wells of {self.format} are inside the stage travel limits - "
                f"holder alignment cannot be measured on this plate."
            )
        corner_scores = (
            lambda rc: -(rc[0] + rc[1]),  # top-left
            lambda rc: rc[1] - rc[0],  # top-right
            lambda rc: rc[0] - rc[1],  # bottom-left
            lambda rc: rc[0] + rc[1],  # bottom-right
        )
        picked: List[Tuple[int, int]] = []
        for score in corner_scores:
            best = max(candidates, key=score)
            if best not in picked:
                picked.append(best)
        # A single row or column has only two extrema. Fill the remaining slots
        # with whichever candidate is farthest from the ones picked.

        def gap(rc):
            return min(abs(rc[0] - r) + abs(rc[1] - c) for r, c in picked)

        while len(picked) < min(REFERENCE_WELL_SLOTS, len(candidates)):
            picked.append(max(candidates, key=gap))
        return picked

    def nominate(self, index: int, well_id: str):
        """Swap a reference well for one the user prefers (A1 may be empty,
        unreachable, or hard to identify - Micro-Manager's spinner lesson)."""
        row, col = self._resolve_well_id(well_id)
        if any(i != index and w.row == row and w.col == col for i, w in enumerate(self.reference_wells)):
            raise SessionError(f"{well_id} is already one of the reference wells.")
        self.reference_wells[index] = self._make_well(row, col)

    def _resolve_well_id(self, well_id: str) -> Tuple[int, int]:
        """A well the stage can be sent to: named properly, on this plate, and
        inside the travel limits. Every path that ends in a move goes through
        here - there is no clamp on the move itself."""
        # Stricter than mosaic_utils.parse_well_id on purpose: that parser
        # accepts interleaved forms like "12A"; a typo here must not silently
        # resolve to a well.
        match = re.match(r"^([A-Za-z]+)(\d+)$", well_id.strip())
        if not match:
            raise SessionError(f"{well_id!r} is not a well name like 'A1' or 'AE47'.")
        row, col = control.utils.row_to_index(match.group(1)), int(match.group(2)) - 1
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise SessionError(f"{well_id} is outside the {self.format} grid.")
        if not self._reachable(plate_transform_for(self.format), row, col):
            raise SessionError(f"{well_id} is outside the stage travel limits.")
        return row, col

    def reference_center_mm(self, index: int) -> Tuple[float, float]:
        """Where the CURRENT calibration puts the center of reference well
        `index` - the place to drive to before touching it. The format's
        measured A1 and whatever rotation is saved today; the fit in progress
        is not involved (it needs this well's touch first)."""
        well = self.reference_wells[index]
        return plate_transform_for(self.format).well_center_mm(well.row, well.col)

    # ---------------------------------------------------------------- touches

    def set_corner_feature(self, feature: str):
        if self.touches_per_well != 1:
            raise SessionError("Corner choice only applies to square-well plates.")
        if feature not in CORNER_FEATURES:
            raise SessionError(f"Unknown corner {feature!r}.")
        if any(w.touches for w in self.reference_wells):
            raise SessionError("The corner must be chosen before the first point is set - it applies to every well.")
        self.feature = feature

    def record_touch(self, index: int, x_mm: float, y_mm: float):
        """Record one touch on reference well `index`; derives the fit point
        when the well's touch count is complete."""
        well = self.reference_wells[index]
        if len(well.touches) >= self.touches_per_well:
            raise SessionError(f"{well.well_id} already has its {self.touches_per_well} touch(es) - undo first.")
        if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
            # The fit refuses NaN too, but that would surface as a traceback at
            # the next refresh; here it is the dialog's own message, at the click.
            raise SessionError(f"The stage reported no position for {well.well_id} - try the touch again.")
        well.touches.append((float(x_mm), float(y_mm)))
        if len(well.touches) == self.touches_per_well:
            if self.touches_per_well == 3:
                try:
                    cx, cy, radius = circumcenter(*well.touches)
                except SessionError:
                    well.touches.pop()
                    raise
                well.point_mm = (cx, cy)
                well.fitted_radius_mm = radius
            else:
                well.point_mm = well.touches[0]

    def undo_touch(self, index: int):
        well = self.reference_wells[index]
        if not well.touches:
            raise SessionError(f"{well.well_id} has no touches to undo.")
        well.touches.pop()
        well.point_mm = None
        well.fitted_radius_mm = None

    @property
    def wells_measured(self) -> int:
        return sum(1 for w in self.reference_wells if w.point_mm is not None)

    @property
    def can_fit(self) -> bool:
        return self.wells_measured >= MIN_WELLS_TO_FIT

    # -------------------------------------------------------------------- fit

    def _nominal_mm(self, well: ReferenceWell) -> Tuple[float, float]:
        return (well.col * self.pitch_x_mm, well.row * self.pitch_y_mm)

    def fit(self) -> PlateFitResult:
        """Fit rotation from the measured wells. Recomputed fresh on every
        call - quality numbers are never cached, let alone persisted."""
        if not self.can_fit:
            raise SessionError(f"Only {self.wells_measured} wells measured - at least {MIN_WELLS_TO_FIT} are required.")
        measured = [w for w in self.reference_wells if w.point_mm is not None]
        # Query only wells the stage can DRIVE to: predicted error at an
        # unreachable well is moot (the planner drops those FOVs), and
        # worst_well doubles as the Drive-to-Test target - it must never
        # command an out-of-limit move.
        transform = plate_transform_for(self.format)
        query = [
            (format_well_id(r, c), c * self.pitch_x_mm, r * self.pitch_y_mm)
            for r in range(self.rows)
            for c in range(self.cols)
            if self._in_window(r, c) and self._reachable(transform, r, c)
        ]
        return fit_plate_placement(
            [self._nominal_mm(w) for w in measured],
            [w.point_mm for w in measured],
            well_size_mm=self.well_size_mm,
            pitch_x_mm=self.pitch_x_mm,
            pitch_y_mm=self.pitch_y_mm,
            query_wells=query,
        )

    # ----------------------------------------------------------------- verify

    def predicted_touch_mm(self, well_id: str) -> Tuple[float, float]:
        """Where the fitted pose predicts the SAME feature of `well_id` sits.

        Valid for both methods because the fit's translation carries the same
        constant feature offset the touches did; the hold-out residual against
        another same-feature touch is therefore honest. This is a VERIFY tool -
        nothing here is persisted.
        """
        row, col = self._resolve_well_id(well_id)
        result = self.fit()
        # A fit result IS a placement - evaluate it through the one owner of
        # the well -> stage math instead of re-rolling the trig here.
        fitted = PlateTransform(
            a1_x_mm=result.a1_x_mm,
            a1_y_mm=result.a1_y_mm,
            pitch_x_mm=self.pitch_x_mm,
            pitch_y_mm=self.pitch_y_mm,
            rotation_deg=result.rotation_deg,
        )
        return fitted.well_center_mm(row, col)

    def holdout_residual_um(self, well_id: str, measured_xy: Tuple[float, float]) -> float:
        """The only number in the report that is not a model: the miss distance
        at a well that was NOT used in the fit."""
        row, col = self._resolve_well_id(well_id)
        if any(w.row == row and w.col == col and w.point_mm is not None for w in self.reference_wells):
            raise SessionError(f"{well_id} was used in the fit - check a well that was not.")
        predicted = self.predicted_touch_mm(well_id)
        return math.hypot(measured_xy[0] - predicted[0], measured_xy[1] - predicted[1]) * 1000.0

    # ------------------------------------------------------------------- save

    def save(self, confirm_warnings: bool = False, clear_overrides: Sequence[str] = ()) -> PlateHolder:
        """Write the minimal holder record. Nothing else is written: the
        fitted translation dies here by design."""
        result = self.fit()
        if result.rejected:
            raise SessionError("; ".join(g.message for g in result.gates if g.level == "reject"))
        if result.needs_confirmation and not confirm_warnings:
            raise SessionError("; ".join(g.message for g in result.gates if g.level == "warn"))

        holder = PlateHolder(
            rotation_deg=result.rotation_deg,
            measured=HolderMeasurement(
                on=self.format,
                feature=self.feature,
                points=[
                    HolderMeasuredPoint(well=w.well_id, x_mm=w.point_mm[0], y_mm=w.point_mm[1])
                    for w in self.reference_wells
                    if w.point_mm is not None
                ],
                timestamp=datetime.now().isoformat(timespec="seconds"),
            ),
        )
        save_plate_holder(holder)
        log.info(f"Holder rotation saved: {result.rotation_deg:.2f} deg, measured on {self.format}.")

        if clear_overrides:
            clear_rotation_overrides(clear_overrides)
        return holder

    # ------------------------------------------------------------------ status

    def status_line(self) -> str:
        """The status card's first line: current angle + provenance."""
        angle, source = resolve_rotation_deg(self.format)
        if source == "none":
            return "No holder rotation measured - 0.00 deg assumed."
        origin = "measured for this format" if source == "measured" else "holder record"
        return f"Current rotation {angle:.2f} deg ({origin})."


# ------------------------------------------------------- the saved angle
# Module level, not session methods: the saved angle belongs to the machine, so
# these must work whatever plate is loaded (glass slide included).


def formats_with_rotation_overrides() -> List[str]:
    """Formats whose definition carries its own measured rotation - they do NOT
    follow a change to the holder record unless the override is cleared."""
    stored = load_user_sample_formats()
    if stored is None:
        return []
    return sorted(fmt for fmt, d in stored.formats.items() if d.rotation_deg is not None)


def clear_rotation_overrides(formats: Sequence[str]):
    stored = load_user_sample_formats_for_edit()  # raises on a damaged file rather than save over it
    for fmt in formats:
        definition = stored.formats.get(fmt)
        if definition is not None and definition.rotation_deg is not None:
            definition.rotation_deg = None
            definition.rotation_measured = None
            log.info(f"Cleared the measured rotation override for {fmt!r}; it now inherits the holder angle.")
    save_user_sample_formats(stored)


def saved_rotation_summary() -> Optional[str]:
    """The saved record in one operator-facing phrase ("0.37 deg, measured on
    1536 well plate"); "an unreadable record" when the file exists but does not
    parse; None when there is nothing saved."""
    holder = load_plate_holder()
    if holder is None:
        return "an unreadable record" if plate_holder_record_exists() else None
    summary = f"{holder.rotation_deg:.2f} deg"
    if holder.measured.on:
        summary += f", measured on {holder.measured.on}"
    return summary


def clear_holder_rotation(clear_overrides: Sequence[str] = ()) -> bool:
    """Remove the machine's holder record: every format that inherits it is
    positioned with 0.00 deg again. The removed points are logged: they were
    the only copy."""
    holder = load_plate_holder()
    removed = clear_plate_holder()
    if removed:
        if holder is None:
            was = "unreadable record removed"
        else:
            points = ", ".join(f"{p.well}=({p.x_mm}, {p.y_mm})" for p in holder.measured.points)
            was = (
                f"was {holder.rotation_deg:.2f} deg, measured on {holder.measured.on!r} "
                f"at {holder.measured.timestamp} from [{points}]"
            )
        log.info(f"Holder rotation cleared: {was}. 0.00 deg now applies.")
    if clear_overrides:
        clear_rotation_overrides(clear_overrides)
    return removed
