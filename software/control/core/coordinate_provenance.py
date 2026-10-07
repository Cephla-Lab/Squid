"""Provenance stamps for saved scan-coordinate CSVs.

A coordinate file is a list of ABSOLUTE stage positions, computed under the
placement (a1 + rotation) and well spacing that were live when it was saved.
Loading it under a different placement silently replays stale positions - the
wells moved, the file did not. The stamp records what the coordinates were
computed under so "Load Coordinates" can say so out loud instead.

The stamp is one comment line of JSON prepended to the CSV:

    # squid-scan-coordinates v1 {"format": "96 well plate", ...}

Old, unstamped files load exactly as before (no stamp -> no check possible).
The check WARNS and still loads: the user may know the plate has not moved.
"""

import json
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd

import control._def
from control.core.plate_fit import ROTATION_QUANTUM_DEG
from control.core.plate_transform import plate_transform_for, resolve_rotation_deg
import squid.logging

log = squid.logging.get_logger(__name__)

STAMP_PREFIX = "# squid-scan-coordinates v1 "

# Comparison tolerances: half the fit's rotation quantum (derived, so the two
# cannot drift apart), and 1 um on a1 and on the well spacing - anything below
# these cannot move a well by a visible amount.
ROTATION_TOL_DEG = ROTATION_QUANTUM_DEG / 2
A1_TOL_MM = 0.001
WELL_SPACING_TOL_MM = 0.001

# The v1 fields and their types. Stamps written before the well spacing was
# recorded lack those two keys; they still validate and simply skip that check.
_NUMBER = (int, float)
_REQUIRED_FIELDS = {"format": str, "a1_x_mm": _NUMBER, "a1_y_mm": _NUMBER, "rotation_deg": _NUMBER}
_OPTIONAL_FIELDS = {"well_spacing_x_mm": _NUMBER, "well_spacing_y_mm": _NUMBER}


def make_stamp(format_: str) -> dict:
    """The placement the coordinates are being computed under, right now."""
    transform = plate_transform_for(format_)
    rotation_deg, rotation_source = resolve_rotation_deg(format_)
    spacing_x_mm, spacing_y_mm = _well_spacing_mm(format_)
    # well (0,0) IS the effective A1 (composed a1 + any legacy offset; the
    # rotation pivots there, so this is exact for any angle)
    a1_x_mm, a1_y_mm = transform.well_center_mm(0, 0)
    return {
        "format": format_,
        "a1_x_mm": a1_x_mm,
        "a1_y_mm": a1_y_mm,
        "rotation_deg": rotation_deg,
        "rotation_source": rotation_source,
        "well_spacing_x_mm": spacing_x_mm,
        "well_spacing_y_mm": spacing_y_mm,
        "saved": datetime.now().isoformat(timespec="seconds"),
    }


def _well_spacing_mm(format_: str) -> Tuple[float, float]:
    settings = control._def.get_wellplate_settings(format_)
    return settings["well_spacing_x_mm"], settings["well_spacing_y_mm"]


def _spacing_text(x_mm: float, y_mm: float) -> str:
    return f"{x_mm:.3f} mm" if x_mm == y_mm else f"({x_mm:.3f} x {y_mm:.3f}) mm"


def _is_valid_stamp(stamp) -> bool:
    """Every v1 field present with the right type. A stamp missing a1_y_mm or
    carrying a string rotation would otherwise raise inside the staleness
    check - after the coordinates were already loaded."""
    if not isinstance(stamp, dict):
        return False
    for key, kind in {**_REQUIRED_FIELDS, **_OPTIONAL_FIELDS}.items():
        if key not in stamp:
            if key in _REQUIRED_FIELDS:
                return False
            continue
        value = stamp[key]
        if not isinstance(value, kind) or isinstance(value, bool):  # bool is an int to isinstance
            return False
    return True


def parse_stamp(line: str) -> Optional[dict]:
    """The stamp carried by `line`, or None when it is not a valid v1 stamp
    (the file then loads as if unstamped - a damaged label is not a reason to
    refuse the coordinates)."""
    if not line.startswith(STAMP_PREFIX):
        return None
    try:
        stamp = json.loads(line[len(STAMP_PREFIX) :])
    except ValueError:
        log.warning(f"Unparseable scan-coordinates stamp ignored: {line!r}")
        return None
    if not _is_valid_stamp(stamp):
        log.warning(f"Scan-coordinates stamp with missing or mistyped fields ignored: {line!r}")
        return None
    return stamp


def staleness_warning(stamp: dict, current_format: str) -> Optional[str]:
    """A human-readable reason the stamped coordinates may be stale, or None.

    Compares the stamp against the CURRENT resolution of the stamped format -
    if the placement changed since the save, the absolute positions in the
    file no longer land where the wells are.
    """
    problems = []
    stamped_format = stamp.get("format")
    if stamped_format and stamped_format != current_format:
        problems.append(f"they were saved for {stamped_format!r} but the selected format is {current_format!r}")

    if stamped_format:
        try:
            transform = plate_transform_for(stamped_format)
            rotation_now, _ = resolve_rotation_deg(stamped_format)
        except Exception:
            # e.g. a custom format that no longer exists in the catalog
            problems.append(f"the saved format {stamped_format!r} is not in the current catalog")
        else:
            if "rotation_deg" in stamp and abs(stamp["rotation_deg"] - rotation_now) > ROTATION_TOL_DEG:
                problems.append(
                    f"the plate rotation changed from {stamp['rotation_deg']:.2f} deg at save time "
                    f"to {rotation_now:.2f} deg now"
                )
            a1_x_now, a1_y_now = transform.well_center_mm(0, 0)
            if "a1_x_mm" in stamp and (
                abs(stamp["a1_x_mm"] - a1_x_now) > A1_TOL_MM or abs(stamp["a1_y_mm"] - a1_y_now) > A1_TOL_MM
            ):
                problems.append(
                    f"the measured A1 position changed from "
                    f"({stamp['a1_x_mm']:.3f}, {stamp['a1_y_mm']:.3f}) mm at save time to "
                    f"({a1_x_now:.3f}, {a1_y_now:.3f}) mm now"
                )
            # Every well but A1 moves with the spacing (a corrected custom
            # format, say) while A1 and rotation stay put.
            spacing_x_now, spacing_y_now = _well_spacing_mm(stamped_format)
            if "well_spacing_x_mm" in stamp and (
                abs(stamp["well_spacing_x_mm"] - spacing_x_now) > WELL_SPACING_TOL_MM
                or abs(stamp["well_spacing_y_mm"] - spacing_y_now) > WELL_SPACING_TOL_MM
            ):
                problems.append(
                    f"the well spacing changed from "
                    f"{_spacing_text(stamp['well_spacing_x_mm'], stamp['well_spacing_y_mm'])} at save time to "
                    f"{_spacing_text(spacing_x_now, spacing_y_now)} now"
                )

    if not problems:
        return None
    return (
        "These coordinates may be stale: "
        + "; and ".join(problems)
        + ". The file stores absolute stage positions, so they will land where the wells "
        "USED to be. Re-generate the coordinates from the well selection if in doubt."
    )


def write_scan_coordinates_csv(path: str, df: pd.DataFrame, format_: str) -> None:
    with open(path, "w", newline="") as f:
        f.write(STAMP_PREFIX + json.dumps(make_stamp(format_)) + "\n")
        df.to_csv(f, index=False)


def read_scan_coordinates_csv(path: str) -> Tuple[pd.DataFrame, Optional[dict]]:
    """Read a scan-coordinates CSV, stamped or legacy-unstamped."""
    with open(path, "r") as f:
        first_line = f.readline()
    # The prefix alone decides whether the first line is data: a stamp that
    # does not parse is ignored (warned above), not fed to pandas as a header.
    stamp = parse_stamp(first_line)
    df = pd.read_csv(path, skiprows=1 if first_line.startswith(STAMP_PREFIX) else 0)
    return df, stamp
