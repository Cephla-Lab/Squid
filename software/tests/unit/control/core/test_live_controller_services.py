"""Tests for LiveController using services and EventBus."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from squid.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
    LiveStateChanged,
)
from control.core.display.live_controller import (
    LiveController,
    TriggerMode,
    LiveState,
)
from squid.abc import CameraAcquisitionMode


class DummyMicroscope:
    """Lightweight microscope stub for LiveController."""

    def __init__(self):
        self.low_level_drivers = MagicMock()
        self.addons = MagicMock()
        self.objective_store = SimpleNamespace(current_objective="10x")
        self.channel_configuration_manager = MagicMock()
        self.channel_configuration_manager.get_channel_configuration_by_name.return_value = SimpleNamespace(
            name="BF"
        )


def make_controller(bus: EventBus):
    mic = DummyMicroscope()
    camera = MagicMock()
    cam_service = MagicMock()
    illum_service = MagicMock()
    periph_service = MagicMock()
    controller = LiveController(
        microscope=mic,
        camera=camera,
        event_bus=bus,
        camera_service=cam_service,
        illumination_service=illum_service,
        peripheral_service=periph_service,
    )
    return controller, cam_service, illum_service, periph_service


def test_start_stop_live_uses_camera_service():
    bus = EventBus()
    controller, cam_svc, _, periph_svc = make_controller(bus)

    events = []
    bus.subscribe(LiveStateChanged, events.append)

    bus.publish(StartLiveCommand(configuration="BF"))
    cam_svc.start_streaming.assert_called_once()
    assert controller.is_live is True
    assert controller.state.is_live is True
    assert controller.state.current_channel == "BF"
    assert events[-1].is_live is True

    events.clear()
    bus.publish(StopLiveCommand())
    cam_svc.stop_streaming.assert_called_once()
    assert controller.is_live is False
    assert any(evt.is_live is False for evt in events)


def test_trigger_mode_change_uses_service():
    bus = EventBus()
    controller, cam_svc, _, _ = make_controller(bus)

    events = []
    bus.subscribe(TriggerModeChanged, events.append)

    bus.publish(SetTriggerModeCommand(mode="Hardware"))
    cam_svc.set_acquisition_mode.assert_called_with(
        CameraAcquisitionMode.HARDWARE_TRIGGER
    )
    assert controller.trigger_mode == TriggerMode.HARDWARE
    assert events[-1].mode == "Hardware"


def test_trigger_fps_update():
    bus = EventBus()
    controller, _, _, _ = make_controller(bus)
    events = []
    bus.subscribe(TriggerFPSChanged, events.append)

    bus.publish(SetTriggerFPSCommand(fps=5.0))
    assert controller.state.trigger_fps == 5.0
    assert events[-1].fps == 5.0
