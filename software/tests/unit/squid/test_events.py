"""Tests for EventBus utility."""

from dataclasses import dataclass
from squid.events import Event, EventBus


@dataclass
class TestEvent(Event):
    """Test event for unit tests."""

    message: str


@dataclass
class OtherEvent(Event):
    """Another test event."""

    value: int


class TestEventBus:
    """Test suite for EventBus."""

    def test_subscribe_and_publish(self):
        """Subscribers should receive published events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(message="hello"))

        assert len(received) == 1
        assert received[0].message == "hello"

    def test_multiple_subscribers(self):
        """Multiple subscribers should all receive events."""
        bus = EventBus()
        received_a = []
        received_b = []

        bus.subscribe(TestEvent, lambda e: received_a.append(e))
        bus.subscribe(TestEvent, lambda e: received_b.append(e))

        bus.publish(TestEvent(message="test"))

        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_different_event_types(self):
        """Subscribers only receive their event type."""
        bus = EventBus()
        test_events = []
        other_events = []

        bus.subscribe(TestEvent, lambda e: test_events.append(e))
        bus.subscribe(OtherEvent, lambda e: other_events.append(e))

        bus.publish(TestEvent(message="test"))
        bus.publish(OtherEvent(value=42))

        assert len(test_events) == 1
        assert len(other_events) == 1
        assert test_events[0].message == "test"
        assert other_events[0].value == 42

    def test_unsubscribe(self):
        """Unsubscribed handlers should not receive events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(message="first"))

        bus.unsubscribe(TestEvent, handler)
        bus.publish(TestEvent(message="second"))

        assert len(received) == 1
        assert received[0].message == "first"

    def test_handler_exception_doesnt_crash(self):
        """Exception in handler should not crash bus."""
        bus = EventBus()
        received = []

        def bad_handler(event):
            raise RuntimeError("handler error")

        def good_handler(event):
            received.append(event)

        bus.subscribe(TestEvent, bad_handler)
        bus.subscribe(TestEvent, good_handler)

        # Should not raise
        bus.publish(TestEvent(message="test"))

        # Good handler should still receive event
        assert len(received) == 1

    def test_clear(self):
        """clear() should remove all subscriptions."""
        bus = EventBus()
        received = []

        bus.subscribe(TestEvent, lambda e: received.append(e))
        bus.clear()
        bus.publish(TestEvent(message="test"))

        assert len(received) == 0
