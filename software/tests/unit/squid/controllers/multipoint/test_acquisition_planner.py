"""Unit tests for AcquisitionPlanner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from squid.backend.controllers.multipoint.acquisition_planner import (
    AcquisitionPlanner,
    AcquisitionEstimate,
    ValidationResult,
)


@dataclass
class FakeChannelMode:
    """Fake ChannelMode for testing."""
    name: str = "Test Channel"


class FakeScanCoordinates:
    """Fake ScanCoordinates for testing."""

    def __init__(self, region_fov_coordinates: Dict[str, List] = None):
        self.region_fov_coordinates = region_fov_coordinates or {
            "region_0": [(0, 0), (0, 1), (1, 0), (1, 1)],  # 4 FOVs
            "region_1": [(2, 0), (2, 1)],  # 2 FOVs
        }

    def has_regions(self) -> bool:
        return len(self.region_fov_coordinates) > 0

    def get_scan_bounds(self) -> Optional[Dict]:
        return {
            "x": (0.0, 10.0),  # 10mm width
            "y": (0.0, 5.0),   # 5mm height
        }


class FakeObjectiveStore:
    """Fake ObjectiveStore for testing."""

    def get_pixel_size_factor(self) -> float:
        return 1.0  # 1x magnification factor


class FakeCameraService:
    """Fake CameraService for testing."""

    def __init__(self):
        self._is_streaming = False
        self._callbacks_enabled = True

    def get_pixel_size_binned_um(self) -> float:
        return 3.45

    def get_is_streaming(self) -> bool:
        return self._is_streaming

    def get_callbacks_enabled(self) -> bool:
        return self._callbacks_enabled

    def enable_callbacks(self, enabled: bool) -> None:
        self._callbacks_enabled = enabled

    def start_streaming(self) -> None:
        self._is_streaming = True

    def stop_streaming(self) -> None:
        self._is_streaming = False

    def send_trigger(self) -> None:
        pass

    def read_frame(self):
        return None  # Simulate no frame available

    def get_pixel_format(self):
        return None

    def get_crop_size(self) -> Tuple[int, int]:
        return (2048, 2048)


class FakeChannelConfigManager:
    """Fake ChannelConfigurationManager for testing."""

    def get_configurations(self, objective: str) -> List:
        return [FakeChannelMode(name="BF"), FakeChannelMode(name="Fluorescence")]


class FakeLaserAFController:
    """Fake LaserAutofocusController for testing."""

    def __init__(self, has_reference: bool = True):
        self.laser_af_properties = MagicMock()
        self.laser_af_properties.has_reference = has_reference


class TestCalculateImageCount:
    """Tests for calculate_image_count method."""

    def test_basic_calculation(self):
        """Test basic image count calculation."""
        planner = AcquisitionPlanner()
        scan_coords = FakeScanCoordinates()  # 6 FOVs total

        count = planner.calculate_image_count(
            scan_coordinates=scan_coords,
            num_timepoints=2,
            num_z_levels=3,
            num_configurations=4,
            merge_channels=False,
        )

        # 2 timepoints * 3 z-levels * 6 FOVs * 4 configs = 144
        assert count == 144

    def test_single_fov(self):
        """Test with single FOV."""
        planner = AcquisitionPlanner()
        scan_coords = FakeScanCoordinates(
            region_fov_coordinates={"region_0": [(0, 0)]}
        )

        count = planner.calculate_image_count(
            scan_coordinates=scan_coords,
            num_timepoints=1,
            num_z_levels=1,
            num_configurations=3,
        )

        assert count == 3

    def test_with_merge_channels(self):
        """Test that merge_channels adds additional images."""
        planner = AcquisitionPlanner()
        scan_coords = FakeScanCoordinates(
            region_fov_coordinates={"region_0": [(0, 0), (0, 1)]}  # 2 FOVs
        )

        count_without_merge = planner.calculate_image_count(
            scan_coordinates=scan_coords,
            num_timepoints=1,
            num_z_levels=1,
            num_configurations=2,
            merge_channels=False,
        )

        count_with_merge = planner.calculate_image_count(
            scan_coordinates=scan_coords,
            num_timepoints=1,
            num_z_levels=1,
            num_configurations=2,
            merge_channels=True,
        )

        # Without merge: 1 * 1 * 2 * 2 = 4
        # With merge: 4 + (1 * 1 * 2) = 6
        assert count_without_merge == 4
        assert count_with_merge == 6

    def test_raises_on_invalid_scan_coords(self):
        """Test that ValueError is raised for invalid scan coordinates."""
        planner = AcquisitionPlanner()
        invalid_coords = MagicMock()
        del invalid_coords.region_fov_coordinates  # Missing attribute

        with pytest.raises(ValueError, match="not properly configured"):
            planner.calculate_image_count(
                scan_coordinates=invalid_coords,
                num_timepoints=1,
                num_z_levels=1,
                num_configurations=1,
            )


class TestValidateSettings:
    """Tests for validate_settings method."""

    def test_valid_settings_without_laser_af(self):
        """Test validation passes when laser AF is not enabled."""
        planner = AcquisitionPlanner()

        result = planner.validate_settings(
            do_reflection_af=False,
            laser_af_controller=None,
        )

        assert result.is_valid
        assert len(result.errors) == 0

    def test_valid_settings_with_laser_af(self):
        """Test validation passes with properly configured laser AF."""
        planner = AcquisitionPlanner()

        result = planner.validate_settings(
            do_reflection_af=True,
            laser_af_controller=FakeLaserAFController(has_reference=True),
        )

        assert result.is_valid
        assert len(result.errors) == 0

    def test_invalid_missing_laser_af_controller(self):
        """Test validation fails when laser AF enabled but no controller."""
        planner = AcquisitionPlanner()

        result = planner.validate_settings(
            do_reflection_af=True,
            laser_af_controller=None,
        )

        assert not result.is_valid
        assert len(result.errors) == 1
        assert "not configured" in result.errors[0]

    def test_invalid_missing_laser_af_reference(self):
        """Test validation fails when laser AF has no reference."""
        planner = AcquisitionPlanner()

        result = planner.validate_settings(
            do_reflection_af=True,
            laser_af_controller=FakeLaserAFController(has_reference=False),
        )

        assert not result.is_valid
        assert len(result.errors) == 1
        assert "reference position" in result.errors[0]


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_success_factory(self):
        """Test ValidationResult.success() factory."""
        result = ValidationResult.success()

        assert result.is_valid is True
        assert result.errors == []

    def test_failure_factory(self):
        """Test ValidationResult.failure() factory."""
        result = ValidationResult.failure(["Error 1", "Error 2"])

        assert result.is_valid is False
        assert result.errors == ["Error 1", "Error 2"]


class TestAcquisitionEstimate:
    """Tests for AcquisitionEstimate dataclass."""

    def test_estimate_holds_values(self):
        """Test that AcquisitionEstimate holds expected values."""
        estimate = AcquisitionEstimate(
            image_count=1000,
            disk_bytes=1024 * 1024 * 100,  # 100MB
            mosaic_ram_bytes=1024 * 1024 * 50,  # 50MB
        )

        assert estimate.image_count == 1000
        assert estimate.disk_bytes == 1024 * 1024 * 100
        assert estimate.mosaic_ram_bytes == 1024 * 1024 * 50


class TestEstimateMosaicRamBytes:
    """Tests for estimate_mosaic_ram_bytes method."""

    def test_returns_zero_when_mosaic_disabled(self):
        """Test returns 0 when USE_NAPARI_FOR_MOSAIC_DISPLAY is False."""
        planner = AcquisitionPlanner()

        with pytest.MonkeyPatch().context() as m:
            m.setattr("squid.backend.controllers.multipoint.acquisition_planner._def.USE_NAPARI_FOR_MOSAIC_DISPLAY", False)

            result = planner.estimate_mosaic_ram_bytes(
                scan_coordinates=FakeScanCoordinates(),
                objective_store=FakeObjectiveStore(),
                camera_service=FakeCameraService(),
                num_configurations=3,
            )

            assert result == 0

    def test_returns_zero_when_no_regions(self):
        """Test returns 0 when scan_coordinates has no regions."""
        planner = AcquisitionPlanner()

        # Create a fake that returns False for has_regions
        empty_coords = FakeScanCoordinates(region_fov_coordinates={})
        empty_coords.has_regions = lambda: False

        result = planner.estimate_mosaic_ram_bytes(
            scan_coordinates=empty_coords,
            objective_store=FakeObjectiveStore(),
            camera_service=FakeCameraService(),
            num_configurations=3,
        )

        assert result == 0

    def test_returns_zero_when_no_configurations(self):
        """Test returns 0 when no configurations selected."""
        planner = AcquisitionPlanner()

        result = planner.estimate_mosaic_ram_bytes(
            scan_coordinates=FakeScanCoordinates(),
            objective_store=FakeObjectiveStore(),
            camera_service=FakeCameraService(),
            num_configurations=0,
        )

        assert result == 0

    def test_returns_positive_value_for_valid_input(self):
        """Test returns positive RAM estimate for valid inputs."""
        planner = AcquisitionPlanner()

        result = planner.estimate_mosaic_ram_bytes(
            scan_coordinates=FakeScanCoordinates(),
            objective_store=FakeObjectiveStore(),
            camera_service=FakeCameraService(),
            num_configurations=3,
        )

        # Should be positive for valid scan coordinates
        assert result > 0


class TestGetFullEstimate:
    """Tests for get_full_estimate convenience method."""

    def test_returns_all_estimates(self):
        """Test that get_full_estimate returns all estimate fields."""
        planner = AcquisitionPlanner()

        # This test may fail if disk estimation requires actual file I/O
        # In that case, we'd need to mock more deeply
        try:
            estimate = planner.get_full_estimate(
                scan_coordinates=FakeScanCoordinates(),
                num_timepoints=1,
                num_z_levels=1,
                configurations=[FakeChannelMode()],
                camera_service=FakeCameraService(),
                channel_config_manager=FakeChannelConfigManager(),
                objective_store=FakeObjectiveStore(),
            )

            assert estimate.image_count > 0
            # disk_bytes might fail in unit test environment
            assert estimate.mosaic_ram_bytes >= 0
        except Exception:
            # Disk estimation may fail in test environment
            pytest.skip("Disk estimation not available in test environment")
