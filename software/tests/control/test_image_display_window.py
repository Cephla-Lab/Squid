"""Tests for ImageDisplayWindow's Ctrl+Scroll Z-navigation event filter."""

import pytest
from qtpy.QtCore import Qt, QPointF, QPoint
from qtpy.QtGui import QWheelEvent

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


@pytest.fixture
def image_display_window(qtbot):
    win = ImageDisplayWindow()
    qtbot.addWidget(win)
    return win


def test_ctrl_scroll_emits_one_um_per_notch(image_display_window):
    received = []
    image_display_window.signal_z_um_delta.connect(received.append)
    image_display_window.eventFilter(image_display_window, _wheel_event(120, Qt.ControlModifier))
    assert received == [pytest.approx(1.0)]


def test_ctrl_shift_scroll_emits_twenty_um_per_notch(image_display_window):
    received = []
    image_display_window.signal_z_um_delta.connect(received.append)
    image_display_window.eventFilter(image_display_window, _wheel_event(120, Qt.ControlModifier | Qt.ShiftModifier))
    assert received == [pytest.approx(20.0)]


def test_ctrl_scroll_down_emits_negative(image_display_window):
    received = []
    image_display_window.signal_z_um_delta.connect(received.append)
    image_display_window.eventFilter(image_display_window, _wheel_event(-120, Qt.ControlModifier))
    assert received == [pytest.approx(-1.0)]


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
