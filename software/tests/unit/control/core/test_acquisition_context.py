from types import SimpleNamespace
from unittest.mock import MagicMock

from squid.backend.controllers.multipoint.acquisition_context import acquisition_context


def test_acquisition_context_restores_state() -> None:
    live = MagicMock()
    live.is_live = True
    camera = MagicMock()
    camera.get_callbacks_enabled.return_value = False
    stage = MagicMock()
    stage.get_position.return_value = SimpleNamespace(x_mm=1.0, y_mm=2.0, z_mm=3.0)

    context = acquisition_context(live, camera, stage)

    live.stop_live.assert_called_once()
    camera.enable_callbacks.assert_any_call(True)

    context.restore(resume_live=True)

    stage.move_x_to.assert_called_once_with(1.0)
    stage.move_y_to.assert_called_once_with(2.0)
    stage.move_z_to.assert_not_called()
    camera.enable_callbacks.assert_any_call(False)
    live.start_live.assert_called_once()


def test_acquisition_context_can_skip_live_resume() -> None:
    live = MagicMock()
    live.is_live = True
    camera = MagicMock()
    camera.get_callbacks_enabled.return_value = True
    stage = MagicMock()
    stage.get_position.return_value = SimpleNamespace(x_mm=0.0, y_mm=0.0, z_mm=0.0)

    context = acquisition_context(live, camera, stage)
    context.restore(resume_live=False)

    live.start_live.assert_not_called()
