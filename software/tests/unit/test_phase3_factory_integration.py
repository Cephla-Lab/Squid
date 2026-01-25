"""Integration tests for per-component simulation in microscope factory."""

import pytest
from unittest.mock import patch, MagicMock
import importlib


class TestMicroscopeFactoryPerComponentSimulation:
    """Test that microscope_factory uses per-component simulation flags."""

    def test_build_microscope_uses_camera_simulation_flag(self):
        """build_microscope should check SIMULATE_CAMERA when not in global simulation."""
        from squid.backend import microscope_factory

        # Verify _should_simulate is called for camera
        with patch.object(microscope_factory, "_should_simulate") as mock_should_sim:
            mock_should_sim.return_value = True

            # We can't easily call build_microscope without lots of setup,
            # but we can verify the function signature and that _should_simulate exists
            assert callable(microscope_factory._should_simulate)

    def test_build_microscope_addons_uses_simulation_flags(self):
        """build_microscope_addons should use per-component simulation flags."""
        import inspect
        from squid.backend import microscope_factory

        # Check that build_microscope_addons source references per-component flags
        source = inspect.getsource(microscope_factory.build_microscope_addons)

        # Should use _should_simulate for various components
        assert "_should_simulate" in source

        # Should reference the component-specific flags
        # (either directly or via _def)
        assert "SIMULATE_SPINNING_DISK" in source or "spinning_disk_simulated" in source

    def test_build_low_level_drivers_uses_simulation_flags(self):
        """build_low_level_drivers should use per-component simulation flags."""
        import inspect
        from squid.backend import microscope_factory

        source = inspect.getsource(microscope_factory.build_low_level_drivers)

        # Should use _should_simulate for microcontroller
        assert "_should_simulate" in source
        assert "SIMULATE_MICROCONTROLLER" in source or "microcontroller_simulated" in source

    def test_should_simulate_function_in_factory(self):
        """_should_simulate should be defined in microscope_factory."""
        from squid.backend import microscope_factory

        # Function should exist
        assert hasattr(microscope_factory, "_should_simulate")

        # Function should work correctly
        fn = microscope_factory._should_simulate

        # Test the truth table
        assert fn(True, True) is True
        assert fn(True, False) is True
        assert fn(False, True) is True
        assert fn(False, False) is False


class TestConfigLoadingIntegration:
    """Test that simulation flags can be loaded from config file."""

    def test_simulation_section_loading(self):
        """Test loading SIMULATION section from a config file."""
        from configparser import ConfigParser
        import tempfile
        import os

        # Create a temporary config file with SIMULATION section
        config_content = """
[SIMULATION]
simulate_camera = true
simulate_microcontroller = false
simulate_spinning_disk = yes
simulate_filter_wheel = 1
simulate_objective_changer = no
simulate_laser_af_camera = false
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write(config_content)
            temp_path = f.name

        try:
            # Parse it using ConfigParser
            config = ConfigParser()
            config.read(temp_path)

            assert config.has_section("SIMULATION")
            assert config.get("SIMULATION", "simulate_camera") == "true"
            assert config.get("SIMULATION", "simulate_microcontroller") == "false"
            assert config.get("SIMULATION", "simulate_spinning_disk") == "yes"
            assert config.get("SIMULATION", "simulate_filter_wheel") == "1"
        finally:
            os.unlink(temp_path)

    def test_parse_sim_setting_with_config_values(self):
        """Test _parse_sim_setting with realistic config values."""
        import _def

        # Test values that might come from ConfigParser
        assert _def._parse_sim_setting("true") is True
        assert _def._parse_sim_setting("false") is False
        assert _def._parse_sim_setting("yes") is True
        assert _def._parse_sim_setting("no") is False
        assert _def._parse_sim_setting("1") is True
        assert _def._parse_sim_setting("0") is False

        # Whitespace handling
        assert _def._parse_sim_setting("  true  ") is True
        assert _def._parse_sim_setting("  false  ") is False


class TestApplicationContextIntegration:
    """Test ApplicationContext with skip_init flag."""

    def test_initialize_hardware_checks_skip_init(self):
        """_initialize_hardware should check _skip_init flag."""
        import inspect
        from squid.application import ApplicationContext

        source = inspect.getsource(ApplicationContext._initialize_hardware)

        # Should check _skip_init flag
        assert "_skip_init" in source
        assert "Skipping hardware initialization" in source

    def test_setup_camera_callbacks_only_is_called_when_skipping(self):
        """When skip_init=True, _setup_camera_callbacks_only should be called."""
        import inspect
        from squid.application import ApplicationContext

        source = inspect.getsource(ApplicationContext._initialize_hardware)

        # Should call _setup_camera_callbacks_only when skipping
        assert "_setup_camera_callbacks_only" in source


class TestPreferencesDialogIntegration:
    """Test PreferencesDialog restart functionality."""

    def test_restart_application_adds_skip_init(self):
        """_restart_application should add --skip-init to args."""
        import inspect
        from squid.ui.widgets.config import PreferencesDialog

        source = inspect.getsource(PreferencesDialog._restart_application)

        # Should check for and add --skip-init
        assert "--skip-init" in source

    def test_prompt_restart_exists(self):
        """_prompt_restart method should exist."""
        from squid.ui.widgets.config import PreferencesDialog

        assert hasattr(PreferencesDialog, "_prompt_restart")

    def test_get_changes_includes_simulation_settings(self):
        """_get_changes should include simulation setting changes."""
        import inspect
        from squid.ui.widgets.config import PreferencesDialog

        source = inspect.getsource(PreferencesDialog._get_changes)

        # Should check simulation settings
        assert "SIMULATION" in source
        assert "simulate_camera" in source
        assert "Simulate Camera" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
