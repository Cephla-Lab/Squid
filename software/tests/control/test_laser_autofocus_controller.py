"""End-to-end tests of the laser AF controller against the Z-responsive simulated focus camera."""

import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.core.config import ConfigRepository
from squid.camera.utils import SimulatedFocusCamera


@pytest.fixture
def sim_laser_af_controller(monkeypatch, tmp_path):
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    # Keep per-objective laser AF configs (written by set_reference) out of the repo tree.
    scope.config_repo = ConfigRepository(base_path=tmp_path)
    # Start from a focused, in-soft-limit Z like real hardware (the simulated stage boots at 0.0,
    # which is below the Z soft-limit minimum where absolute moves clamp).
    z_config = scope.stage.get_config().Z_AXIS
    scope.stage.move_z_to((z_config.MIN_POSITION + z_config.MAX_POSITION) / 2.0)
    controller = ts.get_test_laser_autofocus_controller(scope)
    yield controller
    scope.close()


def test_simulated_laser_af_responds_to_z(sim_laser_af_controller):
    controller = sim_laser_af_controller
    assert isinstance(controller.camera, SimulatedFocusCamera)

    assert controller.initialize_auto()
    assert controller.set_reference()
    assert controller.measure_displacement() == pytest.approx(0.0, abs=1.5)

    controller.stage.move_z(10.0 / 1000.0)
    assert controller.measure_displacement() == pytest.approx(10.0, abs=1.5)


def test_measure_displacement_detailed_returns_spot_and_image(sim_laser_af_controller):
    controller = sim_laser_af_controller
    assert controller.initialize_auto()
    assert controller.set_reference()
    controller.stage.move_z(5.0 / 1000.0)

    m = controller.measure_displacement_detailed()

    assert m.displacement_um == pytest.approx(5.0, abs=1.0)
    props = controller.laser_af_properties
    assert m.displacement_um == pytest.approx((m.spot_x_px - props.x_reference) * props.pixel_to_um)
    assert m.spot_y_px == pytest.approx(controller.camera.get_resolution()[1] / 2, abs=3.0)
    assert m.image is not None and m.image.ndim == 2
