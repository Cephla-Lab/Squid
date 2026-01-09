"""Tests for MCP camera tools."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_app():
    """Create a mock ApplicationContext with camera service."""
    app = MagicMock()
    app.mode_gate.is_blocked_for_commands.return_value = False

    camera_service = MagicMock()
    camera_service.exposure_time_ms = 100.0
    camera_service.analog_gain = 2.0
    camera_service.binning = (1, 1)
    camera_service.roi = (0, 0, 2048, 2048)

    app.services.get.side_effect = lambda name: {
        "camera": camera_service,
    }.get(name)

    return app


@pytest.fixture
def mock_mcp():
    """Create a mock FastMCP server."""
    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    return mcp


class TestSetExposure:
    """Tests for set_exposure tool."""

    def test_sets_exposure(self, mock_app, mock_mcp):
        """Should set exposure time."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.camera as camera_module

                importlib.reload(camera_module)

                result = camera_module.set_exposure(50.0)

        camera = mock_app.services.get("camera")
        camera.set_exposure_time.assert_called_once_with(50.0)

    def test_rejects_out_of_range(self, mock_app, mock_mcp):
        """Should reject exposure outside valid range."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.camera as camera_module

                importlib.reload(camera_module)

                result = camera_module.set_exposure(0.01)  # Too low

        assert "error" in result

    def test_rejects_too_high(self, mock_app, mock_mcp):
        """Should reject exposure that's too high."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.camera as camera_module

                importlib.reload(camera_module)

                result = camera_module.set_exposure(20000)  # Too high

        assert "error" in result


class TestSetGain:
    """Tests for set_gain tool."""

    def test_sets_gain(self, mock_app, mock_mcp):
        """Should set analog gain."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.camera as camera_module

                importlib.reload(camera_module)

                result = camera_module.set_gain(4.0)

        camera = mock_app.services.get("camera")
        camera.set_analog_gain.assert_called_once_with(4.0)


class TestGetCameraSettings:
    """Tests for get_camera_settings tool."""

    def test_returns_settings(self, mock_app, mock_mcp):
        """Should return current camera settings."""
        with patch("squid.mcp.server._app", mock_app):
            with patch("squid.mcp.server.mcp", mock_mcp):
                import importlib
                import squid.mcp.tools.camera as camera_module

                importlib.reload(camera_module)

                result = camera_module.get_camera_settings()

        assert result["exposure_ms"] == 100.0
        assert result["gain"] == 2.0
        assert result["binning"] == (1, 1)
