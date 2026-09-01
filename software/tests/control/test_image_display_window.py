"""Tests for ImageDisplayWindow: Ctrl+Scroll Z-navigation, overexposure indicator, alignment overlay."""

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

RED = (255, 0, 0)


def _mono_frame_with_saturated_corner():
    frame = np.full((2, 2), 1000, dtype=np.uint16)
    frame[0, 0] = np.iinfo(np.uint16).max
    return frame


def _rgb_frame_with_saturated_corner():
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[0, 0] = (255, 255, 255)
    frame[1, 1] = (10, 20, 30)
    return frame


def test_overexposure_indicator_is_off_by_default(image_display_window):
    assert image_display_window.btn_overexposure.isCheckable()
    assert not image_display_window.btn_overexposure.isChecked()
    assert image_display_window.overexposure_item is None


@pytest.mark.parametrize("show_lut", [False, True])
def test_overexposure_overlays_pixels_at_the_upper_contrast_limit_in_red(qtbot, show_lut):
    win = ImageDisplayWindow(show_LUT=show_lut)
    qtbot.addWidget(win)
    win.btn_overexposure.click()

    win.display_image(_mono_frame_with_saturated_corner())

    item = win.overexposure_item
    assert item in win._active_view().addedItems
    assert item.image.tolist() == [[1, 0], [0, 0]]
    lut = np.asarray(item.lut)
    assert tuple(lut[1][:3]) == RED and lut[1][3] == 255
    assert lut[0][3] == 0  # unsaturated pixels stay see-through


def test_overexposure_marks_rgb_frames_without_modifying_them(image_display_window):
    win = image_display_window
    frame = _rgb_frame_with_saturated_corner()
    win.btn_overexposure.click()

    win.display_image(frame)

    assert win.overexposure_item.image.tolist() == [[1, 0], [0, 0]]
    assert np.array_equal(win.graphics_widget.img.image, frame)


def test_overexposure_follows_histogram_level_changes(qtbot):
    win = ImageDisplayWindow(show_LUT=True)
    qtbot.addWidget(win)
    win.btn_overexposure.click()
    win.display_image(np.array([[100, 10], [10, 10]], dtype=np.uint16))
    assert win.overexposure_item.image.tolist() == [[0, 0], [0, 0]]

    win.LUTWidget.setLevels(0, 50)

    assert win.overexposure_item.image.tolist() == [[1, 0], [0, 0]]


def test_overexposure_off_removes_the_overlay(image_display_window):
    win = image_display_window
    win.btn_overexposure.click()
    item = win.overexposure_item

    win.btn_overexposure.click()

    assert win.overexposure_item is None
    assert item not in win._active_view().addedItems


# ─── Alignment reference overlay ────────────────────────────────────────────

BLACK = (0, 0, 0)
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


def test_show_alignment_reference_reduces_color_images_to_intensity(image_display_window):
    """pyqtgraph ignores lookup tables on H x W x 3 data, so the magenta overlay needs a 2-D image."""
    image_display_window.show_alignment_reference(np.zeros((4, 4, 3), dtype=np.uint8))

    assert image_display_window.alignment_reference_item.image.ndim == 2


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
