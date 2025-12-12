"""Generic State Machine base class for controllers.

Provides a thread-safe state machine implementation that controllers
can extend to manage their lifecycle states and validate transitions.

Usage:
    class LiveState(Enum):
        STOPPED = auto()
        STARTING = auto()
        LIVE = auto()
        STOPPING = auto()

    class LiveController(StateMachine[LiveState]):
        def __init__(self, event_bus):
            transitions = {
                LiveState.STOPPED: {LiveState.STARTING},
                LiveState.STARTING: {LiveState.LIVE, LiveState.STOPPED},
                LiveState.LIVE: {LiveState.STOPPING},
                LiveState.STOPPING: {LiveState.STOPPED},
            }
            super().__init__(
                initial_state=LiveState.STOPPED,
                transitions=transitions,
                event_bus=event_bus,
            )

        def start_live(self):
            self._require_state(LiveState.STOPPED)
            self._transition_to(LiveState.STARTING)
            # ... do work ...
            self._transition_to(LiveState.LIVE)
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from enum import Enum
from typing import (
    Callable,
    Dict,
    FrozenSet,
    Generic,
    Optional,
    Set,
    Type,
    TypeVar,
)

import squid.core.logging
from squid.core.events import Event, EventBus

_log = squid.core.logging.get_logger(__name__)

# Type variable for state enum
S = TypeVar("S", bound=Enum)


class InvalidStateTransition(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current_state: Enum, target_state: Enum, valid_targets: Set[Enum]):
        self.current_state = current_state
        self.target_state = target_state
        self.valid_targets = valid_targets
        valid_str = ", ".join(s.name for s in valid_targets) if valid_targets else "none"
        super().__init__(
            f"Invalid transition from {current_state.name} to {target_state.name}. "
            f"Valid targets: {valid_str}"
        )


class InvalidStateForOperation(Exception):
    """Raised when an operation is attempted in an invalid state."""

    def __init__(self, operation: str, current_state: Enum, required_states: Set[Enum]):
        self.operation = operation
        self.current_state = current_state
        self.required_states = required_states
        required_str = ", ".join(s.name for s in required_states)
        super().__init__(
            f"Operation '{operation}' requires state in [{required_str}], "
            f"but current state is {current_state.name}"
        )


class StateMachine(ABC, Generic[S]):
    """Generic state machine base class.

    Provides thread-safe state management with:
    - Validated transitions based on allowed transition map
    - State guards for operations
    - Command validation based on current state
    - Abstract method for publishing state changes

    Thread Safety:
    - All state reads/writes are protected by a lock
    - Transition validation and execution are atomic
    - State change callbacks are called outside the lock
    """

    def __init__(
        self,
        initial_state: S,
        transitions: Dict[S, Set[S]],
        event_bus: Optional[EventBus] = None,
        name: Optional[str] = None,
    ):
        """Initialize the state machine.

        Args:
            initial_state: The starting state
            transitions: Map of state -> set of valid target states
            event_bus: Optional EventBus for publishing state changes
            name: Optional name for logging (defaults to class name)
        """
        self._state = initial_state
        self._transitions = {k: frozenset(v) for k, v in transitions.items()}
        self._event_bus = event_bus
        self._name = name or self.__class__.__name__
        self._lock = threading.RLock()

        # Command validation: state -> set of valid command types
        self._valid_commands: Dict[S, FrozenSet[Type[Event]]] = {}

        # State change callbacks
        self._on_state_change_callbacks: list[Callable[[S, S], None]] = []

    @property
    def state(self) -> S:
        """Get the current state (thread-safe)."""
        with self._lock:
            return self._state

    @property
    def state_name(self) -> str:
        """Get the current state name."""
        return self.state.name

    def _transition_to(self, new_state: S) -> None:
        """Transition to a new state.

        Validates the transition is allowed, updates state atomically,
        then fires callbacks and publishes events.

        Args:
            new_state: The target state

        Raises:
            InvalidStateTransition: If transition is not allowed
        """
        with self._lock:
            old_state = self._state
            valid_targets = self._transitions.get(old_state, frozenset())

            if new_state not in valid_targets:
                raise InvalidStateTransition(old_state, new_state, set(valid_targets))

            self._state = new_state
            _log.debug(f"[{self._name}] {old_state.name} -> {new_state.name}")

        # Fire callbacks outside lock
        self._fire_state_change(old_state, new_state)

    def _require_state(self, *allowed_states: S, operation: str = "operation") -> None:
        """Guard that ensures current state is one of the allowed states.

        Args:
            *allowed_states: States in which the operation is valid
            operation: Name of the operation (for error messages)

        Raises:
            InvalidStateForOperation: If current state is not allowed
        """
        with self._lock:
            if self._state not in allowed_states:
                raise InvalidStateForOperation(
                    operation, self._state, set(allowed_states)
                )

    def _is_in_state(self, *states: S) -> bool:
        """Check if current state is one of the given states (thread-safe)."""
        with self._lock:
            return self._state in states

    def register_valid_commands(self, state: S, command_types: Set[Type[Event]]) -> None:
        """Register which commands are valid in a given state.

        Args:
            state: The state
            command_types: Set of command types valid in this state
        """
        self._valid_commands[state] = frozenset(command_types)

    def is_command_valid(self, command_type: Type[Event]) -> bool:
        """Check if a command type is valid in the current state.

        Returns True if:
        - No command validation is configured for current state, OR
        - The command type is in the valid set for current state

        Args:
            command_type: The command class to check

        Returns:
            True if command is valid in current state
        """
        with self._lock:
            valid_commands = self._valid_commands.get(self._state)
            if valid_commands is None:
                return True  # No validation configured
            return command_type in valid_commands

    def on_state_change(self, callback: Callable[[S, S], None]) -> None:
        """Register a callback for state changes.

        Callback receives (old_state, new_state).

        Args:
            callback: Function to call on state change
        """
        self._on_state_change_callbacks.append(callback)

    def _fire_state_change(self, old_state: S, new_state: S) -> None:
        """Fire state change callbacks and publish event."""
        # Call registered callbacks
        for callback in self._on_state_change_callbacks:
            try:
                callback(old_state, new_state)
            except Exception as e:
                _log.exception(f"Error in state change callback: {e}")

        # Publish event via abstract method
        try:
            self._publish_state_changed(old_state, new_state)
        except Exception as e:
            _log.exception(f"Error publishing state change: {e}")

    @abstractmethod
    def _publish_state_changed(self, old_state: S, new_state: S) -> None:
        """Publish a state change event.

        Subclasses must implement this to publish their specific
        state change event type.

        Args:
            old_state: The previous state
            new_state: The new state
        """
        pass

    def _can_transition_to(self, target_state: S) -> bool:
        """Check if transition to target state is valid from current state.

        Args:
            target_state: The target state to check

        Returns:
            True if transition is valid
        """
        with self._lock:
            valid_targets = self._transitions.get(self._state, frozenset())
            return target_state in valid_targets

    def _force_state(self, new_state: S, reason: str = "forced") -> None:
        """Force state to a new value without validation.

        Use sparingly - primarily for error recovery.

        Args:
            new_state: The target state
            reason: Reason for forcing (for logging)
        """
        with self._lock:
            old_state = self._state
            self._state = new_state
            _log.warning(
                f"[{self._name}] Forced state {old_state.name} -> {new_state.name}: {reason}"
            )

        self._fire_state_change(old_state, new_state)

    def get_valid_transitions(self) -> Set[S]:
        """Get the set of valid target states from current state.

        Returns:
            Set of states that can be transitioned to
        """
        with self._lock:
            return set(self._transitions.get(self._state, frozenset()))
