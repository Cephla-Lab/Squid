"""The stop sequence of an agent session.

A take over or an end puts the session in "stopping". This object then checks, on a timer on the GUI thread, that
nothing of the agent is in progress any more, and only then gives the control to the human (or ends the session).
Nothing here waits for another thread: server threads call into the GUI thread and wait for it, so a wait on the GUI
thread for one of them would never end.
"""

import time
from typing import Callable, List

from qtpy.QtCore import QObject, QTimer, Signal

import squid.logging
from control.core.agent_session import AgentSession, SessionState

AGENT_SESSION_STOP_POLL_MS = 100
AGENT_SESSION_STOP_TIMEOUT_S = 30.0
WAIT_COMMAND = "a command is in progress"
WAIT_ACQUISITION = "an acquisition is running"
WAIT_STAGE = "the stage is moving"


class AgentSessionStopSequence(QObject):
    signal_timed_out = Signal(list)  # the reasons; one time for each start()
    signal_waiting_changed = Signal(list)  # the reasons, when they are not those of the check before

    def __init__(
        self,
        session: AgentSession,
        multipoint_controller,
        stage,
        snapshot: Callable[[bool], dict],
        parent=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """snapshot(with_position) gives the microscope state for the handover; with_position=False must not ask
        the stage."""
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._session = session
        self._multipoint_controller = multipoint_controller
        self._stage = stage
        self._snapshot = snapshot
        self._clock = clock
        self._started_at = 0.0
        self._timed_out = False
        self._reasons: List[str] = []
        self._timer = QTimer(self)
        self._timer.setInterval(AGENT_SESSION_STOP_POLL_MS)
        self._timer.timeout.connect(self._tick)

    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def timed_out(self) -> bool:
        """True from the time limit of this sequence until the next start()."""
        return self._timed_out

    def start(self) -> None:
        """Call on the GUI thread. Returns at once; a second call while it runs does nothing."""
        if self._timer.isActive():
            return
        self._started_at = self._clock()
        self._timed_out = False
        self._reasons = []
        self._timer.start()

    def stop(self) -> None:
        """Cancel the sequence (shutdown). The session stays as it is."""
        self._timer.stop()

    def unlock_anyway(self) -> None:
        """The human decides not to wait any more. The stage is not asked while a command is in progress."""
        self._timer.stop()
        self._session.finish_stop(self._snapshot(self._session.in_flight == 0), unlocked_anyway=True)

    def _waiting_reasons(self) -> List[str]:
        """Why the control cannot go to the human yet. A check that fails counts as "not yet"."""
        reasons = []
        if self._session.in_flight > 0:
            reasons.append(WAIT_COMMAND)
        try:
            if self._multipoint_controller.acquisition_in_progress():
                # Again at each check: an acquisition that a command started a moment after the take over missed
                # the first request.
                self._multipoint_controller.request_abort_aquisition()
                reasons.append(WAIT_ACQUISITION)
        except Exception:
            self._log.exception("Could not check or abort the acquisition")
            reasons.append(WAIT_ACQUISITION)
        if not reasons:
            # Only now: on some stages this call waits for a lock that a move in progress holds.
            try:
                if not self._stage.reports_stopped():
                    reasons.append(WAIT_STAGE)
            except Exception:
                self._log.exception("Could not ask the stage if it stopped")
                reasons.append(WAIT_STAGE)
        return reasons

    def _tick(self) -> None:
        try:
            if self._session.state is not SessionState.STOPPING:
                self._timer.stop()
                return
            reasons = self._waiting_reasons()
            self._session.set_waiting_for(reasons)
            if reasons != self._reasons:
                self._reasons = reasons
                self.signal_waiting_changed.emit(reasons)
            if not reasons:
                self._timer.stop()
                self._session.finish_stop(self._snapshot(True))
                return
            if not self._timed_out and self._clock() - self._started_at > AGENT_SESSION_STOP_TIMEOUT_S:
                self._timed_out = True
                self.signal_timed_out.emit(reasons)
        except Exception:
            self._log.exception("The stop sequence of the agent session failed in one check")
