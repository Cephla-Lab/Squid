#!/usr/bin/env python3
"""Map coverslip height across the scan area with the laser AF spot, before and after a pull.

The focus is not moved: at each XY position the laser AF spot position is read (and the AF
camera frame saved), which gives the coverslip height relative to the AF reference. Runs
entirely inside the GUI (control server python_exec) so the timing is exact:

  1. empty the syringe, turn the valve to --port, then sit at the centre for --settle s
  2. --n-before map passes (two by default: the second measures map-to-map noise)
  3. back to the centre: 10 s baseline, pull --volume at --rate, keep recording until
     --post s after the pump finishes
  4. one map pass ("after"), then 10 s at the centre

During every pass the centre is revisited every --revisit positions to track drift.
Positions come from a Squid acquisition's coordinates.csv (--positions). The stage moves
over them in X and Y at the current focus (no autofocus corrections). Needs the GUI's control
server with python_exec enabled.

    python3 tools/coverslip_flow/af_map_flow.py --positions <acquisition>/0/coordinates.csv \
        --port 1 --volume 500 --rate 500 --out ~/flowtest/map/m1

Afterwards the bright reflection is re-detected in every saved frame (as in
flow_displacement_experiment.py) and the maps are plotted. <out>_flow.csv has the readings of the
first flow sensor in the fluidics config (the one port_hold_check.py reads); any others are not
recorded.
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flow_displacement_experiment as fde  # noqa: E402

REMOTE = r"""
import time
P = __PARAMS__
laf = gui.laserAutofocusController
svc = gui.fluidics
system = svc.system
stage = laf.stage
mc = laf.microcontroller
problems = []
if not laf.is_initialized: problems.append("laser AF not initialized")
if not laf.laser_af_properties.has_reference: problems.append("laser AF has no reference")
if not svc.initialized: problems.append("fluidics not initialized")
if system.busy: problems.append("fluidics busy")
if problems: raise RuntimeError("; ".join(problems))
manual = system.manual
xp = svc.config.syringe_pump.extract_port
x_ref = laf.laser_af_properties.x_reference
um_per_px = laf.laser_af_properties.pixel_to_um
setattr(gui, "_af_map_abort", False)
samples = []   # [t, phase, pos_index, x_mm, y_mm, um_or_None, frame]
events = []
flow = []
errors = []
st = {"af": None, "n": 0}

def _err(m, _e=errors): _e.append(m)
def h(f, ts, _b=flow): _b.append([ts, f])
sensors = list(system.devices.flow_sensors)
recorded = sensors[:1]  # the first flow sensor only: <out>_flow.csv is one sensor's readings
for s in recorded: s.subscribe(h)

def sample(phase, idx, x, y, _l=laf, _st=st, _s=samples, _P=P, _xr=x_ref, _u=um_per_px, _np=np, _t=time.time):
    ta = _t(); c = _l._get_laser_spot_centroid(); tb = _t()
    fi = None
    img = _l.image
    if img is not None:
        if _st["af"] is None:
            _st["af"] = _np.lib.format.open_memmap(_P["af_path"], mode="w+", dtype=img.dtype, shape=(_P["max_frames"],) + tuple(img.shape))
        if _st["n"] < _P["max_frames"]:
            _st["af"][_st["n"]] = img; fi = _st["n"]; _st["n"] += 1
    _s.append([(ta + tb) / 2, phase, idx, x, y, None if c is None else (c[0] - _xr) * _u, fi])

def goto(x, y, _st=stage, _sl=time.sleep, _P=P):
    _st.move_x_to(x); _st.move_y_to(y); _sl(_P["move_settle"])

def check_abort(_g=gui):
    # Ctrl-C in the client sets the flag: raising ends the whole sequence (nothing after it is launched)
    if getattr(_g, "_af_map_abort", False): raise RuntimeError("aborted")

def hold(phase, seconds, _P=P, _t=time.time, _sample=sample, _check=check_abort):
    t_end = _t() + seconds
    while _t() < t_end:
        _check()
        _sample(phase, -1, _P["center"][0], _P["center"][1])

def map_pass(name, _P=P, _goto=goto, _sample=sample, _check=check_abort, _ev=events, _t=time.time):
    _ev.append([_t(), name + "_start", None])
    for i, (x, y) in enumerate(_P["positions"]):
        _check()
        if _P["revisit"] and i and i % _P["revisit"] == 0:
            _goto(*_P["center"]); _sample(name + "_center", -1, _P["center"][0], _P["center"][1])
        _goto(x, y); _sample(name, i, x, y)
    _goto(*_P["center"]); _sample(name + "_center", -1, _P["center"][0], _P["center"][1])
    _ev.append([_t(), name + "_end", None])

def wait_fluidics(phase, _s=system, _hold=hold, _sl=time.sleep):
    _sl(0.2)
    while _s.busy: _hold(phase, 0.5)

def prep(_m=manual, _P=P):
    _m.empty_to_waste(); _m.open_port(_P["port"])

def pump(_m=manual, _P=P, _ev=events, _t=time.time, _xp=xp):
    _m.extract(_xp, _P["volume"], _P["rate"], on_started=lambda est, _ev=_ev, _t=_t: _ev.append([_t(), "pull_start", est]))
    _ev.append([_t(), "pull_end", None])

t0 = time.time()
end_reason = "completed"
launched = False
try:
    mc.turn_on_AF_laser(); mc.wait_till_operation_is_completed()
    goto(*P["center"])
    if P["map_only"]:
        hold("settle", P["settle"])
        for k in range(P["n_before"]):
            map_pass("map%d" % (k + 1))
        goto(*P["center"]); hold("final", 5)
        raise StopIteration("map_only")
    events.append([time.time(), "prep_start", None])
    launched = True
    system.run_manual(prep, callbacks={"on_error": _err})
    wait_fluidics("prep")
    events.append([time.time(), "prep_end", None])
    if errors: raise RuntimeError("prep failed: " + "; ".join(errors))
    hold("settle", P["settle"])
    for k in range(P["n_before"]):
        map_pass("before%d" % (k + 1))
    goto(*P["center"])
    hold("baseline", 10)
    events.append([time.time(), "pump_launch", None])
    system.run_manual(pump, callbacks={"on_error": _err})
    wait_fluidics("pull")
    events.append([time.time(), "pump_idle", None])
    if errors: raise RuntimeError("pump failed: " + "; ".join(errors))
    hold("post", P["post"])
    map_pass("after")
    goto(*P["center"])
    hold("final", 10)
except StopIteration:
    end_reason = "completed"
except Exception as e:
    end_reason = "aborted" if getattr(gui, "_af_map_abort", False) else "error: " + str(e)
finally:
    # however it ended: a job this run started that still runs is stopped, then the laser, then the frames
    try:
        if launched and system.busy:
            system.abort(); system.wait(30)
    finally:
        try:
            mc.turn_off_AF_laser(); mc.wait_till_operation_is_completed()
        finally:
            if st["af"] is not None:
                st["af"].flush(); del st["af"]; st["af"] = None
            for s in recorded: s.unsubscribe(h)
events.append([time.time(), "end", end_reason])
det = {}
for k in ("y_window", "x_window", "min_peak_width", "min_peak_distance", "min_peak_prominence", "spot_spacing", "filter_sigma"):
    det[k] = getattr(laf.laser_af_properties, k, None)
result = {"t0": t0, "samples": samples, "events": events, "flow": flow, "end_reason": end_reason, "af_frames": st["n"],
          "flow_sensor": recorded[0].name if recorded else None, "flow_sensors": [s.name for s in sensors],
          "laser_af": {"x_reference": x_ref, "pixel_to_um": um_per_px, "detection": det}}
"""

ABORT = 'setattr(gui, "_af_map_abort", True)\nresult = "abort requested"'
# what a recording writes; an --out with any of them is refused (the GUI would overwrite the AF frames)
OUTPUT_FILES = ("_samples.csv", "_flow.csv", "_meta.json", "_af_raw.npy")


def run(params, timeout, remote=None):
    """Run `remote` (default: the map recording) in the GUI with `params`; returns its `result`. Ctrl-C stops the
    sequence (and a pump or valve job it started) and still returns what was recorded."""
    code = (remote or REMOTE).replace("__PARAMS__", repr(params))
    fde.PORT = params.pop("_server_port", fde.PORT)
    sock = fde._send("python_exec", {"code": code}, timeout=timeout)
    reply, _interrupted = fde.receive_or_abort(sock, ABORT, "the sequence")
    if not reply.get("success"):
        sys.exit("GUI refused: %s" % reply.get("error"))
    return reply["result"]["result"]


def refuse_existing_output(parser, out):
    taken = fde.existing_output(out, OUTPUT_FILES)
    if taken:
        parser.error("%s already exists; pick another --out" % taken)


def write_flow(out, result):
    """<out>_flow.csv: the readings of the one flow sensor recorded (the first), saying when others were left out."""
    others = [n for n in result["flow_sensors"] if n != result["flow_sensor"]]
    if others:
        print("Flow: flow sensor %s only (%s not recorded)" % (result["flow_sensor"], ", ".join(others)))
    t0 = result["t0"]
    with open(out + "_flow.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "flow_ul_min"])
        for t, v in result["flow"]:
            w.writerow(["%.4f" % (t - t0), "" if v is None else v])


def bright_spot(out, result):
    """Re-detect the bright (right) reflection in every saved frame; returns {frame: um}."""
    import numpy as np

    here = os.getcwd()
    sys.path.insert(0, fde.SOFTWARE_DIR)
    os.chdir(fde.SOFTWARE_DIR)
    try:
        import control.utils as cutils
        from control._def import SpotDetectionMode
    finally:
        os.chdir(here)
    laf = result["laser_af"]
    det = dict(fde.DEFAULT_DETECTION)
    for k, v in laf["detection"].items():
        if v is not None or k == "filter_sigma":
            det[k] = v
    sigma = det.pop("filter_sigma")
    frames = np.load(out + "_af_raw.npy", mmap_mode="r")
    um = {}
    for f in range(result["af_frames"]):
        try:
            x = cutils.find_spot_location(
                np.asarray(frames[f]), mode=SpotDetectionMode.DUAL_RIGHT, params=det, filter_sigma=sigma
            )[0]
            um[f] = (x - laf["x_reference"]) * laf["pixel_to_um"]
        except Exception:
            um[f] = None
    return um


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--positions", required=True, help="coordinates.csv of a Squid acquisition (x (mm), y (mm) columns)")
    p.add_argument("--center", type=float, nargs=2, metavar=("X_MM", "Y_MM"), help="default: middle of the positions")
    p.add_argument("--port", type=int, help="port to pull from (required unless --map-only)")
    p.add_argument("--volume", type=float, default=500)
    p.add_argument("--rate", type=float, default=500)
    p.add_argument(
        "--settle", type=float, default=60, help="s at the centre after the valve move, before the first map"
    )
    p.add_argument("--n-before", type=int, default=2)
    p.add_argument("--post", type=float, default=30, help="s after the pump finishes before the after map starts")
    p.add_argument("--revisit", type=int, default=25, help="revisit the centre every N positions (0: never)")
    p.add_argument("--move-settle", type=float, default=0.15, help="s to wait after each stage move")
    p.add_argument(
        "--map-only",
        action="store_true",
        help="just map: no syringe empty, no valve move, no pull; --n-before passes after --settle s at the centre",
    )
    p.add_argument("--server-port", type=int, default=fde.PORT)
    a = p.parse_args()
    if a.port is None and not a.map_only:
        p.error("--port is required unless --map-only")
    out = os.path.expanduser(a.out)
    refuse_existing_output(p, out)
    fde.prepare_out(out)
    rows = list(csv.DictReader(open(os.path.expanduser(a.positions))))
    pos = [(float(r["x (mm)"]), float(r["y (mm)"])) for r in rows]
    xs, ys = [q[0] for q in pos], [q[1] for q in pos]
    center = tuple(a.center) if a.center else ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    center = min(pos, key=lambda q: (q[0] - center[0]) ** 2 + (q[1] - center[1]) ** 2) if not a.center else center
    n_pass = a.n_before + 1
    est = (
        (a.settle + a.n_before * len(pos) * 0.6 + 10)
        if a.map_only
        else (a.settle + 30 + n_pass * len(pos) * 0.6 + 10 + 60 * a.volume / a.rate + 10 + a.post + 10)
    )
    params = {
        "positions": pos,
        "center": center,
        "port": a.port,
        "volume": a.volume,
        "rate": a.rate,
        "settle": a.settle,
        "n_before": a.n_before,
        "post": a.post,
        "revisit": a.revisit,
        "move_settle": a.move_settle,
        "map_only": a.map_only,
        "af_path": os.path.abspath(out + "_af_raw.npy"),
        "max_frames": int(est * 40) + 2000,
        "_server_port": a.server_port,
    }
    print(
        "%d positions, centre %.3f, %.3f mm; about %.0f min. Ctrl-C aborts."
        % (len(pos), center[0], center[1], est / 60)
    )
    res = run(params, timeout=est * 3 + 600)
    print("End:", res["end_reason"], "| frames:", res["af_frames"])
    t0 = res["t0"]
    bright = bright_spot(out, res) if res["af_frames"] else {}
    with open(out + "_samples.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "phase", "pos_index", "x_mm", "y_mm", "gui_spot_um", "bright_spot_um", "af_frame"])
        for t, ph, i, x, y, um, fi in res["samples"]:
            b = bright.get(fi) if fi is not None else None
            w.writerow(
                [
                    "%.4f" % (t - t0),
                    ph,
                    i,
                    "%.4f" % x,
                    "%.4f" % y,
                    "" if um is None else "%.3f" % um,
                    "" if b is None else "%.3f" % b,
                    "" if fi is None else fi,
                ]
            )
    write_flow(out, res)
    json.dump(
        {
            "events_s": [[e[0] - t0, e[1], e[2]] for e in res["events"]],
            "end_reason": res["end_reason"],
            "laser_af": res["laser_af"],
            "flow_sensor": res["flow_sensor"],
            "args": vars(a),
            "center": center,
            "af_frames": res["af_frames"],
        },
        open(out + "_meta.json", "w"),
        indent=1,
    )
    print("Saved %s_samples.csv, _flow.csv, _meta.json, _af_raw.npy" % out)
    if res["end_reason"] != "completed":
        sys.exit("The run did not complete: %s" % res["end_reason"])


if __name__ == "__main__":
    main()
