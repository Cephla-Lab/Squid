"""Tests for StateMachine base class."""

import threading
import time
from enum import Enum, auto
from typing import Set
from dataclasses import dataclass

import pytest

from squid.core.events import Event, EventBus
from squid.core.state_machine import (
    StateMachine,
    InvalidStateTransition,
    InvalidStateForOperation,
)


class TestState(Enum):
    """Test state enum."""

    IDLE = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    ERROR = auto()


@dataclass
class TestStateChanged(Event):
    """Test state change event."""

    old_state: str
    new_state: str


@dataclass
class StartCommand(Event):
    """Test start command."""

    pass


@dataclass
class StopCommand(Event):
    """Test stop command."""

    pass


class TestController(StateMachine[TestState]):
    """Concrete test implementation of StateMachine."""

    def __init__(self, event_bus=None):
        transitions = {
            TestState.IDLE: {TestState.STARTING, TestState.ERROR},
            TestState.STARTING: {TestState.RUNNING, TestState.IDLE, TestState.ERROR},
            TestState.RUNNING: {TestState.STOPPING, TestState.ERROR},
            TestState.STOPPING: {TestState.IDLE, TestState.ERROR},
            TestState.ERROR: {TestState.IDLE},
        }
        super().__init__(
            initial_state=TestState.IDLE,
            transitions=transitions,
            event_bus=event_bus,
            name="TestController",
        )
        self.published_events = []

    def _publish_state_changed(self, old_state: TestState, new_state: TestState) -> None:
        event = TestStateChanged(old_state=old_state.name, new_state=new_state.name)
        self.published_events.append(event)
        if self._event_bus:
            self._event_bus.publish(event)

    def start(self):
        """Test start operation."""
        self._require_state(TestState.IDLE, operation="start")
        self._transition_to(TestState.STARTING)
        # Simulate work
        self._transition_to(TestState.RUNNING)

    def stop(self):
        """Test stop operation."""
        self._require_state(TestState.RUNNING, operation="stop")
        self._transition_to(TestState.STOPPING)
        # Simulate cleanup
        self._transition_to(TestState.IDLE)


class TestStateMachineBasics:
    """Basic tests for StateMachine."""

    def test_initial_state(self):
        """Should start in initial state."""
        controller = TestController()
        assert controller.state == TestState.IDLE
        assert controller.state_name == "IDLE"

    def test_valid_transition(self):
        """Should allow valid transitions."""
        controller = TestController()
        controller._transition_to(TestState.STARTING)
        assert controller.state == TestState.STARTING

    def test_invalid_transition_raises(self):
        """Should raise on invalid transition."""
        controller = TestController()
        # IDLE -> RUNNING is not valid (must go through STARTING)
        with pytest.raises(InvalidStateTransition) as exc_info:
            controller._transition_to(TestState.RUNNING)

        assert exc_info.value.current_state == TestState.IDLE
        assert exc_info.value.target_state == TestState.RUNNING

    def test_require_state_passes(self):
        """_require_state should pass when in correct state."""
        controller = TestController()
        controller._require_state(TestState.IDLE, operation="test")  # Should not raise

    def test_require_state_fails(self):
        """_require_state should raise when in wrong state."""
        controller = TestController()
        with pytest.raises(InvalidStateForOperation) as exc_info:
            controller._require_state(TestState.RUNNING, operation="test_op")

        assert exc_info.value.operation == "test_op"
        assert exc_info.value.current_state == TestState.IDLE

    def test_require_state_multiple_allowed(self):
        """_require_state should accept multiple allowed states."""
        controller = TestController()
        controller._require_state(TestState.IDLE, TestState.ERROR, operation="test")

    def test_is_in_state(self):
        """_is_in_state should check state correctly."""
        controller = TestController()
        assert controller._is_in_state(TestState.IDLE) is True
        assert controller._is_in_state(TestState.RUNNING) is False
        assert controller._is_in_state(TestState.IDLE, TestState.RUNNING) is True

    def test_can_transition_to(self):
        """_can_transition_to should check valid transitions."""
        controller = TestController()
        assert controller._can_transition_to(TestState.STARTING) is True
        assert controller._can_transition_to(TestState.RUNNING) is False

    def test_get_valid_transitions(self):
        """get_valid_transitions should return valid target states."""
        controller = TestController()
        valid = controller.get_valid_transitions()
        assert TestState.STARTING in valid
        assert TestState.ERROR in valid
        assert TestState.RUNNING not in valid


class TestStateMachineOperations:
    """Tests for higher-level operations."""

    def test_start_operation(self):
        """start() should transition through states correctly."""
        controller = TestController()
        controller.start()
        assert controller.state == TestState.RUNNING
        assert len(controller.published_events) == 2  # IDLE->STARTING, STARTING->RUNNING

    def test_stop_operation(self):
        """stop() should transition through states correctly."""
        controller = TestController()
        controller.start()
        controller.stop()
        assert controller.state == TestState.IDLE

    def test_start_in_wrong_state_raises(self):
        """start() in RUNNING state should raise."""
        controller = TestController()
        controller.start()
        with pytest.raises(InvalidStateForOperation):
            controller.start()

    def test_stop_in_wrong_state_raises(self):
        """stop() in IDLE state should raise."""
        controller = TestController()
        with pytest.raises(InvalidStateForOperation):
            controller.stop()


class TestStateMachineCallbacks:
    """Tests for state change callbacks."""

    def test_state_change_callback(self):
        """Should call registered callbacks on state change."""
        controller = TestController()
        changes = []
        controller.on_state_change(lambda old, new: changes.append((old, new)))

        controller._transition_to(TestState.STARTING)

        assert len(changes) == 1
        assert changes[0] == (TestState.IDLE, TestState.STARTING)

    def test_multiple_callbacks(self):
        """Should call all registered callbacks."""
        controller = TestController()
        changes1 = []
        changes2 = []
        controller.on_state_change(lambda old, new: changes1.append((old, new)))
        controller.on_state_change(lambda old, new: changes2.append((old, new)))

        controller._transition_to(TestState.STARTING)

        assert len(changes1) == 1
        assert len(changes2) == 1

    def test_callback_exception_doesnt_crash(self):
        """Callback exceptions should not prevent state change."""
        controller = TestController()

        def bad_callback(old, new):
            raise RuntimeError("callback error")

        controller.on_state_change(bad_callback)

        # Should not raise
        controller._transition_to(TestState.STARTING)
        assert controller.state == TestState.STARTING


class TestStateMachineCommandValidation:
    """Tests for command validation."""

    def test_command_valid_when_not_configured(self):
        """Commands should be valid when no validation configured."""
        controller = TestController()
        assert controller.is_command_valid(StartCommand) is True
        assert controller.is_command_valid(StopCommand) is True

    def test_command_valid_when_in_valid_set(self):
        """Command should be valid when in configured set."""
        controller = TestController()
        controller.register_valid_commands(TestState.IDLE, {StartCommand})

        assert controller.is_command_valid(StartCommand) is True
        assert controller.is_command_valid(StopCommand) is False

    def test_command_validation_changes_with_state(self):
        """Command validity should change with state."""
        controller = TestController()
        controller.register_valid_commands(TestState.IDLE, {StartCommand})
        controller.register_valid_commands(TestState.RUNNING, {StopCommand})

        assert controller.is_command_valid(StartCommand) is True
        assert controller.is_command_valid(StopCommand) is False

        controller.start()

        assert controller.is_command_valid(StartCommand) is False
        assert controller.is_command_valid(StopCommand) is True


class TestStateMachineForceState:
    """Tests for force_state functionality."""

    def test_force_state_bypasses_validation(self):
        """_force_state should bypass transition validation."""
        controller = TestController()
        # IDLE -> STOPPING is not normally valid
        controller._force_state(TestState.STOPPING, reason="test")
        assert controller.state == TestState.STOPPING

    def test_force_state_fires_callbacks(self):
        """_force_state should still fire callbacks."""
        controller = TestController()
        changes = []
        controller.on_state_change(lambda old, new: changes.append((old, new)))

        controller._force_state(TestState.ERROR, reason="test error")

        assert len(changes) == 1
        assert changes[0] == (TestState.IDLE, TestState.ERROR)


class TestStateMachineThreadSafety:
    """Thread safety tests for StateMachine."""

    def test_concurrent_state_reads(self):
        """Multiple threads should safely read state."""
        controller = TestController()
        controller.start()
        results = []
        barrier = threading.Barrier(5)

        def read_state(thread_id):
            barrier.wait()
            for _ in range(100):
                state = controller.state
                results.append((thread_id, state))

        threads = [
            threading.Thread(target=read_state, args=(i,))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All reads should return RUNNING
        assert all(r[1] == TestState.RUNNING for r in results)

    def test_concurrent_transitions(self):
        """Concurrent transition attempts should be serialized."""
        controller = TestController()
        results = []
        barrier = threading.Barrier(3)

        def try_start(thread_id):
            barrier.wait()
            try:
                controller._require_state(TestState.IDLE, operation="start")
                controller._transition_to(TestState.STARTING)
                results.append((thread_id, "success"))
            except (InvalidStateForOperation, InvalidStateTransition):
                results.append((thread_id, "failed"))

        threads = [
            threading.Thread(target=try_start, args=(i,))
            for i in range(3)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one should succeed
        successes = [r for r in results if r[1] == "success"]
        failures = [r for r in results if r[1] == "failed"]

        assert len(successes) == 1
        assert len(failures) == 2


class TestStateMachineEventBusIntegration:
    """Tests for EventBus integration."""

    def test_publishes_to_event_bus(self):
        """State changes should publish to EventBus."""
        bus = EventBus()
        received = []
        bus.subscribe(TestStateChanged, lambda e: received.append(e))

        controller = TestController(event_bus=bus)
        controller._transition_to(TestState.STARTING)
        bus.drain()

        assert len(received) == 1
        assert received[0].old_state == "IDLE"
        assert received[0].new_state == "STARTING"
