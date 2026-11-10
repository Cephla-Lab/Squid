"""Laser AF z spot-search, motion confirm and iterative correction, on a fake z axis.

The controller's methods are run against a stand-in object (no Qt, no microscope) whose spot position is a
function of z, so each test states the optics it assumes.
"""

import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import control._def
from control._def import LaserAFConfirmMotionMode
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.models import LaserAFConfig

X_REFERENCE_PX = 500.0
PIXEL_TO_UM = 0.5
START_Z_UM = 1000.0


class FakeStage:
    def __init__(self, z_um):
        self.z_um = z_um
        self.moves_um = []

    def get_pos(self):
        return SimpleNamespace(z_mm=self.z_um / 1000)

    def move_z(self, delta_mm):
        self.moves_um.append(delta_mm * 1000)
        self.z_um += delta_mm * 1000


class FakeController:
    """The real search / confirm / correction methods over a fake stage, laser and spot detector."""

    get_current_z_um = LaserAutofocusController.get_current_z_um
    measure_displacement = LaserAutofocusController.measure_displacement
    move_to_target = LaserAutofocusController.move_to_target
    _build_search_positions = LaserAutofocusController._build_search_positions
    _confirm_spot_moves_with_z = LaserAutofocusController._confirm_spot_moves_with_z
    _get_displacement_from_centroid = LaserAutofocusController._get_displacement_from_centroid
    _should_iterate_correction = LaserAutofocusController._should_iterate_correction
    _converge_on_target = LaserAutofocusController._converge_on_target
    _restore_to_position = LaserAutofocusController._restore_to_position
    _move_z = LaserAutofocusController._move_z

    def __init__(self, spot_x_at_z, piezo=None, **config):
        """spot_x_at_z: z_um -> spot x in pixels, or None where no spot is detected."""
        self._log = MagicMock()
        self.stage = FakeStage(START_Z_UM)
        self.piezo = piezo
        self.spot_x_at_z = spot_x_at_z
        self.laser_on = False
        self.laser_was_on_for_every_detection = True
        self.signal_displacement_um = MagicMock()
        self.signal_cross_correlation = MagicMock()
        self.signal_piezo_position_update = MagicMock()
        self.aligned = True
        self.laser_af_properties = LaserAFConfig(
            x_reference=X_REFERENCE_PX, pixel_to_um=PIXEL_TO_UM, has_reference=True, **config
        )

    def _turn_on_laser(self):
        self.laser_on = True

    def _turn_off_laser(self):
        self.laser_on = False

    def _get_laser_spot_centroid(self):
        self.laser_was_on_for_every_detection &= self.laser_on
        x = self.spot_x_at_z(self.get_current_z_um())
        return None if x is None else (x, 128.0)

    def _verify_spot_alignment(self):
        return self.aligned, 1.0 if self.aligned else 0.0


def linear_spot(focus_z_um, visible_within_um=math.inf, gain=1.0):
    """A sample reflection: x moves with defocus, and is only detected near focus.

    gain < 1 makes the spot move less than the calibration predicts, as it does far from focus.
    """

    def spot_x(z_um):
        defocus_um = z_um - focus_z_um
        if abs(defocus_um) > visible_within_um:
            return None
        return X_REFERENCE_PX + gain * defocus_um / PIXEL_TO_UM

    return spot_x


# -- search positions --------------------------------------------------------------------------


def test_search_positions_go_down_first_then_up(monkeypatch):
    monkeypatch.setattr(control._def, "LASER_AF_SEARCH_DOWN_FIRST", True)
    controller = FakeController(linear_spot(START_Z_UM))

    current, step, positions = controller._build_search_positions(range_um=30, step_um=10)

    assert (current, step) == (1000.0, 10.0)
    assert positions == [990.0, 980.0, 970.0, 1000.0, 1010.0, 1020.0, 1030.0]


def test_search_positions_can_go_up_first(monkeypatch):
    monkeypatch.setattr(control._def, "LASER_AF_SEARCH_DOWN_FIRST", False)
    controller = FakeController(linear_spot(START_Z_UM))

    _, _, positions = controller._build_search_positions(range_um=20, step_um=10)

    assert positions == [1010.0, 1020.0, 1000.0, 990.0, 980.0]


def test_search_positions_default_to_the_configured_search_span_not_the_displacement_ceiling():
    controller = FakeController(
        linear_spot(START_Z_UM), laser_af_range=200, laser_af_search_range_um=20, laser_af_search_step_um=5
    )

    _, step, positions = controller._build_search_positions()

    assert step == 5
    assert (min(positions), max(positions), len(positions)) == (980.0, 1020.0, 9)


def test_search_positions_are_clamped_to_the_piezo_travel(monkeypatch):
    monkeypatch.setattr(control._def, "LASER_AF_SEARCH_DOWN_FIRST", True)
    piezo = SimpleNamespace(position=15.0, range_um=300.0)
    controller = FakeController(linear_spot(START_Z_UM), piezo=piezo)

    current, _, positions = controller._build_search_positions(range_um=30, step_um=10)

    assert current == 15.0
    assert positions == [5.0, 15.0, 25.0, 35.0, 45.0]


def test_a_step_larger_than_the_range_only_rechecks_the_current_position():
    controller = FakeController(linear_spot(START_Z_UM))

    _, _, positions = controller._build_search_positions(range_um=5, step_um=10)

    assert positions == [1000.0]
    controller._log.warning.assert_called_once()


def test_a_zero_step_does_not_hang():
    controller = FakeController(linear_spot(START_Z_UM))

    _, step, positions = controller._build_search_positions(range_um=0.2, step_um=0)

    assert step > 0
    assert 1 < len(positions) < 20


# -- z spot-search -----------------------------------------------------------------------------


def test_measurement_at_the_current_position_does_not_move_z():
    controller = FakeController(linear_spot(START_Z_UM - 4))

    assert controller.measure_displacement() == pytest.approx(4.0)
    assert controller.stage.moves_um == []
    assert not controller.laser_on


def test_search_finds_a_spot_that_is_out_of_view_at_the_start(monkeypatch):
    monkeypatch.setattr(control._def, "LASER_AF_SEARCH_DOWN_FIRST", True)
    # focus is 40 um above; the spot is only detected within 15 um of it
    controller = FakeController(
        linear_spot(START_Z_UM + 40, visible_within_um=15), laser_af_search_range_um=60, laser_af_search_step_um=10
    )

    displacement = controller.measure_displacement()

    # first position (going up) that sees the spot is 10 um short of focus, and z stays there
    assert controller.stage.z_um == pytest.approx(START_Z_UM + 30)
    assert displacement == pytest.approx(-10.0)
    assert controller.laser_was_on_for_every_detection
    assert not controller.laser_on


def test_failed_search_restores_z_and_turns_the_laser_off():
    controller = FakeController(lambda z_um: None, laser_af_search_range_um=30, laser_af_search_step_um=10)

    assert math.isnan(controller.measure_displacement())

    assert len(controller.stage.moves_um) > 6  # it did search
    assert controller.stage.z_um == pytest.approx(START_Z_UM)
    assert not controller.laser_on


def test_no_search_when_not_asked_for():
    controller = FakeController(lambda z_um: None)

    assert math.isnan(controller.measure_displacement(search_for_spot=False))
    assert controller.stage.moves_um == []
    assert not controller.laser_on


def test_laser_is_turned_off_when_detection_raises():
    def broken(z_um):
        raise RuntimeError("dropped frame")

    controller = FakeController(broken)

    with pytest.raises(RuntimeError):
        controller.measure_displacement()
    assert not controller.laser_on


# -- motion confirm ----------------------------------------------------------------------------


def test_confirm_accepts_a_spot_that_moves_with_z_and_restores_z():
    controller = FakeController(linear_spot(START_Z_UM), confirm_step_um=4.0)

    accepted, _ = controller._confirm_spot_moves_with_z(X_REFERENCE_PX)

    assert accepted
    assert controller.stage.z_um == pytest.approx(START_Z_UM)


def test_confirm_rejects_a_static_reflection_and_restores_z():
    controller = FakeController(lambda z_um: X_REFERENCE_PX, confirm_step_um=4.0, confirm_tolerance_px=2.0)

    accepted, reason = controller._confirm_spot_moves_with_z(X_REFERENCE_PX)

    assert not accepted
    assert "rejected" in reason
    assert controller.stage.z_um == pytest.approx(START_Z_UM)


def test_confirm_is_skipped_when_the_step_cannot_discriminate():
    # 1 um at 0.5 um/px predicts 2 px, under the 4 px floor: accept unchecked rather than guess
    controller = FakeController(lambda z_um: X_REFERENCE_PX, confirm_step_um=1.0)

    accepted, reason = controller._confirm_spot_moves_with_z(X_REFERENCE_PX)

    assert accepted
    assert "skipped" in reason
    assert controller.stage.moves_um == []


def test_search_skips_static_reflections_when_confirming(monkeypatch):
    monkeypatch.setattr(control._def, "LASER_AF_SEARCH_DOWN_FIRST", True)

    def static_below_real_above(z_um):
        if z_um < START_Z_UM - 5:
            return X_REFERENCE_PX + 30  # a back-reflection that ignores z
        return linear_spot(START_Z_UM + 20, visible_within_um=8)(z_um)

    config = dict(laser_af_search_range_um=20, laser_af_search_step_um=10, confirm_step_um=4.0)

    unconfirmed = FakeController(static_below_real_above, **config)
    assert unconfirmed.measure_displacement() == pytest.approx(15.0)  # locked onto the back-reflection
    assert unconfirmed.stage.z_um == pytest.approx(START_Z_UM - 10)

    confirmed = FakeController(
        static_below_real_above, confirm_motion_mode=LaserAFConfirmMotionMode.SEARCH_ONLY, **config
    )
    assert confirmed.measure_displacement() == pytest.approx(0.0)
    assert confirmed.stage.z_um == pytest.approx(START_Z_UM + 20)


# -- iterative correction ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "enabled, measured_um, target_um, expected",
    [
        (False, 50.0, 0.0, False),  # off: never iterate, however large the correction
        (True, 9.9, 0.0, False),  # below the 10 um minimum: one linear move is accurate
        (True, 10.0, 0.0, True),
        (True, -50.0, 0.0, True),
        (True, 4.0, -8.0, True),  # the size of the correction is what counts, not the displacement
        (True, 12.0, 8.0, False),
    ],
)
def test_iteration_is_gated_on_the_size_of_the_correction(enabled, measured_um, target_um, expected):
    controller = FakeController(
        linear_spot(START_Z_UM),
        iterative_correction_enabled=enabled,
        iterative_correction_min_displacement_um=10.0,
    )

    assert controller._should_iterate_correction(measured_um, target_um) is expected


def test_single_correction_lands_short_when_the_response_is_not_linear():
    # 50 um out of focus, where the spot moves only 70% as far as the calibration predicts
    controller = FakeController(linear_spot(START_Z_UM - 50, gain=0.7))

    assert controller.move_to_target(0.0)

    assert controller.stage.z_um - (START_Z_UM - 50) == pytest.approx(15.0)  # 30% of the way still to go
    assert len(controller.stage.moves_um) == 1


def test_iterative_correction_converges_on_the_same_optics():
    controller = FakeController(
        linear_spot(START_Z_UM - 50, gain=0.7),
        iterative_correction_enabled=True,
        iterative_correction_tolerance_um=1.0,
        iterative_correction_min_displacement_um=10.0,
    )

    assert controller.move_to_target(0.0)

    residual_um = controller.stage.z_um - (START_Z_UM - 50)
    assert abs(residual_um) < 1.5  # measured residual (0.7 x actual) is inside the 1 um tolerance
    assert 1 < len(controller.stage.moves_um) <= 4


def test_iterative_correction_leaves_small_corrections_alone():
    controller = FakeController(linear_spot(START_Z_UM - 5, gain=0.7), iterative_correction_enabled=True)

    assert controller.move_to_target(0.0)
    assert len(controller.stage.moves_um) == 1


def test_failed_alignment_check_restores_the_starting_z():
    controller = FakeController(linear_spot(START_Z_UM - 50, gain=0.7), iterative_correction_enabled=True)
    controller.aligned = False

    assert not controller.move_to_target(0.0)
    assert controller.stage.z_um == pytest.approx(START_Z_UM)


def test_displacement_beyond_laser_af_range_is_refused():
    controller = FakeController(linear_spot(START_Z_UM - 150), laser_af_range=100)

    assert not controller.move_to_target(0.0)
    assert controller.stage.z_um == pytest.approx(START_Z_UM)
