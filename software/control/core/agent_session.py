"""Agent session: one driver of the microscope at a time, the agent or a human.

No Qt here. The control server asks the session for admission before each command, the GUI listens to it, and the
terminal console asks it before each line. With no session (state "none") everything is admitted as before.
"""

import contextlib
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

import squid.logging


class SessionState(str, Enum):
    NONE = "none"
    AGENT = "agent"
    STOPPING = "stopping"
    HUMAN = "human"


SESSION_COMMANDS = frozenset({"begin_agent_session", "take_over", "hand_back", "end_agent_session"})
REFUSAL_STOPPING = "a stop is in progress"
REFUSAL_HUMAN = "a human is driving"
REFUSAL_AGENT_DRIVES = "the agent is driving"


class AgentSessionRefused(RuntimeError):
    """The session does not allow this now. The text says why."""


def is_read_command(command: str) -> bool:
    return command == "ping" or command.startswith("get_")


def snapshot_microscope(microscope, with_position: bool = True) -> dict:
    """The microscope state that a handover records. with_position=False reads no stage position (the stage may be
    busy under another thread)."""
    pos = microscope.stage.get_pos() if with_position else None
    channel = microscope.live_controller.currentConfiguration
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "x_mm": pos.x_mm if pos else None,
        "y_mm": pos.y_mm if pos else None,
        "z_mm": pos.z_mm if pos else None,
        "objective": microscope.objective_store.current_objective,
        "channel": channel.name if channel is not None else None,
        "exposure_ms": microscope.camera.get_exposure_time(),
        "is_live": bool(microscope.live_controller.is_live),
    }


class AgentSession:
    def __init__(self) -> None:
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._lock = threading.Lock()
        self._state = SessionState.NONE
        self._session_id: Optional[str] = None
        self._stop_target = SessionState.HUMAN
        self._stop_cause: Optional[str] = None
        self._in_flight = 0
        self._console_in_flight = 0
        self._waiting_for: List[str] = []
        self._unlocked_anyway = False
        self._handover_count = 0
        self._handover: Dict[str, object] = {"before": None, "after": None, "note": ""}
        self._blocker: Optional[Callable[[], Optional[str]]] = None
        self._listeners: List[Callable[[SessionState, SessionState], None]] = []

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def in_flight(self) -> int:
        """Action commands that the control server runs at this moment."""
        return self._in_flight

    @property
    def console_in_flight(self) -> int:
        return self._console_in_flight

    def add_listener(self, listener: Callable[[SessionState, SessionState], None]) -> None:
        """listener(old, new) after each state change, outside the lock, on the thread that made the change. A
        listener must read the current state itself: two changes from two threads can arrive in the other order."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[SessionState, SessionState], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def set_blocker(self, blocker: Optional[Callable[[], Optional[str]]]) -> None:
        """blocker() says why the agent must not get control now, or returns None. Asked by begin and hand back."""
        self._blocker = blocker

    def _notify(self, old: SessionState, new: SessionState) -> None:
        for listener in list(self._listeners):
            try:
                listener(old, new)
            except Exception:
                self._log.exception("An agent session listener failed")

    def _refusal_to_give_control(self) -> Optional[str]:
        """Called with the lock held."""
        if self._in_flight > 0:
            return "a command is in progress"
        if self._console_in_flight > 0:
            return "the console is running a line"
        return None

    def begin(self) -> str:
        reason = self._blocker() if self._blocker is not None else None
        with self._lock:
            if self._state is not SessionState.NONE:
                raise AgentSessionRefused("a session exists")
            reason = reason or self._refusal_to_give_control()
            if reason:
                raise AgentSessionRefused(reason)
            self._state = SessionState.AGENT
            self._session_id = uuid.uuid4().hex
            self._stop_cause = None
            self._waiting_for = []
            self._unlocked_anyway = False
            self._handover_count = 0
            self._handover = {"before": None, "after": None, "note": ""}
            session_id = self._session_id
        self._notify(SessionState.NONE, SessionState.AGENT)
        return session_id

    def request_stop(self, then: SessionState, cause: str) -> SessionState:
        """Take the control from the agent. `then` is HUMAN (take over) or NONE (end). Returns the state after."""
        if then not in (SessionState.HUMAN, SessionState.NONE):
            raise ValueError("then must be HUMAN or NONE")
        with self._lock:
            old = self._state
            if old is SessionState.NONE:
                raise AgentSessionRefused("no agent session")
            if old is SessionState.HUMAN:
                return old
            if old is SessionState.AGENT:
                self._state = SessionState.STOPPING
                self._stop_target = then
                self._stop_cause = cause
                self._unlocked_anyway = False
            elif then is SessionState.NONE:  # already stopping: an end wins over a take over
                self._stop_target = SessionState.NONE
                self._stop_cause = cause
                return old
            else:
                return old
        self._notify(old, SessionState.STOPPING)
        return SessionState.STOPPING

    def finish_stop(self, snapshot: dict, unlocked_anyway: bool = False) -> SessionState:
        """The stop sequence calls this when nothing of the agent is in progress any more."""
        with self._lock:
            if self._state is not SessionState.STOPPING:
                return self._state
            new = self._stop_target
            self._state = new
            self._waiting_for = []
            self._unlocked_anyway = unlocked_anyway
            if new is SessionState.HUMAN:
                self._handover = {"before": snapshot, "after": None, "note": ""}
            else:
                self._clear_session()
        self._notify(SessionState.STOPPING, new)
        return new

    def hand_back(self, snapshot: dict, note: str = "") -> None:
        reason = self._blocker() if self._blocker is not None else None
        with self._lock:
            if self._state is not SessionState.HUMAN:
                raise AgentSessionRefused("no human is driving")
            reason = reason or self._refusal_to_give_control()
            if reason:
                raise AgentSessionRefused(reason)
            self._state = SessionState.AGENT
            self._handover = {"before": self._handover["before"], "after": snapshot, "note": note}
            self._handover_count += 1
            self._stop_cause = None
            self._unlocked_anyway = False
        self._notify(SessionState.HUMAN, SessionState.AGENT)

    def end(self) -> SessionState:
        with self._lock:
            old = self._state
            if old is SessionState.NONE:
                raise AgentSessionRefused("no agent session")
            if old is SessionState.HUMAN:
                self._state = SessionState.NONE
                self._clear_session()
        if old is SessionState.HUMAN:
            self._notify(SessionState.HUMAN, SessionState.NONE)
            return SessionState.NONE
        return self.request_stop(SessionState.NONE, "end")

    def _clear_session(self) -> None:
        """Called with the lock held."""
        self._session_id = None
        self._handover = {"before": None, "after": None, "note": ""}
        self._handover_count = 0

    @contextlib.contextmanager
    def admit(self, command: str):
        """Admission of one control-server command. A read or a session command is admitted in every state. Another
        command is refused while a stop is in progress or a human drives, and is counted while it runs. The check
        and the count are under one lock."""
        counted = False
        if not is_read_command(command) and command not in SESSION_COMMANDS:
            with self._lock:
                if self._state is SessionState.STOPPING:
                    raise AgentSessionRefused(REFUSAL_STOPPING)
                if self._state is SessionState.HUMAN:
                    raise AgentSessionRefused(REFUSAL_HUMAN)
                self._in_flight += 1
                counted = True
        try:
            yield
        finally:
            if counted:
                with self._lock:
                    self._in_flight -= 1

    @contextlib.contextmanager
    def admit_console(self):
        """Admission of one line of the terminal console: refused while the agent drives or a stop is in progress."""
        with self._lock:
            if self._state is SessionState.AGENT:
                raise AgentSessionRefused(REFUSAL_AGENT_DRIVES)
            if self._state is SessionState.STOPPING:
                raise AgentSessionRefused(REFUSAL_STOPPING)
            self._console_in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self._console_in_flight -= 1

    def set_waiting_for(self, reasons: Sequence[str]) -> None:
        with self._lock:
            self._waiting_for = list(reasons)

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state.value,
                "session_id": self._session_id,
                "commands_in_progress": self._in_flight,
                "stop_cause": self._stop_cause,
                "waiting_for": list(self._waiting_for),
                "unlocked_anyway": self._unlocked_anyway,
                "handover_count": self._handover_count,
                "handover": dict(self._handover),
            }
