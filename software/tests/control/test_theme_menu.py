import pytest
from qtpy.QtWidgets import QMessageBox
import control._def, control.gui_hcs, control.microscope, control.theme as theme


@pytest.fixture
def gui(qapp, qtbot, monkeypatch, tmp_path):
    ini = tmp_path / "configuration_test.ini"
    ini.write_text("[GENERAL]\n")
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", str(ini))
    monkeypatch.setattr(control.gui_hcs, "CACHED_CONFIG_FILE_PATH", str(ini), raising=False)
    # The menu reads the saved theme from control._def.UI_THEME at build time; pin it so the machine's ini
    # (which the test process imported) cannot leak a different saved theme into these tests.
    monkeypatch.setattr(control._def, "UI_THEME", "dark")
    # Closing the window at teardown asks "Confirm Exit"; answer yes or teardown crashes offscreen.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    theme.install(qapp, "dark")
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    yield win, ini
    theme.manager().apply("classic")


def test_menu_has_classic_and_dark_only(gui):
    win, _ = gui
    acts = win.appearance_actions
    assert set(acts) == {"classic", "dark"}
    assert all(a.isCheckable() for a in acts.values())
    assert acts["dark"].isChecked() and not acts["classic"].isChecked()
    assert [a.text() for a in acts.values()] == ["Classic", "Dark"]


def test_choosing_classic_saves_and_defers(gui, qapp, monkeypatch):
    win, ini = gui
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)  # "Later"
    win.appearance_actions["classic"].trigger()
    assert "#202020" in qapp.styleSheet()  # still Dark this session
    assert "ui_theme = classic" in ini.read_text()
    assert win.appearance_actions["classic"].text() == "Classic (after restart)"
    assert win.appearance_actions["classic"].isChecked()


def test_choosing_dark_from_classic_saves_and_defers(gui, qapp, monkeypatch):
    win, ini = gui
    theme.manager().apply("classic")
    win._saved_theme = "classic"
    win._refresh_appearance_labels()
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)  # "Later"
    win.appearance_actions["dark"].trigger()
    assert qapp.styleSheet() == ""  # still Classic this session
    assert "ui_theme = dark" in ini.read_text()
    assert win.appearance_actions["dark"].text() == "Dark (after restart)"
    assert win.appearance_actions["dark"].isChecked()


def test_unsaveable_ini_blocks_classic_transition_both_ways(gui, qapp, monkeypatch):
    win, ini = gui
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", None)
    warned, prompted = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: prompted.append(self) or 0)
    # Dark -> Classic: nothing pending, no restart prompt, Dark stays checked
    win.appearance_actions["classic"].trigger()
    assert warned and not prompted
    assert win.appearance_actions["dark"].isChecked()
    assert win.appearance_actions["classic"].text() == "Classic"
    # Classic -> Dark: same rule from the other side (a Classic session whose ini also says classic)
    theme.manager().apply("classic")
    win._saved_theme = "classic"
    win._refresh_appearance_labels()
    warned.clear()
    win.appearance_actions["dark"].trigger()
    assert warned and not prompted and qapp.styleSheet() == ""
    assert win.appearance_actions["classic"].isChecked()


def test_reclicking_the_active_theme_does_nothing(gui, monkeypatch):
    win, ini = gui
    before = ini.read_text()
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", None)  # a save would warn
    warned, applied = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a) or QMessageBox.Ok)
    manager = theme.manager()
    real_apply = manager.apply
    monkeypatch.setattr(manager, "apply", lambda t: applied.append(t) or real_apply(t))
    win.appearance_actions["dark"].trigger()
    assert not warned and not applied
    assert ini.read_text() == before
    assert win.appearance_actions["dark"].isChecked()


def test_picking_the_active_theme_cancels_a_pending_classic(gui, monkeypatch):
    win, ini = gui
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)  # "Later"
    win.appearance_actions["classic"].trigger()
    assert "ui_theme = classic" in ini.read_text()
    win.appearance_actions["dark"].trigger()
    assert "ui_theme = dark" in ini.read_text()
    assert win.appearance_actions["classic"].text() == "Classic"
    assert win.appearance_actions["dark"].isChecked()


def test_failed_cancellation_keeps_the_saved_classic_pending(gui, monkeypatch):
    win, ini = gui
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)  # "Later"
    win.appearance_actions["classic"].trigger()
    assert "ui_theme = classic" in ini.read_text()
    # The ini becomes unwritable, then the operator picks Dark to cancel the pending Classic.
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", None)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]) or QMessageBox.Ok)
    win.appearance_actions["dark"].trigger()
    assert warned and "Classic" in warned[0]
    assert "ui_theme = classic" in ini.read_text()  # the ini still asks for Classic, and the menu must say so
    assert win.appearance_actions["classic"].text() == "Classic (after restart)"
    assert win.appearance_actions["classic"].isChecked()


def test_session_only_theme_is_saved_when_picked_again(gui, monkeypatch):
    win, ini = gui
    # The ini could not be written when Dark was chosen, so it still asks for Classic.
    win._saved_theme = "classic"
    win.appearance_actions["dark"].trigger()
    assert "ui_theme = dark" in ini.read_text()
    assert win.appearance_actions["dark"].isChecked() and win.appearance_actions["dark"].text() == "Dark"


def test_restart_now_calls_restart_application(gui, monkeypatch):
    win, ini = gui
    buttons = []
    real_add = QMessageBox.addButton

    def recording_add(self, *args):
        button = real_add(self, *args)
        buttons.append(button)
        return button

    monkeypatch.setattr(QMessageBox, "addButton", recording_add)
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: buttons[0])  # "Restart Now"
    restarted = []
    monkeypatch.setattr(win, "restart_application", lambda: restarted.append(True))
    win.appearance_actions["classic"].trigger()
    assert restarted == [True]
