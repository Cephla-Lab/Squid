"""Tests for MicroscopeModeService."""

from unittest.mock import Mock


class TestMicroscopeModeService:
    """Test suite for MicroscopeModeService."""

    def test_handles_set_microscope_mode_command(self):
        """Should respond to SetMicroscopeModeCommand."""
        from squid.events import EventBus, SetMicroscopeModeCommand
        from squid.services.microscope_mode_service import MicroscopeModeService

        mock_controller = Mock()
        mock_config_manager = Mock()
        mock_config = Mock()
        mock_config_manager.get_channel_configuration_by_name.return_value = mock_config
        bus = EventBus()

        MicroscopeModeService(mock_controller, mock_config_manager, bus)
        bus.publish(SetMicroscopeModeCommand(configuration_name="GFP", objective="20x"))

        mock_config_manager.get_channel_configuration_by_name.assert_called_once_with(
            "20x", "GFP"
        )
        mock_controller.set_microscope_mode.assert_called_once_with(mock_config)

    def test_publishes_microscope_mode_changed(self):
        """Should publish MicroscopeModeChanged after setting mode."""
        from squid.events import (
            EventBus,
            MicroscopeModeChanged,
            SetMicroscopeModeCommand,
        )
        from squid.services.microscope_mode_service import MicroscopeModeService

        mock_controller = Mock()
        mock_config_manager = Mock()
        mock_config = Mock()
        mock_config_manager.get_channel_configuration_by_name.return_value = mock_config
        bus = EventBus()

        MicroscopeModeService(mock_controller, mock_config_manager, bus)

        received = []
        bus.subscribe(MicroscopeModeChanged, lambda e: received.append(e))
        bus.publish(
            SetMicroscopeModeCommand(configuration_name="mCherry", objective="10x")
        )

        assert len(received) == 1
        assert received[0].configuration_name == "mCherry"
