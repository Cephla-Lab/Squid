"""The Objective offsets step of the calibration dialog (spec C §6.8)."""

import dataclasses
import re

import numpy as np
import pytest
from qtpy.QtWidgets import QMessageBox

from control._def import FocusMeasureOperator
from control.core.config.repository import ConfigRepository
from control.models.objective_calibration_config import ImageTransform, build_pixel_record, merge_pixel_records
from control.utils import calculate_focus_measure
from control.widgets_objective_calibration import ORIENTATION_MESSAGE, ObjectiveCalibrationDialog
from squid.objective_calibration.engine import ObjectiveSpec, PixelSizeSummary
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene

SPECS = [ObjectiveSpec("4x", 4, 0.13, 1.625), ObjectiveSpec("10x", 10, 0.3, 0.65), ObjectiveSpec("20x", 20, 0.8, 0.325)]
DECLARED = {
    "4x": {"magnification": 4.0, "na": 0.13, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
    "10x": {"magnification": 10.0, "na": 0.3, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
    "20x": {"magnification": 20.0, "na": 0.8, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
}
MOUNTING = {"4x": ("nimotion_turret", 1), "10x": ("nimotion_turret", 2), "20x": ("nimotion_turret", 3)}
TRUE_PX = {"4x": 1.625 * 1.02, "10x": 0.65 * 0.99, "20x": 0.325 * 1.01}
PARCENTRIC = {"4x": (0.0, 0.0), "10x": (12.0, -7.0), "20x": (-9.0, 6.0)}
Z_FOCUS = {"4x": 0.0, "10x": 5.0, "20x": -3.0}
START_XY = (1000.0, 1000.0)


def lape(crop):
    return float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE))


def _fake(orientation=np.eye(2), scene=None, **kw):
    objectives = {
        spec.name: FakeObjective(
            spec.name,
            spec.magnification,
            spec.na,
            matrix_um_per_px=TRUE_PX[spec.name] * orientation,
            z_focus_um=Z_FOCUS[spec.name],
            parcentric_um=PARCENTRIC[spec.name],
        )
        for spec in SPECS
    }
    return FakeCalibrationHardware(
        objectives,
        scene or FakeScene.random(),
        shape=(160, 200),
        start_objective="10x",
        start_xy_um=START_XY,
        start_z_um=2.0,
        binned_px_um=6.5,
        **kw,
    )


@pytest.fixture
def make_dialog(qtbot, tmp_path):
    def make(hw=None, mounting=MOUNTING, offsets=True, pixel_size=True, range_um=20.0, channels=("BF",), **kw):
        hw = hw or _fake()
        dialog = ObjectiveCalibrationDialog(
            hw,
            SPECS,
            list(channels),
            ConfigRepository(base_path=tmp_path),
            tube_lens_mm=180.0,
            get_declared=lambda name: DECLARED[name],
            fine_metric=lape,
            get_mounting=lambda name: mounting[name],
            pos2_offset_um=0.0,
            **kw,
        )
        qtbot.addWidget(dialog)
        dialog.spin_cycles.setValue(1)
        if range_um is not None:  # None keeps the dialog's own default, for the tests that check it
            dialog.spin_range.setValue(range_um)
        dialog.checkbox_offsets.setChecked(offsets)
        dialog.checkbox_pixel_size.setChecked(pixel_size)
        return dialog, hw

    return make


def _wait(qtbot, dialog):
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=120_000)


def _saved(tmp_path):
    return ConfigRepository(base_path=tmp_path).get_objective_calibration()


def _assert_restored(hw):
    assert hw.current_objective() == "10x"
    assert hw.get_xy_um() == pytest.approx(START_XY)
    assert hw.get_z_um() == pytest.approx(2.0)


def _true_pixel_records(camera_key="fake/camera/0"):
    records = {}
    for name, px in TRUE_PX.items():
        summary = PixelSizeSummary(name, 3, px, 0.001, px * np.eye(2), 0.0, np.eye(2), True, 1.0, 0.3)
        records[name] = build_pixel_record(
            summary,
            measured_at="2026-09-28T09:00:00",
            camera_key=camera_key,
            unbinned_sensor_pixel_um=6.5,
            binned_sensor_pixel_um=6.5,
            binning=1,
            image_transform=ImageTransform(),
            tube_lens_mm=180.0,
            declared=DECLARED[name],
        )
    return records


def test_a_combined_run_measures_offsets_and_apply_saves_them(qtbot, tmp_path, make_dialog):
    fresh, _ = make_dialog(range_um=None)
    assert fresh.spin_range.value() == 100.0  # a first calibration searches ±100 µm (spec C §6.1)
    assert fresh.label_offsets.text() == "Offsets: not calibrated"
    dialog, hw = make_dialog()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    _assert_restored(hw)
    assert "offsets measured for 10x, 20x" in dialog.label_result.text()
    assert dialog.table_offsets.rowCount() == 3 and dialog.table_offsets.item(0, 10).text() == "reference"
    assert dialog.table_steps.rowCount() == 3  # the implied step of every pair, from the candidate save
    # what each pair can measure on this 200 x 160 frame, where the dialog explains equal magnifications
    assert re.fullmatch(
        r"Largest offset each pair can measure on this camera's frame: 4x-10x ±\d+\.\d µm in x, ±\d+\.\d µm in y; "
        r"4x-20x ±\d+\.\d µm in x, ±\d+\.\d µm in y\. Objectives of equal or nearly equal magnification are "
        r"compared through a central crop that keeps 20% of the field free on every side, so for them it is 20% of "
        r"the field\.",
        dialog.label_ranges.text(),
    ), dialog.label_ranges.text()
    dialog.button_apply.click()
    saved = _saved(tmp_path)
    assert saved.offset_calibration.reference_objective == "4x"
    assert saved.offset_calibration.reference_mounting.position == 1
    assert saved.offset_calibration.cycles == 1 and saved.offset_calibration.channel == "BF"
    assert saved.offset_calibration.camera_key.roi == [0, 0, 200, 160]
    assert saved.offset_calibration.camera_key.roi_centre_px == [100.0, 80.0]
    for name in ("10x", "20x"):
        block = saved.objectives[name].offset
        assert (block.dx_um, block.dy_um) == pytest.approx(PARCENTRIC[name], abs=0.5)
        # dz carries the 4x reference's focus scatter (its square is 16 px on this frame): spec C §11 R5
        assert block.dz_um == pytest.approx(Z_FOCUS[name], abs=1.0)
        assert block.mounting.position == MOUNTING[name][1]
        assert block.std_dz_um is None  # one cycle
        assert saved.objectives[name].pixel_size is not None  # the same run's pixel size
    assert "4x" in saved.objectives and saved.objectives["4x"].offset is None
    assert dialog.label_offsets.text().endswith("Z valid, XY valid")
    assert "Saved the pixel size of 10x, 20x, 4x and the Z and XY offsets of 10x, 20x." in dialog.label_result.text()
    log = dialog.log_view.toPlainText()
    assert "cycle 1 offsets: reference 4x" in log and "cycle 1 4x-20x: dx " in log  # the offsets report
    assert "self-similarity " in log and "measurable to ±" in log
    reopened, _ = make_dialog(range_um=None)
    assert reopened.spin_range.value() == 20.0  # the recalibration default: a valid calibration predicts each focus
    assert reopened.table_steps.rowCount() == 3
    assert float(reopened.table_steps.item(0, 2).text()) == pytest.approx(0.005, abs=0.001)  # 4x -> 10x, in mm


def test_offsets_alone_need_a_valid_saved_pixel_calibration(make_dialog):
    dialog, hw = make_dialog(pixel_size=False)
    dialog.button_calibrate.click()
    assert not dialog._running() and hw.snaps == 0
    assert (
        "Not started. Offsets alone need a valid saved pixel calibration for 4x, 10x, 20x" in dialog.label_result.text()
    )


def test_offsets_alone_run_on_the_saved_matrices(qtbot, tmp_path, make_dialog):
    repo = ConfigRepository(base_path=tmp_path)
    repo.save_objective_calibration(merge_pixel_records(None, _true_pixel_records()))
    dialog, hw = make_dialog(pixel_size=False)
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    assert dialog.result.pixel_sizes == {}
    dialog.button_apply.click()
    saved = _saved(tmp_path)
    assert saved.objectives["20x"].offset.dx_um == pytest.approx(-9.0, abs=0.5)
    assert saved.objectives["20x"].pixel_size.measured_at == "2026-09-28T09:00:00"  # untouched
    assert "Saved the Z and XY offsets of 10x, 20x." in dialog.label_result.text()


def test_an_orientation_mismatch_saves_z_without_xy(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(hw=_fake(orientation=np.diag([1.0, -1.0])))
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    assert dialog.label_orientation.text() == ORIENTATION_MESSAGE
    assert dialog.table_offsets.item(1, 1).text() == "12.00"  # XY was still measured (pass-2 alignment)
    dialog.button_apply.click()
    saved = _saved(tmp_path)
    block = saved.objectives["10x"].offset
    assert block.dx_um is None and block.dy_um is None
    assert block.dz_um == pytest.approx(5.0, abs=1.0)
    assert ORIENTATION_MESSAGE in dialog.label_result.text()
    assert "Z valid, XY invalid (XY offsets were not saved" in dialog.label_offsets.text()


def test_the_step_cap_blocks_the_save(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(max_step_um=2.0)
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.button_apply.click()
    text = dialog.label_result.text()
    assert text.startswith("Not saved. Implied Z step 0.00") and "between 4x and 10x exceeds the cap (0.002 mm)" in text
    assert _saved(tmp_path) is None
    assert dialog.table_steps.item(0, 2).background().color().name() == "#ffb3b3"


def test_clear_offsets_keeps_the_pixel_size(qtbot, tmp_path, monkeypatch, make_dialog):
    dialog, hw = make_dialog()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.button_apply.click()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dialog.button_clear_offsets.click()
    saved = _saved(tmp_path)
    assert saved.offset_calibration is None
    assert all(entry.offset is None and entry.pixel_size is not None for entry in saved.objectives.values())
    assert dialog.label_offsets.text() == "Offsets: not calibrated"
    assert dialog.table_steps.rowCount() == 0


def test_a_remounted_objective_invalidates_the_saved_offsets(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.button_apply.click()
    remounted, _ = make_dialog(mounting={**MOUNTING, "20x": ("nimotion_turret", 4)}, range_um=None)
    assert "invalid (20x was remounted since calibration); recalibrate" in remounted.label_offsets.text()
    assert remounted.spin_range.value() == 100.0  # no prediction: the first-calibration range
    assert remounted.table_steps.rowCount() == 0


def test_a_moved_roi_invalidates_xy_only(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.button_apply.click()
    hw.set_roi(40, 0, 200, 160)  # the centre moves from (100, 80) to (140, 80)
    moved, _ = make_dialog(hw=hw, range_um=None)
    assert "Z valid, XY invalid (camera ROI centre changed)" in moved.label_offsets.text()
    assert moved.spin_range.value() == 20.0 and moved.table_steps.rowCount() == 3


def test_a_camera_flip_invalidates_xy_and_a_pixel_size_save_does_not_revalidate_it(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.button_apply.click()
    hw.image_transform = lambda: (None, "Vertical")  # the external review's case: Settings flips the camera
    flipped, _ = make_dialog(hw=hw, offsets=False, range_um=None)
    stale = "Z valid, XY invalid (camera image rotation/flip changed since calibration"
    assert stale in flipped.label_offsets.text()
    flipped.button_calibrate.click()  # pixel size only, under the new transform
    _wait(qtbot, flipped)
    flipped.button_apply.click()
    assert flipped.label_result.text().startswith("Saved the pixel size of 10x, 20x, 4x.")
    assert stale in flipped.label_offsets.text()  # B's save does not rewrite the offsets' key


def test_the_saved_channel_is_the_one_the_run_used(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(channels=("BF", "DAPI"))
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.combo_channel.setCurrentText("DAPI")  # the selector stays editable after the run
    dialog.button_apply.click()
    assert _saved(tmp_path).offset_calibration.channel == "BF"


def test_objectives_outside_the_calibration_are_listed(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog()
    dialog.checkboxes["20x"].setChecked(False)
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.button_apply.click()
    assert dialog.label_uncalibrated.text().startswith("Not in the offset calibration: 20x.")
    assert dialog.table_steps.rowCount() == 3  # every pair of installed objectives, 20x in today's frame


def test_a_failed_cycle_reports_and_saves_nothing(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(hw=_fake(scene=FakeScene.periodic(period_um=16.0)))
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    assert not dialog.button_apply.isEnabled()
    assert "Offsets not measured" in dialog.label_result.text()
    assert "cycle 1 offsets: Offsets not measured" in dialog.log_view.toPlainText()  # the report's failure line
    assert _saved(tmp_path) is None
    _assert_restored(hw)


def test_a_grid_one_period_off_saves_nothing(qtbot, tmp_path, make_dialog):
    # The external review's P1 at the dialog: 10x and 20x search about ±31 x ±25 um on this 200 x 160 frame,
    # and the 20x is one period of a 48 um grid from the 10x (drawn on the textured specimen, which the 10x
    # focuses on). The alias at dx ~ 0 used to finish as an offset ready to save.
    repo = ConfigRepository(base_path=tmp_path)
    repo.save_objective_calibration(merge_pixel_records(None, _true_pixel_records()))
    grid = FakeScene.grid(period_um=48.0)
    hw = _fake(scene=dataclasses.replace(grid, octaves=FakeScene.random().octaves))
    hw.objectives["20x"].parcentric_um = (PARCENTRIC["10x"][0] + 48.0, PARCENTRIC["10x"][1])
    dialog, _ = make_dialog(hw=hw, pixel_size=False)
    dialog.checkboxes["4x"].setChecked(False)
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    assert dialog.result.stopped is None and dialog.offsets == {}
    assert not dialog.button_apply.isEnabled()
    refused = re.search(
        r"cycle 1: Cannot uniquely match \S+ and \S+: the sample remains too similar after a ~(\d+) µm shift",
        dialog.label_result.text(),
    )
    assert refused and float(refused.group(1)) == pytest.approx(48.0, rel=0.1)  # on the specimen: 46 um
    assert "cycle 1 offsets: " + refused.group(0)[len("cycle 1: ") :] in dialog.log_view.toPlainText()
    dialog.button_apply.click()
    assert _saved(tmp_path).offset_calibration is None
    assert dialog.label_ranges.text() == ""  # no pair was measured
    _assert_restored(hw)
