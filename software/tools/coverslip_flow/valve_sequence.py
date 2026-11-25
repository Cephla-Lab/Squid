#!/usr/bin/env python3
"""Record the laser AF spot at the current stage position while the selector valve steps through
a sequence of ports, with no pumping (valve-only test).

    python3 tools/coverslip_flow/valve_sequence.py --steps 1:30 5:60 1:60 --out ~/flowtest/x/valve

Each step is PORT:SECONDS (turn the valve to PORT, then hold). Frames are saved and the bright
reflection re-detected afterwards, as in af_map_flow.py; <out>_flow.csv has the first flow
sensor's readings, as there. Needs the GUI's control server with
python_exec enabled. Ctrl-C stops the sequence (no further valve moves) and saves what was
recorded; the script exits non-zero when the sequence did not complete.
"""

import argparse, csv, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flow_displacement_experiment as fde  # noqa: E402
import af_map_flow as amf  # noqa: E402

REMOTE = r"""
import time
P = __PARAMS__
laf = gui.laserAutofocusController; svc = gui.fluidics; system = svc.system; mc = laf.microcontroller
if not laf.is_initialized or not laf.laser_af_properties.has_reference: raise RuntimeError("laser AF not ready")
if not svc.initialized or system.busy: raise RuntimeError("fluidics not ready")
manual = system.manual
x_ref = laf.laser_af_properties.x_reference; um_per_px = laf.laser_af_properties.pixel_to_um
setattr(gui, "_af_map_abort", False)
samples = []; events = []; flow = []; errors = []; st = {"af": None, "n": 0}
def _err(m, _e=errors): _e.append(m)
def h(f, ts, _b=flow): _b.append([ts, f])
sensors = list(system.devices.flow_sensors)
recorded = sensors[:1]  # the first flow sensor only: <out>_flow.csv is one sensor's readings
for s in recorded: s.subscribe(h)
def sample(phase, _l=laf, _st=st, _s=samples, _P=P, _xr=x_ref, _u=um_per_px, _np=np, _t=time.time):
    ta = _t(); c = _l._get_laser_spot_centroid(); tb = _t(); fi = None; img = _l.image
    if img is not None:
        if _st["af"] is None:
            _st["af"] = _np.lib.format.open_memmap(_P["af_path"], mode="w+", dtype=img.dtype, shape=(_P["max_frames"],) + tuple(img.shape))
        if _st["n"] < _P["max_frames"]:
            _st["af"][_st["n"]] = img; fi = _st["n"]; _st["n"] += 1
    _s.append([(ta + tb) / 2, phase, -1, 0.0, 0.0, None if c is None else (c[0] - _xr) * _u, fi])
def hold(phase, seconds, _t=time.time, _sample=sample, _g=gui):
    # Ctrl-C in the client sets the flag: raising ends the sequence (no further valve moves)
    t_end = _t() + seconds
    while _t() < t_end:
        if getattr(_g, "_af_map_abort", False): raise RuntimeError("aborted")
        _sample(phase)
def valve(port, _m=manual): _m.open_port(port)
t0 = time.time(); end_reason = "completed"; launched = False
try:
    mc.turn_on_AF_laser(); mc.wait_till_operation_is_completed()
    hold("start", P["pre"])
    for port, secs in P["steps"]:
        events.append([time.time(), "valve_start", port])
        launched = True
        system.run_manual(lambda _p=port, _v=valve: _v(_p), callbacks={"on_error": _err})
        time.sleep(0.2)
        while system.busy: hold("port%d" % port, 0.2)
        events.append([time.time(), "valve_end", port])
        if errors: raise RuntimeError("; ".join(errors))
        hold("port%d" % port, secs)
except Exception as e:
    end_reason = "aborted" if getattr(gui, "_af_map_abort", False) else "error: " + str(e)
finally:
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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--steps", nargs="+", required=True, help="PORT:SECONDS ...")
    p.add_argument("--pre", type=float, default=10, help="s recorded before the first valve move")
    p.add_argument("--server-port", type=int, default=fde.PORT)
    a = p.parse_args()
    out = os.path.expanduser(a.out)
    amf.refuse_existing_output(p, out)
    fde.prepare_out(out)
    steps = [[int(s.split(":")[0]), float(s.split(":")[1])] for s in a.steps]
    total = a.pre + sum(s for _, s in steps) + 5 * len(steps)
    params = {
        "steps": steps,
        "pre": a.pre,
        "af_path": os.path.abspath(out + "_af_raw.npy"),
        "max_frames": int(total * 45) + 500,
        "_server_port": a.server_port,
    }
    res = amf.run(params, timeout=total * 3 + 300, remote=REMOTE)
    print("End:", res["end_reason"], "| frames:", res["af_frames"])
    t0 = res["t0"]
    bright = amf.bright_spot(out, res) if res["af_frames"] else {}
    with open(out + "_samples.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "phase", "gui_spot_um", "bright_spot_um", "af_frame"])
        for t, ph, _i, _x, _y, um, fi in res["samples"]:
            b = bright.get(fi) if fi is not None else None
            w.writerow(
                [
                    "%.4f" % (t - t0),
                    ph,
                    "" if um is None else "%.3f" % um,
                    "" if b is None else "%.3f" % b,
                    "" if fi is None else fi,
                ]
            )
    amf.write_flow(out, res)
    json.dump(
        {
            "events_s": [[e[0] - t0, e[1], e[2]] for e in res["events"]],
            "end_reason": res["end_reason"],
            "laser_af": res["laser_af"],
            "flow_sensor": res["flow_sensor"],
            "steps": steps,
        },
        open(out + "_meta.json", "w"),
        indent=1,
    )
    print("Saved %s_samples.csv" % out)
    if res["end_reason"] != "completed":
        sys.exit("The sequence did not complete: %s" % res["end_reason"])


if __name__ == "__main__":
    main()
