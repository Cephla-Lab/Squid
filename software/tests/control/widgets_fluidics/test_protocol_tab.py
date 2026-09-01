from types import SimpleNamespace

import pytest
from qtpy.QtCore import Qt

import control.widgets_fluidics.protocol_tab as protocol_tab_module
from control.models.fluidics_protocol import ProtocolFile, load_protocol
from control.widgets_fluidics.protocol_tab import ProtocolTab

SETTINGS = {"channels": ["A"], "z_stack": {"nz": 3, "delta_z_um": 0.5}}
COORDS = {"regions": [{"name": "A1", "fovs": [[1.0, 2.0, 3.0], [1.5, 2.0, 3.0]]}]}


def _protocol():
    return ProtocolFile(
        name="demo",
        imaging={"settings": {"cur": SETTINGS}, "coordinates": {"cur": COORDS}},
        sequences=[
            {
                "type": "priming",
                "round": "setup",
                "name": "prime",
                "fluidic_port": 25,
                "flow_rate": 5000,
                "volume": 800,
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "probe",
                "fluidic_port": 1,
                "flow_rate": 2000,
                "volume": 500,
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "wash",
                "fluidic_port": 25,
                "flow_rate": 5000,
                "volume": 1000,
            },
            {
                "type": "imaging",
                "round": "R01",
                "name": "image",
                "folder": "R01_image",
                "settings": "cur",
                "coordinates": "cur",
            },
            {
                "type": "flow_reagent",
                "round": "R02",
                "name": "probe",
                "fluidic_port": 2,
                "flow_rate": 2000,
                "volume": 500,
            },
            {
                "type": "imaging",
                "round": "R02",
                "name": "image",
                "folder": "R02_image",
                "settings": "cur",
                "coordinates": "cur",
            },
        ],
    )


@pytest.fixture
def quiet_dialogs(monkeypatch):
    monkeypatch.setattr(
        protocol_tab_module.QMessageBox, "question", staticmethod(lambda *a, **k: protocol_tab_module.QMessageBox.Yes)
    )
    monkeypatch.setattr(protocol_tab_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(protocol_tab_module.QMessageBox, "information", staticmethod(lambda *a, **k: None))


@pytest.fixture
def tab(qtbot, quiet_dialogs):
    service = SimpleNamespace(initialized=False)
    widget = ProtocolTab(service)
    qtbot.addWidget(widget)
    widget.set_protocol(_protocol())
    return widget


def _child_rows(tab):
    rows = {}
    for gi in range(tab.tree.topLevelItemCount()):
        group = tab.tree.topLevelItem(gi)
        rows[group.text(0)] = group.childCount()
    return rows


def test_tree_groups_rows_by_round(tab):
    assert _child_rows(tab) == {"setup": 1, "R01": 3, "R02": 2}
    assert "✓ valid" in tab.validation_label.text()
    assert "2/2 rows" in tab.settings_summary.text()
    assert "4 FOVs in blocks" in tab.coordinates_summary.text()


def test_include_checkbox_writes_the_model(tab, qtbot):
    group = tab.tree.topLevelItem(1)
    child = group.child(0)  # probe
    changed = []
    tab.signal_protocol_changed.connect(lambda: changed.append(1))
    child.setCheckState(0, Qt.Unchecked)
    assert tab.protocol.sequences[1]["include"] is False
    assert changed


def test_field_edit_updates_the_model_and_apply_to_all(tab, qtbot):
    tab._select_row(1)  # probe R01
    tab._rebuild_field_editor()
    assert tab._apply_all_checkbox is not None
    from qtpy.QtWidgets import QSpinBox

    spin = QSpinBox()
    spin.setRange(0, 100000)
    spin.setValue(7)
    tab._field_edited(1, "fluidic_port", spin)
    assert tab.protocol.sequences[1]["fluidic_port"] == 7
    assert tab.protocol.sequences[4]["fluidic_port"] == 2  # apply-to-all was off

    tab._select_row(1)
    tab._rebuild_field_editor()
    tab._apply_all_checkbox.setChecked(True)
    spin.setValue(9)
    tab._field_edited(1, "fluidic_port", spin)
    assert tab.protocol.sequences[1]["fluidic_port"] == 9
    assert tab.protocol.sequences[4]["fluidic_port"] == 9  # same-named probe in R02
    qtbot.wait(20)  # let the queued re-render run


def test_add_imaging_appends_with_a_rendered_folder(tab):
    tab.tree.clearSelection()
    tab._add_imaging()
    row = tab.protocol.sequences[-1]
    assert row["type"] == "imaging" and row["round"] == "R02"
    assert row["folder"] == "R02_image"  # pattern {round}_{step}; collides -> flagged below
    assert "✗" in tab.validation_label.text()  # duplicate folder is a problem


def test_duplicate_folder_marks_the_row_invalid(tab):
    tab.protocol.sequences.append({"type": "imaging", "round": "R03", "name": "image", "folder": "R01_image"})
    tab._mark_changed()
    assert "✗" in tab.validation_label.text()
    problems = list(tab._problems.values())
    assert any("duplicate" in p for p in problems)


def test_apply_current_settings_to_all_imaging_rows(qtbot, quiet_dialogs):
    service = SimpleNamespace(initialized=False)
    source = lambda: (None, SETTINGS, COORDS)  # noqa: E731
    tab = ProtocolTab(service, current_source=source)
    qtbot.addWidget(tab)
    protocol = _protocol()
    for row in protocol.sequences:
        if row["type"] == "imaging":
            row["settings"] = None
    tab.set_protocol(protocol)

    tab._apply_current("settings")

    keys = {row["settings"] for row in tab.protocol.sequences if row["type"] == "imaging"}
    assert len(keys) == 1
    key = keys.pop()
    assert key.startswith("current_") and key in tab.protocol.imaging.settings
    assert tab.protocol.imaging.settings[key].source == "Wellplate Multipoint"


def test_capture_refuses_zero_fovs(qtbot, quiet_dialogs):
    service = SimpleNamespace(initialized=False)
    source = lambda: (None, SETTINGS, {"regions": []})  # noqa: E731
    tab = ProtocolTab(service, current_source=source)
    qtbot.addWidget(tab)
    tab.set_protocol(_protocol())

    tab._apply_current("coordinates")

    assert {row["coordinates"] for row in tab.protocol.sequences if row["type"] == "imaging"} == {"cur"}


def test_save_load_round_trip_drops_unreferenced_blocks(tab, tmp_path):
    tab.protocol.imaging.settings["orphan"] = tab.protocol.imaging.settings["cur"]
    tab.protocol_path = str(tmp_path / "demo.yaml")
    assert tab.save()

    again = load_protocol(tab.protocol_path)
    assert "orphan" not in again.imaging.settings
    assert [r["type"] for r in again.sequences] == [r["type"] for r in _protocol().sequences]


def test_set_run_locked_freezes_the_structure(tab):
    tab.set_run_locked(True)
    assert not tab.add_step_button.isEnabled()
    assert not tab.add_rounds_button.isEnabled()
    assert not tab.pattern_edit.isEnabled()
    tab.set_run_locked(False)
    assert tab.add_step_button.isEnabled()


def test_imaging_ready_reports_missing_sources(tab):
    assert tab.imaging_ready() is None
    tab.protocol.sequences[3]["coordinates"] = None
    assert "1 imaging row" in tab.imaging_ready()


def test_run_lock_freezes_include_checkboxes(tab):
    tab.set_run_locked(True)
    child = tab.tree.topLevelItem(1).child(0)  # probe R01 (re-rendered by the lock)
    assert not child.flags() & Qt.ItemIsUserCheckable  # not clickable at all
    child.setCheckState(0, Qt.Unchecked)  # even a programmatic change reverts
    assert tab.protocol.sequences[1].get("include", True) is True
    assert child.checkState(0) == Qt.Checked
    tab.set_run_locked(False)
    child = tab.tree.topLevelItem(1).child(0)
    assert child.flags() & Qt.ItemIsUserCheckable


def test_removing_the_selected_row_clears_the_field_editor(tab, qtbot):
    tab._select_row(len(tab.protocol.sequences) - 1)
    tab._rebuild_field_editor()
    assert tab.field_form.rowCount() > 0
    del tab.protocol.sequences[-1]
    tab._mark_changed()
    qtbot.wait(20)
    assert tab.field_form.rowCount() == 0  # no stale editor addressing a vanished row
