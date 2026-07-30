"""Tests for ImageDisplayWindow's Ctrl+Scroll Z-navigation event filter and center crosshair."""

import numpy as np
import pyqtgraph as pg
import pytest
from qtpy.QtCore import Qt, QPointF, QPoint
from qtpy.QtGui import QWheelEvent
from qtpy.QtWidgets import QApplication

from control.core.core import ImageDisplayWindow


def _wheel_event(angle_y, modifiers):
    """Build a synthetic QWheelEvent with the given y-angle delta and modifiers."""
    return QWheelEvent(
        QPointF(0, 0),
        QPointF(0, 0),
        QPoint(0, 0),
        QPoint(0, angle_y),
        Qt.NoButton,
        modifiers,
        Qt.NoScrollPhase,
        False,
    )


@pytest.fixture(autouse=True)
def _pin_z_step_constants(monkeypatch):
    """Pin Z step values so tests are independent of default-value churn."""
    import control._def

    monkeypatch.setattr(control._def, "LIVE_VIEW_Z_STEP_UM", 1.0)
    monkeypatch.setattr(control._def, "LIVE_VIEW_Z_STEP_FAST_UM", 20.0)


@pytest.fixture
def image_display_window(qtbot):
    win = ImageDisplayWindow()
    qtbot.addWidget(win)
    return win


@pytest.mark.parametrize(
    "angle_y, modifiers, expected_um",
    [
        (120, Qt.ControlModifier, 1.0),
        (120, Qt.ControlModifier | Qt.ShiftModifier, 20.0),
        (-120, Qt.ControlModifier, -1.0),
    ],
)
def test_ctrl_scroll_emits_signed_step_per_notch(image_display_window, angle_y, modifiers, expected_um):
    received = []
    image_display_window.signal_z_um_delta.connect(received.append)
    image_display_window.eventFilter(image_display_window, _wheel_event(angle_y, modifiers))
    assert received == [pytest.approx(expected_um)]


def test_zero_delta_is_consumed_and_does_not_emit(image_display_window):
    received = []
    image_display_window.signal_z_um_delta.connect(received.append)
    consumed = image_display_window.eventFilter(image_display_window, _wheel_event(0, Qt.ControlModifier))
    assert received == []
    assert consumed is True


def test_plain_scroll_is_not_consumed_and_does_not_emit(image_display_window):
    received = []
    image_display_window.signal_z_um_delta.connect(received.append)
    consumed = image_display_window.eventFilter(image_display_window, _wheel_event(120, Qt.NoModifier))
    assert received == []
    assert consumed is False


def test_wheel_event_at_real_target_triggers_filter_with_lut(qtbot):
    """In show_LUT mode, wheel events arrive at the inner pg.ImageView's QGraphicsView
    viewport — not at the outer ImageView. The filter must be installed there."""
    win = ImageDisplayWindow(show_LUT=True)
    qtbot.addWidget(win)
    received = []
    win.signal_z_um_delta.connect(received.append)

    inner_viewport = win.graphics_widget.view.ui.graphicsView.viewport()
    QApplication.sendEvent(inner_viewport, _wheel_event(120, Qt.ControlModifier))

    assert received == [pytest.approx(1.0)]


def test_wheel_step_size_picks_up_live_def_changes(image_display_window, monkeypatch):
    """Updating control._def.LIVE_VIEW_Z_STEP_UM at runtime (e.g. from
    PreferencesDialog._apply_live_settings) must affect the next wheel event —
    the eventFilter must read the constant through the module, not via a local
    binding captured at import."""
    import control._def

    # Override the autouse fixture's pin to verify live updates are picked up.
    monkeypatch.setattr(control._def, "LIVE_VIEW_Z_STEP_UM", 7.5)
    monkeypatch.setattr(control._def, "LIVE_VIEW_Z_STEP_FAST_UM", 99.0)

    received = []
    image_display_window.signal_z_um_delta.connect(received.append)

    image_display_window.eventFilter(image_display_window, _wheel_event(120, Qt.ControlModifier))
    image_display_window.eventFilter(image_display_window, _wheel_event(120, Qt.ControlModifier | Qt.ShiftModifier))

    assert received == [pytest.approx(7.5), pytest.approx(99.0)]


# --- Center crosshair -------------------------------------------------------


# Deliberately non-square (h=100, w=200) so an x/y swap fails loudly; a square
# frame would let a transposed center pass.
FRAME = np.zeros((100, 200), dtype=np.uint16)
CENTER_X = 100.0  # width / 2
CENTER_Y = 50.0  # height / 2


@pytest.fixture
def crosshair_window(qtbot):
    win = ImageDisplayWindow(enable_crosshair=True)
    qtbot.addWidget(win)
    return win


def test_crosshair_button_absent_when_not_enabled(image_display_window):
    """The laser-AF focus view opts out and must not grow a crosshair button."""
    assert image_display_window.btn_crosshair is None
    assert image_display_window.enable_crosshair is False


def test_crosshair_button_disabled_until_first_image(crosshair_window):
    assert crosshair_window.btn_crosshair is not None
    assert crosshair_window.btn_crosshair.isEnabled() is False
    crosshair_window.display_image(FRAME)
    assert crosshair_window.btn_crosshair.isEnabled() is True


def test_toggle_creates_and_shows_lines_then_hides(crosshair_window):
    crosshair_window.display_image(FRAME)
    assert crosshair_window.crosshair_v is None  # created lazily, not before first toggle

    crosshair_window.btn_crosshair.setChecked(True)
    crosshair_window.toggle_crosshair()
    assert isinstance(crosshair_window.crosshair_v, pg.InfiniteLine)
    assert isinstance(crosshair_window.crosshair_h, pg.InfiniteLine)
    assert crosshair_window.crosshair_v.isVisible() is True
    assert crosshair_window.crosshair_h.isVisible() is True

    crosshair_window.btn_crosshair.setChecked(False)
    crosshair_window.toggle_crosshair()
    assert crosshair_window.crosshair_v.isVisible() is False
    assert crosshair_window.crosshair_h.isVisible() is False


def test_crosshair_is_centered_on_the_frame(crosshair_window):
    crosshair_window.display_image(FRAME)
    crosshair_window.btn_crosshair.setChecked(True)
    crosshair_window.toggle_crosshair()

    assert crosshair_window.crosshair_v.value() == pytest.approx(CENTER_X)
    assert crosshair_window.crosshair_h.value() == pytest.approx(CENTER_Y)


def test_crosshair_recenters_when_frame_size_changes(crosshair_window):
    crosshair_window.display_image(FRAME)
    crosshair_window.btn_crosshair.setChecked(True)
    crosshair_window.toggle_crosshair()

    crosshair_window.display_image(np.zeros((40, 60), dtype=np.uint16))
    assert crosshair_window.crosshair_v.value() == pytest.approx(30.0)
    assert crosshair_window.crosshair_h.value() == pytest.approx(20.0)


def test_same_shape_frame_does_not_reposition(crosshair_window, monkeypatch):
    """display_image runs at frame rate; the shape guard must skip the Qt calls."""
    crosshair_window.display_image(FRAME)
    crosshair_window.btn_crosshair.setChecked(True)
    crosshair_window.toggle_crosshair()

    calls = []
    monkeypatch.setattr(crosshair_window.crosshair_v, "setPos", lambda *a: calls.append(a))
    monkeypatch.setattr(crosshair_window.crosshair_h, "setPos", lambda *a: calls.append(a))

    crosshair_window.display_image(FRAME)
    assert calls == []


def test_crosshair_works_with_lut(qtbot):
    """show_LUT=True is the real live-view configuration: the lines must be added to the
    inner ViewBox via _active_view(), not to the outer pg.ImageView."""
    win = ImageDisplayWindow(show_LUT=True, enable_crosshair=True)
    qtbot.addWidget(win)
    win.display_image(FRAME)
    win.btn_crosshair.setChecked(True)
    win.toggle_crosshair()

    # getViewBox(), not ViewBox.addedItems — pyqtgraph does not track items added with
    # ignoreBounds=True in addedItems, but they are still parented to the ViewBox.
    view = win._active_view()
    assert win.crosshair_v.getViewBox() is view
    assert win.crosshair_h.getViewBox() is view
    assert win.crosshair_v.value() == pytest.approx(CENTER_X)
    assert win.crosshair_h.value() == pytest.approx(CENTER_Y)
