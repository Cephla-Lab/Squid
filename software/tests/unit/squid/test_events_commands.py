# tests/squid/test_events_commands.py
"""Tests for command and state event types."""

from dataclasses import is_dataclass


class TestCommandEvents:
    """Test command event dataclasses."""

    def test_set_exposure_command(self):
        """SetExposureTimeCommand should be a dataclass with exposure_time_ms."""
        from squid.core.events import SetExposureTimeCommand

        cmd = SetExposureTimeCommand(exposure_time_ms=100.0)
        assert is_dataclass(cmd)
        assert cmd.exposure_time_ms == 100.0

    def test_set_dac_command(self):
        """SetDACCommand should have channel and value."""
        from squid.core.events import SetDACCommand

        cmd = SetDACCommand(channel=0, value=50.0)
        assert cmd.channel == 0
        assert cmd.value == 50.0

    def test_move_stage_command(self):
        """MoveStageCommand should have axis and distance."""
        from squid.core.events import MoveStageCommand

        cmd = MoveStageCommand(axis="x", distance_mm=1.5)
        assert cmd.axis == "x"
        assert cmd.distance_mm == 1.5


class TestStateEvents:
    """Test state change event dataclasses."""

    def test_exposure_changed(self):
        """ExposureTimeChanged should have exposure_time_ms."""
        from squid.core.events import ExposureTimeChanged

        event = ExposureTimeChanged(exposure_time_ms=100.0)
        assert event.exposure_time_ms == 100.0

    def test_stage_position_changed(self):
        """StagePositionChanged should have x, y, z."""
        from squid.core.events import StagePositionChanged

        event = StagePositionChanged(x_mm=1.0, y_mm=2.0, z_mm=3.0)
        assert event.x_mm == 1.0
        assert event.y_mm == 2.0
        assert event.z_mm == 3.0
