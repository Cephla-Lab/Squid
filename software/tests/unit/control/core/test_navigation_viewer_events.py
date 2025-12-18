import numpy as np
import pytest
from unittest.mock import MagicMock

from squid.core.abc import Pos
from squid.core.events import StageMovementStopped
from squid.ui.widgets.display.navigation_viewer import NavigationViewer


class _DummyObjectiveStore:
    def get_pixel_size_factor(self) -> float:
        return 1.0


class _DummyCamera:
    def get_fov_size_mm(self) -> float:
        return 1.0

    def get_fov_width_mm(self) -> float:
        return 1.0

    def get_fov_height_mm(self) -> float:
        return 1.0


def test_navigation_viewer_stage_stop_event_updates_fov(qtbot, monkeypatch):
    """StageMovementStopped should redraw using event coordinates."""
    # Avoid file I/O for background images
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.cv2.imread",
        lambda _path, _flags=None: np.zeros((4, 4, 3), dtype=np.uint8),
    )

    viewer = NavigationViewer(_DummyObjectiveStore(), _DummyCamera())
    qtbot.addWidget(viewer)

    viewer.draw_fov_current_location = MagicMock()
    event = StageMovementStopped(x_mm=1.2, y_mm=3.4, z_mm=0.5)

    viewer._on_stage_movement_stopped(event)

    viewer.draw_fov_current_location.assert_called_once()
    pos_arg = viewer.draw_fov_current_location.call_args[0][0]
    assert isinstance(pos_arg, Pos)
    assert pos_arg.x_mm == pytest.approx(1.2)
    assert pos_arg.y_mm == pytest.approx(3.4)
    assert pos_arg.z_mm == pytest.approx(0.5)
