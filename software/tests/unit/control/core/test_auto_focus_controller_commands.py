from unittest.mock import MagicMock

from squid.core.events import (
    EventBus,
    StartAutofocusCommand,
    StopAutofocusCommand,
    SetAutofocusParamsCommand,
    AutofocusCompleted,
    AutofocusWorkerFinished,
)
from squid.core.abc import Pos
from squid.mcs.controllers.autofocus.auto_focus_controller import (
    AutoFocusController,
    AutofocusControllerState,
)


def _make_controller(bus: EventBus) -> AutoFocusController:
    live_controller = MagicMock()
    live_controller.is_live = False

    camera_service = MagicMock()
    camera_service.get_callbacks_enabled.return_value = False

    stage_service = MagicMock()
    stage_service.get_position.return_value = Pos(
        x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None
    )

    peripheral_service = MagicMock()
    peripheral_service.wait_till_operation_is_completed.return_value = None

    return AutoFocusController(
        liveController=live_controller,
        camera_service=camera_service,
        stage_service=stage_service,
        peripheral_service=peripheral_service,
        event_bus=bus,
    )


def test_autofocus_controller_starts_on_command(monkeypatch):
    bus = EventBus()
    controller = _make_controller(bus)
    controller.autofocus = MagicMock()

    bus.publish(StartAutofocusCommand())
    bus.drain()

    controller.autofocus.assert_called_once()


def test_autofocus_controller_updates_params_from_command():
    bus = EventBus()
    controller = _make_controller(bus)

    bus.publish(SetAutofocusParamsCommand(n_planes=7, delta_z_um=200.0))
    bus.drain()

    assert controller.N == 7
    assert controller.deltaZ == 0.2


def test_autofocus_controller_emits_failure_on_abort():
    bus = EventBus()
    completed = []
    bus.subscribe(AutofocusCompleted, completed.append)

    controller = _make_controller(bus)

    # Force state to RUNNING to simulate autofocus in progress
    controller._force_state(AutofocusControllerState.RUNNING, reason="test setup")
    controller._keep_running.set()

    bus.publish(StopAutofocusCommand())
    bus.drain()
    # Simulate worker finishing after abort (worker thread publishes this event)
    bus.publish(AutofocusWorkerFinished(success=True, aborted=True))
    bus.drain()

    assert completed, "Expected AutofocusCompleted on abort"
    assert completed[-1].success is False
    assert "aborted" in (completed[-1].error or "")
