# tests/squid/test_application_services.py
"""Tests for ApplicationContext service integration."""
import pytest

# Skip all tests in this module if hardware dependencies are not available
pytest.importorskip("serial")


class TestApplicationContextServices:
    """Test ApplicationContext service integration."""

    def test_context_has_services(self):
        """ApplicationContext should create services."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services is not None
        context.shutdown()

    def test_services_has_camera(self):
        """Services should include camera service."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services.get('camera') is not None
        context.shutdown()

    def test_services_has_stage(self):
        """Services should include stage service."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services.get('stage') is not None
        context.shutdown()

    def test_services_has_peripheral(self):
        """Services should include peripheral service."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.services.get('peripheral') is not None
        context.shutdown()
