"""Tests for ApplicationContext."""
import pytest
from dataclasses import dataclass
from typing import Optional


class TestControllers:
    """Test suite for Controllers dataclass."""

    def test_controllers_creation(self):
        """Should create Controllers with required fields."""
        from squid.application import Controllers

        # Mock objects for testing
        mock_live = object()
        mock_stream_handler = object()

        controllers = Controllers(
            live=mock_live,
            stream_handler=mock_stream_handler,
        )

        assert controllers.live is mock_live
        assert controllers.stream_handler is mock_stream_handler
        assert controllers.multipoint is None  # Optional


class TestApplicationContext:
    """Test suite for ApplicationContext."""

    def test_creates_microscope(self):
        """Should create microscope in simulation mode."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.microscope is not None
        context.shutdown()

    def test_creates_controllers(self):
        """Should expose controllers from microscope."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.controllers is not None
        assert context.controllers.live is not None
        assert context.controllers.stream_handler is not None
        context.shutdown()

    def test_shutdown_doesnt_crash(self):
        """Shutdown should complete without errors."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)
        context.shutdown()  # Should not raise

    def test_is_simulation_flag(self):
        """Should track simulation mode."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)
        assert context.is_simulation is True
        context.shutdown()


class TestExternalControllerCreation:
    """Test suite for external controller creation mode."""

    def test_external_controller_creation(self):
        """Should create controllers externally when flag is set."""
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True, external_controller_creation=True)

        assert context.microscope is not None
        assert context.controllers is not None
        assert context.controllers.live is not None
        assert context.controllers.stream_handler is not None

        # Controllers should be assigned to microscope
        assert context.microscope.live_controller is context.controllers.live
        assert context.microscope.stream_handler is context.controllers.stream_handler

        context.shutdown()

    def test_external_vs_internal_creation_equivalent(self):
        """External and internal creation should produce equivalent results."""
        from squid.application import ApplicationContext

        # Internal creation (default)
        ctx_internal = ApplicationContext(simulation=True, external_controller_creation=False)

        # External creation
        ctx_external = ApplicationContext(simulation=True, external_controller_creation=True)

        # Both should have valid controllers
        assert ctx_internal.controllers.live is not None
        assert ctx_external.controllers.live is not None
        assert ctx_internal.controllers.stream_handler is not None
        assert ctx_external.controllers.stream_handler is not None

        # Both microscopes should have controllers assigned
        assert ctx_internal.microscope.live_controller is not None
        assert ctx_external.microscope.live_controller is not None

        ctx_internal.shutdown()
        ctx_external.shutdown()
