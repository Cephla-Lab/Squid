"""The holder-rotation mode of WellplateCalibration: a thin view over the
session - these tests drive the dialog exactly as an operator would."""

import math
from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtWidgets import QMessageBox

import control._def as _def
from control.models.plate_holder import load_plate_holder


# qapp comes from pytest-qt; catalog_tree/design_travel_limits from conftest.
@pytest.fixture
def tree(catalog_tree, design_travel_limits):
    return catalog_tree


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


def record_all_wells(dialog, stage):
    """Touch the four reference wells at a consistent synthetic pose."""
    session = dialog.holder_session
    for i, well in enumerate(session.reference_wells):
        set_stage_pos(stage, *synthetic_corner_touch(session, well))
        dialog.holder_record_buttons[i].click()


def save_a_rotation(dialog, stage):
    record_all_wells(dialog, stage)
    with patch.object(QMessageBox, "information"):
        dialog.holder_save_button.click()


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
    record_all_wells(dialog, stage)

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


def test_go_to_reference_well_drives_to_its_calibrated_center(qapp, tree):
    from control.core.plate_transform import plate_transform_for

    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    well = dialog.holder_session.reference_wells[3]

    dialog.holder_goto_buttons[3].click()

    expected = plate_transform_for("1536 well plate").well_center_mm(well.row, well.col)
    stage.move_x_to.assert_called_once_with(pytest.approx(expected[0]))
    stage.move_y_to.assert_called_once_with(pytest.approx(expected[1]))
    dialog.close()


def test_check_row_is_locked_until_the_fit_exists(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    session = dialog.holder_session
    assert not dialog.holder_check_goto_button.isEnabled()
    assert not dialog.holder_check_button.isEnabled()
    assert dialog.holder_check_edit.text() == ""

    record_all_wells(dialog, stage)

    assert dialog.holder_check_goto_button.isEnabled()
    assert dialog.holder_check_edit.text() == session.fit().worst_well  # suggested
    dialog.close()


def test_check_row_go_to_drives_to_the_prediction_for_the_typed_well(qapp, qtbot, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    session = dialog.holder_session

    record_all_wells(dialog, stage)

    # the suggested (worst) well first...
    worst = session.fit().worst_well
    dialog.holder_check_goto_button.click()
    expected = session.predicted_touch_mm(worst)
    stage.move_x_to.assert_called_once_with(pytest.approx(expected[0]))
    stage.move_y_to.assert_called_once_with(pytest.approx(expected[1]))

    # ...then one the operator typed, which survives the next refresh
    stage.reset_mock()
    dialog.holder_check_edit.selectAll()
    qtbot.keyClicks(dialog.holder_check_edit, "P24")
    dialog.holder_check_goto_button.click()
    expected = session.predicted_touch_mm("P24")
    stage.move_x_to.assert_called_once_with(pytest.approx(expected[0]))
    dialog._holder_refresh()
    assert dialog.holder_check_edit.text() == "P24"
    dialog.close()


def test_check_row_set_point_reports_the_measured_error(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    session = dialog.holder_session

    record_all_wells(dialog, stage)

    fake_well = session._make_well(15, 23)
    set_stage_pos(stage, *synthetic_corner_touch(session, fake_well))
    dialog.holder_check_edit.setText("P24")
    dialog.holder_check_button.click()
    assert "Measured error at P24: 0 um" in dialog.holder_check_label.text()

    # a reference well is refused - it cannot check itself
    dialog.holder_check_edit.setText("A1")
    with patch.object(QMessageBox, "warning") as warn:
        dialog.holder_check_button.click()
    assert warn.called
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


def test_clear_rotation_is_disabled_until_something_is_saved(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    assert not dialog.holder_clear_button.isEnabled()

    save_a_rotation(dialog, stage)
    assert dialog.holder_clear_button.isEnabled()
    dialog.close()


def test_clear_rotation_asks_first_and_keeps_the_points(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    save_a_rotation(dialog, stage)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.No) as question:
        dialog.holder_clear_button.click()
    assert "0.37 deg, measured on 1536 well plate" in question.call_args.args[2]
    assert load_plate_holder().rotation_deg == 0.37  # declined: nothing happened

    with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
        dialog.holder_clear_button.click()
    assert load_plate_holder() is None
    assert "0.00 deg assumed" in dialog.holder_status_label.text()
    assert not dialog.holder_clear_button.isEnabled()
    # the measurement in progress is untouched and can be saved again
    assert "Rotation 0.37 deg" in dialog.holder_fit_label.text()
    assert dialog.holder_save_button.isEnabled()
    dialog.close()


def test_clear_rotation_works_with_a_glass_slide_loaded(qapp, tree):
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    save_a_rotation(dialog, stage)
    dialog.close()

    dialog, _ = make_dialog(qapp, format_="glass slide")
    dialog.holder_rotation_radio.setChecked(True)
    assert dialog.holder_session is None
    assert dialog.holder_clear_button.isEnabled()

    with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
        dialog.holder_clear_button.click()
    assert load_plate_holder() is None
    assert not dialog.holder_clear_button.isEnabled()
    dialog.close()


def test_calibrate_existing_format_combo_skips_formats_without_a_grid(qapp, tree):
    """The glass slide anchors at the current stage position: nothing to
    calibrate, and its definition cannot be saved (spacing must be positive)."""
    dialog, _ = make_dialog(qapp)
    offered = [dialog.existing_format_combo.itemData(i) for i in range(dialog.existing_format_combo.count())]
    assert "glass slide" not in offered
    assert "96 well plate" in offered and "1536 well plate" in offered
    dialog.close()


def test_three_well_plate_hides_the_fourth_row(qapp, tree, strip_format):
    dialog, _ = make_dialog(qapp, format_=strip_format(3))
    dialog.holder_rotation_radio.setChecked(True)

    assert [e.text() for e in dialog.holder_well_edits] == ["A1", "A3", "A2", ""]
    for widgets in (dialog.holder_well_edits, dialog.holder_goto_buttons, dialog.holder_record_buttons):
        assert [w.isVisibleTo(dialog.holder_widget) for w in widgets] == [True, True, True, False]
    assert "each of the 3 wells below" in dialog.holder_method_label.text()
    assert "0/3 wells measured" in dialog.holder_fit_label.text()
    dialog.close()


def test_go_to_refuses_a_fitted_point_outside_travel(qapp, tree, monkeypatch):
    """The nominated well's CENTER was checked; the fitted corner the check row
    drives to can sit outside travel while the center sits inside."""
    dialog, stage = make_dialog(qapp)
    dialog.holder_rotation_radio.setChecked(True)
    record_all_wells(dialog, stage)  # corner = center - half a well in x and y
    s = _def.WELLPLATE_FORMAT_SETTINGS["1536 well plate"]
    monkeypatch.setattr(_def.SOFTWARE_POS_LIMIT, "X_NEGATIVE", s["a1_x_mm"] - 0.1)  # A1's center in, its corner out

    dialog.holder_check_edit.setText("A1")
    with patch.object(QMessageBox, "warning") as warn:
        dialog.holder_check_goto_button.click()
    assert warn.called and "outside the stage travel" in warn.call_args.args[2]
    stage.move_x_to.assert_not_called()
    # measuring there is still allowed: the operator may have jogged to it by hand
    assert dialog.holder_session.holdout_residual_um("B1", (5.0, 9.0)) > 0
    dialog.close()


def test_format_save_refuses_to_replace_a_damaged_user_file(qapp, tree):
    """An unreadable sample_formats_user.yaml may still hold the lab's other
    formats; a calibration must not overwrite it with a one-format store."""
    user_path = tree / "objective_and_sample_formats" / "sample_formats_user.yaml"
    user_path.write_text("formats: {not yaml\n")
    dialog, _ = make_dialog(qapp, format_="96 well plate")

    with pytest.raises(ValueError, match="cannot be read"):
        dialog._save_format_definition("96 well plate", {"a1_x_mm": 11.41, "a1_y_mm": 10.75})
    assert user_path.read_text() == "formats: {not yaml\n"
    dialog.close()
