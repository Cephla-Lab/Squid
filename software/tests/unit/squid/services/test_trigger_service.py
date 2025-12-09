"""Tests for TriggerService."""

from unittest.mock import Mock


class TestTriggerService:
    """Test suite for TriggerService."""

    def test_handles_set_trigger_mode_command(self):
        """Should respond to SetTriggerModeCommand."""
        from squid.events import EventBus, SetTriggerModeCommand
        from squid.services.trigger_service import TriggerService

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)
        bus.publish(SetTriggerModeCommand(mode="Hardware"))

        mock_controller.set_trigger_mode.assert_called_once_with("Hardware")

    def test_publishes_trigger_mode_changed(self):
        """Should publish TriggerModeChanged after setting mode."""
        from squid.events import EventBus, SetTriggerModeCommand, TriggerModeChanged
        from squid.services.trigger_service import TriggerService

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)

        received = []
        bus.subscribe(TriggerModeChanged, lambda e: received.append(e))
        bus.publish(SetTriggerModeCommand(mode="Software"))

        assert len(received) == 1
        assert received[0].mode == "Software"

    def test_handles_set_trigger_fps_command(self):
        """Should respond to SetTriggerFPSCommand."""
        from squid.events import EventBus, SetTriggerFPSCommand
        from squid.services.trigger_service import TriggerService

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)
        bus.publish(SetTriggerFPSCommand(fps=30.0))

        mock_controller.set_trigger_fps.assert_called_once_with(30.0)

    def test_publishes_trigger_fps_changed(self):
        """Should publish TriggerFPSChanged after setting FPS."""
        from squid.events import EventBus, SetTriggerFPSCommand, TriggerFPSChanged
        from squid.services.trigger_service import TriggerService

        mock_controller = Mock()
        bus = EventBus()

        TriggerService(mock_controller, bus)

        received = []
        bus.subscribe(TriggerFPSChanged, lambda e: received.append(e))
        bus.publish(SetTriggerFPSCommand(fps=15.0))

        assert len(received) == 1
        assert received[0].fps == 15.0
