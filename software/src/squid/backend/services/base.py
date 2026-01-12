# squid/services/base.py
"""Base class for all services."""

import functools
from abc import ABC
from typing import Any, List, Optional, Tuple, Type, Callable, TypeVar

import squid.core.logging
from squid.core.events import EventBus, Event
from squid.core.mode_gate import GlobalModeGate

E = TypeVar("E", bound=Event)
F = TypeVar("F", bound=Callable[..., Any])


def gated_command(
    method: Optional[F] = None,
    *,
    on_blocked: Optional[Callable[[Any, Event], Any]] = None,
) -> F:
    """Decorator that skips command handler when mode gate is active.

    Use this on service command handlers that should be blocked during
    acquisition or other exclusive operations.

    Usage:
        class CameraService(BaseService):
            @gated_command
            def _on_set_exposure(self, cmd: SetExposureCommand) -> None:
                self.set_exposure_time(cmd.exposure_time_ms)

    When mode gate is active, the decorated method returns None without
    executing, and a debug message is logged.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(self: "BaseService", event: Event) -> Any:
            if self._blocked_for_ui_hardware_commands():
                self._log.debug("Ignoring %s due to mode gate", type(event).__name__)
                if on_blocked is not None:
                    try:
                        on_blocked(self, event)
                    except Exception:
                        self._log.exception("Blocked handler hook failed for %s", type(event).__name__)
                return None
            return func(self, event)

        return wrapper  # type: ignore[return-value]

    if method is not None:
        return decorator(method)
    return decorator  # type: ignore[return-value]


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

    def __init__(self, event_bus: EventBus, mode_gate: Optional[GlobalModeGate] = None):
        """
        Initialize service with event bus.

        Args:
            event_bus: EventBus for pub/sub communication
        """
        self._event_bus = event_bus
        self._mode_gate = mode_gate
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._subscriptions: List[Tuple[Type[Event], Callable]] = []

    def _blocked_for_ui_hardware_commands(self) -> bool:
        return bool(self._mode_gate and self._mode_gate.blocked_for_ui_hardware_commands())

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
