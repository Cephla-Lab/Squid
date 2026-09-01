"""Tests for sample alignment: reference-image registration, stage offset mapping, and the AlignmentWidget."""

from unittest.mock import MagicMock

import cv2
import numpy as np
import pandas as pd
import pytest
import tifffile
from qtpy.QtWidgets import QFileDialog, QMessageBox

import control._def
from control import utils
from control.gui_hcs import HighContentScreeningGui
from control.widgets import AlignmentWidget, WellplateCalibration


def _textured_image(shape=(128, 128), dtype=np.uint16):
    rng = np.random.default_rng(0)
    return rng.integers(0, np.iinfo(dtype).max, size=shape, dtype=dtype)


@pytest.mark.parametrize("dx, dy", [(5, 0), (0, -3), (-4, 7)])
def test_measure_translation_px_reports_displacement_of_moving_image(dx, dy):
    reference = _textured_image()
    moving = np.roll(reference, shift=(dy, dx), axis=(0, 1))

    assert utils.measure_translation_px(reference, moving) == pytest.approx((dx, dy))


def test_measure_translation_px_rescales_reference_to_moving_resolution():
    """A reference acquired at a different binning must be compared in the live image's pixels."""
    base = _textured_image()
    reference = cv2.resize(base, (256, 256), interpolation=cv2.INTER_NEAREST)
    moving = np.roll(base, shift=(2, 6), axis=(0, 1))

    assert utils.measure_translation_px(reference, moving) == pytest.approx((6, 2))


def test_measure_translation_px_accepts_color_reference_against_mono_live():
    base = _textured_image(dtype=np.uint8)
    reference = np.stack([base] * 3, axis=-1)
    moving = np.roll(base, shift=(-3, 4), axis=(0, 1))

    assert utils.measure_translation_px(reference, moving) == pytest.approx((4, -3))


def test_measure_translation_px_accounts_for_display_center_crop():
    """Below 100% display resolution the live frame is the center crop of the field of view."""
    base = _textured_image()
    live = utils.crop_to_fraction(np.roll(base, shift=(2, 6), axis=(0, 1)), 0.5)

    assert utils.measure_translation_px(base, live, live_crop_fraction=0.5) == pytest.approx((6, 2))


def test_crop_to_fraction_is_the_stream_handler_center_crop():
    image = _textured_image(shape=(100, 120))

    assert np.array_equal(utils.crop_to_fraction(image, 0.5), utils.crop_image(image, 60, 50))


@pytest.mark.parametrize("inverted_objective, expected", [(False, (0.005, -0.010)), (True, (0.005, 0.010))])
def test_image_delta_to_stage_delta_mm_follows_click_to_move_convention(monkeypatch, inverted_objective, expected):
    monkeypatch.setattr(control._def, "INVERTED_OBJECTIVE", inverted_objective)

    assert utils.image_delta_to_stage_delta_mm(10, 20, pixel_size_um=0.5) == pytest.approx(expected)


@pytest.mark.parametrize("inverted_objective, expected_dy", [(False, -0.010), (True, 0.010)])
def test_wellplate_calibration_click_uses_the_shared_stage_convention(monkeypatch, inverted_objective, expected_dy):
    monkeypatch.setattr(control._def, "INVERTED_OBJECTIVE", inverted_objective)
    stub = MagicMock()
    stub.liveController.microscope.get_image_pixel_size_um.return_value = 0.5

    WellplateCalibration.viewerClicked(stub, 10, 20, 640, 480)

    stub.stage.move_x.assert_called_once_with(pytest.approx(0.005))
    stub.stage.move_y.assert_called_once_with(pytest.approx(expected_dy))


# ─── AlignmentWidget ────────────────────────────────────────────────────────

REFERENCE_IMAGE = _textured_image(shape=(32, 32))
COLOR_REFERENCE_IMAGE = np.stack([_textured_image(shape=(32, 32), dtype=np.uint8)] * 3, axis=-1)
CENTER_FOV_POSITION = (2.0, 5.0)


@pytest.fixture
def acquisition_folder(tmp_path, request):
    """A past acquisition with three FOVs in a row; the middle one (index 1) is the region center.

    Parametrize indirectly with (image, extension) to change the stored reference image.
    """
    image, extension = getattr(request, "param", (REFERENCE_IMAGE, "tiff"))
    coords = pd.DataFrame({"region": ["A1"] * 3, "x (mm)": [1.0, 2.0, 3.0], "y (mm)": [5.0] * 3})
    coords.to_csv(tmp_path / "coordinates.csv", index=False)
    (tmp_path / "0").mkdir()
    path = tmp_path / "0" / f"A1_1_0_Fluorescence_405_nm_Ex.{extension}"
    if extension == "tiff":
        tifffile.imwrite(path, image)
    else:
        cv2.imwrite(str(path), image)
    return tmp_path


@pytest.fixture
def widget(qtbot, image_display_window, acquisition_folder, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(acquisition_folder))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    w = AlignmentWidget(image_display_window)
    qtbot.addWidget(w)
    w.enable()
    return w


def _start_alignment(widget):
    widget.btn_align.click()


def _confirm(widget, current_x_mm, current_y_mm):
    widget.btn_align.click()
    widget.set_current_position(current_x_mm, current_y_mm)


def test_align_moves_to_center_fov_and_overlays_its_image(widget, image_display_window):
    moves = []
    widget.signal_move_to_position.connect(lambda x, y: moves.append((x, y)))

    _start_alignment(widget)

    assert moves == [CENTER_FOV_POSITION]
    assert np.array_equal(image_display_window.alignment_reference_item.image, REFERENCE_IMAGE)
    assert widget.btn_align.text() == "Confirm Offset"


@pytest.mark.parametrize("acquisition_folder", [(COLOR_REFERENCE_IMAGE, "bmp")], indirect=True)
def test_color_reference_is_overlaid_as_an_intensity_image(widget, image_display_window):
    """pyqtgraph ignores lookup tables on H x W x 3 data, so a color reference must be reduced first."""
    _start_alignment(widget)

    assert image_display_window.alignment_reference_item.image.ndim == 2


def test_auto_is_only_available_while_a_reference_is_loaded(widget):
    assert not widget.btn_auto.isEnabled()
    _start_alignment(widget)
    assert widget.btn_auto.isEnabled()
    _confirm(widget, *CENTER_FOV_POSITION)
    assert not widget.btn_auto.isEnabled()


def test_auto_requests_registration_against_the_loaded_reference(widget):
    requests = []
    widget.signal_auto_align_requested.connect(requests.append)
    _start_alignment(widget)

    widget.btn_auto.click()

    assert len(requests) == 1
    assert np.array_equal(requests[0], REFERENCE_IMAGE)


def test_confirm_sets_offset_from_stage_displacement_and_hides_overlay(widget, image_display_window):
    offsets = []
    widget.signal_offset_set.connect(lambda x, y: offsets.append((x, y)))
    _start_alignment(widget)

    _confirm(widget, 2.25, 4.9)

    assert offsets == [pytest.approx((0.25, -0.1))]
    assert widget.apply_offset(1.0, 1.0) == pytest.approx((1.25, 0.9))
    assert image_display_window.alignment_reference_item is None
    assert widget.btn_align.text() == "Clear Offset"


def test_clear_removes_offset(widget):
    cleared = []
    widget.signal_offset_cleared.connect(lambda: cleared.append(True))
    _start_alignment(widget)
    _confirm(widget, 2.25, 4.9)

    widget.btn_align.click()

    assert cleared == [True]
    assert not widget.has_offset
    assert widget.apply_offset(1.0, 1.0) == (1.0, 1.0)
    assert widget.btn_align.text() == "Align"


# ─── GUI auto-align handler ─────────────────────────────────────────────────


def _gui_stub(live_image, pixel_size_um, is_live=True, display_resolution_scaling=1.0):
    """HighContentScreeningGui-shaped stub with just what _alignment_auto_align touches."""
    stub = MagicMock()
    stub.imageDisplayWindow.current_image.return_value = live_image
    stub.microscope.get_image_pixel_size_um.return_value = pixel_size_um
    stub.liveController.is_live = is_live
    stub.liveController.display_resolution_scaling = display_resolution_scaling
    # the real method under test must run, not a MagicMock stand-in
    stub._move_stage_by_image_delta = lambda *args: HighContentScreeningGui._move_stage_by_image_delta(stub, *args)
    return stub


def test_auto_align_moves_stage_to_cancel_measured_displacement(monkeypatch):
    monkeypatch.setattr(control._def, "INVERTED_OBJECTIVE", False)
    live = np.roll(REFERENCE_IMAGE, shift=(-3, 4), axis=(0, 1))  # content moved +4 px in x, -3 px in y
    gui = _gui_stub(live, pixel_size_um=0.5)

    HighContentScreeningGui._alignment_auto_align(gui, REFERENCE_IMAGE)

    gui.stage.move_x.assert_called_once_with(pytest.approx(4 * 0.5 / 1000), blocking=False)
    gui.stage.move_y.assert_called_once_with(pytest.approx(3 * 0.5 / 1000), blocking=True)


def test_auto_align_uses_the_display_crop_fraction(monkeypatch):
    monkeypatch.setattr(control._def, "INVERTED_OBJECTIVE", False)
    reference = _textured_image()
    live = utils.crop_to_fraction(np.roll(reference, shift=(0, 4), axis=(0, 1)), 0.5)
    gui = _gui_stub(live, pixel_size_um=0.5, display_resolution_scaling=0.5)

    HighContentScreeningGui._alignment_auto_align(gui, reference)

    gui.stage.move_x.assert_called_once_with(pytest.approx(4 * 0.5 / 1000), blocking=False)


@pytest.mark.parametrize(
    "gui",
    [
        _gui_stub(REFERENCE_IMAGE, pixel_size_um=None),
        _gui_stub(None, pixel_size_um=0.5),
        _gui_stub(REFERENCE_IMAGE, pixel_size_um=0.5, is_live=False),
    ],
    ids=["no pixel size", "no frame", "live stopped"],
)
def test_auto_align_refuses_to_move_without_a_current_live_frame(monkeypatch, gui):
    """After live view stops, current_image() is stale relative to the stage position."""
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))

    HighContentScreeningGui._alignment_auto_align(gui, REFERENCE_IMAGE)

    gui.stage.move_x.assert_not_called()
    gui.stage.move_y.assert_not_called()
    assert len(warnings) == 1
