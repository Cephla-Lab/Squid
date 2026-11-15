"""What a human sees and can touch in Squid while an agent session exists: the input shield and the banner."""

from typing import Sequence

from qtpy.QtCore import QEvent, QObject, Signal
from qtpy.QtWidgets import QApplication, QHBoxLayout, QInputDialog, QLabel, QPushButton, QToolBar, QWidget

from control.core.agent_session import SessionState

# The events that carry the input of a human. All other types (paint, timers, queued calls, focus, close) pass,
# so the application continues to operate under the shield.
_INPUT_EVENT_TYPES = frozenset(
    {
        QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease,
        QEvent.MouseButtonDblClick,
        QEvent.MouseMove,
        QEvent.Wheel,
        QEvent.KeyPress,
        QEvent.KeyRelease,
        QEvent.Shortcut,
        QEvent.ShortcutOverride,
        QEvent.ContextMenu,
        QEvent.TouchBegin,
        QEvent.TouchUpdate,
        QEvent.TouchEnd,
        QEvent.TabletPress,
        QEvent.TabletMove,
        QEvent.TabletRelease,
        QEvent.DragEnter,
        QEvent.DragMove,
        QEvent.DragLeave,
        QEvent.Drop,
        QEvent.InputMethod,
        QEvent.Gesture,
        QEvent.Type(197),  # QEvent::NativeGesture, by its number: some PyQt5 builds (5.15.6) do not have the name
    }
)


def _is_modal_window(obj: QObject) -> bool:
    return isinstance(obj, QWidget) and obj.isWindow() and obj.isModal()


class InputShield(QObject):
    """Drops the input of the human to everything in the application but `allowed_root` (the banner).

    It is an event filter on the application. It does not disable a widget, so the window looks the same and the
    program can set widget values as usual. A shortcut and a menu action are not widgets: the event of a shortcut
    is dropped unless the shortcut belongs to `allowed_root`.

    A modal dialog blocks the main window, so the banner cannot be clicked while one is open. The shield tells
    about it with `signal_modal_shown`; the input to the dialog stays dropped until `set_modal_passthrough(True)`.
    """

    signal_modal_shown = Signal()

    def __init__(self, allowed_root: QWidget, parent=None) -> None:
        super().__init__(parent)
        self._allowed_root = allowed_root
        self._active = False
        self._modal_passthrough = False

    def set_active(self, active: bool) -> None:
        """Call this on the GUI thread. Each activation starts with no passthrough for modal dialogs."""
        if active == self._active:
            return
        self._active = active
        self._modal_passthrough = False
        if active:
            QApplication.instance().installEventFilter(self)
        else:
            QApplication.instance().removeEventFilter(self)

    def is_active(self) -> bool:
        return self._active

    def set_modal_passthrough(self, passthrough: bool) -> None:
        """Let the input reach modal dialogs. For a dialog that must be answered before the agent's work can end."""
        self._modal_passthrough = passthrough

    def eventFilter(self, receiver: QObject, event: QEvent) -> bool:
        """True drops the event. This runs for each event of the application, so the usual path is one lookup."""
        event_type = event.type()
        if event_type not in _INPUT_EVENT_TYPES:
            if event_type == QEvent.Show and _is_modal_window(receiver):
                self.signal_modal_shown.emit()
            return False
        if receiver.inherits("QWidgetWindow"):
            return False  # Qt sends the event again to the widget in this window; that one is judged
        return not self._may_get_input(receiver)

    def _may_get_input(self, receiver: QObject) -> bool:
        obj = receiver
        while obj is not None:
            if obj is self._allowed_root:
                return True
            if self._modal_passthrough and _is_modal_window(obj):
                return True
            obj = obj.parent()
        return False


PANEL_NOT_LOCKED_WARNING = "The control panel is not locked: do not touch the joystick."

_BANNER_COLORS = {
    SessionState.AGENT: "#ffcc66",
    SessionState.STOPPING: "#ffb07a",
    SessionState.HUMAN: "#b7e4a8",
}


def _banner_text(state: SessionState, waiting_for: Sequence[str]) -> str:
    if state is SessionState.AGENT:
        return "The agent is driving."
    if state is SessionState.STOPPING:
        return f"Stopping. Waiting: {', '.join(waiting_for)}" if waiting_for else "Stopping."
    if state is SessionState.HUMAN:
        return "You are driving. The agent is waiting."
    return ""


class AgentSessionBanner(QToolBar):
    """Says who drives the microscope, and has the buttons with which the human takes and gives the control."""

    signal_take_over = Signal()
    signal_hand_back = Signal(str)  # the line that the human wrote
    signal_end = Signal()
    signal_unlock_anyway = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("Agent session", parent)
        self.setObjectName("agentSessionBanner")
        self.setMovable(False)
        self.setFloatable(False)
        self.toggleViewAction().setVisible(False)  # not in the toolbar menu of the window: it cannot be hidden there

        self.label = QLabel()
        self.warning_label = QLabel()  # shown while the agent has the control and the panel is not locked
        self._panel_warning = ""
        self._state = SessionState.NONE
        self.take_over_button = QPushButton("Take over")
        self.unlock_anyway_button = QPushButton("Unlock anyway")
        self.hand_back_button = QPushButton("Hand back")
        self.end_button = QPushButton("End session")
        self.take_over_button.clicked.connect(self.signal_take_over)
        self.unlock_anyway_button.clicked.connect(self.signal_unlock_anyway)
        self.hand_back_button.clicked.connect(self._ask_and_hand_back)
        self.end_button.clicked.connect(self.signal_end)

        # One widget for all parts: a toolbar shows and hides the widgets that are added to it directly only when it
        # does its layout, and the buttons here must change at once.
        content = QWidget(self)
        layout = QHBoxLayout(content)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.addWidget(self.label)
        for button in (self.take_over_button, self.unlock_anyway_button, self.hand_back_button, self.end_button):
            layout.addWidget(button)
        layout.addWidget(self.warning_label)
        layout.addStretch()
        self.addWidget(content)

        self.show_state(SessionState.NONE)

    def set_panel_warning(self, text: str) -> None:
        """A warning for the time that the agent has the control, or "" for none. For a controller that
        cannot lock its control panel."""
        self._panel_warning = text
        self._show_panel_warning()

    def _show_panel_warning(self) -> None:
        self.warning_label.setText(self._panel_warning)
        agent_has_control = self._state in (SessionState.AGENT, SessionState.STOPPING)
        self.warning_label.setVisible(bool(self._panel_warning) and agent_has_control)

    def show_state(self, state: SessionState, waiting_for: Sequence[str] = (), timed_out: bool = False) -> None:
        self._state = state
        self._show_panel_warning()
        self.label.setText(_banner_text(state, waiting_for))
        self.take_over_button.setVisible(state is SessionState.AGENT)
        self.unlock_anyway_button.setVisible(state is SessionState.STOPPING and timed_out)
        self.hand_back_button.setVisible(state is SessionState.HUMAN)
        self.end_button.setVisible(state is SessionState.HUMAN)
        color = _BANNER_COLORS.get(state)
        if color is not None:
            self.setStyleSheet(
                f"QToolBar#agentSessionBanner {{ background: {color}; }} "
                "QToolBar#agentSessionBanner QLabel { color: black; font-weight: bold; }"
            )
        self.setVisible(state is not SessionState.NONE)

    def _ask_and_hand_back(self) -> None:
        text, accepted = QInputDialog.getText(self, "Hand back", "What did you do? (optional)")
        if accepted:
            self.signal_hand_back.emit(text)
