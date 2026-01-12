"""Wellplate coordinate helpers.

Pure functions for converting between well identifiers and physical coordinates.
"""

import re
from typing import Dict, List, Optional, Tuple


def row_index_to_letter(index: int) -> str:
    """Convert a 0-based row index to letter notation (A, B, ..., Z, AA, AB, ...).

    Args:
        index: 0-based row index (0 = A, 1 = B, etc.)

    Returns:
        Letter notation for the row.

    Examples:
        >>> row_index_to_letter(0)
        'A'
        >>> row_index_to_letter(25)
        'Z'
        >>> row_index_to_letter(26)
        'AA'
    """
    index += 1
    row = ""
    while index > 0:
        index -= 1
        row = chr(index % 26 + ord("A")) + row
        index //= 26
    return row


def letter_to_row_index(letter: str) -> int:
    """Convert a letter notation to 0-based row index.

    Args:
        letter: Letter notation (A, B, ..., Z, AA, AB, ...)

    Returns:
        0-based row index.

    Examples:
        >>> letter_to_row_index('A')
        0
        >>> letter_to_row_index('Z')
        25
        >>> letter_to_row_index('AA')
        26
    """
    index = 0
    for char in letter.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def well_id_to_row_col(well_id: str) -> Optional[Tuple[int, int]]:
    """Parse a well ID (e.g., 'A1', 'B12', 'AA3') to row/column indices.

    Args:
        well_id: Well identifier in standard format (letter(s) + number)

    Returns:
        Tuple of (row_index, col_index) both 0-based, or None if invalid.

    Examples:
        >>> well_id_to_row_col('A1')
        (0, 0)
        >>> well_id_to_row_col('B12')
        (1, 11)
    """
    match = re.match(r"^([A-Za-z]+)(\d+)$", well_id.strip())
    if not match:
        return None

    letter_part, number_part = match.groups()
    row = letter_to_row_index(letter_part)
    col = int(number_part) - 1

    if col < 0:
        return None

    return (row, col)


def row_col_to_well_id(row: int, col: int) -> str:
    """Convert row/column indices to well ID.

    Args:
        row: 0-based row index
        col: 0-based column index

    Returns:
        Well ID string (e.g., 'A1', 'B12').

    Examples:
        >>> row_col_to_well_id(0, 0)
        'A1'
        >>> row_col_to_well_id(1, 11)
        'B12'
    """
    return row_index_to_letter(row) + str(col + 1)


def well_id_to_position(
    well_id: str,
    a1_x_mm: float,
    a1_y_mm: float,
    well_spacing_mm: float,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> Optional[Tuple[float, float]]:
    """Convert well ID to physical (x, y) position.

    Args:
        well_id: Well identifier (e.g., 'A1', 'B2')
        a1_x_mm: X position of well A1
        a1_y_mm: Y position of well A1
        well_spacing_mm: Distance between well centers
        offset_x_mm: Additional X offset (for calibration)
        offset_y_mm: Additional Y offset (for calibration)

    Returns:
        Tuple of (x_mm, y_mm) or None if well_id is invalid.
    """
    parsed = well_id_to_row_col(well_id)
    if parsed is None:
        return None

    row, col = parsed
    x_mm = a1_x_mm + (col * well_spacing_mm) + offset_x_mm
    y_mm = a1_y_mm + (row * well_spacing_mm) + offset_y_mm

    return (x_mm, y_mm)


def parse_well_range(well_range: str) -> List[Tuple[int, int]]:
    """Parse a well range specification to list of (row, col) tuples.

    Supports:
    - Single wells: 'A1', 'B2'
    - Ranges: 'A1:B3' (expands to rectangular region)
    - Comma-separated: 'A1,B2,C3'
    - Mixed: 'A1:A3,B1,C1:C3'

    Args:
        well_range: Well range specification string

    Returns:
        List of (row, col) tuples for all specified wells.

    Examples:
        >>> parse_well_range('A1')
        [(0, 0)]
        >>> parse_well_range('A1:A3')
        [(0, 0), (0, 1), (0, 2)]
        >>> parse_well_range('A1:B2')
        [(0, 0), (0, 1), (1, 0), (1, 1)]
    """
    result: List[Tuple[int, int]] = []
    pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"

    descriptions = well_range.split(",")

    for desc in descriptions:
        desc = desc.strip()
        if not desc:
            continue

        match = re.match(pattern, desc)
        if not match:
            continue

        start_row_str, start_col_str, end_row_str, end_col_str = match.groups()
        start_row = letter_to_row_index(start_row_str)
        start_col = int(start_col_str) - 1

        if end_row_str and end_col_str:
            # It's a range
            end_row = letter_to_row_index(end_row_str)
            end_col = int(end_col_str) - 1

            for row in range(min(start_row, end_row), max(start_row, end_row) + 1):
                for col in range(min(start_col, end_col), max(start_col, end_col) + 1):
                    result.append((row, col))
        else:
            # Single well
            result.append((start_row, start_col))

    return result


def wells_to_positions(
    wells: List[Tuple[int, int]],
    a1_x_mm: float,
    a1_y_mm: float,
    well_spacing_mm: float,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> Dict[str, Tuple[float, float]]:
    """Convert a list of well (row, col) tuples to a dict of well_id -> position.

    Args:
        wells: List of (row, col) tuples
        a1_x_mm: X position of well A1
        a1_y_mm: Y position of well A1
        well_spacing_mm: Distance between well centers
        offset_x_mm: Additional X offset
        offset_y_mm: Additional Y offset

    Returns:
        Dict mapping well_id to (x_mm, y_mm) position.
    """
    result: Dict[str, Tuple[float, float]] = {}
    for row, col in wells:
        well_id = row_col_to_well_id(row, col)
        x_mm = a1_x_mm + (col * well_spacing_mm) + offset_x_mm
        y_mm = a1_y_mm + (row * well_spacing_mm) + offset_y_mm
        result[well_id] = (x_mm, y_mm)
    return result


def apply_s_pattern_to_wells(
    wells: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """Apply S-pattern (serpentine) ordering to a list of wells.

    Reverses the column order for alternating rows.

    Args:
        wells: List of (row, col) tuples

    Returns:
        Sorted list with S-pattern applied.
    """
    if not wells:
        return wells

    # Group by row
    rows_dict: Dict[int, List[Tuple[int, int]]] = {}
    for row, col in wells:
        if row not in rows_dict:
            rows_dict[row] = []
        rows_dict[row].append((row, col))

    # Sort and apply pattern
    sorted_rows = sorted(rows_dict.keys())
    result: List[Tuple[int, int]] = []

    for i, row in enumerate(sorted_rows):
        row_wells = sorted(rows_dict[row], key=lambda x: x[1])
        if i % 2 == 1:
            row_wells.reverse()
        result.extend(row_wells)

    return result
