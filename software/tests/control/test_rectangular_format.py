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
