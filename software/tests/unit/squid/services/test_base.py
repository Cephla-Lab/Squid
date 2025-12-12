# tests/squid/services/test_base.py
"""Tests for BaseService class."""


class TestBaseService:
    """Test suite for BaseService."""

    def test_init_requires_event_bus(self):
        """BaseService requires an EventBus."""
        from squid.mcs.services.base import BaseService
        from squid.core.events import EventBus

        bus = EventBus()

        # Can't instantiate ABC directly, need concrete class
        class ConcreteService(BaseService):
            pass

        service = ConcreteService(bus)
        assert service._event_bus is bus

    def test_subscribe_registers_handler(self):
        """subscribe() should register handler with event bus."""
        from squid.mcs.services.base import BaseService
        from squid.core.events import EventBus, Event
        from dataclasses import dataclass

        @dataclass
        class TestEvent(Event):
            value: int

        class ConcreteService(BaseService):
            def __init__(self, bus):
                super().__init__(bus)
                self.received = []
                self.subscribe(TestEvent, self.handle_test)

            def handle_test(self, event):
                self.received.append(event)

        bus = EventBus()
        service = ConcreteService(bus)

        # Publish event
        bus.publish(TestEvent(value=42))
        bus.drain()

        assert len(service.received) == 1
        assert service.received[0].value == 42

    def test_publish_sends_event(self):
        """publish() should send event through event bus."""
        from squid.mcs.services.base import BaseService
        from squid.core.events import EventBus, Event
        from dataclasses import dataclass

        @dataclass
        class TestEvent(Event):
            value: int

        class ConcreteService(BaseService):
            pass

        bus = EventBus()
        service = ConcreteService(bus)

        received = []
        bus.subscribe(TestEvent, lambda e: received.append(e))

        service.publish(TestEvent(value=99))
        bus.drain()

        assert len(received) == 1
        assert received[0].value == 99

    def test_shutdown_unsubscribes(self):
        """shutdown() should unsubscribe from all events."""
        from squid.mcs.services.base import BaseService
        from squid.core.events import EventBus, Event
        from dataclasses import dataclass

        @dataclass
        class TestEvent(Event):
            value: int

        class ConcreteService(BaseService):
            def __init__(self, bus):
                super().__init__(bus)
                self.received = []
                self.subscribe(TestEvent, self.handle_test)

            def handle_test(self, event):
                self.received.append(event)

        bus = EventBus()
        service = ConcreteService(bus)

        # Shutdown
        service.shutdown()

        # Publish should not reach service
        bus.publish(TestEvent(value=42))
        bus.drain()
        assert len(service.received) == 0
