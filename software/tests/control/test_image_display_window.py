"""Tests for ImageDisplayWindow's Ctrl+Scroll Z-navigation event filter."""

import numpy as np
import pytest
from qtpy.QtCore import Qt, QPointF, QPoint
from qtpy.QtGui import QWheelEvent, QPainter
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


# ─── Overexposure indicator ──────────────────────────────────────────────────

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)


def _live_lut(win):
    """The 256-entry RGB lookup table the live image item currently renders with (None = raw grayscale)."""
    lut = win.graphics_widget.img.lut
    if callable(lut):
        lut = lut(np.zeros((1, 1), dtype=np.uint8))
    return None if lut is None else np.asarray(lut)[:, :3]


def test_overexposure_indicator_is_off_by_default(image_display_window):
    assert image_display_window.btn_overexposure.isCheckable()
    assert not image_display_window.btn_overexposure.isChecked()
    assert _live_lut(image_display_window) is None


def test_overexposure_on_renders_top_bin_red_and_rest_grayscale(image_display_window):
    image_display_window.btn_overexposure.click()

    lut = _live_lut(image_display_window)
    assert tuple(lut[0]) == BLACK
    assert tuple(lut[254]) == WHITE
    assert tuple(lut[255]) == RED


def test_overexposure_off_restores_plain_grayscale(image_display_window):
    image_display_window.btn_overexposure.click()
    image_display_window.btn_overexposure.click()

    lut = _live_lut(image_display_window)
    assert lut is None or tuple(lut[255]) == WHITE


def test_overexposure_toggle_drives_histogram_gradient_in_lut_mode(qtbot):
    """In show_LUT mode the histogram widget owns the live LUT, so the indicator must be
    expressed as its gradient - otherwise the next contrast change would wipe it out."""
    win = ImageDisplayWindow(show_LUT=True)
    qtbot.addWidget(win)

    win.btn_overexposure.click()
    on = _live_lut(win)
    assert tuple(on[255]) == RED
    assert tuple(on[254]) == WHITE
    assert tuple(on[0]) == BLACK

    win.btn_overexposure.click()
    off = _live_lut(win)
    assert off is None or tuple(off[255]) == WHITE


# ─── Alignment reference overlay ────────────────────────────────────────────

MAGENTA = (255, 0, 255)


@pytest.mark.parametrize("show_lut", [False, True])
def test_show_alignment_reference_overlays_additive_magenta_item(qtbot, show_lut):
    win = ImageDisplayWindow(show_LUT=show_lut)
    qtbot.addWidget(win)
    ref = np.arange(16, dtype=np.uint16).reshape(4, 4)

    win.show_alignment_reference(ref)

    item = win.alignment_reference_item
    assert item in win._active_view().addedItems
    assert np.array_equal(item.image, ref)
    lut = np.asarray(item.lut)[:, :3]
    assert tuple(lut[0]) == BLACK
    assert tuple(lut[255]) == MAGENTA
    assert item.paintMode == QPainter.CompositionMode_Plus


def test_show_alignment_reference_twice_reuses_the_overlay_item(image_display_window):
    win = image_display_window
    win.show_alignment_reference(np.zeros((4, 4), dtype=np.uint16))
    first = win.alignment_reference_item
    replacement = np.ones((4, 4), dtype=np.uint16)

    win.show_alignment_reference(replacement)

    assert win.alignment_reference_item is first
    assert np.array_equal(first.image, replacement)
    assert win._active_view().addedItems.count(first) == 1


def test_displayed_frame_levels_propagate_to_alignment_reference(qtbot):
    win = ImageDisplayWindow(autoLevels=True)
    qtbot.addWidget(win)
    win.show_alignment_reference(np.zeros((2, 2), dtype=np.uint16))

    win.display_image(np.array([[100, 200], [300, 4000]], dtype=np.uint16))

    assert tuple(win.alignment_reference_item.getLevels()) == (100, 4000)


def test_histogram_level_changes_propagate_to_alignment_reference(qtbot):
    win = ImageDisplayWindow(show_LUT=True)
    qtbot.addWidget(win)
    win.display_image(np.zeros((4, 4), dtype=np.uint16))
    win.show_alignment_reference(np.zeros((4, 4), dtype=np.uint16))

    win.LUTWidget.setLevels(10, 500)

    assert tuple(win.alignment_reference_item.getLevels()) == (10, 500)


def test_hide_alignment_reference_removes_item_and_is_idempotent(image_display_window):
    win = image_display_window
    win.show_alignment_reference(np.zeros((4, 4), dtype=np.uint16))
    item = win.alignment_reference_item

    win.hide_alignment_reference()
    win.hide_alignment_reference()

    assert win.alignment_reference_item is None
    assert item not in win._active_view().addedItems


def test_current_image_returns_last_displayed_frame(image_display_window):
    assert image_display_window.current_image() is None
    live = np.arange(6, dtype=np.uint16).reshape(2, 3)

    image_display_window.display_image(live)

    assert np.array_equal(image_display_window.current_image(), live)
