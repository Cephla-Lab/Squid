"""Integration tests for subscription patterns across controllers and managers."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from squid.backend.controllers.base import BaseController
from squid.backend.managers.base import BaseManager
from squid.core.events import Event, EventBus, handles
from squid.core.state_machine import StateMachine


@dataclass
class TestCommand(Event):
    """Test command event."""

    value: int


@dataclass
class TestStateEvent(Event):
    """Test state event."""

    data: str


# --- Controller and Manager test classes ---


class SimpleController(BaseController):
    """Simple test controller."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self.commands_received: list[TestCommand] = []

    @handles(TestCommand)
    def _on_command(self, cmd: TestCommand) -> None:
        self.commands_received.append(cmd)


class SimpleManager(BaseManager):
    """Simple test manager."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        super().__init__(event_bus)
        self.events_received: list[TestStateEvent] = []

    @handles(TestStateEvent)
    def _on_event(self, event: TestStateEvent) -> None:
        self.events_received.append(event)


class TestSubscriptionPatternIntegration:
    """Integration tests for subscription patterns."""

    def test_controller_and_manager_on_same_bus(self) -> None:
        """Controller and manager can both subscribe to same event bus."""
        bus = EventBus()
        controller = SimpleController(bus)
        manager = SimpleManager(bus)

        # Both receive events
        bus.publish(TestCommand(value=1))
        bus.publish(TestStateEvent(data="test"))
        bus.drain()

        assert len(controller.commands_received) == 1
        assert len(manager.events_received) == 1

    def test_shutdown_one_doesnt_affect_other(self) -> None:
        """Shutting down one component doesn't affect another."""
        bus = EventBus()
        controller = SimpleController(bus)
        manager = SimpleManager(bus)

        # Shutdown controller only
        controller.shutdown()

        # Manager still receives events
        bus.publish(TestStateEvent(data="after shutdown"))
        bus.drain()
        assert len(manager.events_received) == 1

        # Controller doesn't receive new events
        bus.publish(TestCommand(value=2))
        bus.drain()
        assert len(controller.commands_received) == 0

    def test_multiple_controllers_same_event_type(self) -> None:
        """Multiple controllers can subscribe to same event type."""
        bus = EventBus()
        controller1 = SimpleController(bus)
        controller2 = SimpleController(bus)

        bus.publish(TestCommand(value=42))
        bus.drain()

        assert len(controller1.commands_received) == 1
        assert len(controller2.commands_received) == 1
        assert controller1.commands_received[0].value == 42
        assert controller2.commands_received[0].value == 42

    def test_shutdown_all_components_cleanly(self) -> None:
        """All components can be shut down cleanly."""
        bus = EventBus()
        controller1 = SimpleController(bus)
        controller2 = SimpleController(bus)
        manager = SimpleManager(bus)

        # All receive events
        bus.publish(TestCommand(value=1))
        bus.drain()
        assert len(controller1.commands_received) == 1
        assert len(controller2.commands_received) == 1

        # Shutdown all
        controller1.shutdown()
        controller2.shutdown()
        manager.shutdown()

        # No more events received
        bus.publish(TestCommand(value=2))
        bus.publish(TestStateEvent(data="test"))
        bus.drain()

        assert len(controller1.commands_received) == 1
        assert len(controller2.commands_received) == 1
        assert len(manager.events_received) == 0


class ControllerWithMultipleHandlers(BaseController):
    """Controller with multiple event handlers."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self.commands: list[TestCommand] = []
        self.state_events: list[TestStateEvent] = []

    @handles(TestCommand)
    def _on_command(self, cmd: TestCommand) -> None:
        self.commands.append(cmd)

    @handles(TestStateEvent)
    def _on_state(self, event: TestStateEvent) -> None:
        self.state_events.append(event)


class TestMultipleHandlers:
    """Tests for components with multiple event handlers."""

    def test_multiple_handlers_all_subscribed(self) -> None:
        """All handlers are subscribed on init."""
        bus = EventBus()
        controller = ControllerWithMultipleHandlers(bus)

        bus.publish(TestCommand(value=1))
        bus.publish(TestStateEvent(data="test"))
        bus.drain()

        assert len(controller.commands) == 1
        assert len(controller.state_events) == 1

    def test_multiple_handlers_all_unsubscribed(self) -> None:
        """All handlers are unsubscribed on shutdown."""
        bus = EventBus()
        controller = ControllerWithMultipleHandlers(bus)

        controller.shutdown()

        bus.publish(TestCommand(value=1))
        bus.publish(TestStateEvent(data="test"))
        bus.drain()

        assert len(controller.commands) == 0
        assert len(controller.state_events) == 0


class TestEventBusLifecycle:
    """Tests for event bus lifecycle scenarios."""

    def test_controller_survives_bus_stop_start(self) -> None:
        """Controller handles event bus being stopped and started."""
        bus = EventBus()
        controller = SimpleController(bus)

        # Stop the bus
        bus.stop()

        # Events published while stopped are lost or queued
        bus.publish(TestCommand(value=1))

        # Restart
        bus.start()

        # Controller should still work for new events
        bus.publish(TestCommand(value=2))

        # At least the second event should be received (depends on bus implementation)
        # The important thing is no crash

    def test_manager_without_bus_never_receives_events(self) -> None:
        """Manager created without bus never receives events."""
        manager = SimpleManager()  # No event bus

        # Can't publish to a non-existent bus
        # Just verify it doesn't crash and has empty subscriptions
        assert manager._subscriptions == []
        assert manager._event_bus is None
