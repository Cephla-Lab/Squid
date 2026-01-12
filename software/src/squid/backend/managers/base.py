"""Base class for managers with event bus subscription support."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Tuple, Callable, Optional

import squid.core.logging
from squid.core.events import auto_subscribe, auto_unsubscribe

if TYPE_CHECKING:
    from squid.core.events import EventBus


class BaseManager:
    """Base class for managers with event bus subscription support.

    Provides automatic subscription management using the @handles decorator.
    Subclasses define handlers with @handles(EventType) and get automatic
    subscription via auto_subscribe in __init__.

    Supports optional event_bus (some managers may not need events).

    Usage:
        class MyManager(BaseManager):
            def __init__(self, event_bus=None, other_deps):
                super().__init__(event_bus)
                self._other = other_deps

            @handles(SomeCommand)
            def _on_command(self, cmd: SomeCommand) -> None:
                ...

    Attributes:
        _event_bus: Optional EventBus instance for pub/sub communication
        _log: Logger instance for this manager
        _subscriptions: List of (event_type, handler) tuples for cleanup
    """

    _event_bus: Optional["EventBus"]
    _log: logging.Logger
    _subscriptions: List[Tuple[type, Callable]]

    def __init__(self, event_bus: Optional["EventBus"] = None) -> None:
        """Initialize manager with optional event bus and auto-subscribe handlers.

        Args:
            event_bus: Optional EventBus instance for pub/sub communication
        """
        self._event_bus = event_bus
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._subscriptions = []
        if event_bus:
            self._subscriptions = auto_subscribe(self, event_bus)

    def shutdown(self) -> None:
        """Unsubscribe from all events.

        Subclasses with additional cleanup should override and call super().shutdown()
        after their cleanup logic:

            def shutdown(self) -> None:
                self.cleanup()  # custom cleanup
                super().shutdown()
        """
        if self._event_bus:
            auto_unsubscribe(self._subscriptions, self._event_bus)
        self._subscriptions = []
