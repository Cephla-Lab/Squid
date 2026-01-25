"""
Base class for workflow simulators.

Provides common functionality for all simulator types:
- Event publishing
- Event monitoring
- Backend context access
"""

from __future__ import annotations

from typing import Optional, Type, TypeVar, TYPE_CHECKING

from squid.core.events import Event, EventBus
from squid.core.config.test_timing import sleep as test_sleep

if TYPE_CHECKING:
    from tests.harness.core.backend_context import BackendContext
    from tests.harness.core.event_monitor import EventMonitor


E = TypeVar("E", bound=Event)


class BaseSimulator:
    """
    Base class for workflow simulators.

    Provides common functionality shared by all simulators:
    - Event publishing to backend
    - Event monitoring for assertions
    - Access to backend context

    Subclasses should implement workflow-specific methods.

    Usage:
        class MySimulator(BaseSimulator):
            def do_something(self):
                self.publish(SomeCommand(...))
                result = self.wait_for(SomeEvent)
                return result
    """

    def __init__(self, ctx: "BackendContext"):
        """
        Initialize the simulator.

        Args:
            ctx: BackendContext instance providing backend access
        """
        self._ctx = ctx

    @property
    def ctx(self) -> "BackendContext":
        """Get the backend context."""
        return self._ctx

    @property
    def event_bus(self) -> EventBus:
        """Get the event bus."""
        return self._ctx.event_bus

    @property
    def monitor(self) -> "EventMonitor":
        """Get the event monitor."""
        return self._ctx.event_monitor

    def publish(self, command: Event) -> None:
        """
        Publish a command to the event bus.

        Args:
            command: Event/command to publish
        """
        self.event_bus.publish(command)

    def wait_for(
        self,
        event_type: Type[E],
        timeout_s: float = 30.0,
        predicate=None,
    ) -> Optional[E]:
        """
        Wait for a specific event type.

        Args:
            event_type: Event class to wait for
            timeout_s: Maximum time to wait in seconds
            predicate: Optional function to filter events

        Returns:
            The event if received, None if timeout
        """
        return self.monitor.wait_for(event_type, timeout_s, predicate)

    def sleep(self, seconds: float) -> None:
        """
        Sleep for a specified duration.

        Args:
            seconds: Duration to sleep
        """
        test_sleep(seconds)

    def drain(self, timeout_s: float = 0.5) -> int:
        """
        Drain the event bus queue to process pending commands.

        Args:
            timeout_s: Maximum time to wait in seconds

        Returns:
            Number of events processed
        """
        return self.event_bus.drain(timeout_s=timeout_s)

    def reset(self) -> None:
        """
        Reset the simulator state.

        Clears collected events and resets any internal state.
        Subclasses should override to add additional reset logic.
        """
        self.monitor.clear()
