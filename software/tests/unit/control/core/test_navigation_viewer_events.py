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
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.os.path.isfile",
        lambda _path: True,
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


def test_navigation_viewer_context_menu_has_recenter_action(qtbot, monkeypatch):
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.cv2.imread",
        lambda _path, _flags=None: np.zeros((100, 150, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.os.path.isfile",
        lambda _path: True,
    )
    viewer = NavigationViewer(_DummyObjectiveStore(), _DummyCamera())
    qtbot.addWidget(viewer)

    action_texts = {action.text() for action in viewer.view.menu.actions()}
    assert "Recenter Navigation View" in action_texts


def test_navigation_viewer_recenter_keeps_zoom_and_recenters(qtbot, monkeypatch):
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.cv2.imread",
        lambda _path, _flags=None: np.zeros((100, 150, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.os.path.isfile",
        lambda _path: True,
    )
    viewer = NavigationViewer(_DummyObjectiveStore(), _DummyCamera())
    qtbot.addWidget(viewer)

    viewer.view.setRange(xRange=(5.0, 35.0), yRange=(15.0, 55.0), padding=0)
    before_x, before_y = viewer.view.viewRange()
    before_width = before_x[1] - before_x[0]
    before_height = before_y[1] - before_y[0]

    viewer._recenter_navigation_view()

    after_x, after_y = viewer.view.viewRange()
    after_width = after_x[1] - after_x[0]
    after_height = after_y[1] - after_y[0]
    after_center_x = (after_x[0] + after_x[1]) / 2.0
    after_center_y = (after_y[0] + after_y[1]) / 2.0

    assert after_width == pytest.approx(before_width)
    assert after_height == pytest.approx(before_height)
    assert after_center_x == pytest.approx(viewer.image_width / 2.0)
    assert after_center_y == pytest.approx(viewer.image_height / 2.0)
