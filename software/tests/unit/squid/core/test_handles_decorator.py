"""Tests for the @handles decorator and auto_subscribe/auto_unsubscribe functions."""

from dataclasses import dataclass

from squid.core.events import (
    Event,
    EventBus,
    auto_subscribe,
    auto_unsubscribe,
    handles,
)


@dataclass
class SampleEventA(Event):
    """Test event type A."""

    value: int


@dataclass
class SampleEventB(Event):
    """Test event type B."""

    message: str


@dataclass
class SampleEventC(Event):
    """Test event type C."""

    data: float


class TestHandlesDecorator:
    """Tests for the @handles decorator."""

    def test_handles_marks_method_with_single_event_type(self):
        """@handles decorator marks method with _handles_events attribute."""

        class Handler:
            @handles(SampleEventA)
            def on_event_a(self, event):
                pass

        assert hasattr(Handler.on_event_a, "_handles_events")
        assert Handler.on_event_a._handles_events == [SampleEventA]

    def test_handles_marks_method_with_multiple_event_types(self):
        """@handles with multiple event types stores all of them."""

        class Handler:
            @handles(SampleEventA, SampleEventB, SampleEventC)
            def on_multiple(self, event):
                pass

        assert Handler.on_multiple._handles_events == [SampleEventA, SampleEventB, SampleEventC]

    def test_handles_preserves_method_callable(self):
        """@handles decorator preserves the method as callable."""

        class Handler:
            @handles(SampleEventA)
            def on_event_a(self, event):
                return event.value * 2

        handler = Handler()
        event = SampleEventA(value=21)
        assert handler.on_event_a(event) == 42

    def test_handles_with_empty_event_list_raises(self):
        """@handles with no event types should still work (creates empty list)."""

        class Handler:
            @handles()
            def on_nothing(self, event):
                pass

        assert Handler.on_nothing._handles_events == []


class TestAutoSubscribe:
    """Tests for the auto_subscribe function."""

    def test_auto_subscribe_finds_decorated_methods(self):
        """auto_subscribe finds all @handles-decorated methods."""

        class Handler:
            @handles(SampleEventA)
            def on_event_a(self, event):
                pass

            @handles(SampleEventB)
            def on_event_b(self, event):
                pass

            def regular_method(self):
                pass

        event_bus = EventBus()
        handler = Handler()

        subscriptions = auto_subscribe(handler, event_bus)

        assert len(subscriptions) == 2
        event_types = [et for et, _ in subscriptions]
        assert SampleEventA in event_types
        assert SampleEventB in event_types

    def test_auto_subscribe_subscribes_to_event_bus(self):
        """auto_subscribe actually registers handlers with event_bus."""

        class Handler:
            def __init__(self):
                self.received = []

            @handles(SampleEventA)
            def on_event_a(self, event):
                self.received.append(event)

        event_bus = EventBus()
        handler = Handler()

        auto_subscribe(handler, event_bus)

        # Manually dispatch (not using queue for simpler test)
        event = SampleEventA(value=42)
        event_bus._dispatch(event)

        assert len(handler.received) == 1
        assert handler.received[0].value == 42

    def test_auto_subscribe_handles_multiple_events_per_method(self):
        """auto_subscribe registers method for each event type it handles."""

        class Handler:
            def __init__(self):
                self.received = []

            @handles(SampleEventA, SampleEventB)
            def on_either(self, event):
                self.received.append(event)

        event_bus = EventBus()
        handler = Handler()

        subscriptions = auto_subscribe(handler, event_bus)

        # Should have 2 subscriptions (one for each event type)
        assert len(subscriptions) == 2

        # Both event types should trigger the handler
        event_bus._dispatch(SampleEventA(value=1))
        event_bus._dispatch(SampleEventB(message="hello"))

        assert len(handler.received) == 2

    def test_auto_subscribe_returns_empty_list_for_no_handlers(self):
        """auto_subscribe returns empty list if no @handles methods exist."""

        class PlainClass:
            def regular_method(self):
                pass

        event_bus = EventBus()
        obj = PlainClass()

        subscriptions = auto_subscribe(obj, event_bus)

        assert subscriptions == []

    def test_auto_subscribe_skips_non_callable_attributes(self):
        """auto_subscribe only processes callable attributes."""

        class Handler:
            some_data = "not callable"
            _handles_events = ["this is not a method"]

            @handles(SampleEventA)
            def on_event(self, event):
                pass

        event_bus = EventBus()
        handler = Handler()

        subscriptions = auto_subscribe(handler, event_bus)

        # Should only find the actual decorated method
        assert len(subscriptions) == 1


class TestAutoUnsubscribe:
    """Tests for the auto_unsubscribe function."""

    def test_auto_unsubscribe_removes_all_subscriptions(self):
        """auto_unsubscribe removes all subscriptions from event_bus."""

        class Handler:
            def __init__(self):
                self.received = []

            @handles(SampleEventA)
            def on_event_a(self, event):
                self.received.append(event)

        event_bus = EventBus()
        handler = Handler()

        subscriptions = auto_subscribe(handler, event_bus)
        auto_unsubscribe(subscriptions, event_bus)

        # Event should not be received after unsubscribe
        event_bus._dispatch(SampleEventA(value=42))

        assert len(handler.received) == 0

    def test_auto_unsubscribe_with_empty_list(self):
        """auto_unsubscribe handles empty subscription list gracefully."""
        event_bus = EventBus()

        # Should not raise
        auto_unsubscribe([], event_bus)

    def test_auto_unsubscribe_only_affects_specified_subscriptions(self):
        """auto_unsubscribe only removes the specified subscriptions."""

        class Handler1:
            def __init__(self):
                self.received = []

            @handles(SampleEventA)
            def on_event(self, event):
                self.received.append(event)

        class Handler2:
            def __init__(self):
                self.received = []

            @handles(SampleEventA)
            def on_event(self, event):
                self.received.append(event)

        event_bus = EventBus()
        handler1 = Handler1()
        handler2 = Handler2()

        subs1 = auto_subscribe(handler1, event_bus)
        subs2 = auto_subscribe(handler2, event_bus)

        # Unsubscribe only handler1
        auto_unsubscribe(subs1, event_bus)

        event_bus._dispatch(SampleEventA(value=42))

        # handler1 should not receive, handler2 should receive
        assert len(handler1.received) == 0
        assert len(handler2.received) == 1


class TestIntegration:
    """Integration tests for the subscription pattern."""

    def test_full_lifecycle(self):
        """Test complete subscribe/receive/unsubscribe lifecycle."""

        class MyService:
            def __init__(self, event_bus):
                self._event_bus = event_bus
                self.events_received = []
                self._subscriptions = auto_subscribe(self, event_bus)

            @handles(SampleEventA)
            def _on_event_a(self, event):
                self.events_received.append(("A", event.value))

            @handles(SampleEventB)
            def _on_event_b(self, event):
                self.events_received.append(("B", event.message))

            def shutdown(self):
                auto_unsubscribe(self._subscriptions, self._event_bus)

        event_bus = EventBus()
        service = MyService(event_bus)

        # Should receive events
        event_bus._dispatch(SampleEventA(value=1))
        event_bus._dispatch(SampleEventB(message="hello"))

        assert len(service.events_received) == 2
        assert ("A", 1) in service.events_received
        assert ("B", "hello") in service.events_received

        # After shutdown, should not receive
        service.shutdown()
        service.events_received.clear()

        event_bus._dispatch(SampleEventA(value=2))
        event_bus._dispatch(SampleEventB(message="world"))

        assert len(service.events_received) == 0
