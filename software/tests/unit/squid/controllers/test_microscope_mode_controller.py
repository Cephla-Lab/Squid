"""Tests for MicroscopeModeController."""

from unittest.mock import MagicMock
import pytest

from squid.core.events import EventBus, SetMicroscopeModeCommand, MicroscopeModeChanged
from squid.backend.controllers.microscope_mode_controller import MicroscopeModeController


class MockConfig:
    """Mock channel configuration for testing."""

    def __init__(self, exposure_ms: float, analog_gain: float):
        self.exposure_ms = exposure_ms
        self.analog_gain = analog_gain
        self.illumination_source = 488
        self.intensity = 50.0


class TestMicroscopeModeController:
    """Test suite for MicroscopeModeController."""

    @pytest.fixture
    def mock_camera_service(self):
        return MagicMock()

    @pytest.fixture
    def mock_illumination_service(self):
        return MagicMock()

    @pytest.fixture
    def channel_configs(self):
        return {
            "BF": MockConfig(10.0, 1.0),
            "DAPI": MockConfig(100.0, 5.0),
            "GFP": MockConfig(50.0, 2.0),
        }

    @pytest.fixture
    def mock_filter_wheel_service(self):
        svc = MagicMock()
        svc.is_available.return_value = True
        return svc

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def controller(
        self,
        mock_camera_service,
        mock_illumination_service,
        mock_filter_wheel_service,
        channel_configs,
        event_bus,
    ):
        return MicroscopeModeController(
            camera_service=mock_camera_service,
            illumination_service=mock_illumination_service,
            filter_wheel_service=mock_filter_wheel_service,
            channel_configs=channel_configs,
            event_bus=event_bus,
        )

    def test_initial_state(self, controller):
        """Initial state should have no current mode."""
        assert controller.state.current_mode is None
        assert "BF" in controller.state.available_modes
        assert "DAPI" in controller.state.available_modes
        assert "GFP" in controller.state.available_modes

    def test_handles_set_mode_command(self, controller, event_bus):
        """Controller should handle SetMicroscopeModeCommand."""
        events_received = []
        event_bus.subscribe(MicroscopeModeChanged, events_received.append)

        event_bus.publish(SetMicroscopeModeCommand(configuration_name="BF", objective="20x"))
        event_bus.drain()

        assert controller.state.current_mode == "BF"
        assert len(events_received) == 1
        assert events_received[0].configuration_name == "BF"

    def test_ignores_unknown_mode(self, controller, event_bus):
        """Controller should ignore unknown modes."""
        events_received = []
        event_bus.subscribe(MicroscopeModeChanged, events_received.append)

        event_bus.publish(SetMicroscopeModeCommand(configuration_name="UNKNOWN", objective="20x"))
        event_bus.drain()

        assert controller.state.current_mode is None
        assert len(events_received) == 0

    def test_update_channel_configs_refreshes_modes(
        self, controller, channel_configs
    ):
        """update_channel_configs should replace modes."""
        new_cfgs = {"NEW": MockConfig(5.0, 1.0)}
        controller.update_channel_configs(new_cfgs)
        assert controller.state.available_modes == ("NEW",)

    def test_get_available_modes(self, controller):
        """get_available_modes should return all configured modes."""
        modes = controller.get_available_modes()
        assert "BF" in modes
        assert "DAPI" in modes
        assert "GFP" in modes

    def test_apply_mode_for_acquisition(
        self, controller, mock_camera_service, mock_illumination_service
    ):
        """apply_mode_for_acquisition should call services directly."""
        controller.apply_mode_for_acquisition("DAPI")

        mock_camera_service.set_exposure_time.assert_called_with(100.0)
        mock_camera_service.set_analog_gain.assert_called_with(5.0)
        mock_illumination_service.set_channel_power.assert_called_with(488, 50.0)
        assert controller.state.current_mode == "DAPI"

    def test_apply_mode_for_acquisition_unknown_mode(
        self, controller, mock_camera_service
    ):
        """apply_mode_for_acquisition should ignore unknown modes."""
        controller.apply_mode_for_acquisition("UNKNOWN")

        mock_camera_service.set_exposure_time.assert_not_called()
        assert controller.state.current_mode is None

    def test_controller_without_illumination_service(
        self, mock_camera_service, channel_configs, event_bus
    ):
        """Controller should work without illumination service."""
        controller = MicroscopeModeController(
            camera_service=mock_camera_service,
            illumination_service=None,
            channel_configs=channel_configs,
            event_bus=event_bus,
        )

        event_bus.publish(SetMicroscopeModeCommand(configuration_name="BF", objective="20x"))
        event_bus.drain()
        assert controller.state.current_mode == "BF"
