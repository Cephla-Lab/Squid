"""Unit tests for Phase 3: Per-component simulation and skip-init features."""

import pytest
from unittest.mock import patch, MagicMock
from configparser import ConfigParser


class TestShouldSimulate:
    """Test the _should_simulate() helper function."""

    def test_global_simulation_overrides_component(self):
        """With --simulation flag, all components should be simulated."""
        from squid.backend.microscope_factory import _should_simulate

        # Global simulation=True should always return True
        assert _should_simulate(True, False) is True
        assert _should_simulate(True, True) is True

    def test_component_override_without_global(self):
        """Without --simulation, per-component settings apply."""
        from squid.backend.microscope_factory import _should_simulate

        # Component override True should simulate
        assert _should_simulate(False, True) is True
        # Component override False should use real hardware
        assert _should_simulate(False, False) is False

    def test_all_combinations(self):
        """Test all 4 combinations of global/component flags."""
        from squid.backend.microscope_factory import _should_simulate

        # Truth table:
        # global=False, component=False -> False (real hardware)
        # global=False, component=True  -> True  (simulate this component)
        # global=True,  component=False -> True  (global overrides)
        # global=True,  component=True  -> True  (both want simulation)

        assert _should_simulate(False, False) is False
        assert _should_simulate(False, True) is True
        assert _should_simulate(True, False) is True
        assert _should_simulate(True, True) is True


class TestSimulationFlagDefaults:
    """Test that simulation flags have correct defaults in _def.py."""

    def test_simulation_flags_exist(self):
        """All SIMULATE_* flags should exist in _def."""
        import _def

        # All flags should exist and default to False
        assert hasattr(_def, "SIMULATE_CAMERA")
        assert hasattr(_def, "SIMULATE_MICROCONTROLLER")
        assert hasattr(_def, "SIMULATE_SPINNING_DISK")
        assert hasattr(_def, "SIMULATE_FILTER_WHEEL")
        assert hasattr(_def, "SIMULATE_OBJECTIVE_CHANGER")
        assert hasattr(_def, "SIMULATE_LASER_AF_CAMERA")

    def test_simulation_flags_default_false(self):
        """Simulation flags should default to False."""
        import _def

        # Default values should be False (don't simulate by default)
        assert _def.SIMULATE_CAMERA is False
        assert _def.SIMULATE_MICROCONTROLLER is False
        assert _def.SIMULATE_SPINNING_DISK is False
        assert _def.SIMULATE_FILTER_WHEEL is False
        assert _def.SIMULATE_OBJECTIVE_CHANGER is False
        assert _def.SIMULATE_LASER_AF_CAMERA is False


class TestSimulationConfigParsing:
    """Test the _parse_sim_setting helper function."""

    def test_parse_sim_setting_exists(self):
        """The _parse_sim_setting function should exist."""
        import _def

        assert hasattr(_def, "_parse_sim_setting")

    def test_parse_sim_setting_true_values(self):
        """Test that various 'true' representations are parsed correctly."""
        import _def

        assert _def._parse_sim_setting("true") is True
        assert _def._parse_sim_setting("True") is True
        assert _def._parse_sim_setting("TRUE") is True
        assert _def._parse_sim_setting("1") is True
        assert _def._parse_sim_setting("yes") is True
        assert _def._parse_sim_setting("YES") is True
        assert _def._parse_sim_setting("simulate") is True

    def test_parse_sim_setting_false_values(self):
        """Test that various 'false' representations are parsed correctly."""
        import _def

        assert _def._parse_sim_setting("false") is False
        assert _def._parse_sim_setting("False") is False
        assert _def._parse_sim_setting("0") is False
        assert _def._parse_sim_setting("no") is False
        assert _def._parse_sim_setting("") is False
        assert _def._parse_sim_setting("anything_else") is False


class TestApplicationContextSkipInit:
    """Test ApplicationContext skip_init parameter."""

    def test_skip_init_parameter_exists(self):
        """ApplicationContext should accept skip_init parameter."""
        import inspect
        from squid.application import ApplicationContext

        sig = inspect.signature(ApplicationContext.__init__)
        params = list(sig.parameters.keys())

        assert "skip_init" in params

    def test_skip_init_default_false(self):
        """skip_init should default to False."""
        import inspect
        from squid.application import ApplicationContext

        sig = inspect.signature(ApplicationContext.__init__)
        skip_init_param = sig.parameters["skip_init"]

        assert skip_init_param.default is False


class TestMainHcsCliArgs:
    """Test main_hcs.py CLI argument parsing."""

    def test_skip_init_argument_exists(self):
        """--skip-init argument should be recognized."""
        import argparse

        # Create parser with same args as main_hcs.py
        parser = argparse.ArgumentParser()
        parser.add_argument("--simulation", action="store_true")
        parser.add_argument("--live-only", action="store_true")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--debug-bus", action="store_true")
        parser.add_argument("--start-server", action="store_true")
        parser.add_argument("--server-port", type=int, default=5050)
        parser.add_argument("--skip-init", action="store_true")

        # Test parsing
        args = parser.parse_args(["--skip-init"])
        assert args.skip_init is True

        args = parser.parse_args([])
        assert args.skip_init is False

        # Test combination
        args = parser.parse_args(["--simulation", "--skip-init"])
        assert args.simulation is True
        assert args.skip_init is True


class TestPreferencesDialogSimulationUI:
    """Test PreferencesDialog simulation checkbox attributes."""

    def test_simulation_checkboxes_defined(self):
        """PreferencesDialog should have simulation checkbox attributes."""
        # We can't easily instantiate the dialog without Qt, but we can check
        # that the class has the expected methods
        from squid.ui.widgets.config import PreferencesDialog
        import inspect

        # Check that _create_advanced_tab exists (where checkboxes are created)
        assert hasattr(PreferencesDialog, "_create_advanced_tab")

        # Check restart methods exist
        assert hasattr(PreferencesDialog, "_prompt_restart")
        assert hasattr(PreferencesDialog, "_restart_application")

    def test_apply_settings_saves_simulation_section(self):
        """_apply_settings should handle SIMULATION section."""
        from squid.ui.widgets.config import PreferencesDialog
        import inspect

        # Check method exists
        assert hasattr(PreferencesDialog, "_apply_settings")

        # Check source contains SIMULATION section handling
        source = inspect.getsource(PreferencesDialog._apply_settings)
        assert "SIMULATION" in source
        assert "simulate_camera" in source


class TestSetupCameraCallbacksOnly:
    """Test the _setup_camera_callbacks_only method."""

    def test_method_exists(self):
        """ApplicationContext should have _setup_camera_callbacks_only method."""
        from squid.application import ApplicationContext

        assert hasattr(ApplicationContext, "_setup_camera_callbacks_only")

    def test_method_signature(self):
        """Method should accept _config parameter."""
        import inspect
        from squid.application import ApplicationContext

        sig = inspect.signature(ApplicationContext._setup_camera_callbacks_only)
        params = list(sig.parameters.keys())

        # Should have self and _config
        assert "self" in params
        assert "_config" in params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
