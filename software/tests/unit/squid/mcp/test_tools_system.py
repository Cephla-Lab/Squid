"""Tests for MCP system tools."""

import pytest
from unittest.mock import MagicMock, patch

# We need to mock the server module before importing system tools
# because it imports get_app and mcp at module level


@pytest.fixture
def mock_app():
    """Create a mock ApplicationContext."""
    app = MagicMock()
    app.mode_gate.get_current_mode.return_value = "IDLE"
    app.mode_gate.is_blocked_for_commands.return_value = False

    # Mock services
    stage_service = MagicMock()
    stage_service.position_x_mm = 10.0
    stage_service.position_y_mm = 20.0
    stage_service.position_z_mm = 5.0

    camera_service = MagicMock()
    camera_service.exposure_time_ms = 100.0
    camera_service.analog_gain = 2.0

    app.services.get.side_effect = lambda name: {
        "stage": stage_service,
        "camera": camera_service,
    }.get(name)

    return app


@pytest.fixture
def mock_mcp():
    """Create a mock FastMCP server."""
    mcp = MagicMock()
    # Make the tool decorator just return the function unchanged
    mcp.tool.return_value = lambda fn: fn
    return mcp


class TestPing:
    """Tests for ping tool."""

    def test_ping_returns_ok(self, mock_app, mock_mcp):
        """Ping should return ok status."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                from squid.mcp.tools.system import ping

                result = ping()

        assert result["status"] == "ok"
        assert "running" in result["message"].lower()


class TestGetStatus:
    """Tests for get_status tool."""

    def test_returns_mode_and_position(self, mock_app, mock_mcp):
        """get_status should return mode and position info."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                # Re-import to pick up mocked globals
                import importlib
                import squid.mcp.tools.system as system_module

                importlib.reload(system_module)

                result = system_module.get_status()

        assert result["mode"] == "IDLE"
        assert result["position"]["x_mm"] == 10.0
        assert result["position"]["y_mm"] == 20.0
        assert result["camera"]["exposure_ms"] == 100.0


class TestGetMode:
    """Tests for get_mode tool."""

    def test_returns_current_mode(self, mock_app, mock_mcp):
        """get_mode should return the current mode string."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.system as system_module

                importlib.reload(system_module)

                result = system_module.get_mode()

        assert result == "IDLE"


class TestPythonExec:
    """Tests for python_exec tool."""

    def test_disabled_by_default(self, mock_app, mock_mcp):
        """python_exec should be disabled by default."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.system as system_module

                importlib.reload(system_module)

                result = system_module.python_exec("print('hello')")

        assert "error" in result
        assert result["enabled"] is False

    def test_executes_when_enabled(self, mock_app, mock_mcp):
        """python_exec should execute code when enabled."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.system as system_module

                importlib.reload(system_module)

                # Enable python exec
                system_module.enable_python_exec(True)

                result = system_module.python_exec("x = 1 + 1")

        assert result["success"] is True

    def test_returns_error_on_exception(self, mock_app, mock_mcp):
        """python_exec should return error on exception."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.system as system_module

                importlib.reload(system_module)

                system_module.enable_python_exec(True)

                result = system_module.python_exec("raise ValueError('test error')")

        assert result["success"] is False
        assert "test error" in result["error"]
