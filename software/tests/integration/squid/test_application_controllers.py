"""Integration tests for controller wiring in ApplicationContext."""

import pytest

from squid.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetMicroscopeModeCommand,
    LiveStateChanged,
    TriggerModeChanged,
)


class TestLiveControllerEventBus:
    """Test LiveController EventBus integration."""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def mock_microscope(self):
        from unittest.mock import MagicMock

        microscope = MagicMock()
        microscope.addons.sci_microscopy_led_array = None
        microscope.addons.nl5 = None
        microscope.addons.cellx = None
        microscope.addons.xlight = None
        microscope.addons.dragonfly = None
        microscope.addons.emission_filter_wheel = None
        microscope.illumination_controller = MagicMock()
        microscope.low_level_drivers.microcontroller = MagicMock()
        return microscope

    @pytest.fixture
    def mock_camera(self):
        from unittest.mock import MagicMock

        camera = MagicMock()
        camera.get_ready_for_trigger.return_value = True
        camera.get_exposure_time.return_value = 100.0
        return camera

    @pytest.fixture
    def live_controller(self, mock_microscope, mock_camera, event_bus):
        from control.core.display import LiveController

        return LiveController(
            microscope=mock_microscope,
            camera=mock_camera,
            event_bus=event_bus,
        )

    def test_live_controller_has_event_bus(self, live_controller, event_bus):
        """LiveController should store EventBus reference."""
        assert live_controller._bus is event_bus

    def test_live_controller_has_state(self, live_controller):
        """LiveController should have state property."""
        state = live_controller.state
        assert state.is_live is False
        assert state.trigger_mode == "Software"

    def test_start_live_command_updates_state(self, live_controller, event_bus):
        """StartLiveCommand should update state and publish event."""
        events_received = []
        event_bus.subscribe(LiveStateChanged, events_received.append)

        event_bus.publish(StartLiveCommand(configuration="BF"))

        assert live_controller.state.is_live is True
        assert len(events_received) == 1
        assert events_received[0].is_live is True

    def test_stop_live_command_updates_state(self, live_controller, event_bus):
        """StopLiveCommand should update state and publish event."""
        # Start first
        event_bus.publish(StartLiveCommand())

        events_received = []
        event_bus.subscribe(LiveStateChanged, events_received.append)

        event_bus.publish(StopLiveCommand())

        assert live_controller.state.is_live is False
        assert len(events_received) == 1
        assert events_received[0].is_live is False

    def test_set_trigger_mode_command(self, live_controller, event_bus):
        """SetTriggerModeCommand should update state and publish event."""
        events_received = []
        event_bus.subscribe(TriggerModeChanged, events_received.append)

        event_bus.publish(SetTriggerModeCommand(mode="Hardware"))

        assert live_controller.state.trigger_mode == "Hardware"
        assert len(events_received) == 1
        assert events_received[0].mode == "Hardware"


class TestControllersDataclass:
    """Test Controllers dataclass structure."""

    def test_controllers_dataclass_has_new_fields(self):
        """Controllers dataclass should have new controller fields."""
        from squid.application import Controllers

        # Check the dataclass has the expected fields
        from dataclasses import fields

        field_names = [f.name for f in fields(Controllers)]
        assert "live" in field_names
        assert "stream_handler" in field_names
        assert "microscope_mode" in field_names
        assert "peripherals" in field_names


class TestNewControllerImports:
    """Test that new controllers can be imported."""

    def test_import_microscope_mode_controller(self):
        """MicroscopeModeController should be importable."""
        from squid.controllers import MicroscopeModeController

        assert MicroscopeModeController is not None

    def test_import_peripherals_controller(self):
        """PeripheralsController should be importable."""
        from squid.controllers import PeripheralsController

        assert PeripheralsController is not None

    def test_controllers_module_exports(self):
        """Controllers module should export all controllers."""
        from squid import controllers

        assert hasattr(controllers, "MicroscopeModeController")
        assert hasattr(controllers, "PeripheralsController")
