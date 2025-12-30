"""Tests for MultiPointController RAM estimation for mosaic view."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

import _def
from squid.core.events import EventBus
from squid.backend.controllers.multipoint.multi_point_controller import MultiPointController


# --- Fake/Stub Classes for Testing ---


class _FakeStageService:
    def __init__(self) -> None:
        pass

    def get_pixel_size_binned_um(self) -> float:
        return 1.0


class _FakeCameraService:
    def __init__(self) -> None:
        self._callbacks_enabled = False

    def get_callbacks_enabled(self) -> bool:
        return self._callbacks_enabled

    def enable_callbacks(self, enabled: bool) -> None:
        self._callbacks_enabled = enabled

    def stop_streaming(self) -> None:
        return None

    def get_pixel_size_binned_um(self) -> float:
        return 1.0


class _FakePeripheralService:
    pass


class _FakeLiveController:
    def __init__(self) -> None:
        self.is_live = False
        self.currentConfiguration = object()
        self.trigger_mode = None
        self.enable_channel_auto_filter_switching = False

    def stop_live(self) -> None:
        self.is_live = False

    def start_live(self) -> None:
        self.is_live = True

    def set_microscope_mode(self, _mode) -> None:
        return None


class _FakeAutoFocusController:
    def __init__(self) -> None:
        self.use_focus_map = False
        self.focus_map_coords = []

    def gen_focus_map(self, _coord1, _coord2, _coord3) -> None:
        pass

    def set_focus_map_use(self, enabled: bool) -> None:
        self.use_focus_map = enabled

    def clear_focus_map(self) -> None:
        self.focus_map_coords = []


class _FakeScanCoordinates:
    def __init__(self, has_regions_val: bool = False, bounds: Optional[Dict] = None) -> None:
        self.region_centers = {}
        self.region_fov_coordinates = {}
        self.objectiveStore = object()
        self.stage = object()
        self.camera = object()
        self._has_regions_val = has_regions_val
        self._bounds = bounds

    def has_regions(self) -> bool:
        return self._has_regions_val

    def get_scan_bounds(self) -> Optional[Dict[str, Tuple[float, float]]]:
        return self._bounds


class _FakeObjectiveStore:
    current_objective = "10x"
    objectives_dict = {}

    def get_pixel_size_factor(self) -> float:
        return 1.0


class _FakeChannelConfigurationManager:
    def write_configuration_selected(self, *_args, **_kwargs) -> None:
        return None


class _FakeChannelConfig:
    """Minimal channel configuration stub."""

    def __init__(self, name: str) -> None:
        self.name = name


def _create_test_controller(
    bus: EventBus,
    scan_coordinates: Optional[_FakeScanCoordinates] = None,
    selected_configurations: Optional[List] = None,
) -> MultiPointController:
    """Create a MultiPointController with fake dependencies for testing."""
    controller = MultiPointController(
        live_controller=_FakeLiveController(),
        autofocus_controller=_FakeAutoFocusController(),
        objective_store=_FakeObjectiveStore(),
        channel_configuration_manager=_FakeChannelConfigurationManager(),
        camera_service=_FakeCameraService(),
        stage_service=_FakeStageService(),
        peripheral_service=_FakePeripheralService(),
        event_bus=bus,
        scan_coordinates=scan_coordinates or _FakeScanCoordinates(),
    )
    if selected_configurations is not None:
        controller.selected_configurations = selected_configurations
    return controller


class TestMosaicRamEstimation:
    """Test suite for get_estimated_mosaic_ram_bytes method."""

    def test_returns_zero_when_napari_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that RAM estimation returns 0 when napari mosaic display is disabled."""
        monkeypatch.setattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY", False)

        bus = EventBus()
        bus.start()
        try:
            # Set up scan coordinates with regions and bounds
            bounds = {"x": (0.0, 10.0), "y": (0.0, 10.0)}
            scan_coords = _FakeScanCoordinates(has_regions_val=True, bounds=bounds)
            controller = _create_test_controller(
                bus,
                scan_coordinates=scan_coords,
                selected_configurations=[_FakeChannelConfig("BF")],
            )

            # Should return 0 when napari mosaic display is disabled
            assert controller.get_estimated_mosaic_ram_bytes() == 0
        finally:
            bus.stop()

    def test_returns_zero_when_no_regions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that RAM estimation returns 0 when no scan regions exist."""
        monkeypatch.setattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY", True)

        bus = EventBus()
        bus.start()
        try:
            # Scan coordinates with no regions
            scan_coords = _FakeScanCoordinates(has_regions_val=False)
            controller = _create_test_controller(
                bus,
                scan_coordinates=scan_coords,
                selected_configurations=[_FakeChannelConfig("BF")],
            )

            assert controller.get_estimated_mosaic_ram_bytes() == 0
        finally:
            bus.stop()

    def test_returns_zero_when_no_bounds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that RAM estimation returns 0 when scan bounds are None."""
        monkeypatch.setattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY", True)

        bus = EventBus()
        bus.start()
        try:
            # Scan coordinates with regions but no bounds
            scan_coords = _FakeScanCoordinates(has_regions_val=True, bounds=None)
            controller = _create_test_controller(
                bus,
                scan_coordinates=scan_coords,
                selected_configurations=[_FakeChannelConfig("BF")],
            )

            assert controller.get_estimated_mosaic_ram_bytes() == 0
        finally:
            bus.stop()

    def test_returns_zero_when_no_channels_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that RAM estimation returns 0 when no channels are selected."""
        monkeypatch.setattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY", True)

        bus = EventBus()
        bus.start()
        try:
            bounds = {"x": (0.0, 10.0), "y": (0.0, 10.0)}
            scan_coords = _FakeScanCoordinates(has_regions_val=True, bounds=bounds)
            controller = _create_test_controller(
                bus,
                scan_coordinates=scan_coords,
                selected_configurations=[],  # No channels selected
            )

            assert controller.get_estimated_mosaic_ram_bytes() == 0
        finally:
            bus.stop()

    def test_returns_positive_estimate_with_valid_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that RAM estimation returns positive value with valid configuration."""
        monkeypatch.setattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY", True)
        monkeypatch.setattr(_def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 2)

        bus = EventBus()
        bus.start()
        try:
            # 10mm x 10mm scan area
            bounds = {"x": (0.0, 10.0), "y": (0.0, 10.0)}
            scan_coords = _FakeScanCoordinates(has_regions_val=True, bounds=bounds)
            controller = _create_test_controller(
                bus,
                scan_coordinates=scan_coords,
                selected_configurations=[_FakeChannelConfig("BF")],
            )

            ram_estimate = controller.get_estimated_mosaic_ram_bytes()
            assert ram_estimate > 0, f"Expected RAM > 0, got {ram_estimate}"
        finally:
            bus.stop()

    def test_ram_scales_with_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that RAM estimation scales with number of channels."""
        monkeypatch.setattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY", True)
        monkeypatch.setattr(_def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 2)

        bus = EventBus()
        bus.start()
        try:
            bounds = {"x": (0.0, 10.0), "y": (0.0, 10.0)}
            scan_coords = _FakeScanCoordinates(has_regions_val=True, bounds=bounds)

            # One channel
            controller_1ch = _create_test_controller(
                bus,
                scan_coordinates=scan_coords,
                selected_configurations=[_FakeChannelConfig("BF")],
            )
            ram_one_channel = controller_1ch.get_estimated_mosaic_ram_bytes()

            # Three channels
            controller_3ch = _create_test_controller(
                bus,
                scan_coordinates=scan_coords,
                selected_configurations=[
                    _FakeChannelConfig("BF"),
                    _FakeChannelConfig("DAPI"),
                    _FakeChannelConfig("FITC"),
                ],
            )
            ram_three_channels = controller_3ch.get_estimated_mosaic_ram_bytes()

            assert ram_three_channels > ram_one_channel
            # RAM should scale roughly linearly with number of channels
            expected_ratio = 3.0
            actual_ratio = ram_three_channels / ram_one_channel
            assert abs(actual_ratio - expected_ratio) < 0.1, (
                f"Expected ratio ~{expected_ratio}, got {actual_ratio}"
            )
        finally:
            bus.stop()

    def test_ram_scales_with_area(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that RAM estimation scales with scan area."""
        monkeypatch.setattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY", True)
        monkeypatch.setattr(_def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM", 2)

        bus = EventBus()
        bus.start()
        try:
            # Small area: 5mm x 5mm
            bounds_small = {"x": (0.0, 5.0), "y": (0.0, 5.0)}
            scan_coords_small = _FakeScanCoordinates(has_regions_val=True, bounds=bounds_small)
            controller_small = _create_test_controller(
                bus,
                scan_coordinates=scan_coords_small,
                selected_configurations=[_FakeChannelConfig("BF")],
            )
            ram_small = controller_small.get_estimated_mosaic_ram_bytes()

            # Large area: 10mm x 10mm (4x the area)
            bounds_large = {"x": (0.0, 10.0), "y": (0.0, 10.0)}
            scan_coords_large = _FakeScanCoordinates(has_regions_val=True, bounds=bounds_large)
            controller_large = _create_test_controller(
                bus,
                scan_coordinates=scan_coords_large,
                selected_configurations=[_FakeChannelConfig("BF")],
            )
            ram_large = controller_large.get_estimated_mosaic_ram_bytes()

            assert ram_large > ram_small
            # RAM should scale roughly with area (4x area = ~4x RAM)
            expected_ratio = 4.0
            actual_ratio = ram_large / ram_small
            # Allow some tolerance for rounding
            assert 3.5 < actual_ratio < 4.5, (
                f"Expected ratio ~{expected_ratio}, got {actual_ratio}"
            )
        finally:
            bus.stop()
