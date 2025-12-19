"""Tests for ApplicationContext."""


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
        assert context.controllers.autofocus is not None
        assert context.controllers.multipoint is not None
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


class TestControllerWiring:
    def test_controllers_assigned_to_microscope(self):
        from squid.application import ApplicationContext

        context = ApplicationContext(simulation=True)

        assert context.microscope.live_controller is context.controllers.live
        assert context.microscope.stream_handler is context.controllers.stream_handler

        context.shutdown()
