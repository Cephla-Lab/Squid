"""Tests for the feature flags registry."""

from unittest.mock import MagicMock, patch
import sys

import pytest

from squid.core.config.feature_flags import (
    FeatureFlags,
    get_feature_flags,
    reset_feature_flags,
)


@pytest.fixture(autouse=True)
def reset_flags():
    """Reset feature flags singleton before each test."""
    reset_feature_flags()
    yield
    reset_feature_flags()


class TestFeatureFlags:
    """Tests for the FeatureFlags class."""

    def test_is_enabled_returns_correct_value(self):
        """is_enabled returns the correct flag value."""
        flags = FeatureFlags()

        # Test a known registered flag with its default
        # Note: The actual value depends on _def.py, so we test the mechanism
        result = flags.is_enabled("SUPPORT_LASER_AUTOFOCUS")
        assert isinstance(result, bool)

    def test_is_enabled_unknown_flag_logs_warning(self):
        """is_enabled logs a warning for unknown flags."""
        flags = FeatureFlags()

        with patch.object(flags, "_log", create=True) as mock_log:
            # We need to patch at module level since _log is module-level
            with patch("squid.core.config.feature_flags._log") as mock_module_log:
                result = flags.is_enabled("TOTALLY_UNKNOWN_FLAG_XYZ")
                mock_module_log.warning.assert_called_once()
                assert "TOTALLY_UNKNOWN_FLAG_XYZ" in str(mock_module_log.warning.call_args)

        assert result is False

    def test_is_enabled_unknown_flag_returns_false(self):
        """Unknown flag returns False by default."""
        flags = FeatureFlags()
        result = flags.is_enabled("NONEXISTENT_FLAG")
        assert result is False

    def test_get_with_default(self):
        """get() returns default for undefined flags."""
        flags = FeatureFlags()

        # Definitely undefined flag
        result = flags.get("UNDEFINED_FLAG_ABC", default=True)
        assert result is True

        result = flags.get("UNDEFINED_FLAG_ABC", default=False)
        assert result is False

    def test_get_returns_actual_value_when_defined(self):
        """get() returns actual value for defined flags."""
        flags = FeatureFlags()
        flags._values["TEST_FLAG"] = True

        result = flags.get("TEST_FLAG", default=False)
        assert result is True

    def test_refreshes_values_from_def(self):
        """is_enabled reflects runtime changes in _def values."""
        import types

        mock_def = types.SimpleNamespace()
        mock_def.SUPPORT_LASER_AUTOFOCUS = True

        with patch.dict(sys.modules, {"_def": mock_def}):
            flags = FeatureFlags()
            assert flags.is_enabled("SUPPORT_LASER_AUTOFOCUS") is True

            mock_def.SUPPORT_LASER_AUTOFOCUS = False
            assert flags.is_enabled("SUPPORT_LASER_AUTOFOCUS") is False

    def test_get_category_for_registered_flag(self):
        """get_category returns category for registered flags."""
        flags = FeatureFlags()

        category = flags.get_category("SUPPORT_LASER_AUTOFOCUS")
        assert category == FeatureFlags.HARDWARE

        category = flags.get_category("USE_NAPARI_FOR_LIVE_VIEW")
        assert category == FeatureFlags.UI

    def test_get_category_returns_none_for_unregistered(self):
        """get_category returns None for unregistered flags."""
        flags = FeatureFlags()

        category = flags.get_category("UNREGISTERED_FLAG")
        assert category is None

    def test_list_flags_returns_all_flags(self):
        """list_flags() returns all loaded flags."""
        flags = FeatureFlags()

        all_flags = flags.list_flags()

        assert isinstance(all_flags, dict)
        # Should have at least the registered flags
        assert "SUPPORT_LASER_AUTOFOCUS" in all_flags
        assert "ENABLE_TRACKING" in all_flags

    def test_list_flags_filters_by_category(self):
        """list_flags(category) filters to that category."""
        flags = FeatureFlags()

        hardware_flags = flags.list_flags(category=FeatureFlags.HARDWARE)

        assert "SUPPORT_LASER_AUTOFOCUS" in hardware_flags
        assert "ENABLE_SPINNING_DISK_CONFOCAL" in hardware_flags
        # UI flags should not be included
        assert "USE_NAPARI_FOR_LIVE_VIEW" not in hardware_flags

    def test_accessed_flags_tracking(self):
        """Accessed flags are tracked."""
        flags = FeatureFlags()

        # Initially empty
        assert len(flags.get_accessed_flags()) == 0

        # Access some flags
        flags.is_enabled("SUPPORT_LASER_AUTOFOCUS")
        flags.get("ENABLE_TRACKING")

        accessed = flags.get_accessed_flags()
        assert "SUPPORT_LASER_AUTOFOCUS" in accessed
        assert "ENABLE_TRACKING" in accessed
        assert len(accessed) == 2

    def test_singleton_returns_same_instance(self):
        """get_feature_flags returns the same instance."""
        flags1 = get_feature_flags()
        flags2 = get_feature_flags()

        assert flags1 is flags2

    def test_reset_clears_singleton(self):
        """reset_feature_flags creates a new instance."""
        flags1 = get_feature_flags()
        reset_feature_flags()
        flags2 = get_feature_flags()

        assert flags1 is not flags2

    def test_loads_from_def(self):
        """Flags are loaded from _def.py module."""
        # Create a simple namespace to act as _def module
        import types

        mock_def = types.SimpleNamespace()
        mock_def.SUPPORT_LASER_AUTOFOCUS = True
        mock_def.ENABLE_TRACKING = False
        mock_def.SOME_OTHER_BOOL = True

        with patch.dict(sys.modules, {"_def": mock_def}):
            reset_feature_flags()
            flags = FeatureFlags()

            # Should have loaded values from mock _def
            assert flags.is_enabled("SUPPORT_LASER_AUTOFOCUS") is True
            assert flags.is_enabled("ENABLE_TRACKING") is False


class TestFeatureFlagsCategories:
    """Tests for feature flag categories."""

    def test_hardware_category_exists(self):
        """HARDWARE category constant is defined."""
        assert FeatureFlags.HARDWARE == "hardware"

    def test_ui_category_exists(self):
        """UI category constant is defined."""
        assert FeatureFlags.UI == "ui"

    def test_acquisition_category_exists(self):
        """ACQUISITION category constant is defined."""
        assert FeatureFlags.ACQUISITION == "acquisition"

    def test_debug_category_exists(self):
        """DEBUG category constant is defined."""
        assert FeatureFlags.DEBUG == "debug"

    def test_encoder_category_exists(self):
        """ENCODER category constant is defined."""
        assert FeatureFlags.ENCODER == "encoder"


class TestFeatureFlagsIntegration:
    """Integration tests with actual _def.py loading."""

    def test_can_load_actual_def_module(self):
        """Can load and access flags from actual _def.py."""
        flags = get_feature_flags()

        # These flags should exist in the real _def.py
        # Just check they return booleans without error
        assert isinstance(flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"), bool)
        assert isinstance(flags.is_enabled("ENABLE_TRACKING"), bool)
        assert isinstance(flags.is_enabled("ENABLE_FLEXIBLE_MULTIPOINT"), bool)

    def test_all_loaded_values_are_booleans(self):
        """All loaded flag values are booleans."""
        flags = get_feature_flags()

        for name, value in flags.list_flags().items():
            assert isinstance(value, bool), f"Flag {name} has non-boolean value: {value}"
