"""Tests for control.core.mosaic_utils."""

import os

import numpy as np
import pytest

from control.core.mosaic_utils import (
    _pyrdown_chain,
    calculate_overlap_pixels,
    downsample_tile,
    parse_well_id,
    format_well_id,
)
from control._def import DownsamplingMethod


class TestOverlapCalculation:
    """Tests for overlap calculation from acquisition parameters."""

    def test_overlap_calculation_with_overlap(self):
        """Test overlap pixels calculated correctly from dx, dy, fov size."""
        fov_width = 2048
        fov_height = 2048
        dx_mm = 1.8  # Step size in mm
        dy_mm = 1.8
        pixel_size_um = 1.0  # 1 um/pixel

        # FOV size in mm = 2048 * 1.0 / 1000 = 2.048 mm
        # Overlap = FOV size - step size = 2.048 - 1.8 = 0.248 mm = 248 um = 248 pixels
        overlap = calculate_overlap_pixels(fov_width, fov_height, dx_mm, dy_mm, pixel_size_um)

        assert overlap[0] == 124  # top crop (half of y overlap)
        assert overlap[1] == 124  # bottom crop
        assert overlap[2] == 124  # left crop (half of x overlap)
        assert overlap[3] == 124  # right crop

    def test_overlap_calculation_no_overlap(self):
        """Test zero overlap when step size equals FOV size."""
        fov_width = 2048
        fov_height = 2048
        dx_mm = 2.048  # Exactly FOV size
        dy_mm = 2.048
        pixel_size_um = 1.0

        overlap = calculate_overlap_pixels(fov_width, fov_height, dx_mm, dy_mm, pixel_size_um)

        assert overlap == (0, 0, 0, 0)

    def test_overlap_calculation_asymmetric(self):
        """Test asymmetric overlap in x and y."""
        fov_width = 2048
        fov_height = 1536
        dx_mm = 1.8
        dy_mm = 1.4
        pixel_size_um = 1.0

        overlap = calculate_overlap_pixels(fov_width, fov_height, dx_mm, dy_mm, pixel_size_um)

        # X overlap = 2.048 - 1.8 = 0.248 mm = 248 pixels, half = 124
        # Y overlap = 1.536 - 1.4 = 0.136 mm = 136 pixels, half = 68
        assert overlap[2] == 124  # left
        assert overlap[3] == 124  # right
        assert overlap[0] == 68  # top
        assert overlap[1] == 68  # bottom


class TestDownsampleTile:
    """Tests for tile downsampling."""

    def test_downsample_tile_factor_2(self):
        """Test downsampling produces correct dimensions."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        source_pixel_size_um = 1.0
        target_pixel_size_um = 2.0

        downsampled = downsample_tile(tile, source_pixel_size_um, target_pixel_size_um)

        assert downsampled.shape == (50, 50)

    def test_downsample_tile_factor_5(self):
        """Test downsampling with factor 5."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        source_pixel_size_um = 1.0
        target_pixel_size_um = 5.0

        downsampled = downsample_tile(tile, source_pixel_size_um, target_pixel_size_um)

        assert downsampled.shape == (20, 20)

    def test_downsample_factor_less_than_one(self):
        """Test no downsampling when target resolution < source resolution."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        source_pixel_size_um = 2.0
        target_pixel_size_um = 1.0  # Target is higher resolution than source

        downsampled = downsample_tile(tile, source_pixel_size_um, target_pixel_size_um)

        # Should return original tile unchanged
        assert downsampled.shape == tile.shape
        assert np.array_equal(downsampled, tile)

    def test_downsample_preserves_dtype(self):
        """Test downsampling preserves image dtype."""
        tile = np.ones((100, 100), dtype=np.uint16) * 30000

        downsampled = downsample_tile(tile, 1.0, 2.0)

        assert downsampled.dtype == np.uint16

    def test_downsample_non_divisible_dimensions(self):
        """Test downsampling with non-divisible dimensions."""
        tile = np.random.randint(0, 65535, (103, 97), dtype=np.uint16)

        downsampled = downsample_tile(tile, 1.0, 2.0)

        # cv2.resize handles non-divisible dimensions
        assert downsampled.shape == (51, 48)  # floor(103/2), floor(97/2)

    def test_downsample_inter_linear_method(self):
        """Test downsampling with INTER_LINEAR method."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        downsampled = downsample_tile(tile, 1.0, 5.0, method=DownsamplingMethod.INTER_LINEAR)

        assert downsampled.shape == (20, 20)
        assert downsampled.dtype == np.uint16

    def test_downsample_inter_area_method(self):
        """Test downsampling with INTER_AREA method."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        downsampled = downsample_tile(tile, 1.0, 5.0, method=DownsamplingMethod.INTER_AREA)

        assert downsampled.shape == (20, 20)
        assert downsampled.dtype == np.uint16

    def test_downsample_inter_area_fast_method(self):
        """Test downsampling with INTER_AREA_FAST (pyrDown chain) method."""
        tile = np.random.randint(0, 65535, (200, 200), dtype=np.uint16)

        downsampled = downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA_FAST)

        assert downsampled.shape == (20, 20)
        assert downsampled.dtype == np.uint16

    def test_downsample_inter_area_fast_quality(self):
        """Test that INTER_AREA_FAST produces reasonable quality output."""
        # Use random noise pattern for more realistic quality comparison
        np.random.seed(42)
        tile = np.random.randint(0, 65535, (2048, 2048), dtype=np.uint16)

        linear = downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_LINEAR)
        area_fast = downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA_FAST)
        area = downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA)

        # All should have same shape
        assert linear.shape == area_fast.shape == area.shape == (204, 204)

        # INTER_AREA_FAST should be closer to INTER_AREA than INTER_LINEAR is
        rmse_linear_vs_area = np.sqrt(np.mean((linear.astype(float) - area.astype(float)) ** 2))
        rmse_fast_vs_area = np.sqrt(np.mean((area_fast.astype(float) - area.astype(float)) ** 2))

        # INTER_AREA_FAST should be closer to INTER_AREA than INTER_LINEAR is
        assert rmse_fast_vs_area < rmse_linear_vs_area

    def test_downsample_methods_produce_different_results(self):
        """Test that INTER_LINEAR and INTER_AREA produce different results."""
        # Use a pattern that shows difference between interpolation methods
        tile = np.zeros((100, 100), dtype=np.uint16)
        tile[::2, ::2] = 65535  # Checkerboard pattern

        linear = downsample_tile(tile, 1.0, 5.0, method=DownsamplingMethod.INTER_LINEAR)
        area = downsample_tile(tile, 1.0, 5.0, method=DownsamplingMethod.INTER_AREA)

        # Both should have same shape
        assert linear.shape == area.shape == (20, 20)
        # But different values (INTER_AREA averages, INTER_LINEAR interpolates)
        assert not np.array_equal(linear, area)


class TestDownsamplingMethodEnum:
    """Tests for DownsamplingMethod enum."""

    def test_enum_values(self):
        """Test enum has expected values."""
        assert DownsamplingMethod.INTER_LINEAR.value == "inter_linear"
        assert DownsamplingMethod.INTER_AREA_FAST.value == "inter_area_fast"
        assert DownsamplingMethod.INTER_AREA.value == "inter_area"

    def test_convert_from_string_linear(self):
        """Test converting string to INTER_LINEAR."""
        result = DownsamplingMethod.convert_to_enum("inter_linear")
        assert result == DownsamplingMethod.INTER_LINEAR

    def test_convert_from_string_area_fast(self):
        """Test converting string to INTER_AREA_FAST."""
        result = DownsamplingMethod.convert_to_enum("inter_area_fast")
        assert result == DownsamplingMethod.INTER_AREA_FAST

    def test_convert_from_string_area(self):
        """Test converting string to INTER_AREA."""
        result = DownsamplingMethod.convert_to_enum("inter_area")
        assert result == DownsamplingMethod.INTER_AREA

    def test_convert_from_enum(self):
        """Test that passing enum returns same enum."""
        result = DownsamplingMethod.convert_to_enum(DownsamplingMethod.INTER_LINEAR)
        assert result == DownsamplingMethod.INTER_LINEAR

    def test_convert_invalid_raises(self):
        """Test that invalid string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid downsampling method"):
            DownsamplingMethod.convert_to_enum("invalid_method")


class TestWellIdParsing:
    """Tests for well ID parsing."""

    def test_well_id_parsing_single_letter(self):
        """Test parsing A1 -> (0, 0), H12 -> (7, 11)."""
        assert parse_well_id("A1") == (0, 0)
        assert parse_well_id("A12") == (0, 11)
        assert parse_well_id("H1") == (7, 0)
        assert parse_well_id("H12") == (7, 11)

    def test_well_id_parsing_double_letter(self):
        """Test parsing AA1 -> (26, 0) for 1536-well plates."""
        assert parse_well_id("AA1") == (26, 0)
        assert parse_well_id("AF48") == (31, 47)

    def test_well_id_parsing_lowercase(self):
        """Test parsing works with lowercase."""
        assert parse_well_id("a1") == (0, 0)
        assert parse_well_id("b12") == (1, 11)

    def test_well_id_parsing_mixed_case(self):
        """Test parsing works with mixed case."""
        # "Ab6" gets uppercased to "AB6" = row AB (index 27) + column 6 (index 5)
        assert parse_well_id("Ab6") == (27, 5)
        # Single letter mixed case
        assert parse_well_id("b6") == (1, 5)


class TestWellIdFormatting:
    """Tests for well ID formatting."""

    def test_format_well_id_single_letter(self):
        """Test formatting (0, 0) -> A1, (7, 11) -> H12."""
        assert format_well_id(0, 0) == "A1"
        assert format_well_id(0, 11) == "A12"
        assert format_well_id(7, 0) == "H1"
        assert format_well_id(7, 11) == "H12"

    def test_format_well_id_double_letter(self):
        """Test formatting (26, 0) -> AA1 for 1536-well plates."""
        assert format_well_id(26, 0) == "AA1"
        assert format_well_id(31, 47) == "AF48"

    def test_format_well_id_boundary_rows(self):
        """Test boundary cases at single/double letter transition."""
        # Last single-letter row
        assert format_well_id(25, 0) == "Z1"
        # First double-letter row
        assert format_well_id(26, 0) == "AA1"
        # Second double-letter row
        assert format_well_id(27, 0) == "AB1"
        # Last row of first double-letter series (AZ)
        assert format_well_id(51, 0) == "AZ1"
        # First row of second double-letter series (BA)
        assert format_well_id(52, 0) == "BA1"

    def test_format_well_id_inverse_of_parse(self):
        """Test that format_well_id is the inverse of parse_well_id."""
        for row in range(32):
            for col in range(48):
                well_id = format_well_id(row, col)
                parsed_row, parsed_col = parse_well_id(well_id)
                assert (parsed_row, parsed_col) == (row, col), f"Round-trip failed for ({row}, {col}) -> {well_id}"


class TestPyrdownChain:
    """Tests for _pyrdown_chain helper function."""

    def test_pyrdown_chain_large_image(self):
        """Test pyrDown chain with large image requiring multiple reductions."""
        tile = np.random.randint(0, 65535, (2048, 2048), dtype=np.uint16)
        target_size = (200, 200)

        result = _pyrdown_chain(tile, target_size[0], target_size[1])

        assert result.shape == target_size
        assert result.dtype == tile.dtype

    def test_pyrdown_chain_small_image_no_pyramid(self):
        """Test pyrDown with image too small for pyramid reduction."""
        # When image is already close to target size, should just resize
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        target_size = (80, 80)

        result = _pyrdown_chain(tile, target_size[0], target_size[1])

        assert result.shape == target_size
        assert result.dtype == tile.dtype

    def test_pyrdown_chain_exact_target_size(self):
        """Test pyrDown when image is exactly target size."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        result = _pyrdown_chain(tile, 100, 100)

        assert result.shape == (100, 100)
        # Should be unchanged (or very close)
        assert np.array_equal(result, tile)

    def test_pyrdown_chain_power_of_2_dimensions(self):
        """Test pyrDown with exact power of 2 dimensions."""
        tile = np.random.randint(0, 65535, (1024, 1024), dtype=np.uint16)
        # 1024 -> 512 -> 256 -> 128 (3 pyrDowns, then resize to 100)
        target_size = (100, 100)

        result = _pyrdown_chain(tile, target_size[0], target_size[1])

        assert result.shape == target_size
        assert result.dtype == tile.dtype

    def test_pyrdown_chain_asymmetric_dimensions(self):
        """Test pyrDown with non-square image."""
        tile = np.random.randint(0, 65535, (2048, 1024), dtype=np.uint16)
        target_width, target_height = 100, 200

        result = _pyrdown_chain(tile, target_width, target_height)

        # numpy shape is (height, width)
        assert result.shape == (target_height, target_width)
        assert result.dtype == tile.dtype

    def test_pyrdown_chain_preserves_dtype_uint8(self):
        """Test pyrDown preserves uint8 dtype."""
        tile = np.random.randint(0, 255, (512, 512), dtype=np.uint8)
        target_size = (50, 50)

        result = _pyrdown_chain(tile, target_size[0], target_size[1])

        assert result.shape == target_size
        assert result.dtype == np.uint8


@pytest.mark.skipif(
    os.environ.get("RUN_PERF_TESTS") != "1",
    reason="Timing-based perf assertions are flaky on shared CI runners; opt in with RUN_PERF_TESTS=1.",
)
class TestDownsamplingPerformance:
    """Performance regression tests for downsampling methods."""

    def test_inter_area_fast_faster_than_inter_area(self):
        """Verify INTER_AREA_FAST is significantly faster than INTER_AREA."""
        import time

        tile = np.random.randint(0, 65535, (2048, 2048), dtype=np.uint16)

        # Warmup
        downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA_FAST)
        downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA)

        # Time INTER_AREA_FAST
        iterations = 5
        start = time.perf_counter()
        for _ in range(iterations):
            downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA_FAST)
        time_fast = (time.perf_counter() - start) / iterations

        # Time INTER_AREA
        start = time.perf_counter()
        for _ in range(iterations):
            downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA)
        time_area = (time.perf_counter() - start) / iterations

        # INTER_AREA_FAST should be at least 2x faster (conservative for CI variability)
        speedup = time_area / time_fast
        assert speedup > 2, f"INTER_AREA_FAST speedup {speedup:.1f}x is less than expected 2x"

    def test_inter_linear_fastest(self):
        """Verify INTER_LINEAR is the fastest method."""
        import time

        tile = np.random.randint(0, 65535, (2048, 2048), dtype=np.uint16)

        # Warmup
        for method in DownsamplingMethod:
            downsample_tile(tile, 1.0, 10.0, method=method)

        times = {}
        iterations = 5
        for method in DownsamplingMethod:
            start = time.perf_counter()
            for _ in range(iterations):
                downsample_tile(tile, 1.0, 10.0, method=method)
            times[method] = (time.perf_counter() - start) / iterations

        # INTER_LINEAR should be fastest
        assert times[DownsamplingMethod.INTER_LINEAR] < times[DownsamplingMethod.INTER_AREA_FAST]
        assert times[DownsamplingMethod.INTER_LINEAR] < times[DownsamplingMethod.INTER_AREA]
