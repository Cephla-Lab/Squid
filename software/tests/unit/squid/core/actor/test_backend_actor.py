"""Tests for BackendActor."""

import threading
import time
from dataclasses import dataclass

import pytest

from squid.core.events import Event
from squid.core.actor.backend_actor import (
    BackendActor,
    CommandEnvelope,
    Priority,
    PriorityCommandQueue,
)
from squid.core.actor.thread_assertions import get_backend_thread, clear_backend_thread


@dataclass
class TestCommand(Event):
    """Test command for unit tests."""

    value: int


@dataclass
class StopTestCommand(Event):
    """Test stop command."""

    pass


class TestCommandEnvelope:
    """Tests for CommandEnvelope."""

    def test_envelope_ordering_by_priority(self):
        """Higher priority envelopes should sort first."""
        low = CommandEnvelope(
            priority=Priority.NORMAL,
            timestamp=1.0,
            command=TestCommand(value=1),
        )
        high = CommandEnvelope(
            priority=Priority.STOP,
            timestamp=2.0,  # Later timestamp but higher priority
            command=TestCommand(value=2),
        )

        # High priority should sort before low priority
        assert high < low

    def test_envelope_ordering_same_priority_by_timestamp(self):
        """Same priority should sort by timestamp (FIFO)."""
        first = CommandEnvelope(
            priority=Priority.NORMAL,
            timestamp=1.0,
            command=TestCommand(value=1),
        )
        second = CommandEnvelope(
            priority=Priority.NORMAL,
            timestamp=2.0,
            command=TestCommand(value=2),
        )

        # Earlier timestamp should sort first
        assert first < second


class TestPriorityCommandQueue:
    """Tests for PriorityCommandQueue."""

    def test_basic_put_get(self):
        """Basic put and get operations."""
        queue = PriorityCommandQueue()
        cmd = TestCommand(value=42)

        queue.put(cmd)
        envelope = queue.get(timeout=1.0)

        assert envelope.command == cmd
        assert envelope.priority == Priority.NORMAL

    def test_priority_ordering(self):
        """Higher priority commands dequeued first."""
        queue = PriorityCommandQueue()

        # Put low priority first
        queue.put(TestCommand(value=1), priority=Priority.NORMAL)
        # Put high priority second
        queue.put(TestCommand(value=2), priority=Priority.STOP)

        # High priority should come out first
        first = queue.get(timeout=1.0)
        second = queue.get(timeout=1.0)

        assert first.command.value == 2
        assert second.command.value == 1

    def test_fifo_within_same_priority(self):
        """Commands with same priority are FIFO."""
        queue = PriorityCommandQueue()

        queue.put(TestCommand(value=1), priority=Priority.NORMAL)
        queue.put(TestCommand(value=2), priority=Priority.NORMAL)
        queue.put(TestCommand(value=3), priority=Priority.NORMAL)

        first = queue.get(timeout=1.0)
        second = queue.get(timeout=1.0)
        third = queue.get(timeout=1.0)

        assert first.command.value == 1
        assert second.command.value == 2
        assert third.command.value == 3

    def test_empty_and_qsize(self):
        """Test empty() and qsize() methods."""
        queue = PriorityCommandQueue()

        assert queue.empty()
        assert queue.qsize() == 0

        queue.put(TestCommand(value=1))

        assert not queue.empty()
        assert queue.qsize() == 1


class TestBackendActor:
    """Tests for BackendActor."""

    @pytest.fixture
    def actor(self):
        """Create a BackendActor for testing."""
        actor = BackendActor()
        yield actor
        if actor.is_running:
            actor.stop()
        # Clean up thread assertion state
        clear_backend_thread()

    def test_start_stop_lifecycle(self, actor):
        """Test start and stop lifecycle."""
        assert not actor.is_running

        actor.start()
        assert actor.is_running

        actor.stop()
        assert not actor.is_running

    def test_start_twice_is_safe(self, actor):
        """Starting twice should not raise."""
        actor.start()
        actor.start()  # Should not raise
        assert actor.is_running

    def test_stop_without_start_is_safe(self, actor):
        """Stopping without starting should not raise."""
        actor.stop()  # Should not raise

    def test_register_and_dispatch_handler(self, actor):
        """Handlers should be called when commands are dispatched."""
        received = []

        def handler(cmd):
            received.append(cmd)

        actor.register_handler(TestCommand, handler)
        actor.start()

        actor.enqueue(TestCommand(value=42))
        time.sleep(0.1)  # Give time to process

        assert len(received) == 1
        assert received[0].value == 42

    def test_multiple_handlers_for_same_command(self, actor):
        """Multiple handlers can be registered for the same command."""
        received1 = []
        received2 = []

        actor.register_handler(TestCommand, received1.append)
        actor.register_handler(TestCommand, received2.append)
        actor.start()

        actor.enqueue(TestCommand(value=42))
        time.sleep(0.1)

        assert len(received1) == 1
        assert len(received2) == 1

    def test_unregister_handler(self, actor):
        """Unregistered handlers should not be called."""
        received = []
        handler = received.append

        actor.register_handler(TestCommand, handler)
        actor.unregister_handler(TestCommand, handler)
        actor.start()

        actor.enqueue(TestCommand(value=42))
        time.sleep(0.1)

        assert len(received) == 0

    def test_handler_runs_on_backend_thread(self, actor):
        """Handlers should run on the backend thread."""
        handler_threads = []

        def handler(cmd):
            handler_threads.append(threading.current_thread())

        actor.register_handler(TestCommand, handler)
        actor.start()

        actor.enqueue(TestCommand(value=42))
        time.sleep(0.1)

        assert len(handler_threads) == 1
        assert handler_threads[0].name == "BackendActor"

    def test_backend_thread_assertion_is_set(self, actor):
        """Backend thread should be set for assertions."""
        backend_thread_in_handler = [None]

        def handler(cmd):
            backend_thread_in_handler[0] = get_backend_thread()

        actor.register_handler(TestCommand, handler)
        actor.start()

        actor.enqueue(TestCommand(value=42))
        time.sleep(0.1)

        assert backend_thread_in_handler[0] is not None
        assert backend_thread_in_handler[0].name == "BackendActor"

    def test_handler_exception_does_not_crash_actor(self, actor):
        """Handler exceptions should be caught and logged."""
        received = []

        def bad_handler(cmd):
            raise ValueError("Test error")

        def good_handler(cmd):
            received.append(cmd)

        actor.register_handler(TestCommand, bad_handler)
        actor.register_handler(TestCommand, good_handler)
        actor.start()

        actor.enqueue(TestCommand(value=42))
        time.sleep(0.1)

        # Good handler should still be called despite bad handler exception
        assert len(received) == 1

    def test_enqueue_when_not_running_drops_command(self, actor):
        """Commands enqueued when not running should auto-start and be processed."""
        received = []
        actor.register_handler(TestCommand, received.append)

        actor.enqueue(TestCommand(value=42))  # Triggers auto-start
        time.sleep(0.2)

        assert len(received) == 1
        assert received[0].value == 42

    def test_drain_processes_pending_commands(self, actor):
        """drain() should process pending commands synchronously."""
        received = []
        actor.register_handler(TestCommand, received.append)

        # Don't start the actor thread
        # Directly put commands in queue
        actor._command_queue.put(TestCommand(value=1))
        actor._command_queue.put(TestCommand(value=2))

        count = actor.drain()

        assert count == 2
        assert len(received) == 2
        assert received[0].value == 1
        assert received[1].value == 2

    def test_drain_sets_backend_thread_context(self, actor):
        """drain() should set backend thread for assertion helpers."""
        seen = []

        def handler(cmd):
            # Should not raise
            from squid.core.actor.thread_assertions import assert_backend_thread

            assert_backend_thread("test drain")
            seen.append(cmd.value)

        actor.register_handler(TestCommand, handler)
        actor._command_queue.put(TestCommand(value=99))

        count = actor.drain()

        assert count == 1
        assert seen == [99]

    def test_priority_ordering_in_processing(self, actor):
        """Higher priority commands should be processed first."""
        received = []
        actor.register_handler(TestCommand, received.append)
        actor.register_handler(StopTestCommand, received.append)

        # Put commands directly in queue without starting actor
        actor._command_queue.put(TestCommand(value=1), Priority.NORMAL)
        actor._command_queue.put(TestCommand(value=2), Priority.NORMAL)
        actor._command_queue.put(StopTestCommand(), Priority.STOP)

        actor.drain()

        # Stop command should be processed first despite being enqueued last
        assert isinstance(received[0], StopTestCommand)
        assert isinstance(received[1], TestCommand)
        assert isinstance(received[2], TestCommand)

    def test_submit_work_to_worker_pool(self, actor):
        """Work submitted to worker pool should execute."""
        results = []
        event = threading.Event()

        def work():
            results.append(threading.current_thread().name)
            event.set()

        actor.start()
        actor.submit_work(work)
        event.wait(timeout=1.0)

        assert len(results) == 1
        assert results[0].startswith("BackendWorker")
