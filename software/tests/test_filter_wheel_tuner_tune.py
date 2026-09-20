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

    def __init__(self, screen_edge, endurance_edge):
        self.screen_edge, self.endurance_edge = screen_edge, endurance_edge
        self.homed = 0
        self.levels = []


def _tuner(mod, tmp_path, wheel, **kw):
    a = argparse.Namespace(
        action="tune", out=str(tmp_path), transitions="auto", microsteps=8, vmax=6.0, accel=50.0, ramp="sshape",
        accel_list=[50, 100, 150, 200, 250, 300], pattern=list(mod.DEFAULT_PATTERN), laps=6, margin=0.8,
        max_attempts=4, lost_fullsteps=0.5, slip_fullsteps=2.0, write_ini=False, ini=None, window_deg=0.0,
        plateau_ms=2.0,
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
        lost = 0 if a.accel <= edge else 300
        ms = max(76.0, 19000.0 / a.accel)  # faster with acceleration until the ramp is register-clamped at 250
        res = {
            "phase": "level", "label": label, "n_moves": n, "drift_usteps": lost,
            "worst_move": {"lost_this_move": lost}, "by_distance_ms_median": {"1": ms}, "adjacent_ms_median": ms,
        }  # fmt: skip
        wheel.levels.append((label, a.accel, n))
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
