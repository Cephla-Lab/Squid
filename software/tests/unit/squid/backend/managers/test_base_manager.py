"""Unit tests for BaseManager."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from squid.backend.managers.base import BaseManager
from squid.core.events import Event, EventBus, handles


@dataclass
class TestStateEvent(Event):
    """Test state event."""

    value: int


@dataclass
class TestCommand(Event):
    """Test command event."""

    data: str


class TestManager(BaseManager):
    """Test manager for testing BaseManager functionality."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        super().__init__(event_bus)
        self.received_events: list[TestStateEvent] = []
        self.received_commands: list[TestCommand] = []

    @handles(TestStateEvent)
    def _on_state_event(self, event: TestStateEvent) -> None:
        self.received_events.append(event)

    @handles(TestCommand)
    def _on_command(self, cmd: TestCommand) -> None:
        self.received_commands.append(cmd)


class TestBaseManager:
    """Tests for BaseManager base class."""

    def test_auto_subscribes_handlers_with_event_bus(self) -> None:
        """Handlers decorated with @handles are auto-subscribed when event_bus provided."""
        bus = EventBus()
        manager = TestManager(bus)

        bus.publish(TestStateEvent(value=42))
        bus.publish(TestCommand(data="hello"))
        bus.drain()

        assert len(manager.received_events) == 1
        assert manager.received_events[0].value == 42
        assert len(manager.received_commands) == 1
        assert manager.received_commands[0].data == "hello"

    def test_no_subscriptions_without_event_bus(self) -> None:
        """Manager works without event bus (optional parameter)."""
        manager = TestManager()  # No event bus

        assert manager._event_bus is None
        assert manager._subscriptions == []

    def test_shutdown_unsubscribes_with_event_bus(self) -> None:
        """Shutdown unsubscribes handlers when event bus is present."""
        bus = EventBus()
        manager = TestManager(bus)

        # Verify handlers work
        bus.publish(TestStateEvent(value=1))
        bus.drain()
        assert len(manager.received_events) == 1

        manager.shutdown()

        # Events should no longer be received
        bus.publish(TestStateEvent(value=2))
        bus.drain()
        assert len(manager.received_events) == 1  # Still just 1

    def test_shutdown_safe_without_event_bus(self) -> None:
        """Shutdown is safe when no event bus was provided."""
        manager = TestManager()  # No event bus

        manager.shutdown()  # Should not raise

        assert manager._subscriptions == []

    def test_event_bus_accessible(self) -> None:
        """Event bus is accessible via _event_bus attribute."""
        bus = EventBus()
        manager = TestManager(bus)

        assert manager._event_bus is bus

    def test_logger_available(self) -> None:
        """Logger is available via _log attribute."""
        bus = EventBus()
        manager = TestManager(bus)

        assert manager._log is not None
        # Logger name includes squid prefix
        assert "TestManager" in manager._log.name

    def test_subscriptions_tracked(self) -> None:
        """Subscriptions are tracked in _subscriptions list."""
        bus = EventBus()
        manager = TestManager(bus)

        # Should have 2 subscriptions
        assert len(manager._subscriptions) == 2

    def test_shutdown_clears_subscriptions(self) -> None:
        """Shutdown clears the subscriptions list."""
        bus = EventBus()
        manager = TestManager(bus)

        assert len(manager._subscriptions) > 0

        manager.shutdown()

        assert manager._subscriptions == []

    def test_multiple_shutdown_safe(self) -> None:
        """Multiple shutdown calls are safe."""
        bus = EventBus()
        manager = TestManager(bus)

        manager.shutdown()
        manager.shutdown()  # Should not raise

        assert manager._subscriptions == []


class ManagerWithoutHandlers(BaseManager):
    """Manager with no event handlers."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        super().__init__(event_bus)
        self.initialized = True


class TestManagerWithoutHandlers:
    """Tests for managers that don't subscribe to events."""

    def test_works_without_handlers(self) -> None:
        """Manager without handlers still works."""
        bus = EventBus()
        manager = ManagerWithoutHandlers(bus)

        assert manager.initialized
        assert manager._subscriptions == []

    def test_shutdown_without_handlers(self) -> None:
        """Shutdown works with no handlers."""
        bus = EventBus()
        manager = ManagerWithoutHandlers(bus)

        manager.shutdown()  # Should not raise

        assert manager._subscriptions == []
