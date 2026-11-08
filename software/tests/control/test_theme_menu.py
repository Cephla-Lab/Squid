import pytest
from qtpy.QtWidgets import QMessageBox
import control._def, control.gui_hcs, control.microscope, control.theme as theme


@pytest.fixture
def gui(qapp, qtbot, monkeypatch, tmp_path):
    ini = tmp_path / "configuration_test.ini"
    ini.write_text("[GENERAL]\n")
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", str(ini))
    monkeypatch.setattr(control.gui_hcs, "CACHED_CONFIG_FILE_PATH", str(ini), raising=False)
    # Closing the window at teardown asks "Confirm Exit"; answer yes or teardown crashes offscreen.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    theme.install(qapp, "light")
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    yield win, ini
    theme.manager().apply("classic")


def test_menu_has_three_exclusive_actions(gui):
    win, _ = gui
    acts = win.appearance_actions
    assert set(acts) == {"light", "dark", "classic"}
    assert all(a.isCheckable() for a in acts.values())
    assert acts["light"].isChecked() and not acts["dark"].isChecked()


def test_choosing_dark_applies_and_saves(gui, qapp):
    win, ini = gui
    win.appearance_actions["dark"].trigger()
    assert "#202020" in qapp.styleSheet()
    assert "ui_theme = dark" in ini.read_text()
    assert win.appearance_actions["dark"].text() == "Dark"


def test_choosing_classic_saves_and_defers(gui, qapp, monkeypatch):
    win, ini = gui
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)  # "Later"
    win.appearance_actions["classic"].trigger()
    assert "#f3f3f3" in qapp.styleSheet()  # still Light this session
    assert "ui_theme = classic" in ini.read_text()
    assert win.appearance_actions["classic"].text() == "Classic (after restart)"
    assert win.appearance_actions["classic"].isChecked()


def test_unsaveable_ini_still_applies_dark(gui, qapp, monkeypatch):
    win, ini = gui
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", None)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a) or QMessageBox.Ok)
    win.appearance_actions["dark"].trigger()
    assert "#202020" in qapp.styleSheet() and warned


def test_unsaveable_ini_blocks_classic_transition_both_ways(gui, qapp, monkeypatch):
    win, ini = gui
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", None)
    warned, prompted = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: prompted.append(self) or 0)
    # Light -> Classic: nothing pending, no restart prompt, Light stays checked
    win.appearance_actions["classic"].trigger()
    assert warned and not prompted
    assert win.appearance_actions["light"].isChecked()
    assert win.appearance_actions["classic"].text() == "Classic"
    # Classic -> Light: same rule from the other side
    theme.manager().apply("classic")
    win._pending_theme = None
    win._refresh_appearance_labels()
    warned.clear()
    win.appearance_actions["light"].trigger()
    assert warned and not prompted and qapp.styleSheet() == ""
    assert win.appearance_actions["classic"].isChecked()
