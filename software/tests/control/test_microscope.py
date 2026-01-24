from unittest.mock import patch

import control.microscope
import squid.stage.cephla
import squid.config
from control.microcontroller import Microcontroller, SimSerial
from tests.control.test_microcontroller import get_test_micro


def test_create_simulated_microscope():
    sim_scope = control.microscope.Microscope.build_from_global_config(True)
    sim_scope.close()


def test_create_simulated_microscope_with_skip_homing():
    """Test that skip_homing flag is accepted and doesn't cause errors."""
    sim_scope = control.microscope.Microscope.build_from_global_config(True, skip_homing=True)
    sim_scope.close()


def test_skip_homing_skips_addon_homing():
    """Test that skip_homing=True actually skips homing operations in addons."""
    with patch.object(control.microscope.MicroscopeAddons, "prepare_for_use") as mock_prepare:
        sim_scope = control.microscope.Microscope.build_from_global_config(True, skip_homing=True)

        # Verify prepare_for_use was called with skip_homing=True
        mock_prepare.assert_called_once()
        call_kwargs = mock_prepare.call_args.kwargs
        assert call_kwargs.get("skip_homing") is True, "prepare_for_use should be called with skip_homing=True"

        sim_scope.close()


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
