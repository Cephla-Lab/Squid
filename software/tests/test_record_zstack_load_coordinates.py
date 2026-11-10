"""Record + Z-Stack "Load Coordinates" mode: explicit positions (e.g. smart-acquisition targets) from an
acquisition.yaml, the save/load round trip, and unattended (dialog-free) starts for the Workflow Runner."""

from unittest.mock import MagicMock, patch

import pytest
import yaml

from control.acquisition_yaml_loader import parse_acquisition_yaml
from control.core.record_zstack_controller import _save_record_zstack_yaml
from control.core.smart_acquisition import emit
from control.core.smart_acquisition.targets import Target
from tests.test_record_zstack_widget import _make_stub_controller, simulated_widget_deps  # noqa: F401

REGIONS = [
    {"name": "smart-C5-r0c0", "center_mm": [20.0, 30.0, 3.075], "shape": "Manual", "fovs": [[20.0, 30.0, 3.075]]},
    {"name": "smart-C5-r0c1", "center_mm": [21.0, 30.0, 3.075], "shape": "Manual", "fovs": [[21.0, 30.0, 3.075]]},
]


def _widget(qtbot, deps, tmp_path):
    """A widget set up for a valid z-stack-only run, in the default Select Wells mode."""
    from control.widgets import RecordZStackMultiPointWidget

    w = RecordZStackMultiPointWidget(**deps)
    qtbot.addWidget(w)
    w.lineEdit_savingDir.setText(str(tmp_path))
    w.checkbox_recording.setChecked(False)
    w.checkbox_zstack.setChecked(True)
    w.entry_zmin.setValue(-2.0)
    w.entry_zmax.setValue(2.0)
    w.entry_step.setValue(1.0)
    w._add_zstack_channel_row("BF LED matrix full")
    return w


def _template(qtbot, deps, tmp_path):
    """An acquisition.yaml exactly as the tab's Save Settings writes it."""
    w = _widget(qtbot, deps, tmp_path)
    path = tmp_path / "template" / "acquisition.yaml"
    path.parent.mkdir()
    _save_record_zstack_yaml(w.build_parameters(), str(path), None, {"name": "20x", "camera_binning": [1, 1]})
    return w, str(path)


def test_positions_from_regions_accepts_fovs_or_center_and_rejects_malformed():
    from control.widgets import positions_from_regions

    assert positions_from_regions(REGIONS)[0] == {"name": "smart-C5-r0c0", "fovs": [[20.0, 30.0, 3.075]]}
    assert positions_from_regions([{"name": "a", "center_mm": [1, 2]}]) == [{"name": "a", "fovs": [[1.0, 2.0]]}]
    for bad in ([{"name": "a"}], [{"name": "a", "fovs": [[1.0]]}], [{"name": "a", "fovs": [["x", 2]]}], ["a"]):
        with pytest.raises(ValueError):
            positions_from_regions(bad)


def test_targets_emitted_from_a_record_template_load_into_load_coordinates_mode(qtbot, simulated_widget_deps, tmp_path):
    w, template = _template(qtbot, simulated_widget_deps, tmp_path)
    targets = [Target(x_mm=20.0 + i, y_mm=30.0, z_mm=3.0, label=f"r0c{i}", source_region="C5") for i in range(3)]
    summary = emit.write_targets(
        targets, template, str(tmp_path / "out"), xy_offset_mm=[0.08, -0.94], z_offset_mm=0.075
    )
    assert summary["widget_type"] == "record_zstack"

    # The emitted file keeps the template's recording / z-stack settings and carries the targets.
    parsed = parse_acquisition_yaml(summary["acquisition_yaml"])
    assert parsed.widget_type == "record_zstack"
    assert parsed.xy_mode == "Load Coordinates"
    assert parsed.objective_name is None and parsed.camera_binning is None  # follow-up objective differs
    assert parsed.zstack_enabled and (parsed.z_min_um, parsed.z_max_um, parsed.z_step_um) == (-2.0, 2.0, 1.0)

    scan = simulated_widget_deps["scanCoordinates"]
    scan.reset_mock()
    w._apply_yaml_settings(parsed)

    assert w.combobox_xy_mode.currentText() == "Load Coordinates"
    assert w.label_loaded_positions.text() == "3 position(s) loaded"
    assert not w.label_loaded_positions.isHidden()
    added = [call.args for call in scan.add_region_from_fovs.call_args_list]
    assert [name for name, _fovs in added] == [t.label for t in summary["targets"]]
    assert added[0][1] == [pytest.approx([20.08, 29.06, 3.075])]  # offset applied, z carried per FOV
    scan.set_well_coordinates.assert_not_called()  # the selected wells are not what gets imaged
    assert w.validate() is None


def test_load_coordinates_without_positions_or_outside_limits_is_refused(qtbot, simulated_widget_deps, tmp_path):
    w = _widget(qtbot, simulated_widget_deps, tmp_path)
    w.combobox_xy_mode.setCurrentText("Load Coordinates")
    assert "no positions loaded" in w.validate()
    assert w.label_loaded_positions.text() == "no coordinates loaded"

    scan = simulated_widget_deps["scanCoordinates"]
    scan.validate_coordinates.side_effect = lambda x, y: x < 100
    w.set_loaded_positions([{"name": "ok", "fovs": [[1.0, 2.0]]}, {"name": "far", "fovs": [[500.0, 2.0]]}])
    assert "outside the stage limits: far" in w.validate()


def test_loading_a_wells_file_afterwards_drops_the_loaded_positions(qtbot, simulated_widget_deps, tmp_path):
    from control.acquisition_yaml_loader import RecordZStackYAMLData

    w = _widget(qtbot, simulated_widget_deps, tmp_path)
    w.set_loaded_positions([{"name": "a", "fovs": [[1.0, 2.0]]}])
    assert w.combobox_xy_mode.currentText() == "Load Coordinates"

    w._apply_yaml_settings(RecordZStackYAMLData(widget_type="record_zstack", xy_mode="Select Wells"))
    assert w.combobox_xy_mode.currentText() == "Select Wells"
    assert w.loaded_positions() == []
    assert w.label_loaded_positions.isHidden()


def test_saved_load_coordinates_settings_round_trip(qtbot, simulated_widget_deps, tmp_path):
    from control.widgets import positions_from_regions

    w = _widget(qtbot, simulated_widget_deps, tmp_path)
    positions = positions_from_regions(REGIONS)
    w.set_loaded_positions(positions)
    scan = MagicMock()
    scan.region_fov_coordinates = {p["name"]: [tuple(f) for f in p["fovs"]] for p in positions}
    scan.region_centers = {p["name"]: list(p["fovs"][0]) for p in positions}
    path = tmp_path / "saved.yaml"
    _save_record_zstack_yaml(w.build_parameters(), str(path), scan, {})

    parsed = parse_acquisition_yaml(str(path))
    assert parsed.xy_mode == "Load Coordinates"
    assert positions_from_regions(parsed.wellplate_regions) == positions
    assert yaml.safe_load(path.read_text())["wellplate_scan"]["regions"][0]["shape"] == "Manual"


def test_unattended_start_shows_no_dialog(qtbot, simulated_widget_deps, tmp_path):
    """interactive=False (the Workflow Runner): an invalid start is logged, a recording is not confirmed."""
    ctrl = _make_stub_controller()
    simulated_widget_deps["recordZStackController"] = ctrl
    w = _widget(qtbot, simulated_widget_deps, tmp_path)
    w.btn_startAcquisition.setChecked(True)

    def no_dialog(*args, **kwargs):
        raise AssertionError(f"unexpected dialog: {args[1:3]}")

    with patch("control.widgets.QMessageBox.warning", side_effect=no_dialog), patch(
        "control.widgets.QMessageBox.question", side_effect=no_dialog
    ):
        w.combobox_xy_mode.setCurrentText("Load Coordinates")  # nothing loaded: invalid
        w.toggle_acquisition(True, interactive=False)
        ctrl.run_acquisition.assert_not_called()
        assert not w.btn_startAcquisition.isChecked()  # what tells the runner the start was refused

        w.set_loaded_positions([{"name": "a", "fovs": [[1.0, 2.0, 3.0]]}])
        w.checkbox_recording.setChecked(True)  # would normally ask "Confirm Recording"
        w.btn_startAcquisition.setChecked(True)
        w.toggle_acquisition(True, interactive=False)
    ctrl.run_acquisition.assert_called_once()
    assert ctrl.run_acquisition.call_args.args[0].xy_mode == "Load Coordinates"
