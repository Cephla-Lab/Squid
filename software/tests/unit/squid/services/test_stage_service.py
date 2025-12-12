# tests/squid/services/test_stage_service.py
"""Tests for StageService."""

import pytest
from unittest.mock import Mock
from dataclasses import dataclass


@dataclass
class MockPos:
    """Mock position for testing."""

    x_mm: float
    y_mm: float
    z_mm: float


@dataclass
class MockPosWithTheta:
    """Mock position including theta for testing."""

    x_mm: float
    y_mm: float
    z_mm: float
    theta_rad: float


class TestStageService:
    """Test suite for StageService."""

    def test_move_x_calls_stage(self):
        """move_x should call stage.move_x."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_x(1.5)

        mock_stage.move_x.assert_called_once_with(1.5, True)

    def test_move_y_calls_stage(self):
        """move_y should call stage.move_y."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_y(2.5)

        mock_stage.move_y.assert_called_once_with(2.5, True)

    def test_move_z_calls_stage(self):
        """move_z should call stage.move_z."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_z(0.1)

        mock_stage.move_z.assert_called_once_with(0.1, True)

    def test_move_publishes_position(self):
        """move_x should publish StagePositionChanged."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus, StagePositionChanged

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(10.0, 20.0, 30.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)

        received = []
        bus.subscribe(StagePositionChanged, lambda e: received.append(e))

        service.move_x(1.0)
        bus.drain()

        assert len(received) == 1
        assert received[0].x_mm == 10.0
        assert received[0].y_mm == 20.0
        assert received[0].z_mm == 30.0
        assert received[0].theta_rad is None

    def test_move_publishes_theta_when_available(self):
        """move_x should publish theta when present on Pos."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus, StagePositionChanged

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPosWithTheta(10.0, 20.0, 30.0, 1.23)
        bus = EventBus()

        service = StageService(mock_stage, bus)

        received = []
        bus.subscribe(StagePositionChanged, lambda e: received.append(e))

        service.move_x(1.0)
        bus.drain()

        assert len(received) == 1
        assert received[0].theta_rad == 1.23

    def test_handles_move_command(self):
        """Should respond to MoveStageCommand events."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus, MoveStageCommand

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        StageService(mock_stage, bus)

        bus.publish(MoveStageCommand(axis="x", distance_mm=5.0))
        bus.drain()

        mock_stage.move_x.assert_called_once_with(5.0, True)

    def test_handles_home_command(self):
        """Should respond to HomeStageCommand events."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus, HomeStageCommand

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        StageService(mock_stage, bus)

        bus.publish(HomeStageCommand(x=True, y=False, z=True, theta=True))
        bus.drain()

        mock_stage.home.assert_called_once_with(True, False, True, True)

    def test_handles_zero_command(self):
        """Should respond to ZeroStageCommand events."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus, ZeroStageCommand

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        StageService(mock_stage, bus)

        bus.publish(ZeroStageCommand(x=True, y=True, z=False, theta=True))
        bus.drain()

        mock_stage.zero.assert_called_once_with(True, True, False, True)

    def test_handles_move_to_loading_position_command(self):
        """Should respond to MoveStageToLoadingPositionCommand events."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import (
            EventBus,
            MoveStageToLoadingPositionCommand,
        )

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_to_loading_position = Mock()

        bus.publish(
            MoveStageToLoadingPositionCommand(
                blocking=False, callback=None, is_wellplate=False
            )
        )
        bus.drain()

        service.move_to_loading_position.assert_called_once_with(
            blocking=False, callback=None, is_wellplate=False
        )

    def test_handles_move_to_scanning_position_command(self):
        """Should respond to MoveStageToScanningPositionCommand events."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import (
            EventBus,
            MoveStageToScanningPositionCommand,
        )

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_to_scanning_position = Mock()

        bus.publish(
            MoveStageToScanningPositionCommand(
                blocking=False, callback=None, is_wellplate=True
            )
        )
        bus.drain()

        service.move_to_scanning_position.assert_called_once_with(
            blocking=False, callback=None, is_wellplate=True
        )

    def test_move_to_calls_stage(self):
        """move_to should call stage.move_x_to/move_y_to/move_z_to."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_to(x_mm=10.0, y_mm=20.0, z_mm=5.0)

        mock_stage.move_x_to.assert_called_once_with(10.0, True)
        mock_stage.move_y_to.assert_called_once_with(20.0, True)
        mock_stage.move_z_to.assert_called_once_with(5.0, True)

    def test_home_calls_stage(self):
        """home should call stage.home with all 4 axis params."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.home(x=True, y=True, z=False)

        # Should pass theta=False by default
        mock_stage.home.assert_called_once_with(True, True, False, False)

    def test_home_with_theta(self):
        """home should support theta axis."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.home(x=True, y=True, z=True, theta=True)

        mock_stage.home.assert_called_once_with(True, True, True, True)

    def test_zero_calls_stage(self):
        """zero should call stage.zero with all 4 axis params."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.zero(x=True, y=False, z=True)

        # Should pass theta=False by default
        mock_stage.zero.assert_called_once_with(True, False, True, False)

    def test_zero_with_theta(self):
        """zero should support theta axis."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.zero(x=True, y=True, z=True, theta=True)

        mock_stage.zero.assert_called_once_with(True, True, True, True)

    def test_get_position(self):
        """get_position should return stage position."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        pos = service.get_position()

        assert pos.x_mm == 1.0
        assert pos.y_mm == 2.0
        assert pos.z_mm == 3.0

    # ============================================================
    # Task 2.1: Theta axis methods
    # ============================================================

    def test_move_theta(self):
        """move_theta should call stage.move_theta."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_theta(0.5)

        mock_stage.move_theta.assert_called_once_with(0.5, True)

    def test_move_theta_to(self):
        """move_theta_to should call stage.move_theta_to."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_theta_to(1.57)

        mock_stage.move_theta_to.assert_called_once_with(1.57, True)

    # ============================================================
    # Task 2.2: get_config method
    # ============================================================

    def test_get_config(self):
        """get_config should return stage configuration."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_config = Mock()
        mock_stage.get_config.return_value = mock_config
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        config = service.get_config()

        assert config is mock_config
        mock_stage.get_config.assert_called_once()

    # ============================================================
    # Task 3A: Synchronization and positioning methods
    # ============================================================

    def test_wait_for_idle(self):
        """wait_for_idle should call stage.wait_for_idle."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.wait_for_idle(5.0)

        mock_stage.wait_for_idle.assert_called_once_with(5.0)

    def test_set_limits(self):
        """set_limits should call stage.set_limits."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.set_limits(
            x_pos_mm=10.0,
            x_neg_mm=-10.0,
            y_pos_mm=20.0,
            y_neg_mm=-20.0,
            z_pos_mm=30.0,
            z_neg_mm=-30.0,
        )

        mock_stage.set_limits.assert_called_once_with(
            x_pos_mm=10.0,
            x_neg_mm=-10.0,
            y_pos_mm=20.0,
            y_neg_mm=-20.0,
            z_pos_mm=30.0,
            z_neg_mm=-30.0,
        )

    def test_get_x_mm_per_ustep(self):
        """get_x_mm_per_ustep should return mm per microstep."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        mock_stage.x_mm_to_usteps.return_value = 1000.0  # 1mm = 1000 usteps
        bus = EventBus()

        service = StageService(mock_stage, bus)
        result = service.get_x_mm_per_ustep()

        assert result == 0.001  # 1/1000
        mock_stage.x_mm_to_usteps.assert_called_once_with(1.0)

    def test_get_y_mm_per_ustep(self):
        """get_y_mm_per_ustep should return mm per microstep."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        mock_stage.y_mm_to_usteps.return_value = 500.0  # 1mm = 500 usteps
        bus = EventBus()

        service = StageService(mock_stage, bus)
        result = service.get_y_mm_per_ustep()

        assert result == 0.002  # 1/500
        mock_stage.y_mm_to_usteps.assert_called_once_with(1.0)

    def test_get_z_mm_per_ustep(self):
        """get_z_mm_per_ustep should return mm per microstep."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        mock_stage.z_mm_to_usteps.return_value = 2000.0  # 1mm = 2000 usteps
        bus = EventBus()

        service = StageService(mock_stage, bus)
        result = service.get_z_mm_per_ustep()

        assert result == 0.0005  # 1/2000
        mock_stage.z_mm_to_usteps.assert_called_once_with(1.0)

    def test_move_to_safety_position(self):
        """move_to_safety_position should move Z to safety point."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus
        import _def as _def

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 1.2)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_to_safety_position()

        expected_z = int(_def.Z_HOME_SAFETY_POINT) / 1000.0
        mock_stage.move_z_to.assert_called_once_with(expected_z)

    def test_move_to_loading_position_blocking(self):
        """move_to_loading_position should move to loading position."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_config = Mock()
        mock_config.X_AXIS.MAX_POSITION = 100.0
        mock_config.X_AXIS.MIN_POSITION = -100.0
        mock_config.Y_AXIS.MAX_POSITION = 100.0
        mock_config.Y_AXIS.MIN_POSITION = -100.0
        mock_stage.get_config.return_value = mock_config
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 5.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        result = service.move_to_loading_position(blocking=True, is_wellplate=True)

        assert result is None
        # Verify move to loading position was called
        mock_stage.move_x_to.assert_called()
        mock_stage.move_y_to.assert_called()

    def test_move_to_loading_position_not_wellplate(self):
        """move_to_loading_position should work for non-wellplate."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 5.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        result = service.move_to_loading_position(blocking=True, is_wellplate=False)

        assert result is None
        # Should only call move_x_to and move_y_to once each (no retraction sequence)
        assert mock_stage.move_y_to.call_count == 1
        assert mock_stage.move_x_to.call_count == 1

    def test_move_to_loading_position_callback_error(self):
        """move_to_loading_position should raise if blocking=True with callback."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)

        with pytest.raises(ValueError, match="Callback not supported"):
            service.move_to_loading_position(blocking=True, callback=lambda *a: None)

    def test_move_to_scanning_position_blocking(self):
        """move_to_scanning_position should move to scanning position."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 5.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        result = service.move_to_scanning_position(blocking=True, is_wellplate=True)

        assert result is None
        mock_stage.move_x_to.assert_called()
        mock_stage.move_y_to.assert_called()

    def test_move_to_scanning_position_callback_error(self):
        """move_to_scanning_position should raise if blocking=True with callback."""
        from squid.mcs.services.stage_service import StageService
        from squid.core.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)

        with pytest.raises(ValueError, match="Callback not supported"):
            service.move_to_scanning_position(blocking=True, callback=lambda *a: None)
