import pytest
import control.microscope


@pytest.mark.integration
def test_create_simulated_microscope(simulated_microscope):
    """Test creating a simulated microscope."""
    # The fixture handles creation and cleanup
    assert simulated_microscope is not None


@pytest.mark.integration
def test_simulated_scope_basic_ops(simulated_microscope):
    """Test basic operations on simulated microscope."""
    scope = simulated_microscope

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
