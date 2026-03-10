"""Unit tests for PositionController and ZStackExecutor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, call, patch
import time

import pytest

from squid.backend.controllers.multipoint.position_zstack import (
    PositionController,
    ZStackConfig,
    ZStackExecutor,
)


@dataclass
class FakePos:
    """Fake position for testing."""

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0


class FakeStageService:
    """Fake StageService for testing."""

    def __init__(self):
        self._x_mm = 0.0
        self._y_mm = 0.0
        self._z_mm = 0.0
        self.move_calls = []

    def move_x_to(self, x_mm: float) -> None:
        self.move_calls.append(("move_x_to", x_mm))
        self._x_mm = x_mm

    def move_y_to(self, y_mm: float) -> None:
        self.move_calls.append(("move_y_to", y_mm))
        self._y_mm = y_mm

    def move_z_to(self, z_mm: float) -> None:
        self.move_calls.append(("move_z_to", z_mm))
        self._z_mm = z_mm

    def move_z(self, delta_mm: float) -> None:
        self.move_calls.append(("move_z", delta_mm))
        self._z_mm += delta_mm

    def wait_for_idle(self) -> None:
        pass

    def get_position(self) -> FakePos:
        return FakePos(x_mm=self._x_mm, y_mm=self._y_mm, z_mm=self._z_mm)


class FakePiezoService:
    """Fake PiezoService for testing."""

    def __init__(self, position_um: float = 0.0):
        self._position_um = position_um
        self.move_calls = []

    def move_to(self, position_um: float) -> None:
        self.move_calls.append(position_um)
        self._position_um = position_um

    def get_position(self) -> float:
        return self._position_um


class TestPositionController:
    """Tests for PositionController class."""

    def test_init(self):
        """Test PositionController initialization."""
        stage = FakeStageService()
        controller = PositionController(stage)

        assert controller._stage == stage

    def test_move_to_coordinate_x_only(self):
        """Test moving to X coordinate only."""
        stage = FakeStageService()
        controller = PositionController(
            stage, stabilization_time_x_ms=0, stabilization_time_y_ms=0
        )

        controller.move_to_coordinate(x_mm=5.0)

        assert stage._x_mm == 5.0
        assert ("move_x_to", 5.0) in stage.move_calls

    def test_move_to_coordinate_y_only(self):
        """Test moving to Y coordinate only."""
        stage = FakeStageService()
        controller = PositionController(
            stage, stabilization_time_x_ms=0, stabilization_time_y_ms=0
        )

        controller.move_to_coordinate(y_mm=3.0)

        assert stage._y_mm == 3.0
        assert ("move_y_to", 3.0) in stage.move_calls

    def test_move_to_coordinate_xy(self):
        """Test moving to XY coordinate."""
        stage = FakeStageService()
        controller = PositionController(
            stage, stabilization_time_x_ms=0, stabilization_time_y_ms=0
        )

        controller.move_to_coordinate(x_mm=5.0, y_mm=3.0)

        assert stage._x_mm == 5.0
        assert stage._y_mm == 3.0

    def test_move_to_coordinate_xyz(self):
        """Test moving to XYZ coordinate."""
        stage = FakeStageService()
        controller = PositionController(
            stage,
            stabilization_time_x_ms=0,
            stabilization_time_y_ms=0,
            stabilization_time_z_ms=0,
        )

        controller.move_to_coordinate(x_mm=5.0, y_mm=3.0, z_mm=0.05)

        assert stage._x_mm == 5.0
        assert stage._y_mm == 3.0
        assert stage._z_mm == 0.05

    def test_move_to_z(self):
        """Test moving to absolute Z position."""
        stage = FakeStageService()
        controller = PositionController(stage, stabilization_time_z_ms=0)

        controller.move_to_z(z_mm=0.05)

        assert stage._z_mm == 0.05
        assert ("move_z_to", 0.05) in stage.move_calls

    def test_move_z_relative(self):
        """Test relative Z movement."""
        stage = FakeStageService()
        stage._z_mm = 0.05
        controller = PositionController(stage, stabilization_time_z_ms=0)

        controller.move_z_relative(delta_mm=0.01)

        assert stage._z_mm == pytest.approx(0.06)
        assert ("move_z", 0.01) in stage.move_calls

    def test_get_position(self):
        """Test getting current position."""
        stage = FakeStageService()
        stage._x_mm = 1.0
        stage._y_mm = 2.0
        stage._z_mm = 0.05
        controller = PositionController(stage)

        pos = controller.get_position()

        assert pos.x_mm == 1.0
        assert pos.y_mm == 2.0
        assert pos.z_mm == 0.05


class TestZStackConfig:
    """Tests for ZStackConfig dataclass."""

    def test_default_values(self):
        """Test ZStackConfig default values."""
        config = ZStackConfig(
            num_z_levels=10,
            delta_z_um=1.0,
            z_range=(0.04, 0.05),
        )

        assert config.num_z_levels == 10
        assert config.delta_z_um == 1.0
        assert config.z_range == (0.04, 0.05)
        assert config.stacking_direction == "FROM BOTTOM"
        assert config.use_piezo is False


class TestZStackExecutor:
    """Tests for ZStackExecutor class."""

    def test_init(self):
        """Test ZStackExecutor initialization."""
        stage = FakeStageService()
        piezo = FakePiezoService()
        config = ZStackConfig(
            num_z_levels=10,
            delta_z_um=1.0,
            z_range=(0.04, 0.05),
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=piezo)

        assert executor.num_z_levels == 10
        assert executor.delta_z_um == 1.0

    def test_initialize_records_piezo_home(self):
        """initialize() should NOT call stage.move_z_to, and should record piezo position as home."""
        stage = FakeStageService()
        piezo = FakePiezoService(position_um=100.0)
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            stacking_direction="FROM BOTTOM",
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=piezo, stabilization_time_z_ms=0)

        executor.initialize()

        # Should NOT touch stage Z
        assert not any(call[0] in ("move_z_to", "move_z") for call in stage.move_calls)
        # Should record piezo home position
        assert executor._z_home_um == 100.0
        assert executor._z_piezo_um == 100.0
        assert executor.current_z_level == 0

    def test_initialize_from_top_sets_negative_delta(self):
        """initialize() with FROM TOP should set negative delta."""
        stage = FakeStageService()
        piezo = FakePiezoService(position_um=50.0)
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            stacking_direction="FROM TOP",
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=piezo, stabilization_time_z_ms=0)

        executor.initialize()

        assert executor._delta_z_mm < 0
        assert executor._z_home_um == 50.0

    def test_step_only_uses_piezo(self):
        """step() should move piezo by delta_z_um and never touch stage."""
        stage = FakeStageService()
        piezo = FakePiezoService(position_um=100.0)
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            stacking_direction="FROM BOTTOM",
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=piezo, piezo_delay_ms=0, stabilization_time_z_ms=0)
        executor.initialize()
        stage.move_calls.clear()
        piezo.move_calls.clear()

        executor.step()

        # Piezo should move by +2.0 um
        assert piezo.move_calls == [102.0]
        assert executor.z_piezo_um == 102.0
        assert executor.current_z_level == 1
        # Stage should NOT be touched
        assert len(stage.move_calls) == 0

    def test_return_to_start_restores_home(self):
        """return_to_start() should move piezo back to the home recorded during initialize()."""
        stage = FakeStageService()
        piezo = FakePiezoService(position_um=100.0)
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            stacking_direction="FROM BOTTOM",
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=piezo, piezo_delay_ms=0, stabilization_time_z_ms=0)
        executor.initialize()

        # Step through all levels
        for _ in range(4):
            executor.step()

        assert executor.z_piezo_um == pytest.approx(108.0)  # 100 + 4*2
        piezo.move_calls.clear()

        executor.return_to_start()

        # Should return to home (100.0), not arithmetic reversal
        assert executor.z_piezo_um == pytest.approx(100.0)
        assert piezo.move_calls == [100.0]
        assert executor.current_z_level == 0

    def test_return_to_start_after_from_center_offset(self):
        """FROM CENTER: piezo at 140um, initialize, step, return — should go back to 140."""
        stage = FakeStageService()
        piezo = FakePiezoService(position_um=140.0)
        config = ZStackConfig(
            num_z_levels=3,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            stacking_direction="FROM BOTTOM",
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=piezo, piezo_delay_ms=0, stabilization_time_z_ms=0)
        executor.initialize()

        assert executor._z_home_um == 140.0

        # Step through
        for _ in range(2):
            executor.step()

        assert executor.z_piezo_um == pytest.approx(144.0)

        executor.return_to_start()

        assert executor.z_piezo_um == pytest.approx(140.0)
        assert executor.current_z_level == 0

    def test_step_with_piezo(self):
        """Test z-stack step using piezo."""
        stage = FakeStageService()
        piezo = FakePiezoService()
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            use_piezo=True,
        )
        executor = ZStackExecutor(
            stage, config, piezo_service=piezo, piezo_delay_ms=0
        )
        executor.initialize()
        piezo.move_calls.clear()

        executor.step()

        assert 2.0 in piezo.move_calls  # Should move piezo by 2um
        assert executor.z_piezo_um == 2.0

    def test_return_to_start_piezo(self):
        """Test returning to start position with piezo."""
        stage = FakeStageService()
        piezo = FakePiezoService()
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            use_piezo=True,
        )
        executor = ZStackExecutor(
            stage, config, piezo_service=piezo, piezo_delay_ms=0
        )
        executor.initialize()

        # Simulate stepping through z-stack
        for _ in range(4):
            executor.step()

        piezo.move_calls.clear()
        executor.return_to_start()

        # Piezo should return to start (0)
        assert executor.z_piezo_um == 0.0

    def test_reset_piezo(self):
        """Test resetting piezo position."""
        stage = FakeStageService()
        piezo = FakePiezoService()
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=piezo)

        executor.reset_piezo(initial_position_um=10.0)

        assert executor.z_piezo_um == 10.0
        assert 10.0 in piezo.move_calls

    def test_use_piezo_returns_false_without_service(self):
        """Test use_piezo returns False when no piezo service."""
        stage = FakeStageService()
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            use_piezo=True,  # Config says use piezo
        )
        executor = ZStackExecutor(stage, config, piezo_service=None)

        # Should return False because piezo_service is None
        assert executor.use_piezo is False

    def test_step_raises_without_piezo(self):
        """step() should raise RuntimeError when no piezo service."""
        stage = FakeStageService()
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=None)
        executor.initialize()

        with pytest.raises(RuntimeError, match="Piezo service required"):
            executor.step()

    def test_return_to_start_raises_without_piezo(self):
        """return_to_start() should raise RuntimeError when no piezo service."""
        stage = FakeStageService()
        config = ZStackConfig(
            num_z_levels=5,
            delta_z_um=2.0,
            z_range=(0.04, 0.05),
            use_piezo=True,
        )
        executor = ZStackExecutor(stage, config, piezo_service=None)
        executor.initialize()

        with pytest.raises(RuntimeError, match="Piezo service required"):
            executor.return_to_start()
