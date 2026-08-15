"""Plate geometry -> stage coordinates, in exactly one place.

This module is the single owner of the well -> stage transform that today is
copy-pasted across ScanCoordinates, the SiLA2 coordinates class, the MCP
server, and both well-selector widgets. It is pure Python: no Qt, no hardware,
no module-global reads inside the math.

Design notes (see AI-docs/Squid/in-progress/2026-07-28-wellplate-rotation-
calibration-design.md):

* Scalar arithmetic in the legacy operand order, NOT a matrix multiply.
  ``a1 + (c*dx - s*dy) + off`` is ==-identical to the legacy ``a1 + dx + off``
  when rotation is 0 (cos(0.0) == 1.0 and sin(0.0) == 0.0 exactly; multiplying
  by 1.0 and adding -0.0*dy are IEEE-754 exact). Folding ``a1 + off`` into a
  matrix translation column re-associates the sum and drifts the last bit in
  ~27% of cases - never do that here.
* Rotation (when nonzero) pivots on A1, so ``rotation_deg == 0`` leaves the
  meaning of ``a1_x_mm``/``a1_y_mm`` untouched, and scaling by the pitches
  happens in the plate frame BEFORE the rotation (R*S != S*R once the pitches
  differ).
* ``pitch_x_mm`` multiplies the COLUMN index; ``pitch_y_mm`` the ROW index.
"""

import math
from dataclasses import dataclass, replace
from typing import Tuple

import control._def


class PlateGeometryError(ValueError):
    """The format has no grid to transform (e.g. glass slide, pitch 0)."""


@dataclass(frozen=True)
class WellplateSettings:
    """The payload of signalWellplateSettings.

    Replaces the old 10-positional-arg Qt signal, whose slots silently dropped
    trailing arguments when their signatures were shorter (ScanCoordinates
    accepted 8 of the 10). An object cannot be truncated in transit, and new
    fields do not require touching every connect() in lockstep.
    """

    format: str
    a1_x_mm: float
    a1_y_mm: float
    a1_x_pixel: int
    a1_y_pixel: int
    well_size_mm: float
    well_spacing_mm: float
    number_of_skip: int
    rows: int
    cols: int
    # Per-axis geometry + shape (derived from the scalar columns for every
    # shipped format; anisotropic carriers set them via the user-formats YAML).
    well_spacing_x_mm: float = 0.0  # multiplies the COLUMN index
    well_spacing_y_mm: float = 0.0  # multiplies the ROW index
    well_size_x_mm: float = 0.0
    well_size_y_mm: float = 0.0
    well_shape: str = "circle"  # "circle" | "rectangle" (square = x == y)

    @staticmethod
    def from_format(format_: str) -> "WellplateSettings":
        s = control._def.get_wellplate_settings(format_)
        # The COMPOSED a1 (catalog + placement delta), so the navigation viewer
        # and the planner agree; both compose through the same resolver rules.
        placement = _placement_for(format_)
        a1_x = s["a1_x_mm"] + (placement.a1_dx_mm if placement else 0.0)
        a1_y = s["a1_y_mm"] + (placement.a1_dy_mm if placement else 0.0)
        return WellplateSettings(
            format=format_,
            a1_x_mm=a1_x,
            a1_y_mm=a1_y,
            a1_x_pixel=s["a1_x_pixel"],
            a1_y_pixel=s["a1_y_pixel"],
            well_size_mm=s["well_size_mm"],
            well_spacing_mm=s["well_spacing_mm"],
            number_of_skip=s["number_of_skip"],
            rows=s["rows"],
            cols=s["cols"],
            well_spacing_x_mm=s["well_spacing_x_mm"],
            well_spacing_y_mm=s["well_spacing_y_mm"],
            well_size_x_mm=s["well_size_x_mm"],
            well_size_y_mm=s["well_size_y_mm"],
            well_shape=s["well_shape"],
        )

    @staticmethod
    def glass_slide() -> "WellplateSettings":
        # The literal values the old code emitted for glass slide; only
        # reachable if the CSV's "glass slide" row is somehow absent.
        return WellplateSettings("glass slide", 0, 0, 0, 0, 0, 0, 0, 1, 1)


@dataclass(frozen=True)
class PlateTransform:
    a1_x_mm: float
    a1_y_mm: float
    pitch_x_mm: float  # multiplies the COLUMN index
    pitch_y_mm: float  # multiplies the ROW index
    rotation_deg: float = 0.0  # + = CCW in the stage XY math frame; pivot = A1
    offset_x_mm: float = 0.0  # absorbs the legacy WELLPLATE_OFFSET_X_mm
    offset_y_mm: float = 0.0

    def well_center_mm(self, row: int, col: int) -> Tuple[float, float]:
        """Stage position of the center of well (row, col), 0-indexed."""
        dx = col * self.pitch_x_mm  # scale in the plate frame FIRST,
        dy = row * self.pitch_y_mm  # then rotate - R*S != S*R when x != y
        if self.rotation_deg == 0.0:
            # Legacy operand order, kept verbatim so the golden oracle holds
            # with exact ==. (cos/sin of 0.0 would also be exact, but keeping
            # the branch makes the identity path self-evident.)
            return (
                self.a1_x_mm + dx + self.offset_x_mm,
                self.a1_y_mm + dy + self.offset_y_mm,
            )
        c = math.cos(math.radians(self.rotation_deg))
        s = math.sin(math.radians(self.rotation_deg))
        return (
            self.a1_x_mm + (c * dx - s * dy) + self.offset_x_mm,
            self.a1_y_mm + (s * dx + c * dy) + self.offset_y_mm,
        )

    def well_index_at(self, x_mm: float, y_mm: float) -> Tuple[float, float]:
        """Inverse: fractional (row, col) at a stage position.

        New capability - nothing computes 'which well am I in' today. Raises
        PlateGeometryError when either pitch is 0 (glass slide, format '0'),
        which callers must short-circuit to identity before reaching here.
        """
        if self.pitch_x_mm == 0.0 or self.pitch_y_mm == 0.0:
            raise PlateGeometryError("format has no well grid (pitch is 0)")
        px = x_mm - self.a1_x_mm - self.offset_x_mm
        py = y_mm - self.a1_y_mm - self.offset_y_mm
        if self.rotation_deg != 0.0:
            c = math.cos(math.radians(self.rotation_deg))
            s = math.sin(math.radians(self.rotation_deg))
            px, py = c * px + s * py, -s * px + c * py  # R(-theta)
        return (py / self.pitch_y_mm, px / self.pitch_x_mm)

    def nominal(self) -> "PlateTransform":
        """Plate-frame copy: no rotation, no offset.

        For display assets (the plate PNG renderer) that must stay registered
        against the offset-free a1_x_pixel = round(a1_x_mm * scale) convention.
        """
        return replace(self, rotation_deg=0.0, offset_x_mm=0.0, offset_y_mm=0.0)


def _placement_for(format_: str):
    from control.models.plate_placement import load_plate_placements

    stored = load_plate_placements()
    if stored is None:
        return None
    return stored.placements.get(format_)


def resolve_rotation_deg(format_: str) -> Tuple[float, str]:
    """One branch, zero arithmetic between stored angles (override-with-inherit).

    Returns (angle, source) where source is "measured" (this format's placement
    entry), "holder" (the machine's holder record), or "none". Pitch-0 formats
    (glass slide, '0') always resolve to 0.0: with a 1x1 grid the only well sits
    at the pivot, so an angle could not move anything anyway.
    """
    settings = control._def.get_wellplate_settings(format_)
    if settings["well_spacing_x_mm"] == 0.0 or settings["well_spacing_y_mm"] == 0.0:
        return 0.0, "none"
    placement = _placement_for(format_)
    if placement is not None and placement.rotation_deg is not None:
        return placement.rotation_deg, "measured"
    from control.models.plate_holder import load_plate_holder

    holder = load_plate_holder()
    if holder is not None:
        return holder.rotation_deg, "holder"
    return 0.0, "none"


def plate_transform_for(format_: str, *, apply_legacy_offset: bool = True) -> PlateTransform:
    """The single resolver: nominal geometry + measured placement + legacy offset.

    Reads control._def and the placement sidecar at CALL time (never cache the
    result across calls - the snapshot-at-__init__ pattern is the bug this
    module exists to end). Composition, one owner per quantity:

      a1            <- catalog (shipped CSV + user YAML) + placement DELTA
      pitch/shape   <- catalog (shipped CSV + user YAML)
      rotation      <- placement override when measured, else the holder record
      legacy offset <- WELLPLATE_OFFSET_*, SUPPRESSED for any format that has a
                       placement entry: the delta is the whole measured
                       correction, so exactly one offset is live per format and
                       double-apply is unrepresentable.
    """
    settings = control._def.get_wellplate_settings(format_)
    placement = _placement_for(format_)
    rotation, _source = resolve_rotation_deg(format_)
    if placement is not None:
        a1_x = settings["a1_x_mm"] + placement.a1_dx_mm
        a1_y = settings["a1_y_mm"] + placement.a1_dy_mm
        offset_x = 0.0
        offset_y = 0.0
    else:
        a1_x = settings["a1_x_mm"]
        a1_y = settings["a1_y_mm"]
        offset_x = control._def.WELLPLATE_OFFSET_X_mm if apply_legacy_offset else 0.0
        offset_y = control._def.WELLPLATE_OFFSET_Y_mm if apply_legacy_offset else 0.0
    if not apply_legacy_offset:
        offset_x = 0.0
        offset_y = 0.0
    return PlateTransform(
        a1_x_mm=a1_x,
        a1_y_mm=a1_y,
        pitch_x_mm=settings["well_spacing_x_mm"],
        pitch_y_mm=settings["well_spacing_y_mm"],
        rotation_deg=rotation,
        offset_x_mm=offset_x,
        offset_y_mm=offset_y,
    )
