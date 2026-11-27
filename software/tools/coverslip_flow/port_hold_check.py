#!/usr/bin/env python3
"""Port hold check: does a port's line leave the flow cell under suction after a small pull?

For each port, with the GUI open (control server + python exec enabled):
  1. flow_displacement_experiment.py: empty the syringe, turn the valve to the port (recorded),
     30 s baseline, pull --volume at --rate, record --post s after the pump finishes
  2. valve_sequence.py: turn the valve to --release-port (a port known to pass liquid freely)
     and hold --hold s, recording the coverslip

A port whose line passes liquid freely lets the coverslip come back to where it started within
seconds. A port that stops passing liquid at small pressure differences leaves it deflected
("held"), and the move to the release port then lets liquid in and the coverslip rises.

    python3 tools/coverslip_flow/port_hold_check.py --ports 1 2 3 4 --release-port 5 \
        --order shuffle --seed 1 --out-dir ~/flowtest/port_hold

    python3 tools/coverslip_flow/port_hold_check.py --analyze-only --out-dir ~/flowtest/port_hold

Files per port (NN = run number): NN_pull<V>_port<P>.* and NN_release_port<P>_to<R>_*.
Writes <out-dir>/port_hold_summary.csv. Positions are the bright laser AF reflection
(re-detected in the saved frames), in um from the laser AF reference; lower = pulled in.
A port is flagged HOLDS PRESSURE when the coverslip is still more than 5 um from where it was
before the pull at the end of the post-pull recording; a free line comes back within 2 um.

--sensor-only: the same pull and release, judged from the flow sensor alone (no laser AF,
no frames, ~1.5 min per port). A held line leaves the flow cell short of liquid; when the
valve moves to the release port that liquid flows back in and the sensor sees it. Verdict
from the sensor volume in the 15 s after the move: >= 1.0 uL holds, 0.6-1.0 borderline
(calibrated against the coverslip check, where a holding line takes in roughly 1-4 uL and a
free line under 1 uL). Always release to the same free port: moving into a higher bottle
also lets liquid in. Writes NN_sensor_port<P>_to<R>_flow.csv / _meta.json and
<out-dir>/port_hold_sensor_summary.csv.

    python3 tools/coverslip_flow/port_hold_check.py --sensor-only --ports 1 2 3 --release-port 5 \
        --out-dir ~/flowtest/sensor_hold

Both talk to the GUI's control server and need its python_exec enabled.

After every pull the valve goes to the release port, whatever happened: a failed pull, a release
recording that failed, or Ctrl-C (which stops the pull, waits for the release move, then stops the
suite). If even that move fails, the check stops with RELEASE FAILED: turn the valve to the release
port by hand.
"""

import argparse
import contextlib
import csv
import glob
import json
import os
import random
import re
import signal
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SOFTWARE_DIR = os.path.dirname(os.path.dirname(HERE))  # this file is software/tools/coverslip_flow/
sys.path.insert(0, HERE)
import flow_displacement_experiment as fde  # noqa: E402

PY = sys.executable or "python3"
CHILD_STOP_S = 180  # how long a stopped pull recording gets to clean up and save before it is killed
RELEASE_TIMEOUT_S = 90
HOLD_FLAG_UM = 5.0
HELD_WINDOW_S = (
    10.0  # held: the coverslip over the last 10 s of the post-pull recording, all of it after the pump stopped
)
SENSOR_HOLD_UL = 1.0  # inflow in the 15 s after the release move
SENSOR_BORDERLINE_UL = 0.6
GLITCH_UL_MIN = 1000  # any reading above this in the window: air or a bad reading
SETTLE_UL_MIN = 20  # still above rest by this at 12-15 s after the move: not a refill pulse
# A verdict needs its data. A window with too few valid readings, or a gap in them longer than this, is
# INCONCLUSIVE: a missing reading must never read as "this line is free".
SENSOR_MAX_GAP_S = 1.0  # the sensor reads ~16 Hz
COVERSLIP_MAX_GAP_S = 3.0  # bright-spot readings, ~30 Hz, in 3-10 s windows
MIN_READINGS = 5
# --post: the recording ends --post s after the pump stops, its last reading up to a gap before that; the held window
# must still fit after the pump stopped
MIN_POST_S = HELD_WINDOW_S + COVERSLIP_MAX_GAP_S
INCONCLUSIVE = "INCONCLUSIVE - repeat"


def coverslip_verdict(held_um):
    """HOLDS PRESSURE when the coverslip is still more than HOLD_FLAG_UM from where it was before the pull;
    INCONCLUSIVE when there is no held reading (no spot at the end, less than HELD_WINDOW_S of recording after the pump
    stopped, or no baseline before the pull)."""
    if not np.isfinite(held_um):
        return INCONCLUSIVE
    return "HOLDS PRESSURE" if abs(held_um) > HOLD_FLAG_UM else "ok"


def sensor_verdict(ft, fv, rv, rest):
    """(inflow_ul, peak_ul_min, verdict) for the 15 s after the release move at rv.
    ft, fv unfiltered sensor readings; rest the sensor's reading just before the move. INCONCLUSIVE without a
    rest reading or without valid readings covering the whole window."""
    a, b = rv - 0.5, rv + 15
    w = (ft > a) & (ft < b)
    raw = fv[w]
    ok = np.isfinite(raw) & (np.abs(raw) < 5000)
    if not np.isfinite(rest) or not _covered(ft[w][ok], a, b, SENSOR_MAX_GAP_S):
        return np.nan, np.nan, INCONCLUSIVE
    inflow = _volume(ft[w][ok], raw[ok], a, b, rest)
    peak = float(np.max(raw[ok]) - rest)
    late = fv[(ft > rv + 12) & (ft < rv + 15)]
    late = late[np.isfinite(late) & (np.abs(late) < 5000)]
    if np.any(np.abs(raw[np.isfinite(raw)]) > GLITCH_UL_MIN) or (len(late) and np.median(late) - rest > SETTLE_UL_MIN):
        return inflow, peak, "SENSOR GLITCH - repeat"
    if inflow >= SENSOR_HOLD_UL:
        return inflow, peak, "HOLDS"
    if inflow >= SENSOR_BORDERLINE_UL:
        return inflow, peak, "borderline"
    return inflow, peak, "ok"


# ---------------------------------------------------------------- reading
def _f(v):
    return float(v) if v not in ("", None, "nan") else np.nan


def _events(meta_path):
    ev = {}
    for t, name, _v in json.load(open(meta_path))["events_s"]:
        ev.setdefault(name.split(" ")[0], t)
    return ev


def _flow_main(csv_path):
    """Flow readings from the experiment CSV (column flow_<name>_ul_min, first one found)."""
    rows = list(csv.DictReader(open(csv_path)))
    col = next((k for k in rows[0] if k.startswith("flow_") and k.endswith("_ul_min")), None)
    if col is None:
        return np.array([]), np.array([])
    t = np.array([float(r["t_s"]) for r in rows if r[col] not in ("", "nan")])
    v = np.array([float(r[col]) for r in rows if r[col] not in ("", "nan")])
    ok = np.abs(v) < 5000  # drop invalid (32767) and wild spikes
    return t[ok], v[ok]


def _series(csv_path, tcol, vcol):
    rows = list(csv.DictReader(open(csv_path)))
    t = np.array([float(r[tcol]) for r in rows])
    v = np.array([_f(r[vcol]) for r in rows])
    return t, v


def _covered(t, a, b, max_gap):
    """True when the valid readings at times t cover [a, b]: at least MIN_READINGS of them and no gap longer than
    max_gap, the window's edges included."""
    t = np.sort(t[(t >= a) & (t <= b)])
    if len(t) < MIN_READINGS:
        return False
    return float(np.max(np.diff(np.concatenate(([a], t, [b]))))) <= max_gap


def _med(t, z, a, b, max_gap=COVERSLIP_MAX_GAP_S):
    """Median of the valid readings in [a, b); NaN unless they cover the window."""
    w = (t >= a) & (t < b) & np.isfinite(z)
    return float(np.median(z[w])) if _covered(t[w], a, b, max_gap) else np.nan


def _rest(v):
    """The sensor's at-rest reading: the median of the valid readings v; NaN with fewer than MIN_READINGS."""
    v = v[np.isfinite(v) & (np.abs(v) < 5000)]
    return float(np.median(v)) if len(v) >= MIN_READINGS else np.nan


def _volume(ft, fv, a, b, rest):
    """Sensor volume (uL) between a and b, relative to the sensor's at-rest reading."""
    w = (ft > a) & (ft < b)
    if w.sum() < 2:
        return np.nan
    return float(np.sum((fv[w] - rest) * np.gradient(ft[w])) / 60)


# ---------------------------------------------------------------- analysis
def analyze_pair(pull_base, release_base):
    ev = _events(pull_base + "_meta.json")
    end_reason = json.load(open(pull_base + "_meta.json"))["end_reason"]
    t, z = _series(pull_base + "_bright.csv", "t_s", "bright_spot_um")
    ft, fv = _flow_main(pull_base + ".csv")
    vs = ev.get("valve_start", ev.get("prep_start", 5.0))
    ps = ev["pull_start"]
    pe = ev.get("pump_idle", ev.get("pull_end"))
    # the sensor's own zero: its median reading at rest before anything moves
    rest_flow = _rest(fv[ft < vs - 0.5])
    start = _med(t, z, 0, 4.5)
    before_pull = _med(t, z, ps - 10, ps)
    dip = float(np.nanmin(z[(t >= ps) & (t <= pe + 5)])) - before_pull
    # the last HELD_WINDOW_S of the recording; it must lie entirely after the pump stopped, else there is no held reading
    # (a recording re-analysed with --analyze-only was never checked against MIN_POST_S)
    held_from = t[-1] - HELD_WINDOW_S
    held = _med(t, z, held_from, t[-1] + 1) - before_pull if held_from >= pe else np.nan
    out = {
        "valve_move_um": before_pull - start,
        "valve_move_inflow_ul": _volume(ft, fv, vs - 0.5, vs + 15, rest_flow),
        "dip_um": dip,
        "sensor_volume_ul": _volume(ft, fv, ps, pe + 30, rest_flow),
        "tail_ul": _volume(ft, fv, pe + 3, pe + 30, rest_flow),
        "held_um": held,
        "sensor_rest_ul_min": rest_flow,
        "abs_before_pull_um": before_pull,
        "end_reason": end_reason,
    }
    if release_base and os.path.exists(release_base + "_samples.csv"):
        rt, rz = _series(release_base + "_samples.csv", "t_s", "bright_spot_um")
        rft_raw, rfv_raw = _series(release_base + "_flow.csv", "t_s", "flow_ul_min")
        ok = np.isfinite(rfv_raw) & (np.abs(rfv_raw) < 5000)
        rft, rfv = rft_raw[ok], rfv_raw[ok]
        release_meta = json.load(open(release_base + "_meta.json"))
        rv = [tt for tt, e, _v in release_meta["events_s"] if e == "valve_start"][0]
        # the sensor's rest reading just before the move; without one the sensor verdict is INCONCLUSIVE (the reading
        # from before the pull is not used: the sensor's offset drifts)
        rrest = _rest(rfv[rft < rv - 0.5])
        r0 = _med(rt, rz, rv - 3, rv)
        r1 = _med(rt, rz, rt[-1] - 8, rt[-1] + 1)
        _inf, peak, sv = sensor_verdict(rft_raw, rfv_raw, rv, rrest)
        if release_meta["end_reason"] != "completed":
            sv = INCONCLUSIVE
        out.update(
            {
                "release_rise_um": r1 - r0,
                "release_inflow_ul": _volume(rft, rfv, rv - 0.5, rv + 15, rrest),
                "abs_after_release_um": r1,
                "release_peak_ul_min": peak,
                "sensor_verdict": sv,
            }
        )
    else:
        out.update(
            {
                "release_rise_um": np.nan,
                "release_inflow_ul": np.nan,
                "abs_after_release_um": np.nan,
                "release_peak_ul_min": np.nan,
                "sensor_verdict": "",
            }
        )
    return out


def find_runs(out_dir):
    """[(nn, port, pull_base, release_base_or_None)] from NN_pull<V>_port<P>.csv files."""
    runs = []
    for f in sorted(glob.glob(os.path.join(out_dir, "*_pull*_port*.csv"))):
        m = re.match(r"^(\d+)_pull[\d.]+_port(\d+)\.csv$", os.path.basename(f))
        if not m:
            continue
        nn, port = m.group(1), int(m.group(2))
        base = f[:-4]
        if not os.path.exists(base + "_bright.csv"):
            print("skip %s: no _bright.csv (laser AF frames not saved?)" % os.path.basename(base))
            continue
        rel = glob.glob(os.path.join(out_dir, "%s_release_port%d_to*_samples.csv" % (nn, port)))
        runs.append((nn, port, base, rel[0][: -len("_samples.csv")] if rel else None))
    return runs


def analyze_dir(out_dir):
    rows = []
    for nn, port, base, rel in find_runs(out_dir):
        try:
            r = analyze_pair(base, rel)
        except Exception as e:  # keep going: one bad run should not hide the rest
            print("failed %s: %s" % (os.path.basename(base), e))
            continue
        r = {"run": nn, "port": port, **r}
        r["verdict"] = coverslip_verdict(r["held_um"]) if r["end_reason"] == "completed" else INCONCLUSIVE
        rows.append(r)
    if not rows:
        sys.exit("no analysable runs in %s" % out_dir)
    keys = [
        "run",
        "port",
        "held_um",
        "release_rise_um",
        "release_inflow_ul",
        "dip_um",
        "sensor_volume_ul",
        "tail_ul",
        "valve_move_um",
        "valve_move_inflow_ul",
        "sensor_rest_ul_min",
        "abs_before_pull_um",
        "abs_after_release_um",
        "verdict",
        "release_peak_ul_min",
        "sensor_verdict",
        "end_reason",
    ]
    path = os.path.join(out_dir, "port_hold_summary.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(keys)
        for r in rows:
            w.writerow([("%.2f" % r[k]) if isinstance(r[k], float) else r[k] for k in keys])
    fmt = lambda v, s="%+.1f": "  —" if v is None or (isinstance(v, float) and np.isnan(v)) else s % v
    print("\nport  held      release rise  inflow   dip    sensor vol  valve move   verdict          sensor verdict")
    print("      (um)      (um)          (uL)     (um)   (uL)        (um)")
    for r in sorted(rows, key=lambda r: r["run"]):
        print(
            "%4d  %7s   %8s      %6s  %7s   %6s     %7s     %-15s  %s"
            % (
                r["port"],
                fmt(r["held_um"]),
                fmt(r["release_rise_um"]),
                fmt(r["release_inflow_ul"]),
                fmt(r["dip_um"]),
                fmt(r["sensor_volume_ul"], "%.1f"),
                fmt(r["valve_move_um"]),
                r["verdict"] + ("" if r["end_reason"] == "completed" else " (" + r["end_reason"] + ")"),
                r["sensor_verdict"],
            )
        )
    print("\nSaved %s" % path)
    print(
        "A port passing liquid freely holds < 2 um and rises < 5 um on release. 'held' is the "
        "coverslip at the end of the post-pull recording relative to just before it (negative = still pulled in). "
        "%s: the run did not complete or the spot was lost before the pull or at the end; that port is not checked."
        % INCONCLUSIVE
    )
    return rows


# ---------------------------------------------------------------- sensor-only
# Runs inside the GUI (python_exec). Callbacks can't see the exec's locals: bind via defaults.
SENSOR_REMOTE = r"""
import time
P = __PARAMS__
svc = getattr(gui, "fluidics", None)
system = getattr(svc, "system", None) if svc is not None else None
if system is None or not svc.initialized:
    raise RuntimeError("fluidics is not initialized (Fluidics tab -> Initialize)")
if system.busy:
    raise RuntimeError("the fluidics system is busy with another job")
sensors = list(system.devices.flow_sensors)
if not sensors:
    raise RuntimeError("no flow sensor is running (add flow_sensors: to the fluidics config)")
manual = system.manual
xp = svc.config.syringe_pump.extract_port
setattr(gui, "_flow_displacement_abort", False)
events = []; flow = []
def h(f, ts, _b=flow): _b.append([ts, f])
sensor = sensors[0]
sensor.subscribe(h)
def check_abort(_g=gui):
    if getattr(_g, "_flow_displacement_abort", False): raise RuntimeError("aborted")
def wait(seconds, _t=time.time, _sl=time.sleep, _check=check_abort):
    t_end = _t() + seconds
    while _t() < t_end:
        _check()
        _sl(0.05)
    _check()  # never returns with a Ctrl-C pending: the job after a wait is not dispatched
# accepted: run_manual took a job of this run (from the preparation on, the valve may be off the release port: the
# release is owed). running: that job may still be running (only then does the cleanup stop the fluidics, never a job
# of someone else's that made run_manual refuse this run's).
own = {"accepted": False, "running": False}
def job(fn, label, abortable=True, timeout=None, _s=system, _ev=events, _t=time.time, _sl=time.sleep, _check=check_abort,
        _own=own):
    errs = []; stopped = []
    _ev.append([_t(), label + "_start", None])
    _s.run_manual(fn, callbacks={"on_error": errs.append, "on_stopped": lambda _x=stopped: _x.append(1)})
    _own["accepted"] = _own["running"] = True
    _sl(0.2)
    t_end = None if timeout is None else _t() + timeout
    while _s.busy:
        if abortable: _check()
        if t_end is not None and _t() > t_end: raise RuntimeError("%s did not finish in %g s" % (label, timeout))
        _sl(0.05)
    _own["running"] = False
    _ev.append([_t(), label + "_end", None])
    if errs: raise RuntimeError("; ".join(errs))
    if stopped: raise RuntimeError(label + " was stopped")
def prep(_m=manual, _P=P, _ev=events, _t=time.time):
    _m.empty_to_waste()
    _ev.append([_t(), "valve_start", _P["port"]])
    _m.open_port(_P["port"])
def pump(_m=manual, _P=P, _ev=events, _t=time.time, _xp=xp):
    _m.extract(_xp, _P["volume"], _P["rate"], on_started=lambda est, _ev=_ev, _t=_t: _ev.append([_t(), "pull_start", est]))
def release(_m=manual, _P=P, _ev=events, _t=time.time):
    _ev.append([_t(), "release_valve_start", _P["release_port"]])
    _m.open_port(_P["release_port"])
t0 = time.time(); end_reason = "completed"
released = False; release_error = ""
try:
    wait(P["pre"])
    job(prep, "prep")
    wait(P["baseline"])
    job(pump, "pump")
    events.append([time.time(), "pump_idle", None])
    wait(P["post"])
    job(release, "release")
    released = True
    wait(P["hold"])
except Exception as e:
    end_reason = "aborted" if getattr(gui, "_flow_displacement_abort", False) else "error: " + str(e)
finally:
    # However the run ended: a job of this run still running is stopped, then the valve goes to the release port
    # (not stoppable by the abort flag). Each step runs even if the one before fails.
    valve_moved = own["accepted"]
    try:
        if own["running"] and system.busy:
            system.abort(); system.wait(30)
    except Exception as e:
        end_reason += "; stopping the running job failed: " + str(e)
    try:
        if valve_moved and not released:
            job(release, "release", abortable=False, timeout=60)
            released = True
    except Exception as e:
        release_error = str(e)
    sensor.unsubscribe(h)
events.append([time.time(), "end", end_reason])
try:
    held_after = manual.held_volume_ul(refresh=True)
except Exception as e:
    held_after = "error: " + str(e)
result = {"t0": t0, "events": events, "flow": flow, "end_reason": end_reason, "sensor": sensor.name,
          "held_after_ul": held_after, "valve_moved": valve_moved, "released": released,
          "release_error": release_error}
"""

# Ctrl-C in the sensor-only client: only the flag. The run stops its own pump job and then moves the valve to the
# release port; aborting the fluidics from here as well could stop that release move.
SENSOR_ABORT = 'setattr(gui, "_flow_displacement_abort", True)\nresult = "abort requested"'

# The valve move to the release port with nothing recorded: after a pull that failed or was stopped, or when the
# recorded release (valve_sequence.py) failed.
RELEASE_REMOTE = r"""
import time
P = __PARAMS__
svc = getattr(gui, "fluidics", None)
system = getattr(svc, "system", None) if svc is not None else None
if system is None or not svc.initialized:
    raise RuntimeError("fluidics is not initialized")
manual = system.manual
# A pull recording whose client was stopped may still be running: stop it, and let it finish its own cleanup (it
# stops its own pump) before the valve moves, so the two never drive the fluidics at once. A job that is not this
# check's is never aborted from here: if the fluidics stays busy, the release fails and says so.
setattr(gui, "_flow_displacement_abort", True)
t_end = time.time() + P["timeout"]
while (getattr(gui, "_flow_displacement_running", False) or system.busy) and time.time() < t_end:
    time.sleep(0.05)
if getattr(gui, "_flow_displacement_running", False) or system.busy:
    raise RuntimeError("the fluidics is still busy after %g s; the valve was not moved" % P["timeout"])
errors = []; stopped = []
system.run_manual(lambda _m=manual, _p=P["port"]: _m.open_port(_p),
                  callbacks={"on_error": errors.append, "on_stopped": lambda _s=stopped: _s.append(1)})
time.sleep(0.2)
t_end = time.time() + P["timeout"]
while system.busy and time.time() < t_end:
    time.sleep(0.05)
if system.busy:
    errors.append("the valve move did not finish in %g s" % P["timeout"])
if stopped:
    errors.append("the valve move was stopped")
result = {"released": not errors, "error": "; ".join(errors)}
"""


def _say(text, stream=None):
    """Status output between a pull and its release, best-effort: an output that is gone (e.g. `| tee` stopped by the
    same Ctrl-C) must never keep the valve from moving to the release port."""
    try:
        print(text, file=sys.stdout if stream is None else stream, flush=True)
    except (OSError, ValueError):
        pass


def _report_release_failed(port, release_port, why):
    _say(
        "\n!!! RELEASE FAILED after port %d: %s\n!!! The valve may still be on port %d, with the flow cell under "
        "suction if the pull ran. Turn it to port %d in the Fluidics tab now.\n"
        % (port, why or "unknown", port, release_port),
        sys.stderr,
    )


def run_sensor_port(a, port, base):
    """One sensor-only pull and release in the GUI. Saves its files (also after Ctrl-C, with what was recorded) and
    returns {"end_reason", "released", "release_error", "interrupted"}."""
    fde.PORT = a.server_port
    params = {
        "port": port,
        "release_port": a.release_port,
        "volume": a.volume,
        "rate": a.rate,
        "pre": 3.0,
        "baseline": 15.0,
        "post": a.post,
        "hold": 20.0,
    }
    code = SENSOR_REMOTE.replace("__PARAMS__", repr(params))
    try:
        sock = fde._send("python_exec", {"code": code}, timeout=a.post + 600)
    except ConnectionRefusedError:
        sys.exit(
            "Cannot reach the GUI's control server on port %d (Settings -> Enable MCP Control Server)." % a.server_port
        )
    reply, interrupted = fde.receive_or_abort(
        sock, SENSOR_ABORT, "the pull (the GUI then moves the valve to port %d)" % a.release_port
    )
    if not reply.get("success"):
        sys.exit("The GUI refused the run on port %d: %s" % (port, reply.get("error")))
    res = (reply.get("result") or {}).get("result")
    released = res["released"] or not res["valve_moved"]
    t0 = res["t0"]
    with open(base + "_flow.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "flow_ul_min"])
        for t, v in res["flow"]:
            w.writerow(["%.4f" % (t - t0), "" if v is None else v])
    json.dump(
        {
            "events_s": [[e[0] - t0, e[1], e[2]] for e in res["events"]],
            "end_reason": res["end_reason"],
            "sensor": res["sensor"],
            "port": port,
            "release_port": a.release_port,
            "volume_ul": a.volume,
            "rate_ul_min": a.rate,
            "released": released,
            "release_error": res["release_error"],
            "interrupted": interrupted,
        },
        open(base + "_meta.json", "w"),
        indent=1,
    )
    return {
        "end_reason": res["end_reason"],
        "released": released,
        "release_error": res["release_error"],
        "interrupted": interrupted,
    }


def analyze_sensor_run(base):
    meta = json.load(open(base + "_meta.json"))
    ev = {}
    for t, name, _v in meta["events_s"]:
        ev.setdefault(name, t)
    ft, fv = _series(base + "_flow.csv", "t_s", "flow_ul_min")
    ok = np.isfinite(fv) & (np.abs(fv) < 5000)
    ps, pe, rv = ev["pull_start"], ev["pump_idle"], ev["release_valve_start"]
    rest = _rest(fv[(ft > ps - 8) & (ft < ps - 0.5)])
    rrest = _rest(fv[(ft > rv - 8) & (ft < rv - 0.5)])
    inflow, peak, verdict = sensor_verdict(ft, fv, rv, rrest)
    if meta["end_reason"] != "completed":
        verdict = INCONCLUSIVE
    return {
        "pull_volume_ul": _volume(ft[ok], fv[ok], ps, pe + 30, rest),
        "tail_ul": _volume(ft[ok], fv[ok], pe + 3, pe + 30, rest),
        "valve_move_inflow_ul": _volume(ft[ok], fv[ok], ev["valve_start"] - 0.5, ev["valve_start"] + 12, rest),
        "release_inflow_ul": inflow,
        "release_peak_ul_min": peak,
        "sensor_rest_ul_min": rest,
        "end_reason": meta["end_reason"],
        "verdict": verdict,
    }


def analyze_sensor_dir(out_dir):
    rows = []
    for f in sorted(glob.glob(os.path.join(out_dir, "*_sensor_port*_to*_meta.json"))):
        m = re.match(r"^(\d+)_sensor_port(\d+)_to(\d+)_meta\.json$", os.path.basename(f))
        if not m:
            continue
        try:
            r = analyze_sensor_run(f[: -len("_meta.json")])
        except Exception as e:
            print("failed %s: %s" % (os.path.basename(f), e))
            continue
        rows.append({"run": m.group(1), "port": int(m.group(2)), "release_port": int(m.group(3)), **r})
    if not rows:
        sys.exit("no sensor-only runs in %s" % out_dir)
    keys = [
        "run",
        "port",
        "release_port",
        "release_inflow_ul",
        "release_peak_ul_min",
        "pull_volume_ul",
        "tail_ul",
        "valve_move_inflow_ul",
        "sensor_rest_ul_min",
        "end_reason",
        "verdict",
    ]
    path = os.path.join(out_dir, "port_hold_sensor_summary.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(keys)
        for r in rows:
            w.writerow([("%.2f" % r[k]) if isinstance(r[k], float) else r[k] for k in keys])
    print("\nport  release inflow  release peak  pull volume  tail    verdict")
    print("      (uL, 15 s)      (uL/min)      (uL)         (uL)")
    for r in rows:
        print(
            "%4d  %+8.2f        %+8.0f      %7.1f      %+5.1f   %s"
            % (
                r["port"],
                r["release_inflow_ul"],
                r["release_peak_ul_min"],
                r["pull_volume_ul"],
                r["tail_ul"],
                r["verdict"] + ("" if r["end_reason"] == "completed" else " (" + r["end_reason"] + ")"),
            )
        )
    print("\nSaved %s" % path)
    print(
        "HOLDS: >= %.1f uL flowed in when the valve moved to the release port (the line had left the flow cell "
        "under suction). borderline: %.1f-%.1f uL. %s: the run did not complete or the sensor data do not cover "
        "the release; that port is not checked." % (SENSOR_HOLD_UL, SENSOR_BORDERLINE_UL, SENSOR_HOLD_UL, INCONCLUSIVE)
    )
    return rows


# ---------------------------------------------------------------- running
def run_sensor_suite(a, out_dir, ports):
    existing = [int(m.group(1)) for f in os.listdir(out_dir) for m in [re.match(r"^(\d+)_", f)] if m]
    n = max(existing) if existing else 0
    print(
        "Sensor-only. Ports in order: %s; release port %d; %g uL at %g uL/min each"
        % (ports, a.release_port, a.volume, a.rate)
    )
    for p in ports:
        n += 1
        base = os.path.join(out_dir, "%02d_sensor_port%d_to%d" % (n, p, a.release_port))
        print("===== %02d port %d =====" % (n, p), flush=True)
        outcome = run_sensor_port(a, p, base)
        print("  End: " + outcome["end_reason"], flush=True)
        if not outcome["released"]:
            _report_release_failed(p, a.release_port, outcome["release_error"])
            sys.exit("RELEASE FAILED after port %d; stopping." % p)
        if outcome["interrupted"] or outcome["end_reason"] != "completed":
            stop = "port %d: %s; stopping." % (
                p,
                "stopped with Ctrl-C" if outcome["interrupted"] else outcome["end_reason"],
            )
            print(stop, flush=True)
            analyze_sensor_dir(out_dir)
            sys.exit(stop)
    analyze_sensor_dir(out_dir)


@contextlib.contextmanager
def _ctrl_c_deferred(note):
    """While the block runs, Ctrl-C only prints `note` and is remembered; yields the list of presses."""
    presses = []

    def handler(signum, frame):
        presses.append(signum)
        _say("\nCtrl-C: " + note, sys.stderr)

    previous = signal.signal(signal.SIGINT, handler)
    try:
        yield presses
    finally:
        signal.signal(signal.SIGINT, previous)


def _run_child(cmd, log_path, shield=False):
    """(returncode, output) of one of the recording scripts; its output is also kept in log_path.

    Ctrl-C reaches the child as well (same terminal): it stops its run in the GUI and saves what it recorded. This
    waits for that, up to CHILD_STOP_S, then raises the KeyboardInterrupt. shield=True starts the child in its own
    session, so Ctrl-C does not reach it."""
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, cwd=SOFTWARE_DIR, stdout=log, stderr=subprocess.STDOUT, start_new_session=shield)
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            _say("\nCtrl-C: waiting for the run to stop and save its data...", sys.stderr)
            with _ctrl_c_deferred("still waiting for the run to stop and save its data"):
                try:
                    proc.wait(timeout=CHILD_STOP_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            raise
    with open(log_path) as fh:
        return rc, fh.read()


def _print_status(output):
    tail = [l for l in output.splitlines() if re.search(r"End:|refused|rror|Cannot", l)]
    _say("  " + ("\n  ".join(tail) if tail else "(no status line)"))


def _bare_release(a):
    """(released, why): turn the valve to the release port, nothing recorded."""
    code = RELEASE_REMOTE.replace("__PARAMS__", repr({"port": a.release_port, "timeout": RELEASE_TIMEOUT_S}))
    try:
        reply = fde._receive(fde._send("python_exec", {"code": code}, timeout=3 * RELEASE_TIMEOUT_S + 60))
    except Exception as e:  # the release is reported, whatever stopped it
        return False, "%s: %s" % (type(e).__name__, e)
    if not reply.get("success"):
        return False, str(reply.get("error"))
    res = (reply.get("result") or {}).get("result") or {}
    return res.get("released") is True, res.get("error", "")


def release_after_pull(a, port, rel_base, record):
    """Turn the valve to the release port after a pull on `port`; returns (released, ctrl_c_pressed).

    record (the pull completed): the move is recorded with valve_sequence.py, which gives the release data.
    Otherwise, or when that fails (also when it cannot start, e.g. no space for its log), the valve is moved with
    nothing recorded. Ctrl-C does not stop the release: it is remembered and honoured by the caller once the valve
    has moved."""
    note = "finishing the move to the release port %d first, then stopping" % a.release_port
    with _ctrl_c_deferred(note) as presses:
        released = False
        if record:
            try:
                _say("===== port %d: release to port %d =====" % (port, a.release_port))
                cmd = [
                    PY,
                    os.path.join(HERE, "valve_sequence.py"),
                    "--pre",
                    "5",
                    "--steps",
                    "%d:%g" % (a.release_port, a.hold),
                ]
                cmd += ["--server-port", str(a.server_port), "--out", rel_base]
                rc, output = _run_child(cmd, rel_base + "_log.txt", shield=True)
                _print_status(output)
                released = rc == 0
            except Exception as e:  # the move is still owed: it is made below, unrecorded
                _say("  the recorded release failed: %s: %s" % (type(e).__name__, e), sys.stderr)
        if not released:
            _say("  moving the valve to the release port %d (not recorded)..." % a.release_port)
            released, why = _bare_release(a)
            if not released:
                _report_release_failed(port, a.release_port, why)
    return released, bool(presses)


def run_suite(a):
    out_dir = os.path.abspath(os.path.expanduser(a.out_dir))
    os.makedirs(out_dir, exist_ok=True)
    ports = list(a.ports)
    if a.order == "shuffle":
        random.Random(a.seed).shuffle(ports)
    existing = [int(m.group(1)) for f in os.listdir(out_dir) for m in [re.match(r"^(\d+)_", f)] if m]
    n = max(existing) if existing else 0
    if a.release_port in ports:
        sys.exit(
            "port %d is also the release port; test it in a separate run with another --release-port" % a.release_port
        )
    if a.sensor_only:
        return run_sensor_suite(a, out_dir, ports)
    vol_tag = "%g" % a.volume
    fde.PORT = a.server_port
    print("Ports in order: %s; release port %d; %g uL at %g uL/min each" % (ports, a.release_port, a.volume, a.rate))
    for p in ports:
        n += 1
        nn = "%02d" % n
        pull = os.path.join(out_dir, "%s_pull%s_port%d" % (nn, vol_tag, p))
        rel = os.path.join(out_dir, "%s_release_port%d_to%d" % (nn, p, a.release_port))
        print("===== %s port %d: pull =====" % (nn, p), flush=True)
        cmd = [
            PY,
            os.path.join(HERE, "flow_displacement_experiment.py"),
            "--empty-first",
            "--port",
            str(p),
            "--volume",
            str(a.volume),
            "--rate",
            str(a.rate),
            "--pre",
            "5",
            "--baseline",
            "30",
            "--post",
            str(a.post),
            "--no-plot",
            "--server-port",
            str(a.server_port),
            "--out",
            pull,
        ]
        rc = None
        try:
            rc, output = _run_child(cmd, pull + "_log.txt")
            _print_status(output)
        finally:
            # Whatever happened once the pull started (it failed, Ctrl-C stopped it or came just after it, anything
            # else raised), the valve goes to the release port before anything else; recorded after a completed pull.
            released, ctrl_c = release_after_pull(a, p, rel, record=rc == 0)
        if not released:
            sys.exit("RELEASE FAILED after port %d; stopping." % p)
        if rc != 0:
            sys.exit("pull on port %d failed; stopping. Output:\n%s" % (p, output[-2000:]))
        if ctrl_c:
            raise KeyboardInterrupt
    analyze_dir(out_dir)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--ports", type=int, nargs="+", help="ports to test")
    p.add_argument(
        "--release-port",
        type=int,
        help="a port whose line passes liquid freely (check it first: it should hold < 2 um)",
    )
    p.add_argument("--volume", type=float, default=50)
    p.add_argument("--rate", type=float, default=500)
    p.add_argument(
        "--post",
        type=float,
        default=45,
        help="s recorded after the pump finishes (at least %g: held is the last %g s, all after the pump stopped)"
        % (MIN_POST_S, HELD_WINDOW_S),
    )
    p.add_argument("--hold", type=float, default=30, help="s held at the release port")
    p.add_argument("--order", choices=("given", "shuffle"), default="given")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--server-port", type=int, default=5050)
    p.add_argument("--analyze-only", action="store_true")
    p.add_argument(
        "--sensor-only", action="store_true", help="judge holding from the flow sensor alone (no laser AF needed)"
    )
    a = p.parse_args()
    if a.analyze_only:
        d = os.path.abspath(os.path.expanduser(a.out_dir))
        (analyze_sensor_dir if a.sensor_only else analyze_dir)(d)
        return
    if not a.ports or a.release_port is None:
        p.error("--ports and --release-port are required unless --analyze-only")
    if not a.sensor_only and a.post < MIN_POST_S:
        p.error(
            "--post must be at least %g s: the held value is the coverslip over the last %g s of the recording, all of "
            "it after the pump stops" % (MIN_POST_S, HELD_WINDOW_S)
        )
    try:
        run_suite(a)
    except KeyboardInterrupt:
        sys.exit("Stopped with Ctrl-C.")


if __name__ == "__main__":
    main()
