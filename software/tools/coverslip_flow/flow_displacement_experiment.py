#!/usr/bin/env python3
"""Record coverslip displacement (laser AF) and flow rate while the syringe pump draws.

Runs against the Squid GUI that is already open, through its control server, so the
GUI keeps the hardware and nothing has to be closed. One run of this script is one
experiment:

    PRE s -> preparation (empty syringe if asked, turn valves to PORT) -> BASELINE s
    -> pull VOLUME at RATE -> POST seconds
  all recorded; flow is saved exactly as the sensor reports it (no offset removed).

What it records, each sample with its own timestamp:
  * displacement_um -- the laser AF spot offset from the reference, in um. The AF laser
    is held on for the whole recording, so a sample is just the focus-camera frames
    (LASER_AF_AVERAGING_N of them, averaged) -- typically well above 5 Hz.
  * flow_<sensor>_ul_min -- every flow-sensor reading (~16 Hz, one per MCU packet).
  * events -- recording start, preparation start/end, pull start/end, recording end.

It only pulls: liquid is never pushed back through the flow cell (a push bows the coverslip
outward about as far as a pull draws it in, and leaves it past where it started).

Outputs (next to --out): <out>.csv, <out>_meta.json, <out>.png (unless --no-plot), and
<out>_af.tif -- the laser AF camera frame of each displacement sample, as a TIFF stack,
with <out>_af_frames.csv giving each frame's time and displacement (unless
--no-af-images). The CSV's af_frame column links each displacement sample to its frame.
With frames saved, the script then re-detects the spot in every frame following the bright
reflection (the faint one the GUI tracks can vanish at large deflections) and writes
<out>_bright.csv; <out>.png then plots that track as displacement from the baseline.
Redo it for an existing run with:  --out <out> --reprocess

Before the first run, in the GUI:
  1. Settings menu -> "Enable MCP Control Server" and "Enable MCP Python Exec" (the recording
     runs inside the GUI through the control server's python_exec).
  2. Fluidics tab -> Initialize. The config must list the flow sensor (flow_sensors:).
  3. Select the objective, then in the Laser AF tab: initialize, calibrate, and Set
     Reference on the coverslip. Leave the focus-camera live view off.

Examples:
    # check the rig and the achieved sample rate, no pumping (10 s)
    python3 tools/coverslip_flow/flow_displacement_experiment.py --dry-run 10 --out ~/flowtest/dry

    # empty the syringe, pull 500 uL at 500 uL/min from port 1, record until 60 s after it stops
    python3 tools/coverslip_flow/flow_displacement_experiment.py --empty-first --port 1 --volume 500 \
        --rate 500 --out ~/flowtest/port1_500ul

Ctrl-C stops the pump and the recording; whatever was recorded is still saved.
"""

import argparse
import csv
import json
import os
import socket
import sys
from datetime import datetime

HOST = "127.0.0.1"
PORT = 5050

# Runs inside the GUI process (control server's python_exec). That namespace has
# restricted builtins and imports, and functions defined here cannot see the exec's
# local names, so everything a callback needs is bound through default arguments.
REMOTE_CODE = r"""
import time, math
P = __PARAMS__

laf = getattr(gui, "laserAutofocusController", None)
svc = getattr(gui, "fluidics", None)
system = getattr(svc, "system", None) if svc is not None else None

problems = []
if laf is None:
    problems.append("this GUI has no laser autofocus controller")
else:
    props = laf.laser_af_properties
    if not laf.is_initialized:
        problems.append("laser AF is not initialized (Laser AF tab -> Initialize)")
    if not props.has_reference or props.x_reference is None:
        problems.append("laser AF has no reference (Laser AF tab -> Set Reference)")
if system is None:
    problems.append("fluidics is not initialized (Fluidics tab -> Initialize)")
else:
    if system.busy:
        problems.append("the fluidics system is busy with another job")
    sensors = list(system.devices.flow_sensors)
    if not sensors and not P["allow_no_flow_sensor"]:
        problems.append("no flow sensor is running (add flow_sensors: to the fluidics config)")
    sp = system.devices.syringe_pump
    if not P["dry_run"]:
        for label, rate in (("rate", P["rate"]), ("prefill rate", P["prefill_rate"])):
            if label == "prefill rate" and P["prefill"] <= 0:
                continue
            actual = sp.get_flow_rate(sp.flow_rate_to_speed_code(rate))
            if abs(actual - rate) > 0.02 * rate:
                problems.append(
                    "%s %s uL/min is not available on this %s uL syringe (nearest: %.0f). Available: %s"
                    % (label, rate, sp.volume, actual,
                       ", ".join("%.0f" % r for r in sp.available_flow_rates())))
if problems:
    raise RuntimeError("; ".join(problems))

ABORT_FLAG = "_flow_displacement_abort"
setattr(gui, ABORT_FLAG, False)
manual = system.manual
extract_port = svc.config.syringe_pump.extract_port
errors = []
events = []
flow = []
disp = []

def _job_error(msg, _e=errors):
    _e.append(msg)

if not P["dry_run"]:
    # The preparation -- emptying the syringe if asked, then turning the selector valves
    # to the port -- runs inside the recording (after P["pre"] seconds), so whatever it
    # does to the flow cell is in the data instead of happening off the record. The
    # baseline is then counted from the end of the preparation.
    # --empty-if-needed empties only when this pull would not fit; the held volume is read
    # fresh from the plunger, not from the pump's last cached reading.
    held_before = manual.held_volume_ul(refresh=True)
    need_empty = P["empty_first"] or (P["empty_if_needed"] and held_before + P["volume"] > sp.volume)
    held = 0.0 if need_empty else held_before
    if held + P["prefill"] + P["volume"] > sp.volume:
        raise RuntimeError(
            "the syringe holds %.0f uL; drawing %s more would exceed its %s uL. Use --empty-first or --empty-if-needed."
            % (held, P["prefill"] + P["volume"], sp.volume))
    def prep(_m=manual, _P=P, _empty=need_empty, _ev=events, _t=time.time, _xp=extract_port):
        if _empty:
            _ev.append([_t(), "empty_start", None])
            _m.empty_to_waste()
            _ev.append([_t(), "empty_end", None])
        _ev.append([_t(), "valve_start", "port %d" % _P["port"]])
        _m.open_port(_P["port"])
        _ev.append([_t(), "valve_end", None])
        if _P["prefill"] > 0:
            # draws through the flow cell, like the measured pull; the baseline that
            # follows (counted from prep_end) is the settling time after it
            _m.extract(_xp, _P["prefill"], _P["prefill_rate"],
                       on_started=lambda est, _ev=_ev, _t=_t: _ev.append([_t(), "prefill_start", est]))
            _ev.append([_t(), "prefill_end", None])

def pump(_m=manual, _P=P, _ev=events, _t=time.time, _xp=extract_port):
    _m.extract(_xp, _P["volume"], _P["rate"],
               on_started=lambda est, _ev=_ev, _t=_t: _ev.append([_t(), "pull_start", est]))
    _ev.append([_t(), "pull_end", None])

handlers = []
for s in sensors:
    def h(f, ts, _n=s.name, _buf=flow):
        _buf.append([ts, _n, f])
    s.subscribe(h)
    handlers.append((s, h))

mc = laf.microcontroller
x_ref = laf.laser_af_properties.x_reference
um_per_px = laf.laser_af_properties.pixel_to_um
end_reason = "completed"
launched = False
pump_done_at = None
prep_launched = False
prep_done_at = None

# Laser AF frames go straight to a disk-backed .npy (memmap) sized for the longest
# possible recording; the client trims it to af_n frames and converts it to TIFF.
# np.lib.format.open_memmap is plain numpy Python, so its imports are not caught by
# the sandbox's import filter (str(dtype) and the like are, so stay clear of those).
af_stack = None
af_n = 0

mc.turn_on_AF_laser()
mc.wait_till_operation_is_completed()
t0 = time.time()
events.append([t0, "recording_start", None])
try:
    while True:
        now = time.time()
        if getattr(gui, ABORT_FLAG, False):
            end_reason = "aborted"
            break
        if P["dry_run"]:
            if now - t0 >= P["dry_run"]:
                break
        else:
            if not prep_launched and now - t0 >= P["pre"]:
                events.append([now, "prep_start", ("empty + valve" if need_empty else "valve")
                               + (" + prefill %.0f uL" % P["prefill"] if P["prefill"] > 0 else "")])
                system.run_manual(prep, callbacks={"on_error": _job_error})
                prep_launched = True
            elif prep_launched and prep_done_at is None and not system.busy:
                prep_done_at = now
                events.append([now, "prep_end", None])
                if errors:
                    end_reason = "preparing the pump failed: " + "; ".join(errors)
                    break
            if not launched and prep_done_at is not None and now - prep_done_at >= P["baseline"]:
                system.run_manual(pump, callbacks={"on_error": _job_error})
                launched = True
            elif launched and pump_done_at is None and not system.busy:
                pump_done_at = now
                events.append([now, "pump_idle", None])
                if errors:
                    end_reason = "pump error: " + "; ".join(errors)
            if pump_done_at is not None and (errors or now - pump_done_at >= P["post"]):
                break
        if now - t0 > P["hard_cap"]:
            end_reason = "hard time cap reached"
            break
        ta = time.time()
        c = laf._get_laser_spot_centroid()
        tb = time.time()
        frame_idx = None
        if P["af_path"] is not None and len(disp) % P["af_every"] == 0:
            # The last of the LASER_AF_AVERAGING_N frames this sample averaged, full
            # frame (stored before any crop), kept even when spot detection failed.
            img = laf.image
            if img is not None:
                if af_stack is None:
                    af_stack = np.lib.format.open_memmap(
                        P["af_path"], mode="w+", dtype=img.dtype,
                        shape=(P["af_max_frames"],) + tuple(img.shape))
                if af_n < P["af_max_frames"] and tuple(img.shape) == tuple(af_stack.shape[1:]):
                    af_stack[af_n] = img
                    frame_idx = af_n
                    af_n += 1
        disp.append([(ta + tb) / 2, None if c is None else (c[0] - x_ref) * um_per_px, frame_idx])
        if P["interval"] > 0:
            rest = P["interval"] - (time.time() - ta)
            if rest > 0:
                time.sleep(rest)
finally:
    try:
        mc.turn_off_AF_laser()
        mc.wait_till_operation_is_completed()
    finally:
        if af_stack is not None:
            af_stack.flush()
            del af_stack
            af_stack = None
        for s, h in handlers:
            s.unsubscribe(h)
        if (launched or prep_launched) and system.busy and end_reason != "completed":
            system.abort()
            system.wait(30)
    events.append([time.time(), "recording_end", end_reason])

# getattr: a missing setting must not cost the recording it describes
detection = {}
for k in ("y_window", "x_window", "min_peak_width", "min_peak_distance",
          "min_peak_prominence", "spot_spacing", "filter_sigma"):
    detection[k] = getattr(laf.laser_af_properties, k, None)
result = {
    "t0": t0,
    "syringe": None if P["dry_run"] else {"held_before_ul": held_before, "emptied": need_empty,
                                            "held_at_start_ul": held,
                                            "prefill_ul": P["prefill"], "prefill_rate": P["prefill_rate"],
                                            "held_at_pull_ul": held + P["prefill"]},
    "displacement": disp,
    "flow": flow,
    "events": events,
    "end_reason": end_reason,
    "af_frames": af_n,
    "sensors": [s.name for s in sensors],
    "objective": str(getattr(objective_store, "current_objective", "")),
    "syringe_ul": sp.volume,
    "laser_af": {"x_reference": x_ref, "pixel_to_um": um_per_px,
                 "averaging_n": laf.laser_af_properties.laser_af_averaging_n,
                 "exposure_ms": laf.laser_af_properties.focus_camera_exposure_time_ms,
                 "detection": detection},
}
"""

ABORT_CODE = """
setattr(gui, "_flow_displacement_abort", True)
s = getattr(getattr(gui, "fluidics", None), "system", None)
if s is not None and s.busy:
    s.abort()
result = "abort requested"
"""


def _send(command, params, timeout):
    """One request to the control server; returns the open socket after sending."""
    sock = socket.create_connection((HOST, PORT), timeout=10)
    sock.settimeout(timeout)
    sock.sendall((json.dumps({"command": command, "params": params}) + "\n").encode())
    return sock


def _receive(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    sock.close()
    if not buf:
        raise RuntimeError("the control server closed the connection without a reply")
    return json.loads(buf.decode())


def run_remote(params):
    code = REMOTE_CODE.replace("__PARAMS__", repr(params))
    try:
        sock = _send("python_exec", {"code": code}, timeout=params["hard_cap"] + 400)
    except ConnectionRefusedError:
        sys.exit(
            "Cannot reach the GUI's control server on %s:%d. In the GUI: Settings -> "
            "Enable MCP Control Server." % (HOST, PORT)
        )
    try:
        reply = _receive(sock)
    except KeyboardInterrupt:
        print("\nStopping the pump and the recording...", file=sys.stderr)
        _receive(_send("python_exec", {"code": ABORT_CODE}, timeout=30))
        reply = _receive(sock)  # the recording returns what it has
    if not reply.get("success"):
        sys.exit("The GUI refused the experiment: %s" % reply.get("error"))
    # python_exec answers {"result": <the remote `result`>}, inside the server's own envelope.
    result = (reply.get("result") or {}).get("result")
    if not isinstance(result, dict):
        sys.exit("Unexpected reply from the GUI: %r" % (result,))
    return result


def summarize(result):
    t = [row[0] for row in result["displacement"]]
    gaps = sorted(b - a for a, b in zip(t, t[1:]))
    n_bad = sum(1 for row in result["displacement"] if row[1] is None)
    s = {"displacement_samples": len(t), "displacement_failed": n_bad}
    if gaps and gaps[len(gaps) // 2] > 0:
        s["displacement_rate_hz_median"] = 1.0 / gaps[len(gaps) // 2]
        s["displacement_gap_s_max"] = gaps[-1]
    for name in result["sensors"]:
        ft = [row[0] for row in result["flow"] if row[1] == name]
        if len(ft) > 1:
            s["flow_rate_hz_%s" % name] = (len(ft) - 1) / (ft[-1] - ft[0])
    return s


def write_outputs(out, args, result, summary):
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    t0 = result["t0"]
    sensors = result["sensors"]
    header = ["t_s", "wall_time", "displacement_um", "af_frame"] + ["flow_%s_ul_min" % n for n in sensors] + ["event"]
    rows = []
    for ts, um, frame in result["displacement"]:
        rows.append((ts, {2: "" if um is None else "%.4f" % um, 3: "" if frame is None else frame}))
    for ts, name, f in result["flow"]:
        rows.append((ts, {4 + sensors.index(name): "" if f is None else "%.2f" % f}))
    for ts, name, extra in result["events"]:
        label = name if extra is None else "%s (%s)" % (name, extra if isinstance(extra, str) else "%.1f s est" % extra)
        rows.append((ts, {len(header) - 1: label}))
    rows.sort(key=lambda r: r[0])
    with open(out + ".csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for ts, cells in rows:
            line = ["%.4f" % (ts - t0), datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")]
            line += [""] * (len(header) - 2)
            for col, val in cells.items():
                line[col] = val
            w.writerow(line)
    if result.get("af_frames"):
        with open(out + "_af_frames.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["af_frame", "t_s", "displacement_um"])
            for ts, um, frame in result["displacement"]:
                if frame is not None:
                    w.writerow([frame, "%.4f" % (ts - t0), "" if um is None else "%.4f" % um])
    meta = {
        "arguments": vars(args),
        "objective": result["objective"],
        "syringe": result.get("syringe"),
        "syringe_ul": result["syringe_ul"],
        "laser_af": result["laser_af"],
        "end_reason": result["end_reason"],
        "events_s": [[round(ts - t0, 4), name, extra] for ts, name, extra in result["events"]],
        "summary": summary,
    }
    with open(out + "_meta.json", "w") as f:
        json.dump(meta, f, indent=2)


def convert_af_images(raw_path, tif_path, n_frames):
    """Trim the GUI's preallocated .npy stack to the frames it filled and save it as a
    TIFF stack (Fiji/ImageJ opens it as a time series). The .npy is removed once the
    TIFF is written; if anything fails it is kept, so no frames are lost."""
    if not os.path.exists(raw_path):
        return None
    import numpy as np

    stack = np.load(raw_path, mmap_mode="r")[:n_frames]
    try:
        import tifffile
    except ImportError:
        print("tifffile not found; laser AF frames left in %s" % raw_path, file=sys.stderr)
        return raw_path
    try:
        tifffile.imwrite(tif_path, stack, photometric="minisblack", bigtiff=stack.nbytes > 3.5e9)
    except Exception as e:
        print("Could not write %s (%s); laser AF frames left in %s" % (tif_path, e, raw_path), file=sys.stderr)
        return raw_path
    del stack
    os.remove(raw_path)
    return tif_path


# This file is software/tools/coverslip_flow/<script>.py; SQUID_SOFTWARE_DIR overrides where Squid is.
SOFTWARE_DIR = os.environ.get("SQUID_SOFTWARE_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
# Squid's default spot detection (LASER_AF_* in control/_def.py), for any setting a run's meta lacks.
DEFAULT_DETECTION = {
    "y_window": 96,
    "x_window": 20,
    "min_peak_width": 10.0,
    "min_peak_distance": 10.0,
    "min_peak_prominence": 0.2,
    "spot_spacing": 100.0,
    "filter_sigma": None,
}


def track_bright_spot(out):
    """Re-detect the laser spot in every saved frame, following the brightest (rightmost)
    reflection instead of the faint one the GUI tracks.

    The focus camera sees two reflections ~114 um apart that move together. The faint one
    the GUI follows fades out when the coverslip moves far from focus, and the finder then
    reports the bright one instead -- a ~114 um jump. The bright spot stays sharp, so its
    track is continuous. Works from the files on disk only, so finished runs can be redone
    with --reprocess. Writes <out>_bright.csv; returns (rows, info) or None without frames.
    rows: [t_s, bright_spot_um (from the AF reference), displacement_um (from baseline)].
    """
    tif_path = out + "_af.tif"
    if not os.path.exists(tif_path):
        return None
    import numpy as np
    import tifffile

    here = os.getcwd()
    sys.path.insert(0, SOFTWARE_DIR)
    os.chdir(SOFTWARE_DIR)  # control._def reads the machine config relative to the software dir
    try:
        import control.utils as cutils
        from control._def import SpotDetectionMode
    finally:
        os.chdir(here)

    meta = json.load(open(out + "_meta.json"))
    laf = meta["laser_af"]
    det = dict(DEFAULT_DETECTION)
    for k, v in laf.get("detection", {}).items():
        if v is not None or k == "filter_sigma":
            det[k] = v
    sigma = det.pop("filter_sigma")
    x_ref, um_per_px = laf["x_reference"], laf["pixel_to_um"]
    frames = [(int(r["af_frame"]), float(r["t_s"])) for r in csv.DictReader(open(out + "_af_frames.csv"))]
    events = {name: t for t, name, _ in meta["events_s"]}

    rows = []
    with tifffile.TiffFile(tif_path) as tif:
        for f, t in frames:
            try:
                x = cutils.find_spot_location(
                    tif.pages[f].asarray(), mode=SpotDetectionMode.DUAL_RIGHT, params=det, filter_sigma=sigma
                )[0]
                rows.append([t, (x - x_ref) * um_per_px])
            except Exception:
                rows.append([t, None])
    # Baseline: after the preparation (if recorded) and before the pump starts; a dry
    # run: its first 2 s.
    t_base = events.get("pull_start", rows[0][0] + 2 if rows else 0)
    t_from = events.get("prep_end", float("-inf"))
    base = [um for t, um in rows if um is not None and t_from <= t < t_base]
    baseline = float(np.mean(base)) if base else None
    for r in rows:
        r.append(None if r[1] is None or baseline is None else r[1] - baseline)
    with open(out + "_bright.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "bright_spot_um", "displacement_from_baseline_um"])
        for t, um, dd in rows:
            w.writerow(["%.4f" % t, "" if um is None else "%.3f" % um, "" if dd is None else "%.3f" % dd])
    ok = [dd for _, _, dd in rows if dd is not None]
    info = {
        "bright_spot_failed": sum(1 for r in rows if r[1] is None),
        "bright_spot_frames": len(rows),
        "baseline_um": baseline,
    }
    if ok:
        i = min(range(len(rows)), key=lambda k: rows[k][2] if rows[k][2] is not None else float("inf"))
        info.update({"min_displacement_um": rows[i][2], "min_at_s": rows[i][0]})
        tail = [dd for t, _, dd in rows if dd is not None and t > rows[-1][0] - 10]
        info["last_10s_mean_um"] = float(np.mean(tail)) if tail else None
    return rows, info


def plot(out, result, bright=None):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not found; skipping the plot", file=sys.stderr)
        return
    t0 = result["t0"]
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
    if bright:
        d = [(t, dd) for t, _, dd in bright if dd is not None]
        ax1.set_ylabel("Displacement from baseline (µm)\n(bright spot)")
    else:
        d = [(ts - t0, um) for ts, um, _ in result["displacement"] if um is not None]
        ax1.set_ylabel("Displacement (µm)")
    if d:
        ax1.plot(*zip(*d), ".", ms=1.5)
    for name in result["sensors"]:
        fl = [(ts - t0, f) for ts, n, f in result["flow"] if n == name and f is not None]
        if fl:
            ax2.plot(*zip(*fl), lw=0.8, label=name)
    ax2.set_ylabel("Flow, sensor reading (µL/min)")
    ax2.set_xlabel("Time (s)")
    if len(result["sensors"]) > 1:
        ax2.legend()
    ev = {name: ts - t0 for ts, name, _ in result["events"]}
    for start, end, color in (("prep_start", "prep_end", "tab:gray"), ("pull_start", "pull_end", "tab:blue")):
        if start in ev:
            for ax in (ax1, ax2):
                ax.axvspan(ev[start], ev.get(end, ev.get("recording_end", ev[start])), color=color, alpha=0.12)
    ax1.set_title(os.path.basename(out))
    fig.tight_layout()
    fig.savefig(out + ".png", dpi=150)


def main():
    global PORT
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="output path without extension")
    p.add_argument("--port", type=int, help="reagent (selector valve) port to draw from")
    p.add_argument("--volume", type=float, help="volume to pull, uL")
    p.add_argument("--rate", type=float, help="pull rate, uL/min")
    p.add_argument(
        "--pre", type=float, default=5, help="seconds recorded before the preparation (emptying, valve move) starts"
    )
    p.add_argument(
        "--baseline",
        type=float,
        default=10,
        help="seconds recorded after the preparation ends and before the pump starts",
    )
    p.add_argument("--post", type=float, default=60, help="seconds recorded after the pump stops")
    p.add_argument(
        "--interval", type=float, default=0, help="displacement sample period, s (0 = as fast as the camera allows)"
    )
    p.add_argument(
        "--prefill",
        type=float,
        default=0,
        metavar="UL",
        help="during the preparation, draw this much into the syringe (through the flow cell) "
        "before the baseline; combine with --empty-first to set the fill at the measured pull",
    )
    p.add_argument("--prefill-rate", type=float, default=1000, help="pre-fill rate, uL/min")
    p.add_argument("--empty-first", action="store_true", help="empty the syringe to waste before the baseline")
    p.add_argument(
        "--empty-if-needed",
        action="store_true",
        help="empty the syringe to waste before the baseline only if this pull would not fit",
    )
    p.add_argument(
        "--dry-run",
        type=float,
        default=0,
        metavar="SECONDS",
        help="record for this long without pumping (checks the setup and sample rate)",
    )
    p.add_argument("--allow-no-flow-sensor", action="store_true", help="record displacement only")
    p.add_argument("--no-af-images", action="store_true", help="do not save the laser AF camera frames")
    p.add_argument(
        "--af-every",
        type=int,
        default=1,
        metavar="N",
        help="save the laser AF frame of every Nth displacement sample (default: every one)",
    )
    p.add_argument("--no-plot", action="store_true")
    p.add_argument("--server-port", type=int, default=PORT, help="the GUI control server's port")
    p.add_argument(
        "--reprocess",
        action="store_true",
        help="redo the bright-spot tracking and plot for an existing --out, without running anything",
    )
    args = p.parse_args()
    PORT = args.server_port

    if args.reprocess:
        reprocess(os.path.expanduser(args.out), args.no_plot)
        return
    if not args.dry_run and None in (args.port, args.volume, args.rate):
        p.error("--port, --volume and --rate are required (or use --dry-run)")
    out = os.path.expanduser(args.out)
    for ext in (".csv", "_meta.json", "_af.tif", "_af_raw.npy"):
        if os.path.exists(out + ext):
            p.error("%s already exists; pick another --out" % (out + ext))
    pump_s = 0 if args.dry_run else 60 * args.volume / args.rate
    params = {
        "dry_run": args.dry_run,
        "port": args.port,
        "volume": args.volume,
        "rate": args.rate,
        "pre": args.pre,
        "baseline": args.baseline,
        "post": args.post,
        "interval": args.interval,
        "empty_first": args.empty_first,
        "prefill": 0 if args.dry_run else args.prefill,
        "prefill_rate": args.prefill_rate,
        "empty_if_needed": args.empty_if_needed,
        "allow_no_flow_sensor": args.allow_no_flow_sensor,
        "af_path": None if args.no_af_images else os.path.abspath(out + "_af_raw.npy"),
        "af_every": max(1, args.af_every),
        # stop even if the pump never reports done: expected length plus generous slack
        # (the preparation gets up to 180 s: emptying a full 5 mL syringe plus valve moves)
        "hard_cap": (
            args.dry_run
            or args.pre + 180 + 90 * args.prefill / args.prefill_rate + args.baseline + pump_s * 1.5 + args.post + 120
        ),
    }
    # Room for every frame the longest allowed recording could produce at 50 Hz; the
    # file is sparse until written and trimmed to what was used afterwards.
    params["af_max_frames"] = int(params["hard_cap"] * 50 / params["af_every"]) + 100
    if args.dry_run:
        print("Dry run: recording %.0f s without pumping..." % args.dry_run)
    else:
        print(
            "Recording: %.0f s, preparation, %.0f s baseline, pull %.0f uL at %.0f uL/min, then %.0f s. Ctrl-C to stop."
            % (args.pre, args.baseline, args.volume, args.rate, args.post)
        )
    result = run_remote(params)
    summary = summarize(result)
    summary["af_frames_saved"] = result.get("af_frames", 0)
    write_outputs(out, args, result, summary)
    af_file = None
    if result.get("af_frames"):
        print("Writing %d laser AF frames..." % result["af_frames"])
        af_file = convert_af_images(out + "_af_raw.npy", out + "_af.tif", result["af_frames"])
    bright = None
    if af_file and af_file.endswith(".tif"):
        print("Tracking the bright laser spot in the saved frames...")
        tracked = track_bright_spot(out)
        if tracked:
            bright, info = tracked
            summary.update(info)
            meta_path = out + "_meta.json"
            meta = json.load(open(meta_path))
            meta["summary"] = summary
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)
    if not args.no_plot:
        plot(out, result, bright)

    print("End: %s" % result["end_reason"])
    for k, v in summary.items():
        print("  %s: %s" % (k, "%.2f" % v if isinstance(v, float) else v))
    if summary.get("displacement_rate_hz_median", 0) < 5:
        print(
            "WARNING: displacement sampled below 5 Hz. Lower LASER_AF_AVERAGING_N or the focus-camera "
            "exposure, or crop the focus camera."
        )
    print("Saved %s.csv, %s_meta.json%s" % (out, out, "" if args.no_plot else ", %s.png" % out))
    if af_file:
        print("Laser AF frames: %s (frame times: %s_af_frames.csv)" % (af_file, out))
    if bright:
        print("Bright-spot displacement: %s_bright.csv" % out)


def reprocess(out, no_plot=False):
    """Bright-spot tracking and the plot for a run already on disk."""
    tracked = track_bright_spot(out)
    if tracked is None:
        sys.exit("No laser AF frames at %s_af.tif; nothing to track." % out)
    bright, info = tracked
    if not no_plot:
        # Rebuild what plot() needs from the CSV: flow and events, times relative to t0 = 0.
        flow, events, sensors = [], [], []
        with open(out + ".csv") as f:
            rd = csv.DictReader(f)
            cols = [c for c in rd.fieldnames if c.startswith("flow_")]
            sensors = [c[len("flow_") : -len("_ul_min")] for c in cols]
            for r in rd:
                t = float(r["t_s"])
                for c, n in zip(cols, sensors):
                    if r[c]:
                        flow.append([t, n, float(r[c])])
                if r["event"]:
                    events.append([t, r["event"].split(" ")[0], None])
        plot(out, {"t0": 0.0, "displacement": [], "flow": flow, "events": events, "sensors": sensors}, bright)
    for k, v in info.items():
        print("  %s: %s" % (k, "%.2f" % v if isinstance(v, float) else v))
    print("Saved %s_bright.csv%s" % (out, "" if no_plot else ", %s.png" % out))


if __name__ == "__main__":
    main()
