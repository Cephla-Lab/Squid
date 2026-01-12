"""
Tests for MCP view settings commands in microscope_control_server.py.

These commands allow runtime control of view settings for RAM debugging:
- get_view_settings: Query current state
- set_save_downsampled_images: Toggle downsampled well image saving
- set_display_plate_view: Toggle plate view generation
- set_display_mosaic_view: Toggle mosaic view updates (immediate effect)
- set_view_settings: Batch update multiple settings
"""

import pytest
from unittest.mock import MagicMock

import control._def
from control.microscope_control_server import MicroscopeControlServer


@pytest.fixture
def mock_microscope():
    """Create a minimal mock microscope for MCP server instantiation."""
    mock = MagicMock()
    mock.stage.get_pos.return_value = MagicMock(x_mm=0, y_mm=0, z_mm=0)
    return mock


@pytest.fixture
def mcp_server(mock_microscope):
    """Create an MCP server instance (without starting the TCP server)."""
    return MicroscopeControlServer(microscope=mock_microscope)


@pytest.fixture
def save_and_restore_def_values():
    """Save original control._def values and restore after test."""
    originals = {
        "SAVE_DOWNSAMPLED_WELL_IMAGES": control._def.SAVE_DOWNSAMPLED_WELL_IMAGES,
        "DISPLAY_PLATE_VIEW": control._def.DISPLAY_PLATE_VIEW,
        "USE_NAPARI_FOR_MOSAIC_DISPLAY": control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY,
        "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM": control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM,
        "DOWNSAMPLED_WELL_RESOLUTIONS_UM": control._def.DOWNSAMPLED_WELL_RESOLUTIONS_UM,
        "DOWNSAMPLED_PLATE_RESOLUTION_UM": control._def.DOWNSAMPLED_PLATE_RESOLUTION_UM,
    }
    yield
    # Restore original values
    control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = originals["SAVE_DOWNSAMPLED_WELL_IMAGES"]
    control._def.DISPLAY_PLATE_VIEW = originals["DISPLAY_PLATE_VIEW"]
    control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = originals["USE_NAPARI_FOR_MOSAIC_DISPLAY"]
    control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM = originals["MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM"]
    control._def.DOWNSAMPLED_WELL_RESOLUTIONS_UM = originals["DOWNSAMPLED_WELL_RESOLUTIONS_UM"]
    control._def.DOWNSAMPLED_PLATE_RESOLUTION_UM = originals["DOWNSAMPLED_PLATE_RESOLUTION_UM"]


class TestGetViewSettings:
    """Tests for get_view_settings command."""

    def test_returns_all_view_settings(self, mcp_server, save_and_restore_def_values):
        """Test that get_view_settings returns all expected fields."""
        result = mcp_server._cmd_get_view_settings()

        assert "save_downsampled_well_images" in result
        assert "display_plate_view" in result
        assert "display_mosaic_view" in result
        assert "mosaic_view_target_pixel_size_um" in result
        assert "downsampled_well_resolutions_um" in result
        assert "downsampled_plate_resolution_um" in result
        assert "performance_mode" in result

    def test_returns_current_def_values(self, mcp_server, save_and_restore_def_values):
        """Test that get_view_settings reflects current control._def values."""
        # Set known values
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = True
        control._def.DISPLAY_PLATE_VIEW = False
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

        result = mcp_server._cmd_get_view_settings()

        assert result["save_downsampled_well_images"] is True
        assert result["display_plate_view"] is False
        assert result["display_mosaic_view"] is True

    def test_performance_mode_none_when_no_gui(self, mcp_server, save_and_restore_def_values):
        """Test that performance_mode is None when GUI is not available."""
        result = mcp_server._cmd_get_view_settings()
        assert result["performance_mode"] is None


class TestSetSaveDownsampledImages:
    """Tests for set_save_downsampled_images command."""

    def test_enable_saving(self, mcp_server, save_and_restore_def_values):
        """Test enabling downsampled image saving."""
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = False

        result = mcp_server._cmd_set_save_downsampled_images(enabled=True)

        assert control._def.SAVE_DOWNSAMPLED_WELL_IMAGES is True
        assert result["save_downsampled_well_images"] is True
        assert "enabled" in result["message"]

    def test_disable_saving(self, mcp_server, save_and_restore_def_values):
        """Test disabling downsampled image saving."""
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = True

        result = mcp_server._cmd_set_save_downsampled_images(enabled=False)

        assert control._def.SAVE_DOWNSAMPLED_WELL_IMAGES is False
        assert result["save_downsampled_well_images"] is False
        assert "disabled" in result["message"]

    def test_message_indicates_next_acquisition(self, mcp_server, save_and_restore_def_values):
        """Test that message indicates setting takes effect on next acquisition."""
        result = mcp_server._cmd_set_save_downsampled_images(enabled=True)
        assert "next acquisition" in result["message"]

    def test_rejects_non_boolean(self, mcp_server, save_and_restore_def_values):
        """Test that non-boolean values raise TypeError."""
        with pytest.raises(TypeError, match="enabled must be a boolean"):
            mcp_server._cmd_set_save_downsampled_images(enabled="true")
        with pytest.raises(TypeError, match="enabled must be a boolean"):
            mcp_server._cmd_set_save_downsampled_images(enabled=1)


class TestSetDisplayPlateView:
    """Tests for set_display_plate_view command."""

    def test_enable_plate_view(self, mcp_server, save_and_restore_def_values):
        """Test enabling plate view display."""
        control._def.DISPLAY_PLATE_VIEW = False

        result = mcp_server._cmd_set_display_plate_view(enabled=True)

        assert control._def.DISPLAY_PLATE_VIEW is True
        assert result["display_plate_view"] is True
        assert "enabled" in result["message"]

    def test_disable_plate_view(self, mcp_server, save_and_restore_def_values):
        """Test disabling plate view display."""
        control._def.DISPLAY_PLATE_VIEW = True

        result = mcp_server._cmd_set_display_plate_view(enabled=False)

        assert control._def.DISPLAY_PLATE_VIEW is False
        assert result["display_plate_view"] is False
        assert "disabled" in result["message"]

    def test_message_indicates_next_acquisition(self, mcp_server, save_and_restore_def_values):
        """Test that message indicates setting takes effect on next acquisition."""
        result = mcp_server._cmd_set_display_plate_view(enabled=True)
        assert "next acquisition" in result["message"]

    def test_rejects_non_boolean(self, mcp_server, save_and_restore_def_values):
        """Test that non-boolean values raise TypeError."""
        with pytest.raises(TypeError, match="enabled must be a boolean"):
            mcp_server._cmd_set_display_plate_view(enabled="false")
        with pytest.raises(TypeError, match="enabled must be a boolean"):
            mcp_server._cmd_set_display_plate_view(enabled=0)


class TestSetDisplayMosaicView:
    """Tests for set_display_mosaic_view command."""

    def test_enable_mosaic_view(self, mcp_server, save_and_restore_def_values):
        """Test enabling mosaic view display."""
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = False

        result = mcp_server._cmd_set_display_mosaic_view(enabled=True)

        assert control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY is True
        assert result["display_mosaic_view"] is True
        assert "enabled" in result["message"]

    def test_disable_mosaic_view(self, mcp_server, save_and_restore_def_values):
        """Test disabling mosaic view display."""
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

        result = mcp_server._cmd_set_display_mosaic_view(enabled=False)

        assert control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY is False
        assert result["display_mosaic_view"] is False
        assert "disabled" in result["message"]

    def test_message_indicates_immediate_effect(self, mcp_server, save_and_restore_def_values):
        """Test that message indicates setting takes effect immediately (not on next acquisition)."""
        result = mcp_server._cmd_set_display_mosaic_view(enabled=True)
        assert "immediately" in result["message"]
        assert "next acquisition" not in result["message"]

    def test_rejects_non_boolean(self, mcp_server, save_and_restore_def_values):
        """Test that non-boolean values raise TypeError."""
        with pytest.raises(TypeError, match="enabled must be a boolean"):
            mcp_server._cmd_set_display_mosaic_view(enabled="true")
        with pytest.raises(TypeError, match="enabled must be a boolean"):
            mcp_server._cmd_set_display_mosaic_view(enabled=None)


class TestSetViewSettings:
    """Tests for set_view_settings batch command."""

    def test_set_all_settings(self, mcp_server, save_and_restore_def_values):
        """Test setting all view settings at once."""
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = True
        control._def.DISPLAY_PLATE_VIEW = True
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

        result = mcp_server._cmd_set_view_settings(
            save_downsampled_well_images=False,
            display_plate_view=False,
            display_mosaic_view=False,
        )

        assert control._def.SAVE_DOWNSAMPLED_WELL_IMAGES is False
        assert control._def.DISPLAY_PLATE_VIEW is False
        assert control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY is False
        assert result["save_downsampled_well_images"] is False
        assert result["display_plate_view"] is False
        assert result["display_mosaic_view"] is False

    def test_set_single_setting_leaves_others_unchanged(self, mcp_server, save_and_restore_def_values):
        """Test that setting one value doesn't affect others."""
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = True
        control._def.DISPLAY_PLATE_VIEW = True
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

        result = mcp_server._cmd_set_view_settings(display_plate_view=False)

        # Only display_plate_view should change
        assert control._def.SAVE_DOWNSAMPLED_WELL_IMAGES is True
        assert control._def.DISPLAY_PLATE_VIEW is False
        assert control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY is True
        # Verify return value reflects the change
        assert result["display_plate_view"] is False
        assert len(result["changes"]) == 1

    def test_none_values_are_ignored(self, mcp_server, save_and_restore_def_values):
        """Test that None values don't modify settings."""
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = True
        control._def.DISPLAY_PLATE_VIEW = True

        result = mcp_server._cmd_set_view_settings(
            save_downsampled_well_images=None,
            display_plate_view=None,
        )

        # Nothing should change
        assert control._def.SAVE_DOWNSAMPLED_WELL_IMAGES is True
        assert control._def.DISPLAY_PLATE_VIEW is True
        # Verify return value shows no changes
        assert result["changes"] == []

    def test_changes_list_reflects_modifications(self, mcp_server, save_and_restore_def_values):
        """Test that changes list correctly reports what was modified."""
        result = mcp_server._cmd_set_view_settings(
            save_downsampled_well_images=True,
            display_mosaic_view=False,
        )

        assert len(result["changes"]) == 2
        assert any("save_downsampled_well_images" in change for change in result["changes"])
        assert any("display_mosaic_view" in change for change in result["changes"])

    def test_no_args_returns_empty_changes(self, mcp_server, save_and_restore_def_values):
        """Test calling with no arguments returns current state with empty changes."""
        result = mcp_server._cmd_set_view_settings()

        assert result["changes"] == []
        assert "save_downsampled_well_images" in result
        assert "display_plate_view" in result
        assert "display_mosaic_view" in result


class TestCommandDiscovery:
    """Tests that view settings commands are properly registered."""

    def test_view_commands_are_discovered(self, mcp_server):
        """Test that all view settings commands are auto-discovered."""
        assert "get_view_settings" in mcp_server._commands
        assert "set_save_downsampled_images" in mcp_server._commands
        assert "set_display_plate_view" in mcp_server._commands
        assert "set_display_mosaic_view" in mcp_server._commands
        assert "set_view_settings" in mcp_server._commands

    def test_commands_have_schema(self, mcp_server):
        """Test that view settings commands have schema metadata."""
        for cmd_name in [
            "get_view_settings",
            "set_save_downsampled_images",
            "set_display_plate_view",
            "set_display_mosaic_view",
            "set_view_settings",
        ]:
            cmd_func = mcp_server._commands[cmd_name]
            assert hasattr(cmd_func, "_schema"), f"{cmd_name} should have _schema attribute"
