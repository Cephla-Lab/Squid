import pytest

import control.microscope
import squid.config
from control._def import FocusMeasureOperator
from control.objective_calibration_hardware import (
    MicroscopeCalibrationHardware,
    camera_key,
    image_transform,
    simulation_hardware,
)
from control.utils import FlipVariant, calculate_focus_measure
from squid.objective_calibration.engine import ObjectiveSpec, RunConfig, run_calibration
from squid.objective_calibration.hardware import CalibrationError, LimitError


@pytest.fixture
def scope():
    scope = control.microscope.Microscope.build_from_global_config(simulated=True)
    yield scope
    scope.close()


class _Changer:
    def __init__(self):
        self.moves = []
        self.fail = False

    def move_to_objective(self, name):
        self.moves.append(name)
        if self.fail:
            raise RuntimeError("turret rotated but its Z restore failed")


def _hw(scope, changer=None):
    return MicroscopeCalibrationHardware(
        scope, camera_config=squid.config.get_camera_config(), objective_changer=changer
    )


def test_limits_and_microstep_come_from_the_stage_config(scope):
    hw = _hw(scope)
    cfg = scope.stage.get_config()
    assert hw.xy_limits_um() == (
        (cfg.X_AXIS.MIN_POSITION * 1000, cfg.X_AXIS.MAX_POSITION * 1000),
        (cfg.Y_AXIS.MIN_POSITION * 1000, cfg.Y_AXIS.MAX_POSITION * 1000),
    )
    assert hw.z_limits_um() == (cfg.Z_AXIS.MIN_POSITION * 1000, cfg.Z_AXIS.MAX_POSITION * 1000)
    per_axis = [1000 * a.SCREW_PITCH / (a.MICROSTEPS_PER_STEP * a.FULL_STEPS_PER_REV) for a in (cfg.X_AXIS, cfg.Y_AXIS)]
    assert hw.xy_microstep_um() == pytest.approx(max(per_axis))


def test_moves_round_trip_in_micrometres(scope):
    hw = _hw(scope)
    (x_low, x_high), (y_low, y_high) = hw.xy_limits_um()
    x, y = (x_low + x_high) / 2, (y_low + y_high) / 2
    hw.move_xy_to_um(x, y)
    assert hw.get_xy_um() == pytest.approx((x, y), abs=2 * hw.xy_microstep_um())
    z_low, z_high = hw.z_limits_um()
    z = z_low + 0.25 * (z_high - z_low)
    hw.move_z_to_um(z)
    assert hw.get_z_um() == pytest.approx(z, abs=1.0)


def test_targets_outside_the_limits_are_refused_before_moving(scope):
    hw = _hw(scope)
    before = hw.get_xy_um()
    (x_low, _), _ = hw.xy_limits_um()
    with pytest.raises(LimitError):
        hw.move_xy_to_um(x_low - 1000.0, before[1])
    assert hw.get_xy_um() == before
    z_low, _ = hw.z_limits_um()
    z_before = hw.get_z_um()
    with pytest.raises(LimitError):
        hw.move_z_to_um(z_low - 10.0)
    assert hw.get_z_um() == z_before


def test_snap_applies_the_channel_and_returns_a_2d_frame(scope):
    hw = _hw(scope)
    objective = scope.objective_store.current_objective
    channel = scope.live_controller.get_channels(objective)[0].name
    frame = hw.snap(objective, channel)
    assert frame.ndim == 2
    assert frame.shape == hw.frame_shape(channel)
    assert scope.live_controller.currentConfiguration.name == channel


def test_a_channel_missing_for_the_objective_is_a_gate_failure(scope):
    hw = _hw(scope)
    with pytest.raises(CalibrationError, match="not enabled"):
        hw.snap(scope.objective_store.current_objective, "No such channel")


def test_restore_mode_reapplies_the_start_channel_and_the_next_run_reapplies_its_own(scope):
    objective = scope.objective_store.current_objective
    channels = scope.live_controller.get_channels(objective)
    assert len(channels) >= 2
    scope.live_controller.set_microscope_mode(channels[0])
    hw = _hw(scope)
    hw.snap(objective, channels[1].name)
    hw.restore_mode()
    assert scope.live_controller.currentConfiguration.name == channels[0].name
    hw.snap(objective, channels[1].name)  # a second run in the same dialog, same adapter
    assert scope.live_controller.currentConfiguration.name == channels[1].name


def test_switch_moves_the_changer_once_and_updates_the_store(scope):
    changer = _Changer()
    hw = _hw(scope, changer)
    assert hw.has_changer
    other = next(n for n in scope.objective_store.objectives_dict if n != hw.current_objective())
    hw.switch_objective(other)
    hw.switch_objective(other)
    assert changer.moves == [other]
    assert hw.current_objective() == scope.objective_store.current_objective == other


def test_a_switch_that_fails_partway_forces_the_restore_to_switch_back(scope):
    changer = _Changer()
    hw = _hw(scope, changer)
    start = hw.current_objective()
    other = next(n for n in scope.objective_store.objectives_dict if n != start)
    changer.fail = True
    with pytest.raises(RuntimeError, match="Z restore failed"):
        hw.switch_objective(other)
    assert hw.current_objective() is None  # unknown: the turret may have rotated
    changer.fail = False
    hw.switch_objective(start)  # the restore: must move the changer although the store still says `start`
    assert changer.moves == [other, start]
    assert hw.current_objective() == start


def test_binning_comes_from_the_camera(scope):
    assert _hw(scope).binning() == tuple(scope.camera.get_binning())


def test_without_a_changer_switch_only_updates_the_store(scope):
    hw = _hw(scope)
    assert not hw.has_changer
    other = next(n for n in scope.objective_store.objectives_dict if n != hw.current_objective())
    hw.switch_objective(other)
    assert scope.objective_store.current_objective == other


def test_camera_key_and_image_transform():
    base = squid.config.get_camera_config()
    config = base.model_copy(update={"serial_number": "SN42", "rotate_image_angle": 90.0, "flip": FlipVariant.VERTICAL})
    assert camera_key(config).startswith(base.camera_type.value + "/")
    assert camera_key(config).endswith("/SN42")
    assert camera_key(base.model_copy(update={"serial_number": None})).endswith("/unknown")
    assert image_transform(config) == (90.0, "Vertical")
    assert image_transform(base.model_copy(update={"rotate_image_angle": None, "flip": None})) == (None, None)


def test_simulation_hardware_calibrates_end_to_end():
    # 60x is the hardest case: 0.063 um pixels against the 0.79 um stage microstep.
    specs = [
        ObjectiveSpec("4x", 4, 0.13, 0.94),
        ObjectiveSpec("20x", 20, 0.8, 0.188),
        ObjectiveSpec("60x", 60, 1.2, 0.063),
    ]
    hw = simulation_hardware(specs, "20x", 3.76)
    result = run_calibration(
        hw,
        RunConfig(specs, "BF", 100.0, 1),
        fine_metric=lambda crop: float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE)),
    )
    assert result.stopped is None
    assert set(result.pixel_sizes) == {"4x", "20x", "60x"}
    for name, summary in result.pixel_sizes.items():
        # Stage-limited (spec B §5.2, ~error/distance): 0.1 um over the ~15 um 60x moves is ~0.7%.
        assert summary.pixel_size_um == pytest.approx(hw.objectives[name].pixel_um, rel=0.01)


def test_simulation_hardware_has_something_to_measure():
    specs = [ObjectiveSpec("4x", 4, 0.13, 1.625), ObjectiveSpec("20x", 20, 0.4, 0.325)]
    hw = simulation_hardware(specs, "20x", 6.5)
    assert hw.current_objective() == "20x"
    assert hw.binned_sensor_pixel_um() == 6.5
    for spec in specs:
        assert hw.objectives[spec.name].pixel_um == pytest.approx(spec.nominal_px_um, rel=0.031)
        assert hw.objectives[spec.name].pixel_um != spec.nominal_px_um
