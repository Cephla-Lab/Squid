"""The start-time check that a Set Z-range piezo stack stays inside the piezo's travel."""

from unittest.mock import MagicMock

import pytest

from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_utils import piezo_z_range_problem
from control.piezo import PiezoStage


@pytest.mark.parametrize(
    "piezo_z_range_um, nz, delta_z_um, z_stacking_config",
    [
        ((20.0, 26.0), 4, 2.0, "FROM BOTTOM"),  # planes 20..26
        ((0.0, 300.0), 4, 100.0, "FROM BOTTOM"),  # planes 0..300: both ends of the travel are reachable
        ((2.0, 10.0), 3, 4.0, "FROM TOP"),  # planes 10, 6, 2
    ],
)
def test_a_stack_inside_the_travel_passes(piezo_z_range_um, nz, delta_z_um, z_stacking_config):
    assert piezo_z_range_problem(piezo_z_range_um, nz, delta_z_um, z_stacking_config, travel_um=300.0) is None


def test_a_last_plane_past_the_top_of_the_travel_is_refused_with_the_overrun():
    # Nz = ceil((300 - 280) / 7) + 1 = 4 puts the last plane at 301 µm: 1 µm past Z-max and the travel.
    problem = piezo_z_range_problem((280.0, 300.0), 4, 7.0, "FROM BOTTOM", travel_um=300.0)
    assert problem is not None
    assert "1.0 µm" in problem


def test_a_from_top_stack_below_zero_is_refused_with_the_overrun():
    # From the top: 10, 6, 2, -2 µm.
    problem = piezo_z_range_problem((2.0, 10.0), 4, 4.0, "FROM TOP", travel_um=300.0)
    assert problem is not None
    assert "2.0 µm" in problem


def _controller(piezo_z_range_um, use_piezo=True, nz=4, delta_z_mm=0.002):
    c = MultiPointController.__new__(MultiPointController)
    c._log = MagicMock()
    c.do_reflection_af = False
    c.use_piezo = use_piezo
    c.piezo = PiezoStage(MagicMock(), {"OBJECTIVE_PIEZO_RANGE_UM": 300})
    c.NZ = nz
    c.deltaZ = delta_z_mm
    c.z_stacking_config = "FROM BOTTOM"
    c.piezo_z_range_um = piezo_z_range_um
    return c


def test_start_is_refused_when_the_piezo_stack_leaves_the_travel():
    c = _controller(piezo_z_range_um=(296.0, 300.0), nz=4, delta_z_mm=0.002)  # planes 296..302
    assert c.validate_acquisition_settings() is False
    c._log.error.assert_called_once()


def test_start_is_refused_when_a_piezo_range_is_set_but_the_stack_runs_on_the_stepper():
    c = _controller(piezo_z_range_um=(20.0, 26.0), use_piezo=False)
    assert c.validate_acquisition_settings() is False


@pytest.mark.parametrize("piezo_z_range_um", [None, (20.0, 26.0)])
def test_start_is_allowed_without_a_piezo_range_or_with_one_that_fits(piezo_z_range_um):
    assert _controller(piezo_z_range_um).validate_acquisition_settings() is True


def test_start_is_refused_when_laser_af_is_on_with_a_piezo_range():
    # Laser AF moves the piezo to its reference at every FOV and the stack runs from there, so a range of
    # absolute piezo positions would be ignored (and the travel check above would vouch for planes never taken).
    c = _controller(piezo_z_range_um=(20.0, 26.0))
    c.do_reflection_af = True
    c.laserAutoFocusController = MagicMock()
    c.laserAutoFocusController.laser_af_properties.has_reference = True
    assert c.validate_acquisition_settings() is False
    c._log.error.assert_called_once()
    assert "laser AF" in c._log.error.call_args.args[0]


def test_a_from_center_stack_with_a_range_is_refused():
    # The worker moves the piezo to Z-min, then From Center steps it below that by half the stack: a range
    # has no centre to run from (0..10 µm, Nz 5, dz 2 µm would ask the piezo for -4 µm).
    problem = piezo_z_range_problem((0.0, 10.0), 5, 2.0, "FROM CENTER", travel_um=300.0)
    assert problem is not None
    assert "From Center" in problem
