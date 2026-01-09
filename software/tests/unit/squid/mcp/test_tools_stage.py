"""Tests for MCP stage tools."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_app():
    """Create a mock ApplicationContext with stage service."""
    app = MagicMock()
    app.mode_gate.is_blocked_for_commands.return_value = False
    app.mode_gate.get_current_mode.return_value = "IDLE"

    stage_service = MagicMock()
    stage_service.position_x_mm = 10.0
    stage_service.position_y_mm = 20.0
    stage_service.position_z_mm = 5.0

    app.services.get.side_effect = lambda name: {
        "stage": stage_service,
    }.get(name)

    return app


@pytest.fixture
def mock_mcp():
    """Create a mock FastMCP server."""
    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    return mcp


class TestGetPosition:
    """Tests for get_position tool."""

    def test_returns_current_position(self, mock_app, mock_mcp):
        """Should return current stage position."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.stage as stage_module

                importlib.reload(stage_module)

                result = stage_module.get_position()

        assert result["x_mm"] == 10.0
        assert result["y_mm"] == 20.0
        assert result["z_mm"] == 5.0


class TestMoveTo:
    """Tests for move_to tool."""

    def test_moves_to_position(self, mock_app, mock_mcp):
        """Should move stage to specified position."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.stage as stage_module

                importlib.reload(stage_module)

                result = stage_module.move_to(x_mm=15.0, y_mm=25.0)

        stage = mock_app.services.get("stage")
        stage.move_to.assert_called_once()

    def test_blocked_when_acquiring(self, mock_app, mock_mcp):
        """Should raise error when system is acquiring."""
        mock_app.mode_gate.is_blocked_for_commands.return_value = True
        mock_app.mode_gate.get_current_mode.return_value = "ACQUIRING"

        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.stage as stage_module

                importlib.reload(stage_module)

                with pytest.raises(RuntimeError) as exc_info:
                    stage_module.move_to(x_mm=15.0)

        assert "Cannot move stage" in str(exc_info.value)


class TestMoveRelative:
    """Tests for move_relative tool."""

    def test_moves_relative(self, mock_app, mock_mcp):
        """Should move stage by relative amount."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.stage as stage_module

                importlib.reload(stage_module)

                result = stage_module.move_relative(dx_mm=1.0, dy_mm=2.0)

        stage = mock_app.services.get("stage")
        stage.move_relative.assert_called_once_with(1.0, 2.0)


class TestHome:
    """Tests for home tool."""

    def test_homes_all_axes(self, mock_app, mock_mcp):
        """Should home all axes by default."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.stage as stage_module

                importlib.reload(stage_module)

                result = stage_module.home()

        stage = mock_app.services.get("stage")
        stage.home.assert_called_once_with(x=True, y=True, z=True)
        assert result["status"] == "homed"

    def test_homes_selected_axes(self, mock_app, mock_mcp):
        """Should home only selected axes."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.stage as stage_module

                importlib.reload(stage_module)

                result = stage_module.home(x=True, y=False, z=False)

        stage = mock_app.services.get("stage")
        stage.home.assert_called_once_with(x=True, y=False, z=False)
