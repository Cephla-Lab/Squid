"""tools/coverslip_flow: the offline parts of the coverslip flow tests - how a port is judged to hold, the sensor
volumes, the bowl fit of a height map - and that the displacement experiment only pulls.

The recordings themselves run inside the Squid GUI through the control server and are not exercised here.
"""

import csv
import importlib.util
import json
import pathlib
import sys

import numpy as np
import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools" / "coverslip_flow"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hold():
    return _load("port_hold_check")


REST = -3.0  # what a flow sensor reads with nothing flowing
RATE_HZ = 16.0
PULL = (18.0, 28.0)  # pull_start, pump_idle
RELEASE = 73.0  # the valve moves to the release port


def _trace(inflow_ul=0.0, spike=False, tail_ul_min=0.0, end=95.0):
    """A sensor trace of a sensor-only check: rest, a 300 uL/min pull, rest, and `inflow_ul` taken in over 3 s
    after the release move (plus an optional reading spike, or flow that has not settled 12-15 s after it)."""
    t = np.arange(0.0, end, 1 / RATE_HZ)
    f = np.full_like(t, REST)
    f[(t >= PULL[0]) & (t < PULL[1])] += 300.0
    pulse = (t >= RELEASE + 0.5) & (t < RELEASE + 3.5)
    f[pulse] += inflow_ul * 60.0 / 3.0
    if spike:
        f[np.argmax(t >= RELEASE + 1)] = 2500.0
    f[(t >= RELEASE + 5) & (t < RELEASE + 20)] += tail_ul_min
    return t, f


@pytest.mark.parametrize(
    "inflow_ul, verdict",
    [(0.0, "ok"), (0.4, "ok"), (0.8, "borderline"), (1.2, "HOLDS"), (3.0, "HOLDS")],
)
def test_sensor_verdict_grades_the_inflow_after_the_release_move(hold, inflow_ul, verdict):
    t, f = _trace(inflow_ul)
    inflow, peak, got = hold.sensor_verdict(t, f, RELEASE, REST)
    assert got == verdict
    assert inflow == pytest.approx(inflow_ul, abs=0.05)
    assert peak == pytest.approx(inflow_ul * 20.0, abs=1e-6)


def test_a_reading_spike_or_flow_that_does_not_settle_is_a_glitch_not_a_hold(hold):
    assert hold.sensor_verdict(*_trace(2.0, spike=True), RELEASE, REST)[2] == "SENSOR GLITCH - repeat"
    assert hold.sensor_verdict(*_trace(0.0, tail_ul_min=40.0), RELEASE, REST)[2] == "SENSOR GLITCH - repeat"


def test_sensor_volume_is_measured_from_the_rest_reading(hold):
    t, f = _trace()
    assert hold._volume(t, f, PULL[0] - 1, PULL[1] + 1, REST) == pytest.approx(50.0, abs=0.5)


@pytest.mark.parametrize("held_um, verdict", [(0.0, "ok"), (-4.9, "ok"), (4.9, "ok"), (-5.1, "HOLDS PRESSURE")])
def test_coverslip_verdict_flags_a_port_left_more_than_5_um_off(hold, held_um, verdict):
    assert hold.coverslip_verdict(held_um) == verdict


def test_analyze_only_reads_sensor_runs_and_writes_the_summary(hold, tmp_path, capsys):
    for run, (port, inflow_ul) in enumerate([(3, 2.5), (7, 0.1)], start=1):
        base = tmp_path / f"{run:02d}_sensor_port{port}_to5"
        t, f = _trace(inflow_ul)
        with open(f"{base}_flow.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t_s", "flow_ul_min"])
            w.writerows(zip(t, f))
        events = [[3.0, "valve_start", port], [PULL[0], "pull_start", 6.0], [PULL[1], "pump_idle", None]]
        events += [[RELEASE, "release_valve_start", 5], [93.0, "end", "completed"]]
        json.dump({"events_s": events, "end_reason": "completed"}, open(f"{base}_meta.json", "w"))

    rows = hold.analyze_sensor_dir(str(tmp_path))

    assert [(r["port"], r["release_port"], r["verdict"]) for r in rows] == [(3, 5, "HOLDS"), (7, 5, "ok")]
    assert rows[0]["pull_volume_ul"] == pytest.approx(50.0, abs=0.5)
    summary = list(csv.DictReader(open(tmp_path / "port_hold_sensor_summary.csv")))
    assert [s["verdict"] for s in summary] == ["HOLDS", "ok"]
    assert "HOLDS" in capsys.readouterr().out


def test_bowl_fit_recovers_the_centre_and_depth_of_a_paraboloid():
    analyze = _load("af_map_analyze")
    x, y = np.meshgrid(np.linspace(-7, 7, 15), np.linspace(-7, 7, 15))
    x, y = x.ravel() + 30.0, y.ravel() + 20.0
    k = 0.2  # um per mm^2
    z = 10.0 + k * ((x - 31.0) ** 2 + (y - 19.5) ** 2)
    z[5] = np.nan  # a failed reading is left out of the fit

    bowl = analyze.fit_bowl(x, y, z, radius=7.0)

    assert (bowl["centre_x_mm"], bowl["centre_y_mm"]) == (31.0, 19.5)
    assert bowl["z_at_centre_um"] == pytest.approx(10.0, abs=0.1)
    assert bowl["depth_at_radius_um"] == pytest.approx(k * 49, abs=0.1)
    assert bowl["rms_residual_um"] == pytest.approx(0.0, abs=0.01)


def test_the_displacement_experiment_has_no_push_back(monkeypatch, capsys):
    # Pulls only: pushing liquid back through the flow cell bows the coverslip outward. The option is gone, so a
    # command line that asks for it is refused before anything reaches the GUI.
    experiment = _load("flow_displacement_experiment")
    monkeypatch.setattr(experiment, "run_remote", lambda params: pytest.fail("reached the GUI"))
    argv = ["flow_displacement_experiment.py", "--port", "1", "--volume", "50", "--rate", "500", "--out", "x"]
    monkeypatch.setattr(sys, "argv", argv + ["--push-back", "50"])
    with pytest.raises(SystemExit) as refused:
        experiment.main()
    assert refused.value.code == 2 and "--push-back" in capsys.readouterr().err


# ---------------------------------------------------------------- review follow-up: fail closed, always clean up
# The recordings are code the control server's python_exec runs inside the GUI. These fakes stand in for the GUI's
# laser AF, microcontroller, piezo and fluidics so that code runs here, the way the server runs it.

import builtins  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

INCONCLUSIVE = "INCONCLUSIVE - repeat"
_REAL_IMPORT = builtins.__import__


def _exec_remote(code, gui, modules=None):
    """Run remote code as python_exec does: restricted builtins and imports, the exec's names in a separate locals
    dict (functions defined in the code see them only through default arguments). Returns those locals.
    modules: stand-ins for allowed modules the code imports (e.g. {"time": JumpClock()})."""
    safe = {
        k: v
        for k, v in vars(builtins).items()
        if k not in ("eval", "exec", "compile", "__import__", "open", "input", "breakpoint")
    }

    def _import(name, *args, **kwargs):
        if name not in ("numpy", "math", "time", "datetime", "json", "os.path"):
            raise ImportError(f"Import of '{name}' not allowed")
        if modules and name in modules:
            return modules[name]
        return _REAL_IMPORT(name, *args, **kwargs)

    safe["__import__"] = _import
    local = {"gui": gui, "np": np, "objective_store": None, "result": None}
    exec(code, {"__builtins__": safe, "np": np}, local)
    return local


class FakeMC:
    def __init__(self, fail_laser_on_ack=False):
        self.log = []
        self.fail_laser_on_ack = fail_laser_on_ack

    def turn_on_AF_laser(self):
        self.log.append("laser on")

    def turn_off_AF_laser(self):
        self.log.append("laser off")

    def wait_till_operation_is_completed(self):
        if self.fail_laser_on_ack and self.log[-1] == "laser on":
            raise TimeoutError("no ack from the MCU")


class FakeFluidics:
    """FluidicsSystem and its manual verbs in one. A job runs synchronously inside run_manual; a verb listed in
    `stuck` leaves the job busy until abort(), a verb listed in `fail` reports on_error, and `on_verb` runs a hook
    after a verb (e.g. a Ctrl-C arriving while it runs). Verbs are keyed "pull" or the valve port number."""

    def __init__(self, stuck=(), fail=(), on_verb=None):
        self.log = []
        self.aborts = 0
        self.busy = False
        self.stuck, self.fail, self.on_verb = stuck, fail, on_verb or {}
        self.manual = self
        self.devices = SimpleNamespace(flow_sensors=[FakeSensor()], syringe_pump=FakePump())
        self._callbacks = {}

    def run_manual(self, fn, callbacks=None):
        if self.busy:
            raise RuntimeError("another job holds the session")
        self._callbacks = callbacks or {}
        self.busy, self._stay = True, False
        try:
            fn()
        except Exception as e:
            self._callbacks.get("on_error", lambda m: None)(str(e))
        if not self._stay:
            self.busy = False

    def abort(self):
        self.aborts += 1
        self.log.append("ABORT")
        if self.busy:
            self.busy = False
            self._callbacks.get("on_stopped", lambda: None)()

    def wait(self, timeout):
        return True

    def _verb(self, entry, key):
        self.log.append(entry)
        if key in self.on_verb:
            self.on_verb[key]()
        if key in self.fail:
            raise RuntimeError(entry + " failed")
        if key in self.stuck:
            self._stay = True

    def empty_to_waste(self):
        self.log.append("empty")

    def open_port(self, port):
        self._verb("valve %d" % port, port)

    def extract(self, port, volume, rate, on_started=None):
        if on_started:
            on_started(0.1)
        self._verb("pull %g" % volume, "pull")

    def held_volume_ul(self, refresh=False):
        return 0.0


class FakeSensor:
    name = "s1"

    def subscribe(self, h):
        pass

    def unsubscribe(self, h):
        pass


class FakePump:
    volume = 5000

    def get_flow_rate(self, code):
        return code

    def flow_rate_to_speed_code(self, rate):
        return rate

    def available_flow_rates(self):
        return []


class FakeLAF:
    def __init__(self, mc=None, centroid=None, pixel_to_um=0.4, piezo=None):
        self.microcontroller = mc or FakeMC()
        self.is_initialized = True
        self.image = None
        self.laser_af_properties = SimpleNamespace(
            has_reference=True,
            x_reference=0.0,
            pixel_to_um=pixel_to_um,
            laser_af_averaging_n=1,
            focus_camera_exposure_time_ms=1.0,
        )
        self._centroid = centroid or (lambda: (1.0, 0.0))
        self.piezo = piezo
        self.stage = SimpleNamespace(move_x_to=lambda x: None, move_y_to=lambda y: None)

    def _get_laser_spot_centroid(self):
        time.sleep(0.002)
        return self._centroid()


def _fake_gui(laf=None, **fluidics):
    gui = SimpleNamespace()
    system = FakeFluidics(**fluidics)
    gui.fluidics = SimpleNamespace(
        system=system, initialized=True, config=SimpleNamespace(syringe_pump=SimpleNamespace(extract_port=1))
    )
    gui.laserAutofocusController = laf or FakeLAF()
    return gui, system


def _ctrl_c(gui, flag):
    """What the client's abort request does to a running recording: it sets the recording's abort flag."""
    return lambda: setattr(gui, flag, True)


@pytest.fixture(scope="module")
def experiment():
    return _load("flow_displacement_experiment")


@pytest.fixture(scope="module")
def amf():
    return _load("af_map_flow")


@pytest.fixture(scope="module")
def valves():
    return _load("valve_sequence")


@pytest.fixture(scope="module")
def piezo():
    return _load("piezo_check")


# ---- finding 5: missing or partial data is never "ok"


def test_coverslip_verdict_without_a_reading_is_inconclusive(hold):
    assert hold.coverslip_verdict(float("nan")) == INCONCLUSIVE


def test_sensor_verdict_with_no_valid_readings_is_inconclusive(hold):
    t = np.arange(RELEASE - 5, RELEASE + 20, 1 / RATE_HZ)
    assert hold.sensor_verdict(t, np.full_like(t, np.nan), RELEASE, REST)[2] == INCONCLUSIVE


def test_sensor_verdict_with_a_fraction_of_the_window_is_inconclusive(hold):
    t = RELEASE + np.linspace(0.0, 0.4, 7)  # 7 readings over 0.4 s of the 15.5 s window
    assert hold.sensor_verdict(t, np.full_like(t, 50.0), RELEASE, REST)[2] == INCONCLUSIVE


def test_sensor_verdict_with_a_gap_over_the_refill_is_inconclusive(hold):
    t, f = _trace(3.0)  # a clear hold, but the sensor dropped out over the refill pulse
    gap = (t > RELEASE) & (t < RELEASE + 4)
    assert hold.sensor_verdict(t[~gap], f[~gap], RELEASE, REST)[2] == INCONCLUSIVE
    f = f.copy()
    f[gap] = np.nan  # the same as readings that came back invalid
    assert hold.sensor_verdict(t, f, RELEASE, REST)[2] == INCONCLUSIVE


def test_sensor_verdict_without_a_rest_reading_is_inconclusive(hold):
    assert hold.sensor_verdict(*_trace(3.0), RELEASE, float("nan"))[2] == INCONCLUSIVE


def test_too_few_readings_after_the_move_are_inconclusive(hold):
    t, f = _trace(2.0, end=RELEASE - 1)
    assert hold.sensor_verdict(t, f, RELEASE, REST)[2] == INCONCLUSIVE


def _write_sensor_run(path, run, port, inflow_ul, end_reason="completed"):
    base = path / f"{run:02d}_sensor_port{port}_to5"
    t, f = _trace(inflow_ul)
    with open(f"{base}_flow.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "flow_ul_min"])
        w.writerows(zip(t, f))
    events = [[3.0, "valve_start", port], [PULL[0], "pull_start", 6.0], [PULL[1], "pump_idle", None]]
    events += [[RELEASE, "release_valve_start", 5], [93.0, "end", end_reason]]
    json.dump({"events_s": events, "end_reason": end_reason}, open(f"{base}_meta.json", "w"))


def test_a_sensor_run_that_did_not_complete_is_inconclusive(hold, tmp_path, capsys):
    _write_sensor_run(tmp_path, 1, 3, 0.0, end_reason="error: pump failed")
    assert [r["verdict"] for r in hold.analyze_sensor_dir(str(tmp_path))] == [INCONCLUSIVE]


def _write_pull_run(path, run, port, z_of_t, end_reason="completed", end=80.0):
    """A coverslip-mode pull: bright-spot track z_of_t(t) (None = no reading), a flat flow trace, its events."""
    base = path / f"{run:02d}_pull50_port{port}"
    t = np.arange(0.0, end, 1 / 30)
    with open(f"{base}_bright.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "bright_spot_um", "displacement_from_baseline_um"])
        for tt in t:
            z = z_of_t(tt)
            w.writerow(["%.4f" % tt, "" if z is None else "%.3f" % z, ""])
    with open(f"{base}.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "flow_s1_ul_min"])
        w.writerows(["%.4f" % tt, REST] for tt in np.arange(0.0, end, 1 / RATE_HZ))
    events = [[0.0, "recording_start", None], [5.0, "valve_start", "port 3"], [6.0, "prep_end", None]]
    events += [[36.0, "pull_start", 6.0], [42.0, "pump_idle", None], [end, "recording_end", end_reason]]
    json.dump({"events_s": events, "end_reason": end_reason}, open(f"{base}_meta.json", "w"))


def test_a_coverslip_run_that_lost_the_spot_at_the_end_is_inconclusive(hold, tmp_path):
    # held_um comes from the last 10 s; with no reading there it must not read as "comes back"
    _write_pull_run(tmp_path, 1, 3, lambda t: None if t > 65 else 0.0)
    assert [r["verdict"] for r in hold.analyze_dir(str(tmp_path))] == [INCONCLUSIVE]


def test_a_coverslip_run_without_a_baseline_before_the_pull_is_inconclusive(hold, tmp_path):
    _write_pull_run(tmp_path, 1, 3, lambda t: None if 20 < t < 36 else 0.0)
    assert [r["verdict"] for r in hold.analyze_dir(str(tmp_path))] == [INCONCLUSIVE]


def test_a_coverslip_run_that_did_not_complete_is_inconclusive(hold, tmp_path):
    _write_pull_run(tmp_path, 1, 3, lambda t: 0.0, end_reason="aborted")
    assert [r["verdict"] for r in hold.analyze_dir(str(tmp_path))] == [INCONCLUSIVE]


def test_a_complete_free_coverslip_run_is_still_ok(hold, tmp_path):
    _write_pull_run(tmp_path, 1, 3, lambda t: -60.0 if 36 <= t < 44 else 0.5)
    assert [r["verdict"] for r in hold.analyze_dir(str(tmp_path))] == ["ok"]


# ---- finding 6: the piezo check judges the spot's movement whatever the calibration's sign


def _piezo_verdict(piezo, monkeypatch, pixel_to_um, xs, step=None):
    reply = {"piezo_start_um": 50.0, "piezo_end_um": 50.0, "pixel_to_um": pixel_to_um, "x_reference": 100.0}
    reply.update(zip(("x0", "x_plus", "x_minus", "x_back"), xs))
    monkeypatch.setattr(piezo, "call", lambda code, port: reply)
    monkeypatch.setattr(sys, "argv", ["piezo_check.py"] + ([] if step is None else ["--step", str(step)]))
    with pytest.raises(SystemExit) as done:
        piezo.main()
    return done.value.code


@pytest.mark.parametrize("pixel_to_um", [0.4, -0.4])
def test_a_dead_piezo_fails_whatever_the_calibration_sign(piezo, monkeypatch, pixel_to_um):
    assert _piezo_verdict(piezo, monkeypatch, pixel_to_um, [100, 100, 100, 100]) not in (0, None)


@pytest.mark.parametrize("pixel_to_um, xs", [(0.4, [100, 125, 75, 100]), (-0.4, [100, 75, 125, 100])])
def test_a_working_piezo_passes_whatever_the_calibration_sign(piezo, monkeypatch, pixel_to_um, xs):
    assert _piezo_verdict(piezo, monkeypatch, pixel_to_um, xs) in (0, None)


def test_the_spot_moving_the_same_way_up_and_down_fails(piezo, monkeypatch):
    assert _piezo_verdict(piezo, monkeypatch, 0.4, [100, 125, 125, 100]) not in (0, None)


@pytest.mark.parametrize("pixel_to_um", [0.0, float("nan"), float("inf")])
def test_a_zero_or_non_finite_calibration_fails(piezo, monkeypatch, pixel_to_um):
    assert _piezo_verdict(piezo, monkeypatch, pixel_to_um, [100, 125, 75, 100]) not in (0, None)


def test_a_zero_step_is_refused(piezo, monkeypatch):
    assert _piezo_verdict(piezo, monkeypatch, 0.4, [100, 100, 100, 100], step=0) not in (0, None)


# ---- finding 7: the AF laser goes on inside the cleanup scope, and goes off whatever else fails


class FakePiezo:
    def __init__(self, fail_from_call=None):
        self.position = 50.0
        self.calls = 0
        self.fail_from_call = fail_from_call

    def move_relative(self, d):
        self.calls += 1
        if self.fail_from_call is not None and self.calls >= self.fail_from_call:
            raise RuntimeError("piezo did not answer")
        self.position += d


def test_piezo_check_turns_the_laser_off_when_the_laser_on_ack_times_out(piezo):
    mc = FakeMC(fail_laser_on_ack=True)
    gui, _ = _fake_gui(FakeLAF(mc=mc, piezo=FakePiezo()))
    with pytest.raises(TimeoutError):
        _exec_remote(piezo.CODE.replace("__STEP__", "10.0"), gui)
    assert mc.log[-1] == "laser off"


def test_piezo_check_turns_the_laser_off_when_restoring_the_piezo_fails(piezo):
    mc = FakeMC()
    gui, _ = _fake_gui(FakeLAF(mc=mc, piezo=FakePiezo(fail_from_call=2)))
    with pytest.raises(RuntimeError):
        _exec_remote(piezo.CODE.replace("__STEP__", "10.0"), gui)
    assert mc.log[-1] == "laser off"


def _experiment_params(**kw):
    p = dict(
        dry_run=0,
        port=3,
        volume=50,
        rate=500,
        pre=0.05,
        baseline=0.05,
        post=0.1,
        interval=0,
        empty_first=False,
        prefill=0,
        prefill_rate=1000,
        empty_if_needed=False,
        allow_no_flow_sensor=True,
        af_path=None,
        af_every=1,
        hard_cap=10,
        af_max_frames=10,
    )
    p.update(kw)
    return p


def test_experiment_turns_the_laser_off_when_the_laser_on_ack_times_out(experiment):
    mc = FakeMC(fail_laser_on_ack=True)
    gui, system = _fake_gui(FakeLAF(mc=mc))
    ns = _exec_remote(experiment.REMOTE_CODE.replace("__PARAMS__", repr(_experiment_params())), gui)
    assert mc.log[-1] == "laser off"
    assert ns["result"]["end_reason"].startswith("error")
    assert system.log == []  # nothing moved


def _map_params(**kw):
    p = dict(
        center=(0, 0),
        positions=[(1, 1), (2, 2)],
        revisit=0,
        move_settle=0,
        settle=0.3,
        n_before=1,
        map_only=False,
        port=3,
        volume=50,
        rate=500,
        post=0.1,
        af_path=None,
        max_frames=10,
    )
    p.update(kw)
    return p


def test_map_turns_the_laser_off_when_the_laser_on_ack_times_out(amf):
    mc = FakeMC(fail_laser_on_ack=True)
    gui, system = _fake_gui(FakeLAF(mc=mc))
    ns = _exec_remote(amf.REMOTE.replace("__PARAMS__", repr(_map_params())), gui)
    assert mc.log[-1] == "laser off" and ns["result"]["end_reason"].startswith("error")
    assert system.log == []


def test_valve_sequence_turns_the_laser_off_when_the_laser_on_ack_times_out(valves):
    mc = FakeMC(fail_laser_on_ack=True)
    gui, system = _fake_gui(FakeLAF(mc=mc))
    params = dict(steps=[[1, 0.1]], pre=0.1, af_path=None, max_frames=10)
    ns = _exec_remote(valves.REMOTE.replace("__PARAMS__", repr(params)), gui)
    assert mc.log[-1] == "laser off" and ns["result"]["end_reason"].startswith("error")
    assert system.log == []


# ---- finding 1: any exceptional end of a pull stops the pump


def test_an_exception_during_the_pull_aborts_the_pump_and_keeps_the_data(experiment):
    holder = {}

    def centroid():
        if holder["system"].busy:
            raise RuntimeError("focus camera gone")
        return (1.0, 0.0)

    gui, system = _fake_gui(FakeLAF(centroid=centroid), stuck=("pull",))
    holder["system"] = system
    ns = _exec_remote(experiment.REMOTE_CODE.replace("__PARAMS__", repr(_experiment_params())), gui)
    assert system.aborts == 1 and not system.busy
    assert ns["result"]["end_reason"] == "error: focus camera gone"
    assert ns["result"]["displacement"]  # what was recorded before it comes back
    assert getattr(gui, "_flow_displacement_running") is False


# ---- finding 2: a Ctrl-C stops the map and the valve sequence, and stops a job they started


def test_map_abort_during_the_settle_does_not_launch_the_pull(amf):
    n = {"k": 0}
    holder = {}

    def centroid():
        n["k"] += 1
        if n["k"] == 3:
            setattr(holder["gui"], "_af_map_abort", True)
        return (1.0, 0.0)

    gui, system = _fake_gui(FakeLAF(centroid=centroid))
    holder["gui"] = gui
    ns = _exec_remote(amf.REMOTE.replace("__PARAMS__", repr(_map_params())), gui)
    assert system.log == ["empty", "valve 3"]
    assert ns["result"]["end_reason"] == "aborted"


def test_map_abort_during_a_fluidics_job_stops_it_and_the_sequence(amf):
    gui, system = _fake_gui(stuck=(3,))  # the preparation's valve move is still running at the Ctrl-C
    system.on_verb = {3: _ctrl_c(gui, "_af_map_abort")}
    ns = _exec_remote(amf.REMOTE.replace("__PARAMS__", repr(_map_params())), gui)
    assert system.log == ["empty", "valve 3", "ABORT"]  # stopped, and no pull after it
    assert ns["result"]["end_reason"] == "aborted"
    assert not [s for s in ns["result"]["samples"] if s[1].startswith("before")]


def test_valve_sequence_abort_stops_the_remaining_moves(valves):
    n = {"k": 0}
    holder = {}

    def centroid():
        n["k"] += 1
        if n["k"] == 3:
            setattr(holder["gui"], "_af_map_abort", True)
        return (1.0, 0.0)

    gui, system = _fake_gui(FakeLAF(centroid=centroid))
    holder["gui"] = gui
    params = dict(steps=[[1, 0.2], [5, 0.2], [7, 0.2]], pre=0.3, af_path=None, max_frames=10)
    ns = _exec_remote(valves.REMOTE.replace("__PARAMS__", repr(params)), gui)
    assert system.log == []
    assert ns["result"]["end_reason"] == "aborted"


def test_valve_sequence_abort_during_a_move_stops_it(valves):
    gui, system = _fake_gui(stuck=(1,))
    system.on_verb = {1: _ctrl_c(gui, "_af_map_abort")}
    params = dict(steps=[[1, 0.2], [5, 0.2]], pre=0.05, af_path=None, max_frames=10)
    ns = _exec_remote(valves.REMOTE.replace("__PARAMS__", repr(params)), gui)
    assert system.log == ["valve 1", "ABORT"]
    assert ns["result"]["end_reason"] == "aborted"


# ---- finding 4: after a pull the valve always goes to the release port


def _sensor_params(**kw):
    p = dict(port=3, release_port=5, volume=50, rate=500, pre=0.05, baseline=0.05, post=0.05, hold=0.05)
    p.update(kw)
    return p


def _sensor_remote(hold, gui):
    return _exec_remote(hold.SENSOR_REMOTE.replace("__PARAMS__", repr(_sensor_params())), gui)["result"]


def test_sensor_run_releases_after_a_completed_pull(hold):
    gui, system = _fake_gui()
    res = _sensor_remote(hold, gui)
    assert system.log == ["empty", "valve 3", "pull 50", "valve 5"]
    assert res["end_reason"] == "completed" and res["released"] is True


def test_sensor_run_releases_after_a_pump_error(hold):
    gui, system = _fake_gui(fail=("pull",))
    res = _sensor_remote(hold, gui)
    assert system.log[-1] == "valve 5"
    assert res["end_reason"].startswith("error") and res["released"] is True


def test_sensor_run_releases_after_a_ctrl_c_during_the_pull(hold):
    gui, system = _fake_gui(stuck=("pull",))
    system.on_verb = {"pull": _ctrl_c(gui, "_flow_displacement_abort")}
    res = _sensor_remote(hold, gui)
    assert system.log == ["empty", "valve 3", "pull 50", "ABORT", "valve 5"]
    assert res["end_reason"] == "aborted" and res["released"] is True


def test_sensor_run_reports_a_release_that_failed(hold):
    gui, system = _fake_gui(fail=(5,))
    res = _sensor_remote(hold, gui)
    assert res["released"] is False and "valve 5 failed" in res["release_error"]


def test_release_remote_stops_a_pull_still_running_and_moves_the_valve_after_it(hold):
    gui, system = _fake_gui(stuck=("pull",))
    system.run_manual(lambda: system.extract(1, 50, 500))  # a pull recording whose client is gone, pump running
    gui._flow_displacement_running = True

    def pull_recording():  # what that recording does on its abort flag: stop its pump, then end
        while not getattr(gui, "_flow_displacement_abort", False):
            time.sleep(0.01)
        time.sleep(0.1)
        system.abort()
        gui._flow_displacement_running = False

    threading.Thread(target=pull_recording, daemon=True).start()
    ns = _exec_remote(hold.RELEASE_REMOTE.replace("__PARAMS__", repr({"port": 5, "timeout": 5})), gui)
    assert system.log == ["pull 50", "ABORT", "valve 5"]  # the valve moves only once the pull has stopped
    assert ns["result"]["released"] is True


def test_release_remote_never_aborts_a_job_that_is_not_the_checks(hold):
    gui, system = _fake_gui(stuck=(9,))
    system.run_manual(lambda: system.open_port(9))  # e.g. a fluidics protocol (the pull was refused: busy)
    with pytest.raises(RuntimeError, match="still busy"):
        _exec_remote(hold.RELEASE_REMOTE.replace("__PARAMS__", repr({"port": 5, "timeout": 0.3})), gui)
    assert system.log == ["valve 9"] and system.busy


def test_release_remote_reports_a_failed_move(hold):
    gui, system = _fake_gui(fail=(5,))
    ns = _exec_remote(hold.RELEASE_REMOTE.replace("__PARAMS__", repr({"port": 5, "timeout": 5})), gui)
    assert ns["result"]["released"] is False and "valve 5 failed" in ns["result"]["error"]


def _suite_args(tmp_path, **kw):
    a = SimpleNamespace(
        out_dir=str(tmp_path),
        ports=[3],
        release_port=5,
        volume=50,
        rate=500,
        post=45,
        hold=30,
        order="given",
        seed=0,
        server_port=5050,
        sensor_only=False,
    )
    vars(a).update(kw)
    return a


def _fake_children(monkeypatch, hold, pull, release_rc=0, bare_ok=True):
    """Replace the two recording children and the bare release; returns the call log."""
    calls = []

    def run_child(cmd, log_path, shield=False):
        script = os.path.basename(cmd[1])
        calls.append(script)
        if script == "flow_displacement_experiment.py":
            return pull()
        return release_rc, "End: completed"

    def bare_release(a):
        calls.append("bare release")
        return bare_ok, "" if bare_ok else "valve 5 failed"

    monkeypatch.setattr(hold, "_run_child", run_child)
    monkeypatch.setattr(hold, "_bare_release", bare_release)
    monkeypatch.setattr(hold, "analyze_dir", lambda d: [])
    return calls


def test_suite_releases_after_a_failed_pull(hold, tmp_path, monkeypatch):
    calls = _fake_children(monkeypatch, hold, lambda: (1, "End: pump error: x"))
    with pytest.raises(SystemExit) as stopped:
        hold.run_suite(_suite_args(tmp_path))
    assert calls == ["flow_displacement_experiment.py", "bare release"]
    assert "pull on port 3" in str(stopped.value.code)


def test_suite_releases_after_a_ctrl_c_during_the_pull(hold, tmp_path, monkeypatch):
    def pull():
        raise KeyboardInterrupt

    calls = _fake_children(monkeypatch, hold, pull)
    with pytest.raises(KeyboardInterrupt):
        hold.run_suite(_suite_args(tmp_path))
    assert calls == ["flow_displacement_experiment.py", "bare release"]


def test_suite_falls_back_to_the_bare_release_when_the_recorded_one_fails(hold, tmp_path, monkeypatch):
    calls = _fake_children(monkeypatch, hold, lambda: (0, "End: completed"), release_rc=1)
    hold.run_suite(_suite_args(tmp_path))
    assert calls == ["flow_displacement_experiment.py", "valve_sequence.py", "bare release"]


def test_suite_reports_a_release_that_failed(hold, tmp_path, monkeypatch, capsys):
    calls = _fake_children(monkeypatch, hold, lambda: (1, "End: error"), bare_ok=False)
    with pytest.raises(SystemExit) as stopped:
        hold.run_suite(_suite_args(tmp_path))
    assert calls == ["flow_displacement_experiment.py", "bare release"]
    assert "RELEASE FAILED" in str(stopped.value.code) + capsys.readouterr().err


def test_suite_finishes_the_release_before_honouring_a_ctrl_c(hold, tmp_path, monkeypatch):
    calls = _fake_children(monkeypatch, hold, lambda: (0, "End: completed"))
    inner = hold._run_child

    def release_with_ctrl_c(cmd, log_path, shield=False):
        if "valve_sequence.py" in cmd[1]:
            os.kill(os.getpid(), signal.SIGINT)  # Ctrl-C while the release runs
            time.sleep(0.05)
        return inner(cmd, log_path, shield)

    monkeypatch.setattr(hold, "_run_child", release_with_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        hold.run_suite(_suite_args(tmp_path, ports=[3, 4]))
    assert calls == ["flow_displacement_experiment.py", "valve_sequence.py"]  # port 4 never starts


def test_run_child_waits_for_the_child_after_ctrl_c_instead_of_killing_it(hold, tmp_path, monkeypatch):
    done = tmp_path / "child_finished"
    code = "import time, pathlib; time.sleep(0.5); pathlib.Path(%r).write_text('saved')" % str(done)
    real_wait = subprocess.Popen.wait
    hits = {"n": 0}

    def wait(self, timeout=None):
        if timeout is None and hits["n"] == 0:
            hits["n"] += 1
            raise KeyboardInterrupt  # Ctrl-C reaches the parent while the child runs
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait)
    with pytest.raises(KeyboardInterrupt):
        hold._run_child([sys.executable, "-c", code], str(tmp_path / "child_log.txt"))
    assert done.read_text() == "saved"


# ---- finding 3: Ctrl-C in the sensor-only client stops the run, waits for its cleanup and keeps the data


def test_sensor_client_ctrl_c_sends_the_abort_and_saves_what_came_back(hold, tmp_path, monkeypatch):
    sent = []
    partial = {
        "t0": 100.0,
        "events": [[101.0, "prep_start", None], [104.0, "release_valve_start", 5], [104.5, "end", "aborted"]],
        "flow": [[100.5, -3.0], [101.0, -3.1]],
        "end_reason": "aborted",
        "sensor": "s1",
        "held_after_ul": 0.0,
        "valve_moved": True,
        "released": True,
        "release_error": "",
    }
    replies = {"run": [KeyboardInterrupt, {"success": True, "result": {"result": partial}}]}

    def send(command, params, timeout):
        sent.append(params["code"])
        return "run" if len(sent) == 1 else "abort"

    def receive(sock):
        if sock == "abort":
            return {"success": True, "result": {"result": "abort requested"}}
        r = replies["run"].pop(0)
        if r is KeyboardInterrupt:
            raise KeyboardInterrupt
        return r

    monkeypatch.setattr(hold.fde, "_send", send)
    monkeypatch.setattr(hold.fde, "_receive", receive)
    base = str(tmp_path / "01_sensor_port3_to5")
    outcome = hold.run_sensor_port(_suite_args(tmp_path, sensor_only=True), 3, base)
    assert len(sent) == 2 and "_flow_displacement_abort" in sent[1] and "True" in sent[1]
    assert outcome["interrupted"] and outcome["released"]
    assert json.load(open(base + "_meta.json"))["end_reason"] == "aborted"
    assert len(list(csv.DictReader(open(base + "_flow.csv")))) == 2


def test_sensor_suite_stops_loudly_when_the_release_failed(hold, tmp_path, monkeypatch, capsys):
    outcome = {"end_reason": "error: x", "released": False, "release_error": "valve 5 failed", "interrupted": False}
    monkeypatch.setattr(hold, "run_sensor_port", lambda a, p, base: outcome)
    monkeypatch.setattr(hold, "analyze_sensor_dir", lambda d: [])
    with pytest.raises(SystemExit) as stopped:
        hold.run_sensor_suite(_suite_args(tmp_path, sensor_only=True), str(tmp_path), [3, 4])
    assert "RELEASE FAILED" in str(stopped.value.code) + capsys.readouterr().err


# ---- finding 8: the output folder is made (and checked) before anything reaches the GUI


def _minimal_result(end_reason="completed"):
    return {
        "t0": 0.0,
        "displacement": [],
        "flow": [],
        "events": [[0.0, "recording_end", end_reason]],
        "end_reason": end_reason,
        "af_frames": 0,
        "sensors": [],
        "objective": "",
        "syringe_ul": 5000,
        "laser_af": {"x_reference": 0.0, "pixel_to_um": 0.4},
    }


def test_experiment_makes_the_output_folder_before_the_run(experiment, tmp_path, monkeypatch):
    out = tmp_path / "new" / "deeper" / "run1"

    def run_remote(params):
        assert out.parent.is_dir()
        return _minimal_result(), False

    monkeypatch.setattr(experiment, "run_remote", run_remote)
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run", "1", "--no-plot", "--out", str(out)])
    experiment.main()
    assert (tmp_path / "new" / "deeper" / "run1.csv").exists()


def test_experiment_exits_non_zero_when_the_run_did_not_complete(experiment, tmp_path, monkeypatch):
    out = tmp_path / "run1"
    monkeypatch.setattr(experiment, "run_remote", lambda params: (_minimal_result("pump error: x"), False))
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run", "1", "--no-plot", "--out", str(out)])
    with pytest.raises(SystemExit) as ended:
        experiment.main()
    assert ended.value.code not in (0, None)
    assert (tmp_path / "run1_meta.json").exists()  # what was recorded is still saved


def test_experiment_refuses_an_output_folder_it_cannot_create(experiment, tmp_path, monkeypatch):
    blocker = tmp_path / "a_file"
    blocker.write_text("")
    monkeypatch.setattr(experiment, "run_remote", lambda params: pytest.fail("reached the GUI"))
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run", "1", "--out", str(blocker / "run1")])
    with pytest.raises(SystemExit):
        experiment.main()


def _sequence_result(end_reason="completed"):
    return {
        "t0": 0.0,
        "samples": [],
        "events": [],
        "flow": [],
        "end_reason": end_reason,
        "af_frames": 0,
        "flow_sensor": "s1",
        "flow_sensors": ["s1"],
        "laser_af": {"x_reference": 0.0, "pixel_to_um": 0.4, "detection": {}},
    }


def test_valve_sequence_makes_the_output_folder_and_exits_non_zero_on_error(valves, tmp_path, monkeypatch):
    out = tmp_path / "new" / "valve"

    def run(params, timeout, remote=None):
        assert out.parent.is_dir()
        return _sequence_result("error: x")

    monkeypatch.setattr(valves.amf, "run", run)
    monkeypatch.setattr(sys, "argv", ["x", "--steps", "5:1", "--out", str(out)])
    with pytest.raises(SystemExit) as ended:
        valves.main()
    assert ended.value.code not in (0, None)
    assert (tmp_path / "new" / "valve_meta.json").exists()


def test_map_makes_the_output_folder_before_the_run(amf, tmp_path, monkeypatch):
    positions = tmp_path / "coordinates.csv"
    positions.write_text("x (mm),y (mm)\n1,1\n2,2\n")
    out = tmp_path / "new" / "m1"

    def run(params, timeout, remote=None):
        assert out.parent.is_dir()
        return _sequence_result()

    monkeypatch.setattr(amf, "run", run)
    monkeypatch.setattr(sys, "argv", ["x", "--map-only", "--positions", str(positions), "--out", str(out)])
    amf.main()
    assert (tmp_path / "new" / "m1_meta.json").exists()


# ---------------------------------------------------------------- CodeRabbit follow-up
# The map analysis finds the passes a run recorded; the bowl centre is only reported when the map defines it; the map
# and valve recordings keep one named flow sensor; and no recording overwrites the AF frames of an earlier one.


@pytest.fixture(scope="module")
def analyze():
    return _load("af_map_analyze")


_GRID = [(x, y) for x in np.linspace(-5, 5, 6) for y in np.linspace(-5, 5, 6)]


def _bowl(x, y):
    return 0.5 * ((x - 1) ** 2 + (y + 2) ** 2)


def _write_map_run(base, phases, grid=_GRID):
    """A recorded map run: each (phase, offset_um) is a pass over `grid` with a centre revisit after it, after a hold
    at the centre ("settle"); the heights are a bowl plus the pass's offset."""
    with open(f"{base}_samples.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "phase", "pos_index", "x_mm", "y_mm", "gui_spot_um", "bright_spot_um", "af_frame"])
        t = 0
        w.writerow([t, "settle", -1, 0, 0, "", 0.0, ""])
        for phase, offset in phases:
            for i, (x, y) in enumerate(grid):
                t += 1
                w.writerow([t, phase, i, x, y, "", _bowl(x, y) + offset, ""])
            t += 1
            w.writerow([t, phase + "_center", -1, 0, 0, "", offset, ""])
    json.dump({"events_s": [[0, "end", "completed"]]}, open(f"{base}_meta.json", "w"))


def _analyze_run(analyze, base, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["af_map_analyze.py", str(base)])
    analyze.main()
    return json.load(open(f"{base}_map_summary.json"))


def test_a_map_only_run_is_analysed_and_its_noise_is_map2_minus_map1(analyze, tmp_path, monkeypatch):
    base = tmp_path / "maponly"
    _write_map_run(base, [("map1", 0.0), ("map2", 0.3)])

    summary = _analyze_run(analyze, base, monkeypatch)

    assert list(summary) == ["map1", "map2", "noise: map2 - map1"]
    assert summary["noise: map2 - map1"]["median"] == pytest.approx(0.3)
    assert summary["noise: map2 - map1"]["passes"] == ["map2", "map1"]
    assert (tmp_path / "maponly_maps.png").exists()


def test_after_is_compared_with_the_last_before_pass(analyze, tmp_path, monkeypatch):
    base = tmp_path / "b3"
    _write_map_run(base, [("before1", 0.0), ("before2", 0.2), ("before3", 5.0), ("after", 5.0)])

    summary = _analyze_run(analyze, base, monkeypatch)

    assert list(summary) == ["before1", "before2", "before3", "after", "noise: before2 - before1", "after - before"]
    assert summary["after - before"]["passes"] == ["after", "before3"]
    assert summary["after - before"]["median"] == pytest.approx(0.0)
    assert summary["noise: before2 - before1"]["median"] == pytest.approx(0.2)


def test_a_one_line_map_has_no_bowl_centre(analyze):
    x = np.linspace(-5, 5, 12)
    y = np.full_like(x, 3.0)

    bowl = analyze.fit_bowl(x, y, _bowl(x, y), radius=7.0)

    assert (bowl["centre_x_mm"], bowl["centre_y_mm"], bowl["z_at_centre_um"]) == (None, None, None)
    assert bowl["k_um_per_mm2"] == pytest.approx(0.5)  # the curvature along the line is still measured
    assert bowl["depth_at_radius_um"] == pytest.approx(24.5)


@pytest.mark.parametrize("noise_um", [0.0, 0.05, 1.0])
def test_a_flat_map_has_no_bowl_centre(analyze, noise_um):
    x = np.array([p[0] for p in _GRID])
    y = np.array([p[1] for p in _GRID])
    z = 10.0 + noise_um * np.random.default_rng(0).standard_normal(len(x))

    bowl = analyze.fit_bowl(x, y, z, radius=7.0)

    assert (bowl["centre_x_mm"], bowl["centre_y_mm"], bowl["z_at_centre_um"]) == (None, None, None)
    assert abs(bowl["depth_at_radius_um"]) < 3 * noise_um + 0.1


def test_a_noisy_bowl_still_has_its_centre(analyze):
    x = np.array([p[0] for p in _GRID])
    y = np.array([p[1] for p in _GRID])
    z = _bowl(x, y) + 0.2 * np.random.default_rng(1).standard_normal(len(x))

    bowl = analyze.fit_bowl(x, y, z, radius=7.0)

    assert bowl["centre_x_mm"] == pytest.approx(1.0, abs=0.2)
    assert bowl["centre_y_mm"] == pytest.approx(-2.0, abs=0.2)


class ReadingSensor(FakeSensor):
    """A flow sensor that delivers one reading, `value`, as soon as it is subscribed."""

    def __init__(self, name, value):
        self.name, self.value, self.subscribed = name, value, []

    def subscribe(self, h):
        self.subscribed.append(h)
        h(self.value, 1.0)

    def unsubscribe(self, h):
        self.subscribed.remove(h)


def _two_sensors(system):
    sensors = [ReadingSensor("s1", 10.0), ReadingSensor("s2", 99.0)]
    system.devices.flow_sensors = sensors
    return sensors


def test_the_map_records_the_first_flow_sensor_only(amf):
    gui, system = _fake_gui()
    sensors = _two_sensors(system)
    ns = _exec_remote(amf.REMOTE.replace("__PARAMS__", repr(_map_params(settle=0.05, post=0.05))), gui)
    result = ns["result"]
    assert result["end_reason"] == "completed"
    assert [f for _t, f in result["flow"]] == [10.0]
    assert (result["flow_sensor"], result["flow_sensors"]) == ("s1", ["s1", "s2"])
    assert [s.subscribed for s in sensors] == [[], []]  # unsubscribed again


def test_the_valve_sequence_records_the_first_flow_sensor_only(valves):
    gui, system = _fake_gui()
    sensors = _two_sensors(system)
    params = dict(steps=[[1, 0.05]], pre=0.05, af_path=None, max_frames=10)
    result = _exec_remote(valves.REMOTE.replace("__PARAMS__", repr(params)), gui)["result"]
    assert result["end_reason"] == "completed"
    assert [f for _t, f in result["flow"]] == [10.0]
    assert (result["flow_sensor"], result["flow_sensors"]) == ("s1", ["s1", "s2"])
    assert [s.subscribed for s in sensors] == [[], []]


def test_the_valve_sequence_runs_without_a_flow_sensor(valves):
    gui, system = _fake_gui()
    system.devices.flow_sensors = []
    params = dict(steps=[[1, 0.05]], pre=0.05, af_path=None, max_frames=10)
    result = _exec_remote(valves.REMOTE.replace("__PARAMS__", repr(params)), gui)["result"]
    assert result["end_reason"] == "completed"
    assert (result["flow"], result["flow_sensor"], result["flow_sensors"]) == ([], None, [])


def test_the_map_and_valve_sequence_name_the_recorded_sensor(amf, valves, tmp_path, monkeypatch, capsys):
    positions = tmp_path / "coordinates.csv"
    positions.write_text("x (mm),y (mm)\n1,1\n2,2\n")
    two = dict(_sequence_result(), flow=[[0.5, 10.0]], flow_sensor="s1", flow_sensors=["s1", "s2"])
    for module in (amf, valves.amf):  # valve_sequence.py imports its own af_map_flow
        monkeypatch.setattr(module, "run", lambda params, timeout, remote=None: two)

    monkeypatch.setattr(sys, "argv", ["x", "--map-only", "--positions", str(positions), "--out", str(tmp_path / "m")])
    amf.main()
    monkeypatch.setattr(sys, "argv", ["x", "--steps", "5:1", "--out", str(tmp_path / "v")])
    valves.main()

    for base in ("m", "v"):
        assert json.load(open(tmp_path / f"{base}_meta.json"))["flow_sensor"] == "s1"
        rows = list(csv.DictReader(open(tmp_path / f"{base}_flow.csv")))
        assert [(r["t_s"], r["flow_ul_min"]) for r in rows] == [("0.5000", "10.0")]
    assert capsys.readouterr().out.count("flow sensor s1 only (s2 not recorded)") == 2


@pytest.mark.parametrize("taken", ["_samples.csv", "_af_raw.npy"])
def test_the_map_refuses_an_output_whose_files_exist(amf, tmp_path, monkeypatch, taken):
    positions = tmp_path / "coordinates.csv"
    positions.write_text("x (mm),y (mm)\n1,1\n")
    (tmp_path / f"m1{taken}").write_text("an earlier run")
    monkeypatch.setattr(amf, "run", lambda *a, **k: pytest.fail("reached the GUI"))
    monkeypatch.setattr(sys, "argv", ["x", "--map-only", "--positions", str(positions), "--out", str(tmp_path / "m1")])
    with pytest.raises(SystemExit):
        amf.main()
    assert (tmp_path / f"m1{taken}").read_text() == "an earlier run"


@pytest.mark.parametrize("taken", ["_samples.csv", "_af_raw.npy"])
def test_the_valve_sequence_refuses_an_output_whose_files_exist(valves, tmp_path, monkeypatch, taken):
    (tmp_path / f"v{taken}").write_text("an earlier run")
    monkeypatch.setattr(valves.amf, "run", lambda *a, **k: pytest.fail("reached the GUI"))
    monkeypatch.setattr(sys, "argv", ["x", "--steps", "5:1", "--out", str(tmp_path / "v")])
    with pytest.raises(SystemExit):
        valves.main()
    assert (tmp_path / f"v{taken}").read_text() == "an earlier run"


# ---------------------------------------------------------------- CodeRabbit round 2
# The valve still goes to the release port when the release recorder cannot start or the output is gone; a Ctrl-C is
# honoured before a fluidics job is dispatched; and a verdict without its data is INCONCLUSIVE.


def test_the_bare_release_runs_when_the_release_recorder_cannot_start(hold, tmp_path, monkeypatch, capsys):
    # e.g. the pull filled the disk and the release recording cannot open its log; the valve move needs no disk
    calls = _fake_children(monkeypatch, hold, lambda: (0, "End: completed"))

    def run_child(cmd, log_path, shield=False):
        calls.append(os.path.basename(cmd[1]))
        if "valve_sequence.py" in cmd[1]:
            raise OSError(28, "No space left on device")
        return 0, "End: completed"

    monkeypatch.setattr(hold, "_run_child", run_child)
    hold.run_suite(_suite_args(tmp_path))
    assert calls == ["flow_displacement_experiment.py", "valve_sequence.py", "bare release"]
    assert "No space left on device" in capsys.readouterr().err


class BrokenOutput:
    """stdout or stderr once the `| tee` it wrote to has gone (the same Ctrl-C stops tee)."""

    def write(self, s):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")


@pytest.mark.parametrize(
    "pull_rc, release_rc, after_pull",
    [(0, 0, ["valve_sequence.py"]), (0, 1, ["valve_sequence.py", "bare release"]), (1, 0, ["bare release"])],
)
def test_a_broken_output_does_not_stop_the_release(hold, tmp_path, monkeypatch, pull_rc, release_rc, after_pull):
    def pull():  # the output goes away while the pull runs
        monkeypatch.setattr(sys, "stdout", BrokenOutput())
        monkeypatch.setattr(sys, "stderr", BrokenOutput())
        return pull_rc, "End: completed" if pull_rc == 0 else "End: error"

    calls = _fake_children(monkeypatch, hold, pull, release_rc=release_rc)
    try:
        hold.run_suite(_suite_args(tmp_path))
    except SystemExit:  # a failed pull stops the check, after the release
        assert pull_rc != 0
    assert calls == ["flow_displacement_experiment.py"] + after_pull


class CtrlCOnFirstWrite:
    """stdout where a Ctrl-C arrives while the first line after the pull is written."""

    def __init__(self):
        self.hit = False

    def write(self, s):
        if not self.hit:
            self.hit = True
            raise KeyboardInterrupt
        return len(s)

    def flush(self):
        pass


def test_a_ctrl_c_while_the_pull_status_prints_still_releases(hold, tmp_path, monkeypatch):
    def pull():
        monkeypatch.setattr(sys, "stdout", CtrlCOnFirstWrite())
        return 0, "End: completed"

    calls = _fake_children(monkeypatch, hold, pull)
    with pytest.raises(KeyboardInterrupt):
        hold.run_suite(_suite_args(tmp_path, ports=[3, 4]))
    assert calls == ["flow_displacement_experiment.py", "valve_sequence.py"]  # released; port 4 never starts


def test_a_ctrl_c_during_the_release_with_a_broken_output_still_finishes_it(hold, tmp_path, monkeypatch):
    calls = _fake_children(monkeypatch, hold, lambda: (0, "End: completed"))
    inner = hold._run_child

    def release_with_ctrl_c(cmd, log_path, shield=False):
        if "valve_sequence.py" in cmd[1]:
            monkeypatch.setattr(sys, "stderr", BrokenOutput())  # the Ctrl-C note cannot be printed
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)
        return inner(cmd, log_path, shield)

    monkeypatch.setattr(hold, "_run_child", release_with_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        hold.run_suite(_suite_args(tmp_path, ports=[3, 4]))
    assert calls == ["flow_displacement_experiment.py", "valve_sequence.py"]  # the recorded release, not a fallback


class JumpClock:
    """`time` for remote code, on a clock the test can move forward: a hold's deadline then passes at once.
    on_sleep(seconds), if set, runs at each sleep the code makes."""

    def __init__(self, on_sleep=None):
        self.offset = 0.0
        self.on_sleep = on_sleep

    def time(self):
        return time.time() + self.offset

    def sleep(self, seconds):
        if self.on_sleep:
            self.on_sleep(seconds)
        time.sleep(seconds)


def _run_map(amf, gui, on_move=None, on_sample=None, clock=None, **params):
    """Run the map recording. on_move(n) runs after the n-th stage move, on_sample(n) at each spot reading (n: the
    moves so far). Returns (result, the x of each move). With _map_params: move 1 is the first centre move, 2-3 the
    first pass, 4 its return to the centre, 5 the move to the centre before the baseline (map-only: the final hold)."""
    moves = []

    def move_x_to(x):
        moves.append(x)
        if on_move:
            on_move(len(moves))

    def centroid():
        if on_sample:
            on_sample(len(moves))
        return (1.0, 0.0)

    laf = gui.laserAutofocusController
    laf.stage = SimpleNamespace(move_x_to=move_x_to, move_y_to=lambda y: None)
    laf._centroid = centroid
    code = amf.REMOTE.replace("__PARAMS__", repr(_map_params(**params)))
    return _exec_remote(code, gui, {"time": clock} if clock else None)["result"], moves


def _abort_at_first_sample_after_move(gui, n_moves, clock):
    """Ctrl-C during the first reading of the hold after move n_moves, which is also that hold's last one."""

    def on_sample(n):
        if n == n_moves and not getattr(gui, "_af_map_abort", False):
            clock.offset += 60.0
            setattr(gui, "_af_map_abort", True)

    return on_sample


def test_map_abort_during_the_first_centre_move_dispatches_no_fluidics_job(amf):
    gui, system = _fake_gui()
    res, moves = _run_map(amf, gui, on_move=lambda n: n == 1 and setattr(gui, "_af_map_abort", True))
    assert system.log == [] and moves == [0]
    assert res["end_reason"] == "aborted"


def test_map_abort_in_the_last_baseline_reading_launches_no_pull(amf):
    gui, system = _fake_gui()
    clock = JumpClock()
    res, _moves = _run_map(amf, gui, on_sample=_abort_at_first_sample_after_move(gui, 5, clock), clock=clock)
    assert system.log == ["empty", "valve 3"]
    assert res["end_reason"] == "aborted"


def test_map_abort_after_the_last_reading_of_a_pass_does_not_return_to_the_centre(amf):
    gui, system = _fake_gui()
    res, moves = _run_map(amf, gui, on_sample=lambda n: n == 3 and setattr(gui, "_af_map_abort", True), map_only=True)
    assert moves == [0, 1, 2]
    assert res["end_reason"] == "aborted"


def test_map_only_abort_in_the_last_final_reading_is_not_completed(amf):
    gui, system = _fake_gui()
    clock = JumpClock()
    on_sample = _abort_at_first_sample_after_move(gui, 5, clock)
    res, moves = _run_map(amf, gui, on_sample=on_sample, clock=clock, map_only=True)
    assert moves == [0, 1, 2, 0, 0]
    assert res["end_reason"] == "aborted"


@pytest.mark.parametrize("busy_from_move, ours_before", [(1, []), (2, ["empty", "valve 3"])])
def test_map_never_aborts_a_fluidics_job_it_did_not_launch(amf, busy_from_move, ours_before):
    # a job started from the GUI after the map's idle check: the map's next dispatch (prep, or the pull) is refused
    gui, system = _fake_gui(stuck=(9,))
    clock = JumpClock()

    def on_move(n):
        if n == busy_from_move:
            system.run_manual(lambda: system.open_port(9))

    def on_sample(n):  # no 10 s baseline in a test
        if n == 5 and clock.offset == 0:
            clock.offset += 60.0

    res, _moves = _run_map(amf, gui, on_move=on_move, on_sample=on_sample, clock=clock)
    assert system.log == ours_before + ["valve 9"] and system.aborts == 0 and system.busy
    assert res["end_reason"] == "error: another job holds the session"


@pytest.mark.parametrize("steps", [[[5, 0], [1, 0]], [[5, 0]]])
def test_valve_sequence_abort_during_a_move_held_0_s_stops_there(valves, steps):
    gui, system = _fake_gui()
    system.on_verb = {5: _ctrl_c(gui, "_af_map_abort")}  # Ctrl-C while the valve turns to 5
    params = dict(steps=steps, pre=0.05, af_path=None, max_frames=10)
    res = _exec_remote(valves.REMOTE.replace("__PARAMS__", repr(params)), gui)["result"]
    assert system.log == ["valve 5"]
    assert res["end_reason"] == "aborted"


@pytest.mark.parametrize("busy_once, ours_before", [([], []), (["valve 1"], ["valve 1"])])
def test_valve_sequence_never_aborts_a_fluidics_job_it_did_not_launch(valves, busy_once, ours_before):
    gui, system = _fake_gui(stuck=(9,))

    def centroid():  # a job started from the GUI while the sequence holds: its next valve move is refused
        if system.log == busy_once and not system.busy:
            system.run_manual(lambda: system.open_port(9))
        return (1.0, 0.0)

    gui.laserAutofocusController._centroid = centroid
    params = dict(steps=[[1, 0.05], [5, 0.05]], pre=0.05, af_path=None, max_frames=10)
    res = _exec_remote(valves.REMOTE.replace("__PARAMS__", repr(params)), gui)["result"]
    assert system.log == ours_before + ["valve 9"] and system.aborts == 0 and system.busy
    assert res["end_reason"] == "error: another job holds the session"


def _write_release_run(path, run, port, flow_of_t, end=35.0, valve_at=5.0):
    """The recorded release after a coverslip-mode pull: the spot steady at 0, flow_of_t(t) (None = no reading)."""
    base = path / f"{run:02d}_release_port{port}_to5"
    with open(f"{base}_samples.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "phase", "gui_spot_um", "bright_spot_um", "af_frame"])
        w.writerows(["%.4f" % tt, "port5", "", "0.000", ""] for tt in np.arange(0.0, end, 1 / 30))
    with open(f"{base}_flow.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "flow_ul_min"])
        for tt in np.arange(0.0, end, 1 / RATE_HZ):
            v = flow_of_t(tt)
            w.writerow(["%.4f" % tt, "" if v is None else v])
    events = [[valve_at, "valve_start", 5], [valve_at + 1, "valve_end", 5], [end, "end", "completed"]]
    json.dump({"events_s": events, "end_reason": "completed"}, open(f"{base}_meta.json", "w"))


@pytest.mark.parametrize("read_before_the_move, sensor_verdict", [(True, "ok"), (False, INCONCLUSIVE)])
def test_the_release_sensor_verdict_needs_a_rest_reading_of_its_own(
    hold, tmp_path, read_before_the_move, sensor_verdict
):
    # The sensor's offset has drifted 8 uL/min since before the pull, and nothing flows in on release (a flat trace).
    # Against its own rest reading that is ok; without one the verdict is not taken from the pull's (that read HOLDS).
    _write_pull_run(tmp_path, 1, 3, lambda t: 0.0)
    drifted = REST + 8.0
    _write_release_run(tmp_path, 1, 3, lambda t: drifted if read_before_the_move or t >= 4.6 else None)

    [row] = hold.analyze_dir(str(tmp_path))

    assert row["sensor_verdict"] == sensor_verdict
    assert row["verdict"] == "ok"  # the coverslip verdict does not depend on it


def test_held_is_taken_after_the_pump_stopped_only(hold, tmp_path):
    # A 4 s post-pull recording (pump_idle at 42 s) with the coverslip still 8 um in: the last 10 s would reach back
    # before the pump stopped and read the baseline (held 0, "ok").
    _write_pull_run(tmp_path, 1, 3, lambda t: -8.0 if t >= 42 else 0.0, end=46.0)
    [row] = hold.analyze_dir(str(tmp_path))
    assert row["held_um"] == pytest.approx(-8.0)
    assert row["verdict"] == "HOLDS PRESSURE"


def test_held_from_too_few_readings_after_the_pump_stopped_is_inconclusive(hold, tmp_path):
    _write_pull_run(tmp_path, 1, 3, lambda t: 0.0, end=42.1)  # 3 readings after pump_idle
    assert [r["verdict"] for r in hold.analyze_dir(str(tmp_path))] == [INCONCLUSIVE]


@pytest.mark.parametrize(
    "extra, refused",
    [(["--post", "4"], True), (["--post", "10"], False), (["--sensor-only", "--post", "4"], False)],
)
def test_a_post_pull_recording_shorter_than_the_held_window_is_refused(
    hold, tmp_path, monkeypatch, capsys, extra, refused
):
    ran = []
    monkeypatch.setattr(hold, "run_suite", ran.append)
    argv = ["port_hold_check.py", "--out-dir", str(tmp_path), "--ports", "3", "--release-port", "5"] + extra
    monkeypatch.setattr(sys, "argv", argv)
    if refused:
        with pytest.raises(SystemExit) as stopped:
            hold.main()
        assert stopped.value.code == 2 and "--post" in capsys.readouterr().err
        assert ran == []
    else:
        hold.main()
        assert len(ran) == 1


def _sensor_run_on(hold, gui, clock):
    """The sensor-only run on `clock` (its on_sleep sees each sleep: 0.05 s in a wait, 0.2 s just after a dispatch)."""
    code = hold.SENSOR_REMOTE.replace("__PARAMS__", repr(_sensor_params()))
    return _exec_remote(code, gui, {"time": clock})["result"]


def test_sensor_run_abort_in_the_last_baseline_wait_launches_no_pull(hold):
    gui, system = _fake_gui()
    clock = JumpClock()

    def on_sleep(seconds):  # Ctrl-C in the last 50 ms of the baseline (the preparation is done), which then runs out
        if seconds == 0.05 and system.log == ["empty", "valve 3"] and not gui._flow_displacement_abort:
            gui._flow_displacement_abort = True
            clock.offset += 60.0

    clock.on_sleep = on_sleep
    res = _sensor_run_on(hold, gui, clock)
    assert system.log == ["empty", "valve 3", "valve 5"]  # no pull; the valve still goes to the release port
    assert res["end_reason"] == "aborted" and res["released"] is True


@pytest.mark.parametrize(
    "busy_after, ours, owed", [([], [], False), (["empty", "valve 3"], ["empty", "valve 3"], True)]
)
def test_sensor_run_never_aborts_a_fluidics_job_it_did_not_launch(hold, busy_after, ours, owed):
    # a job started from the GUI after the run's idle check: the run's next job (the preparation, or the pull) is refused
    gui, system = _fake_gui(stuck=(9,))

    def on_sleep(seconds):
        if seconds == 0.05 and system.log == busy_after and not system.busy:
            system.run_manual(lambda: system.open_port(9))

    res = _sensor_run_on(hold, gui, JumpClock(on_sleep))
    assert system.log == ours + ["valve 9"] and system.aborts == 0 and system.busy
    assert res["end_reason"] == "error: another job holds the session"
    assert res["valve_moved"] is owed  # nothing is owed when the valve never moved
    if owed:  # the valve is off the release port and its release is refused too: reported, never forced
        assert res["released"] is False and "another job holds the session" in res["release_error"]


def test_experiment_never_aborts_a_fluidics_job_it_did_not_launch(experiment):
    # a job started from the GUI once the preparation has finished: the pull is refused, and that job is not stopped
    gui, system = _fake_gui(stuck=(9,))
    seen = {"n": 0}

    def centroid():
        if system.log == ["valve 3"]:
            seen["n"] += 1
            if seen["n"] == 2:  # the reading after the one that saw the preparation finish
                system.run_manual(lambda: system.open_port(9))
        return (1.0, 0.0)

    gui.laserAutofocusController._centroid = centroid
    ns = _exec_remote(experiment.REMOTE_CODE.replace("__PARAMS__", repr(_experiment_params())), gui)
    assert system.log == ["valve 3", "valve 9"] and system.aborts == 0 and system.busy
    assert ns["result"]["end_reason"] == "error: another job holds the session"
