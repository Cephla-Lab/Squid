from unittest.mock import Mock


def test_live_controller_sets_mode_gate_live_and_idle() -> None:
    from squid.core.events import EventBus, GlobalModeChanged
    from squid.core.mode_gate import GlobalMode, GlobalModeGate
    from squid.backend.controllers.live_controller import LiveController
    from _def import TriggerMode

    bus = EventBus()
    gate = GlobalModeGate(bus)

    camera_service = Mock()
    camera_service.start_streaming = Mock()
    camera_service.stop_streaming = Mock()

    controller = LiveController(
        event_bus=bus,
        camera_service=camera_service,
        control_illumination=False,
        use_internal_timer_for_hardware_trigger=False,
        mode_gate=gate,
    )
    controller.trigger_mode = TriggerMode.HARDWARE

    received: list[GlobalModeChanged] = []
    bus.subscribe(GlobalModeChanged, received.append)

    controller.start_live()
    bus.drain()
    assert gate.get_mode() is GlobalMode.LIVE

    controller.stop_live()
    bus.drain()
    assert gate.get_mode() is GlobalMode.IDLE

    assert [e.new_mode for e in received] == ["LIVE", "IDLE"]
