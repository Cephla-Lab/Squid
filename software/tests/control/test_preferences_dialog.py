import os
import sys
import tempfile
from configparser import ConfigParser
from unittest.mock import patch, MagicMock

import pytest
from qtpy.QtWidgets import QMessageBox

import control.widgets


@pytest.fixture
def sample_config():
    """Create a sample config with test values."""
    config = ConfigParser()
    config.add_section("GENERAL")
    config.set("GENERAL", "file_saving_option", "OME_TIFF")
    config.set("GENERAL", "default_saving_path", "/test/path")
    config.set("GENERAL", "multipoint_autofocus_channel", "BF LED matrix full")
    config.set("GENERAL", "enable_flexible_multipoint", "True")
    config.set("GENERAL", "max_velocity_x_mm", "30.0")
    config.set("GENERAL", "enable_tracking", "false")

    config.add_section("CAMERA_CONFIG")
    config.set("CAMERA_CONFIG", "binning_factor_default", "2")
    config.set("CAMERA_CONFIG", "flip_image", "None")
    config.set("CAMERA_CONFIG", "temperature_default", "20")
    config.set("CAMERA_CONFIG", "roi_width_default", "None")

    config.add_section("AF")
    config.set("AF", "stop_threshold", "0.85")
    config.set("AF", "crop_width", "800")

    config.add_section("SOFTWARE_POS_LIMIT")
    config.set("SOFTWARE_POS_LIMIT", "x_positive", "115.0")

    config.add_section("TRACKING")
    config.set("TRACKING", "default_tracker", "csrt")
    config.set("TRACKING", "search_area_ratio", "10")

    return config


@pytest.fixture
def temp_config_file(sample_config):
    """Create a temporary config file."""
    fd, filepath = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    with open(filepath, "w") as f:
        sample_config.write(f)
    yield filepath
    if os.path.exists(filepath):
        os.remove(filepath)


@pytest.fixture
def preferences_dialog(qtbot, sample_config, temp_config_file):
    """Create a PreferencesDialog instance for testing."""
    dialog = control.widgets.PreferencesDialog(sample_config, temp_config_file)
    qtbot.addWidget(dialog)
    return dialog


class TestConfigHelpers:
    """Test config value retrieval helper methods."""

    def test_get_config_value_existing(self, preferences_dialog):
        result = preferences_dialog._get_config_value("GENERAL", "file_saving_option", "default")
        assert result == "OME_TIFF"

    def test_get_config_value_missing_option(self, preferences_dialog):
        result = preferences_dialog._get_config_value("GENERAL", "nonexistent", "default_value")
        assert result == "default_value"

    def test_get_config_value_missing_section(self, preferences_dialog):
        result = preferences_dialog._get_config_value("NONEXISTENT", "option", "default_value")
        assert result == "default_value"

    def test_get_config_bool_true_lowercase(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "true")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_true_capitalized(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "True")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_one(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "1")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_yes(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "test_bool", "yes")
        result = preferences_dialog._get_config_bool("GENERAL", "test_bool", False)
        assert result is True

    def test_get_config_bool_false(self, preferences_dialog):
        result = preferences_dialog._get_config_bool("GENERAL", "enable_tracking", True)
        assert result is False

    def test_get_config_bool_missing(self, preferences_dialog):
        result = preferences_dialog._get_config_bool("GENERAL", "nonexistent", True)
        assert result is True

    def test_get_config_int_valid(self, preferences_dialog):
        result = preferences_dialog._get_config_int("CAMERA_CONFIG", "binning_factor_default", 1)
        assert result == 2

    def test_get_config_int_invalid(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "bad_int", "not_a_number")
        result = preferences_dialog._get_config_int("GENERAL", "bad_int", 99)
        assert result == 99

    def test_get_config_int_missing(self, preferences_dialog):
        result = preferences_dialog._get_config_int("GENERAL", "nonexistent", 42)
        assert result == 42

    def test_get_config_float_valid(self, preferences_dialog):
        result = preferences_dialog._get_config_float("AF", "stop_threshold", 0.5)
        assert result == 0.85

    def test_get_config_float_invalid(self, preferences_dialog):
        preferences_dialog.config.set("GENERAL", "bad_float", "not_a_float")
        result = preferences_dialog._get_config_float("GENERAL", "bad_float", 1.5)
        assert result == 1.5

    def test_get_config_float_missing(self, preferences_dialog):
        result = preferences_dialog._get_config_float("GENERAL", "nonexistent", 3.14)
        assert result == 3.14


class TestFloatsEqual:
    """Test floating-point comparison helper."""

    def test_equal_values(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.0) is True

    def test_nearly_equal_within_epsilon(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.00005) is True

    def test_different_values(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.001) is False

    def test_custom_epsilon(self, preferences_dialog):
        assert preferences_dialog._floats_equal(1.0, 1.001, epsilon=0.01) is True

    def test_negative_values(self, preferences_dialog):
        assert preferences_dialog._floats_equal(-1.0, -1.00005) is True

    def test_zero(self, preferences_dialog):
        assert preferences_dialog._floats_equal(0.0, 0.00005) is True


class TestEnsureSection:
    """Test section creation helper."""

    def test_ensure_existing_section(self, preferences_dialog):
        preferences_dialog._ensure_section("GENERAL")
        assert preferences_dialog.config.has_section("GENERAL")

    def test_ensure_new_section(self, preferences_dialog):
        assert not preferences_dialog.config.has_section("NEW_SECTION")
        preferences_dialog._ensure_section("NEW_SECTION")
        assert preferences_dialog.config.has_section("NEW_SECTION")


class TestChangeDetection:
    """Test change detection functionality."""

    def test_no_changes(self, preferences_dialog):
        changes = preferences_dialog._get_changes()
        assert len(changes) == 0

    def test_detect_string_change(self, preferences_dialog):
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "File Saving Format" for c in changes)

    def test_detect_bool_change(self, preferences_dialog):
        current = preferences_dialog.flexible_multipoint_checkbox.isChecked()
        preferences_dialog.flexible_multipoint_checkbox.setChecked(not current)
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "Enable Flexible Multipoint" for c in changes)

    def test_detect_int_change(self, preferences_dialog):
        preferences_dialog.binning_spinbox.setValue(4)
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "Default Binning Factor" for c in changes)

    def test_detect_float_change(self, preferences_dialog):
        preferences_dialog.max_vel_x.setValue(50.0)
        changes = preferences_dialog._get_changes()
        assert any(c[0] == "Max Velocity X" for c in changes)

    def test_change_includes_restart_flag(self, preferences_dialog):
        preferences_dialog.binning_spinbox.setValue(4)
        changes = preferences_dialog._get_changes()
        binning_change = next(c for c in changes if c[0] == "Default Binning Factor")
        assert binning_change[3] is True  # requires_restart

    def test_live_setting_no_restart(self, preferences_dialog):
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        changes = preferences_dialog._get_changes()
        file_change = next(c for c in changes if c[0] == "File Saving Format")
        assert file_change[3] is False  # does not require restart


class TestApplySettings:
    """Test settings application."""

    def test_apply_settings_saves_to_file(self, preferences_dialog, temp_config_file):
        preferences_dialog.file_saving_combo.setCurrentText("INDIVIDUAL_IMAGES")
        preferences_dialog._apply_settings()

        # Read back the file
        saved_config = ConfigParser()
        saved_config.read(temp_config_file)
        assert saved_config.get("GENERAL", "file_saving_option") == "INDIVIDUAL_IMAGES"

    def test_apply_settings_creates_missing_sections(self, qtbot, temp_config_file):
        # Create config without TRACKING section
        config = ConfigParser()
        config.add_section("GENERAL")
        config.set("GENERAL", "file_saving_option", "OME_TIFF")
        with open(temp_config_file, "w") as f:
            config.write(f)

        dialog = control.widgets.PreferencesDialog(config, temp_config_file)
        qtbot.addWidget(dialog)
        dialog._apply_settings()

        # Verify TRACKING section was created
        saved_config = ConfigParser()
        saved_config.read(temp_config_file)
        assert saved_config.has_section("TRACKING")

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod doesn't reliably prevent writes on Windows")
    def test_apply_settings_handles_read_only_file(self, preferences_dialog, temp_config_file):
        os.chmod(temp_config_file, 0o444)
        try:
            # Should not raise, but show error dialog
            preferences_dialog._apply_settings()
        finally:
            os.chmod(temp_config_file, 0o644)

    def test_apply_settings_emits_signal(self, qtbot, preferences_dialog):
        """Verify signal_config_changed is emitted when settings are saved."""
        with qtbot.waitSignal(preferences_dialog.signal_config_changed, timeout=1000):
            preferences_dialog._apply_settings()


class TestSaveAndCloseWorkflow:
    """Test the complete save workflow via _save_and_close method."""

    def test_save_and_close_no_changes(self, qtbot, preferences_dialog):
        """When no changes, should close silently without dialog."""
        preferences_dialog.accept = MagicMock()
        preferences_dialog._save_and_close()
        preferences_dialog.accept.assert_called_once()

    def test_save_and_close_single_change_no_restart(self, qtbot, preferences_dialog, temp_config_file):
        """Single change that doesn't require restart should save silently."""
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")

        # Mock accept to track if dialog closes
        preferences_dialog.accept = MagicMock()
        preferences_dialog._save_and_close()

        # Should save and close without showing restart message
        preferences_dialog.accept.assert_called_once()

        # Verify saved
        saved_config = ConfigParser()
        saved_config.read(temp_config_file)
        assert saved_config.get("GENERAL", "file_saving_option") == "MULTI_PAGE_TIFF"

    def test_save_and_close_single_change_requires_restart(self, qtbot, preferences_dialog, temp_config_file):
        """Single change requiring restart should show restart message."""
        preferences_dialog.binning_spinbox.setValue(4)

        with patch.object(QMessageBox, "information") as mock_info:
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

            # Should show restart message
            mock_info.assert_called_once()
            assert "restart" in str(mock_info.call_args)
            preferences_dialog.accept.assert_called_once()

    def test_save_and_close_multiple_changes_accepted(self, qtbot, preferences_dialog, temp_config_file):
        """Multiple changes should show confirmation dialog."""
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        preferences_dialog.binning_spinbox.setValue(4)

        # Mock the confirmation dialog to return Accepted
        with patch("qtpy.QtWidgets.QDialog.exec_", return_value=True):
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

            # Should save and close
            preferences_dialog.accept.assert_called_once()

            # Verify both changes saved
            saved_config = ConfigParser()
            saved_config.read(temp_config_file)
            assert saved_config.get("GENERAL", "file_saving_option") == "MULTI_PAGE_TIFF"
            assert saved_config.get("CAMERA_CONFIG", "binning_factor_default") == "4"

    def test_save_and_close_multiple_changes_cancelled(self, qtbot, preferences_dialog, temp_config_file):
        """When user cancels confirmation dialog, settings should not be saved."""
        preferences_dialog.file_saving_combo.setCurrentText("MULTI_PAGE_TIFF")
        preferences_dialog.binning_spinbox.setValue(4)

        # Mock the confirmation dialog to return Rejected (cancelled)
        with patch("qtpy.QtWidgets.QDialog.exec_", return_value=False):
            preferences_dialog.accept = MagicMock()
            preferences_dialog._save_and_close()

            # Should NOT save or close
            preferences_dialog.accept.assert_not_called()

            # Verify original values preserved in file
            saved_config = ConfigParser()
            saved_config.read(temp_config_file)
            assert saved_config.get("GENERAL", "file_saving_option") == "OME_TIFF"
            assert saved_config.get("CAMERA_CONFIG", "binning_factor_default") == "2"


class TestUIInitialization:
    """Test UI is properly initialized from config."""

    def test_file_saving_combo_initialized(self, preferences_dialog):
        assert preferences_dialog.file_saving_combo.currentText() == "OME_TIFF"

    def test_saving_path_initialized(self, preferences_dialog):
        assert preferences_dialog.saving_path_edit.text() == "/test/path"

    def test_binning_spinbox_initialized(self, preferences_dialog):
        assert preferences_dialog.binning_spinbox.value() == 2

    def test_temperature_spinbox_initialized(self, preferences_dialog):
        assert preferences_dialog.temperature_spinbox.value() == 20

    def test_max_velocity_initialized(self, preferences_dialog):
        assert preferences_dialog.max_vel_x.value() == 30.0

    def test_af_threshold_initialized(self, preferences_dialog):
        assert preferences_dialog.af_stop_threshold.value() == 0.85
