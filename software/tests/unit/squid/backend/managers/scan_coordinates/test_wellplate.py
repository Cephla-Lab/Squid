"""Tests for the wellplate module."""

import pytest

from squid.backend.managers.scan_coordinates.wellplate import (
    apply_s_pattern_to_wells,
    letter_to_row_index,
    parse_well_range,
    row_col_to_well_id,
    row_index_to_letter,
    well_id_to_position,
    well_id_to_row_col,
    wells_to_positions,
)


class TestRowIndexToLetter:
    """Tests for row_index_to_letter function."""

    def test_first_row(self):
        """Row 0 is A."""
        assert row_index_to_letter(0) == "A"

    def test_last_single_letter(self):
        """Row 25 is Z."""
        assert row_index_to_letter(25) == "Z"

    def test_first_double_letter(self):
        """Row 26 is AA."""
        assert row_index_to_letter(26) == "AA"

    def test_middle_double_letter(self):
        """Row 27 is AB."""
        assert row_index_to_letter(27) == "AB"

    def test_row_51(self):
        """Row 51 is AZ."""
        assert row_index_to_letter(51) == "AZ"

    def test_row_52(self):
        """Row 52 is BA."""
        assert row_index_to_letter(52) == "BA"


class TestLetterToRowIndex:
    """Tests for letter_to_row_index function."""

    def test_a(self):
        """A is row 0."""
        assert letter_to_row_index("A") == 0

    def test_z(self):
        """Z is row 25."""
        assert letter_to_row_index("Z") == 25

    def test_aa(self):
        """AA is row 26."""
        assert letter_to_row_index("AA") == 26

    def test_ab(self):
        """AB is row 27."""
        assert letter_to_row_index("AB") == 27

    def test_lowercase(self):
        """Works with lowercase letters."""
        assert letter_to_row_index("a") == 0
        assert letter_to_row_index("aa") == 26

    def test_roundtrip(self):
        """Roundtrip conversion works."""
        for i in range(100):
            letter = row_index_to_letter(i)
            assert letter_to_row_index(letter) == i


class TestWellIdToRowCol:
    """Tests for well_id_to_row_col function."""

    def test_a1(self):
        """A1 is (0, 0)."""
        assert well_id_to_row_col("A1") == (0, 0)

    def test_b12(self):
        """B12 is (1, 11)."""
        assert well_id_to_row_col("B12") == (1, 11)

    def test_aa1(self):
        """AA1 is (26, 0)."""
        assert well_id_to_row_col("AA1") == (26, 0)

    def test_lowercase(self):
        """Works with lowercase."""
        assert well_id_to_row_col("a1") == (0, 0)
        assert well_id_to_row_col("b2") == (1, 1)

    def test_whitespace(self):
        """Handles whitespace."""
        assert well_id_to_row_col("  A1  ") == (0, 0)

    def test_invalid_format(self):
        """Returns None for invalid format."""
        assert well_id_to_row_col("1A") is None
        assert well_id_to_row_col("") is None
        assert well_id_to_row_col("A") is None
        assert well_id_to_row_col("1") is None

    def test_invalid_column_zero(self):
        """Returns None for column 0 (invalid)."""
        assert well_id_to_row_col("A0") is None


class TestRowColToWellId:
    """Tests for row_col_to_well_id function."""

    def test_0_0(self):
        """(0, 0) is A1."""
        assert row_col_to_well_id(0, 0) == "A1"

    def test_1_11(self):
        """(1, 11) is B12."""
        assert row_col_to_well_id(1, 11) == "B12"

    def test_26_0(self):
        """(26, 0) is AA1."""
        assert row_col_to_well_id(26, 0) == "AA1"

    def test_roundtrip(self):
        """Roundtrip conversion works."""
        for row in range(50):
            for col in range(24):
                well_id = row_col_to_well_id(row, col)
                parsed = well_id_to_row_col(well_id)
                assert parsed == (row, col)


class TestWellIdToPosition:
    """Tests for well_id_to_position function."""

    def test_a1_at_origin(self):
        """A1 at origin with spacing."""
        pos = well_id_to_position("A1", 0, 0, 9.0)
        assert pos == (0, 0)

    def test_b2(self):
        """B2 offset from A1."""
        pos = well_id_to_position("B2", 0, 0, 9.0)
        assert pos == (9.0, 9.0)  # col=1, row=1

    def test_with_a1_offset(self):
        """A1 not at origin."""
        pos = well_id_to_position("A1", 10.0, 20.0, 9.0)
        assert pos == (10.0, 20.0)

    def test_with_calibration_offset(self):
        """Additional calibration offset."""
        pos = well_id_to_position("A1", 0, 0, 9.0, offset_x_mm=1.0, offset_y_mm=2.0)
        assert pos == (1.0, 2.0)

    def test_invalid_well(self):
        """Returns None for invalid well ID."""
        pos = well_id_to_position("invalid", 0, 0, 9.0)
        assert pos is None


class TestParseWellRange:
    """Tests for parse_well_range function."""

    def test_single_well(self):
        """Single well parses correctly."""
        result = parse_well_range("A1")
        assert result == [(0, 0)]

    def test_row_range(self):
        """Range within same row."""
        result = parse_well_range("A1:A3")
        assert result == [(0, 0), (0, 1), (0, 2)]

    def test_column_range(self):
        """Range within same column."""
        result = parse_well_range("A1:C1")
        assert result == [(0, 0), (1, 0), (2, 0)]

    def test_rectangular_range(self):
        """Rectangular range."""
        result = parse_well_range("A1:B2")
        assert set(result) == {(0, 0), (0, 1), (1, 0), (1, 1)}

    def test_comma_separated(self):
        """Comma-separated wells."""
        result = parse_well_range("A1,B2,C3")
        assert result == [(0, 0), (1, 1), (2, 2)]

    def test_mixed(self):
        """Mixed ranges and single wells."""
        result = parse_well_range("A1:A2,B1")
        assert set(result) == {(0, 0), (0, 1), (1, 0)}

    def test_whitespace(self):
        """Handles whitespace."""
        result = parse_well_range("A1 , B2")
        assert result == [(0, 0), (1, 1)]

    def test_empty(self):
        """Empty string returns empty list."""
        result = parse_well_range("")
        assert result == []

    def test_reversed_range(self):
        """Range with start > end still works."""
        result = parse_well_range("B2:A1")
        assert set(result) == {(0, 0), (0, 1), (1, 0), (1, 1)}


class TestWellsToPositions:
    """Tests for wells_to_positions function."""

    def test_single_well(self):
        """Single well position."""
        wells = [(0, 0)]
        result = wells_to_positions(wells, 0, 0, 9.0)
        assert result == {"A1": (0, 0)}

    def test_multiple_wells(self):
        """Multiple well positions."""
        wells = [(0, 0), (0, 1), (1, 0)]
        result = wells_to_positions(wells, 0, 0, 9.0)
        assert result == {
            "A1": (0, 0),
            "A2": (9.0, 0),
            "B1": (0, 9.0),
        }

    def test_with_offset(self):
        """Positions with offset."""
        wells = [(0, 0)]
        result = wells_to_positions(wells, 10, 20, 9.0, 1.0, 2.0)
        assert result == {"A1": (11.0, 22.0)}


class TestApplySPatternToWells:
    """Tests for apply_s_pattern_to_wells function."""

    def test_empty_list(self):
        """Empty list returns empty."""
        assert apply_s_pattern_to_wells([]) == []

    def test_single_well(self):
        """Single well returns unchanged."""
        wells = [(0, 0)]
        assert apply_s_pattern_to_wells(wells) == [(0, 0)]

    def test_single_row(self):
        """Single row is sorted left-to-right."""
        wells = [(0, 2), (0, 0), (0, 1)]
        result = apply_s_pattern_to_wells(wells)
        assert result == [(0, 0), (0, 1), (0, 2)]

    def test_two_rows(self):
        """Two rows: first left-to-right, second right-to-left."""
        wells = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
        result = apply_s_pattern_to_wells(wells)

        # First row should be left to right
        assert result[0:3] == [(0, 0), (0, 1), (0, 2)]
        # Second row should be right to left
        assert result[3:6] == [(1, 2), (1, 1), (1, 0)]

    def test_three_rows(self):
        """Three rows alternate correctly."""
        wells = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
        result = apply_s_pattern_to_wells(wells)

        # Row 0: left to right
        assert result[0:2] == [(0, 0), (0, 1)]
        # Row 1: right to left
        assert result[2:4] == [(1, 1), (1, 0)]
        # Row 2: left to right
        assert result[4:6] == [(2, 0), (2, 1)]
