import control.microscope
import squid.stage.cephla
import squid.config
from control.microcontroller import Microcontroller, SimSerial
from control.microscope import _should_simulate
from tests.control.test_microcontroller import get_test_micro


class TestShouldSimulate:
    """Tests for _should_simulate() per-component simulation logic."""

    def test_auto_follows_global_flag_true(self):
        """Auto (None) with --simulation flag should simulate."""
        assert _should_simulate(global_simulated=True, component_override=None) is True

    def test_auto_follows_global_flag_false(self):
        """Auto (None) without --simulation flag should use real hardware."""
        assert _should_simulate(global_simulated=False, component_override=None) is False

    def test_simulate_override_without_global(self):
        """Simulate (True) should simulate even without --simulation flag."""
        assert _should_simulate(global_simulated=False, component_override=True) is True

    def test_simulate_override_with_global(self):
        """Simulate (True) with --simulation flag should simulate."""
        assert _should_simulate(global_simulated=True, component_override=True) is True

    def test_real_hardware_override_without_global(self):
        """Real Hardware (False) without --simulation should use real hardware."""
        assert _should_simulate(global_simulated=False, component_override=False) is False

    def test_real_hardware_override_with_global(self):
        """Real Hardware (False) should use real hardware even with --simulation flag."""
        assert _should_simulate(global_simulated=True, component_override=False) is False


def test_create_simulated_microscope():
    sim_scope = control.microscope.Microscope.build_from_global_config(True)
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
