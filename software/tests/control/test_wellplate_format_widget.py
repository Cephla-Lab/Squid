"""WellplateFormatWidget: cancel-revert and single-emission format selection.

Two long-standing dialog bugs are pinned here:

* Cancelling the calibration dialog used to strand the dropdown on
  "calibrate format...": wellplateChanged overwrote self.wellplate_format with
  "custom" BEFORE the Rejected branch read it back, so findData("custom")
  found the calibrate item itself. (A second, competing revert inside
  WellplateCalibration.reject() parsed the sample name to an int that
  findData() could never match - it is deleted outright.)

* Re-selecting a format after calibration used populate_combo_box() with
  signals live. populate starts with comboBox.clear(), so currentIndexChanged
  fired three times (-1, 0, target) and the index-0 emission momentarily
  reconfigured the app for whatever format is first in the dict.
"""

from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialog

import control._def as _def
import control.widgets
from control.models.sample_format_config import (
    DISABLED_SAMPLE_FORMATS_PATH,
    load_disabled_sample_formats,
    load_disabled_sample_formats_for_edit,
    save_disabled_sample_formats,
)
from control.models.yaml_store import YamlStoreDamaged


@pytest.fixture(autouse=True)
def _isolated_catalog(format_catalog):
    """Every test here runs on a tmp cwd and the shipped table, so this machine's
    disabled list and calibrations can neither leak in nor be written to."""
    return format_catalog


def _make_widget(qtbot):
    widget = control.widgets.WellplateFormatWidget(
        stage=MagicMock(), navigationViewer=MagicMock(), streamHandler=MagicMock(), liveController=MagicMock()
    )
    qtbot.addWidget(widget)
    return widget


def _select_data(widget, data):
    index = widget.comboBox.findData(data)
    assert index >= 0, f"combo has no item with data {data!r}"
    widget.comboBox.setCurrentIndex(index)


def test_cancelling_calibration_restores_previous_format(qtbot):
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")
    assert widget.wellplate_format == "96 well plate"

    with patch.object(control.widgets, "WellplateCalibration") as dialog_cls:
        dialog_cls.return_value.exec_.return_value = QDialog.Rejected
        _select_data(widget, "custom")

    assert widget.wellplate_format == "96 well plate"
    assert widget.comboBox.currentData() == "96 well plate"


def test_cancelling_twice_still_restores(qtbot):
    """The restore must be repeatable - the old bug only bit on the first cancel."""
    widget = _make_widget(qtbot)
    _select_data(widget, "384 well plate")

    with patch.object(control.widgets, "WellplateCalibration") as dialog_cls:
        dialog_cls.return_value.exec_.return_value = QDialog.Rejected
        _select_data(widget, "custom")
        _select_data(widget, "custom")

    assert widget.comboBox.currentData() == "384 well plate"


def test_select_format_silently_emits_exactly_once(qtbot):
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")

    emissions = []
    widget.signalWellplateSettings.connect(lambda *args: emissions.append(args))

    index = widget.select_format_silently("384 well plate")

    assert index >= 0
    assert len(emissions) == 1, f"expected one emission, saw {[e[0] for e in emissions]}"
    assert emissions[0][0].format == "384 well plate"
    assert widget.wellplate_format == "384 well plate"
    assert widget.comboBox.currentData() == "384 well plate"


def test_select_format_silently_never_emits_the_first_dict_entry(qtbot):
    """The spurious index-0 emission is the bug this helper exists to prevent."""
    widget = _make_widget(qtbot)
    first_format = next(iter(_def.WELLPLATE_FORMAT_SETTINGS))
    target = "1536 well plate"
    assert first_format != target

    _select_data(widget, "96 well plate")
    emissions = []
    widget.signalWellplateSettings.connect(lambda settings: emissions.append(settings.format))

    widget.select_format_silently(target)

    assert first_format not in emissions
    assert emissions == [target]


def test_dialog_opened_from_the_dropdown_sees_the_loaded_plate(qtbot, catalog_tree, design_travel_limits):
    """ "calibrate format..." is an action, not a format: while the dialog is open,
    wellplate_format must still name the loaded plate. The holder-rotation mode
    reads it to know which plate to measure on - it used to get "custom"."""
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")
    opened = []

    def operator_selects_holder_mode(dialog):
        dialog.holder_rotation_radio.setChecked(True)
        opened.append(dialog)
        return QDialog.Rejected

    with patch.object(control.widgets.WellplateCalibration, "exec_", operator_selects_holder_mode):
        _select_data(widget, "custom")

    (dialog,) = opened
    assert dialog.holder_session.format == "96 well plate"
    assert [edit.text() for edit in dialog.holder_well_edits] == ["A1", "A12", "H1", "H12"]


# --- disabling formats ("manage formats...") ---------------------------------


def _combo_formats(widget):
    return [widget.comboBox.itemData(i) for i in range(widget.comboBox.count())]


def test_disabled_store_round_trips_and_tolerates_damage():
    assert load_disabled_sample_formats() == set()  # absent: hide nothing

    save_disabled_sample_formats(["6 well plate", "12 well plate"])
    assert load_disabled_sample_formats() == {"6 well plate", "12 well plate"}

    with open(DISABLED_SAMPLE_FORMATS_PATH, "w") as f:
        f.write("disabled: [unterminated" + chr(10))
    assert load_disabled_sample_formats() == set()  # damaged: show every format
    with pytest.raises(YamlStoreDamaged):
        load_disabled_sample_formats_for_edit()  # ...but never edit from that empty set


def test_disabled_formats_are_hidden_from_the_dropdown(qtbot):
    save_disabled_sample_formats(["6 well plate", "1536 well plate"])
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")

    listed = _combo_formats(widget)
    assert "6 well plate" not in listed and "1536 well plate" not in listed
    assert "96 well plate" in listed
    assert listed[-2:] == ["custom", "manage"]
    # Hidden, not deleted: the definition still resolves.
    assert _def.get_wellplate_settings("6 well plate")["rows"] == 2


def test_the_loaded_format_stays_listed_even_when_disabled(qtbot):
    """E.g. the format cached from the last session was disabled since."""
    save_disabled_sample_formats(["96 well plate"])
    with patch.object(control.widgets, "WELLPLATE_FORMAT", "96 well plate"):
        widget = _make_widget(qtbot)

    assert widget.comboBox.currentData() == "96 well plate"


def test_manage_formats_saves_and_keeps_the_loaded_plate(qtbot):
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")
    emissions = []
    widget.signalWellplateSettings.connect(lambda settings: emissions.append(settings.format))

    def operator_unchecks_6_well(dialog):
        for i in range(dialog.format_list.count()):
            if dialog.format_list.item(i).data(Qt.UserRole) == "6 well plate":
                dialog.format_list.item(i).setCheckState(Qt.Unchecked)
        return QDialog.Accepted

    with patch.object(control.widgets.SampleFormatVisibilityDialog, "exec_", operator_unchecks_6_well):
        _select_data(widget, "manage")

    assert load_disabled_sample_formats() == {"6 well plate"}
    assert "6 well plate" not in _combo_formats(widget)
    assert widget.comboBox.currentData() == "96 well plate"
    assert widget.wellplate_format == "96 well plate"
    assert emissions == []  # the plate did not change, so nothing downstream reconfigures


def test_cancelling_manage_formats_changes_nothing(qtbot, format_catalog):
    widget = _make_widget(qtbot)
    _select_data(widget, "384 well plate")

    with patch.object(control.widgets.SampleFormatVisibilityDialog, "exec_", return_value=QDialog.Rejected):
        _select_data(widget, "manage")

    assert not (format_catalog / DISABLED_SAMPLE_FORMATS_PATH).exists()
    assert widget.comboBox.currentData() == "384 well plate"


def test_manage_formats_refuses_to_edit_a_damaged_list(qtbot):
    """Starting from the guarded load's empty set would save over the lab's list."""
    with open(DISABLED_SAMPLE_FORMATS_PATH, "w") as f:
        f.write("version: 1" + chr(10) + "disabled: [unterminated" + chr(10))
    with open(DISABLED_SAMPLE_FORMATS_PATH) as f:
        damaged = f.read()
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")

    with patch.object(control.widgets.SampleFormatVisibilityDialog, "exec_") as exec_, patch.object(
        control.widgets.QMessageBox, "critical"
    ) as critical:
        _select_data(widget, "manage")

    exec_.assert_not_called()
    critical.assert_called_once()
    with open(DISABLED_SAMPLE_FORMATS_PATH) as f:
        assert f.read() == damaged
    assert widget.comboBox.currentData() == "96 well plate"


def test_visibility_dialog_keeps_stored_state_it_does_not_show(qtbot):
    """OK without touching anything must save back exactly what was stored: the
    loaded plate's own disabled state, and names this build has no definition for."""
    stored = {"96 well plate", "6 well plate", "plate from another machine"}
    dialog = control.widgets.SampleFormatVisibilityDialog("96 well plate", stored)
    qtbot.addWidget(dialog)
    items = {
        dialog.format_list.item(i).data(Qt.UserRole): dialog.format_list.item(i)
        for i in range(dialog.format_list.count())
    }

    assert list(items) == list(_def.WELLPLATE_FORMAT_SETTINGS)
    assert "(in use)" in items["96 well plate"].text()
    assert items["96 well plate"].flags() & Qt.ItemIsUserCheckable
    assert set(dialog.disabled_formats()) == stored

    dialog.show_all()
    assert dialog.disabled_formats() == ["plate from another machine"]


def test_select_format_silently_lists_a_disabled_target_without_re_enabling_it(qtbot):
    save_disabled_sample_formats(["384 well plate"])
    widget = _make_widget(qtbot)
    _select_data(widget, "96 well plate")

    assert widget.select_format_silently("384 well plate") >= 0

    assert widget.comboBox.currentData() == "384 well plate"
    assert load_disabled_sample_formats() == {"384 well plate"}  # selecting is not a preference change


def test_saving_a_format_definition_re_enables_it(qtbot):
    """Every definition write - new format, recalibration, parameter update - goes
    through _save_format_definition, so a plate just calibrated or edited must not
    vanish from the dropdown once another plate is loaded."""
    save_disabled_sample_formats(["384 well plate", "6 well plate"])
    calibration = MagicMock(_CARRIED_MEASUREMENTS=control.widgets.WellplateCalibration._CARRIED_MEASUREMENTS)

    control.widgets.WellplateCalibration._save_format_definition(
        calibration, "384 well plate", {"well_spacing_mm": 4.5, "well_size_mm": 3.3}
    )

    assert load_disabled_sample_formats() == {"6 well plate"}


def test_new_format_under_an_existing_hidden_name_asks_first(qtbot):
    """A disabled format is in neither dropdown, so its name looks free - creating
    a 'new' one must not silently replace its definition."""
    save_disabled_sample_formats(["6 well plate"])
    calibration = MagicMock()
    calibration.nameInput.text.return_value = "6 well plate"

    with patch.object(control.widgets.QMessageBox, "question", return_value=control.widgets.QMessageBox.No) as ask:
        control.widgets.WellplateCalibration._calibrate_new_format(calibration)

    assert "disabled" in ask.call_args.args[2]
    calibration._get_calibration_data.assert_not_called()
    calibration._save_format_definition.assert_not_called()


def _widget_on_disabled_96(qtbot):
    save_disabled_sample_formats(["96 well plate"])
    with patch.object(control.widgets, "WELLPLATE_FORMAT", "96 well plate"):
        widget = _make_widget(qtbot)
    assert "96 well plate" in _combo_formats(widget)  # listed while loaded
    return widget


def test_switching_away_from_a_disabled_plate_drops_it(qtbot):
    widget = _widget_on_disabled_96(qtbot)
    emissions = []
    widget.signalWellplateSettings.connect(lambda settings: emissions.append(settings.format))

    _select_data(widget, "384 well plate")

    assert "96 well plate" not in _combo_formats(widget)
    assert widget.comboBox.currentData() == "384 well plate"
    assert emissions == ["384 well plate"]  # the relist itself emits nothing


def test_select_format_silently_drops_a_disabled_outgoing_plate(qtbot):
    widget = _widget_on_disabled_96(qtbot)
    emissions = []
    widget.signalWellplateSettings.connect(lambda settings: emissions.append(settings.format))

    widget.select_format_silently("384 well plate")

    assert "96 well plate" not in _combo_formats(widget)
    assert widget.comboBox.currentData() == "384 well plate"
    assert emissions == ["384 well plate"]


def test_select_format_silently_with_an_unknown_format_keeps_the_loaded_plate(qtbot):
    widget = _widget_on_disabled_96(qtbot)

    assert widget.select_format_silently("no such plate") == -1

    assert widget.comboBox.currentData() == "96 well plate"
    assert widget.wellplate_format == "96 well plate"


def test_hand_edited_bare_plate_sizes_are_read_as_plates():
    with open(DISABLED_SAMPLE_FORMATS_PATH, "w") as f:
        f.write("version: 1" + chr(10) + "disabled: [384, 1536]" + chr(10))

    assert load_disabled_sample_formats() == {"384 well plate", "1536 well plate"}


def test_calibration_dialog_lists_disabled_formats_marked(qtbot):
    """Recalibrating or editing is how a hidden plate comes back, so the
    calibration dialog must still offer it."""
    from qtpy.QtWidgets import QComboBox

    save_disabled_sample_formats(["384 well plate"])
    calibration = MagicMock()
    calibration.existing_format_combo = QComboBox()
    qtbot.addWidget(calibration.existing_format_combo)
    calibration._format_display_name = lambda f: control.widgets.WellplateCalibration._format_display_name(
        calibration, f
    )

    control.widgets.WellplateCalibration.populate_existing_formats(calibration)

    combo = calibration.existing_format_combo
    labels = {combo.itemData(i): combo.itemText(i) for i in range(combo.count())}
    assert labels["384 well plate"].endswith("(disabled)")
    assert not labels["96 well plate"].endswith("(disabled)")


def test_new_format_replacing_an_existing_one_keeps_none_of_its_geometry(qtbot):
    """ "Replace its definition?" must mean replace: the shipped 6-well's derived
    per-axis pitch (39.2 mm) used to win over the new scalar spacing."""
    calibration = MagicMock(_CARRIED_MEASUREMENTS=control.widgets.WellplateCalibration._CARRIED_MEASUREMENTS)
    new_format = {
        "a1_x_mm": 10.0,
        "a1_y_mm": 10.0,
        "a1_x_pixel": 0,
        "a1_y_pixel": 0,
        "well_size_mm": 6.0,
        "well_spacing_mm": 9.0,
        "number_of_skip": 0,
        "rows": 8,
        "cols": 12,
    }

    control.widgets.WellplateCalibration._save_format_definition(calibration, "6 well plate", new_format, replace=True)

    settings = _def.get_wellplate_settings("6 well plate")
    assert (settings["rows"], settings["cols"]) == (8, 12)
    assert settings["well_spacing_x_mm"] == settings["well_spacing_y_mm"] == 9.0
    assert settings["well_size_x_mm"] == settings["well_size_y_mm"] == 6.0
