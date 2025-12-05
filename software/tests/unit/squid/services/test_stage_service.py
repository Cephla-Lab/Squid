# tests/squid/services/test_stage_service.py
"""Tests for StageService."""
import pytest
from unittest.mock import Mock, MagicMock
from dataclasses import dataclass


@dataclass
class MockPos:
    """Mock position for testing."""
    x_mm: float
    y_mm: float
    z_mm: float


class TestStageService:
    """Test suite for StageService."""

    def test_move_x_calls_stage(self):
        """move_x should call stage.move_x."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_x(1.5)

        mock_stage.move_x.assert_called_once_with(1.5, True)

    def test_move_y_calls_stage(self):
        """move_y should call stage.move_y."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_y(2.5)

        mock_stage.move_y.assert_called_once_with(2.5, True)

    def test_move_z_calls_stage(self):
        """move_z should call stage.move_z."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_z(0.1)

        mock_stage.move_z.assert_called_once_with(0.1, True)

    def test_move_publishes_position(self):
        """move_x should publish StagePositionChanged."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus, StagePositionChanged

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(10.0, 20.0, 30.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)

        received = []
        bus.subscribe(StagePositionChanged, lambda e: received.append(e))

        service.move_x(1.0)

        assert len(received) == 1
        assert received[0].x_mm == 10.0
        assert received[0].y_mm == 20.0
        assert received[0].z_mm == 30.0

    def test_handles_move_command(self):
        """Should respond to MoveStageCommand events."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus, MoveStageCommand

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)

        bus.publish(MoveStageCommand(axis='x', distance_mm=5.0))

        mock_stage.move_x.assert_called_once_with(5.0, True)

    def test_move_to_calls_stage(self):
        """move_to should call stage.move_x_to/move_y_to/move_z_to."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.move_to(x_mm=10.0, y_mm=20.0, z_mm=5.0)

        mock_stage.move_x_to.assert_called_once_with(10.0, True)
        mock_stage.move_y_to.assert_called_once_with(20.0, True)
        mock_stage.move_z_to.assert_called_once_with(5.0, True)

    def test_home_calls_stage(self):
        """home should call stage.home."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(0.0, 0.0, 0.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        service.home(x=True, y=True, z=False)

        mock_stage.home.assert_called_once_with(True, True, False)

    def test_get_position(self):
        """get_position should return stage position."""
        from squid.services.stage_service import StageService
        from squid.events import EventBus

        mock_stage = Mock()
        mock_stage.get_pos.return_value = MockPos(1.0, 2.0, 3.0)
        bus = EventBus()

        service = StageService(mock_stage, bus)
        pos = service.get_position()

        assert pos.x_mm == 1.0
        assert pos.y_mm == 2.0
        assert pos.z_mm == 3.0
