"""Tests for ImageDisplayWindow's Ctrl+Scroll Z-navigation event filter."""

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
