"""UI-aware event bus wrapper.

UIEventBus wraps the core EventBus and ensures all handler callbacks
are executed on the Qt main thread, making it safe for widget updates.
"""
from typing import Callable, Dict, Tuple, Type
import threading

from squid.events import Event, EventBus
from squid.qt_event_dispatcher import QtEventDispatcher
import squid.logging

_log = squid.logging.get_logger(__name__)


class UIEventBus:
    """Thread-safe event bus for UI components.

    This wrapper ensures all subscribed handlers are called on the Qt
    main thread, regardless of which thread publishes the event.

    Services and controllers should use the core EventBus directly.
    Widgets should use UIEventBus for thread-safe updates.

    Usage:
        core_bus = EventBus()
        dispatcher = QtEventDispatcher()
        ui_bus = UIEventBus(core_bus, dispatcher)

        # Widget subscribes via ui_bus
        ui_bus.subscribe(StagePositionChanged, self._on_position_changed)

        # Any code can publish via core_bus or ui_bus
        core_bus.publish(StagePositionChanged(x=1.0, y=2.0, z=0.5))
        # Handler runs on Qt main thread
    """

    def __init__(self, core_bus: EventBus, dispatcher: QtEventDispatcher):
        self._core_bus = core_bus
        self._dispatcher = dispatcher
        self._wrapper_map: Dict[Tuple[Type[Event], Callable], Callable] = {}
        self._lock = threading.RLock()

    def publish(self, event: Event) -> None:
        """Publish an event to the core bus.

        Events are delivered to all subscribers (both core and UI).
        UI subscribers will have their handlers run on the Qt main thread.
        """
        self._core_bus.publish(event)

    def subscribe(
        self,
        event_type: Type[Event],
        handler: Callable[[Event], None]
    ) -> None:
        """Subscribe a handler that will run on the Qt main thread.

        Args:
            event_type: The event class to subscribe to
            handler: Callback that receives the event (runs on main thread)
        """
        with self._lock:
            # Create wrapper that marshals to main thread
            def wrapper(event: Event, _handler=handler) -> None:
                is_main = self._dispatcher.is_main_thread()
                thread_name = threading.current_thread().name
                if is_main:
                    # Already on main thread, call directly (optimization)
                    _log.info(f"UIEventBus: {type(event).__name__} on main thread ({thread_name}), calling directly")
                    _handler(event)
                else:
                    # Marshal to main thread via Qt signal
                    _log.info(f"UIEventBus: {type(event).__name__} from {thread_name}, dispatching to main thread")
                    self._dispatcher.dispatch.emit(_handler, event)

            self._wrapper_map[(event_type, handler)] = wrapper
            self._core_bus.subscribe(event_type, wrapper)
            _log.debug(f"UIEventBus: subscribed {handler} to {event_type.__name__}")

    def unsubscribe(
        self,
        event_type: Type[Event],
        handler: Callable[[Event], None]
    ) -> None:
        """Unsubscribe a handler.

        Args:
            event_type: The event class to unsubscribe from
            handler: The original handler passed to subscribe()
        """
        with self._lock:
            wrapper = self._wrapper_map.pop((event_type, handler), None)

        if wrapper is not None:
            self._core_bus.unsubscribe(event_type, wrapper)
            _log.debug(f"UIEventBus: unsubscribed {handler} from {event_type.__name__}")
        else:
            _log.warning(
                f"UIEventBus: tried to unsubscribe unknown handler {handler} "
                f"from {event_type.__name__}"
            )
