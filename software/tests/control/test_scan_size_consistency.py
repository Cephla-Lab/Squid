"""
Tests for scan size and coverage consistency.

Conceptually, coverage = (area of well actually covered by tiles) / well_area × 100.
In the implementation under test, this is approximated via grid sampling (discrete
point counting) rather than an exact geometric area calculation.
"""

import math

from control.core.geometry_utils import (
    get_effective_well_size,
    get_tile_positions,
    calculate_well_coverage,
)


class TestWellCoverage:
    """Tests for well coverage calculation."""

    def test_small_scan_partial_coverage(self):
        """Small scan should give partial coverage."""
        well_size = 15.54
        fov = 3.9
        overlap = 10

        coverage = calculate_well_coverage(15.0, fov, overlap, "Circle", well_size)
        assert coverage < 100, f"15mm scan should have partial coverage, got {coverage}%"
        assert coverage > 0, "Should have some coverage"

    def test_larger_scan_more_coverage(self):
        """Larger scan should cover more of the well."""
        well_size = 15.54
        fov = 3.9
        overlap = 10

        cov_15 = calculate_well_coverage(15.0, fov, overlap, "Circle", well_size)
        cov_16 = calculate_well_coverage(16.0, fov, overlap, "Circle", well_size)

        assert cov_16 > cov_15, f"16mm should cover more than 15mm: {cov_16}% vs {cov_15}%"

    def test_coverage_capped_at_100(self):
        """Coverage should not exceed 100%."""
        well_size = 15.54
        fov = 3.9
        overlap = 10

        # Even with large scan, coverage of well cannot exceed 100%
        coverage = calculate_well_coverage(30.0, fov, overlap, "Circle", well_size)
        assert coverage <= 100, f"Coverage should not exceed 100%, got {coverage}%"

    def test_square_well_coverage(self):
        """Test coverage calculation for square wells (384/1536 well plates).

        Uses scan_size smaller than well_size to verify partial coverage calculation.
        """
        well_size = 3.21  # 384 well plate
        fov = 0.5
        overlap = 10
        scan_size = 2.0  # Smaller than well to test partial coverage

        coverage = calculate_well_coverage(scan_size, fov, overlap, "Square", well_size, is_round_well=False)
        assert coverage > 0, "Should have some coverage"
        assert coverage < 100, "Partial scan should give partial coverage"

    def test_rectangle_shape_coverage(self):
        """Test coverage calculation for Rectangle scan shape."""
        well_size = 6.21
        fov = 1.0
        overlap = 10

        coverage = calculate_well_coverage(well_size, fov, overlap, "Rectangle", well_size)
        assert coverage > 0, "Rectangle scan should have some coverage"
        assert coverage <= 100, "Coverage should not exceed 100%"


class TestEffectiveWellSize:
    """Tests for effective well size calculations (used for scan_size defaults)."""

    def test_square_on_round_well_inscribed(self):
        well_size = 6.21
        effective = get_effective_well_size(well_size, 0.5, "Square", is_round_well=True)
        expected = well_size / math.sqrt(2)
        assert abs(effective - expected) < 0.001

    def test_circle_includes_fov_adjustment(self):
        well_size = 6.21
        fov_size = 0.5
        effective = get_effective_well_size(well_size, fov_size, "Circle")
        expected = well_size + fov_size * (1 + math.sqrt(2))
        assert effective == expected

    def test_rectangle_on_round_well(self):
        well_size = 6.21
        effective = get_effective_well_size(well_size, 0.5, "Rectangle", is_round_well=True)
        expected = well_size / math.sqrt(1.36)
        assert abs(effective - expected) < 0.001


class TestTilePositions:
    """Tests for tile position generation."""

    def test_single_tile_for_small_scan(self):
        """Very small scan should produce at least one tile."""
        tiles = get_tile_positions(1.0, 3.9, 10, "Circle")
        assert len(tiles) >= 1

    def test_more_tiles_for_larger_scan(self):
        """Larger scan should produce more tiles."""
        tiles_small = get_tile_positions(10.0, 3.9, 10, "Circle")
        tiles_large = get_tile_positions(20.0, 3.9, 10, "Circle")
        assert len(tiles_large) > len(tiles_small)

    def test_circle_filters_corner_tiles(self):
        """Circle shape should have fewer tiles than Square for same scan size."""
        tiles_circle = get_tile_positions(20.0, 3.9, 10, "Circle")
        tiles_square = get_tile_positions(20.0, 3.9, 10, "Square")
        assert len(tiles_circle) < len(tiles_square)
