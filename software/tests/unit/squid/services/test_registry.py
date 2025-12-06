# tests/squid/services/test_registry.py
"""Tests for ServiceRegistry."""


class TestServiceRegistry:
    """Test suite for ServiceRegistry."""

    def test_register_and_get(self):
        """Should register and retrieve services by name."""
        from squid.services import ServiceRegistry, BaseService
        from squid.events import EventBus

        class MockService(BaseService):
            pass

        bus = EventBus()
        registry = ServiceRegistry(bus)
        service = MockService(bus)

        registry.register("test", service)

        assert registry.get("test") is service

    def test_get_unknown_returns_none(self):
        """get() should return None for unknown service."""
        from squid.services import ServiceRegistry
        from squid.events import EventBus

        registry = ServiceRegistry(EventBus())

        assert registry.get("unknown") is None

    def test_shutdown_calls_all_services(self):
        """shutdown() should call shutdown on all registered services."""
        from squid.services import ServiceRegistry, BaseService
        from squid.events import EventBus

        class MockService(BaseService):
            def __init__(self, bus):
                super().__init__(bus)
                self.shutdown_called = False

            def shutdown(self):
                super().shutdown()
                self.shutdown_called = True

        bus = EventBus()
        registry = ServiceRegistry(bus)

        service1 = MockService(bus)
        service2 = MockService(bus)
        registry.register("s1", service1)
        registry.register("s2", service2)

        registry.shutdown()

        assert service1.shutdown_called
        assert service2.shutdown_called
