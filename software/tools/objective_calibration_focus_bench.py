"""Hardware bench for the objective-calibration focus sweep (squid.objective_calibration.focus).

Run from software/ with the machine ini in place, the GUI closed, one objective in the light path and
the sample hand-focused on the calibration target:

    python tools/objective_calibration_focus_bench.py random --trials 12 --range-um 100 --out bench_random
    python tools/objective_calibration_focus_bench.py repeat --runs 50 --range-um 100 --out bench_repeat

Both modes start the app like main_hcs.py (no MCP server), wait for startup, take a reference focus
from the hand-focused Z (z_ref), run the trials in a worker thread, move back to z_ref, restore the
live channel, and write <out>/record.json, <out>/images/*.png and <out>/figure.pdf.

random  each trial starts at z_ref + a seeded random offset in ±1.5 x range, so about a third of the
        trials must be refused "at the edge" (the contract) and the rest must focus.
        figure: page 1 time vs |offset| and found Z - z_ref vs offset; pages 2+ before | after frames.
repeat  every run starts at z_ref (approached from below) and focuses again.
        figure: Z per run with a straight-line fit, histogram of Z, time per run. The title carries
        the raw Z std, the drift (the fit's slope) and the std about the fit: a stage that creeps
        during the run is reported as drift, not charged to the sweep.
Every record carries, per run, the coarse sweep's peak_sigmas and peak_rise, the values that decide
the flat-field refusal, so a new sample class can be judged from the numbers.
"""

import argparse
import json
import logging
import os
import random
import sys
import threading
import time
import traceback
from datetime import datetime

os.environ.setdefault("QT_API", "pyqt5")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from qtpy.QtCore import QTimer  # noqa: E402
from qtpy.QtWidgets import QApplication, QMessageBox  # noqa: E402

import squid.logging  # noqa: E402

squid.logging.setup_uncaught_exception_logging()
import control._def  # noqa: E402,F401
import control.gui_hcs as gui  # noqa: E402
import control.microscope  # noqa: E402
import control.utils  # noqa: E402
from control.single_instance import acquire_single_instance_lock  # noqa: E402
from tools.migrate_acquisition_configs import run_auto_migration  # noqa: E402

log = logging.getLogger("focus_bench")
APPROACH_UM = 2.0  # every start position is approached from below, as the sweep approaches its targets


def pick_channel(names):
    for want in ("BF LED matrix full", "BF LED matrix low NA", "BF"):
        for n in names:
            if n.startswith(want):
                return n
    return names[0] if names else None


def to_png(image, path, crop_px=600):
    h, w = image.shape[:2]
    side = min(crop_px, h, w)
    r0, c0 = (h - side) // 2, (w - side) // 2
    crop = np.asarray(image[r0 : r0 + side, c0 : c0 + side], dtype=np.float32)
    lo, hi = np.percentile(crop, (0.5, 99.5))
    crop8 = np.clip((crop - lo) / max(hi - lo, 1.0) * 255.0, 0, 255).astype(np.uint8)
    cv2.imwrite(path, crop8)
    return crop8


def linear_drift(runs):
    """Straight-line fit of focus Z against the time each run started, over the focused runs: drift in
    um/min, raw std, and std about the fit. None with fewer than 3 runs."""
    ok = [t for t in runs if not t["error"]]
    if len(ok) < 3:
        return None
    t_min = np.array([t["t_s"] for t in ok]) / 60.0
    z = np.array([t["z_um"] for t in ok])
    slope, intercept = np.polyfit(t_min, z, 1)
    return {
        "um_per_min": float(slope),
        "intercept_um": float(intercept),
        "raw_std_um": float(z.std(ddof=1)),
        "detrended_std_um": float((z - (slope * t_min + intercept)).std(ddof=1)),
    }


class Bench:
    def __init__(self, win, microscope, args):
        self.win, self.scope, self.args = win, microscope, args
        self.record = {"started": datetime.now().isoformat(timespec="seconds"), "args": vars(args), "runs": []}
        self.done = threading.Event()
        self.started = time.monotonic()
        self.worker = None
        self.timer = QTimer()
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    # ------------------------------------------------------------ scheduling
    def tick(self):
        if self.worker is None:
            if time.monotonic() - self.started < self.args.startup_s:
                return
            busy = getattr(self.win, "objective_calibration_busy_reason", lambda: None)()
            if busy:
                log.info("waiting for startup: %s", busy)
                return
            self.worker = threading.Thread(target=self.run, name="focus-bench", daemon=True)
            self.worker.start()
        elif self.done.is_set():
            self.timer.stop()
            self.save()
            try:
                self.figure()
            except Exception:  # noqa: BLE001
                log.error("figure failed:\n%s", traceback.format_exc())
            print("BENCH DONE ->", self.args.out, flush=True)
            gui.QMessageBox.question = lambda *a, **k: QMessageBox.Yes  # auto-confirm "Confirm Exit"
            QTimer.singleShot(2000, self.win.close)

    def save(self):
        with open(os.path.join(self.args.out, "record.json"), "w", encoding="utf-8") as f:
            json.dump(self.record, f, indent=1, default=str)

    # ------------------------------------------------------------------- run
    def run(self):
        try:
            self._run()
        except Exception:  # noqa: BLE001
            self.record["fatal"] = traceback.format_exc()
            log.error("BENCH CRASHED:\n%s", self.record["fatal"])
        finally:
            self.done.set()

    def _run(self):
        import squid.config
        from control.objective_calibration_hardware import MicroscopeCalibrationHardware
        from squid.objective_calibration.focus import FocusError, autofocus, depth_of_field_um, peak_sigmas

        a = self.args
        scope = self.scope
        objective = scope.objective_store.current_objective
        na = float(scope.objective_store.objectives_dict[objective]["NA"])
        channel = a.channel or pick_channel([c.name for c in scope.live_controller.get_channels(objective)])
        hw = MicroscopeCalibrationHardware(scope, camera_config=squid.config.get_camera_config())
        self.record["setup"] = {
            "mode": a.mode,
            "objective": objective,
            "na": na,
            "dof_um": depth_of_field_um(na),
            "channel": channel,
            "z_hand_focus_um": hw.get_z_um(),
            "z_limits_um": hw.z_limits_um(),
            "frame_shape": hw.frame_shape(channel),
            "repo_state": control.utils.get_squid_repo_state_description(),
        }
        log.info("setup: %s", self.record["setup"])
        images = os.path.join(a.out, "images")
        os.makedirs(images, exist_ok=True)

        t_start = time.perf_counter()

        def focus(label):
            t0 = time.perf_counter()
            try:
                result = autofocus(hw, objective=objective, channel=channel, na=na, range_um=a.range_um)
                coarse = result.levels[0]
                out = {
                    "z_um": result.z_best_um,
                    "elapsed_s": time.perf_counter() - t0,
                    "peak_rise": result.peak_rise,
                    "peak_sigmas": peak_sigmas(coarse.values),
                    "frames": sum(len(lv.values) for lv in result.levels),
                    "levels": [[lv.metric, lv.z_um, lv.values] for lv in result.levels],
                    "error": None,
                    "t_s": t0 - t_start,
                }
                print(
                    f"{label}: focus {result.z_best_um:.2f} um in {out['elapsed_s']:.1f} s "
                    f"({out['frames']} frames, peak {out['peak_sigmas']:.0f} sigma, rise {result.peak_rise:.1f}x)",
                    flush=True,
                )
            except FocusError as e:
                out = {"z_um": hw.get_z_um(), "elapsed_s": time.perf_counter() - t0, "error": str(e), "t_s": t0 - t_start}
                print(f"{label}: REFUSED after {out['elapsed_s']:.1f} s: {e}", flush=True)
            return out

        def go_to(z_um):
            hw.move_z_to_um(z_um - APPROACH_UM)
            hw.move_z_to_um(z_um)

        ref = focus("reference")
        self.record["reference"] = ref
        if ref["error"]:
            raise RuntimeError(f"reference autofocus refused: {ref['error']}")
        z_ref = ref["z_um"]
        to_png(hw.snap(objective, channel), os.path.join(images, "reference.png"))

        if a.mode == "random":
            rng = random.Random(a.seed)
            for i in range(1, a.trials + 1):
                offset = rng.uniform(-1.5 * a.range_um, 1.5 * a.range_um)
                go_to(z_ref + offset)
                before = to_png(hw.snap(objective, channel), os.path.join(images, f"trial_{i:02d}_before.png"))
                res = focus(f"trial {i:02d} (start {offset:+.1f} um from focus)")
                after = to_png(hw.snap(objective, channel), os.path.join(images, f"trial_{i:02d}_after.png"))
                res.update(
                    {
                        "run": i,
                        "offset_um": offset,
                        "expected": "focus" if abs(offset) < a.range_um else "refuse",
                        "z_error_um": (res["z_um"] - z_ref) if not res["error"] else None,
                        "before_sharpness": float(cv2.Laplacian(before, cv2.CV_32F).var()),
                        "after_sharpness": float(cv2.Laplacian(after, cv2.CV_32F).var()),
                    }
                )
                self.record["runs"].append(res)
                self.save()
        else:
            for i in range(1, a.runs + 1):
                go_to(z_ref)
                res = focus(f"run {i:02d}")
                res.update({"run": i, "z_error_um": (res["z_um"] - z_ref) if not res["error"] else None})
                self.record["runs"].append(res)
                self.save()

        go_to(z_ref)
        hw.restore_mode()
        if a.mode == "repeat":
            self.record["drift"] = linear_drift(self.record["runs"])
            if self.record["drift"]:
                d = self.record["drift"]
                print(f"Z drift {d['um_per_min']:+.3f} um/min; std {d['raw_std_um']:.3f} um raw, {d['detrended_std_um']:.3f} um about the fit", flush=True)
        self.record["finished"] = datetime.now().isoformat(timespec="seconds")

    # ---------------------------------------------------------------- figure
    def figure(self):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages

        runs = self.record["runs"]
        ok = [t for t in runs if not t["error"]]
        bad = [t for t in runs if t["error"]]
        s = self.record["setup"]
        head = f"{s['objective']} NA {s['na']}, {s['channel']}, ±{self.args.range_um:g} µm, {datetime.now():%Y-%m-%d}"
        with PdfPages(os.path.join(self.args.out, "figure.pdf")) as pdf:
            if self.args.mode == "random":
                self._figure_random(pdf, plt, runs, ok, bad, head)
            else:
                self._figure_repeat(pdf, plt, runs, ok, bad, head, s["dof_um"])

    def _figure_random(self, pdf, plt, runs, ok, bad, head):
        images = os.path.join(self.args.out, "images")
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
        ax1.scatter([abs(t["offset_um"]) for t in ok], [t["elapsed_s"] for t in ok], c="tab:blue", label="focused")
        if bad:
            ax1.scatter(
                [abs(t["offset_um"]) for t in bad],
                [t["elapsed_s"] for t in bad],
                c="tab:red",
                marker="x",
                label="refused",
            )
        ax1.axvline(self.args.range_um, color="0.6", lw=0.8)
        ax1.set_xlabel("|start offset from focus| (µm)")
        ax1.set_ylabel("time (s)")
        ax1.set_title("Time to focus or refusal")
        ax1.legend()
        ax2.scatter([t["offset_um"] for t in ok], [t["z_error_um"] for t in ok], c="tab:blue")
        ax2.axhline(0, color="0.6", lw=0.8)
        ax2.set_xlabel("start offset from focus (µm)")
        ax2.set_ylabel("found Z − reference Z (µm)")
        ax2.set_title("Accuracy")
        wrong = [t for t in runs if (t["expected"] == "focus") == bool(t["error"])]
        fig.suptitle(f"{head}: {len(ok)} focused, {len(bad)} refused, {len(wrong)} against the contract")
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)
        per_page = 3
        for p in range(0, len(runs), per_page):
            chunk = runs[p : p + per_page]
            fig, axes = plt.subplots(len(chunk), 2, figsize=(8, 3.6 * len(chunk)), squeeze=False)
            for row, t in zip(axes, chunk):
                for ax, kind in zip(row, ("before", "after")):
                    path = os.path.join(images, f"trial_{t['run']:02d}_{kind}.png")
                    ax.imshow(cv2.imread(path, cv2.IMREAD_GRAYSCALE), cmap="gray", vmin=0, vmax=255)
                    ax.set_xticks([])
                    ax.set_yticks([])
                row[0].set_title(f"trial {t['run']}: start {t['offset_um']:+.1f} µm from focus", fontsize=9)
                if t["error"]:
                    row[1].set_title(f"refused after {t['elapsed_s']:.1f} s", color="tab:red", fontsize=9)
                else:
                    row[1].set_title(
                        f"focused at {t['z_um']:.2f} µm (Δ {t['z_error_um']:+.2f}) in {t['elapsed_s']:.1f} s",
                        fontsize=9,
                    )
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

    def _figure_repeat(self, pdf, plt, runs, ok, bad, head, dof_um):
        z = np.array([t["z_um"] for t in ok])
        drift = self.record.get("drift") or {}
        fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
        axes[0].plot([t["run"] for t in ok], z, "o-", ms=3)
        if drift:
            t_min = np.array([t["t_s"] for t in ok]) / 60.0
            axes[0].plot([t["run"] for t in ok], drift["um_per_min"] * t_min + drift["intercept_um"], "--", color="0.4", lw=1)
        axes[0].set_xlabel("run")
        axes[0].set_ylabel("focus Z (µm)")
        axes[0].set_title("Z per run" + (f": drift {drift['um_per_min']:+.3f} µm/min" if drift else ""))
        axes[1].hist(z, bins=min(20, max(5, len(z) // 3)))
        axes[1].set_xlabel("focus Z (µm)")
        std = z.std(ddof=1) if len(z) > 1 else 0
        axes[1].set_title(f"std {std:.3f} µm raw" + (f", {drift['detrended_std_um']:.3f} about the fit" if drift else "") + f"; DOF {dof_um:.2f} µm")
        axes[2].plot([t["run"] for t in ok], [t["elapsed_s"] for t in ok], "o-", ms=3)
        axes[2].set_xlabel("run")
        axes[2].set_ylabel("time (s)")
        axes[2].set_title("Time per run")
        fig.suptitle(f"{head}: {len(ok)} focused, {len(bad)} refused")
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["random", "repeat"])
    parser.add_argument("--trials", type=int, default=12, help="random: number of random-start trials")
    parser.add_argument("--runs", type=int, default=50, help="repeat: number of runs from the reference focus")
    parser.add_argument("--range-um", type=float, default=100.0, help="autofocus search half-range")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--channel", default=None, help="channel name; default: the first BF LED matrix channel")
    parser.add_argument("--startup-s", type=float, default=8.0, help="seconds to wait before polling startup state")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    args.out = args.out or f"focus_bench_{args.mode}"
    os.makedirs(args.out, exist_ok=True)

    app = QApplication(["Squid"])
    app.setStyle("Fusion")
    lock_result = acquire_single_instance_lock()
    if lock_result.lock is None:
        print(f"single-instance lock busy={lock_result.busy} path={lock_result.path}; close Squid first", flush=True)
        sys.exit(2)
    app.aboutToQuit.connect(lock_result.lock.unlock)
    squid.logging.add_file_logging(f"{squid.logging.get_default_log_directory()}/main_hcs.log")
    squid.logging.add_file_logging(os.path.join(args.out, "squid_session.log"))
    log.info("focus bench. Squid repository state: %s", control.utils.get_squid_repo_state_description())
    run_auto_migration()
    microscope = control.microscope.Microscope.build_from_global_config(False)
    win = gui.HighContentScreeningGui(microscope=microscope, is_simulation=False)
    win.showMaximized()
    bench = Bench(win, microscope, args)  # noqa: F841 - kept alive by the reference
    code = app.exec_()
    logging.shutdown()
    os._exit(code)


if __name__ == "__main__":
    main()
