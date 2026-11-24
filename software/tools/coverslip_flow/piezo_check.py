#!/usr/bin/env python3
"""Piezo check: does the objective piezo actually move the focus?

With the laser AF on, moves the piezo +STEP, -STEP (from the start) and back, reading the laser
spot each time. A working piezo shifts the spot by STEP / |pixel_to_um| pixels each way, in opposite
directions, and returns it to the start (the calibration's sign follows the focus camera's
orientation, so either sign is fine). A piezo whose driver has stopped following its input reports the new position in
software but the spot does not move (every laser AF correction then fails its
cross-correlation check and a whole scan stays at one z).

    python3 tools/coverslip_flow/piezo_check.py            # +/-10 um
    python3 tools/coverslip_flow/piezo_check.py --step 5

Needs the GUI's control server with python exec enabled, laser AF initialized. Leaves the piezo
where it started.
"""

import argparse
import json
import math
import socket
import sys

CODE = r"""
import time
STEP = __STEP__
laf = gui.laserAutofocusController
mc = laf.microcontroller
pz = laf.piezo
if pz is None:
    raise RuntimeError("this rig has no objective piezo")
if not laf.is_initialized:
    raise RuntimeError("laser AF is not initialized")
out = {"piezo_start_um": pz.position, "pixel_to_um": laf.laser_af_properties.pixel_to_um,
       "x_reference": laf.laser_af_properties.x_reference}
def spot(_l=laf):
    xs = []
    for i in range(3):
        c = _l._get_laser_spot_centroid()
        if c is not None:
            xs.append(float(c[0]))
    return sum(xs) / len(xs) if xs else None
moved = 0.0
try:
    mc.turn_on_AF_laser(); mc.wait_till_operation_is_completed()
    out["x0"] = spot()
    pz.move_relative(STEP); moved += STEP; time.sleep(0.3); out["x_plus"] = spot()
    pz.move_relative(-2 * STEP); moved -= 2 * STEP; time.sleep(0.3); out["x_minus"] = spot()
    pz.move_relative(STEP); moved += STEP; time.sleep(0.3); out["x_back"] = spot()
finally:
    try:
        if moved:
            pz.move_relative(-moved)
    finally:
        mc.turn_off_AF_laser(); mc.wait_till_operation_is_completed()
out["piezo_end_um"] = pz.position
result = out
"""


def call(code, port, timeout=60):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall((json.dumps({"command": "python_exec", "params": {"code": code}}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    reply = json.loads(buf)
    if not reply.get("success"):
        sys.exit("GUI refused: %s" % reply.get("error"))
    return reply["result"]["result"]


DRIVER_ADVICE = (
    "Check the piezo driver (power, external/analog input, closed loop, cable from the controller), restart it, "
    "refocus, and run this again."
)


def usable_calibration(pixel_to_um):
    return isinstance(pixel_to_um, (int, float)) and math.isfinite(pixel_to_um) and pixel_to_um != 0


def judge(step, pixel_to_um, up, down, back):
    """(ok, why) for spot shifts up/down/back (px from the start) after moves of +step, -step and back to 0 um.

    Magnitudes are compared with |pixel_to_um|: its sign follows the focus camera's orientation. The up and down
    shifts must have opposite signs, so a spot that drifts one way, or does not move, cannot pass."""
    if not usable_calibration(pixel_to_um):
        return False, "the laser AF calibration (pixel_to_um = %r) is not usable. Recalibrate laser AF." % pixel_to_um
    expected = step / abs(pixel_to_um)
    if not up * down < 0:
        return False, "the spot did not move in opposite directions for +%g and -%g um. %s" % (
            step,
            step,
            DRIVER_ADVICE,
        )
    if min(abs(up), abs(down)) < 0.5 * expected:
        return False, "the spot moved less than half the expected %.1f px. %s" % (expected, DRIVER_ADVICE)
    if abs(back) >= max(1.0, 0.1 * expected):
        return False, "the spot did not come back to the start. %s" % DRIVER_ADVICE
    return True, ""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--step", type=float, default=10.0, help="um each way")
    p.add_argument("--server-port", type=int, default=5050)
    a = p.parse_args()
    if not (math.isfinite(a.step) and a.step > 0):
        p.error("--step must be a positive number of um")
    try:
        r = call(CODE.replace("__STEP__", repr(a.step)), a.server_port)
    except ConnectionRefusedError:
        sys.exit(
            "Cannot reach the GUI control server on port %d (Settings -> Enable MCP Control Server)" % a.server_port
        )
    xs = [r.get(k) for k in ("x0", "x_plus", "x_minus", "x_back")]
    if any(x is None for x in xs):
        sys.exit("FAIL: laser spot not found in some readings: %s" % json.dumps(r))
    x0, xp, xm, xb = xs
    um_per_px = r["pixel_to_um"]
    up, down, back = xp - x0, xm - x0, xb - x0
    ok, why = judge(a.step, um_per_px, up, down, back)
    if not usable_calibration(um_per_px):
        sys.exit("FAIL: " + why)
    expected = a.step / abs(um_per_px)
    print("piezo %.2f um (start) -> %.2f um (end)" % (r["piezo_start_um"], r["piezo_end_um"]))
    print(
        "spot start %.2f px (laser AF reference %.2f px, %+.1f um from it)"
        % (x0, r["x_reference"], (x0 - r["x_reference"]) * um_per_px)
    )
    print(
        "+%g um: %+.1f px   -%g um: %+.1f px   back: %+.2f px   expected about %.1f px each way"
        % (a.step, up, a.step, down, back, expected)
    )
    implied = 2 * a.step / abs(xp - xm) if xp != xm else float("inf")
    print(
        "implied %.3f um/px vs calibration %.3f um/px (sign aside; %+.0f%%)"
        % (implied, abs(um_per_px), 100 * (implied / abs(um_per_px) - 1))
    )
    print("PASS: the piezo moves the focus" if ok else "FAIL: " + why)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
