"""The Z lift of an upright V-308 system (squid/stage/lift.py) and the loading-position flow that lowers it."""

import json
from unittest.mock import MagicMock

import pytest

import control._def as _def
import squid.config
import squid.stage.utils
from control.firmware_sim_serial import FirmwareSimSerial
from control.microcontroller import Microcontroller
from squid.abc import Pos
from squid.stage.lift import (
    FocusWheelRouter,
    LiftHeights,
    LiftStage,
    default_focus_wheel_mm_per_tick,
)


@pytest.fixture
def sim():
    s = FirmwareSimSerial(strict=True, firmware_version=_def.FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    yield s
    s.close()


@pytest.fixture
def mcu(sim):
    m = Microcontroller(serial_device=sim, reset_and_initialize=False)
    yield m
    m.close()


@pytest.fixture
def z_axis():
    return squid.config.get_stage_config().Z_AXIS


@pytest.fixture
def lift(mcu, z_axis):
    return LiftStage(mcu, z_axis, min_mm=0.0, max_mm=8.0, lowered_mm=0.0)


def _wait_for_pos(lift, mm):
    # The simulated controller answers at once, but the position arrives in the next status packet.
    import time

    deadline = time.time() + 2
    while abs(lift.get_position_mm() - mm) > 1e-3 and time.time() < deadline:
        time.sleep(0.01)
    return lift.get_position_mm()


def test_lift_moves_are_clamped_to_its_own_travel(lift):
    lift.move_to(3.0)
    assert _wait_for_pos(lift, 3.0) == pytest.approx(3.0, abs=1e-3)
    lift.move_to(50.0)
    assert _wait_for_pos(lift, 8.0) == pytest.approx(8.0, abs=1e-3)
    lift.move_relative(-1.5)
    assert _wait_for_pos(lift, 6.5) == pytest.approx(6.5, abs=1e-3)


def test_lift_travel_must_be_ordered(mcu, z_axis):
    with pytest.raises(ValueError):
        LiftStage(mcu, z_axis, min_mm=5.0, max_mm=5.0)


def test_lower_returns_the_height_it_left(lift):
    lift.move_to(4.2)
    _wait_for_pos(lift, 4.2)
    assert lift.lower() == pytest.approx(4.2, abs=1e-3)
    assert _wait_for_pos(lift, 0.0) == pytest.approx(0.0, abs=1e-3)
    assert lift.is_lowered()


def test_home_zeroes_the_lift(lift, sim):
    lift.move_to(2.0)
    _wait_for_pos(lift, 2.0)
    lift.home()
    assert _wait_for_pos(lift, 0.0) == pytest.approx(0.0, abs=1e-3)


def test_limit_switches_are_bottom_right_top_left(lift, sim):
    sim.z_limit_right = True
    lift.move_relative(0.0)  # any command: a fresh status packet carries the inputs
    import time

    deadline = time.time() + 2
    while lift.limit_switches() != (True, False) and time.time() < deadline:
        time.sleep(0.01)
    assert lift.limit_switches() == (True, False)


def test_apply_limits_programs_the_firmware_z_limits(z_axis):
    mcu = MagicMock()
    LiftStage(mcu, z_axis, min_mm=0.5, max_mm=9.0).apply_limits()
    sent = {call.args[0]: call.args[1] for call in mcu.set_lim.call_args_list}
    expected_pos, expected_neg = (
        (_def.LIMIT_CODE.Z_POSITIVE, _def.LIMIT_CODE.Z_NEGATIVE)
        if z_axis.MOVEMENT_SIGN == 1
        else (_def.LIMIT_CODE.Z_NEGATIVE, _def.LIMIT_CODE.Z_POSITIVE)
    )
    assert sent[expected_pos] == z_axis.convert_real_units_to_ustep(9.0)
    assert sent[expected_neg] == z_axis.convert_real_units_to_ustep(0.5)


# --- heights per sample format --------------------------------------------------------------------------------


def test_heights_round_trip(tmp_path):
    path = str(tmp_path / "sub" / "heights.json")
    heights = LiftHeights(path)
    assert heights.by_format == {} and heights.get("96 well plate") is None
    heights.set("96 well plate", 6.25)
    heights.set("glass slide", 5.5)
    heights.set("glass slide", None)  # cleared
    heights.save()
    assert LiftHeights(path).by_format == {"96 well plate": 6.25}


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"heights_by_format": ["a"]}),
        json.dumps({"heights_by_format": {"a": "nan"}}),
        json.dumps({"heights_by_format": {"a": "x"}}),
    ],
)
def test_unreadable_heights_start_empty(tmp_path, content):
    path = tmp_path / "heights.json"
    path.write_text(content)
    assert LiftHeights(str(path)).by_format == {}


def test_sample_height_follows_the_current_format(lift, tmp_path):
    lift.heights = LiftHeights(str(tmp_path / "h.json"))
    lift.heights.set("96 well plate", 6.0)
    fmt = ["96 well plate"]
    lift.current_format_fn = lambda: fmt[0]
    assert lift.sample_height_mm() == 6.0
    fmt[0] = "glass slide"
    assert lift.sample_height_mm() is None


# --- focus wheel router -------------------------------------------------------------------------------------------


class _FakeFocus:
    def __init__(self, z_mm=1.0):
        self.z_mm = z_mm
        self.targets = []

    def get_pos(self):
        return Pos(x_mm=0.0, y_mm=0.0, z_mm=self.z_mm, theta_rad=None)

    def move_z_to(self, abs_mm, blocking=True):
        assert blocking is False
        self.targets.append(round(abs_mm, 9))


def _router(count=0, enabled=True, z_mm=1.0):
    mcu = MagicMock()
    mcu.focus_wheel_count = count
    focus = _FakeFocus(z_mm)
    router = FocusWheelRouter(mcu, focus, mm_per_tick=0.001, enabled=lambda: enabled)
    router.poll()  # takes the baseline
    return router, mcu, focus


def test_router_accumulates_ticks_onto_the_last_target():
    router, mcu, focus = _router(count=100)
    mcu.focus_wheel_count = 103
    router.poll()
    focus.z_mm = 1.001  # still moving: the next ticks add to the target, not to this
    mcu.focus_wheel_count = 105
    router.poll()
    assert focus.targets == [1.003, 1.005]


def test_router_target_stays_within_the_reachable_range():
    mcu = MagicMock()
    mcu.focus_wheel_count = 0
    focus = _FakeFocus(z_mm=1.0)
    focus.z_range_mm = lambda: (0.0, 1.002)
    router = FocusWheelRouter(mcu, focus, mm_per_tick=0.001)
    router.poll()
    mcu.focus_wheel_count = 10  # 8 ticks past the end
    router.poll()
    mcu.focus_wheel_count = 9  # turned back at once: moves off the end, not 8 ticks later
    router.poll()
    assert focus.targets == [1.002, 1.001]


def test_pi_focus_stage_reports_its_range_in_squid_coordinates():
    import squid.stage.pi

    sim = squid.stage.pi._SimulatedC414()
    sim.initialize(reference=True)
    sim.reset_range_limit(7.0)
    stage = squid.stage.pi.PIFocusStage(sim, invert_z=True)  # squid = 7 - native
    assert stage.z_range_mm() == pytest.approx((0.0, 7.0))
    stage.set_limits(z_pos_mm=6.5, z_neg_mm=1.0)  # fenced at runtime, as at startup
    assert stage.z_range_mm() == pytest.approx((1.0, 6.5))


def test_router_handles_the_int32_wrap():
    router, mcu, focus = _router(count=2**31 - 2)
    mcu.focus_wheel_count = -(2**31) + 1  # +3 ticks across the wrap
    router.poll()
    assert focus.targets == [1.003]


def test_router_drops_ticks_while_disabled():
    enabled = [False]
    mcu = MagicMock()
    mcu.focus_wheel_count = 0
    focus = _FakeFocus()
    router = FocusWheelRouter(mcu, focus, mm_per_tick=0.001, enabled=lambda: enabled[0])
    router.poll()
    mcu.focus_wheel_count = 50
    router.poll()
    assert focus.targets == []
    enabled[0] = True
    mcu.focus_wheel_count = 51
    router.poll()
    assert focus.targets == [1.001]  # only the tick after re-enabling


def test_router_ignores_packets_without_a_wheel_count():
    router, mcu, focus = _router(count=None)
    mcu.focus_wheel_count = None
    router.poll()
    assert focus.targets == []


def test_router_start_and_stop_switch_the_firmware_mode(mcu, sim):
    router = FocusWheelRouter(mcu, _FakeFocus(), mm_per_tick=0.001)
    router.start()
    try:
        assert sim.focus_wheel_mode == _def.FOCUS_WHEEL_MODE.TO_HOST
    finally:
        router.stop()
    assert sim.focus_wheel_mode == _def.FOCUS_WHEEL_MODE.DRIVES_Z


def test_default_wheel_step_matches_one_stepper_microstep(z_axis, monkeypatch):
    monkeypatch.setattr(_def, "PI_FOCUS_WHEEL_UM_PER_TICK", 0.0)
    monkeypatch.setattr(_def, "PI_FOCUS_WHEEL_INVERT", False)
    assert default_focus_wheel_mm_per_tick(z_axis) == pytest.approx(z_axis.convert_to_real_units(1))
    monkeypatch.setattr(_def, "PI_FOCUS_WHEEL_UM_PER_TICK", 0.5)
    monkeypatch.setattr(_def, "PI_FOCUS_WHEEL_INVERT", True)
    assert default_focus_wheel_mm_per_tick(z_axis) == pytest.approx(-0.0005 * z_axis.MOVEMENT_SIGN)


# --- loading position ---------------------------------------------------------------------------------------------


class _RecordingLift:
    def __init__(self, events, height=4.0, sample_format="96 well plate", heights=None):
        self.events = events
        self.height = height
        self.sample_format = sample_format
        self.heights = heights or {}

    def current_format(self):
        return self.sample_format

    def sample_height_mm(self):
        return self.heights.get(self.sample_format)

    def lower(self):
        self.events.append("lift_lower")
        before, self.height = self.height, 0.0
        return before

    def move_to(self, mm, blocking=True):
        self.events.append(f"lift_to_{mm}")
        self.height = mm


def _recording_stage(events, **lift_kwargs):
    stage = MagicMock()
    stage.lift = _RecordingLift(events, **lift_kwargs)
    stage._scanning_position_lift_mm = None
    stage.move_x_to.side_effect = lambda mm, *a, **k: events.append("x")
    stage.move_y_to.side_effect = lambda mm, *a, **k: events.append("y")
    return stage


def _load_then_unload(stage, events, change_format_to=None):
    squid.stage.utils.move_to_loading_position(stage, blocking=True, is_wellplate=False)
    assert events == ["lift_lower", "y", "x"]
    events.clear()
    if change_format_to is not None:
        stage.lift.sample_format = change_format_to
    squid.stage.utils.move_to_scanning_position(stage, blocking=True, is_wellplate=False)
    assert stage._scanning_position_lift_mm is None


def test_unloading_raises_the_lift_to_the_sample_format_height_last():
    events = []
    stage = _recording_stage(events, heights={"96 well plate": 6.25})
    _load_then_unload(stage, events)
    assert events == ["y", "x", "lift_to_6.25"]


def test_unloading_uses_the_new_format_height_after_a_format_change():
    events = []
    stage = _recording_stage(events, heights={"96 well plate": 6.25, "glass slide": 5.5})
    _load_then_unload(stage, events, change_format_to="glass slide")
    assert events == ["y", "x", "lift_to_5.5"]


def test_unloading_without_a_saved_height_goes_back_for_the_same_format_only():
    events = []
    stage = _recording_stage(events)
    _load_then_unload(stage, events)
    assert events == ["y", "x", "lift_to_4.0"]

    events.clear()
    stage = _recording_stage(events)
    _load_then_unload(stage, events, change_format_to="glass slide")
    assert events == ["y", "x"]  # a different sample with no height of its own: stays lowered


def test_loading_without_a_lift_is_unchanged():
    stage = MagicMock(spec=["move_x_to", "move_y_to"])
    squid.stage.utils.move_to_loading_position(stage, blocking=True, is_wellplate=False)
    squid.stage.utils.move_to_scanning_position(stage, blocking=True, is_wellplate=False)
    assert stage.move_x_to.call_count == 2 and stage.move_y_to.call_count == 2


# --- XY homing interlock ------------------------------------------------------------------------------------------


def _combined_with_lift(lift):
    import squid.stage.pi

    xy = MagicMock()
    xy.get_config.return_value = squid.config.get_stage_config()
    z = MagicMock()
    z.z_mm_to_usteps.return_value = 100000
    return squid.stage.pi.CombinedStage(xy_stage=xy, z_stage=z, lift=lift), xy


def test_xy_homing_lowers_the_lift_first(lift):
    lift.move_to(5.0)
    _wait_for_pos(lift, 5.0)
    stage, xy = _combined_with_lift(lift)
    stage.home(x=True, y=False, z=False, theta=False)
    assert lift.is_lowered()
    xy.home.assert_called_once()


def test_xy_homing_is_refused_when_the_lift_does_not_get_down():
    stuck = MagicMock()
    stuck.is_lowered.return_value = False
    stuck.get_position_mm.return_value = 5.0
    stuck.lowered_mm = 0.0
    stage, xy = _combined_with_lift(stuck)
    with pytest.raises(RuntimeError, match="XY homing refused"):
        stage.home(x=True, y=True, z=False, theta=False)
    stuck.lower.assert_called_once()
    xy.home.assert_not_called()


def test_z_only_homing_does_not_touch_the_lift():
    lift = MagicMock()
    stage, _ = _combined_with_lift(lift)
    stage.home(x=False, y=False, z=True, theta=False)
    lift.lower.assert_not_called()


# --- microscope -----------------------------------------------------------------------------------------------------


def _lift_scope(monkeypatch, tmp_path, heights=None, sample_format="96 well plate"):
    import control.microscope

    monkeypatch.setattr(_def, "USE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(_def, "SIMULATE_PI_FOCUS_STAGE", True, raising=False)
    monkeypatch.setattr(_def, "USE_Z_LIFT_STAGE", True, raising=False)
    monkeypatch.setattr(_def, "Z_LIFT_MIN_MM", 0.0, raising=False)
    monkeypatch.setattr(_def, "Z_LIFT_MAX_MM", 8.0, raising=False)
    monkeypatch.setattr(_def, "HOMING_ENABLED_X", False, raising=False)
    monkeypatch.setattr(_def, "HOMING_ENABLED_Y", False, raising=False)
    monkeypatch.setattr(_def, "USE_OBJECTIVE_TURRET", False)
    monkeypatch.setattr(_def, "USE_XERYON", False)
    monkeypatch.setattr(_def, "WELLPLATE_FORMAT", sample_format)
    path = tmp_path / "heights.json"
    if heights is not None:
        path.write_text(json.dumps({"heights_by_format": heights}))
    monkeypatch.setattr(_def, "Z_LIFT_HEIGHTS_PATH", str(path), raising=False)
    return control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)


def test_microscope_builds_the_lift_on_the_cephla_z(monkeypatch, tmp_path):
    scope = _lift_scope(monkeypatch, tmp_path)
    try:
        assert scope.z_lift is scope.stage.lift
        assert scope.z_lift.heights.by_format == {}
        assert scope.z_lift.current_format() == "96 well plate"
        # Squid's Z is still the V-308; the lift moves the stepper without touching it.
        scope.stage.home(x=False, y=False, z=True, theta=False)
        scope.stage.move_z_to(0.3)
        scope.z_lift.move_to(2.0)
        _wait_for_pos(scope.z_lift, 2.0)
        assert scope.stage.get_pos().z_mm == pytest.approx(0.3, abs=1e-9)
        # SimSerial reports firmware 1.3: the wheel stays on the lift, no router.
        assert scope.focus_wheel_router is None
    finally:
        scope.close()


def test_homing_lowers_the_lift_then_rises_to_the_sample_format_height(monkeypatch, tmp_path):
    scope = _lift_scope(monkeypatch, tmp_path, heights={"96 well plate": 3.5, "glass slide": 2.0})
    try:
        scope.z_lift.move_to(6.0)
        _wait_for_pos(scope.z_lift, 6.0)
        calls = []
        real_home = scope.z_lift.home
        scope.z_lift.home = lambda: (calls.append("lift_home"), real_home())
        scope.home_xyz()
        assert calls == ["lift_home"]
        assert _wait_for_pos(scope.z_lift, 0.0) == pytest.approx(0.0, abs=1e-3)
        scope._raise_z_lift_to_sample_height()
        assert _wait_for_pos(scope.z_lift, 3.5) == pytest.approx(3.5, abs=1e-3)
    finally:
        scope.close()


def test_homing_leaves_the_lift_lowered_for_a_format_without_a_height(monkeypatch, tmp_path):
    scope = _lift_scope(monkeypatch, tmp_path, heights={"glass slide": 2.0})
    try:
        scope.home_xyz()
        scope._raise_z_lift_to_sample_height()
        assert _wait_for_pos(scope.z_lift, 0.0) == pytest.approx(0.0, abs=1e-3)
    finally:
        scope.close()


def test_lift_needs_the_pi_focus_stage(monkeypatch):
    import control.microscope

    monkeypatch.setattr(_def, "USE_PI_FOCUS_STAGE", False, raising=False)
    monkeypatch.setattr(_def, "USE_Z_LIFT_STAGE", True, raising=False)
    with pytest.raises(ValueError, match="USE_PI_FOCUS_STAGE"):
        control.microscope.Microscope.build_from_global_config(simulated=True, skip_init=True)


# --- focus wheel speed cap ----------------------------------------------------------------------------------------


def _capped_router(monkeypatch, max_speed_mm_s=0.1):
    import squid.stage.lift as lift_module

    clock = [100.0]
    monkeypatch.setattr(lift_module.time, "monotonic", lambda: clock[0])
    mcu = MagicMock()
    mcu.focus_wheel_count = 0
    focus = _FakeFocus(z_mm=1.0)
    router = FocusWheelRouter(mcu, focus, mm_per_tick=0.001, max_speed_mm_s=max_speed_mm_s)
    router.poll()  # baseline
    return router, mcu, focus, clock


def test_wheel_speed_is_capped_and_extra_ticks_are_dropped(monkeypatch):
    router, mcu, focus, clock = _capped_router(monkeypatch)  # 100 um/s
    clock[0] += 0.03
    mcu.focus_wheel_count = 50  # 50 um in one 30 ms poll = 1.67 mm/s
    router.poll()
    assert focus.targets == [pytest.approx(1.003)]  # 100 um/s x 30 ms

    clock[0] += 0.03  # the wheel has stopped: nothing queued keeps Z moving
    router.poll()
    assert len(focus.targets) == 1

    clock[0] += 0.03
    mcu.focus_wheel_count = 49  # turned back: moves back at once, from the capped target
    router.poll()
    assert focus.targets[-1] == pytest.approx(1.002)


def test_slow_turns_are_not_capped(monkeypatch):
    router, mcu, focus, clock = _capped_router(monkeypatch)
    clock[0] += 0.03
    mcu.focus_wheel_count = 2  # 2 um in 30 ms = 67 um/s, under the cap
    router.poll()
    assert focus.targets == [pytest.approx(1.002)]


def test_a_late_poll_does_not_make_a_large_step(monkeypatch):
    router, mcu, focus, clock = _capped_router(monkeypatch)
    clock[0] += 2.0  # the polling thread stalled
    mcu.focus_wheel_count = 500
    router.poll()
    assert focus.targets == [pytest.approx(1.010)]  # capped to 100 um/s x 0.1 s


def test_no_cap_when_max_speed_is_zero(monkeypatch):
    router, mcu, focus, clock = _capped_router(monkeypatch, max_speed_mm_s=0.0)
    clock[0] += 0.03
    mcu.focus_wheel_count = 50
    router.poll()
    assert focus.targets == [pytest.approx(1.050)]


def test_default_wheel_scale_is_fine_enough_for_the_v308(z_axis):
    # The panel's smallest step is 16 counts; at the default scale that is 0.1 um of V-308 travel.
    assert _def.PI_FOCUS_WHEEL_UM_PER_TICK == 0.00625
    assert abs(default_focus_wheel_mm_per_tick(z_axis)) * 16 * 1000 == pytest.approx(0.1)
