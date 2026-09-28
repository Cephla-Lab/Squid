import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QAbstractItemView, QMessageBox

import tests.control.gui_test_stubs  # noqa: F401  (same Qt setup as the other dialog tests)
import control._def
import control.objectives_config as oc
import control.widgets_objectives as widgets_objectives
from control.core.config.repository import ConfigRepository
from control.objectives_config import EditorRow, ObjectivesConfigError
from control.widgets_objectives import ObjectivesEditorDialog

CATALOG = {
    "4x": {"magnification": 4.0, "NA": 0.13, "tube_lens_f_mm": 180.0},
    "10x": {"magnification": 10.0, "NA": 0.3, "tube_lens_f_mm": 180.0},
    "20x": {"magnification": 20.0, "NA": 0.8, "tube_lens_f_mm": 180.0},
}


@pytest.fixture
def repo(tmp_path):
    for profile in ("a", "b"):
        channel_configs = tmp_path / "user_profiles" / profile / "channel_configs"
        channel_configs.mkdir(parents=True)
        (channel_configs / "20x.yaml").write_text(f"{profile}-20x")
    return ConfigRepository(base_path=tmp_path)


@pytest.fixture
def no_dialogs(monkeypatch):
    shown = {"warning": [], "question": [], "critical": []}
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown["warning"].append(a[2]) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: shown["question"].append(a[2]) or QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown["critical"].append(a[2]) or QMessageBox.Ok)
    return shown


def _turret_dialog(qtbot, repo, on_restart=None):
    dialog = ObjectivesEditorDialog(
        repo,
        use_xeryon=False,
        use_turret=True,
        catalog=CATALOG,
        turret_positions={"4x": 1, "10x": 2, "20x": 3},
        xeryon_pos_1=[],
        xeryon_pos_2=[],
        on_restart=on_restart,
    )
    qtbot.addWidget(dialog)
    return dialog


def test_first_open_seeds_from_the_ini_map(qtbot, repo, no_dialogs):
    dialog = _turret_dialog(qtbot, repo)
    assert [(r.name, r.slot) for r in dialog.rows()] == [("4x", 1), ("10x", 2), ("20x", 3)]


def test_save_writes_the_yaml_and_offers_restart(qtbot, repo, no_dialogs):
    restarted = []
    dialog = _turret_dialog(qtbot, repo, on_restart=lambda: restarted.append(True))
    assert dialog.save()
    assert repo.get_objectives_config() is not None
    assert no_dialogs["question"] and restarted == []  # "No" chosen


def test_restart_now_calls_the_callback(qtbot, repo, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    restarted = []
    dialog = _turret_dialog(qtbot, repo, on_restart=lambda: restarted.append(True))
    assert dialog.save()
    assert restarted == [True]


def test_added_objective_copies_channel_settings(qtbot, repo, no_dialogs, tmp_path):
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)  # frees slot 1
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    assert dialog.save()
    for profile in ("a", "b"):
        path = tmp_path / "user_profiles" / profile / "channel_configs" / "20x water.yaml"
        assert path.read_text() == f"{profile}-20x"


def test_copy_column_is_a_combo_for_a_new_row_preselected_to_nearest_magnification(qtbot, repo, no_dialogs):
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)  # frees slot 1; "4x" stays in the mounted (copy-source) pool regardless
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    combo = dialog._table.cellWidget(dialog._table.rowCount() - 1, widgets_objectives._COL_COPY)
    assert isinstance(combo, widgets_objectives.QComboBox)
    assert [combo.itemText(i) for i in range(combo.count())] == ["4x", "10x", "20x"]
    assert combo.currentText() == "20x"
    # An existing (mounted) row's cell is not a combo.
    assert dialog._table.cellWidget(0, widgets_objectives._COL_COPY) is None


def test_copy_combo_can_be_changed_to_a_different_mounted_objective(qtbot, repo, no_dialogs, tmp_path):
    for profile in ("a", "b"):
        (tmp_path / "user_profiles" / profile / "channel_configs" / "10x.yaml").write_text(f"{profile}-10x")
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)  # frees slot 1
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    combo = dialog._table.cellWidget(dialog._table.rowCount() - 1, widgets_objectives._COL_COPY)
    combo.setCurrentIndex(combo.findText("10x"))
    assert dialog.save()
    for profile in ("a", "b"):
        path = tmp_path / "user_profiles" / profile / "channel_configs" / "20x water.yaml"
        assert path.read_text() == f"{profile}-10x"


def test_second_save_is_idempotent(qtbot, repo, no_dialogs, tmp_path):
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    assert dialog.save()
    first = repo.get_objectives_config()
    assert dialog.save()
    assert repo.get_objectives_config() == first
    assert sorted(p.name for p in (tmp_path / "user_profiles" / "a" / "channel_configs").iterdir()) == [
        "20x water.yaml",
        "20x.yaml",
    ]


def test_invalid_table_is_refused_with_the_startup_message(qtbot, repo, no_dialogs):
    dialog = _turret_dialog(qtbot, repo)
    dialog.add_row(EditorRow("40x", 40.0, 0.95, 180.0, 1), new=True)  # slot 1 is taken by 4x
    assert not dialog.save()
    assert repo.get_objectives_config() is None
    assert "already used" in no_dialogs["warning"][0]


def test_copy_failure_shows_critical_and_does_not_write_yaml(qtbot, repo, no_dialogs, monkeypatch):
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)  # frees slot 1
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)

    def _raise(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(repo, "copy_objective_channel_configs", _raise)
    assert not dialog.save()
    assert repo.get_objectives_config() is None  # the YAML was not written
    assert no_dialogs["critical"] and "disk full" in no_dialogs["critical"][0]
    assert "NOT saved" in no_dialogs["critical"][0]
    assert no_dialogs["question"] == []  # R6(c): no restart prompt on a failed save


def test_save_failure_shows_critical(qtbot, repo, no_dialogs, monkeypatch):
    dialog = _turret_dialog(qtbot, repo)

    def _raise(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(repo, "save_objectives_config", _raise)
    assert not dialog.save()
    assert no_dialogs["critical"] and "permission denied" in no_dialogs["critical"][0]
    assert "not written" in no_dialogs["critical"][0]
    assert no_dialogs["question"] == []  # R6(c): no restart prompt on a failed save


def test_for_current_machine_seeds_from_the_def_module_with_raw_string_lists(qtbot, repo, monkeypatch):
    monkeypatch.setattr(control._def, "USE_XERYON", True)
    monkeypatch.setattr(control._def, "USE_OBJECTIVE_TURRET", False)
    # The shipped Xeryon ini loads these as Python-literal strings, not real lists (see the
    # comment in ObjectivesEditorDialog.for_current_machine).
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_1", "['10x', '20x']")
    monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2", "['4x']")
    monkeypatch.setattr(control._def, "read_objectives_csv", lambda path: CATALOG)

    dialog = ObjectivesEditorDialog.for_current_machine(repo)
    qtbot.addWidget(dialog)

    assert [(r.name, r.slot) for r in dialog.rows()] == [("10x", 1), ("20x", 1), ("4x", 2)]


def test_xeryon_seed_blocks_save_until_one_per_position(qtbot, repo, no_dialogs):
    dialog = ObjectivesEditorDialog(
        repo,
        use_xeryon=True,
        use_turret=False,
        catalog=CATALOG,
        turret_positions={},
        xeryon_pos_1=["10x", "20x"],
        xeryon_pos_2=["4x"],
    )
    qtbot.addWidget(dialog)
    assert dialog.highlighted_rows() == {0, 1}
    assert not dialog.save()
    dialog.remove_row(0)
    assert dialog.highlighted_rows() == set()
    assert dialog.save()


# --- R1/R7: a new row's copy source is always one of the objectives mounted when the dialog
# opened, regardless of rows added or removed during this session ---


def test_second_added_row_offers_only_mounted_objectives_as_copy_source(qtbot, repo, no_dialogs):
    # turret 4x/10x/20x, add 40x, then add 60x: the 60x combo must offer only the mounted
    # names (never "40x", which was added earlier in this dialog session), preselected to
    # the nearest MOUNTED objective (20x, not 40x).
    dialog = _turret_dialog(qtbot, repo)
    dialog.add_row(dialog._new_row("40x", 40.0, 0.95, 180.0), new=True)
    dialog.add_row(dialog._new_row("60x", 60.0, 1.2, 180.0), new=True)
    last = dialog._table.rowCount() - 1
    combo = dialog._table.cellWidget(last, widgets_objectives._COL_COPY)
    assert [combo.itemText(i) for i in range(combo.count())] == ["4x", "10x", "20x"]
    assert combo.currentText() == "20x"


def test_removing_all_mounted_rows_still_offers_them_as_copy_sources(qtbot, repo, no_dialogs):
    # Ruling 13: the copy-source pool is fixed at dialog-open time (existing config or seed) and
    # is not pruned by removals during the session -- an objective's channel-config files stay on
    # disk after it is removed from the table, so it remains a valid source.
    dialog = _turret_dialog(qtbot, repo)
    for _ in range(3):
        dialog.remove_row(0)
    dialog.add_row(dialog._new_row("40x", 40.0, 0.95, 180.0), new=True)
    last = dialog._table.rowCount() - 1
    combo = dialog._table.cellWidget(last, widgets_objectives._COL_COPY)
    assert [combo.itemText(i) for i in range(combo.count())] == ["4x", "10x", "20x"]
    assert combo.currentText() == "20x"  # nearest to 40 among the open-time mounted objectives
    assert dialog.save()


def test_remove_then_add_rename_workflow_can_still_copy_the_removed_objectives_settings(
    qtbot, repo, no_dialogs, monkeypatch, tmp_path
):
    # The locked-name tooltip (R4) prescribes remove-then-add for a rename. Remove "20x" (freeing
    # its slot), then "rename" it by adding a custom "20x oil" at the same magnification: it must
    # still be able to copy "20x"'s settings, even though that row is no longer in the table.
    monkeypatch.setattr(widgets_objectives.QInputDialog, "getText", lambda *a, **k: ("20x oil", True))
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(2)  # "20x", slot 3
    dialog._add_custom()
    row = dialog._table.rowCount() - 1
    combo = dialog._table.cellWidget(row, widgets_objectives._COL_COPY)
    dialog._table.item(row, widgets_objectives._COL_NA).setText("0.8")
    dialog._table.item(row, widgets_objectives._COL_TUBE).setText("180")
    dialog._table.item(row, widgets_objectives._COL_MAG).setText("20")
    assert [combo.itemText(i) for i in range(combo.count())] == ["4x", "10x", "20x"]
    assert combo.currentText() == "20x"
    assert dialog.save()
    for profile in ("a", "b"):
        path = tmp_path / "user_profiles" / profile / "channel_configs" / "20x oil.yaml"
        assert path.read_text() == f"{profile}-20x"


# --- R3: an invalid objectives.yaml at editor-open time falls back to the seed ---


def test_invalid_existing_yaml_shows_a_warning_and_seeds_from_the_constructor_args(
    qtbot, repo, no_dialogs, monkeypatch
):
    def _raise():
        raise ObjectivesConfigError(repo.machine_configs_path / "objectives.yaml", "(file)", "is not valid YAML")

    monkeypatch.setattr(repo, "get_objectives_config", _raise)
    dialog = _turret_dialog(qtbot, repo)
    assert [(r.name, r.slot) for r in dialog.rows()] == [("4x", 1), ("10x", 2), ("20x", 3)]
    assert no_dialogs["warning"] and "not valid YAML" in no_dialogs["warning"][0]


def test_a_file_damaged_after_startup_reopens_the_running_list_not_the_catalog(qtbot, repo, no_dialogs, monkeypatch):
    # The software started from a valid objectives.yaml holding one custom objective (no changer);
    # the file was then damaged. The editor must reopen the list the software runs with: seeding
    # from the catalog would make Save replace the custom objective with every catalog entry.
    running = oc.parse_objectives_config(
        {
            "version": 1,
            "changer": {"kind": "none"},
            "objectives": [{"name": "25x custom", "magnification": 25, "na": 0.75, "tube_lens_f_mm": 180}],
        }
    )
    monkeypatch.setattr(control._def, "USE_XERYON", False)
    monkeypatch.setattr(control._def, "USE_OBJECTIVE_TURRET", False)
    monkeypatch.setattr(control._def, "OBJECTIVES_CONFIG", running)
    monkeypatch.setattr(control._def, "read_objectives_csv", lambda path: CATALOG)

    def _raise():
        raise ObjectivesConfigError(repo.machine_configs_path / "objectives.yaml", "(file)", "is not valid YAML")

    monkeypatch.setattr(repo, "get_objectives_config", _raise)
    dialog = ObjectivesEditorDialog.for_current_machine(repo)
    qtbot.addWidget(dialog)
    assert [(r.name, r.slot) for r in dialog.rows()] == [("25x custom", None)]
    assert no_dialogs["warning"] and "not valid YAML" in no_dialogs["warning"][0]

    assert dialog.save()
    saved = oc.load_objectives_config(repo.machine_configs_path / "objectives.yaml")
    assert [o.name for o in saved.objectives] == ["25x custom"]


# --- R4: a mounted row's name cannot be edited; a newly added row's can ---


def test_mounted_row_name_is_not_editable_but_a_new_rows_is(qtbot, repo, no_dialogs):
    dialog = _turret_dialog(qtbot, repo)
    mounted_item = dialog._table.item(0, widgets_objectives._COL_NAME)
    assert not bool(mounted_item.flags() & Qt.ItemIsEditable)
    dialog.add_row(dialog._new_row("40x", 40.0, 0.95, 180.0), new=True)
    new_item = dialog._table.item(dialog._table.rowCount() - 1, widgets_objectives._COL_NAME)
    assert bool(new_item.flags() & Qt.ItemIsEditable)


def test_editing_a_mounted_name_via_the_table_is_refused(qtbot, repo, no_dialogs):
    # A double-click or F2 goes through QAbstractItemView.edit(index), which Qt refuses (logging
    # "editing failed") and opens no editor when the model reports the index is not editable.
    # edit() itself types nothing, so the real check is that Qt never entered its editing state.
    dialog = _turret_dialog(qtbot, repo)
    index = dialog._table.model().index(0, widgets_objectives._COL_NAME)
    assert not bool(dialog._table.model().flags(index) & Qt.ItemIsEditable)
    dialog._table.edit(index)
    assert dialog._table.state() != QAbstractItemView.EditingState


# --- R5: a new row's copy source tracks its magnification until the user picks one ---


def test_copy_source_tracks_magnification_until_the_user_picks_one(qtbot, repo, no_dialogs, monkeypatch, tmp_path):
    for profile in ("a", "b"):
        (tmp_path / "user_profiles" / profile / "channel_configs" / "10x.yaml").write_text(f"{profile}-10x")
    monkeypatch.setattr(widgets_objectives.QInputDialog, "getText", lambda *a, **k: ("custom", True))
    dialog = _turret_dialog(qtbot, repo)
    dialog._add_custom()
    row = dialog._table.rowCount() - 1
    combo = dialog._table.cellWidget(row, widgets_objectives._COL_COPY)
    dialog._table.item(row, widgets_objectives._COL_NA).setText("0.8")
    dialog._table.item(row, widgets_objectives._COL_TUBE).setText("180")

    dialog._table.item(row, widgets_objectives._COL_MAG).setText("20")
    assert combo.currentText() == "20x"

    combo.setCurrentText("10x")  # the user picks a source explicitly
    dialog._table.item(row, widgets_objectives._COL_MAG).setText("40")
    assert combo.currentText() == "10x"  # no longer auto-updated

    assert dialog.save()
    for profile in ("a", "b"):
        path = tmp_path / "user_profiles" / profile / "channel_configs" / "custom.yaml"
        assert path.read_text() == f"{profile}-10x"
