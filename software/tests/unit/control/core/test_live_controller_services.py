"""Tests for LiveController using services and EventBus."""

from unittest.mock import MagicMock

from squid.core.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
    LiveStateChanged,
)
from squid.mcs.controllers.live_controller import (
    LiveController,
    TriggerMode,
    LiveStateData,
    LiveControllerState,
)
from squid.core.abc import CameraAcquisitionMode


def make_controller(bus: EventBus):
    cam_service = MagicMock()
    illum_service = MagicMock()
    periph_service = MagicMock()
    controller = LiveController(
        camera_service=cam_service,
        event_bus=bus,
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
    bus.drain()
    cam_svc.start_streaming.assert_called_once()
    assert controller.is_live is True
    assert controller.observable_state.is_live is True
    assert controller.observable_state.current_channel == "BF"
    assert events[-1].is_live is True

    events.clear()
    bus.publish(StopLiveCommand())
    bus.drain()
    cam_svc.stop_streaming.assert_called_once()
    assert controller.is_live is False
    assert any(evt.is_live is False for evt in events)


def test_trigger_mode_change_uses_service():
    bus = EventBus()
    controller, cam_svc, _, _ = make_controller(bus)

    events = []
    bus.subscribe(TriggerModeChanged, events.append)

    bus.publish(SetTriggerModeCommand(mode="Hardware"))
    bus.drain()
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
    bus.drain()
    assert controller.observable_state.trigger_fps == 5.0
    assert events[-1].fps == 5.0


def test_live_controller_requires_illumination_service_when_enabled():
    bus = EventBus()
    cam_service = MagicMock()
    try:
        LiveController(camera_service=cam_service, event_bus=bus)
    except ValueError as exc:
        assert "IlluminationService" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError when illumination service missing")
