"""Utils > Objective Calibration...: measure each objective's pixel size and pixel->stage matrix.

AI-docs objective-pixel-size design §6.1. The engine (squid/objective_calibration) runs in a QThread
on the hardware adapter; every cycle, however it ends, puts the objective, XY and Z back. Nothing is
written until "Apply and save", which replaces only the measured objectives' pixel_size blocks in
machine_configs/objective_calibration.yaml. This version records the calibration; nothing applies it
yet.
"""

from datetime import datetime
from typing import Callable, Dict, List, Optional

from qtpy.QtCore import QMetaObject, Qt, QThread, Signal, Slot
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

import squid.logging
from control.models.objective_calibration_config import (
    CurrentSetup,
    ImageTransform,
    ObjectiveCalibrationFileError,
    build_pixel_record,
    clear_pixel_records,
    merge_pixel_records,
    pixel_size_validity,
    review_save,
    with_summary,
)
from squid.objective_calibration.engine import ObjectiveSpec, RunConfig, RunResult, cycle_report, run_calibration
from squid.objective_calibration.hardware import RunCancelled

log = squid.logging.get_logger(__name__)

RUNNING_MESSAGE = (
    "A calibration is running. Cancel it and wait for the stage and objective to be put back before closing."
)
GUIDANCE = (
    "Use a flat, textured, non-periodic sample (a stained section, or a region of a USAF target containing "
    "several bar groups of different sizes: a single group is periodic, and bare glass has no texture), "
    "roughly in focus on the current objective, with the stage away from its travel limits. "
    "Saved calibrations are recorded for review; this version does not apply them yet."
)
COLUMNS = [
    "Objective",
    "Pixel size (µm)",
    "± std (µm)",
    "vs nominal",
    "Anisotropy",
    "Rotation (°)",
    "Fit residual (µm)",
    "Saved (µm)",
    "Status",
]


class CalibrationWorker(QThread):
    """Runs the engine off the GUI thread. Never raises into Qt: the RunResult, or the exception that
    ended the run, comes back through signal_finished."""

    signal_progress = Signal(str)
    signal_finished = Signal(object)

    def __init__(self, hardware, config: RunConfig, fine_metric, after_run=None, parent=None):
        super().__init__(parent)
        self.hardware = hardware
        self.config = config
        self.fine_metric = fine_metric
        self.after_run = after_run
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            result = run_calibration(
                self.hardware,
                self.config,
                fine_metric=self.fine_metric,
                progress=self.signal_progress.emit,
                should_cancel=lambda: self._cancel,
            )
        except Exception as e:  # noqa: BLE001 - reported in the dialog
            log.error("Objective calibration failed", exc_info=True)
            result = e
        if self.after_run is not None:
            try:
                self.after_run()
            except Exception:  # noqa: BLE001
                log.error("Restoring the live channel after calibration failed", exc_info=True)
        self.signal_finished.emit(result)


class _PromptedSwitch:
    """The hardware, with every objective switch first confirmed by the operator (no motorized changer)."""

    def __init__(self, hardware, ask: Callable[[str], bool]):
        self._hardware = hardware
        self._ask = ask

    def __getattr__(self, name):
        return getattr(self._hardware, name)

    def switch_objective(self, name: str) -> None:
        if name != self._hardware.current_objective() and not self._ask(name):
            raise RunCancelled("Objective switch declined")
        self._hardware.switch_objective(name)


class ObjectiveCalibrationDialog(QDialog):
    def __init__(
        self,
        hardware,
        specs: List[ObjectiveSpec],
        channels: List[str],
        config_repo,
        *,
        tube_lens_mm: float,
        get_declared: Callable[[str], dict],
        fine_metric: Callable,
        manual_switch: bool = False,
        default_channel: Optional[str] = None,
        busy_reason: Optional[Callable[[], Optional[str]]] = None,
        after_run: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Objective Calibration")
        self.hardware = hardware
        self.specs = {spec.name: spec for spec in specs}
        self.config_repo = config_repo
        self.tube_lens_mm = tube_lens_mm
        self._get_declared = get_declared
        self.fine_metric = fine_metric
        self.manual_switch = manual_switch
        self.busy_reason = busy_reason
        self.after_run = after_run
        self.worker: Optional[CalibrationWorker] = None
        self.result: Optional[RunResult] = None
        self._switch_target = ""
        self._switch_answer = False
        try:
            self.saved = config_repo.get_objective_calibration()
            self.file_error = ""
        except ObjectiveCalibrationFileError as e:
            self.saved = None
            self.file_error = str(e)
        self._build(channels, default_channel)
        self._refresh_saved()
        self._set_running(False)

    # ---------------------------------------------------------------- layout
    def _build(self, channels, default_channel):
        inputs = QGroupBox("Calibrate")
        form = QFormLayout(inputs)
        row = QHBoxLayout()
        self.checkboxes: Dict[str, QCheckBox] = {}
        for name in sorted(self.specs, key=lambda n: self.specs[n].magnification):
            box = QCheckBox(name)
            box.setChecked(True)
            self.checkboxes[name] = box
            row.addWidget(box)
        form.addRow("Objectives", row)
        self.combo_channel = QComboBox()
        self.combo_channel.addItems(channels)
        if default_channel in channels:
            self.combo_channel.setCurrentText(default_channel)
        form.addRow("Channel", self.combo_channel)
        self.spin_range = QDoubleSpinBox()
        self.spin_range.setRange(10.0, 1000.0)
        self.spin_range.setValue(100.0)
        self.spin_range.setPrefix("± ")
        self.spin_range.setSuffix(" µm")
        form.addRow("Focus search range", self.spin_range)
        self.spin_cycles = QSpinBox()
        self.spin_cycles.setRange(1, 5)
        self.spin_cycles.setValue(3)
        form.addRow("Cycles", self.spin_cycles)
        self.checkbox_pixel_size = QCheckBox("Pixel size")
        self.checkbox_pixel_size.setChecked(True)
        self.checkbox_pixel_size.setEnabled(False)  # the only measurement until C1 adds Offsets
        form.addRow("Measure", self.checkbox_pixel_size)

        guidance = QLabel(GUIDANCE)
        guidance.setWordWrap(True)

        saved_box = QGroupBox("Saved calibration")
        saved_layout = QVBoxLayout(saved_box)
        self.label_file = QLabel("")
        self.label_file.setWordWrap(True)
        self.label_file.setStyleSheet("color: red")
        saved_layout.addWidget(self.label_file)
        self.validity_labels: Dict[str, QLabel] = {}
        for name in self.checkboxes:
            label = QLabel("")
            self.validity_labels[name] = label
            saved_layout.addWidget(label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.label_orientation = QLabel("")
        self.label_result = QLabel("")
        self.label_result.setWordWrap(True)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(120)

        buttons = QHBoxLayout()
        self.button_calibrate = QPushButton("Calibrate")
        self.button_cancel = QPushButton("Cancel")
        self.button_apply = QPushButton("Apply and save")
        self.button_clear = QPushButton("Clear pixel size")
        self.button_close = QPushButton("Close")
        for button in (
            self.button_calibrate,
            self.button_cancel,
            self.button_apply,
            self.button_clear,
            self.button_close,
        ):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        self.button_calibrate.clicked.connect(self._start)
        self.button_cancel.clicked.connect(self._cancel)
        self.button_apply.clicked.connect(self._apply)
        self.button_clear.clicked.connect(self._clear)
        self.button_close.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        for widget in (
            inputs,
            guidance,
            saved_box,
            self.table,
            self.label_orientation,
            self.label_result,
            self.log_view,
        ):
            layout.addWidget(widget)
        layout.addLayout(buttons)

    # ---------------------------------------------------------------- state
    def _running(self) -> bool:
        # Until _finished has run, not merely until the thread exits: in between, the result is not
        # applied yet, and a new run started there would have its worker cleared by the old _finished.
        return self.worker is not None

    def _say(self, message: str):
        self.label_result.setText(message)
        self.log_view.append(message)

    def _set_running(self, running: bool):
        self.button_calibrate.setEnabled(not running)
        self.button_cancel.setEnabled(running)
        self.button_apply.setEnabled(not running and self._can_apply())
        self.button_clear.setEnabled(not running and not self.file_error)
        self.button_close.setEnabled(not running)

    def _can_apply(self) -> bool:
        return (
            not self.file_error
            and self.result is not None
            and self.result.stopped is None
            and bool(self.result.pixel_sizes)
        )

    def _declared(self, name: str) -> Optional[dict]:
        try:
            return self._get_declared(name)
        except KeyError:  # get_declared's contract for an objective no longer in the list
            return None

    def _setup(self) -> CurrentSetup:
        rotate_deg, flip = self.hardware.image_transform()
        return CurrentSetup(
            self.hardware.camera_key(),
            self.hardware.unbinned_sensor_pixel_um(),
            self.tube_lens_mm,
            ImageTransform(rotate_deg=rotate_deg, flip=flip),
        )

    def _refresh_saved(self):
        self.label_file.setText(self.file_error)
        self.label_file.setVisible(bool(self.file_error))
        setup = self._setup()
        for name, label in self.validity_labels.items():
            entry = self.saved.objectives.get(name) if self.saved is not None else None
            record = entry.pixel_size if entry is not None else None
            validity = pixel_size_validity(record, setup, self._declared(name))
            if record is None:
                label.setText(f"{name}: not calibrated")
            elif validity.directional:
                label.setText(f"{name}: {record.pixel_size_um:.4f} µm/px, measured {record.measured_at}: valid")
            elif validity.scalar:
                label.setText(f"{name}: {record.pixel_size_um:.4f} µm/px: scale valid, XY invalid ({validity.reason})")
            else:
                label.setText(f"{name}: {record.pixel_size_um:.4f} µm/px: invalid ({validity.reason})")

    # ---------------------------------------------------------------- run
    def _start(self):
        if self._running():
            return
        reason = self.busy_reason() if self.busy_reason is not None else None
        if reason:
            self._say(f"Not started. {reason}")
            return
        selected = [self.specs[name] for name, box in self.checkboxes.items() if box.isChecked()]
        if not selected:
            self._say("Not started. Select at least one objective.")
            return
        config = RunConfig(
            selected,
            self.combo_channel.currentText(),
            search_range_um=self.spin_range.value(),
            cycles=self.spin_cycles.value(),
        )
        hardware = _PromptedSwitch(self.hardware, self._ask_switch) if self.manual_switch else self.hardware
        self.result = None
        self.table.setRowCount(0)
        self.label_orientation.setText("")
        self.log_view.clear()
        self._say("Calibrating...")
        self.worker = CalibrationWorker(hardware, config, self.fine_metric, after_run=self.after_run, parent=self)
        self.worker.signal_progress.connect(self.log_view.append)
        self.worker.signal_finished.connect(self._finished)
        self._set_running(True)
        self.worker.start()

    def _ask_switch(self, name: str) -> bool:
        """Called from the worker thread; blocks until the operator answers on the GUI thread."""
        self._switch_target = name
        self._switch_answer = False
        QMetaObject.invokeMethod(self, "_prompt_switch", Qt.BlockingQueuedConnection)
        return self._switch_answer

    @Slot()
    def _prompt_switch(self):
        # Exceptions in a BlockingQueuedConnection slot are swallowed by Qt: catch and log them here.
        try:
            reply = QMessageBox.question(
                self,
                "Switch objective",
                f"Switch to {self._switch_target} by hand, then press OK.\n"
                "Cancel stops the calibration and puts the stage back.",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Ok,
            )
            self._switch_answer = reply == QMessageBox.Ok
        except Exception:  # noqa: BLE001
            log.error("The manual objective switch prompt failed", exc_info=True)
            self._switch_answer = False

    def _cancel(self):
        if self._running():
            self._say(
                "Cancelling: the run stops before its next frame (a stage move or objective switch in progress "
                "finishes first), then the stage and objective are put back."
            )
            self.worker.cancel()
            self.button_cancel.setEnabled(False)

    def _finished(self, result):
        self.worker = None
        if isinstance(result, Exception):
            self._say(f"Calibration failed: {result}. Check the stage and objective before continuing.")
        else:
            self.result = result
            self._show_result(result)
            for line in cycle_report(result):  # every gate value, so the squid log is the bench record
                log.info(line)
                self.log_view.append(line)
            if result.stopped == "cancelled":
                self._say("Calibration cancelled. The stage and objective have been put back.")
            elif result.restore_failed:
                self._say(
                    f"Calibration stopped: {result.stopped}. The stage or objective may not be where it started: "
                    "check the machine, then reselect the objective in the main window."
                )
            elif result.stopped:
                self._say(f"Calibration stopped: {result.stopped}. Check the stage and objective before continuing.")
            else:
                measured = ", ".join(sorted(result.pixel_sizes)) or "no objective"
                self._say(f"Calibration finished: pixel size measured for {measured}. Nothing has been saved yet.")
        self._set_running(False)

    def _show_result(self, result: RunResult):
        rows = {}
        for cycle in result.cycles:
            for name, r in cycle.objectives.items():
                if r.error:
                    rows.setdefault(name, []).append(f"cycle {cycle.index + 1}: {r.error}")
        names = [name for name in self.checkboxes if name in result.pixel_sizes or name in rows]
        self.table.setRowCount(len(names))
        for i, name in enumerate(names):
            summary = result.pixel_sizes.get(name)
            entry = self.saved.objectives.get(name) if self.saved is not None else None
            saved_px = f"{entry.pixel_size.pixel_size_um:.4f}" if entry is not None and entry.pixel_size else ""
            if summary is not None:
                nominal = self.specs[name].nominal_px_um
                values = [
                    name,
                    f"{summary.pixel_size_um:.4f}",
                    f"{summary.std_pixel_size_um:.4f}" if summary.std_pixel_size_um is not None else "",
                    f"{summary.pixel_size_um / nominal - 1:+.2%}",
                    f"{summary.anisotropy:.4f}",
                    f"{summary.rotation_deg:+.3f}",
                    f"{summary.fit_residual_um:.3f}",
                    saved_px,
                    "; ".join(rows.get(name, [])) or "ok",
                ]
            else:
                values = [name, "", "", "", "", "", "", saved_px, "; ".join(rows[name])]
            for j, value in enumerate(values):
                self.table.setItem(i, j, QTableWidgetItem(value))
        summaries = list(result.pixel_sizes.values())
        if summaries:
            s = summaries[0]
            match = "matches the mosaic" if s.orientation_matches_mosaic else "does NOT match the mosaic"
            self.label_orientation.setText(f"Camera orientation (F = {s.flip.astype(int).tolist()}): {match}")

    # ---------------------------------------------------------------- save
    def _declared_by_name(self, config) -> Dict[str, Optional[dict]]:
        names = set(config.objectives) | set(self.specs)
        return {name: self._declared(name) for name in names}

    def _apply(self):
        if self._running() or not self._can_apply():
            return
        setup = self._setup()
        binned = self.hardware.binned_sensor_pixel_um()
        binning = self.hardware.binning()[0]
        measured_at = datetime.now().isoformat(timespec="seconds")
        records = {
            name: build_pixel_record(
                summary,
                measured_at=measured_at,
                camera_key=setup.camera_key,
                unbinned_sensor_pixel_um=setup.unbinned_sensor_pixel_um,
                binned_sensor_pixel_um=binned,
                binning=binning,
                image_transform=setup.image_transform,
                tube_lens_mm=self.tube_lens_mm,
                declared=self._declared(name),
            )
            for name, summary in self.result.pixel_sizes.items()
        }
        merged = merge_pixel_records(self.saved, records)
        declared = self._declared_by_name(merged)
        blockers, warnings = review_save(
            merged,
            {name: s.pixel_size_um for name, s in self.result.pixel_sizes.items()},
            {name: self.specs[name].nominal_px_um for name in self.result.pixel_sizes},
            setup,
            declared,
        )
        if blockers:
            self._say("Not saved. " + " ".join(blockers))
            return
        merged = with_summary(merged, setup, declared)
        try:
            self.config_repo.save_objective_calibration(merged)
        except OSError as e:
            self._say(f"Not saved: {e}")
            return
        self.saved = merged
        self.result = None
        self._refresh_saved()
        self._set_running(False)
        self._say(" ".join([f"Saved the pixel size of {', '.join(sorted(records))}."] + warnings))

    def _clear(self):
        if self._running() or self.file_error or self.saved is None:
            return
        names = [
            name
            for name, box in self.checkboxes.items()
            if box.isChecked() and name in self.saved.objectives and self.saved.objectives[name].pixel_size is not None
        ]
        if not names:
            self._say("Nothing to clear for the selected objectives.")
            return
        reply = QMessageBox.question(
            self,
            "Clear pixel size",
            f"Clear the saved pixel size of {', '.join(names)}?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        cleared = clear_pixel_records(self.saved, names)
        cleared = with_summary(cleared, self._setup(), self._declared_by_name(cleared))
        try:
            self.config_repo.save_objective_calibration(cleared)
        except OSError as e:
            self._say(f"Not cleared: {e}")
            return
        self.saved = cleared
        self._refresh_saved()
        self._say(f"Cleared the saved pixel size of {', '.join(names)}.")

    # ---------------------------------------------------------------- Qt
    def reject(self):
        # Escape does not go through closeEvent. While the run owns the stage and objective the dialog
        # stays modal: released, the main window would let the user move what the run is driving.
        if self._running():
            self._say(RUNNING_MESSAGE)
            return
        super().reject()

    def accept(self):
        if self._running():
            self._say(RUNNING_MESSAGE)
            return
        super().accept()

    def closeEvent(self, event):
        if self._running():
            self._say(RUNNING_MESSAGE)
            event.ignore()
            return
        super().closeEvent(event)
