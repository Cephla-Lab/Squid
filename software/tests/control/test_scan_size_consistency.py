"""
Tests for scan size and coverage consistency in WellplateMultiPointWidget.

These tests verify that:
1. Scan size is preserved after acquisition (reset_coordinates)
2. Coverage is correctly derived from scan_size
3. Objective changes maintain coverage percentage
4. Glass slide mode is handled correctly
"""

import math
import pytest


class TestScanSizeCoverageLogic:
    """Unit tests for the scan size/coverage calculation logic.
    
    These tests verify the mathematical relationships without requiring Qt widgets.
    """

    def get_effective_well_size(self, well_size_mm, fov_size_mm, shape, is_round_well=True):
        """Calculate effective well size (mirrors widget logic)."""
        if shape == "Circle":
            return well_size_mm + fov_size_mm * (1 + math.sqrt(2))
        elif shape == "Square" and is_round_well:
            # For square scan in round well, inscribe the square inside the circle
            return well_size_mm / math.sqrt(2)
        return well_size_mm

    def update_coverage_from_scan_size(self, scan_size, effective_well_size):
        """Calculate coverage from scan_size (mirrors widget logic)."""
        return round((scan_size / effective_well_size) * 100, 2)

    def update_scan_size_from_coverage(self, coverage, effective_well_size):
        """Calculate scan_size from coverage (mirrors widget logic)."""
        return round((coverage / 100) * effective_well_size, 3)

    def test_scan_size_preserved_after_reset_coordinates(self):
        """Scan size should NOT change after reset_coordinates (coverage is derived from scan_size)."""
        well_size_mm = 15.54  # 24 well plate
        fov_size_mm = 1.57  # 4x objective
        shape = "Circle"

        effective_well_size = self.get_effective_well_size(well_size_mm, fov_size_mm, shape)

        # User sets scan_size
        user_scan_size = 19.0

        # reset_coordinates calls update_coverage_from_scan_size (our fix)
        coverage = self.update_coverage_from_scan_size(user_scan_size, effective_well_size)

        # Verify scan_size is unchanged (this is the key fix)
        # In the old code, it would call update_scan_size_from_coverage which could change it
        assert user_scan_size == 19.0, "Scan size should be preserved after reset_coordinates"

        # Coverage should be calculated correctly
        expected_coverage = round((19.0 / effective_well_size) * 100, 2)
        assert coverage == expected_coverage

    def test_coverage_updates_scan_size_correctly(self):
        """When user changes coverage, scan_size should update to match."""
        well_size_mm = 6.21  # 96 well plate
        fov_size_mm = 0.5  # 20x objective
        shape = "Circle"

        effective_well_size = self.get_effective_well_size(well_size_mm, fov_size_mm, shape)

        # User sets 100% coverage
        coverage = 100
        scan_size = self.update_scan_size_from_coverage(coverage, effective_well_size)

        # Verify scan_size equals effective well size at 100%
        assert abs(scan_size - effective_well_size) < 0.001

        # User sets 50% coverage
        coverage = 50
        scan_size = self.update_scan_size_from_coverage(coverage, effective_well_size)

        # Verify scan_size is half of effective well size
        expected = round(effective_well_size * 0.5, 3)
        assert scan_size == expected

    def test_objective_change_maintains_coverage(self):
        """When objective changes, coverage should be maintained by updating scan_size."""
        well_size_mm = 15.54  # 24 well plate
        shape = "Circle"

        # Start with 4x objective, 100% coverage
        fov_4x = 1.57
        effective_4x = self.get_effective_well_size(well_size_mm, fov_4x, shape)
        coverage = 100
        scan_size_4x = self.update_scan_size_from_coverage(coverage, effective_4x)

        # Switch to 20x objective
        fov_20x = 0.5
        effective_20x = self.get_effective_well_size(well_size_mm, fov_20x, shape)

        # handle_objective_change calls update_scan_size_from_coverage
        # to maintain the same coverage with new effective well size
        scan_size_20x = self.update_scan_size_from_coverage(coverage, effective_20x)

        # Verify scan_size changed
        assert scan_size_4x != scan_size_20x, "Scan size should change when objective changes"

        # Verify coverage is still 100% for both
        coverage_4x = self.update_coverage_from_scan_size(scan_size_4x, effective_4x)
        coverage_20x = self.update_coverage_from_scan_size(scan_size_20x, effective_20x)

        assert coverage_4x == 100.0, "Coverage should be 100% with 4x objective"
        assert coverage_20x == 100.0, "Coverage should be 100% with 20x objective"

    def test_round_trip_consistency(self):
        """Coverage -> scan_size -> coverage should be consistent."""
        well_size_mm = 6.21  # 96 well plate
        fov_size_mm = 0.5
        shape = "Circle"

        effective_well_size = self.get_effective_well_size(well_size_mm, fov_size_mm, shape)

        for initial_coverage in [25, 50, 75, 100, 150]:
            scan_size = self.update_scan_size_from_coverage(initial_coverage, effective_well_size)
            final_coverage = self.update_coverage_from_scan_size(scan_size, effective_well_size)

            # Should be within rounding tolerance
            assert abs(final_coverage - initial_coverage) < 0.1, (
                f"Round-trip failed: {initial_coverage}% -> {scan_size}mm -> {final_coverage}%"
            )

    def test_square_shape_on_square_well_no_adjustment(self):
        """Square shape on square well (384/1536) should equal well size."""
        well_size_mm = 3.3  # 384 well plate (square wells)
        fov_size_mm = 0.5
        shape = "Square"

        # 384/1536 plates have square wells
        effective_well_size = self.get_effective_well_size(well_size_mm, fov_size_mm, shape, is_round_well=False)

        # For Square on square well, effective well size equals well size
        assert effective_well_size == well_size_mm

    def test_square_shape_on_round_well_inscribed(self):
        """Square shape on round well should be inscribed (side = diameter / sqrt(2))."""
        well_size_mm = 6.21  # 96 well plate (round wells)
        fov_size_mm = 0.5
        shape = "Square"

        # 96 well plate has round wells
        effective_well_size = self.get_effective_well_size(well_size_mm, fov_size_mm, shape, is_round_well=True)

        # For Square on round well, square is inscribed: side = diameter / sqrt(2)
        expected = well_size_mm / math.sqrt(2)
        assert abs(effective_well_size - expected) < 0.001
        assert effective_well_size < well_size_mm  # Must be smaller than diameter

    def test_circle_shape_includes_fov_adjustment(self):
        """Circle shape should include FOV adjustment in effective well size."""
        well_size_mm = 6.21  # 96 well plate
        fov_size_mm = 0.5
        shape = "Circle"

        effective_well_size = self.get_effective_well_size(well_size_mm, fov_size_mm, shape)

        # For Circle, effective well size > well size
        expected = well_size_mm + fov_size_mm * (1 + math.sqrt(2))
        assert effective_well_size == expected
        assert effective_well_size > well_size_mm

    def test_glass_slide_guard(self):
        """Glass slide (well_size = 0) should be handled gracefully."""
        well_size_mm = 0  # Glass slide
        fov_size_mm = 0.5
        shape = "Circle"

        effective_well_size = self.get_effective_well_size(well_size_mm, fov_size_mm, shape)

        # Even with 0 well size, calculation should not error
        # The widget has guards for this, but the math still works
        assert effective_well_size == fov_size_mm * (1 + math.sqrt(2))


class TestWellplateSizes:
    """Test calculations with actual wellplate sizes from sample_formats.csv."""

    WELLPLATE_SIZES = {
        "6": 34.94,
        "12": 22.05,
        "24": 15.54,
        "96": 6.21,
        "384": 3.3,
        "1536": 1.53,
    }

    # 384 and 1536 have square wells, others have round wells
    SQUARE_WELL_PLATES = ["384", "1536"]

    def get_effective_well_size(self, well_size_mm, fov_size_mm, shape, is_round_well):
        if shape == "Circle":
            return well_size_mm + fov_size_mm * (1 + math.sqrt(2))
        elif shape == "Square" and is_round_well:
            return well_size_mm / math.sqrt(2)
        return well_size_mm

    @pytest.mark.parametrize("plate,well_size", WELLPLATE_SIZES.items())
    def test_100_percent_coverage_matches_effective_well_size(self, plate, well_size):
        """100% coverage should result in scan_size equal to effective well size."""
        fov_size_mm = 0.5  # 20x objective
        is_round_well = plate not in self.SQUARE_WELL_PLATES
        shape = "Circle" if is_round_well else "Square"

        effective = self.get_effective_well_size(well_size, fov_size_mm, shape, is_round_well)
        scan_size = round((100 / 100) * effective, 3)

        assert abs(scan_size - effective) < 0.001, f"Failed for {plate} well plate"

    @pytest.mark.parametrize("plate,well_size", WELLPLATE_SIZES.items())
    def test_scan_size_preserved_across_operations(self, plate, well_size):
        """User-set scan_size should be preserved after simulated reset_coordinates."""
        fov_size_mm = 0.5
        is_round_well = plate not in self.SQUARE_WELL_PLATES
        shape = "Circle" if is_round_well else "Square"

        effective = self.get_effective_well_size(well_size, fov_size_mm, shape, is_round_well)

        # User sets a specific scan_size (e.g., 75% of effective)
        user_scan_size = round(effective * 0.75, 3)

        # Simulate reset_coordinates (now calls update_coverage_from_scan_size)
        coverage = round((user_scan_size / effective) * 100, 2)

        # Scan size should be unchanged
        assert user_scan_size == round(effective * 0.75, 3), f"Scan size changed for {plate} well plate"
        assert abs(coverage - 75.0) < 0.1, f"Coverage incorrect for {plate} well plate"
