import numpy as np
import pytest
from qtpy.QtWidgets import QMessageBox

from control._def import FocusMeasureOperator
from control.core.config.repository import ConfigRepository
from control.models.objective_calibration_config import (
    ImageTransform,
    build_pixel_record,
    merge_pixel_records,
)
from control.utils import calculate_focus_measure
import control.widgets_objective_calibration as woc
from control.widgets_objective_calibration import ObjectiveCalibrationDialog
from squid.objective_calibration.engine import ObjectiveSpec, PixelSizeSummary
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene

SPECS = [ObjectiveSpec("4x", 4, 0.13, 1.625), ObjectiveSpec("10x", 10, 0.3, 0.65)]
DECLARED = {
    "4x": {"magnification": 4.0, "na": 0.13, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
    "10x": {"magnification": 10.0, "na": 0.3, "tube_lens_f_mm": 180.0, "model": "", "serial": ""},
}
START_XY = (1000.0, 1000.0)


def lape(crop):
    return float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE))


def _fake(px_10x=0.65 * 0.99):
    objectives = {
        "4x": FakeObjective("4x", 4, 0.13, pixel_um=1.625 * 1.02, z_focus_um=0.0),
        "10x": FakeObjective("10x", 10, 0.3, pixel_um=px_10x, z_focus_um=5.0),
    }
    return FakeCalibrationHardware(
        objectives,
        FakeScene.random(),
        shape=(160, 200),
        start_objective="10x",
        start_xy_um=START_XY,
        start_z_um=2.0,
        binned_px_um=6.5,
    )


@pytest.fixture
def make_dialog(qtbot, tmp_path):
    def make(hw=None, **kw):
        hw = hw or _fake()
        dialog = ObjectiveCalibrationDialog(
            hw,
            SPECS,
            ["BF"],
            ConfigRepository(base_path=tmp_path),
            tube_lens_mm=180.0,
            get_declared=lambda name: DECLARED[name],
            fine_metric=lape,
            **kw,
        )
        qtbot.addWidget(dialog)
        for box in dialog.checkboxes.values():  # unticked by default; the tests calibrate both objectives
            box.setChecked(True)
        dialog.spin_cycles.setValue(1)
        dialog.spin_range.setValue(20.0)
        return dialog, hw

    return make


def _wait(qtbot, dialog):
    qtbot.waitUntil(lambda: not dialog._running(), timeout=120_000)


def _calibration_file(tmp_path):
    return tmp_path / "machine_configs" / "objective_calibration.yaml"


def _assert_restored(hw):
    assert hw.current_objective() == "10x"
    assert hw.get_xy_um() == pytest.approx(START_XY)
    assert hw.get_z_um() == pytest.approx(2.0)


def test_calibrate_then_apply_and_save_writes_the_measured_records(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    _assert_restored(hw)
    assert dialog.button_apply.isEnabled()
    assert not _calibration_file(tmp_path).exists()  # nothing is written before Apply and save
    dialog.button_apply.click()
    saved = ConfigRepository(base_path=tmp_path).get_objective_calibration()
    assert saved.objectives["4x"].pixel_size.pixel_size_um == pytest.approx(1.625 * 1.02, rel=0.005)
    assert saved.objectives["10x"].pixel_size.factor == pytest.approx(0.65 * 0.99 / 6.5, rel=0.005)
    assert saved.pixel_calibration.orientation_matches_mosaic
    assert dialog.validity_labels["4x"].text().endswith(": valid")
    assert not dialog.button_apply.isEnabled()


def test_cannot_close_while_running(qtbot, make_dialog):
    dialog, hw = make_dialog()
    dialog.show()
    dialog.button_calibrate.click()
    assert dialog._running()
    dialog.reject()
    assert dialog.isVisible()
    assert "running" in dialog.label_result.text()
    dialog.button_cancel.click()
    _wait(qtbot, dialog)
    assert "cancelled" in dialog.label_result.text()
    _assert_restored(hw)
    assert not dialog.button_apply.isEnabled()


def test_a_busy_instrument_refuses_to_start(make_dialog):
    dialog, hw = make_dialog(busy_reason=lambda: "Live view is running.")
    dialog.button_calibrate.click()
    assert not dialog._running()
    assert hw.snaps == 0
    assert dialog.label_result.text() == "Not started. Live view is running."


def test_declined_manual_switch_cancels_and_restores(qtbot, tmp_path, monkeypatch, make_dialog):
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Cancel)
    dialog, hw = make_dialog(manual_switch=True)
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    assert "cancelled" in dialog.label_result.text()
    _assert_restored(hw)
    assert not dialog.button_apply.isEnabled()
    assert not _calibration_file(tmp_path).exists()


def test_a_save_blocker_prevents_saving(qtbot, tmp_path, make_dialog):
    dialog, hw = make_dialog(hw=_fake(px_10x=0.65 * 1.2))
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    dialog.button_apply.click()
    assert "Not saved." in dialog.label_result.text()
    assert "10x differs from nominal by +20" in dialog.label_result.text()
    assert not _calibration_file(tmp_path).exists()


def test_an_unreadable_file_disables_apply_and_clear(tmp_path, make_dialog):
    _calibration_file(tmp_path).parent.mkdir(parents=True)
    _calibration_file(tmp_path).write_text("objectives: [broken\n", encoding="utf-8")
    dialog, _ = make_dialog()
    assert not dialog.button_clear.isEnabled()
    assert "cannot be read" in dialog.label_file.text()


def _saved_record(name, px, camera_key):
    summary = PixelSizeSummary(
        objective=name,
        cycles=3,
        pixel_size_um=px,
        std_pixel_size_um=0.001,
        matrix_um_per_px=px * np.eye(2),
        rotation_deg=0.1,
        flip=np.eye(2),
        orientation_matches_mosaic=True,
        anisotropy=1.0,
        fit_residual_um=0.3,
    )
    return build_pixel_record(
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


def test_validity_banner_and_clear_selected(tmp_path, monkeypatch, make_dialog):
    repo = ConfigRepository(base_path=tmp_path)
    repo.save_objective_calibration(
        merge_pixel_records(
            None,
            {"4x": _saved_record("4x", 1.66, "fake/camera/0"), "10x": _saved_record("10x", 0.64, "other/camera/0")},
        )
    )
    dialog, _ = make_dialog()
    assert dialog.validity_labels["4x"].text().endswith(": valid")
    assert (
        "invalid" in dialog.validity_labels["10x"].text() and "camera changed" in dialog.validity_labels["10x"].text()
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dialog.checkboxes["4x"].setChecked(False)
    dialog.button_clear.click()
    saved = repo.get_objective_calibration()
    assert "10x" not in saved.objectives
    assert saved.objectives["4x"].pixel_size.pixel_size_um == pytest.approx(1.66)
    assert "not calibrated" in dialog.validity_labels["10x"].text()


def test_guidance_points_at_texture_not_blank_glass():
    assert "several bar groups of different sizes" in woc.GUIDANCE
    assert "away from its bar groups" not in woc.GUIDANCE


def test_a_failed_restore_asks_the_operator_to_reselect_the_objective(qtbot, make_dialog):
    dialog, hw = make_dialog()
    hw.fail_switch_to = "10x"  # the switch to 10x faults, and so does the restore back to it
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    assert "reselect the objective in the main window" in dialog.label_result.text()


def test_the_log_shows_every_objective_of_every_cycle(qtbot, make_dialog):
    dialog, _ = make_dialog()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    log = dialog.log_view.toPlainText()
    assert "cycle 1 4x: focus" in log and "cycle 1 10x: focus" in log


def test_the_run_shows_the_frames_it_takes(qtbot, make_dialog):
    dialog, hw = make_dialog()
    assert dialog.frame_view.pixmap() is None or dialog.frame_view.pixmap().isNull()
    dialog.button_calibrate.click()
    _wait(qtbot, dialog)
    pixmap = dialog.frame_view.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    assert max(pixmap.width(), pixmap.height()) <= woc.FRAME_VIEW_PX
    assert dialog.frame_caption.text().split(",")[0] in ("4x", "10x")


def test_no_objective_is_selected_by_default(qtbot, tmp_path):
    dialog = ObjectiveCalibrationDialog(
        _fake(), SPECS, ["BF"], ConfigRepository(base_path=tmp_path), tube_lens_mm=180.0,
        get_declared=lambda name: DECLARED[name], fine_metric=lape,
    )
    qtbot.addWidget(dialog)
    assert not any(box.isChecked() for box in dialog.checkboxes.values())
    dialog.button_calibrate.click()
    assert "Select at least one objective" in dialog.label_result.text()
    assert not dialog._running()
