"""Unit tests for BaseController."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from squid.backend.controllers.base import BaseController
from squid.core.events import Event, EventBus, handles


@dataclass
class TestCommand(Event):
    """Test command event."""

    value: int


@dataclass
class TestEvent(Event):
    """Test event."""

    data: str


class TestController(BaseController):
    """Test controller for testing BaseController functionality."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self.received_commands: list[TestCommand] = []
        self.received_events: list[TestEvent] = []

    @handles(TestCommand)
    def _on_command(self, cmd: TestCommand) -> None:
        self.received_commands.append(cmd)

    @handles(TestEvent)
    def _on_event(self, event: TestEvent) -> None:
        self.received_events.append(event)


class TestBaseController:
    """Tests for BaseController base class."""

    def test_auto_subscribes_handlers_on_init(self) -> None:
        """Handlers decorated with @handles are auto-subscribed on init."""
        bus = EventBus()
        controller = TestController(bus)

        # Publish events and drain to process synchronously
        bus.publish(TestCommand(value=42))
        bus.publish(TestEvent(data="hello"))
        bus.drain()

        assert len(controller.received_commands) == 1
        assert controller.received_commands[0].value == 42
        assert len(controller.received_events) == 1
        assert controller.received_events[0].data == "hello"

    def test_shutdown_unsubscribes_all_handlers(self) -> None:
        """Shutdown unsubscribes all event handlers."""
        bus = EventBus()
        controller = TestController(bus)

        # Verify handlers work before shutdown
        bus.publish(TestCommand(value=1))
        bus.drain()
        assert len(controller.received_commands) == 1

        # Shutdown
        controller.shutdown()

        # Events should no longer be received
        bus.publish(TestCommand(value=2))
        bus.drain()
        assert len(controller.received_commands) == 1  # Still just 1

    def test_event_bus_accessible(self) -> None:
        """Event bus is accessible via _event_bus attribute."""
        bus = EventBus()
        controller = TestController(bus)

        assert controller._event_bus is bus

    def test_logger_available(self) -> None:
        """Logger is available via _log attribute."""
        bus = EventBus()
        controller = TestController(bus)

        assert controller._log is not None
        # Logger name includes squid prefix
        assert "TestController" in controller._log.name

    def test_subscriptions_tracked(self) -> None:
        """Subscriptions are tracked in _subscriptions list."""
        bus = EventBus()
        controller = TestController(bus)

        # Should have 2 subscriptions (TestCommand, TestEvent)
        assert len(controller._subscriptions) == 2

    def test_shutdown_clears_subscriptions(self) -> None:
        """Shutdown clears the subscriptions list."""
        bus = EventBus()
        controller = TestController(bus)

        assert len(controller._subscriptions) > 0

        controller.shutdown()

        assert len(controller._subscriptions) == 0

    def test_multiple_shutdown_safe(self) -> None:
        """Multiple shutdown calls are safe."""
        bus = EventBus()
        controller = TestController(bus)

        controller.shutdown()
        controller.shutdown()  # Should not raise

        assert len(controller._subscriptions) == 0


class ControllerWithExtraShutdown(BaseController):
    """Controller that does extra work in shutdown."""

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__(event_bus)
        self.extra_shutdown_called = False
        self.shutdown_order: list[str] = []

    @handles(TestCommand)
    def _on_command(self, cmd: TestCommand) -> None:
        pass

    def shutdown(self) -> None:
        self.shutdown_order.append("extra")
        self.extra_shutdown_called = True
        super().shutdown()
        self.shutdown_order.append("base")


class TestControllerWithOverriddenShutdown:
    """Tests for controllers that override shutdown."""

    def test_custom_shutdown_with_super(self) -> None:
        """Custom shutdown can call super().shutdown()."""
        bus = EventBus()
        controller = ControllerWithExtraShutdown(bus)

        controller.shutdown()

        assert controller.extra_shutdown_called
        assert controller.shutdown_order == ["extra", "base"]
        assert len(controller._subscriptions) == 0

    def test_handlers_work_before_custom_shutdown(self) -> None:
        """Handlers work before custom shutdown is called."""
        bus = EventBus()
        controller = ControllerWithExtraShutdown(bus)

        # Should have 1 subscription
        assert len(controller._subscriptions) == 1

        bus.publish(TestCommand(value=1))
        # Handler runs (no-op, but subscription exists)

        controller.shutdown()
        assert controller._subscriptions == []
