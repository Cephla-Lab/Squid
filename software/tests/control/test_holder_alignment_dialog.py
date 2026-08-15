"""The holder-rotation mode of WellplateCalibration: a thin view over the
session - these tests drive the dialog exactly as an operator would."""

import math
import os
from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtWidgets import QApplication, QMessageBox

import control._def as _def
from control.models.plate_holder import load_plate_holder


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tree(tmp_path, monkeypatch):
    import shutil

    repo = os.getcwd()
    (tmp_path / "objective_and_sample_formats").mkdir()
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "cache").mkdir()
    for f in ("sample_formats.csv", "objectives.csv"):
        shutil.copy(
            os.path.join(repo, "objective_and_sample_formats", f), tmp_path / "objective_and_sample_formats" / f
        )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_NEGATIVE", 5.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_POSITIVE", 115.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "Y_NEGATIVE", 4.0)
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "Y_POSITIVE", 76.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 0.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 0.0)
    return tmp_path


def make_dialog(qapp, format_="1536 well plate"):
    from control.widgets import WellplateCalibration

    format_widget = MagicMock()
    format_widget.wellplate_format = format_
    stage = MagicMock()
    live_controller = MagicMock()
    live_controller.is_live = True
    dialog = WellplateCalibration(format_widget, stage, MagicMock(), MagicMock(), live_controller)
    return dialog, stage


def synthetic_corner_touch(session, well, theta_deg=0.37, a1=(11.01, 7.87)):
    t = math.radians(theta_deg)
    px = well.col * session.pitch_x_mm - 0.5 * session.well_size_mm
    py = well.row * session.pitch_y_mm - 0.5 * session.well_size_mm
    return (
        a1[0] + math.cos(t) * px - math.sin(t) * py,
        a1[1] + math.sin(t) * px + math.cos(t) * py,
    )


def set_stage_pos(stage, x, y):
    pos = MagicMock()
    pos.x_mm, pos.y_mm = x, y
    stage.get_pos.return_value = pos


def test_holder_mode_shows_computed_ring_and_square_method(qapp, tree):
    dialog, _ = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)

    assert dialog.holder_widget.isVisibleTo(dialog)
    assert not dialog.calibrateButton.isVisibleTo(dialog)
    assert [edit.text() for edit in dialog.holder_well_edits] == ["A1", "A47", "AE1", "AE47"]
    # square wells: corner picker shown, one touch per well
    assert dialog.holder_corner_combo.isVisibleTo(dialog.holder_widget)
    assert dialog.holder_session.touches_per_well == 1
    assert "0.00 deg assumed" in dialog.holder_status_label.text()
    dialog.close()


def test_record_fit_save_flow(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    session = dialog.holder_session

    assert not dialog.holder_save_button.isEnabled()  # nothing measured yet
    for i, well in enumerate(session.reference_wells):
        set_stage_pos(stage, *synthetic_corner_touch(session, well))
        dialog.holder_record_buttons[i].click()

    assert "Rotation 0.37 deg" in dialog.holder_fit_label.text()
    assert "REJECTED" not in dialog.holder_fit_label.text()
    assert dialog.holder_save_button.isEnabled()

    with patch.object(QMessageBox, "information") as info:
        dialog.holder_save_button.click()
    assert info.called

    holder = load_plate_holder()
    assert holder.rotation_deg == 0.37
    assert holder.measured.on == "1536 well plate"
    assert [p.well for p in holder.measured.points] == ["A1", "A47", "AE1", "AE47"]
    assert "0.37 deg (holder record)" in dialog.holder_status_label.text()
    dialog.close()


def test_misclick_disables_save_with_gate_copy(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    session = dialog.holder_session

    for i, well in enumerate(session.reference_wells):
        x, y = synthetic_corner_touch(session, well, theta_deg=0.1)
        if i == 3:  # touched the well one pitch right of the one named
            x += session.pitch_x_mm
        set_stage_pos(stage, x, y)
        dialog.holder_record_buttons[i].click()

    assert "REJECTED" in dialog.holder_fit_label.text()
    assert not dialog.holder_save_button.isEnabled()
    assert load_plate_holder() is None
    dialog.close()


def test_nominate_through_the_edit(qapp, tree):
    dialog, _ = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)

    dialog.holder_well_edits[0].setText("B2")
    dialog._holder_nominate(0)
    assert dialog.holder_session.reference_wells[0].well_id == "B2"

    # invalid nomination reverts the edit and warns
    dialog.holder_well_edits[0].setText("Z99")
    with patch.object(QMessageBox, "warning") as warn:
        dialog._holder_nominate(0)
    assert warn.called
    assert dialog.holder_well_edits[0].text() == "B2"
    dialog.close()


def test_holdout_records_measured_residual(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    session = dialog.holder_session

    for i, well in enumerate(session.reference_wells):
        set_stage_pos(stage, *synthetic_corner_touch(session, well))
        dialog.holder_record_buttons[i].click()

    fake_well = session._make_well(15, 23)
    set_stage_pos(stage, *synthetic_corner_touch(session, fake_well))
    dialog.holder_holdout_edit.setText("P24")
    dialog.holder_holdout_button.click()
    assert "residual at P24: 0 um" in dialog.holder_holdout_label.text()
    dialog.close()


def test_drive_to_test_well_moves_stage_to_prediction(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    session = dialog.holder_session

    for i, well in enumerate(session.reference_wells):
        set_stage_pos(stage, *synthetic_corner_touch(session, well))
        dialog.holder_record_buttons[i].click()

    dialog.holder_test_button.click()
    worst = session.fit().worst_well
    expected = session.predicted_touch_mm(worst)
    stage.move_x_to.assert_called_once_with(pytest.approx(expected[0]))
    stage.move_y_to.assert_called_once_with(pytest.approx(expected[1]))
    dialog.close()


def test_glass_slide_disables_holder_mode_content(qapp, tree):
    dialog, _ = make_dialog(qapp, format_="glass slide")
    dialog.holder_rotation_radio.setChecked(True)

    assert dialog.holder_session is None
    assert "no grid to calibrate" in dialog.holder_status_label.text()
    assert not dialog.holder_record_buttons[0].isEnabled()
    assert not dialog.holder_save_button.isEnabled()
    dialog.close()


def test_round_plate_hides_corner_picker_and_uses_rim_method(qapp, tree):
    dialog, _ = make_dialog(qapp, format_="96 well plate")
    dialog.holder_rotation_radio.setChecked(True)

    assert dialog.holder_session.touches_per_well == 3
    assert not dialog.holder_corner_combo.isVisibleTo(dialog.holder_widget)
    assert "3 points on the rim" in dialog.holder_method_label.text()
    dialog.close()
