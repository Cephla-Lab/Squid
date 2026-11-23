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


def test_too_few_readings_after_the_move_say_so(hold):
    t, f = _trace(2.0, end=RELEASE - 1)
    assert hold.sensor_verdict(t, f, RELEASE, REST)[2] == "no sensor data"


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
