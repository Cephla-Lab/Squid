"""Integration tests for controller wiring in ApplicationContext."""

import pytest

from squid.core.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    LiveStateChanged,
    TriggerModeChanged,
)


class TestLiveControllerEventBus:
    """Test LiveController EventBus integration (service-based controller)."""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def mock_camera_service(self):
        from unittest.mock import MagicMock

        service = MagicMock()
        service.get_ready_for_trigger.return_value = True
        service.get_total_frame_time.return_value = 10.0
        service.get_strobe_time.return_value = 0.0
        return service

    @pytest.fixture
    def live_controller(self, mock_camera_service, event_bus):
        from squid.mcs.controllers.live_controller import LiveController

        return LiveController(
            camera_service=mock_camera_service,
            event_bus=event_bus,
            control_illumination=False,
            use_internal_timer_for_hardware_trigger=False,
        )

    def test_live_controller_has_event_bus(self, live_controller, event_bus):
        """LiveController should store EventBus reference."""
        assert live_controller._bus is event_bus

    def test_live_controller_has_state(self, live_controller):
        state = live_controller.observable_state
        assert state.is_live is False
        assert state.trigger_mode == "Software"

    def test_start_live_command_updates_state(self, live_controller, event_bus):
        """StartLiveCommand should update state and publish event."""
        events_received = []
        event_bus.subscribe(LiveStateChanged, events_received.append)

        # Ensure we don't start any trigger timers in this test.
        event_bus.publish(SetTriggerModeCommand(mode="Hardware"))
        event_bus.drain()

        event_bus.publish(StartLiveCommand(configuration="BF"))
        event_bus.drain()

        assert live_controller.observable_state.is_live is True
        assert events_received, "Expected LiveStateChanged events"
        assert events_received[-1].is_live is True

    def test_stop_live_command_updates_state(self, live_controller, event_bus):
        """StopLiveCommand should update state and publish event."""
        # Start first
        event_bus.publish(SetTriggerModeCommand(mode="Hardware"))
        event_bus.drain()
        event_bus.publish(StartLiveCommand())
        event_bus.drain()

        events_received = []
        event_bus.subscribe(LiveStateChanged, events_received.append)

        event_bus.publish(StopLiveCommand())
        event_bus.drain()

        assert live_controller.observable_state.is_live is False
        assert events_received, "Expected LiveStateChanged events"
        assert events_received[-1].is_live is False

    def test_set_trigger_mode_command(self, live_controller, event_bus):
        """SetTriggerModeCommand should update state and publish event."""
        events_received = []
        event_bus.subscribe(TriggerModeChanged, events_received.append)

        event_bus.publish(SetTriggerModeCommand(mode="Hardware"))
        event_bus.drain()

        assert live_controller.observable_state.trigger_mode == "Hardware"
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
        assert "stream_handler_focus" in field_names
        assert "microscope_mode" in field_names
        assert "peripherals" in field_names
        assert "autofocus" in field_names
        assert "laser_autofocus" in field_names
        assert "multipoint" in field_names
        assert "scan_coordinates" in field_names


class TestNewControllerImports:
    """Test that new controllers can be imported."""

    def test_import_microscope_mode_controller(self):
        """MicroscopeModeController should be importable."""
        from squid.mcs.controllers import MicroscopeModeController

        assert MicroscopeModeController is not None

    def test_import_peripherals_controller(self):
        """PeripheralsController should be importable."""
        from squid.mcs.controllers import PeripheralsController

        assert PeripheralsController is not None

    def test_controllers_module_exports(self):
        """Controllers should be available from squid.mcs.controllers."""
        import squid.mcs.controllers as controllers

        assert hasattr(controllers, "MicroscopeModeController")
        assert hasattr(controllers, "PeripheralsController")
