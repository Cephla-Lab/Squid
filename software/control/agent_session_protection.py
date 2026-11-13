"""The protection of the microscope while an agent drives it.

The session (control.core.agent_session) says who drives. This object makes four things agree with it: the input
shield, the lock of the control panel, the stop sequence and the banner. It also does the two changes that give the
control to the agent (begin and hand back), because the protection must be in place before the agent can send a
command.
"""

from typing import Callable, List, Optional

from qtpy.QtCore import QObject, Qt, QThread, Signal, Slot
from qtpy.QtWidgets import QApplication, QMessageBox

import squid.logging
from control.agent_session_stop import AgentSessionStopSequence
from control.agent_session_widgets import AgentSessionBanner, InputShield
from control.core.agent_session import AgentSession, AgentSessionRefused, SessionState

REFUSAL_DIALOG_OPEN = "a dialog or a menu is open in Squid"
REFUSAL_PANEL_LOCK = "could not lock the control panel"
REFUSAL_CLOSING = "Squid is closing"
REFUSAL_NO_ANSWER = "Squid did not answer"
STOP_CAUSE_TAKE_OVER = "take over"
STOP_CAUSE_DIALOG = "a dialog opened in Squid"


class AgentSessionProtection(QObject):
    _signal_session_changed = Signal()
    _signal_transition = Signal(str, str, object)  # kind, note, a list that gets the answer

    def __init__(
        self,
        session: AgentSession,
        banner: AgentSessionBanner,
        shield: InputShield,
        stop_sequence: AgentSessionStopSequence,
        lock_panel: Callable[[], None],
        unlock_panel: Callable[[], None],
        blocker: Callable[[], Optional[str]],
        snapshot: Callable[[bool], dict],
        parent=None,
    ) -> None:
        """lock_panel() locks the control panel for the session and returns when the controller did it; it raises
        if the controller did not. unlock_panel() removes that lock. A second call of one of them changes nothing.
        blocker() says why the agent must not get the control now (or None); it can run on any thread.
        snapshot(with_position) gives the microscope state for a handover."""
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._session = session
        self._banner = banner
        self._shield = shield
        self._stop_sequence = stop_sequence
        self._lock_panel = lock_panel
        self._unlock_panel = unlock_panel
        self._snapshot = snapshot
        self._shut_down = False

        session.set_blocker(blocker)
        session.add_listener(self._on_session_changed)
        self._signal_session_changed.connect(self.sync, Qt.QueuedConnection)
        self._signal_transition.connect(self._transition_for_another_thread, Qt.BlockingQueuedConnection)
        banner.signal_take_over.connect(self._take_over)
        banner.signal_hand_back.connect(self._hand_back)
        banner.signal_end.connect(self._end)
        banner.signal_unlock_anyway.connect(stop_sequence.unlock_anyway)
        stop_sequence.signal_waiting_changed.connect(self._on_stop_sequence_news)
        stop_sequence.signal_timed_out.connect(self._on_stop_sequence_news)
        shield.signal_modal_shown.connect(self._on_modal_shown)
        self.sync()

    # --- the session changed ---

    def _on_session_changed(self, old: SessionState, new: SessionState) -> None:
        """Session listener, on the thread that made the change. It only tells the GUI thread that something
        changed. `old` and `new` are not used: two changes from two threads can arrive in the other order."""
        self._signal_session_changed.emit()

    def _on_stop_sequence_news(self, reasons: List[str]) -> None:
        self.sync()

    @Slot()
    def sync(self) -> None:
        """Make the shield, the stop sequence, the banner and the panel lock agree with the session state at this
        moment. It reads the state itself, so a call at any time, and any number of calls, is correct. GUI thread."""
        if self._shut_down:
            return
        try:  # also called through a queued connection, where Qt would swallow an exception
            status = self._session.status()
            state = SessionState(status["state"])
            agent_has_control = state in (SessionState.AGENT, SessionState.STOPPING)
            self._shield.set_active(agent_has_control)
            timed_out = False
            if state is SessionState.STOPPING:
                self._stop_sequence.start()
                timed_out = self._stop_sequence.timed_out
                if timed_out:
                    # A dialog can keep a command of the agent in progress. After the time limit it can be answered.
                    self._shield.set_modal_passthrough(True)
            self._banner.show_state(state, status["waiting_for"], timed_out=timed_out)
            if agent_has_control:
                self._lock_panel()
            else:
                self._unlock_panel()
        except Exception:
            self._log.exception("Could not make the agent session protection agree with the session")

    # --- the agent gets the control ---

    def transition(self, kind: str, note: str = "") -> Optional[str]:
        """Give the control to the agent: kind is "begin" or "hand_back". Returns None, or why it was refused.
        From any thread: the work is done on the GUI thread and the caller waits for it."""
        if QThread.currentThread() == self.thread():
            return self._transition(kind, note)
        answer: List[Optional[str]] = []
        self._signal_transition.emit(kind, note, answer)  # returns when the GUI thread did the work
        return answer[0] if answer else REFUSAL_NO_ANSWER

    @Slot(str, str, object)
    def _transition_for_another_thread(self, kind: str, note: str, answer: list) -> None:
        answer.append(self._transition(kind, note))

    def _transition(self, kind: str, note: str) -> Optional[str]:
        try:  # Qt swallows an exception of a slot that is called with a blocking connection
            return self._give_control_to_the_agent(kind, note)
        except Exception as error:
            self._log.exception(f"The agent session change '{kind}' failed")
            self.sync()
            return f"the change failed in Squid ({type(error).__name__}: {error})"

    def _give_control_to_the_agent(self, kind: str, note: str) -> Optional[str]:
        if self._shut_down:
            return REFUSAL_CLOSING
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return REFUSAL_DIALOG_OPEN
        reason = self._session.why_not(kind)
        if reason:
            return reason
        # The protection first: from the moment the session says "agent", the server runs the commands of the agent.
        self._shield.set_active(True)
        try:
            self._lock_panel()
        except Exception:
            self._log.exception("Could not lock the control panel for the agent session")
            self.sync()
            return REFUSAL_PANEL_LOCK
        try:
            if kind == "begin":
                self._session.begin()
            else:
                self._session.hand_back(self._snapshot(True), note)
        except AgentSessionRefused as refusal:  # something started between the question and the change
            self.sync()
            return str(refusal)
        self.sync()
        return None

    # --- the human takes the control, or ends the session ---

    def _request_take_over(self, cause: str) -> None:
        try:
            self._session.request_stop(SessionState.HUMAN, cause)
        except AgentSessionRefused:
            pass  # the session ended a moment before; the notification of that end is on its way

    def _take_over(self) -> None:
        self._request_take_over(STOP_CAUSE_TAKE_OVER)

    def _on_modal_shown(self) -> None:
        """A modal dialog blocks the main window, so the banner cannot be clicked: take over for the human."""
        self._request_take_over(STOP_CAUSE_DIALOG)

    def _hand_back(self, note: str) -> None:
        refusal = self.transition("hand_back", note)
        if refusal:
            QMessageBox.warning(
                self._banner.window(), "Hand back", f"Squid cannot give the control to the agent now: {refusal}."
            )

    def _end(self) -> None:
        try:
            self._session.end()
        except AgentSessionRefused:
            pass  # no session any more

    # --- Squid closes ---

    def shut_down(self) -> None:
        """Squid closes or restarts: from now nothing follows the session, and the agent cannot get the control.
        GUI thread, before the hardware is closed. The caller unlocks the control panel."""
        self._shut_down = True
        self._stop_sequence.stop()
        self._session.remove_listener(self._on_session_changed)
        self._session.set_blocker(lambda: REFUSAL_CLOSING)
        self._shield.set_active(False)
