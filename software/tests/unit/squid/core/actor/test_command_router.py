"""Tests for BackendCommandRouter."""

from dataclasses import dataclass

import pytest

from squid.core.events import Event, EventBus
from squid.core.actor.backend_actor import BackendActor, Priority
from squid.core.actor.command_router import BackendCommandRouter
from squid.core.actor.thread_assertions import clear_backend_thread


@dataclass
class TestCommand(Event):
    """Test command."""

    value: int


@dataclass
class StopLiveCommand(Event):
    """Test stop command - should get high priority."""

    pass


@dataclass
class AbortAcquisitionCommand(Event):
    """Test abort command - should get high priority."""

    pass


@dataclass
class CancelOperationCommand(Event):
    """Test cancel command - should get high priority."""

    pass


class TestBackendCommandRouter:
    """Tests for BackendCommandRouter."""

    @pytest.fixture
    def event_bus(self):
        """Create an EventBus for testing."""
        bus = EventBus()
        yield bus

    @pytest.fixture
    def actor(self):
        """Create a BackendActor for testing."""
        actor = BackendActor()
        yield actor
        if actor.is_running:
            actor.stop()
        clear_backend_thread()

    @pytest.fixture
    def router(self, event_bus, actor):
        """Create a BackendCommandRouter for testing."""
        return BackendCommandRouter(event_bus, actor)

    def test_register_command(self, router, event_bus, actor):
        """Registered commands should be routed to backend actor."""
        router.register_command(TestCommand)

        assert TestCommand in router.registered_commands
        assert len(router.registered_commands) == 1

    def test_register_commands_list(self, router):
        """Multiple commands can be registered at once."""
        router.register_commands([TestCommand, StopLiveCommand])

        assert TestCommand in router.registered_commands
        assert StopLiveCommand in router.registered_commands

    def test_register_same_command_twice(self, router):
        """Registering same command twice should be safe."""
        router.register_command(TestCommand)
        router.register_command(TestCommand)

        assert router.registered_commands.count(TestCommand) == 1

    def test_unregister_command(self, router):
        """Unregistered commands should no longer be routed."""
        router.register_command(TestCommand)
        router.unregister_command(TestCommand)

        assert TestCommand not in router.registered_commands

    def test_unregister_all(self, router):
        """unregister_all should remove all registrations."""
        router.register_commands([TestCommand, StopLiveCommand])
        router.unregister_all()

        assert len(router.registered_commands) == 0

    def test_routing_from_eventbus_to_actor(self, router, event_bus, actor):
        """Commands published to EventBus should be routed to BackendActor."""
        received = []
        actor.register_handler(TestCommand, received.append)

        router.register_command(TestCommand)

        # Start the actor so it accepts commands
        actor.start()

        # Publish to EventBus
        event_bus.publish(TestCommand(value=42))
        event_bus.drain()

        # Give time for actor to process
        import time
        time.sleep(0.1)

        assert len(received) == 1
        assert received[0].value == 42

    def test_stop_command_gets_high_priority(self, router):
        """Stop commands should be detected as high priority."""
        # Test priority detection directly
        priority = router._get_priority(StopLiveCommand())
        assert priority == Priority.STOP

    def test_abort_command_gets_high_priority(self, router):
        """Abort commands should be enqueued with STOP priority."""
        priority = router._get_priority(AbortAcquisitionCommand())
        assert priority == Priority.STOP

    def test_cancel_command_gets_high_priority(self, router):
        """Cancel commands should be enqueued with STOP priority."""
        priority = router._get_priority(CancelOperationCommand())
        assert priority == Priority.STOP

    def test_normal_command_gets_normal_priority(self, router):
        """Normal commands should get NORMAL priority."""
        priority = router._get_priority(TestCommand(value=1))
        assert priority == Priority.NORMAL

    def test_unregistered_command_not_routed(self, router, event_bus, actor):
        """Commands not registered should not be routed."""
        received = []
        actor.register_handler(TestCommand, received.append)

        # Don't register the command with router

        event_bus.publish(TestCommand(value=42))
        event_bus.drain()
        actor.drain()

        # Command should not have been routed
        assert len(received) == 0
