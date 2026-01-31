from unittest.mock import patch, MagicMock

import control.microscope
import control._def
import squid.stage.cephla
import squid.config
from control.microcontroller import Microcontroller, SimSerial
from control.microscope import _should_simulate
from squid.camera.utils import SimulatedCamera
from tests.control.test_microcontroller import get_test_micro


class TestShouldSimulate:
    """Tests for _should_simulate() per-component simulation logic."""

    def test_simulate_without_global_flag(self):
        """Simulate (True) without --simulation flag should simulate."""
        assert _should_simulate(global_simulated=False, component_override=True) is True

    def test_simulate_with_global_flag(self):
        """Simulate (True) with --simulation flag should simulate."""
        assert _should_simulate(global_simulated=True, component_override=True) is True

    def test_real_hardware_without_global_flag(self):
        """Real Hardware (False) without --simulation should use real hardware."""
        assert _should_simulate(global_simulated=False, component_override=False) is False

    def test_real_hardware_with_global_flag(self):
        """Real Hardware (False) with --simulation flag should still simulate (--simulation overrides all)."""
        assert _should_simulate(global_simulated=True, component_override=False) is True


def test_create_simulated_microscope():
    sim_scope = control.microscope.Microscope.build_from_global_config(True)
    sim_scope.close()


def test_create_simulated_microscope_with_skip_init():
    """Test that skip_init flag is accepted and doesn't cause errors."""
    sim_scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)
    sim_scope.close()


def test_skip_init_skips_addon_homing():
    """Test that skip_init=True actually skips homing operations in addons."""
    with patch.object(control.microscope.MicroscopeAddons, "prepare_for_use") as mock_prepare:
        sim_scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)

        # Verify prepare_for_use was called with skip_init=True
        mock_prepare.assert_called_once()
        call_kwargs = mock_prepare.call_args.kwargs
        assert call_kwargs.get("skip_init") is True, "prepare_for_use should be called with skip_init=True"

        sim_scope.close()


@patch("squid.config.get_filter_wheel_config")
def test_prepare_for_use_skips_homing_when_flag_set(mock_get_fw_config):
    """Test that MicroscopeAddons.prepare_for_use skips home() calls when skip_init=True."""
    # Mock filter wheel config
    mock_fw_config = MagicMock()
    mock_fw_config.indices = [1]
    mock_get_fw_config.return_value = mock_fw_config

    mock_filter_wheel = MagicMock()
    mock_piezo_stage = MagicMock()

    addons = control.microscope.MicroscopeAddons(
        emission_filter_wheel=mock_filter_wheel,
        piezo_stage=mock_piezo_stage,
    )

    # With skip_init=True, home() should NOT be called
    addons.prepare_for_use(skip_init=True)
    mock_filter_wheel.home.assert_not_called()
    mock_piezo_stage.home.assert_not_called()

    # With skip_init=False (default), home() SHOULD be called
    mock_filter_wheel.reset_mock()
    mock_piezo_stage.reset_mock()
    addons.prepare_for_use(skip_init=False)
    mock_filter_wheel.home.assert_called_once()
    mock_piezo_stage.home.assert_called_once()


def test_simulated_scope_basic_ops():
    scope = control.microscope.Microscope.build_from_global_config(True)

    scope.stage.home(x=True, y=True, z=True, theta=False, blocking=True)
    scope.stage.move_x_to(scope.stage.get_config().X_AXIS.MAX_POSITION / 2)
    scope.stage.move_y_to(scope.stage.get_config().Y_AXIS.MAX_POSITION / 2)
    scope.stage.move_z_to(scope.stage.get_config().Z_AXIS.MAX_POSITION / 2)

    scope.camera.start_streaming()
    scope.illumination_controller.turn_on_illumination()
    scope.camera.send_trigger()
    scope.camera.read_frame()
    scope.illumination_controller.turn_off_illumination()
    scope.camera.stop_streaming()


class TestPerComponentSimulationIntegration:
    """Integration tests verifying per-component settings affect microscope build."""

    def test_simulate_camera_without_global_flag(self, monkeypatch):
        """SIMULATE_CAMERA=True without --simulation should create simulated camera."""
        # Set per-component camera simulation (all components must be simulated for build to work
        # since we're testing without --simulation flag and real hardware isn't available in CI)
        monkeypatch.setattr(control._def, "SIMULATE_CAMERA", True)
        monkeypatch.setattr(control._def, "SIMULATE_MICROCONTROLLER", True)
        monkeypatch.setattr(control._def, "SIMULATE_SPINNING_DISK", True)
        monkeypatch.setattr(control._def, "SIMULATE_FILTER_WHEEL", True)
        monkeypatch.setattr(control._def, "SIMULATE_OBJECTIVE_CHANGER", True)
        monkeypatch.setattr(control._def, "SIMULATE_LASER_AF_CAMERA", True)

        # Build microscope WITHOUT --simulation flag (simulated=False)
        scope = control.microscope.Microscope.build_from_global_config(simulated=False)
        try:
            # Camera should be simulated due to per-component setting
            assert isinstance(
                scope.camera, SimulatedCamera
            ), f"Expected SimulatedCamera but got {type(scope.camera).__name__}"
        finally:
            scope.close()

    def test_global_simulation_overrides_per_component(self, monkeypatch):
        """--simulation flag should simulate ALL components regardless of per-component settings."""
        # Set per-component to "Real Hardware" (False) for all components
        monkeypatch.setattr(control._def, "SIMULATE_CAMERA", False)
        monkeypatch.setattr(control._def, "SIMULATE_MICROCONTROLLER", False)
        monkeypatch.setattr(control._def, "SIMULATE_SPINNING_DISK", False)
        monkeypatch.setattr(control._def, "SIMULATE_FILTER_WHEEL", False)
        monkeypatch.setattr(control._def, "SIMULATE_OBJECTIVE_CHANGER", False)
        monkeypatch.setattr(control._def, "SIMULATE_LASER_AF_CAMERA", False)

        # Build microscope WITH --simulation flag (simulated=True)
        scope = control.microscope.Microscope.build_from_global_config(simulated=True)
        try:
            # Camera should be simulated because --simulation overrides per-component
            assert isinstance(
                scope.camera, SimulatedCamera
            ), f"Expected SimulatedCamera (--simulation overrides per-component) but got {type(scope.camera).__name__}"
        finally:
            scope.close()
