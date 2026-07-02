"""Instrument state machine (spec §3)."""

import threading
from enum import Enum
from typing import Callable, Dict, FrozenSet, Optional, Set


class InstrumentState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    INITIALIZED = "INITIALIZED"
    RESERVED = "RESERVED"
    ACQUIRING = "ACQUIRING"
    PROCESSING = "PROCESSING"
    ERROR = "ERROR"
    RECOVERING = "RECOVERING"
    SHUTTING_DOWN = "SHUTTING_DOWN"


BUSY_STATES: FrozenSet[InstrumentState] = frozenset(
    {
        InstrumentState.INITIALIZING,
        InstrumentState.ACQUIRING,
        InstrumentState.PROCESSING,
        InstrumentState.RECOVERING,
        InstrumentState.SHUTTING_DOWN,
    }
)

_ALLOWED: Dict[InstrumentState, Set[InstrumentState]] = {
    InstrumentState.UNINITIALIZED: {InstrumentState.INITIALIZING, InstrumentState.SHUTTING_DOWN},
    InstrumentState.INITIALIZING: {InstrumentState.INITIALIZED, InstrumentState.ERROR},
    InstrumentState.INITIALIZED: {
        InstrumentState.ACQUIRING,
        InstrumentState.INITIALIZING,
        InstrumentState.RECOVERING,
        InstrumentState.ERROR,
        InstrumentState.SHUTTING_DOWN,
    },
    InstrumentState.ACQUIRING: {
        InstrumentState.PROCESSING,
        InstrumentState.ERROR,
        InstrumentState.INITIALIZED,  # abort path collapses PROCESSING when nothing to drain
    },
    InstrumentState.PROCESSING: {InstrumentState.INITIALIZED, InstrumentState.ERROR},
    InstrumentState.ERROR: {
        InstrumentState.RECOVERING,
        InstrumentState.INITIALIZING,
        InstrumentState.SHUTTING_DOWN,
    },
    InstrumentState.RECOVERING: {InstrumentState.INITIALIZED, InstrumentState.ERROR},
    InstrumentState.RESERVED: set(),
    InstrumentState.SHUTTING_DOWN: set(),
}


class InvalidTransition(Exception):
    pass


class StateMachine:
    """Thread-safe instrument state with a transition listener.

    The listener is invoked outside the lock so it may call back into the machine.
    Self-transitions are silent no-ops (idempotent callers).
    """

    def __init__(
        self,
        initial: InstrumentState,
        on_transition: Optional[Callable[[InstrumentState, InstrumentState], None]] = None,
    ):
        self._lock = threading.Lock()
        self._state = initial
        self._on_transition = on_transition

    @property
    def state(self) -> InstrumentState:
        with self._lock:
            return self._state

    def is_busy(self) -> bool:
        return self.state in BUSY_STATES

    def transition(self, new: InstrumentState) -> None:
        with self._lock:
            old = self._state
            if new == old:
                return
            if new not in _ALLOWED[old]:
                raise InvalidTransition(f"{old.value} -> {new.value} is not allowed")
            self._state = new
        if self._on_transition is not None:
            self._on_transition(old, new)
