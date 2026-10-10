"""Provenance stamps for saved scan-coordinate CSVs.

A coordinate file is a list of ABSOLUTE stage positions, computed under the
placement (A1, rotation, well spacing) that was live when it was saved.
Loading it under a different placement silently replays stale positions - the
wells moved, the file did not. The stamp records where the wells were when
the file was saved so "Load Coordinates" can say so out loud instead.

The stamp is one comment line of JSON prepended to the CSV:

    # squid-scan-coordinates v1 {"format": "96 well plate", "wells": [...], ...}

It carries the stage positions of three corner wells - A1, the end of row A,
the end of column 1 - computed by the same transform the planner uses. The
well grid is a linear map of the row/column index, so those three fix every
well: whatever moves wells (A1, rotation, well spacing) moves at least one of
them, and one check at one tolerance covers it all.

Old, unstamped files load exactly as before (no stamp -> no check possible).
The check WARNS and still loads: the user may know the plate has not moved.
"""

import math
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError

import control._def
from control.core.mosaic_utils import format_well_id
from control.core.plate_fit import ROTATION_QUANTUM_DEG
from control.core.plate_transform import plate_transform_for, resolve_rotation_deg
import squid.logging

log = squid.logging.get_logger(__name__)

STAMP_PREFIX = "# squid-scan-coordinates v1 "

# A well that moved by less than this cannot have moved by a visible amount.
POSITION_TOL_MM = 0.001
# For the message only: half the fit's rotation quantum (derived, so the two
# cannot drift apart) decides whether the rotation is worth mentioning.
ROTATION_TOL_DEG = ROTATION_QUANTUM_DEG / 2


class StampedWell(BaseModel):
    model_config = ConfigDict(strict=True)

    well: str
    row: int
    col: int
    x_mm: float
    y_mm: float


class ScanCoordinatesStamp(BaseModel):
    """Strict: a mistyped field (a string rotation, a bool) is a damaged stamp,
    not something to coerce - it would otherwise raise inside the staleness
    check, after the coordinates were already loaded."""

    model_config = ConfigDict(strict=True)

    format: str
    wells: List[StampedWell]
    rotation_deg: float  # context for the message; the positions carry the check
    rotation_source: str
    saved: str


def make_stamp(format_: str) -> ScanCoordinatesStamp:
    """Where the corner wells are, right now, under the planner's transform."""
    transform = plate_transform_for(format_)
    rotation_deg, rotation_source = resolve_rotation_deg(format_)
    settings = control._def.get_wellplate_settings(format_)
    corners = [(0, 0), (0, settings["cols"] - 1), (settings["rows"] - 1, 0)]
    wells = []
    for row, col in dict.fromkeys(corners):  # a 1x1 grid has one corner, not three
        x_mm, y_mm = transform.well_center_mm(row, col)
        wells.append(StampedWell(well=format_well_id(row, col), row=row, col=col, x_mm=x_mm, y_mm=y_mm))
    return ScanCoordinatesStamp(
        format=format_,
        wells=wells,
        rotation_deg=rotation_deg,
        rotation_source=rotation_source,
        saved=datetime.now().isoformat(timespec="seconds"),
    )


def parse_stamp(line: str) -> Optional[ScanCoordinatesStamp]:
    """The stamp carried by `line`, or None when it is not a valid v1 stamp
    (the file then loads as if unstamped - a damaged label is not a reason to
    refuse the coordinates)."""
    if not line.startswith(STAMP_PREFIX):
        return None
    try:
        return ScanCoordinatesStamp.model_validate_json(line[len(STAMP_PREFIX) :])
    except ValidationError:
        log.warning(f"Damaged scan-coordinates stamp ignored: {line!r}")
        return None


def staleness_warning(stamp: ScanCoordinatesStamp, current_format: str) -> Optional[str]:
    """A human-readable reason the stamped coordinates may be stale, or None.

    Compares the stamped well positions against where the CURRENT resolution
    of the stamped format puts those wells - if the placement changed since
    the save, the absolute positions in the file no longer land on the wells.
    """
    problems = []
    if stamp.format != current_format:
        problems.append(f"they were saved for {stamp.format!r} but the selected format is {current_format!r}")

    try:
        transform = plate_transform_for(stamp.format)
        rotation_now, _ = resolve_rotation_deg(stamp.format)
    except Exception:
        # e.g. a custom format that no longer exists in the catalog
        problems.append(f"the saved format {stamp.format!r} is not in the current catalog")
    else:
        moved = []
        for well in stamp.wells:
            x_now, y_now = transform.well_center_mm(well.row, well.col)
            distance_mm = math.hypot(x_now - well.x_mm, y_now - well.y_mm)
            if distance_mm > POSITION_TOL_MM:
                moved.append(f"{well.well} by {distance_mm * 1000:.0f} um")
        if moved:
            reason = "wells moved since the file was saved: " + ", ".join(moved)
            if abs(stamp.rotation_deg - rotation_now) > ROTATION_TOL_DEG:
                reason += f" (rotation {stamp.rotation_deg:.2f} deg at save time, {rotation_now:.2f} deg now)"
            problems.append(reason)

    if not problems:
        return None
    return (
        "These coordinates may be stale: "
        + "; and ".join(problems)
        + ". The file stores absolute stage positions, so they will land where the wells "
        "USED to be. Re-generate the coordinates from the well selection if in doubt."
    )


def write_scan_coordinates_csv(path: str, df: pd.DataFrame, stamp: Optional[ScanCoordinatesStamp]) -> None:
    """`stamp` is written as the provenance line; None writes a legacy-shaped,
    unstamped file (rows of unknown provenance stay unknown)."""
    with open(path, "w", newline="") as f:
        if stamp is not None:
            f.write(STAMP_PREFIX + stamp.model_dump_json() + "\n")
        df.to_csv(f, index=False)


def read_scan_coordinates_csv(path: str) -> Tuple[pd.DataFrame, Optional[ScanCoordinatesStamp]]:
    """Read a scan-coordinates CSV, stamped or legacy-unstamped."""
    with open(path, "r") as f:
        first_line = f.readline()
    # skip by PREFIX, not by the parsed stamp: a damaged stamp line is still not data
    stamp = parse_stamp(first_line)
    df = pd.read_csv(path, skiprows=1 if first_line.startswith(STAMP_PREFIX) else 0)
    return df, stamp
