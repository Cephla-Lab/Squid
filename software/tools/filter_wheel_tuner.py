"""Filter-wheel (W axis) speed tuning on the bench, with the wheel's encoder as the lost-step detector.

The command line around squid/filter_wheel_tuning.py, which holds the engine: what a level means, where the stall
edge is, how the margin is applied, what goes into the ini. The GUI's Utils > Filter Wheel Tuning... runs that same
engine on the application's own controller; this tool opens a controller of its own and resets it, so close the
Squid GUI first: it holds the port.

Needs firmware >= 1.6: SET_ENCODER_REPORTING streams the wheel's ENC_POS and the loop error ENC_POS - XACTUAL (the
wheel has no position field in the status packet, so this is the only way to see where it is), SET_RAMP_PROFILE
selects trapezoid vs S-shape.

Usage (from software/, with the project venv):
  python tools/filter_wheel_tuner.py check                       # init, configure, encoder sign/scale, home
  python tools/filter_wheel_tuner.py pattern --ramp sshape        # the shipping profile, as a baseline
  python tools/filter_wheel_tuner.py accelsweep --accel-list 50 100 150 200 250 300 --vmax 3.19
  python tools/filter_wheel_tuner.py velsweep --vel-list 3.19 4 5 6 --accel 150
  python tools/filter_wheel_tuner.py verify                      # 96 moves at THIS machine's settings: PASS / FAIL
  python tools/filter_wheel_tuner.py tune [--write-ini]           # find this wheel's profile, confirm it, report ini keys

verify and tune are the per-instrument procedures. A wheel's stall edge depends on what it carries (how many
filters, how balanced), on the motor and on its temperature, so a profile qualified on one wheel is a starting point,
not a setting. `verify` runs the endurance pattern (--laps x the 16-move pattern, default 6 = 96 moves) at the
settings the machine is configured with and fails on lost steps: run it after a filter change or a service visit.
A lightly loaded wheel may show no stall edge at all. --current-ma runs it at a REDUCED current (bench only, never
above the machine's, restored at exit, never written to an ini) so the edge-finding logic can still be exercised.
`tune` screens the acceleration ladder with the short pattern, takes the highest clean level, backs off by --margin
when a stall edge was found (the edge is statistical: a level can pass 16 moves and slip in 96), never asks for more
acceleration than makes the wheel faster (--plateau-ms: above some level the jerk register clamps the ramp), confirms the result
with the endurance pattern, steps down and repeats if that fails, and prints the ini keys. --write-ini puts them in
the machine ini ([GENERAL]), after saving a timestamped backup next to it. Motor current is never changed.
Options: --vmax rev/s  --accel rev/s^2  --ramp trapezoid|sshape  --microsteps 64  --flip auto|0|1
         --lost-usteps 32 (stop a sweep when a level drifts by more than this)  --out wheel_tune

This tool does NOT home the wheel at exit and does not keep the host's position record: the wheel is left wherever
the last move put it, and the next GUI start homes it.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import control._def as _def  # noqa: E402
from control.microcontroller import Microcontroller, get_microcontroller_serial_device  # noqa: E402

# The engine. Imported by name so that `python tools/filter_wheel_tuner.py` and the GUI dialog run the same code,
# and so the tool's own tests reach the helpers through this module as they always did.
from squid.filter_wheel_tuning import (  # noqa: E402,F401
    DEFAULT_PATTERN,
    FULLSTEPS,
    FW_MIN,
    INI_KEYS,
    MIN_INDEX,
    OFFSET_REV,
    SIGN,
    SLOTS,
    TRANSITIONS,
    Sampler,
    TuningCancelled,
    WheelTuner as _WheelTunerEngine,
    bench_current_ma,
    choose_accel,
    step_down_accel,
    refine_midpoint,
    fallback_speeds,
    choose_fastest,
    gentlest_as_fast,
    level_ok,
    lost_limits_usteps,
    model_move_s,
    resolve_defaults,
    set_microsteps,
    slot_usteps,
    update_ini_text,
    write_profile_ini,
)


class WheelTuner(_WheelTunerEngine):
    """The engine plus the port: this tool owns its controller, resets and initialises it, and closes it at exit."""

    def connect(self):
        if self.mcu is None:
            dev = get_microcontroller_serial_device(version=_def.CONTROLLER_VERSION, sn=_def.CONTROLLER_SN)
            self.mcu = Microcontroller(dev, reset_and_initialize=True)
            time.sleep(0.5)
            self.log("connected; controller reset and initialised")
        super().connect()

    def shutdown(self):
        super().shutdown()
        if self.mcu is not None:
            self.mcu.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "pattern", "accelsweep", "velsweep", "wrap", "verify", "tune"])
    ap.add_argument("--wrap-n", type=int, default=5, help="wrap: number of 1<->8 short-way round trips across the flag")
    ap.add_argument(
        "--wrap-timeout", type=float, default=5.0, help="wrap: seconds before a crossing is declared stopped"
    )
    ap.add_argument("--vmax", type=float, default=None, help="rev/s (default: the machine's; tune: 6)")
    ap.add_argument("--accel", type=float, default=float(_def.MAX_ACCELERATION_W_mm), help="rev/s^2")
    ap.add_argument(
        "--ramp", choices=["trapezoid", "sshape"], default=None, help="default trapezoid; verify and tune: sshape"
    )
    ap.add_argument("--microsteps", type=int, default=None, help="default: the machine's; tune: 8")
    ap.add_argument("--laps", type=int, default=6, help="verify/tune: endurance = laps x the pattern (6 x 16 = 96)")
    ap.add_argument("--margin", type=float, default=0.8, help="tune: factor applied below a found stall edge")
    ap.add_argument("--max-attempts", type=int, default=4, help="tune: endurance attempts before giving up")
    ap.add_argument(
        "--refine-step",
        type=float,
        default=10.0,
        help="tune: bisect between the last clean and the first failing acceleration down to this (0 = ladder only)",
    )
    ap.add_argument(
        "--vmax-fallback",
        type=float,
        nargs="*",
        default=[4.5, 3.19],
        help="tune: lower top speeds also searched when a stall edge is found (or nothing holds) at --vmax; the fastest confirmed profile wins (none = --vmax only)",
    )
    ap.add_argument(
        "--plateau-ms",
        type=float,
        default=2.0,
        help="tune: prefer the lowest acceleration whose adjacent-slot time is within this of the fastest clean level",
    )
    ap.add_argument("--lost-fullsteps", type=float, default=0.5, help="verify/tune: level drift that fails, full steps")
    ap.add_argument("--slip-fullsteps", type=float, default=2.0, help="verify/tune: single-move loss that fails")
    ap.add_argument(
        "--current-ma",
        type=float,
        default=None,
        help="BENCH: run at a REDUCED motor current (never above the machine's) to bring the stall edge into reach "
        "on a lightly loaded wheel; the result is never written to an ini; the machine's current is restored at exit",
    )
    ap.add_argument("--write-ini", action="store_true", help="tune: write the result to the machine ini (backup first)")
    ap.add_argument("--ini", default=None, help="tune: ini to update (default: the one control._def loaded)")
    ap.add_argument("--flip", choices=["auto", "0", "1"], default="auto")
    ap.add_argument(
        "--window-deg",
        type=float,
        default=0.0,
        help="SET_COMPLETION_WINDOW for W in degrees (0 = exact-target completion)",
    )
    ap.add_argument(
        "--transitions",
        default="auto",
        help="encoder transitions per revolution, or auto = measure over one turn and use it",
    )
    ap.add_argument("--accel-list", type=float, nargs="+", default=[50, 100, 150, 200, 250, 300])
    ap.add_argument("--vel-list", type=float, nargs="+", default=[3.19, 4, 5, 6])
    ap.add_argument("--pattern", type=int, nargs="+", default=DEFAULT_PATTERN)
    ap.add_argument(
        "--lost-usteps",
        type=int,
        default=32,
        help="level drift that counts as lost steps (64 usteps = 1 full step at 64 usteps/FS)",
    )
    ap.add_argument("--leave-enabled", action="store_true", help="leave the W driver energised at exit")
    ap.add_argument("--out", default="wheel_tune")
    a = ap.parse_args()
    try:
        resolve_defaults(a)
    except ValueError as e:
        ap.error(str(e))  # exits before a WheelTuner exists, i.e. before the port is opened
    set_microsteps(a.microsteps)
    for s in a.pattern:
        if not (MIN_INDEX <= s <= MIN_INDEX + SLOTS - 1):
            ap.error(f"slot {s} out of range")
    tuner = WheelTuner(a)
    tuner.run()
    sys.exit(tuner.exit_code)


if __name__ == "__main__":
    main()
