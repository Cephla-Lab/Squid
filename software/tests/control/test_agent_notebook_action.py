"""The menu entry Utils > Agent Notebook: the start runs on a thread, and its result comes back to the window."""

import threading
from unittest.mock import patch

from qtpy.QtWidgets import QAction, QApplication, QMainWindow, QMessageBox

import control._def
import control.agent_notebook_action
from control.agent_notebook_action import AgentNotebookAction, add_agent_notebook_to_utils_menu


class FakeNotebook:
    """Stands in for AgentNotebook: open() gives the reason of a failure, or None."""

    def __init__(self, setting, url, start_control_server, open_page):
        self.setting, self.url = setting, url
        self.start_control_server = start_control_server
        self.reason = None
        self.may_end = threading.Event()
        self.opened_on = []
        self.closed = False

    def open(self):
        self.opened_on.append(threading.current_thread().name)
        self.start_control_server()
        self.may_end.wait(5)
        return self.reason

    def close(self):
        self.closed = True


def make(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    started = []
    entry = AgentNotebookAction(
        window, "the-command", "http://127.0.0.1:1/", lambda: started.append(1), notebook_class=FakeNotebook
    )
    return window, entry, started


def test_the_start_runs_off_the_gui_thread_and_the_entry_waits_for_it(qtbot):
    _, entry, started = make(qtbot)
    shown = []
    entry.control_server_started.connect(lambda: shown.append(threading.current_thread().name))
    assert entry.action.text() == "Agent Notebook" and entry.action.isEnabled()
    entry.action.trigger()
    assert not entry.action.isEnabled()  # one start at a time
    entry.notebook.may_end.set()
    qtbot.waitUntil(entry.action.isEnabled, timeout=5000)
    assert entry.notebook.opened_on and entry.notebook.opened_on[0] != threading.current_thread().name
    assert started == [1]  # the control server was started for the notebook
    qtbot.waitUntil(
        lambda: shown == [threading.current_thread().name], timeout=5000
    )  # the window is told on its thread


def test_a_start_that_fails_shows_its_reason(qtbot):
    window, entry, _ = make(qtbot)
    entry.notebook.reason = "The notebook program ended with code 3."
    entry.notebook.may_end.set()
    with patch.object(QMessageBox, "warning") as warning:
        entry.action.trigger()
        qtbot.waitUntil(entry.action.isEnabled, timeout=5000)
    warning.assert_called_once()
    assert warning.call_args.args[0] is window and "ended with code 3" in warning.call_args.args[2]


def test_a_start_that_works_shows_no_message_and_close_ends_the_program(qtbot):
    _, entry, _ = make(qtbot)
    entry.notebook.may_end.set()
    with patch.object(QMessageBox, "warning") as warning:
        entry.action.trigger()
        qtbot.waitUntil(entry.action.isEnabled, timeout=5000)
    warning.assert_not_called()
    entry.close()
    assert entry.notebook.closed


def window_with_menus(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.menuBar().addMenu("Settings")
    utils = window.menuBar().addMenu("Utils")
    server_entry = QAction("Enable MCP Control Server", window)
    server_entry.setCheckable(True)
    return window, utils, server_entry


def test_with_no_command_in_the_settings_there_is_no_menu_entry(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "AGENT_NOTEBOOK_COMMAND", "")
    window, utils, server_entry = window_with_menus(qtbot)
    entry = add_agent_notebook_to_utils_menu(
        window, window.menuBar(), QApplication.instance(), lambda: None, server_entry
    )
    assert entry is None and [action.text() for action in utils.actions()] == []


def test_with_a_command_the_entry_is_in_the_utils_menu_and_shows_the_control_server_as_on(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "AGENT_NOTEBOOK_COMMAND", "the-command --serve")
    monkeypatch.setattr(control._def, "AGENT_NOTEBOOK_URL", "http://127.0.0.1:1/")
    monkeypatch.setattr(control.agent_notebook_action, "AgentNotebook", FakeNotebook)
    window, utils, server_entry = window_with_menus(qtbot)
    started = []
    entry = add_agent_notebook_to_utils_menu(
        window, window.menuBar(), QApplication.instance(), lambda: started.append(1), server_entry
    )
    assert [action.text() for action in utils.actions()] == ["Agent Notebook"]
    assert (entry.notebook.setting, entry.notebook.url) == ("the-command --serve", "http://127.0.0.1:1/")
    entry.notebook.may_end.set()
    utils.actions()[0].trigger()
    qtbot.waitUntil(server_entry.isChecked, timeout=5000)  # the notebook started the server: the window shows it
    assert started == [1]
