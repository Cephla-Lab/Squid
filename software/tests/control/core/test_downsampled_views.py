"""Tests for downsampled well and plate view generation."""

import tempfile
import os

import numpy as np
import pytest

# These imports will fail until we implement the module
try:
    from control.core.downsampled_views import (
        DownsampledViewManager,
        WellTileAccumulator,
        _pyrdown_chain,
        calculate_overlap_pixels,
        crop_overlap,
        downsample_tile,
        downsample_to_resolutions,
        stitch_tiles,
        parse_well_id,
        format_well_id,
        ensure_plate_resolution_in_well_resolutions,
    )
    from control._def import DownsamplingMethod

    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not MODULE_AVAILABLE, reason="downsampled_views module not yet implemented")


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


class TestCropOverlap:
    """Tests for tile overlap cropping."""

    def test_crop_overlap_all_sides(self):
        """Test tile cropping removes correct overlap regions."""
        tile = np.ones((100, 100), dtype=np.uint16) * 1000
        overlap = (10, 10, 15, 15)  # top, bottom, left, right

        cropped = crop_overlap(tile, overlap)

        assert cropped.shape == (80, 70)  # 100-10-10, 100-15-15

    def test_crop_overlap_preserves_dtype(self):
        """Test cropping preserves image dtype."""
        tile = np.ones((100, 100), dtype=np.uint16) * 65535
        overlap = (5, 5, 5, 5)

        cropped = crop_overlap(tile, overlap)

        assert cropped.dtype == np.uint16
        assert np.all(cropped == 65535)

    def test_crop_overlap_zero(self):
        """Test no cropping when overlap is zero."""
        tile = np.ones((100, 100), dtype=np.uint16)
        overlap = (0, 0, 0, 0)

        cropped = crop_overlap(tile, overlap)

        assert cropped.shape == tile.shape
        assert np.array_equal(cropped, tile)

    def test_crop_overlap_edge_tile_top_left(self):
        """Test cropping for edge tile (no top/left crop)."""
        tile = np.ones((100, 100), dtype=np.uint16)
        # For edge tiles, we only crop the sides with neighbors
        overlap = (0, 10, 0, 15)  # No top/left, crop bottom/right

        cropped = crop_overlap(tile, overlap)

        assert cropped.shape == (90, 85)

    def test_crop_overlap_rgb_image(self):
        """Test cropping works on RGB images."""
        tile = np.ones((100, 100, 3), dtype=np.uint8) * 128
        overlap = (10, 10, 10, 10)

        cropped = crop_overlap(tile, overlap)

        assert cropped.shape == (80, 80, 3)


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


class TestDownsampleToResolutions:
    """Tests for multi-resolution downsampling."""

    def test_downsample_to_resolutions_single(self):
        """Test downsampling to a single resolution."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        results = downsample_to_resolutions(tile, 1.0, [5.0])

        assert len(results) == 1
        assert 5.0 in results
        assert results[5.0].shape == (20, 20)

    def test_downsample_to_resolutions_multiple(self):
        """Test downsampling to multiple resolutions."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        results = downsample_to_resolutions(tile, 1.0, [2.0, 5.0, 10.0])

        assert len(results) == 3
        assert results[2.0].shape == (50, 50)
        assert results[5.0].shape == (20, 20)
        assert results[10.0].shape == (10, 10)

    def test_downsample_to_resolutions_inter_linear(self):
        """Test multi-resolution with INTER_LINEAR (parallel from original)."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        results = downsample_to_resolutions(tile, 1.0, [5.0, 10.0], method=DownsamplingMethod.INTER_LINEAR)

        assert len(results) == 2
        assert results[5.0].shape == (20, 20)
        assert results[10.0].shape == (10, 10)

    def test_downsample_to_resolutions_inter_area(self):
        """Test multi-resolution with INTER_AREA (cascaded)."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        results = downsample_to_resolutions(tile, 1.0, [5.0, 10.0], method=DownsamplingMethod.INTER_AREA)

        assert len(results) == 2
        assert results[5.0].shape == (20, 20)
        assert results[10.0].shape == (10, 10)

    def test_downsample_to_resolutions_inter_area_fast(self):
        """Test multi-resolution with INTER_AREA_FAST (parallel, pyrDown chain)."""
        tile = np.random.randint(0, 65535, (200, 200), dtype=np.uint16)

        results = downsample_to_resolutions(tile, 1.0, [5.0, 10.0], method=DownsamplingMethod.INTER_AREA_FAST)

        assert len(results) == 2
        assert results[5.0].shape == (40, 40)
        assert results[10.0].shape == (20, 20)

    def test_downsample_to_resolutions_unsorted_input(self):
        """Test that unsorted resolutions are handled correctly."""
        tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        # Pass resolutions in unsorted order
        results = downsample_to_resolutions(tile, 1.0, [10.0, 2.0, 5.0])

        assert len(results) == 3
        assert results[2.0].shape == (50, 50)
        assert results[5.0].shape == (20, 20)
        assert results[10.0].shape == (10, 10)

    def test_downsample_to_resolutions_preserves_dtype(self):
        """Test that dtype is preserved across all resolutions."""
        tile = np.ones((100, 100), dtype=np.uint16) * 30000

        results = downsample_to_resolutions(tile, 1.0, [2.0, 5.0, 10.0])

        for resolution, img in results.items():
            assert img.dtype == np.uint16

    def test_downsample_cascaded_vs_parallel_quality(self):
        """Test that cascaded INTER_AREA has minimal quality loss vs parallel."""
        # For INTER_AREA, cascaded should be very close to parallel
        tile = np.random.randint(0, 65535, (1000, 1000), dtype=np.uint16)

        # Get cascaded result
        cascaded = downsample_to_resolutions(tile, 1.0, [5.0, 10.0, 20.0], method=DownsamplingMethod.INTER_AREA)

        # Compute parallel result manually
        parallel_10 = downsample_tile(tile, 1.0, 10.0, method=DownsamplingMethod.INTER_AREA)

        # For INTER_AREA cascaded, the 10um result should be very close to parallel
        # (small differences due to rounding in intermediate steps)
        diff = np.abs(cascaded[10.0].astype(float) - parallel_10.astype(float))
        max_diff = diff.max()
        # Allow some tolerance for cascading artifacts
        assert max_diff < 1000, f"Max diff {max_diff} too large for INTER_AREA cascading"


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


class TestStitchTiles:
    """Tests for tile stitching."""

    def test_stitch_tiles_single_fov(self):
        """Test stitching with single FOV returns tile as-is."""
        tile = np.ones((100, 100), dtype=np.uint16) * 5000
        tiles = [(tile, (0.0, 0.0))]  # Single tile at origin
        pixel_size_um = 1.0

        stitched = stitch_tiles(tiles, pixel_size_um)

        assert stitched.shape == tile.shape
        assert np.array_equal(stitched, tile)

    def test_stitch_tiles_2x2_grid(self):
        """Test stitching 2x2 FOV grid with known positions."""
        tile_size = 100
        tile1 = np.ones((tile_size, tile_size), dtype=np.uint16) * 1000
        tile2 = np.ones((tile_size, tile_size), dtype=np.uint16) * 2000
        tile3 = np.ones((tile_size, tile_size), dtype=np.uint16) * 3000
        tile4 = np.ones((tile_size, tile_size), dtype=np.uint16) * 4000

        pixel_size_um = 1.0
        step_mm = 0.1  # 100 um = 100 pixels at 1 um/pixel

        tiles = [
            (tile1, (0.0, 0.0)),  # top-left
            (tile2, (step_mm, 0.0)),  # top-right
            (tile3, (0.0, step_mm)),  # bottom-left
            (tile4, (step_mm, step_mm)),  # bottom-right
        ]

        stitched = stitch_tiles(tiles, pixel_size_um)

        assert stitched.shape == (200, 200)
        # Check each quadrant has correct value
        assert np.all(stitched[0:100, 0:100] == 1000)  # top-left
        assert np.all(stitched[0:100, 100:200] == 2000)  # top-right
        assert np.all(stitched[100:200, 0:100] == 3000)  # bottom-left
        assert np.all(stitched[100:200, 100:200] == 4000)  # bottom-right

    def test_stitch_tiles_respects_positions(self):
        """Test tiles placed at correct positions based on coordinates."""
        tile = np.ones((50, 50), dtype=np.uint16) * 1000
        pixel_size_um = 2.0  # 2 um/pixel

        # Single tile at position (0.1, 0.1) - canvas starts at min position
        # So this single tile creates a 50x50 canvas starting at its position
        tiles = [(tile, (0.1, 0.1))]

        stitched = stitch_tiles(tiles, pixel_size_um)

        # Single tile at non-zero position still creates canvas sized to fit
        # Canvas origin is at (0.1, 0.1), so tile fills entire canvas
        assert stitched.shape == (50, 50)
        assert np.all(stitched == 1000)


class TestPlateViewManager:
    """Tests for DownsampledViewManager."""

    def test_plate_view_manager_init(self):
        """Test plate view array initialized with correct shape."""
        num_rows = 8  # 96-well plate
        num_cols = 12
        well_slot_shape = (100, 100)

        manager = DownsampledViewManager(num_rows, num_cols, well_slot_shape)

        # Shape is (C, H, W) with default 1 channel
        assert manager.plate_view.shape == (1, 800, 1200)
        assert manager.plate_view.dtype == np.uint16
        assert np.all(manager.plate_view == 0)

    def test_plate_view_manager_init_custom_dtype(self):
        """Test plate view with custom dtype."""
        manager = DownsampledViewManager(4, 6, (50, 50), dtype=np.uint8)

        assert manager.plate_view.dtype == np.uint8

    def test_plate_view_manager_init_multi_channel(self):
        """Test plate view with multiple channels."""
        manager = DownsampledViewManager(4, 6, (50, 50), num_channels=3, channel_names=["DAPI", "GFP", "RFP"])

        assert manager.plate_view.shape == (3, 200, 300)
        assert manager.num_channels == 3
        assert manager.channel_names == ["DAPI", "GFP", "RFP"]

    def test_plate_view_manager_update_well(self):
        """Test well image placed at correct grid position."""
        manager = DownsampledViewManager(8, 12, (100, 100))
        well_image = np.ones((80, 80), dtype=np.uint16) * 5000

        # Update well B3 (row=1, col=2) - pass as dict with channel_idx
        manager.update_well(1, 2, {0: well_image})

        # Check image placed at correct position (channel 0)
        y_start = 1 * 100
        x_start = 2 * 100
        assert np.all(manager.plate_view[0, y_start : y_start + 80, x_start : x_start + 80] == 5000)
        # Check surrounding area is still zero
        assert manager.plate_view[0, 0, 0] == 0
        assert manager.plate_view[0, y_start - 1, x_start] == 0

    def test_plate_view_compact_layout(self):
        """Test wells are immediately adjacent (no gaps)."""
        manager = DownsampledViewManager(2, 2, (100, 100))

        # Fill all wells with different values (pass as dicts)
        manager.update_well(0, 0, {0: np.ones((100, 100), dtype=np.uint16) * 1000})
        manager.update_well(0, 1, {0: np.ones((100, 100), dtype=np.uint16) * 2000})
        manager.update_well(1, 0, {0: np.ones((100, 100), dtype=np.uint16) * 3000})
        manager.update_well(1, 1, {0: np.ones((100, 100), dtype=np.uint16) * 4000})

        # Check wells are adjacent - no gaps between them (channel 0)
        assert manager.plate_view[0, 99, 99] == 1000  # Bottom-right of A1
        assert manager.plate_view[0, 99, 100] == 2000  # Bottom-left of A2 (immediately adjacent)
        assert manager.plate_view[0, 100, 99] == 3000  # Top-right of B1 (immediately adjacent)
        assert manager.plate_view[0, 100, 100] == 4000  # Top-left of B2

    def test_plate_view_save(self):
        """Test plate view can be saved to disk."""
        manager = DownsampledViewManager(2, 2, (50, 50))
        manager.update_well(0, 0, {0: np.ones((50, 50), dtype=np.uint16) * 1000})

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "plate_10um.tiff")
            manager.save_plate_view(path)

            assert os.path.exists(path)
            # Verify we can read it back
            import tifffile

            loaded = tifffile.imread(path)
            # Shape is (C, H, W) with 1 channel
            assert loaded.shape == (1, 100, 100)
            assert np.all(loaded[0, 0:50, 0:50] == 1000)

    def test_plate_view_partial_acquisition(self):
        """Test plate view valid even if only some wells imaged."""
        manager = DownsampledViewManager(8, 12, (100, 100))

        # Only image wells A1 and H12 (pass as dicts)
        manager.update_well(0, 0, {0: np.ones((100, 100), dtype=np.uint16) * 1000})
        manager.update_well(7, 11, {0: np.ones((100, 100), dtype=np.uint16) * 2000})

        # Both wells should be present, rest should be zero (channel 0)
        assert np.all(manager.plate_view[0, 0:100, 0:100] == 1000)
        assert np.all(manager.plate_view[0, 700:800, 1100:1200] == 2000)
        assert np.all(manager.plate_view[0, 100:700, :] == 0)  # Middle rows empty

    def test_plate_view_multi_channel_update(self):
        """Test updating multiple channels for a well."""
        manager = DownsampledViewManager(2, 2, (50, 50), num_channels=2, channel_names=["Ch0", "Ch1"])

        # Update well A1 with different values for each channel
        manager.update_well(
            0,
            0,
            {
                0: np.ones((50, 50), dtype=np.uint16) * 1000,
                1: np.ones((50, 50), dtype=np.uint16) * 2000,
            },
        )

        # Check each channel has correct values
        assert np.all(manager.plate_view[0, 0:50, 0:50] == 1000)
        assert np.all(manager.plate_view[1, 0:50, 0:50] == 2000)
        # Other wells still zero
        assert np.all(manager.plate_view[0, 0:50, 50:100] == 0)
        assert np.all(manager.plate_view[1, 0:50, 50:100] == 0)


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


class TestConfigValidation:
    """Tests for configuration validation."""

    def test_config_plate_resolution_already_in_list(self):
        """Test no change when plate resolution already in well resolutions."""
        well_resolutions = [5.0, 10.0, 20.0]
        plate_resolution = 10.0

        result = ensure_plate_resolution_in_well_resolutions(well_resolutions, plate_resolution)

        assert result == [5.0, 10.0, 20.0]

    def test_config_plate_resolution_added_if_missing(self):
        """Test plate resolution auto-added if missing from well resolutions."""
        well_resolutions = [5.0, 20.0]
        plate_resolution = 10.0

        result = ensure_plate_resolution_in_well_resolutions(well_resolutions, plate_resolution)

        assert 10.0 in result
        assert result == [5.0, 10.0, 20.0]  # Should be sorted

    def test_config_does_not_modify_original_list(self):
        """Test original list is not modified."""
        well_resolutions = [5.0, 20.0]
        plate_resolution = 10.0

        result = ensure_plate_resolution_in_well_resolutions(well_resolutions, plate_resolution)

        assert well_resolutions == [5.0, 20.0]  # Original unchanged
        assert result == [5.0, 10.0, 20.0]


class TestCircularScanInSquareSlot:
    """Tests for circular scan areas in square well slots."""

    def test_circular_scan_in_square_slot(self):
        """Test circular well scan placed correctly in square plate grid slot."""
        manager = DownsampledViewManager(2, 2, (100, 100))

        # Create a circular "well image" - circle inscribed in square
        well_image = np.zeros((100, 100), dtype=np.uint16)
        center = (50, 50)
        radius = 40
        y, x = np.ogrid[:100, :100]
        mask = (x - center[0]) ** 2 + (y - center[1]) ** 2 <= radius**2
        well_image[mask] = 5000

        manager.update_well(0, 0, {0: well_image})

        # Corners should be zero (outside circle) - channel 0
        assert manager.plate_view[0, 0, 0] == 0
        assert manager.plate_view[0, 0, 99] == 0
        assert manager.plate_view[0, 99, 0] == 0
        assert manager.plate_view[0, 99, 99] == 0

        # Center should have data
        assert manager.plate_view[0, 50, 50] == 5000


class TestWellTileAccumulatorZProjection:
    """Tests for z-projection modes in WellTileAccumulator."""

    def test_middle_z_mode_single_z(self):
        """Test middle mode with single z-level (z=0 is middle)."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=1,
            total_channels=1,
            pixel_size_um=1.0,
            total_z_levels=1,
            z_projection_mode="middle",
        )

        tile = np.ones((100, 100), dtype=np.uint16) * 1000
        accumulator.add_tile(tile, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=0)

        assert accumulator.is_complete()
        stitched = accumulator.stitch_all_channels()
        assert 0 in stitched
        assert np.all(stitched[0] == 1000)

    def test_middle_z_mode_three_z_levels(self):
        """Test middle mode with 3 z-levels (only z=1 is used)."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=1,
            total_channels=1,
            pixel_size_um=1.0,
            total_z_levels=3,
            z_projection_mode="middle",
        )

        # Add z=0 (should be ignored)
        tile_z0 = np.ones((100, 100), dtype=np.uint16) * 100
        accumulator.add_tile(tile_z0, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=0)
        assert not accumulator.is_complete()

        # Add z=1 (middle, should be accepted)
        tile_z1 = np.ones((100, 100), dtype=np.uint16) * 500
        accumulator.add_tile(tile_z1, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=1)
        assert accumulator.is_complete()

        # Add z=2 (should be ignored)
        tile_z2 = np.ones((100, 100), dtype=np.uint16) * 900
        accumulator.add_tile(tile_z2, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=2)

        stitched = accumulator.stitch_all_channels()
        assert np.all(stitched[0] == 500)  # Only middle z value

    def test_mip_mode_single_z(self):
        """Test MIP mode with single z-level."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=1,
            total_channels=1,
            pixel_size_um=1.0,
            total_z_levels=1,
            z_projection_mode="mip",
        )

        tile = np.ones((100, 100), dtype=np.uint16) * 1000
        accumulator.add_tile(tile, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=0)

        assert accumulator.is_complete()
        stitched = accumulator.stitch_all_channels()
        assert np.all(stitched[0] == 1000)

    def test_mip_mode_running_maximum(self):
        """Test MIP mode computes running maximum across z-levels."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=1,
            total_channels=1,
            pixel_size_um=1.0,
            total_z_levels=3,
            z_projection_mode="mip",
        )

        # z=0: low values
        tile_z0 = np.ones((100, 100), dtype=np.uint16) * 100
        accumulator.add_tile(tile_z0, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=0)
        assert not accumulator.is_complete()

        # z=1: high values
        tile_z1 = np.ones((100, 100), dtype=np.uint16) * 900
        accumulator.add_tile(tile_z1, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=1)
        assert not accumulator.is_complete()

        # z=2: medium values
        tile_z2 = np.ones((100, 100), dtype=np.uint16) * 500
        accumulator.add_tile(tile_z2, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=2)
        assert accumulator.is_complete()

        stitched = accumulator.stitch_all_channels()
        assert np.all(stitched[0] == 900)  # Maximum across z-levels

    def test_mip_mode_pixel_wise_maximum(self):
        """Test MIP correctly computes per-pixel maximum."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=1,
            total_channels=1,
            pixel_size_um=1.0,
            total_z_levels=2,
            z_projection_mode="mip",
        )

        # z=0: high in top-left, low elsewhere
        tile_z0 = np.zeros((100, 100), dtype=np.uint16)
        tile_z0[:50, :50] = 1000
        accumulator.add_tile(tile_z0, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=0)

        # z=1: high in bottom-right, low elsewhere
        tile_z1 = np.zeros((100, 100), dtype=np.uint16)
        tile_z1[50:, 50:] = 2000
        accumulator.add_tile(tile_z1, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=1)

        assert accumulator.is_complete()
        stitched = accumulator.stitch_all_channels()

        # Top-left should be 1000 (max from z=0)
        assert np.all(stitched[0][:50, :50] == 1000)
        # Bottom-right should be 2000 (max from z=1)
        assert np.all(stitched[0][50:, 50:] == 2000)
        # Other regions should be 0 (max of 0 and 0)
        assert np.all(stitched[0][:50, 50:] == 0)
        assert np.all(stitched[0][50:, :50] == 0)

    def test_mip_mode_multi_fov(self):
        """Test MIP mode with multiple FOVs."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=2,
            total_channels=1,
            pixel_size_um=1.0,
            total_z_levels=2,
            z_projection_mode="mip",
        )

        # FOV 0: z=0 and z=1
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 100, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=0)
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 200, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=1)

        assert not accumulator.is_complete()  # Still need FOV 1

        # FOV 1: z=0 and z=1
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 300, (0.05, 0.0), channel_idx=0, fov_idx=1, z_index=0)
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 400, (0.05, 0.0), channel_idx=0, fov_idx=1, z_index=1)

        assert accumulator.is_complete()
        stitched = accumulator.stitch_all_channels()

        # FOV 0 max is 200, FOV 1 max is 400
        assert stitched[0][25, 25] == 200  # FOV 0 region
        assert stitched[0][25, 75] == 400  # FOV 1 region

    def test_mip_mode_multi_channel(self):
        """Test MIP mode with multiple channels."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=1,
            total_channels=2,
            pixel_size_um=1.0,
            total_z_levels=2,
            z_projection_mode="mip",
        )

        # Channel 0
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 100, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=0)
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 500, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=1)

        # Channel 1
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 800, (0.0, 0.0), channel_idx=1, fov_idx=0, z_index=0)
        accumulator.add_tile(np.ones((50, 50), dtype=np.uint16) * 200, (0.0, 0.0), channel_idx=1, fov_idx=0, z_index=1)

        assert accumulator.is_complete()
        stitched = accumulator.stitch_all_channels()

        assert np.all(stitched[0] == 500)  # Channel 0 max
        assert np.all(stitched[1] == 800)  # Channel 1 max

    def test_mip_no_extra_memory_per_z(self):
        """Test MIP mode doesn't accumulate memory per z-level."""
        accumulator = WellTileAccumulator(
            well_id="A1",
            total_fovs=1,
            total_channels=1,
            pixel_size_um=1.0,
            total_z_levels=100,  # Many z-levels
            z_projection_mode="mip",
        )

        for z in range(100):
            tile = np.ones((100, 100), dtype=np.uint16) * z
            accumulator.add_tile(tile, (0.0, 0.0), channel_idx=0, fov_idx=0, z_index=z)

        # Should only have 1 tile stored (the running max), not 100
        assert len(accumulator.mip_tiles) == 1
        assert accumulator.is_complete()

        stitched = accumulator.stitch_all_channels()
        assert np.all(stitched[0] == 99)  # Max of 0..99


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
