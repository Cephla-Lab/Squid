"""Hardware bench for large acquisition mode (PR 648): focus and signal across a pause.

The pause checkpoint does nothing to the hardware. The stage sits at the last field and the
illumination stays where the last frame left it, which is off in both trigger modes (verified in
tests/control/test_MultiPointController_pause.py on the simulated microcontroller). Simulation cannot
show whether the sample's focus or signal survive minutes of that idle. This bench measures it.

Run from software/ with the machine ini in place, Squid closed, one objective in the light path, and
the sample hand-focused on a field in the channel you will acquire with:

    python tools/pause_focus_bench.py --minutes 20 --out bench_pause_20min
    python tools/pause_focus_bench.py --minutes 20 --channel "Fluorescence 488 nm Ex" --out bench_488
    python tools/pause_focus_bench.py --simulation --minutes 0.2 --out /tmp/bench_smoke   (code path only)

Procedure (no GUI, no acquisition; the same idle a pause produces):
  1. reference: one frame at the hand-focused Z. Focus measure (control.utils.calculate_focus_measure,
     the acquisition's own operator), mean intensity, Z, and the laser-AF displacement when laser AF is
     initialized.
  2. idle for --minutes with the illumination off. Every --sample-every-s one frame at the same Z,
     recording the same quantities (one frame per sample is a negligible dose; --sample-every-s 0 takes
     none, so the run is a pure dark idle).
  3. a fine Z sweep of +-sweep-um in sweep-steps frames: where the focus actually is now.
     drift_um = z_peak - z_ref.
  4. one frame back at z_ref: focus_measure_after / focus_measure_before and mean_after / mean_before.
  5. when laser AF is initialized: move_to_target(0), then Z and focus measure after it.
  6. back to z_ref, illumination off.

Writes <out>/record.json and <out>/figure.pdf (focus measure and Z against time, the sweep with z_ref
and the peak marked) and prints one summary line.

Reading the result: the depth of field is in the record (0.55 um / NA^2). drift_um inside it and a
focus ratio near 1 mean a pause of this length needs nothing extra. Otherwise the next field after a
resume relies on the run's own autofocus, which the worker runs for that field exactly as for any
other (tests/control/test_MultiPointController_pause.py), and this number says whether mode-on runs
should require it.
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime

os.environ.setdefault("QT_API", "pyqt5")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # software/

import numpy as np  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

import squid.logging  # noqa: E402

squid.logging.setup_uncaught_exception_logging()
import control._def  # noqa: E402
import control.microscope  # noqa: E402
import control.utils  # noqa: E402
from squid.abc import CameraAcquisitionMode  # noqa: E402

log = logging.getLogger("pause_focus_bench")
WAVELENGTH_UM = 0.55


def depth_of_field_um(na: float) -> float:
    return WAVELENGTH_UM / (na * na) if na else float("nan")


def pick_channel(names, wanted):
    if wanted:
        for n in names:
            if n == wanted:
                return n
        raise SystemExit(f"channel {wanted!r} not found; available: {names}")
    for n in names:
        if "Fluorescence" in n:
            return n
    return names[0]


class Bench:
    def __init__(self, scope, args):
        self.scope = scope
        self.a = args
        self.live = scope.live_controller
        self.camera = scope.camera
        self.stage = scope.stage
        self.mcu = scope.low_level_drivers.microcontroller
        self.record = {"started": datetime.now().isoformat(timespec="seconds"), "args": vars(args), "samples": []}
        objective = scope.objective_store.current_objective
        na = float(scope.objective_store.objectives_dict[objective].get("NA") or 0.0)
        self.channels = scope.live_controller.get_channels(objective)
        self.channel = pick_channel([c.name for c in self.channels], args.channel)
        self.config = next(c for c in self.channels if c.name == self.channel)
        self.laser_af = scope.laser_autofocus_controller
        if self.laser_af is not None and not getattr(self.laser_af, "is_initialized", False):
            self.laser_af = None
        self.record["setup"] = {
            "objective": objective,
            "na": na,
            "dof_um": depth_of_field_um(na),
            "channel": self.channel,
            "trigger_mode": str(self.live.trigger_mode),
            "laser_af_initialized": self.laser_af is not None,
            "focus_measure_operator": str(control._def.FOCUS_MEASURE_OPERATOR),
            "repo_state": control.utils.get_squid_repo_state_description(),
        }

    # ------------------------------------------------------------- hardware
    def z_um(self) -> float:
        return self.stage.get_pos().z_mm * 1000.0

    def move_z_um(self, z_um: float) -> None:
        self.stage.move_z_to(z_um / 1000.0)
        self.stage.wait_for_idle(10.0)

    def snap(self) -> np.ndarray:
        """One frame the way the acquisition takes it in software-trigger mode: illumination on, trigger,
        read, illumination off."""
        self.live.set_microscope_mode(self.config)
        self.live.turn_on_illumination()
        self.mcu.wait_till_operation_is_completed()
        self.camera.send_trigger(illumination_time=self.camera.get_exposure_time())
        try:
            image = self.camera.read_frame()
        finally:
            self.live.turn_off_illumination()
            self.mcu.wait_till_operation_is_completed()
        if image is None:
            raise RuntimeError("camera.read_frame() returned None")
        return image

    def measure(self, label: str, t0: float) -> dict:
        image = self.snap()
        fm = float(control.utils.calculate_focus_measure(image, control._def.FOCUS_MEASURE_OPERATOR))
        out = {
            "label": label,
            "t_min": (time.monotonic() - t0) / 60.0,
            "z_um": self.z_um(),
            "focus_measure": fm,
            "mean": float(np.mean(image)),
            "max": float(np.max(image)),
            "laser_af_um": None,
        }
        if self.laser_af is not None:
            try:
                out["laser_af_um"] = float(self.laser_af.measure_displacement())
            except Exception as e:  # noqa: BLE001
                out["laser_af_error"] = str(e)
        print(
            f"{label:>14}: t={out['t_min']:6.2f} min  z={out['z_um']:10.3f} um  focus={fm:12.1f}  "
            f"mean={out['mean']:8.1f}"
            + (f"  laserAF={out['laser_af_um']:+.3f} um" if out["laser_af_um"] is not None else ""),
            flush=True,
        )
        return out

    # ------------------------------------------------------------------ run
    def run(self):
        a = self.a
        self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
        if not getattr(self.camera, "is_streaming", lambda: True)():
            self.camera.start_streaming()
        t0 = time.monotonic()
        z_ref = self.z_um()
        self.record["setup"]["z_ref_um"] = z_ref
        print(f"setup: {json.dumps(self.record['setup'], default=str)}", flush=True)

        ref = self.measure("reference", t0)
        self.record["reference"] = ref

        # 2. idle, dark, sampling
        end = t0 + a.minutes * 60.0
        next_sample = t0 + a.sample_every_s if a.sample_every_s > 0 else float("inf")
        while time.monotonic() < end:
            now = time.monotonic()
            if now >= next_sample:
                self.record["samples"].append(self.measure(f"idle {len(self.record['samples']) + 1}", t0))
                self.save()
                next_sample += a.sample_every_s
            time.sleep(min(1.0, max(0.0, end - time.monotonic())))

        # 3. where is the focus now: fine sweep about z_ref
        half = a.sweep_um
        zs = np.linspace(z_ref - half, z_ref + half, a.sweep_steps)
        self.move_z_um(zs[0] - 2.0)  # approach from below, like the acquisition's z moves
        fms = []
        for z in zs:
            self.move_z_um(float(z))
            fms.append(float(control.utils.calculate_focus_measure(self.snap(), control._def.FOCUS_MEASURE_OPERATOR)))
        i_peak = int(np.argmax(fms))
        z_peak = float(zs[i_peak])
        self.record["sweep"] = {
            "z_um": [float(z) for z in zs],
            "focus_measure": fms,
            "z_peak_um": z_peak,
            "peak_at_edge": i_peak in (0, len(zs) - 1),
        }

        # 4. back at z_ref
        self.move_z_um(z_ref - 2.0)
        self.move_z_um(z_ref)
        after = self.measure("after idle", t0)
        self.record["after"] = after

        # 5. laser AF recovery
        if self.laser_af is not None:
            ok = False
            try:
                ok = bool(self.laser_af.move_to_target(0.0))
            except Exception as e:  # noqa: BLE001
                self.record["laser_af_error"] = str(e)
            rec = self.measure("after laserAF", t0)
            rec["move_to_target_ok"] = ok
            self.record["after_laser_af"] = rec

        # 6. home
        self.move_z_um(z_ref - 2.0)
        self.move_z_um(z_ref)
        self.live.turn_off_illumination()

        dof = self.record["setup"]["dof_um"]
        result = {
            "minutes": a.minutes,
            "dof_um": dof,
            "drift_um": z_peak - z_ref,
            "drift_in_dof": abs(z_peak - z_ref) <= dof if dof == dof else None,
            "focus_ratio": after["focus_measure"] / ref["focus_measure"] if ref["focus_measure"] else None,
            "mean_ratio": after["mean"] / ref["mean"] if ref["mean"] else None,
            "sweep_peak_ratio": max(fms) / ref["focus_measure"] if ref["focus_measure"] else None,
        }
        if "after_laser_af" in self.record:
            result["laser_af_recovered_um"] = self.record["after_laser_af"]["z_um"] - z_ref
            result["laser_af_focus_ratio"] = (
                self.record["after_laser_af"]["focus_measure"] / ref["focus_measure"] if ref["focus_measure"] else None
            )
        self.record["result"] = result
        self.record["finished"] = datetime.now().isoformat(timespec="seconds")
        print(
            f"RESULT: {a.minutes:g} min idle, {self.channel}: focus moved {result['drift_um']:+.2f} um "
            f"(DOF {dof:.2f} um), focus measure after/before {result['focus_ratio']:.3f}, "
            f"mean after/before {result['mean_ratio']:.3f}"
            + (
                f", laser AF brought it back to {result['laser_af_recovered_um']:+.2f} um"
                if "laser_af_recovered_um" in result
                else ""
            ),
            flush=True,
        )

    def save(self):
        with open(os.path.join(self.a.out, "record.json"), "w", encoding="utf-8") as f:
            json.dump(self.record, f, indent=1, default=str)

    # --------------------------------------------------------------- figure
    def figure(self):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages

        r = self.record
        pts = [r["reference"]] + r["samples"] + [r["after"]]
        t = [p["t_min"] for p in pts]
        with PdfPages(os.path.join(self.a.out, "figure.pdf")) as pdf:
            fig, axes = plt.subplots(3, 1, figsize=(8, 10))
            axes[0].plot(t, [p["focus_measure"] for p in pts], "o-")
            axes[0].set_ylabel("focus measure")
            axes[0].set_title(
                f"{r['setup']['objective']} {r['setup']['channel']}: {self.a.minutes:g} min idle, illumination off"
            )
            axes[1].plot(t, [p["mean"] for p in pts], "o-", color="tab:orange")
            axes[1].set_ylabel("mean intensity")
            if any(p.get("laser_af_um") is not None for p in pts):
                ax2 = axes[1].twinx()
                ax2.plot(t, [p.get("laser_af_um") for p in pts], "s--", color="tab:green")
                ax2.set_ylabel("laser AF displacement (um)")
            axes[1].set_xlabel("minutes")
            s = r["sweep"]
            axes[2].plot(s["z_um"], s["focus_measure"], "o-")
            axes[2].axvline(r["setup"]["z_ref_um"], color="k", ls="--", label="z_ref (before)")
            axes[2].axvline(
                s["z_peak_um"], color="r", ls=":", label=f"peak after idle ({r['result']['drift_um']:+.2f} um)"
            )
            axes[2].set_xlabel("z (um)")
            axes[2].set_ylabel("focus measure")
            axes[2].legend()
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=20.0, help="length of the dark idle")
    parser.add_argument(
        "--sample-every-s", type=float, default=120.0, help="one frame every N s during the idle; 0 = none"
    )
    parser.add_argument("--sweep-um", type=float, default=5.0, help="half-range of the final focus sweep")
    parser.add_argument("--sweep-steps", type=int, default=21)
    parser.add_argument("--channel", default=None, help="channel name; default: the first Fluorescence channel")
    parser.add_argument("--simulation", action="store_true", help="simulated hardware (exercises the code path only)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    args.out = args.out or f"pause_focus_bench_{int(args.minutes)}min"
    os.makedirs(args.out, exist_ok=True)

    app = QApplication.instance() or QApplication(["Squid"])  # noqa: F841 - Qt objects in the controllers
    squid.logging.add_file_logging(os.path.join(args.out, "squid_session.log"))
    scope = control.microscope.Microscope.build_from_global_config(args.simulation)
    bench = Bench(scope, args)
    code = 0
    try:
        bench.run()
    except Exception:  # noqa: BLE001
        bench.record["fatal"] = traceback.format_exc()
        print("BENCH CRASHED:\n" + bench.record["fatal"], flush=True)
        code = 1
    finally:
        bench.save()
        if "result" in bench.record:
            try:
                bench.figure()
            except Exception:  # noqa: BLE001
                print("figure failed:\n" + traceback.format_exc(), flush=True)
        try:
            bench.live.turn_off_illumination()
        except Exception:  # noqa: BLE001
            pass
        print("BENCH DONE ->", args.out, flush=True)
    logging.shutdown()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
