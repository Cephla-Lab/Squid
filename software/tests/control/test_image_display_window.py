"""Tests for ImageDisplayWindow: Ctrl+Scroll Z-navigation, overexposure indicator, alignment overlay."""

import numpy as np
import pyqtgraph as pg
import pytest
from qtpy.QtCore import Qt, QPointF, QPoint, QRectF
from qtpy.QtGui import QWheelEvent, QPainter, QImage
from qtpy.QtWidgets import QApplication

from types import SimpleNamespace

from control.core.core import ImageDisplayWindow
from squid.config import CameraPixelFormat


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
    assert image_display_window.btn_overexposure.text() == "Over-exposed Pixels"
    assert image_display_window.btn_overexposure.isCheckable()
    assert not image_display_window.btn_overexposure.isChecked()
    assert image_display_window.overexposure_item is None


@pytest.mark.parametrize("show_lut", [False, True])
def test_overexposure_overlays_pixels_at_the_dtype_maximum_in_red_without_a_camera(qtbot, show_lut):
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


def test_overexposure_ignores_the_contrast_levels(qtbot):
    win = ImageDisplayWindow(show_LUT=True)
    qtbot.addWidget(win)
    win.btn_overexposure.click()
    win.display_image(np.array([[100, 10], [10, 10]], dtype=np.uint16))
    assert win.overexposure_item.image.tolist() == [[0, 0], [0, 0]]

    win.LUTWidget.setLevels(0, 50)

    assert win.overexposure_item.image.tolist() == [[0, 0], [0, 0]]


@pytest.mark.parametrize(
    "pixel_format, dtype, saturation",
    [
        (CameraPixelFormat.MONO16, np.uint16, 65535),
        (CameraPixelFormat.MONO12, np.uint16, 4095 << 4),  # 12-bit data is left-aligned in the container
        (CameraPixelFormat.MONO8, np.uint8, 255),
        (CameraPixelFormat.RGB24, np.uint8, 255),
    ],
)
def test_pixel_format_max_value_is_the_format_maximum_in_the_frame_container(pixel_format, dtype, saturation):
    assert pixel_format.max_value(dtype) == saturation


def test_overexposure_marks_the_camera_saturation_value_not_the_dtype_maximum(qtbot):
    camera = SimpleNamespace(get_pixel_format=lambda: CameraPixelFormat.MONO12)
    win = ImageDisplayWindow(liveController=SimpleNamespace(camera=camera))
    qtbot.addWidget(win)
    win.btn_overexposure.click()

    win.display_image(np.array([[4095 << 4, 65535], [4095, 0]], dtype=np.uint16))

    assert win.overexposure_item.image.tolist() == [[1, 1], [0, 0]]


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


def test_invalidated_frame_is_gone_until_the_next_one_arrives(image_display_window):
    win = image_display_window
    win.display_image(np.zeros((2, 3), dtype=np.uint16))

    win.invalidate_current_image()
    assert win.current_image() is None

    fresh = np.ones((2, 3), dtype=np.uint16)
    win.display_image(fresh)
    assert np.array_equal(win.current_image(), fresh)


# ─── Live image rendered in green while a reference is shown ────────────────

GREEN = (0, 255, 0)
WHITE = (255, 255, 255)


def _rendered_center_color(win):
    """Color painted at the center of the live image, overlays included."""
    img = win.graphics_widget.img
    scene = img.scene()
    scene.prepareForPaint()  # apply any pending ViewBox auto-range now, not halfway through render()
    target = QImage(9, 9, QImage.Format_RGB32)
    target.fill(Qt.black)
    painter = QPainter(target)
    scene.render(painter, QRectF(0, 0, 9, 9), img.mapRectToScene(img.boundingRect()))
    painter.end()
    color = target.pixelColor(4, 4)
    return (color.red(), color.green(), color.blue())


@pytest.mark.parametrize("show_lut", [False, True])
def test_live_image_is_green_only_while_a_reference_is_shown(qtbot, show_lut):
    """Reference in magenta over a green live view: fringes show misalignment, overlap adds up to white."""
    win = ImageDisplayWindow(show_LUT=show_lut)
    qtbot.addWidget(win)
    win.show()
    white_frame = np.full((4, 4), np.iinfo(np.uint16).max, dtype=np.uint16)
    win.display_image(white_frame)
    assert _rendered_center_color(win) == WHITE

    win.show_alignment_reference(np.zeros((4, 4), dtype=np.uint16))
    assert _rendered_center_color(win) == GREEN
    win.display_image(white_frame)  # the tint survives new frames
    assert _rendered_center_color(win) == GREEN
    assert win.live_tint_item.zValue() < win.alignment_reference_item.zValue() < win.frame_item.zValue()

    win.hide_alignment_reference()
    assert win.live_tint_item is None
    assert _rendered_center_color(win) == WHITE


def test_live_tint_follows_the_live_image_size(image_display_window):
    win = image_display_window
    win.show_alignment_reference(np.zeros((4, 4), dtype=np.uint16))

    win.display_image(np.zeros((6, 8), dtype=np.uint16))

    tint = win.live_tint_item
    assert tint.mapRectToParent(tint.boundingRect()) == win.graphics_widget.img.boundingRect()
    assert win.frame_item.rect() == win.graphics_widget.img.boundingRect()


def test_overexposure_mask_stacks_above_the_alignment_overlays(image_display_window):
    win = image_display_window
    win.show_alignment_reference(np.zeros((4, 4), dtype=np.uint16))
    win.set_overexposure_indicator(True)

    assert win.overexposure_item.zValue() > win.alignment_reference_item.zValue() > win.live_tint_item.zValue()


def _endpoints(roi):
    start, end = ImageDisplayWindow._line_roi_endpoints(roi)
    return (start.x(), start.y()), (end.x(), end.y())


def _draw_line(win, start, end):
    win.line_start_pos, win.line_end_pos = start, end
    return win.create_line_roi()


def _profiles(win):
    """{curve name: y data} of the line profile plot."""
    return {item.name(): item.getData()[1] for item in win.line_profiler_plot.listDataItems()}


@pytest.fixture
def profiling_window(qtbot):
    """A window showing a 101x51 frame whose pixel value is its row index, with the line profiler on."""
    win = ImageDisplayWindow()
    qtbot.addWidget(win)
    frame = np.repeat(np.arange(51, dtype=np.uint16)[:, None], 101, axis=1)
    win.display_image(frame)
    win.btn_line_profiler.click()
    return win


def test_line_shortcuts_show_only_while_the_profiler_is_on(image_display_window):
    win = image_display_window
    win.display_image(np.zeros((4, 4), dtype=np.uint8))
    assert all(b.isHidden() for b in win.line_shortcut_buttons)

    win.btn_line_profiler.click()
    assert not any(b.isHidden() for b in win.line_shortcut_buttons)

    win.btn_line_profiler.click()
    assert all(b.isHidden() for b in win.line_shortcut_buttons)


@pytest.mark.parametrize("show_lut", [False, True])
def test_horizontal_line_spans_the_fov_through_the_image_center(qtbot, show_lut):
    win = ImageDisplayWindow(show_LUT=show_lut)
    qtbot.addWidget(win)
    win.display_image(np.zeros((51, 101), dtype=np.uint16))
    win.btn_line_profiler.click()

    win.btn_line_horizontal.click()

    start, end = _endpoints(win.selected_line_roi)
    assert start == pytest.approx((0, 25), abs=1e-6)
    assert end == pytest.approx((100, 25), abs=1e-6)
    assert win.selected_line_roi in win._active_view().addedItems


def test_vertical_line_spans_the_fov_through_the_image_center(profiling_window):
    profiling_window.btn_line_vertical.click()

    start, end = _endpoints(profiling_window.selected_line_roi)
    assert start == pytest.approx((50, 0), abs=1e-6)
    assert end == pytest.approx((50, 50), abs=1e-6)


def test_profile_samples_the_line_itself_not_the_roi_edge(profiling_window):
    """Each pixel holds its row index, so a horizontal line's profile is the row it is drawn on."""
    win = profiling_window
    win.btn_line_horizontal.click()

    (profile,) = _profiles(win).values()
    assert profile == pytest.approx(np.full(len(profile), 25.0))


def test_lines_accumulate_with_one_colored_profile_each(profiling_window):
    win = profiling_window
    win.btn_line_horizontal.click()
    win.btn_line_vertical.click()
    _draw_line(win, (10, 40), (90, 40))

    assert len(win.line_rois) == 3
    assert all(roi in win._active_view().addedItems for roi in win.line_rois)
    profiles = _profiles(win)
    assert list(profiles) == ["Line 1", "Line 2", "Line 3"]
    assert profiles["Line 1"] == pytest.approx(np.full(len(profiles["Line 1"]), 25.0))
    assert profiles["Line 2"] == pytest.approx(np.linspace(0, 50, len(profiles["Line 2"])), abs=0.5)
    assert profiles["Line 3"] == pytest.approx(np.full(len(profiles["Line 3"]), 40.0))
    colors = [win._line_roi_labels[roi][0] for roi in win.line_rois]
    assert len(set(colors)) == 3


def test_newest_line_is_selected_and_drawn_thicker(profiling_window):
    win = profiling_window
    first = _draw_line(win, (10, 10), (20, 10))
    second = _draw_line(win, (10, 20), (20, 20))

    assert win.selected_line_roi is second
    assert second.pen.width() > first.pen.width()

    win.select_line_roi(first)
    assert first.pen.width() > second.pen.width()


@pytest.mark.parametrize("offset_px, selects", [(0, True), (6, True), (20, False)])
def test_clicking_near_a_line_selects_it_on_a_zoomed_out_frame(qtbot, offset_px, selects):
    """On a camera-sized frame a 5 px line is 1-2 screen pixels thick, so a click selects it within a tolerance."""
    win = ImageDisplayWindow(show_LUT=True, autoLevels=True)
    qtbot.addWidget(win)
    win.resize(1000, 800)
    win.show()
    win.display_image(np.zeros((2000, 3000), dtype=np.uint16))
    win.btn_line_profiler.click()
    drawn = _draw_line(win, (500, 500), (1500, 500))
    win.btn_line_horizontal.click()
    QApplication.processEvents()
    view, graphics_view = win._active_view(), win.graphics_widget.view.ui.graphicsView
    assert view.viewPixelSize()[0] > 2  # zoomed out: several image pixels per screen pixel

    target = graphics_view.mapFromScene(view.mapViewToScene(pg.Point(1000, 500))) + QPoint(0, offset_px)
    qtbot.mouseMove(graphics_view.viewport(), target)
    qtbot.mouseClick(graphics_view.viewport(), Qt.LeftButton, pos=target)

    assert (win.selected_line_roi is drawn) == selects


def test_dragging_a_line_selects_it(profiling_window):
    win = profiling_window
    first = _draw_line(win, (10, 10), (20, 10))
    _draw_line(win, (10, 20), (20, 20))

    first.sigRegionChangeStarted.emit(first)

    assert win.selected_line_roi is first


@pytest.mark.parametrize("widen", ["btn_line_fill", None])
def test_new_lines_are_5px_wide_whatever_the_selected_line_is(profiling_window, widen):
    win = profiling_window
    win.btn_line_horizontal.click()
    first = win.selected_line_roi
    if widen:
        getattr(win, widen).click()  # a just-filled band must not pass its width on
    else:
        first.setSize((first.size()[0], 9))
    assert first.size()[1] > 5

    win.btn_line_vertical.click()
    _draw_line(win, (10, 10), (20, 20))

    assert first in win.line_rois
    assert [roi.size()[1] for roi in win.line_rois[1:]] == pytest.approx([5, 5])


def test_draw_button_starts_drawing_another_line(profiling_window):
    win = profiling_window
    win.btn_line_horizontal.click()
    assert not win.is_drawing_line

    win.btn_line_draw.click()

    assert win.is_drawing_line
    assert len(win.line_rois) == 1


def test_shortcut_cancels_a_line_being_drawn(profiling_window):
    win = profiling_window
    assert win.is_drawing_line  # opening the profiler without a line starts drawing mode
    win.line_start_pos = (3, 3)

    win.btn_line_horizontal.click()

    assert not win.is_drawing_line
    assert len(win.line_rois) == 1


def test_remove_requested_removes_only_that_line_and_reselects(profiling_window):
    win = profiling_window
    first = _draw_line(win, (10, 10), (20, 10))
    second = _draw_line(win, (10, 20), (20, 20))

    second.sigRemoveRequested.emit(second)

    assert win.line_rois == [first]
    assert second not in win._active_view().addedItems
    assert win.selected_line_roi is first
    assert list(_profiles(win)) == ["Line 1"]


def test_clear_removes_every_line_and_profile(profiling_window):
    win = profiling_window
    win.btn_line_horizontal.click()
    win.btn_line_vertical.click()
    rois = list(win.line_rois)

    win.btn_line_clear.click()

    assert win.line_rois == []
    assert win.selected_line_roi is None
    assert not any(roi in win._active_view().addedItems for roi in rois)
    assert _profiles(win) == {}


def test_toggling_the_profiler_hides_and_restores_all_lines(profiling_window):
    win = profiling_window
    win.btn_line_horizontal.click()
    win.btn_line_vertical.click()

    win.btn_line_profiler.click()
    assert not any(roi.isVisible() for roi in win.line_rois)

    win.btn_line_profiler.click()
    assert all(roi.isVisible() for roi in win.line_rois)
    assert not win.is_drawing_line


@pytest.mark.parametrize(
    "drawn, expected",
    [
        (((40, 20), (60, 30)), ((0, 0), (100, 50))),  # diagonal through the corners
        (((10, 10), (20, 10)), ((0, 10), (100, 10))),  # off-center horizontal keeps its row
        (((70, 40), (70, 30)), ((70, 50), (70, 0))),  # keeps its direction
    ],
)
def test_full_extends_the_selected_line_to_the_fov_edges(profiling_window, drawn, expected):
    win = profiling_window
    roi = _draw_line(win, *drawn)
    roi.setSize((roi.size()[0], 7), center=(0.5, 0.5))  # widen about the centerline

    win.btn_line_full.click()

    assert win.selected_line_roi is roi
    start, end = _endpoints(roi)
    assert start == pytest.approx(expected[0], abs=1e-6)
    assert end == pytest.approx(expected[1], abs=1e-6)
    assert roi.size()[1] == pytest.approx(7)


def test_full_only_changes_the_selected_line(profiling_window):
    win = profiling_window
    first = _draw_line(win, (10, 10), (20, 10))
    second = _draw_line(win, (10, 20), (20, 20))
    win.select_line_roi(first)

    win.btn_line_full.click()

    assert _endpoints(first) == (pytest.approx((0, 10)), pytest.approx((100, 10)))
    assert _endpoints(second) == (pytest.approx((10, 20)), pytest.approx((20, 20)))


def test_full_without_a_line_does_nothing(profiling_window):
    profiling_window.btn_line_full.click()
    assert profiling_window.line_rois == []


def test_full_leaves_a_line_outside_the_image_alone(profiling_window):
    win = profiling_window
    roi = _draw_line(win, (10, -20), (60, -20))

    win.btn_line_full.click()

    assert _endpoints(roi) == (pytest.approx((10, -20)), pytest.approx((60, -20)))


def test_shortcut_fits_the_profile_plot_to_the_new_line(profiling_window):
    win = profiling_window
    _draw_line(win, (10, 10), (30, 10))
    assert win.line_profiler_plot.viewRange()[0][1] < 50

    win.btn_line_full.click()

    assert win.line_profiler_plot.viewRange()[0][1] >= 100


@pytest.mark.parametrize("shortcut, axis", [("btn_line_horizontal", 0), ("btn_line_vertical", 1)])
def test_fill_fov_averages_the_whole_image_across_an_axis_line(qtbot, shortcut, axis):
    """A filled horizontal line's profile is the mean of every row; a vertical one's, of every column."""
    win = ImageDisplayWindow(show_LUT=True, autoLevels=True)
    qtbot.addWidget(win)
    frame = np.random.default_rng(0).integers(0, 4000, (60, 90), dtype=np.uint16)
    win.display_image(frame)
    win.btn_line_profiler.click()
    getattr(win, shortcut).click()

    win.btn_line_fill.click()

    assert win.selected_line_roi.size()[1] == pytest.approx(frame.shape[axis])
    (profile,) = _profiles(win).values()
    assert profile == pytest.approx(frame.mean(axis=axis))


def test_fill_fov_centers_a_tilted_line_and_averages_only_pixels_on_the_image(profiling_window):
    win = profiling_window
    win.display_image(np.full((51, 101), 100, dtype=np.uint16))
    roi = _draw_line(win, (10, 10), (30, 20))  # slope 1/2, off-center

    win.btn_line_fill.click()

    start, end = _endpoints(roi)
    assert ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2) == pytest.approx((50, 25))
    assert (end[1] - start[1]) / (end[0] - start[0]) == pytest.approx(0.5)
    # The band reaches past the image on a tilted line; samples there must not be counted as zeros
    (profile,) = _profiles(win).values()
    assert np.isfinite(profile).sum() > 0.9 * len(profile)
    assert profile[np.isfinite(profile)] == pytest.approx(100.0)


def test_fill_fov_without_a_line_does_nothing(profiling_window):
    profiling_window.btn_line_fill.click()
    assert profiling_window.line_rois == []


def test_axis_line_off_the_image_reads_nan_there(profiling_window):
    win = profiling_window
    _draw_line(win, (-20, 10), (20, 10))

    (profile,) = _profiles(win).values()
    assert np.isnan(profile[:20]).all()
    assert profile[20:] == pytest.approx(10.0)


# ─── Sensor region outline (laser AF full-sensor view) ───────────────────────


@pytest.mark.parametrize("show_lut", [False, True])
def test_region_outline_marks_the_given_sensor_region(qtbot, show_lut):
    win = ImageDisplayWindow(show_LUT=show_lut)
    qtbot.addWidget(win)
    win.display_image(np.zeros((1080, 1920), dtype=np.uint8))

    win.show_region_outline(800, 900, 1536, 256)

    item = win.region_outline_item
    assert item in win._active_view().addedItems
    assert item.isVisible()
    assert item.rect() == QRectF(800, 900, 1536, 256)


def test_hide_region_outline_keeps_the_frame_outline(image_display_window):
    win = image_display_window
    win.show_region_outline(800, 900, 1536, 256)

    win.hide_region_outline()

    assert not win.region_outline_item.isVisible()
    assert win.frame_item.isVisible()


def test_region_outline_survives_new_frames(image_display_window):
    """The outline marks sensor coordinates; a new live frame must not move or clear it."""
    win = image_display_window
    win.show_region_outline(800, 900, 1536, 256)

    win.display_image(np.zeros((1080, 1920), dtype=np.uint8))

    assert win.region_outline_item.isVisible()
    assert win.region_outline_item.rect() == QRectF(800, 900, 1536, 256)


@pytest.fixture
def shown_window(qtbot):
    """A window with a real viewport, so the view box has a size to fit frames into."""
    win = ImageDisplayWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    return win


def test_a_new_frame_size_refits_the_view(shown_window):
    """Zoomed into the laser AF strip, switching to the full sensor must show the whole sensor."""
    win = shown_window
    win.display_image(np.zeros((256, 1536), dtype=np.uint8))
    view = win._active_view()
    view.setRange(xRange=(100, 200), yRange=(100, 150), padding=0)

    win.display_image(np.zeros((1080, 1920), dtype=np.uint8))

    (x0, x1), (y0, y1) = view.viewRange()
    assert x0 <= 0 and x1 >= 1920
    assert y0 <= 0 and y1 >= 1080


def test_same_size_frames_keep_the_zoom(shown_window):
    win = shown_window
    win.display_image(np.zeros((256, 1536), dtype=np.uint8))
    view = win._active_view()
    view.setRange(xRange=(100, 200), yRange=(100, 150), padding=0)

    win.display_image(np.ones((256, 1536), dtype=np.uint8))

    (x0, x1), (y0, y1) = view.viewRange()
    assert (x0, x1) == pytest.approx((100, 200), abs=1)


# ─── Laser AF spot marks ─────────────────────────────────────────────────────

GREEN_BGR = (0, 255, 0)
GREY_BGR = (128, 128, 128)


def test_mark_spot_draws_the_chosen_spot_green_and_the_other_reflections_grey(image_display_window):
    win = image_display_window
    frame = np.zeros((40, 120), dtype=np.uint8)

    win.mark_spot(frame, 50, 20, other_x=[10, 90])

    shown = win._current_image
    assert tuple(shown[20, 50]) == GREEN_BGR
    assert tuple(shown[20, 10]) == GREY_BGR
    assert tuple(shown[20, 90]) == GREY_BGR
    assert tuple(shown[5, 5]) == (0, 0, 0)
