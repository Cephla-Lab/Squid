"""Unit tests for FocusMapGenerator and AutofocusExecutor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from squid.backend.controllers.multipoint.focus_operations import (
    FocusMapConfig,
    FocusMapState,
    FocusMapGenerator,
    AutofocusExecutor,
)


class FakeStageService:
    """Fake StageService for testing."""

    def __init__(self):
        self.move_calls = []

    def move_x_to(self, x_mm: float) -> None:
        self.move_calls.append(("x", x_mm))

    def move_y_to(self, y_mm: float) -> None:
        self.move_calls.append(("y", y_mm))


class FakeAutofocusController:
    """Fake AutoFocusController for testing."""

    def __init__(self):
        self.focus_map_coords: List[Tuple[float, float, float]] = []
        self.use_focus_map: bool = False
        self.gen_focus_map_calls = []
        self.autofocus_called = False
        self.autofocus_completed = True
        self.cleared = False

    def gen_focus_map(
        self,
        coord1: Tuple[float, float],
        coord2: Tuple[float, float],
        coord3: Tuple[float, float],
    ) -> None:
        self.gen_focus_map_calls.append((coord1, coord2, coord3))

    def set_focus_map_use(self, use: bool) -> None:
        self.use_focus_map = use

    def clear_focus_map(self) -> None:
        self.focus_map_coords.clear()
        self.cleared = True

    def autofocus(self) -> None:
        self.autofocus_called = True

    def wait_till_autofocus_has_completed(self, timeout_s: Optional[float] = None) -> bool:
        return self.autofocus_completed


class FakeLaserAFController:
    """Fake LaserAutofocusController for testing."""

    def __init__(self):
        self.move_to_target_calls = []
        self.should_fail = False

    def move_to_target(self, target: float) -> None:
        if self.should_fail:
            raise Exception("Laser AF failed")
        self.move_to_target_calls.append(target)


class FakeFocusLockController:
    """Fake FocusLockController for testing."""

    def __init__(self):
        self.mode = "off"
        self.is_running = False
        self.wait_result = True

    def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
        return self.wait_result


class FakeScanCoordinates:
    """Fake ScanCoordinates for testing."""

    def __init__(self):
        self.region_fov_coordinates: Dict[str, List[Tuple[float, ...]]] = {
            "region_0": [(1.0, 2.0), (1.5, 2.0)],
            "region_1": [(3.0, 4.0)],
        }
        self.updated_z_levels = []

    def update_fov_z_level(self, region_id: str, fov: int, z: float) -> None:
        self.updated_z_levels.append((region_id, fov, z))


class FakeFocusMap:
    """Fake focus map for testing."""

    def __init__(self, z_value: float = 0.05):
        self.z_value = z_value

    def interpolate(self, x: float, y: float, region_id: str) -> float:
        return self.z_value


class FakeChannelConfigManager:
    """Fake ChannelConfigurationManager for testing."""

    def __init__(self):
        self.returned_config = MagicMock()

    def get_channel_configuration_by_name(
        self, objective: str, channel_name: str
    ) -> Any:
        return self.returned_config


class FakeObjectiveStore:
    """Fake ObjectiveStore for testing."""

    def __init__(self):
        self.current_objective = "10x"


class TestFocusMapConfig:
    """Tests for FocusMapConfig dataclass."""

    def test_defaults(self):
        """Test FocusMapConfig default values."""
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        assert config.delta_x_mm == 1.0
        assert config.delta_y_mm == 1.0
        assert config.max_grid_points == 4
        assert config.min_grid_points == 2


class TestFocusMapState:
    """Tests for FocusMapState dataclass."""

    def test_holds_state(self):
        """Test FocusMapState holds correct values."""
        state = FocusMapState(
            coords=[(1.0, 2.0, 0.05), (3.0, 4.0, 0.06)],
            use_focus_map=True,
        )

        assert len(state.coords) == 2
        assert state.use_focus_map is True


class TestFocusMapGenerator:
    """Tests for FocusMapGenerator class."""

    def test_init(self):
        """Test FocusMapGenerator initialization."""
        af = FakeAutofocusController()
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)

        assert generator._autofocus == af
        assert generator._stage == stage
        assert generator._config == config

    def test_focus_map_context_saves_and_restores(self):
        """Test that context manager saves and restores focus map state."""
        af = FakeAutofocusController()
        af.focus_map_coords = [(1.0, 2.0, 0.05)]
        af.use_focus_map = True
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)

        with generator.focus_map_context():
            # Modify focus map
            af.focus_map_coords.clear()
            af.focus_map_coords.append((5.0, 6.0, 0.1))
            af.use_focus_map = False

        # State should be restored
        assert len(af.focus_map_coords) == 1
        assert af.focus_map_coords[0] == (1.0, 2.0, 0.05)
        assert af.use_focus_map is True

    def test_generate_from_bounds(self):
        """Test generating focus map from scan bounds."""
        af = FakeAutofocusController()
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)

        bounds = {"x": (0.0, 4.0), "y": (0.0, 3.0)}
        result = generator.generate_from_bounds(bounds)

        assert result is True
        assert len(af.gen_focus_map_calls) == 1
        assert af.use_focus_map is True

    def test_generate_from_bounds_returns_to_center(self):
        """Test that generate_from_bounds returns stage to center."""
        af = FakeAutofocusController()
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)

        bounds = {"x": (0.0, 4.0), "y": (0.0, 2.0)}
        generator.generate_from_bounds(bounds)

        # Should move to center (2.0, 1.0)
        assert ("x", 2.0) in stage.move_calls
        assert ("y", 1.0) in stage.move_calls

    def test_generate_from_bounds_empty_returns_false(self):
        """Test that empty bounds returns False."""
        af = FakeAutofocusController()
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)

        result = generator.generate_from_bounds({})

        assert result is False
        assert len(af.gen_focus_map_calls) == 0

    def test_interpolate_z_positions(self):
        """Test interpolating z-positions using focus surface."""
        af = FakeAutofocusController()
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)

        scan_coords = FakeScanCoordinates()
        focus_map = FakeFocusMap(z_value=0.07)

        generator.interpolate_z_positions(scan_coords, focus_map)

        # All FOVs should have updated z-levels
        assert len(scan_coords.updated_z_levels) == 3
        for region_id, fov, z in scan_coords.updated_z_levels:
            assert z == 0.07

    def test_interpolate_z_positions_none_focus_map(self):
        """Test that None focus map does nothing."""
        af = FakeAutofocusController()
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)

        scan_coords = FakeScanCoordinates()
        generator.interpolate_z_positions(scan_coords, None)

        assert len(scan_coords.updated_z_levels) == 0

    def test_clear_focus_map(self):
        """Test clearing focus map."""
        af = FakeAutofocusController()
        af.focus_map_coords = [(1.0, 2.0, 0.05)]
        stage = FakeStageService()
        config = FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0)

        generator = FocusMapGenerator(af, stage, config)
        generator.clear_focus_map()

        assert af.cleared is True


class TestAutofocusExecutor:
    """Tests for AutofocusExecutor class."""

    def test_init(self):
        """Test AutofocusExecutor initialization."""
        executor = AutofocusExecutor()

        assert executor._do_autofocus is False
        assert executor._do_reflection_af is False
        assert executor._nz == 1

    def test_configure(self):
        """Test configuring autofocus behavior."""
        executor = AutofocusExecutor()

        executor.configure(
            do_autofocus=True,
            do_reflection_af=False,
            nz=5,
            z_stacking_config="FROM CENTER",
            fovs_per_af=10,
        )

        assert executor._do_autofocus is True
        assert executor._do_reflection_af is False
        assert executor._nz == 5
        assert executor._z_stacking_config == "FROM CENTER"
        assert executor._fovs_per_af == 10

    def test_should_perform_autofocus_reflection_af(self):
        """Test that reflection AF always returns True."""
        executor = AutofocusExecutor()
        executor.configure(do_reflection_af=True)

        assert executor.should_perform_autofocus() is True

    def test_should_perform_autofocus_contrast_af(self):
        """Test contrast AF with single z-level."""
        executor = AutofocusExecutor()
        executor.configure(do_autofocus=True, nz=1)

        assert executor.should_perform_autofocus() is True

    def test_should_perform_autofocus_z_stack_from_center(self):
        """Test contrast AF with z-stack from center."""
        executor = AutofocusExecutor()
        executor.configure(
            do_autofocus=True, nz=5, z_stacking_config="FROM CENTER"
        )

        assert executor.should_perform_autofocus() is True

    def test_should_perform_autofocus_z_stack_from_bottom(self):
        """Test contrast AF with z-stack from bottom returns False."""
        executor = AutofocusExecutor()
        executor.configure(
            do_autofocus=True, nz=5, z_stacking_config="FROM BOTTOM"
        )

        assert executor.should_perform_autofocus() is False

    def test_should_perform_autofocus_fov_interval(self):
        """Test AF respects FOV interval."""
        executor = AutofocusExecutor()
        executor.configure(do_autofocus=True, nz=1, fovs_per_af=3)

        # FOV 0 should trigger AF
        executor.af_fov_count = 0
        assert executor.should_perform_autofocus() is True

        # FOV 1 should not
        executor.af_fov_count = 1
        assert executor.should_perform_autofocus() is False

        # FOV 3 should trigger AF
        executor.af_fov_count = 3
        assert executor.should_perform_autofocus() is True

    def test_perform_contrast_autofocus(self):
        """Test performing contrast-based autofocus."""
        af = FakeAutofocusController()
        executor = AutofocusExecutor(autofocus_controller=af)
        executor.configure(do_autofocus=True, nz=1)

        result = executor.perform_autofocus()

        assert result is True
        assert af.autofocus_called is True

    def test_perform_contrast_autofocus_timeout(self):
        """Test contrast autofocus timeout returns False."""
        af = FakeAutofocusController()
        af.autofocus_completed = False
        executor = AutofocusExecutor(autofocus_controller=af)
        executor.configure(do_autofocus=True, nz=1)

        result = executor.perform_autofocus(timeout_s=1.0)

        assert result is False

    def test_perform_laser_autofocus(self):
        """Test performing laser reflection autofocus."""
        laser_af = FakeLaserAFController()
        executor = AutofocusExecutor(laser_af_controller=laser_af)
        executor.configure(do_reflection_af=True)

        result = executor.perform_autofocus()

        assert result is True
        assert 0 in laser_af.move_to_target_calls

    def test_perform_laser_autofocus_failure(self):
        """Test laser AF failure returns False."""
        laser_af = FakeLaserAFController()
        laser_af.should_fail = True
        executor = AutofocusExecutor(laser_af_controller=laser_af)
        executor.configure(do_reflection_af=True)

        result = executor.perform_autofocus()

        assert result is False

    def test_perform_autofocus_with_focus_lock(self):
        """Test autofocus uses focus lock when active."""
        focus_lock = FakeFocusLockController()
        focus_lock.mode = "continuous"
        focus_lock.is_running = True
        focus_lock.wait_result = True

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)
        executor.configure(do_reflection_af=True)

        result = executor.perform_autofocus()

        assert result is True

    def test_increment_fov_count(self):
        """Test incrementing FOV counter."""
        executor = AutofocusExecutor()

        assert executor.af_fov_count == 0
        executor.increment_fov_count()
        assert executor.af_fov_count == 1
        executor.increment_fov_count()
        assert executor.af_fov_count == 2

    def test_reset_fov_count(self):
        """Test resetting FOV counter."""
        executor = AutofocusExecutor()
        executor.af_fov_count = 10

        executor.reset_fov_count()

        assert executor.af_fov_count == 0

    def test_apply_config_callback(self):
        """Test apply config callback is called for contrast AF."""
        af = FakeAutofocusController()
        channel_config = FakeChannelConfigManager()
        objectives = FakeObjectiveStore()
        callback_received = []

        def mock_callback(config):
            callback_received.append(config)

        executor = AutofocusExecutor(
            autofocus_controller=af,
            channel_config_manager=channel_config,
            objective_store=objectives,
        )
        executor.configure(do_autofocus=True, nz=1)
        executor.set_apply_config_callback(mock_callback)

        executor.perform_autofocus()

        assert len(callback_received) == 1
        assert callback_received[0] == channel_config.returned_config
