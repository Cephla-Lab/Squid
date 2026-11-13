import copy
import errno
import os
import stat
from pathlib import Path

import pytest
import yaml
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QAbstractItemView, QMessageBox

import tests.control.gui_test_stubs  # noqa: F401  (same Qt setup as the other dialog tests)
import control._def
import control.atomic_file as atomic_file
import control.objectives_config as oc
import control.widgets_objectives as widgets_objectives
import control.core.config.repository as repository
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


def _turret_dialog(qtbot, repo, on_restart=None, running_config=None):
    dialog = ObjectivesEditorDialog(
        repo,
        use_xeryon=False,
        use_turret=True,
        catalog=CATALOG,
        turret_positions={"4x": 1, "10x": 2, "20x": 3},
        xeryon_pos_1=[],
        xeryon_pos_2=[],
        running_config=running_config,
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


def _channel_config(tmp_path, profile, name):
    return tmp_path / "user_profiles" / profile / "channel_configs" / f"{name}.yaml"


def _add_10x_settings(tmp_path):
    for profile in ("a", "b"):
        _channel_config(tmp_path, profile, "10x").write_text(f"{profile}-10x")


def _new_row_combo(dialog):
    return dialog._table.cellWidget(dialog._table.rowCount() - 1, widgets_objectives._COL_COPY)


def _raise_disk_full(*a, **k):
    raise OSError("disk full")


def test_retry_after_a_failed_list_save_copies_from_the_current_selection(
    qtbot, repo, no_dialogs, tmp_path, monkeypatch
):
    # The failed attempt's copies (from 20x) must not survive to be kept by the retry from 10x.
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)  # frees slot 1
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    real_save = repo.save_objectives_config
    monkeypatch.setattr(repo, "save_objectives_config", _raise_disk_full)
    assert not dialog.save()
    monkeypatch.setattr(repo, "save_objectives_config", real_save)  # the fault is fixed
    for profile in ("a", "b"):
        assert not _channel_config(tmp_path, profile, "20x water").exists()

    _new_row_combo(dialog).setCurrentIndex(_new_row_combo(dialog).findText("10x"))
    assert dialog.save()
    for profile in ("a", "b"):
        assert _channel_config(tmp_path, profile, "20x water").read_text() == f"{profile}-10x"


def test_a_copy_failing_in_one_profile_leaves_no_profile_on_another_source(
    qtbot, repo, no_dialogs, tmp_path, monkeypatch
):
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    real_write = repository.write_atomically

    def _fail_in_b(path, data):
        if path.parent.parent.name == "b":
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(repository, "write_atomically", _fail_in_b)
    assert not dialog.save()
    monkeypatch.setattr(repository, "write_atomically", real_write)  # the fault is fixed

    _new_row_combo(dialog).setCurrentIndex(_new_row_combo(dialog).findText("10x"))
    assert dialog.save()
    for profile in ("a", "b"):
        assert _channel_config(tmp_path, profile, "20x water").read_text() == f"{profile}-10x"


def test_a_failed_save_removes_the_copies_of_every_row_it_made(qtbot, repo, no_dialogs, tmp_path, monkeypatch):
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)  # frees slot 1
    dialog.remove_row(0)  # frees slot 2
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    dialog.add_row(EditorRow("10x oil", 10.0, 0.4, 180.0, 2, copy_from="10x"), new=True)
    real_copy = repo.copy_objective_channel_configs

    def _copy(source, target):
        if target == "10x oil":
            raise repository.ChannelCopyError(OSError("disk full"), {})
        return real_copy(source, target)

    monkeypatch.setattr(repo, "copy_objective_channel_configs", _copy)
    assert not dialog.save()
    for profile in ("a", "b"):
        assert not _channel_config(tmp_path, profile, "20x water").exists()


def test_a_copy_that_cannot_be_removed_after_a_failed_save_is_named(qtbot, repo, no_dialogs, tmp_path, monkeypatch):
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    real_save, real_unlink = repo.save_objectives_config, Path.unlink
    monkeypatch.setattr(repo, "save_objectives_config", _raise_disk_full)
    monkeypatch.setattr(Path, "unlink", _unremovable(_channel_config(tmp_path, "a", "20x water")))
    assert not dialog.save()
    message = no_dialogs["critical"][0]
    assert "disk full" in message and "(Permission denied)" in message
    assert message.count(str(_channel_config(tmp_path, "a", "20x water"))) == 1 and "delete" in message
    assert str(_channel_config(tmp_path, "b", "20x water")) not in message  # b's copy was removed

    monkeypatch.setattr(repo, "save_objectives_config", real_save)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert dialog.save()
    for profile in ("a", "b"):
        assert _channel_config(tmp_path, profile, "20x water").read_text() == f"{profile}-20x"


def _unremovable(path_to_keep):
    real_unlink = Path.unlink

    def _unlink(self, missing_ok=False):
        if self == path_to_keep:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))  # shaped like the real one
        real_unlink(self, missing_ok=missing_ok)

    return _unlink


def _fail_copy_in_b_and_keep_as_copy(tmp_path, monkeypatch):
    """Profile a copies, profile b fails, and a's copy cannot be removed. Returns a callable that
    clears the faults it names: "write", "unlink"."""
    real_write, real_unlink = repository.write_atomically, Path.unlink

    def _fail_in_b(path, data):
        if path.parent.parent.name == "b":
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(repository, "write_atomically", _fail_in_b)
    monkeypatch.setattr(Path, "unlink", _unremovable(_channel_config(tmp_path, "a", "20x water")))

    def clear(*faults):
        if "write" in faults:
            monkeypatch.setattr(repository, "write_atomically", real_write)
        if "unlink" in faults:
            monkeypatch.setattr(Path, "unlink", real_unlink)

    return clear


def test_a_copy_left_by_a_failed_save_is_named_and_removed_before_the_retry(
    qtbot, repo, no_dialogs, tmp_path, monkeypatch
):
    # The review's sequence: a copies from 20x, b fails, removing a's copy fails too. Without this,
    # the retry from 10x kept a's 20x settings while b got 10x's, and nothing said so.
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    clear = _fail_copy_in_b_and_keep_as_copy(tmp_path, monkeypatch)
    assert not dialog.save()
    message = no_dialogs["critical"][0]
    assert str(_channel_config(tmp_path, "a", "20x water")) in message and "delete" in message

    clear("write", "unlink")
    _new_row_combo(dialog).setCurrentIndex(_new_row_combo(dialog).findText("10x"))
    assert dialog.save()
    for profile in ("a", "b"):
        assert _channel_config(tmp_path, profile, "20x water").read_text() == f"{profile}-10x"


def test_save_is_refused_while_a_copy_left_by_a_failed_save_cannot_be_removed(
    qtbot, repo, no_dialogs, tmp_path, monkeypatch
):
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    clear = _fail_copy_in_b_and_keep_as_copy(tmp_path, monkeypatch)
    assert not dialog.save()
    clear("write")  # a's copy still cannot be removed

    _new_row_combo(dialog).setCurrentIndex(_new_row_combo(dialog).findText("10x"))
    assert not dialog.save()
    message = no_dialogs["critical"][-1]
    assert str(_channel_config(tmp_path, "a", "20x water")) in message and "delete" in message
    assert repo.get_objectives_config() is None
    assert not _channel_config(tmp_path, "b", "20x water").exists()


def test_any_failure_during_a_copy_undoes_the_save_and_names_what_stays(qtbot, repo, no_dialogs, tmp_path, monkeypatch):
    # Not only OSError: "20x water" copied to a and b, then "10x oil" failed some other way, and
    # a's earlier copy could not be removed. The save must still be undone and the stray named.
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)  # frees slot 1
    dialog.remove_row(0)  # frees slot 2
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    dialog.add_row(EditorRow("10x oil", 10.0, 0.4, 180.0, 2, copy_from="10x"), new=True)
    real_write, real_unlink = repository.write_atomically, Path.unlink

    def _fail_for_10x_oil(path, data):
        if path.name == "10x oil.yaml":
            raise RuntimeError("unexpected")
        real_write(path, data)

    stray = _channel_config(tmp_path, "a", "20x water")
    monkeypatch.setattr(repository, "write_atomically", _fail_for_10x_oil)
    monkeypatch.setattr(Path, "unlink", _unremovable(stray))
    assert not dialog.save()
    message = no_dialogs["critical"][0]
    assert "unexpected" in message and "NOT saved" in message and str(stray) in message
    assert not _channel_config(tmp_path, "b", "20x water").exists()
    assert no_dialogs["question"] == []

    monkeypatch.setattr(repository, "write_atomically", real_write)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    rows = {dialog._table.item(r, widgets_objectives._COL_NAME).text(): r for r in range(dialog._table.rowCount())}
    combo = dialog._table.cellWidget(rows["20x water"], widgets_objectives._COL_COPY)
    combo.setCurrentIndex(combo.findText("10x"))
    assert dialog.save()
    for profile in ("a", "b"):
        assert _channel_config(tmp_path, profile, "20x water").read_text() == f"{profile}-10x"
        assert _channel_config(tmp_path, profile, "10x oil").read_text() == f"{profile}-10x"


def test_any_failure_writing_the_list_undoes_the_copies_and_is_reported(qtbot, repo, no_dialogs, tmp_path, monkeypatch):
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    real_save = repo.save_objectives_config

    def _raise_unexpected(config):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(repo, "save_objectives_config", _raise_unexpected)
    assert not dialog.save()
    assert "unexpected" in no_dialogs["critical"][0] and "not written" in no_dialogs["critical"][0]
    for profile in ("a", "b"):
        assert not _channel_config(tmp_path, profile, "20x water").exists()
    assert _new_row_combo(dialog).isEnabled()  # nothing was saved, so the source can still change

    monkeypatch.setattr(repo, "save_objectives_config", real_save)
    _new_row_combo(dialog).setCurrentIndex(_new_row_combo(dialog).findText("10x"))
    assert dialog.save()
    for profile in ("a", "b"):
        assert _channel_config(tmp_path, profile, "20x water").read_text() == f"{profile}-10x"


def test_closing_with_a_copy_left_by_a_failed_save_names_it(qtbot, repo, no_dialogs, tmp_path, monkeypatch):
    # The refusal to save only lasts while the dialog is open: after closing, adding the objective
    # again would keep the stray copy ("never overwrite"), so closing says which file to delete.
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    _fail_copy_in_b_and_keep_as_copy(tmp_path, monkeypatch)
    assert not dialog.save()
    dialog.reject()
    assert len(no_dialogs["warning"]) == 1
    warning = no_dialogs["warning"][0]
    assert str(_channel_config(tmp_path, "a", "20x water")) in warning and "delete" in warning


def test_closing_after_the_fault_clears_removes_the_leftover_quietly(qtbot, repo, no_dialogs, tmp_path, monkeypatch):
    _add_10x_settings(tmp_path)
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    clear = _fail_copy_in_b_and_keep_as_copy(tmp_path, monkeypatch)
    assert not dialog.save()
    clear("write", "unlink")
    dialog.reject()
    assert no_dialogs["warning"] == []
    assert not _channel_config(tmp_path, "a", "20x water").exists()


def test_copy_source_is_locked_once_the_list_is_saved(qtbot, repo, no_dialogs, tmp_path):
    # After a successful save the copy is in place and "never overwrite" keeps it: a source chosen
    # afterwards would be silently ignored, so it can no longer be chosen.
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"), new=True)
    assert _new_row_combo(dialog).isEnabled()
    assert dialog.save()
    assert not _new_row_combo(dialog).isEnabled()
    assert "20x" in _new_row_combo(dialog).toolTip()
    # Nor does a magnification edit retarget it.
    dialog._table.item(dialog._table.rowCount() - 1, widgets_objectives._COL_MAG).setText("4")
    assert _new_row_combo(dialog).currentText() == "20x"


def test_optics_round_trip_without_rounding(qtbot, repo, no_dialogs):
    path = repo.machine_configs_path / "objectives.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "changer": {"kind": "nimotion_turret"},
        "objectives": [
            {"name": "12x", "magnification": 12.3456789, "na": 0.123456789, "tube_lens_f_mm": 180.123456789, "slot": 1},
            {"name": "20x", "magnification": 20, "na": 0.8, "tube_lens_f_mm": 180, "slot": 2},
        ],
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    dialog = _turret_dialog(qtbot, repo)
    assert dialog._table.item(1, widgets_objectives._COL_MAG).text() == "20"  # whole numbers stay plain
    assert dialog.save()
    saved = oc.load_objectives_config(path)
    assert [(o.magnification, o.na, o.tube_lens_f_mm) for o in saved.objectives] == [
        (12.3456789, 0.123456789, 180.123456789),
        (20.0, 0.8, 180.0),
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
        raise repository.ChannelCopyError(OSError("disk full"), {})

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


_RUNNING_TURRET = {
    "version": 1,
    "changer": {"kind": "nimotion_turret"},
    "objectives": [
        {"name": "10x", "magnification": 10, "na": 0.3, "tube_lens_f_mm": 180, "slot": 2},
        {"name": "20x", "magnification": 20, "na": 0.8, "tube_lens_f_mm": 180, "slot": 3},
    ],
}


def _slot(slot):
    def edit(data):
        data["objectives"][1]["slot"] = slot

    return edit


def _xeryon(data):
    data["changer"]["kind"] = "xeryon"


@pytest.mark.parametrize(
    "edit, reason",
    [
        pytest.param(_slot(99), "must be 1..4", id="slot-out-of-range"),
        pytest.param(_slot(None), "is required", id="slot-missing"),
        pytest.param(_xeryon, "the machine .ini selects nimotion_turret", id="changer-kind"),
    ],
)
def test_a_file_startup_would_refuse_reopens_the_running_list(qtbot, repo, no_dialogs, edit, reason):
    # Hand-edited after startup into a file that still parses: the table must not coerce it (slot 99
    # showed as slot 1, and Save published slot 1). Startup's own validation decides, as on startup.
    running = oc.parse_objectives_config(_RUNNING_TURRET)
    edited = copy.deepcopy(_RUNNING_TURRET)
    edit(edited)
    path = repo.machine_configs_path / "objectives.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(edited), encoding="utf-8")

    dialog = _turret_dialog(qtbot, repo, running_config=running)
    assert [(r.name, r.slot) for r in dialog.rows()] == [("10x", 2), ("20x", 3)]
    assert no_dialogs["warning"] and reason in no_dialogs["warning"][0]

    assert dialog.save()
    assert [(o.name, o.slot) for o in oc.load_objectives_config(path).objectives] == [("10x", 2), ("20x", 3)]


@pytest.mark.skipif(os.name != "posix", reason="only POSIX fsyncs the directory")
def test_save_whose_directory_sync_fails_reports_the_list_as_saved(qtbot, repo, no_dialogs, monkeypatch):
    # The new objectives.yaml is already in place when the directory sync fails, and it applies at
    # the next startup: "not written" would be false. Report it saved, with the durability caveat.
    real_fsync = os.fsync

    def _fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("I/O error")
        real_fsync(fd)

    monkeypatch.setattr(atomic_file.os, "fsync", _fsync)
    dialog = _turret_dialog(qtbot, repo)
    assert dialog.save()
    assert [(o.name, o.slot) for o in repo.get_objectives_config().objectives] == [("4x", 1), ("10x", 2), ("20x", 3)]
    assert no_dialogs["critical"] == []
    assert len(no_dialogs["question"]) == 1
    assert "could not confirm" in no_dialogs["question"][0] and "I/O error" in no_dialogs["question"][0]


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
