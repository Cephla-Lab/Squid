import pytest
from qtpy.QtWidgets import QMessageBox

import tests.control.gui_test_stubs  # noqa: F401  (same Qt setup as the other dialog tests)
from control.core.config.repository import ConfigRepository
from control.objectives_config import EditorRow
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
    shown = {"warning": [], "question": []}
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown["warning"].append(a[2]) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: shown["question"].append(a[2]) or QMessageBox.No)
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
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"))
    assert dialog.save()
    for profile in ("a", "b"):
        path = tmp_path / "user_profiles" / profile / "channel_configs" / "20x water.yaml"
        assert path.read_text() == f"{profile}-20x"


def test_second_save_is_idempotent(qtbot, repo, no_dialogs, tmp_path):
    dialog = _turret_dialog(qtbot, repo)
    dialog.remove_row(0)
    dialog.add_row(EditorRow("20x water", 20.0, 0.95, 180.0, 1, copy_from="20x"))
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
    dialog.add_row(EditorRow("40x", 40.0, 0.95, 180.0, 1))  # slot 1 is taken by 4x
    assert not dialog.save()
    assert repo.get_objectives_config() is None
    assert "already used" in no_dialogs["warning"][0]


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
