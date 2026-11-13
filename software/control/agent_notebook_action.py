"""The menu entry Utils > Agent Notebook (see control/agent_notebook_launcher.py for what it starts)."""

import threading
import webbrowser
from typing import Callable, Optional

from qtpy.QtCore import QObject, Signal
from qtpy.QtWidgets import QAction, QMessageBox

import control._def
from control.agent_notebook_launcher import AgentNotebook


class AgentNotebookAction(QObject):
    """Utils > Agent Notebook. The start of the notebook program can take some seconds, so it runs on a thread;
    its result comes back to the GUI thread by a signal (a thread of Python has no Qt event loop of its own)."""

    control_server_started = Signal()  # on the GUI thread: the window can show its control server as on
    _finished = Signal(object)  # the reason of a failure, or None

    def __init__(
        self,
        window,
        setting: str,
        url: str,
        start_control_server: Callable[[], object],
        notebook_class=None,
    ):
        super().__init__(window)
        notebook_class = notebook_class or AgentNotebook
        self._window = window

        def start_control_server_and_tell_the_window():
            start_control_server()  # before the program starts: its agent session needs the server at once
            self.control_server_started.emit()

        self.notebook = notebook_class(setting, url, start_control_server_and_tell_the_window, webbrowser.open)
        self.action = QAction("Agent Notebook", window)
        self.action.setToolTip("Open the lab notebook of an agent session; Squid starts it if it does not run")
        self.action.triggered.connect(self._start)
        self._finished.connect(self._show_result)

    def _start(self) -> None:
        self.action.setEnabled(False)  # one start at a time
        threading.Thread(target=lambda: self._finished.emit(self.notebook.open()), daemon=True).start()

    def _show_result(self, reason) -> None:
        self.action.setEnabled(True)
        if reason:
            QMessageBox.warning(self._window, "Agent Notebook", reason)

    def close(self) -> None:
        """End the notebook program if this Squid started it (the application closes)."""
        self.notebook.close()


def add_agent_notebook_to_utils_menu(
    window, menu_bar, app, start_control_server: Callable[[], object], control_server_action: QAction
) -> Optional[AgentNotebookAction]:
    """Add Utils > Agent Notebook when the settings have a command for it (agent_notebook_command). The window's
    control server entry is shown as on when the notebook started the server, and the notebook program that this
    Squid started ends with the application. Returns the entry, or None when there is none."""
    utils_menu = next((action.menu() for action in menu_bar.actions() if action.text() == "Utils"), None)
    if not control._def.AGENT_NOTEBOOK_COMMAND or utils_menu is None:
        return None
    entry = AgentNotebookAction(
        window, control._def.AGENT_NOTEBOOK_COMMAND, control._def.AGENT_NOTEBOOK_URL, start_control_server
    )
    entry.control_server_started.connect(lambda: control_server_action.setChecked(True))
    utils_menu.addAction(entry.action)
    app.aboutToQuit.connect(entry.close)
    return entry
