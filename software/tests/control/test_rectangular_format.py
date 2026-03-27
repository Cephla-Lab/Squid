"""Tests for rectangular sample format support."""

import csv
import os
import tempfile

from control._def import read_sample_formats_csv


class TestReadSampleFormatsCSV:
    """Tests for read_sample_formats_csv with new column format."""

    def test_new_format_csv(self, tmp_path):
        """New CSV with well_size_x_mm, well_size_y_mm, well_spacing_x_mm, well_spacing_y_mm, well_shape."""
        csv_path = tmp_path / "sample_formats.csv"
        csv_path.write_text(
            "format,a1_x_mm,a1_y_mm,a1_x_pixel,a1_y_pixel,well_size_x_mm,well_size_y_mm,"
            "well_spacing_x_mm,well_spacing_y_mm,well_shape,number_of_skip,rows,cols\n"
            "96,11.31,10.75,171,135,6.21,6.21,9.0,9.0,circular,0,8,12\n"
            "custom_chip,5.0,5.0,60,60,2.0,1.5,4.0,3.0,rectangular,0,10,20\n"
        )
        formats = read_sample_formats_csv(str(csv_path))

        assert "96 well plate" in formats
        assert formats["96 well plate"]["well_spacing_x_mm"] == 9.0
        assert formats["96 well plate"]["well_spacing_y_mm"] == 9.0
        assert formats["96 well plate"]["well_size_x_mm"] == 6.21
        assert formats["96 well plate"]["well_size_y_mm"] == 6.21
        assert formats["96 well plate"]["well_shape"] == "circular"

        assert "custom_chip" in formats
        assert formats["custom_chip"]["well_spacing_x_mm"] == 4.0
        assert formats["custom_chip"]["well_spacing_y_mm"] == 3.0
        assert formats["custom_chip"]["well_size_x_mm"] == 2.0
        assert formats["custom_chip"]["well_size_y_mm"] == 1.5
        assert formats["custom_chip"]["well_shape"] == "rectangular"

    def test_backward_compat_old_csv(self, tmp_path):
        """Old CSV with single well_size_mm and well_spacing_mm should be auto-upgraded."""
        csv_path = tmp_path / "sample_formats.csv"
        csv_path.write_text(
            "format,a1_x_mm,a1_y_mm,a1_x_pixel,a1_y_pixel,well_size_mm,well_spacing_mm,"
            "number_of_skip,rows,cols\n"
            "96,11.31,10.75,171,135,6.21,9.0,0,8,12\n"
        )
        formats = read_sample_formats_csv(str(csv_path))

        assert formats["96 well plate"]["well_spacing_x_mm"] == 9.0
        assert formats["96 well plate"]["well_spacing_y_mm"] == 9.0
        assert formats["96 well plate"]["well_size_x_mm"] == 6.21
        assert formats["96 well plate"]["well_size_y_mm"] == 6.21
        assert formats["96 well plate"]["well_shape"] == "circular"

    def test_glass_slide_format(self, tmp_path):
        """Glass slide should have all zeros and circular shape."""
        csv_path = tmp_path / "sample_formats.csv"
        csv_path.write_text(
            "format,a1_x_mm,a1_y_mm,a1_x_pixel,a1_y_pixel,well_size_x_mm,well_size_y_mm,"
            "well_spacing_x_mm,well_spacing_y_mm,well_shape,number_of_skip,rows,cols\n"
            "glass slide,0,0,0,0,0,0,0,0,circular,0,1,1\n"
        )
        formats = read_sample_formats_csv(str(csv_path))
        assert "glass slide" in formats
        assert formats["glass slide"]["well_spacing_x_mm"] == 0
        assert formats["glass slide"]["well_spacing_y_mm"] == 0


class TestGetWellplateSettings:
    """Tests for get_wellplate_settings with new format."""

    def test_settings_have_xy_keys(self):
        """Settings dict should contain X/Y keys and well_shape."""
        from control._def import get_wellplate_settings

        settings = get_wellplate_settings("96 well plate")
        assert "well_spacing_x_mm" in settings
        assert "well_spacing_y_mm" in settings
        assert "well_size_x_mm" in settings
        assert "well_size_y_mm" in settings
        assert "well_shape" in settings

    def test_zero_format_has_xy_keys(self):
        """The '0' format should also have X/Y keys."""
        from control._def import get_wellplate_settings

        settings = get_wellplate_settings("0")
        assert "well_spacing_x_mm" in settings
        assert "well_spacing_y_mm" in settings
        assert "well_size_x_mm" in settings
        assert "well_size_y_mm" in settings
        assert settings["well_shape"] == "circular"


import math

from control.core.geometry_utils import get_effective_well_size, calculate_well_coverage


class TestRectangularEffectiveWellSize:
    """Tests for get_effective_well_size with rectangular wells."""

    def test_rectangular_well_square_scan(self):
        """Rectangular well with Square scan returns (size_x, size_y) tuple."""
        result = get_effective_well_size(2.0, 1.5, 0.5, "Square", is_round_well=False)
        assert result == (2.0, 1.5)

    def test_rectangular_well_rectangle_scan(self):
        """Rectangular well with Rectangle scan returns (size_x, size_y) tuple."""
        result = get_effective_well_size(2.0, 1.5, 0.5, "Rectangle", is_round_well=False)
        assert result == (2.0, 1.5)

    def test_square_well_returns_tuple(self):
        """Square wells (384/1536) return tuple with equal values."""
        result = get_effective_well_size(3.3, 3.3, 0.5, "Square", is_round_well=False)
        assert result == (3.3, 3.3)

    def test_circular_well_unchanged(self):
        """Circular well (equal X/Y) should work as before."""
        result = get_effective_well_size(6.21, 6.21, 0.5, "Square", is_round_well=True)
        expected = 6.21 / math.sqrt(2)
        assert abs(result - expected) < 0.001

    def test_rectangular_well_circle_scan(self):
        """Rectangular well with Circle scan returns circumscribing circle."""
        size_x, size_y, fov = 2.0, 1.5, 0.5
        result = get_effective_well_size(size_x, size_y, fov, "Circle", is_round_well=False)
        expected = math.sqrt(size_x**2 + size_y**2) + fov * (1 + math.sqrt(2))
        assert abs(result - expected) < 0.001


class TestRectangularWellCoverage:
    """Tests for calculate_well_coverage with rectangular wells."""

    def test_rectangular_well_full_coverage(self):
        """Large scan over small rectangular well should give ~100% coverage."""
        coverage = calculate_well_coverage(
            5.0, 0.5, 10, "Square", well_size_x_mm=2.0, well_size_y_mm=1.5, is_round_well=False
        )
        assert coverage > 90

    def test_rectangular_well_partial_coverage(self):
        """Small scan over rectangular well gives partial coverage."""
        coverage = calculate_well_coverage(
            1.0, 0.5, 10, "Square", well_size_x_mm=2.0, well_size_y_mm=1.5, is_round_well=False
        )
        assert 0 < coverage < 100

    def test_round_well_backward_compat(self):
        """Round well with positional well_size_mm should still work."""
        coverage = calculate_well_coverage(15.0, 3.9, 10, "Circle", 15.54)
        assert coverage > 0


class TestSquareWellShape:
    """Tests for 'square' well_shape (384/1536 plates)."""

    def test_square_shape_in_csv(self, tmp_path):
        """CSV with well_shape='square' should be parsed correctly."""
        csv_path = tmp_path / "sample_formats.csv"
        csv_path.write_text(
            "format,a1_x_mm,a1_y_mm,a1_x_pixel,a1_y_pixel,well_size_x_mm,well_size_y_mm,"
            "well_spacing_x_mm,well_spacing_y_mm,well_shape,number_of_skip,rows,cols\n"
            "custom_square,10.0,10.0,100,100,2.0,2.0,4.0,4.0,square,0,8,12\n"
        )
        formats = read_sample_formats_csv(str(csv_path))
        assert formats["custom_square"]["well_shape"] == "square"
        assert formats["custom_square"]["well_size_x_mm"] == 2.0
        assert formats["custom_square"]["well_size_y_mm"] == 2.0

    def test_square_well_effective_size(self):
        """Square wells should return tuple from get_effective_well_size."""
        result = get_effective_well_size(2.0, 2.0, 0.5, "Square", is_round_well=False)
        assert result == (2.0, 2.0)

    def test_square_well_coverage(self):
        """Square wells should use rectangular bounds for coverage."""
        coverage = calculate_well_coverage(
            5.0, 0.5, 10, "Square", well_size_x_mm=2.0, well_size_y_mm=2.0, is_round_well=False
        )
        assert coverage > 90


class TestPerAxisAddRegion:
    """Tests for add_region with per-axis scan sizes."""

    def test_asymmetric_scan_generates_rectangular_grid(self):
        """add_region with scan_size_y_mm != scan_size_mm should produce rectangular grid."""
        from unittest.mock import MagicMock
        from control.core.scan_coordinates import ScanCoordinates

        sc = ScanCoordinates(MagicMock(), MagicMock(), MagicMock())
        # Mock FOV size to 1.0mm
        sc.objectiveStore = MagicMock()
        sc.objectiveStore.get_pixel_size_factor.return_value = 1.0
        sc.camera = MagicMock()
        sc.camera.get_fov_size_mm.return_value = 1.0

        # Use center at 50,50 to be within typical stage limits
        sc.add_region("test", 50, 50, scan_size_mm=3.0, overlap_percent=0, shape="Square", scan_size_y_mm=2.0)

        coords = sc.region_fov_coordinates.get("test", [])
        assert len(coords) > 0

        # X should span ~3mm (3 steps), Y should span ~2mm (2 steps)
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        x_unique = sorted(set(round(x, 3) for x in xs))
        y_unique = sorted(set(round(y, 3) for y in ys))
        assert len(x_unique) == 3, f"Expected 3 X positions, got {len(x_unique)}: {x_unique}"
        assert len(y_unique) == 2, f"Expected 2 Y positions, got {len(y_unique)}: {y_unique}"

    def test_equal_scan_sizes_no_per_axis(self):
        """When scan_size_y_mm == scan_size_mm, should behave like scalar scan."""
        from unittest.mock import MagicMock
        from control.core.scan_coordinates import ScanCoordinates

        sc = ScanCoordinates(MagicMock(), MagicMock(), MagicMock())
        sc.objectiveStore = MagicMock()
        sc.objectiveStore.get_pixel_size_factor.return_value = 1.0
        sc.camera = MagicMock()
        sc.camera.get_fov_size_mm.return_value = 1.0

        # Use center at 50,50 to be within typical stage limits
        sc.add_region("a", 50, 50, scan_size_mm=3.0, overlap_percent=0, shape="Square", scan_size_y_mm=3.0)
        sc.add_region("b", 50, 50, scan_size_mm=3.0, overlap_percent=0, shape="Square", scan_size_y_mm=None)

        coords_a = sc.region_fov_coordinates["a"]
        coords_b = sc.region_fov_coordinates["b"]
        assert len(coords_a) == len(coords_b)


class TestScanCoordinatesRectangular:
    """Tests for ScanCoordinates with asymmetric X/Y spacing."""

    def test_well_position_asymmetric_spacing(self):
        """Wells should use separate X and Y spacing."""
        from unittest.mock import MagicMock
        from control.core.scan_coordinates import ScanCoordinates

        import control._def

        original_x = control._def.WELL_SPACING_X_MM
        original_y = control._def.WELL_SPACING_Y_MM
        try:
            control._def.WELL_SPACING_X_MM = 4.0
            control._def.WELL_SPACING_Y_MM = 3.0

            sc = ScanCoordinates(MagicMock(), MagicMock(), MagicMock())
            sc.well_spacing_x_mm = 4.0
            sc.well_spacing_y_mm = 3.0
            sc.a1_x_mm = 5.0
            sc.a1_y_mm = 5.0
            sc.wellplate_offset_x_mm = 0
            sc.wellplate_offset_y_mm = 0
            sc.format = "custom"

            mock_selector = MagicMock()
            mock_selector.get_selected_cells.return_value = [[1, 2]]
            sc.well_selector = mock_selector

            wells = sc.get_selected_wells()
            # x = 5.0 + 2 * 4.0 = 13.0
            # y = 5.0 + 1 * 3.0 = 8.0
            well_id = list(wells.keys())[0]
            assert wells[well_id] == (13.0, 8.0), f"Expected (13.0, 8.0), got {wells[well_id]}"
        finally:
            control._def.WELL_SPACING_X_MM = original_x
            control._def.WELL_SPACING_Y_MM = original_y
