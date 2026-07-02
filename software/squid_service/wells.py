"""Well-name parsing and GUI-consistent well-center coordinates.

The coordinate formula matches ScanCoordinates.get_selected_wells
(control/core/scan_coordinates.py): a1 + index*spacing + WELLPLATE_OFFSET.
"""

import re
from typing import List, Tuple

import control._def  # module import: offsets are runtime-modifiable

_WELL_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def row_to_index(row: str) -> int:
    index = 0
    for char in row.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def index_to_row(index: int) -> str:
    index += 1
    row = ""
    while index > 0:
        index -= 1
        row = chr(index % 26 + ord("A")) + row
        index //= 26
    return row


def _parse_one(token: str) -> Tuple[int, int]:
    match = _WELL_RE.match(token.strip())
    if not match:
        raise ValueError(f"Invalid well name: {token!r}")
    return row_to_index(match.group(1)), int(match.group(2)) - 1


def parse_well_names(wells: str) -> List[str]:
    if not wells or not wells.strip():
        raise ValueError("Empty well selection")
    names: List[str] = []
    for part in wells.split(","):
        part = part.strip()
        if ":" in part:
            start, _, end = part.partition(":")
            r0, c0 = _parse_one(start)
            r1, c1 = _parse_one(end)
            if r1 < r0 or c1 < c0:
                raise ValueError(f"Range end before start: {part!r}")
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    names.append(f"{index_to_row(r)}{c + 1}")
        else:
            r, c = _parse_one(part)
            names.append(f"{index_to_row(r)}{c + 1}")
    return names


def well_center_mm(well_name: str, wellplate_settings: dict) -> Tuple[float, float]:
    row_idx, col_idx = _parse_one(well_name)
    rows = int(wellplate_settings.get("rows", 8))
    cols = int(wellplate_settings.get("cols", 12))
    if not (0 <= row_idx < rows and 0 <= col_idx < cols):
        raise ValueError(f"Well {well_name!r} outside {rows}x{cols} plate")
    x = (
        wellplate_settings["a1_x_mm"]
        + col_idx * wellplate_settings["well_spacing_mm"]
        + getattr(control._def, "WELLPLATE_OFFSET_X_mm", 0.0)
    )
    y = (
        wellplate_settings["a1_y_mm"]
        + row_idx * wellplate_settings["well_spacing_mm"]
        + getattr(control._def, "WELLPLATE_OFFSET_Y_mm", 0.0)
    )
    return x, y
