"""Per-instrument `verify` / `tune` of the filter-wheel tuner: the decision logic and the ini writer.

A wheel's stall edge depends on its load, motor and temperature, so the profile is found per machine. These
tests pin the parts that decide and that write: what counts as lost steps, how the margin is applied, that the
endurance pattern is the bar (a level can pass the short screen and fail it), and that the machine ini is edited
in place without disturbing anything else. No hardware: the tuner's motion primitives are stubbed.
"""

import argparse
import importlib.util
import pathlib

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "filter_wheel_tuner.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("filter_wheel_tuner_under_test", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- limits and the level verdict
def test_lost_limits_scale_with_microstepping(mod):
    assert mod.lost_limits_usteps(8) == (4, 16)  # half a full step, two full steps
    assert mod.lost_limits_usteps(64) == (32, 128)
    assert mod.lost_limits_usteps(1) == (2, 4)  # floors keep rest scatter from failing a coarse setting


def _level(drift=0, worst=0, **extra):
    return {"drift_usteps": drift, "worst_move": {"lost_this_move": worst}, **extra}


def test_level_verdict(mod):
    assert mod.level_ok(_level(drift=-1, worst=3), 4, 16)
    assert not mod.level_ok(_level(drift=5), 4, 16)  # net drift
    assert not mod.level_ok(_level(drift=0, worst=-40), 4, 16)  # a slip that was cancelled by one the other way
    assert not mod.level_ok({"error": "timeout"}, 4, 16)
    assert not mod.level_ok(None, 4, 16)


def test_margin_is_applied_only_below_a_found_edge(mod):
    assert mod.choose_accel(200, True, 0.8) == 160
    assert mod.choose_accel(250, True, 0.8) == 200
    assert mod.choose_accel(130, True, 0.8) == 100  # rounded DOWN to the quantum, never up toward the edge
    assert mod.choose_accel(300, False, 0.8) == 300  # no edge inside the ladder: keep the top
    assert mod.choose_accel(10, True, 0.5) == 10  # never below one quantum


# ---------------------------------------------------------------- the ini writer
INI = (
    "[GENERAL]\r\n"
    "# motion\r\n"
    "microstepping_default_w = 64\r\n"
    "max_velocity_w_mm = 3.19   \r\n"
    "use_something = True\r\n"
    "\r\n"
    "[CAMERA_CONFIG]\r\n"
    "max_acceleration_w_mm = 999\r\n"
)


def test_ini_update_replaces_in_place_and_appends_missing_keys(mod):
    new, changes = mod.update_ini_text(
        INI,
        {"microstepping_default_w": "8", "max_velocity_w_mm": "6", "max_acceleration_w_mm": "240"},
        "tuned 2026-09-20",
    )
    lines = new.split("\r\n")
    assert "\n" not in new.replace("\r\n", "")  # line endings kept
    assert lines[2] == "microstepping_default_w = 8" and lines[3] == "max_velocity_w_mm = 6"
    assert lines[1] == "# motion" and "use_something = True" in lines  # untouched
    # the missing key lands at the end of [GENERAL], under the comment, before the blank line and the next section
    i = lines.index("max_acceleration_w_mm = 240")
    assert lines[i - 1] == "# tuned 2026-09-20"
    assert i < lines.index("[CAMERA_CONFIG]")
    assert "max_acceleration_w_mm = 999" in lines  # another section's key of the same name is left alone
    assert changes == {
        "microstepping_default_w": ("64", "8"),
        "max_velocity_w_mm": ("3.19", "6"),
        "max_acceleration_w_mm": (None, "240"),
    }


def test_ini_update_is_idempotent_and_needs_a_general_section(mod):
    once, _ = mod.update_ini_text(INI, {"max_acceleration_w_mm": "240"}, "c")
    twice, changes = mod.update_ini_text(once, {"max_acceleration_w_mm": "240"}, "c")
    assert once == twice and changes == {"max_acceleration_w_mm": ("240", "240")}
    with pytest.raises(ValueError):
        mod.update_ini_text("[OTHER]\nx = 1\n", {"a": "1"}, "c")


# ---------------------------------------------------------------- action defaults
def _args(mod, action, **kw):
    a = argparse.Namespace(action=action, microsteps=None, vmax=None, ramp=None, window_deg=0.0)
    for k, v in kw.items():
        setattr(a, k, v)
    return mod.resolve_defaults(a)


def test_action_defaults(mod):
    t = _args(mod, "tune")
    assert (t.microsteps, t.vmax, t.ramp) == (8, 6.0, "sshape")
    v = _args(mod, "verify")
    assert v.microsteps == int(mod._def.MICROSTEPPING_DEFAULT_W) and v.vmax == float(mod._def.MAX_VELOCITY_W_mm)
    assert v.ramp == "sshape"  # what the firmware runs the wheel with; the host never sets the wheel's ramp
    s = _args(mod, "accelsweep")
    assert s.ramp == "trapezoid" and s.microsteps == int(mod._def.MICROSTEPPING_DEFAULT_W)  # historical behaviour
    assert _args(mod, "tune", microsteps=16, vmax=4.0, ramp="trapezoid").microsteps == 16  # explicit wins


# ---------------------------------------------------------------- the tune flow, hardware stubbed
class _Wheel:
    """Stands in for the wheel: a level loses steps when its acceleration exceeds what the 'motor' can do for the
    number of moves asked (the short screen survives more than the endurance, which is the whole point)."""

    def __init__(self, screen_edge, endurance_edge, vmax_ok=None):
        self.screen_edge, self.endurance_edge = screen_edge, endurance_edge
        self.vmax_ok = vmax_ok  # above this top speed the wheel loses steps whatever the acceleration
        self.edge_by_vmax = {}  # top speed -> the acceleration the wheel holds at that speed (both patterns)
        self.homed = 0
        self.levels = []


def _tuner(mod, tmp_path, wheel, **kw):
    a = argparse.Namespace(
        action="tune", out=str(tmp_path), transitions="auto", microsteps=8, vmax=6.0, accel=50.0, ramp="sshape",
        accel_list=[50, 100, 150, 200, 250, 300], pattern=list(mod.DEFAULT_PATTERN), laps=6, margin=0.8,
        max_attempts=4, lost_fullsteps=0.5, slip_fullsteps=2.0, write_ini=False, ini=None, window_deg=0.0,
        plateau_ms=2.0, refine_step=0.0, vmax_fallback=[],
    )  # fmt: skip
    for k, v in kw.items():
        setattr(a, k, v)
    mod.set_microsteps(a.microsteps)
    t = mod.WheelTuner(a)
    t.log = lambda msg: None
    t.set_motion = lambda vmax, accel, ramp: None
    t.home = lambda: setattr(wheel, "homed", wheel.homed + 1)
    t.parked = []
    t.move_to_slot = lambda slot: (t.parked.append(slot), setattr(t, "slot", slot))

    def pattern(label, slots=None, settle_s=0.12):
        n = len(slots or a.pattern)
        edge = wheel.endurance_edge if n > len(a.pattern) else wheel.screen_edge
        edge = wheel.edge_by_vmax.get(a.vmax, edge)
        lost = 0 if a.accel <= edge else 300
        if wheel.vmax_ok is not None and a.vmax > wheel.vmax_ok:
            lost = 300
        ms = max(76.0, 19000.0 / a.accel)  # faster with acceleration until the ramp is register-clamped at 250
        res = {
            "phase": "level", "label": label, "n_moves": n, "drift_usteps": lost,
            "worst_move": {"lost_this_move": lost}, "by_distance_ms_median": {"1": ms}, "adjacent_ms_median": ms,
        }  # fmt: skip
        wheel.levels.append((label, a.accel, n))
        wheel.speeds = getattr(wheel, "speeds", []) + [a.vmax]
        t.summary["results"].append(res)
        return res

    t.pattern = pattern
    return t


def test_tune_backs_off_from_the_edge_and_confirms_with_the_endurance_pattern(mod, tmp_path):
    wheel = _Wheel(screen_edge=200, endurance_edge=200)
    rec = _tuner(mod, tmp_path, wheel).tune()
    assert rec["pass"] and rec["stall_edge_found"]
    assert rec["max_acceleration_w_mm"] == 160  # 0.8 x the highest clean level, 200
    assert rec["microstepping_default_w"] == 8 and rec["max_velocity_w_mm"] == 6.0
    assert rec["moves_confirmed"] == 96
    assert wheel.homed == 1  # re-anchored after the level that lost steps
    assert [lv[1] for lv in wheel.levels] == [50, 100, 150, 200, 250, 160]  # stopped screening at the first failure


def test_a_level_that_passes_the_screen_but_fails_the_endurance_is_stepped_down(mod, tmp_path):
    # the 2026-09-07 bench case: trapezoid a100 passed 16 moves and slipped 6 times in 96
    wheel = _Wheel(screen_edge=200, endurance_edge=130)
    rec = _tuner(mod, tmp_path, wheel).tune()
    assert [e["accel"] for e in rec["endurance"]] == [160, 120]
    assert [e["pass"] for e in rec["endurance"]] == [False, True]
    assert rec["max_acceleration_w_mm"] == 120
    assert wheel.homed == 2


def test_a_step_down_is_always_strictly_lower_even_with_no_margin(mod, tmp_path):
    # bench 2026-09-20: trapezoid, --margin 1.0: a140 passed the screen, lost 320 usteps over 96 moves, and the tool
    # "stepped down" onto a140 again - twice - then accepted it when the third run happened to pass
    assert mod.step_down_accel(140, 1.0) == 130
    assert mod.step_down_accel(140, 0.97) == 130  # rounds back onto 140 without the rule
    assert mod.step_down_accel(140, 0.8) == 110  # the margin still applies when it does go lower
    assert mod.step_down_accel(10, 0.8) == 0  # nothing below one quantum: the loop ends in TUNE FAIL
    wheel = _Wheel(screen_edge=10_000, endurance_edge=125)
    rec = _tuner(mod, tmp_path, wheel, accel_list=[100, 110, 120, 130, 140], margin=1.0).tune()
    assert [e["accel"] for e in rec["endurance"]] == [140, 130, 120]
    assert [e["pass"] for e in rec["endurance"]] == [False, False, True]
    assert rec["max_acceleration_w_mm"] == 120


def test_refine_midpoint_bisects_on_a_grid_and_stops_at_the_step(mod):
    assert mod.refine_midpoint(50, 100, 10) == 70
    assert mod.refine_midpoint(70, 100, 10) == 80
    assert mod.refine_midpoint(80, 100, 10) == 90
    assert mod.refine_midpoint(80, 90, 10) is None  # within one step: done
    assert mod.refine_midpoint(0, 50, 10) == 20  # nothing clean yet: search BELOW the first ladder level
    assert mod.refine_midpoint(0, 20, 10) == 10 and mod.refine_midpoint(0, 10, 10) is None
    assert mod.refine_midpoint(50, 100, 0) is None  # refinement off


def test_the_edge_is_refined_between_the_last_clean_and_the_first_failing_level(mod, tmp_path):
    # bench 2026-09-20 at 600 mA: clean to 86 rev/s2, the 50-wide ladder gave a40; refined it must give 0.8 x 80 = 60
    wheel = _Wheel(screen_edge=86, endurance_edge=86)
    rec = _tuner(mod, tmp_path, wheel, refine_step=10.0).tune()
    assert [lv[1] for lv in wheel.levels][:5] == [50, 100, 70, 80, 90]
    assert rec["stall_edge_between"] == [80, 90] and rec["max_acceleration_w_mm"] == 60
    assert [x["accel"] for x in rec["screened"] if x.get("refine")] == [70, 80, 90]
    assert wheel.homed == 2  # after a100 and after a90


def test_refinement_searches_below_the_ladder_when_its_first_level_already_fails(mod, tmp_path):
    wheel = _Wheel(screen_edge=35, endurance_edge=35)
    rec = _tuner(mod, tmp_path, wheel, refine_step=10.0).tune()
    assert [lv[1] for lv in wheel.levels][:4] == [50, 20, 30, 40]
    assert rec["max_acceleration_w_mm"] == 20  # 0.8 x 30, rounded down to the quantum


def test_fallback_speeds(mod):
    assert mod.fallback_speeds(6.0, [4.5, 3.19]) == [6.0, 4.5, 3.19]
    assert mod.fallback_speeds(4.0, [4.5, 3.19, 3.19]) == [4.0, 3.19]  # only LOWER speeds, each once
    assert mod.fallback_speeds(6.0, []) == [6.0] and mod.fallback_speeds(6.0, None) == [6.0]


def test_the_top_speed_is_reduced_when_no_acceleration_holds(mod, tmp_path):
    # a wheel that loses steps above 4 rev/s whatever the acceleration: 6 fails, 4.5 fails, 3.19 holds
    wheel = _Wheel(screen_edge=200, endurance_edge=200, vmax_ok=4.0)
    rec = _tuner(mod, tmp_path, wheel, refine_step=10.0, vmax_fallback=[4.5, 3.19]).tune()
    assert rec["pass"] and rec["speed_reduced"] and rec["max_velocity_w_mm"] == 3.19
    assert [(x["vmax"], x["pass"]) for x in rec["speeds_tried"]] == [(6.0, False), (4.5, False), (3.19, True)]
    assert rec["max_acceleration_w_mm"] == 160
    assert sorted(set(wheel.speeds), reverse=True) == [6.0, 4.5, 3.19]


def test_with_a_stall_edge_the_lower_speeds_are_searched_and_the_fastest_profile_wins(mod, tmp_path):
    # bench 2026-09-20 at 600 mA: 6 rev/s held a80 (-> a60, 189 ms), 4.5 rev/s held a90 and more (134 ms)
    wheel = _Wheel(screen_edge=86, endurance_edge=86)
    wheel.edge_by_vmax = {6.0: 86, 4.5: 135, 3.19: 100}
    rec = _tuner(mod, tmp_path, wheel, refine_step=10.0, vmax_fallback=[4.5, 3.19]).tune()
    got = [(x["vmax"], x["max_acceleration_w_mm"]) for x in rec["speeds_tried"]]
    assert got == [(6.0, 60), (4.5, 100), (3.19, 80)]
    assert rec["max_velocity_w_mm"] == 4.5 and rec["max_acceleration_w_mm"] == 100 and rec["speed_reduced"]


def test_without_a_stall_edge_the_requested_speed_is_kept_and_nothing_else_is_searched(mod, tmp_path):
    wheel = _Wheel(screen_edge=10_000, endurance_edge=10_000)
    rec = _tuner(mod, tmp_path, wheel, refine_step=10.0, vmax_fallback=[4.5, 3.19]).tune()
    assert set(wheel.speeds) == {6.0} and rec["speed_reduced"] is False and rec["max_velocity_w_mm"] == 6.0


def test_choose_fastest_prefers_the_higher_speed_between_equals(mod):
    r = lambda v, ms: {"max_velocity_w_mm": v, "adjacent_ms_median": ms}  # noqa: E731
    assert mod.choose_fastest([r(6.0, 189.0), r(4.5, 134.0), r(3.19, 150.0)])["max_velocity_w_mm"] == 4.5
    assert mod.choose_fastest([r(6.0, 135.5), r(4.5, 134.0)])["max_velocity_w_mm"] == 6.0  # within 2 ms
    assert mod.choose_fastest([]) is None


def test_the_top_speed_is_reduced_when_every_endurance_attempt_fails(mod, tmp_path):
    class _W(_Wheel):
        pass

    wheel = _W(screen_edge=10_000, endurance_edge=0)  # screens pass, no 96-move run ever holds ...
    t = _tuner(mod, tmp_path, wheel, vmax_fallback=[3.19], max_attempts=2)
    assert t.tune() is None  # ... at any speed: reported as a failure, after trying both
    assert [x["vmax"] for x in t.summary["tune"]["speeds_tried"]] == [6.0, 3.19]
    assert t.summary["tune"]["pass"] is False


def test_without_fallback_speeds_a_failure_is_final(mod, tmp_path):
    wheel = _Wheel(screen_edge=200, endurance_edge=200, vmax_ok=4.0)
    t = _tuner(mod, tmp_path, wheel, vmax_fallback=[])
    assert t.tune() is None and set(wheel.speeds) == {6.0}


def test_no_edge_takes_the_gentlest_level_that_is_as_fast_as_the_best(mod, tmp_path):
    # bench 2026-09-20: 250, 300 and 400 rev/s2 all gave 76 ms (ramp register-clamped); 400 buys nothing over 250
    wheel = _Wheel(screen_edge=10_000, endurance_edge=10_000)
    rec = _tuner(mod, tmp_path, wheel, accel_list=[50, 100, 150, 200, 250, 300, 400]).tune()
    assert rec["max_acceleration_w_mm"] == 250 and rec["stall_edge_found"] is False and wheel.homed == 0


def test_gentlest_as_fast(mod):
    levels = [(50, 210.0), (100, 134.0), (200, 87.0), (250, 76.0), (300, 76.0), (400, 75.0)]
    assert mod.gentlest_as_fast(levels, 2.0) == 250
    assert mod.gentlest_as_fast(levels, 0.0) == 400
    assert mod.gentlest_as_fast(levels[:3], 2.0) == 200  # still getting faster: take the top


def test_tune_reports_failure_when_nothing_is_clean(mod, tmp_path):
    t = _tuner(mod, tmp_path, _Wheel(screen_edge=0, endurance_edge=0))
    assert t.tune() is None and t.summary["tune"]["pass"] is False


def test_write_ini_backs_up_then_updates(mod, tmp_path):
    ini = tmp_path / "configuration_test.ini"
    ini.write_bytes(INI.encode())
    t = _tuner(mod, tmp_path, _Wheel(200, 200), write_ini=True, ini=str(ini))
    rec = t.tune()
    text = ini.read_bytes().decode()
    assert "microstepping_default_w = 8\r\n" in text and "max_acceleration_w_mm = 160\r\n" in text
    backups = list(tmp_path.glob("configuration_test.ini.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes().decode() == INI
    assert t.summary["tune"]["ini"]["changes"]["max_velocity_w_mm"] == ("3.19", "6")
    assert rec["max_acceleration_w_mm"] == 160


def test_verify_passes_and_fails_on_the_endurance_pattern(mod, tmp_path):
    ok = _tuner(mod, tmp_path, _Wheel(200, 200), action="verify", accel=150.0)
    assert ok.verify() is True and ok.summary["verify"]["moves"] == 96
    bad = _tuner(mod, tmp_path, _Wheel(200, 100), action="verify", accel=150.0)
    assert bad.verify() is False


def test_the_pattern_starts_one_slot_before_its_first_target(mod, tmp_path):
    # the encoder check leaves the wheel on slot 2, where the default pattern begins: that first move was a no-op
    t = _tuner(mod, tmp_path, _Wheel(200, 200), action="verify", accel=150.0)
    t.slot = 2
    t.verify()
    assert t.parked == [1]
    t.parked.clear()
    t.a.pattern = [1, 5, 2]
    t.slot = 3
    t.endurance("x")
    assert t.parked == [8]  # one before slot 1 is the last slot, around the circle


# ---------------------------------------------------------------- bench: a REDUCED current to reach the stall edge
def test_the_bench_current_only_ever_reduces(mod):
    assert mod.bench_current_ma(None, 1900) == 1900
    assert mod.bench_current_ma(1200, 1900) == 1200
    assert mod.bench_current_ma(1900, 1900) == 1900
    for bad in (1901, 2500, 0, -5):
        with pytest.raises(ValueError, match="REDUCES"):
            mod.bench_current_ma(bad, 1900)


def test_a_profile_found_at_a_reduced_current_is_never_written_to_the_ini(mod, tmp_path):
    ini = tmp_path / "configuration_test.ini"
    ini.write_bytes(INI.encode())
    t = _tuner(mod, tmp_path, _Wheel(200, 200), write_ini=True, ini=str(ini))
    t.reduced_current, t.current_ma = True, 1200.0
    rec = t.tune()
    assert rec["max_acceleration_w_mm"] == 160 and rec["reduced_current_ma"] == 1200.0
    assert ini.read_bytes().decode() == INI  # untouched, whatever --write-ini said
    assert not list(tmp_path.glob("configuration_test.ini.bak-*"))
    assert "ini" not in t.summary["tune"]


def test_a_current_above_the_machines_is_refused_before_anything_connects(mod):
    """resolve_defaults runs in main() ahead of WheelTuner(): a bad --current-ma never opens the port."""
    machine = float(mod._def.W_MOTOR_RMS_CURRENT_mA)
    with pytest.raises(ValueError, match="REDUCES"):
        _args(mod, "tune", current_ma=machine + 1)
    assert _args(mod, "tune", current_ma=machine / 2).current_ma == machine / 2
    assert _args(mod, "tune").microsteps == 8  # no --current-ma at all is fine
