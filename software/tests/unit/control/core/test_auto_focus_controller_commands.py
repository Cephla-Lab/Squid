from unittest.mock import MagicMock

from squid.events import (
    EventBus,
    StartAutofocusCommand,
    StopAutofocusCommand,
    SetAutofocusParamsCommand,
    AutofocusCompleted,
)
from squid.abc import Pos
from control.core.autofocus.auto_focus_controller import AutoFocusController


class FakeStage:
    def __init__(self):
        self._pos = Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None)

    def get_pos(self):
        return self._pos

    def wait_for_idle(self, *_args, **_kwargs):
        return


def test_autofocus_controller_starts_on_command(monkeypatch):
    bus = EventBus()
    controller = AutoFocusController(
        camera=MagicMock(),
        stage=FakeStage(),
        liveController=MagicMock(),
        microcontroller=MagicMock(),
        event_bus=bus,
    )
    controller.autofocus = MagicMock()

    bus.publish(StartAutofocusCommand())

    controller.autofocus.assert_called_once()


def test_autofocus_controller_updates_params_from_command():
    bus = EventBus()
    controller = AutoFocusController(
        camera=MagicMock(),
        stage=FakeStage(),
        liveController=MagicMock(),
        microcontroller=MagicMock(),
        event_bus=bus,
    )

    bus.publish(SetAutofocusParamsCommand(n_planes=7, delta_z_um=200.0))

    assert controller.N == 7
    assert controller.deltaZ == 0.2


def test_autofocus_controller_emits_failure_on_abort():
    bus = EventBus()
    completed = []
    bus.subscribe(AutofocusCompleted, completed.append)

    controller = AutoFocusController(
        camera=MagicMock(),
        stage=FakeStage(),
        liveController=MagicMock(),
        microcontroller=MagicMock(),
        event_bus=bus,
    )

    controller.autofocus_in_progress = True
    controller._keep_running.set()

    bus.publish(StopAutofocusCommand())
    # Simulate worker finishing after abort
    controller._on_autofocus_completed()

    assert completed, "Expected AutofocusCompleted on abort"
    assert completed[-1].success is False
    assert "aborted" in (completed[-1].error or "")
