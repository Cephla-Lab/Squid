"""
Event monitoring for test assertions.

The EventMonitor class subscribes to EventBus events and collects them
for test assertions. It provides methods to wait for specific events
with timeouts, and to assert event sequences.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Generic, List, Optional, Type, TypeVar

from squid.core.events import Event, EventBus

E = TypeVar("E", bound=Event)


@dataclass
class EventRecord:
    """Record of a received event with timestamp."""

    event: Event
    timestamp: float
    event_type: Type[Event]


class EventMonitor:
    """
    Monitors EventBus events and collects them for test assertions.

    This class subscribes to specified event types and stores all received
    events. It provides methods to:
    - Wait for specific events with timeout
    - Get all events of a specific type
    - Assert event sequences
    - Clear collected events

    Usage:
        monitor = EventMonitor(event_bus)
        monitor.subscribe(AcquisitionProgress, AcquisitionFinished)

        # ... run test ...

        result = monitor.wait_for(AcquisitionFinished, timeout_s=30)
        assert result.success

        progress_events = monitor.get_events(AcquisitionProgress)
        assert len(progress_events) > 0
    """

    def __init__(self, event_bus: Optional[EventBus] = None):
        """
        Initialize the event monitor.

        Args:
            event_bus: EventBus instance to monitor. If None, uses the global event_bus.
        """
        if event_bus is None:
            from squid.core.events import event_bus as global_bus
            event_bus = global_bus

        self._event_bus = event_bus
        self._events: List[EventRecord] = []
        self._lock = threading.Lock()
        self._subscribed_types: List[Type[Event]] = []

        # Event waiters: mapping from event type to (condition, predicate, result)
        self._waiters: Dict[Type[Event], List[tuple]] = {}

    def subscribe(self, *event_types: Type[Event]) -> "EventMonitor":
        """
        Subscribe to one or more event types.

        Args:
            event_types: Event classes to subscribe to

        Returns:
            self for chaining
        """
        for event_type in event_types:
            if event_type not in self._subscribed_types:
                self._event_bus.subscribe(event_type, self._on_event)
                self._subscribed_types.append(event_type)
        return self

    def unsubscribe_all(self) -> None:
        """Unsubscribe from all event types."""
        for event_type in self._subscribed_types:
            self._event_bus.unsubscribe(event_type, self._on_event)
        self._subscribed_types.clear()

    def _on_event(self, event: Event) -> None:
        """Handle incoming events."""
        record = EventRecord(
            event=event,
            timestamp=time.time(),
            event_type=type(event),
        )

        with self._lock:
            self._events.append(record)

            # Notify any waiters for this event type
            event_type = type(event)
            if event_type in self._waiters:
                for condition, predicate, result_holder in self._waiters[event_type]:
                    if predicate is None or predicate(event):
                        result_holder.append(event)
                        with condition:
                            condition.notify_all()

    def wait_for(
        self,
        event_type: Type[E],
        timeout_s: float = 30.0,
        predicate: Optional[Callable[[E], bool]] = None,
    ) -> Optional[E]:
        """
        Wait for a specific event type with optional predicate.

        Args:
            event_type: Event class to wait for
            timeout_s: Maximum time to wait in seconds
            predicate: Optional function to filter events (e.g., lambda e: e.success)

        Returns:
            The event if received, None if timeout
        """
        # First check if we already have a matching event
        with self._lock:
            for record in self._events:
                if record.event_type == event_type:
                    if predicate is None or predicate(record.event):
                        return record.event

        # Subscribe if not already subscribed
        if event_type not in self._subscribed_types:
            self.subscribe(event_type)

        # Set up waiter
        condition = threading.Condition()
        result_holder: List[E] = []

        with self._lock:
            if event_type not in self._waiters:
                self._waiters[event_type] = []
            self._waiters[event_type].append((condition, predicate, result_holder))

        try:
            # Wait for event
            deadline = time.time() + timeout_s
            with condition:
                while not result_holder and time.time() < deadline:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    condition.wait(timeout=remaining)

            return result_holder[0] if result_holder else None

        finally:
            # Clean up waiter
            with self._lock:
                if event_type in self._waiters:
                    self._waiters[event_type] = [
                        w for w in self._waiters[event_type]
                        if w[2] is not result_holder
                    ]

    def wait_for_all(
        self,
        event_types: List[Type[Event]],
        timeout_s: float = 30.0,
    ) -> Dict[Type[Event], Optional[Event]]:
        """
        Wait for all specified event types.

        Args:
            event_types: List of event classes to wait for
            timeout_s: Maximum time to wait for all events

        Returns:
            Dict mapping event type to received event (or None if not received)
        """
        results: Dict[Type[Event], Optional[Event]] = {}
        deadline = time.time() + timeout_s

        for event_type in event_types:
            remaining = deadline - time.time()
            if remaining <= 0:
                results[event_type] = None
            else:
                results[event_type] = self.wait_for(event_type, timeout_s=remaining)

        return results

    def get_events(self, event_type: Type[E]) -> List[E]:
        """
        Get all collected events of a specific type.

        Args:
            event_type: Event class to filter by

        Returns:
            List of events of the specified type
        """
        with self._lock:
            return [
                record.event for record in self._events
                if record.event_type == event_type
            ]

    def get_all_events(self) -> List[EventRecord]:
        """Get all collected events with metadata."""
        with self._lock:
            return list(self._events)

    def get_event_count(self, event_type: Type[Event]) -> int:
        """Get count of events of a specific type."""
        with self._lock:
            return sum(1 for r in self._events if r.event_type == event_type)

    def has_event(self, event_type: Type[Event]) -> bool:
        """Check if any event of the specified type was received."""
        return self.get_event_count(event_type) > 0

    def clear(self) -> None:
        """Clear all collected events."""
        with self._lock:
            self._events.clear()

    def assert_event_received(
        self,
        event_type: Type[Event],
        message: Optional[str] = None,
    ) -> None:
        """
        Assert that at least one event of the specified type was received.

        Args:
            event_type: Event class to check for
            message: Optional custom assertion message
        """
        if not self.has_event(event_type):
            msg = message or f"Expected event {event_type.__name__} was not received"
            raise AssertionError(msg)

    def assert_event_sequence(
        self,
        event_types: List[Type[Event]],
        strict: bool = False,
    ) -> None:
        """
        Assert that events were received in the specified order.

        Args:
            event_types: Expected sequence of event types
            strict: If True, no other events can appear between expected events
        """
        with self._lock:
            if strict:
                # Exact match
                actual_types = [r.event_type for r in self._events]
                if actual_types != event_types:
                    raise AssertionError(
                        f"Expected event sequence {[t.__name__ for t in event_types]}, "
                        f"got {[t.__name__ for t in actual_types]}"
                    )
            else:
                # Subsequence match
                expected_idx = 0
                for record in self._events:
                    if expected_idx < len(event_types) and record.event_type == event_types[expected_idx]:
                        expected_idx += 1

                if expected_idx < len(event_types):
                    missing = event_types[expected_idx:]
                    raise AssertionError(
                        f"Event sequence incomplete. Missing: {[t.__name__ for t in missing]}"
                    )

    def __enter__(self) -> "EventMonitor":
        return self

    def __exit__(self, *args) -> None:
        self.unsubscribe_all()
