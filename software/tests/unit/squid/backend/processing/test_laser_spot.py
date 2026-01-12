"""Tests for the laser_spot module."""

import numpy as np
import pytest

from squid.backend.processing.laser_spot import (
    DisplacementResult,
    SpotDetectionResult,
    compute_correlation,
    compute_displacement,
    extract_spot_crop,
    is_spot_in_range,
    normalize_crop_for_reference,
    remove_background,
)


class TestSpotDetectionResult:
    """Tests for SpotDetectionResult dataclass."""

    def test_is_valid_with_positive_snr(self):
        """Result is valid when SNR is positive."""
        result = SpotDetectionResult(x=100, y=100, intensity=1000, snr=10.0, background=50)
        assert result.is_valid is True

    def test_is_invalid_with_zero_snr(self):
        """Result is invalid when SNR is zero."""
        result = SpotDetectionResult(x=100, y=100, intensity=0, snr=0.0, background=50)
        assert result.is_valid is False

    def test_is_invalid_with_negative_snr(self):
        """Result is invalid when SNR is negative."""
        result = SpotDetectionResult(x=100, y=100, intensity=0, snr=-1.0, background=50)
        assert result.is_valid is False


class TestDisplacementResult:
    """Tests for DisplacementResult dataclass."""

    def test_is_valid_with_valid_displacement(self):
        """Result is valid with valid displacement and positive SNR."""
        result = DisplacementResult(
            displacement_um=5.0, spot_x=100, spot_y=100, snr=10.0, intensity=1000
        )
        assert result.is_valid is True

    def test_is_invalid_with_nan_displacement(self):
        """Result is invalid when displacement is NaN."""
        result = DisplacementResult(
            displacement_um=float("nan"), spot_x=100, spot_y=100, snr=10.0, intensity=1000
        )
        assert result.is_valid is False

    def test_is_invalid_with_zero_snr(self):
        """Result is invalid when SNR is zero."""
        result = DisplacementResult(
            displacement_um=5.0, spot_x=100, spot_y=100, snr=0.0, intensity=1000
        )
        assert result.is_valid is False


class TestRemoveBackground:
    """Tests for remove_background function."""

    def test_removes_uniform_background(self):
        """Removes uniform background from image."""
        # Create image with uniform background and a bright spot
        image = np.ones((100, 100), dtype=np.uint8) * 50
        image[45:55, 45:55] = 200

        result = remove_background(image, kernel_size=20)

        # Background should be mostly removed, spot should remain bright
        assert result[50, 50] > result[10, 10]

    def test_output_shape_matches_input(self):
        """Output has same shape as input."""
        image = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        result = remove_background(image)
        assert result.shape == image.shape

    def test_handles_small_kernel(self):
        """Works with small kernel size."""
        image = np.random.randint(0, 256, (50, 50), dtype=np.uint8)
        result = remove_background(image, kernel_size=5)
        assert result.shape == image.shape


class TestComputeDisplacement:
    """Tests for compute_displacement function."""

    def test_positive_displacement(self):
        """Computes positive displacement correctly."""
        displacement = compute_displacement(spot_x=110, reference_x=100, pixel_to_um=0.5)
        assert displacement == pytest.approx(5.0)

    def test_negative_displacement(self):
        """Computes negative displacement correctly."""
        displacement = compute_displacement(spot_x=90, reference_x=100, pixel_to_um=0.5)
        assert displacement == pytest.approx(-5.0)

    def test_zero_displacement(self):
        """Returns zero when spot is at reference."""
        displacement = compute_displacement(spot_x=100, reference_x=100, pixel_to_um=0.5)
        assert displacement == 0.0

    def test_different_pixel_to_um(self):
        """Uses pixel_to_um conversion factor."""
        displacement = compute_displacement(spot_x=110, reference_x=100, pixel_to_um=2.0)
        assert displacement == pytest.approx(20.0)


class TestExtractSpotCrop:
    """Tests for extract_spot_crop function."""

    def test_extracts_centered_crop(self):
        """Extracts crop centered on spot location."""
        image = np.arange(100 * 100, dtype=np.uint8).reshape(100, 100)
        crop, bounds = extract_spot_crop(image, center_x=50, center_y=50, crop_size=20)

        assert crop.shape == (20, 20)
        assert bounds == (40, 40, 60, 60)

    def test_handles_edge_crop(self):
        """Handles crop at image edge without error."""
        image = np.ones((100, 100), dtype=np.uint8)
        crop, bounds = extract_spot_crop(image, center_x=5, center_y=5, crop_size=20)

        # Crop should be clipped to image bounds
        assert crop.shape[0] <= 20
        assert crop.shape[1] <= 20
        assert bounds[0] >= 0
        assert bounds[1] >= 0

    def test_handles_corner_crop(self):
        """Handles crop at image corner."""
        image = np.ones((100, 100), dtype=np.uint8)
        crop, bounds = extract_spot_crop(image, center_x=0, center_y=0, crop_size=20)

        assert bounds[0] == 0
        assert bounds[1] == 0

    def test_bounds_returned_correctly(self):
        """Returns correct bounds tuple."""
        image = np.ones((100, 100), dtype=np.uint8)
        _, bounds = extract_spot_crop(image, center_x=50, center_y=60, crop_size=10)

        x_start, y_start, x_end, y_end = bounds
        assert x_end - x_start == 10
        assert y_end - y_start == 10


class TestComputeCorrelation:
    """Tests for compute_correlation function."""

    def test_identical_crops_return_one(self):
        """Identical normalized crops return correlation ~1."""
        crop = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
        ref = normalize_crop_for_reference(crop)

        correlation = compute_correlation(crop, ref)
        assert correlation == pytest.approx(1.0, abs=0.01)

    def test_different_crops_return_lower_correlation(self):
        """Different crops return lower correlation."""
        crop1 = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
        crop2 = np.array([[9, 8, 7], [6, 5, 4], [3, 2, 1]], dtype=np.float32)
        ref = normalize_crop_for_reference(crop2)

        correlation = compute_correlation(crop1, ref)
        assert correlation is not None
        assert correlation < 0.5

    def test_returns_none_for_empty_crop(self):
        """Returns None for empty crop."""
        empty = np.array([], dtype=np.float32).reshape(0, 0)
        ref = np.ones((3, 3), dtype=np.float32)

        correlation = compute_correlation(empty, ref)
        assert correlation is None

    def test_returns_none_for_shape_mismatch(self):
        """Returns None when shapes don't match."""
        crop1 = np.ones((3, 3), dtype=np.float32)
        crop2 = np.ones((4, 4), dtype=np.float32)

        correlation = compute_correlation(crop1, crop2)
        assert correlation is None

    def test_returns_none_for_zero_image(self):
        """Returns None for all-zero image."""
        crop = np.zeros((3, 3), dtype=np.float32)
        ref = np.ones((3, 3), dtype=np.float32)

        correlation = compute_correlation(crop, ref)
        assert correlation is None


class TestNormalizeCropForReference:
    """Tests for normalize_crop_for_reference function."""

    def test_normalizes_crop(self):
        """Produces normalized output."""
        crop = np.array([[10, 20], [30, 40]], dtype=np.uint8)
        normalized = normalize_crop_for_reference(crop)

        assert normalized is not None
        assert normalized.dtype == np.float32

    def test_returns_none_for_empty(self):
        """Returns None for empty array."""
        empty = np.array([], dtype=np.uint8).reshape(0, 0)
        normalized = normalize_crop_for_reference(empty)
        assert normalized is None

    def test_returns_none_for_zero_image(self):
        """Returns None for all-zero image."""
        zeros = np.zeros((3, 3), dtype=np.uint8)
        normalized = normalize_crop_for_reference(zeros)
        assert normalized is None


class TestIsSpotInRange:
    """Tests for is_spot_in_range function."""

    def test_spot_within_range(self):
        """Returns True when spot is within range."""
        result = is_spot_in_range(
            spot_x=105, reference_x=100, pixel_to_um=0.5, max_range_um=10.0
        )
        assert result is True

    def test_spot_outside_range(self):
        """Returns False when spot is outside range."""
        result = is_spot_in_range(
            spot_x=150, reference_x=100, pixel_to_um=0.5, max_range_um=10.0
        )
        assert result is False

    def test_spot_exactly_at_boundary(self):
        """Returns True when spot is exactly at boundary."""
        result = is_spot_in_range(
            spot_x=120, reference_x=100, pixel_to_um=0.5, max_range_um=10.0
        )
        assert result is True

    def test_negative_displacement_in_range(self):
        """Handles negative displacement correctly."""
        result = is_spot_in_range(
            spot_x=95, reference_x=100, pixel_to_um=0.5, max_range_um=10.0
        )
        assert result is True
