"""Standalone PyQt5 GUI to characterize a camera's hot/stuck/dead pixels from dark frames.

Run from software/:
    python tools/hot_pixel_test_gui.py --camera toupcam
    python tools/hot_pixel_test_gui.py --camera toupcam --simulated   # no hardware

The simulated camera emits random noise (not dark frames); use it only to exercise the UI
and capture/plumbing, not to validate detection.
"""

import argparse
import logging
import os
import sys
from typing import List, Optional

import numpy as np
from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

import squid.camera.utils
import squid.config
import squid.logging
from squid.abc import CameraAcquisitionMode
from squid.config import CameraPixelFormat
from squid.camera import hot_pixel_capture as cap
from squid.camera import hot_pixels as hp

log = squid.logging.get_logger("hot_pixel_test_gui")


def _parse_float_list(text: str) -> List[float]:
    return [float(p.strip()) for p in text.split(",") if p.strip()]


class CaptureWorker(QObject):
    """Runs all camera I/O off the GUI thread. Signals are queued (fire-and-forget)."""

    progress = pyqtSignal(str)
    log = pyqtSignal(str)
    snap_result = pyqtSignal(object)  # hp.DefectResult
    sweep_finished = pyqtSignal(object)  # hp.SweepSummary
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, camera, pixel_format: CameraPixelFormat):
        super().__init__()
        self._camera = camera
        self._pixel_format = pixel_format
        self._stop = False

    def request_stop(self):
        self._stop = True

    def _should_stop(self) -> bool:
        return self._stop

    def run_snap(self, exposure_ms: float, n_frames: int, thresholds: hp.DefectThresholds):
        self._stop = False
        try:
            max_value = hp.max_value_for_pixel_format(self._pixel_format)
            self.progress.emit(f"Capturing {n_frames} frames @ {exposure_ms:g} ms ...")
            stack = cap.capture_dark_stack(
                self._camera,
                exposure_ms,
                n_frames,
                should_stop=self._should_stop,
                on_frame=lambda i: self.progress.emit(f"Frame {i}/{n_frames}"),
            )
            if stack is None:
                self.log.emit("Snap cancelled.")
                return
            black_level = float(self._camera.get_black_level())
            warning = hp.darkness_check(stack.mean, black_level, max_value)
            if warning:
                self.log.emit("WARNING: " + warning)
            result = hp.detect_defects(stack.mean, stack.min_proj, stack.max_proj, max_value, thresholds, black_level)
            self.log.emit(
                f"Snap: {result.combined_count()} defects "
                f"(median {result.stats.median:.1f} DN, robust sigma {result.stats.sigma_robust:.2f})"
            )
            self.snap_result.emit(result)
        except Exception as e:  # surface, do not let Qt swallow it
            log.exception("Snap failed")
            self.error.emit(repr(e))
        finally:
            self.finished.emit()

    def run_sweep_job(
        self,
        exposures_ms: List[float],
        temperatures_c: Optional[List[float]],
        n_frames: int,
        thresholds: hp.DefectThresholds,
        output_dir: str,
        settle_tolerance_c: float,
        settle_timeout_s: float,
    ):
        self._stop = False
        try:
            black_level = float(self._camera.get_black_level())

            def on_progress(t, e):
                label = hp.condition_label(t, e)
                self.progress.emit(f"Capturing {label} ...")
                self.log.emit(f"-> {label}")

            results = cap.run_sweep(
                self._camera,
                exposures_ms,
                temperatures_c,
                n_frames,
                thresholds,
                self._pixel_format,
                black_level=black_level,
                should_stop=self._should_stop,
                on_progress=on_progress,
                settle_kwargs={"tolerance_c": settle_tolerance_c, "timeout_s": settle_timeout_s},
            )
            if not results:
                self.log.emit("Sweep produced no results (cancelled or empty).")
                return
            summary = hp.aggregate_sweep(results)
            os.makedirs(output_dir, exist_ok=True)
            metadata = {
                "pixel_format": self._pixel_format.value,
                "n_frames": n_frames,
                "exposures_ms": exposures_ms,
                "temperatures_c": temperatures_c,
                "sigma_n": thresholds.sigma_n,
                "abs_threshold_dn": thresholds.abs_threshold_dn,
            }
            hp.write_defect_csv(summary, os.path.join(output_dir, "defects.csv"))
            hp.write_summary_json(summary, metadata, os.path.join(output_dir, "summary.json"))
            for c in results:
                fig = hp.render_defect_map(
                    c.result,
                    c.result.masks[hp.DefectType.HOT_STATISTICAL].shape,
                    title=hp.condition_label(c.temperature_c, c.exposure_ms),
                )
                fig.savefig(
                    os.path.join(
                        output_dir,
                        f"map_{hp.condition_label(c.temperature_c, c.exposure_ms).replace(',', '_')}.png",
                    )
                )
            hp.render_count_vs_exposure(summary).savefig(os.path.join(output_dir, "count_vs_exposure.png"))
            self.log.emit(f"Sweep complete. Artifacts written to {output_dir}")
            self.sweep_finished.emit(summary)
        except Exception as e:
            log.exception("Sweep failed")
            self.error.emit(repr(e))
        finally:
            self.finished.emit()


class HotPixelWindow(QMainWindow):
    def __init__(self, camera, pixel_format: CameraPixelFormat):
        super().__init__()
        self.setWindowTitle("Hot-Pixel Characterization")
        self._camera = camera
        self._pixel_format = pixel_format
        self._original_temperature = self._safe_get_temperature()

        self._thread = QThread()
        self._worker = CaptureWorker(camera, pixel_format)
        self._worker.moveToThread(self._thread)
        self._thread.start()

        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._append_log)
        self._worker.error.connect(lambda m: self._append_log("ERROR: " + m))
        self._worker.snap_result.connect(self._show_snap_result)
        self._worker.finished.connect(self._on_job_finished)

        tabs = QTabWidget()
        tabs.addTab(self._build_snap_tab(), "Snap & Inspect")
        tabs.addTab(self._build_sweep_tab(), "Batch Sweep")

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.addWidget(tabs)
        self._status = QLabel("Ready")
        outer.addWidget(self._status)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        outer.addWidget(self._log_view)
        self.setCentralWidget(central)

    # ---- shared helpers ----
    def _safe_get_temperature(self):
        try:
            return self._camera.get_temperature()
        except Exception:
            return None

    def _thresholds_from(self, sigma_spin, abs_check, abs_spin) -> hp.DefectThresholds:
        return hp.DefectThresholds(
            sigma_n=sigma_spin.value(),
            abs_threshold_dn=int(abs_spin.value()) if abs_check.isChecked() else None,
        )

    def _append_log(self, text: str):
        self._log_view.appendPlainText(text)

    def _on_progress(self, text: str):
        self._status.setText(text)

    def _set_running(self, running: bool):
        self._snap_button.setEnabled(not running)
        self._run_button.setEnabled(not running)
        self._stop_button.setEnabled(running)

    def _on_job_finished(self):
        self._set_running(False)
        self._status.setText("Ready")

    # ---- snap tab ----
    def _build_snap_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self._snap_exposure = QDoubleSpinBox()
        self._snap_exposure.setRange(0.001, 60000.0)
        self._snap_exposure.setValue(100.0)
        self._snap_frames = QSpinBox()
        self._snap_frames.setRange(1, 1000)
        self._snap_frames.setValue(20)
        self._snap_sigma = QDoubleSpinBox()
        self._snap_sigma.setRange(0.0, 100.0)
        self._snap_sigma.setValue(5.0)
        self._snap_abs_check = QCheckBox("Use absolute DN threshold")
        self._snap_abs = QSpinBox()
        self._snap_abs.setRange(0, 65535)
        self._snap_abs.setValue(1000)
        self._snap_button = QPushButton("Capture")
        self._snap_button.clicked.connect(self._do_snap)
        self._snap_canvas = FigureCanvasQTAgg(_empty_figure())
        form.addRow("Exposure (ms)", self._snap_exposure)
        form.addRow("Frames", self._snap_frames)
        form.addRow("Sigma N", self._snap_sigma)
        form.addRow(self._snap_abs_check)
        form.addRow("Absolute DN", self._snap_abs)
        form.addRow(self._snap_button)
        form.addRow(self._snap_canvas)
        return w

    def _do_snap(self):
        self._set_running(True)
        thresholds = self._thresholds_from(self._snap_sigma, self._snap_abs_check, self._snap_abs)
        # Invoke the worker slot on its own thread via a queued call.
        from PyQt5.QtCore import QMetaObject, Qt, Q_ARG

        QMetaObject.invokeMethod(
            self._worker,
            "run_snap",
            Qt.QueuedConnection,
            Q_ARG(float, self._snap_exposure.value()),
            Q_ARG(int, self._snap_frames.value()),
            Q_ARG(object, thresholds),
        )

    def _show_snap_result(self, result: hp.DefectResult):
        fig = hp.render_defect_map(result, result.masks[hp.DefectType.HOT_STATISTICAL].shape, title="Snap defect map")
        self._replace_canvas("_snap_canvas", fig)

    # ---- sweep tab ----
    def _build_sweep_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self._sweep_exposures = QLineEdit("1,10,100,500,1000")
        self._sweep_temps = QLineEdit("")  # empty = ambient
        self._sweep_frames = QSpinBox()
        self._sweep_frames.setRange(1, 1000)
        self._sweep_frames.setValue(20)
        self._sweep_sigma = QDoubleSpinBox()
        self._sweep_sigma.setRange(0.0, 100.0)
        self._sweep_sigma.setValue(5.0)
        self._sweep_abs_check = QCheckBox("Use absolute DN threshold")
        self._sweep_abs = QSpinBox()
        self._sweep_abs.setRange(0, 65535)
        self._sweep_abs.setValue(1000)
        self._sweep_tol = QDoubleSpinBox()
        self._sweep_tol.setRange(0.1, 10.0)
        self._sweep_tol.setValue(1.0)
        self._sweep_timeout = QDoubleSpinBox()
        self._sweep_timeout.setRange(1.0, 3600.0)
        self._sweep_timeout.setValue(300.0)
        self._output_dir = QLineEdit(os.path.abspath("hot_pixel_results"))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_output)
        outrow = QHBoxLayout()
        outrow.addWidget(self._output_dir)
        outrow.addWidget(browse)
        outwrap = QWidget()
        outwrap.setLayout(outrow)

        self._run_button = QPushButton("Run sweep")
        self._run_button.clicked.connect(self._do_sweep)
        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._worker.request_stop)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # busy indicator while running
        self._progress.reset()

        form.addRow("Exposures (ms, comma)", self._sweep_exposures)
        form.addRow("Temperatures (C, comma; empty=ambient)", self._sweep_temps)
        form.addRow("Frames", self._sweep_frames)
        form.addRow("Sigma N", self._sweep_sigma)
        form.addRow(self._sweep_abs_check)
        form.addRow("Absolute DN", self._sweep_abs)
        form.addRow("Settle tol (C)", self._sweep_tol)
        form.addRow("Settle timeout (s)", self._sweep_timeout)
        form.addRow("Output dir", outwrap)
        form.addRow(self._run_button)
        form.addRow(self._stop_button)
        form.addRow(self._progress)
        return w

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory", self._output_dir.text())
        if d:
            self._output_dir.setText(d)

    def _do_sweep(self):
        from PyQt5.QtCore import QMetaObject, Qt, Q_ARG
        from datetime import datetime

        try:
            exposures = _parse_float_list(self._sweep_exposures.text())
            temps = _parse_float_list(self._sweep_temps.text())
        except ValueError:
            self._append_log("ERROR: could not parse exposure/temperature lists.")
            return
        if not exposures:
            self._append_log("ERROR: provide at least one exposure.")
            return
        thresholds = self._thresholds_from(self._sweep_sigma, self._sweep_abs_check, self._sweep_abs)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = os.path.join(self._output_dir.text(), stamp)
        self._set_running(True)
        QMetaObject.invokeMethod(
            self._worker,
            "run_sweep_job",
            Qt.QueuedConnection,
            Q_ARG(object, exposures),
            Q_ARG(object, temps if temps else None),
            Q_ARG(int, self._sweep_frames.value()),
            Q_ARG(object, thresholds),
            Q_ARG(str, output_dir),
            Q_ARG(float, self._sweep_tol.value()),
            Q_ARG(float, self._sweep_timeout.value()),
        )

    def _replace_canvas(self, attr: str, fig):
        old = getattr(self, attr)
        new = FigureCanvasQTAgg(fig)
        parent_layout = old.parent().layout()
        parent_layout.replaceWidget(old, new)
        old.deleteLater()
        setattr(self, attr, new)

    def closeEvent(self, event):
        # Cleanup: stop worker, wait for in-flight capture, restore TEC, stop streaming, close camera.
        try:
            self._worker.request_stop()
            self._thread.quit()
            self._thread.wait(10000)
            if self._original_temperature is not None:
                try:
                    self._camera.set_temperature(self._original_temperature)
                except Exception:
                    log.warning("Could not restore original temperature on close")
            self._camera.stop_streaming()
            try:
                self._camera.close()
            except Exception:
                pass
        finally:
            super().closeEvent(event)


def _empty_figure():
    import matplotlib

    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure

    fig = Figure(figsize=(6, 6))
    fig.add_subplot(111).set_title("No capture yet")
    return fig


def parse_args(argv):
    ap = argparse.ArgumentParser(description="GUI hot-pixel characterization tool.")
    ap.add_argument("--camera", default="toupcam", help="Camera type (default: toupcam).")
    ap.add_argument("--simulated", action="store_true", help="Use the simulated camera (no hardware).")
    ap.add_argument("--verbose", action="store_true", help="DEBUG logging.")
    return ap.parse_args(argv)


def _build_camera(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)
    pixel_format = CameraPixelFormat.MONO12
    config = squid.config.get_camera_config().model_copy(
        update={
            "camera_type": squid.config.CameraVariant.from_string(args.camera),
            "default_pixel_format": pixel_format,
            "default_binning": (1, 1),
        }
    )
    camera = squid.camera.utils.get_camera(config, simulated=args.simulated)
    try:
        camera.set_pixel_format(pixel_format)
    except Exception:
        pixel_format = camera.get_pixel_format()
    camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    camera.start_streaming()
    return camera, pixel_format


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    app = QApplication(sys.argv)
    camera, pixel_format = _build_camera(args)
    window = HotPixelWindow(camera, pixel_format)
    window.resize(900, 800)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
