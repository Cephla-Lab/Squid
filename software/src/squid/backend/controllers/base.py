"""Base class for controllers with event bus subscription support."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Tuple, Callable

import squid.core.logging
from squid.core.events import auto_subscribe, auto_unsubscribe

if TYPE_CHECKING:
    from squid.core.events import EventBus


class BaseController:
    """Base class for controllers with event bus subscription support.

    Provides automatic subscription management using the @handles decorator.
    Subclasses define handlers with @handles(EventType) and get automatic
    subscription via auto_subscribe in __init__.

    Usage:
        class MyController(BaseController):
            def __init__(self, event_bus, other_deps):
                super().__init__(event_bus)
                self._other = other_deps

            @handles(SomeCommand)
            def _on_command(self, cmd: SomeCommand) -> None:
                ...

    Attributes:
        _event_bus: The EventBus instance for pub/sub communication
        _log: Logger instance for this controller
        _subscriptions: List of (event_type, handler) tuples for cleanup
    """

    _event_bus: "EventBus"
    _log: logging.Logger
    _subscriptions: List[Tuple[type, Callable]]

    def __init__(self, event_bus: "EventBus") -> None:
        """Initialize controller with event bus and auto-subscribe handlers.

        Args:
            event_bus: EventBus instance for pub/sub communication
        """
        self._event_bus = event_bus
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._subscriptions = auto_subscribe(self, self._event_bus)

    def shutdown(self) -> None:
        """Unsubscribe from all events.

        Subclasses with additional cleanup should override and call super().shutdown()
        after their cleanup logic:

            def shutdown(self) -> None:
                self.stop()  # custom cleanup
                super().shutdown()
        """
        auto_unsubscribe(self._subscriptions, self._event_bus)
        self._subscriptions = []
