# squid/services/base.py
"""Base class for all services."""

from abc import ABC
from typing import List, Tuple, Type, Callable, TypeVar

import squid.core.logging
from squid.core.events import EventBus, Event

E = TypeVar("E", bound=Event)


class BaseService(ABC):
    """
    Base class for service layer implementations.

    Services orchestrate hardware operations and manage state.
    They subscribe to command events and publish state events.

    Usage:
        class CameraService(BaseService):
            def __init__(self, camera, event_bus):
                super().__init__(event_bus)
                self._camera = camera
                self.subscribe(SetExposureCommand, self._on_set_exposure)

            def _on_set_exposure(self, event):
                self._camera.set_exposure_time(event.exposure_time_ms)
                self.publish(ExposureTimeChanged(event.exposure_time_ms))
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize service with event bus.

        Args:
            event_bus: EventBus for pub/sub communication
        """
        self._event_bus = event_bus
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._subscriptions: List[Tuple[Type[Event], Callable]] = []

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: Type of event to subscribe to
            handler: Callable to handle events
        """
        self._event_bus.subscribe(event_type, handler)  # type: ignore
        self._subscriptions.append((event_type, handler))  # type: ignore
        self._log.debug(f"Subscribed to {event_type.__name__}")

    def publish(self, event: Event):
        """
        Publish an event.

        Args:
            event: Event to publish
        """
        self._log.debug(f"Publishing {type(event).__name__}")
        self._event_bus.publish(event)

    def shutdown(self):
        """Unsubscribe from all events and clean up."""
        for event_type, handler in self._subscriptions:
            self._event_bus.unsubscribe(event_type, handler)
            self._log.debug(f"Unsubscribed from {event_type.__name__}")
        self._subscriptions.clear()
