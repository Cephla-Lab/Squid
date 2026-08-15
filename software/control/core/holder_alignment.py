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
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import control._def
import control.utils
from control.core.plate_fit import fit_plate_placement, PlateFitResult
from control.core.plate_transform import plate_transform_for, resolve_rotation_deg
from control.models.plate_holder import (
    HolderMeasuredPoint,
    HolderMeasurement,
    PlateHolder,
    save_plate_holder,
)
import squid.logging

log = squid.logging.get_logger(__name__)

# Same-corner features the square-well method may nominate (shown once,
# applied to every well - mixing corners would break the constant-offset
# cancellation the method depends on).
CORNER_FEATURES = ("corner_top_left", "corner_top_right", "corner_bottom_left", "corner_bottom_right")


class SessionError(ValueError):
    """The session cannot proceed as asked; .args[0] is user-facing copy."""


def index_to_row_label(index: int) -> str:
    index += 1
    row = ""
    while index > 0:
        index -= 1
        row = chr(index % 26 + ord("A")) + row
        index //= 26
    return row


def circumcenter(p1, p2, p3) -> Tuple[float, float, float]:
    """Center + radius of the circle through three rim touches.

    The circumcenter cancels the touch-radius error the same way the corner
    midpoint cancels fillet displacement. Collinear touches have no circle.
    """
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        raise SessionError(
            "These three rim touches are (nearly) in a line - they don't define a circle. "
            "Re-touch the rim at three well-separated points."
        )
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


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
        if settings["well_spacing_x_mm"] == 0.0 or settings["well_spacing_y_mm"] == 0.0:
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
        return ReferenceWell(well_id=f"{index_to_row_label(row)}{col + 1}", row=row, col=col)

    def _in_window(self, row: int, col: int) -> bool:
        return self.skip <= row <= self.rows - 1 - self.skip and self.skip <= col <= self.cols - 1 - self.skip

    def _reachable(self, row: int, col: int) -> bool:
        """Can the stage drive to this well under the CURRENT transform?"""
        x, y = plate_transform_for(self.format).well_center_mm(row, col)
        limits = control._def.SOFTWARE_POS_LIMIT
        return limits.X_NEGATIVE <= x <= limits.X_POSITIVE and limits.Y_NEGATIVE <= y <= limits.Y_POSITIVE

    def _default_reference_indices(self) -> List[Tuple[int, int]]:
        """The extreme reachable corners of the skip window - computed, never
        hardcoded: there is no clamp on the move path, so a hardcoded corner
        (e.g. AF48 on 1536) would command an out-of-limit move."""
        candidates = [
            (r, c)
            for r in range(self.rows)
            for c in range(self.cols)
            if self._in_window(r, c) and self._reachable(r, c)
        ]
        if len(candidates) < 3:
            raise SessionError(
                f"Fewer than 3 wells of {self.format} are inside the stage travel limits - "
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
        return picked

    def nominate(self, index: int, well_id: str):
        """Swap a reference well for one the user prefers (A1 may be empty,
        unreachable, or hard to identify - Micro-Manager's spinner lesson)."""
        parsed = self._parse_well_id(well_id)
        if parsed is None:
            raise SessionError(f"{well_id!r} is not a well name like 'A1' or 'AE47'.")
        row, col = parsed
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise SessionError(f"{well_id} is outside the {self.format} grid.")
        if not self._reachable(row, col):
            raise SessionError(f"{well_id} is outside the stage travel limits.")
        if any(i != index and w.row == row and w.col == col for i, w in enumerate(self.reference_wells)):
            raise SessionError(f"{well_id} is already one of the reference wells.")
        self.reference_wells[index] = self._make_well(row, col)

    def _parse_well_id(self, well_id: str) -> Optional[Tuple[int, int]]:
        import re

        match = re.match(r"^([A-Za-z]+)(\d+)$", well_id.strip())
        if not match:
            return None
        return (control.utils.row_to_index(match.group(1)), int(match.group(2)) - 1)

    # ---------------------------------------------------------------- touches

    def set_corner_feature(self, feature: str):
        if self.touches_per_well != 1:
            raise SessionError("Corner choice only applies to square-well plates.")
        if feature not in CORNER_FEATURES:
            raise SessionError(f"Unknown corner {feature!r}.")
        if any(w.touches for w in self.reference_wells):
            raise SessionError("The corner must be chosen before recording - it applies to every well.")
        self.feature = feature

    def record_touch(self, index: int, x_mm: float, y_mm: float):
        """Record one touch on reference well `index`; derives the fit point
        when the well's touch count is complete."""
        well = self.reference_wells[index]
        if len(well.touches) >= self.touches_per_well:
            raise SessionError(f"{well.well_id} already has its {self.touches_per_well} touch(es) - undo first.")
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
        # Four is the default, three the accepted fallback; two would fit but
        # leaves the mis-click test without residual degrees of freedom.
        return self.wells_measured >= 3

    # -------------------------------------------------------------------- fit

    def _nominal_mm(self, well: ReferenceWell) -> Tuple[float, float]:
        return (well.col * self.pitch_x_mm, well.row * self.pitch_y_mm)

    def fit(self) -> PlateFitResult:
        """Fit rotation from the measured wells. Recomputed fresh on every
        call - quality numbers are never cached, let alone persisted."""
        if not self.can_fit:
            raise SessionError(f"Only {self.wells_measured} wells measured - at least 3 are required.")
        measured = [w for w in self.reference_wells if w.point_mm is not None]
        query = [
            (f"{index_to_row_label(r)}{c + 1}", c * self.pitch_x_mm, r * self.pitch_y_mm)
            for r in range(self.rows)
            for c in range(self.cols)
            if self._in_window(r, c)
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
        parsed = self._parse_well_id(well_id)
        if parsed is None:
            raise SessionError(f"{well_id!r} is not a well name.")
        row, col = parsed
        result = self.fit()
        theta = math.radians(result.rotation_deg)
        px, py = col * self.pitch_x_mm, row * self.pitch_y_mm
        return (
            result.a1_x_mm + math.cos(theta) * px - math.sin(theta) * py,
            result.a1_y_mm + math.sin(theta) * px + math.cos(theta) * py,
        )

    def holdout_residual_um(self, well_id: str, measured_xy: Tuple[float, float]) -> float:
        """The only number in the report that is not a model: the miss distance
        at a well that was NOT used in the fit."""
        if any(w.well_id == well_id and w.point_mm is not None for w in self.reference_wells):
            raise SessionError(f"{well_id} was used in the fit - a hold-out must be a different well.")
        predicted = self.predicted_touch_mm(well_id)
        return math.hypot(measured_xy[0] - predicted[0], measured_xy[1] - predicted[1]) * 1000.0

    # ------------------------------------------------------------------- save

    def formats_with_measured_overrides(self) -> List[str]:
        """Formats whose placement carries a rotation measured under the
        PREVIOUS mounting - offered for clearing at save time (the write-time
        staleness handling; there is no counter to expire them otherwise)."""
        from control.models.plate_placement import load_plate_placements

        stored = load_plate_placements()
        if stored is None:
            return []
        return sorted(fmt for fmt, p in stored.placements.items() if p.rotation_deg is not None)

    def save(self, confirm_warnings: bool = False, clear_overrides: Tuple[str, ...] = ()) -> PlateHolder:
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
            self._clear_rotation_overrides(clear_overrides)
        return holder

    def _clear_rotation_overrides(self, formats: Tuple[str, ...]):
        from control.models.plate_placement import load_plate_placements, save_plate_placements

        stored = load_plate_placements()
        if stored is None:
            return
        for fmt in formats:
            placement = stored.placements.get(fmt)
            if placement is not None and placement.rotation_deg is not None:
                placement.rotation_deg = None
                log.info(f"Cleared the measured rotation override for {fmt!r}; it now inherits the holder angle.")
        save_plate_placements(stored)

    # ------------------------------------------------------------------ status

    def status_line(self) -> str:
        """The status card's first line: current angle + provenance."""
        angle, source = resolve_rotation_deg(self.format)
        if source == "none":
            return "No holder rotation measured - 0.00 deg assumed."
        origin = "measured for this format" if source == "measured" else "holder record"
        return f"Current rotation {angle:.2f} deg ({origin})."
