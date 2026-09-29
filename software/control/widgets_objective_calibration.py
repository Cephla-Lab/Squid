"""Utils > Objective Calibration...: measure each objective's pixel size and pixel->stage matrix
(B1), and the Z and XY offsets between objectives (C1).

AI-docs objective-pixel-size design §6.1 and objective-offset design §6.8. The engine
(squid/objective_calibration) runs in a QThread on the hardware adapter; every cycle, however it
ends, puts the objective, XY and Z back. Nothing is written until "Apply and save", which replaces
only the measured objectives' pixel_size blocks, and, when offsets were measured, the
offset_calibration section and every objective's offset block, in
machine_configs/objective_calibration.yaml. This version records the calibrations; nothing applies
them yet (B2 and C2).
"""

from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from qtpy.QtCore import QMetaObject, Qt, QThread, Signal, Slot
from qtpy.QtGui import QColor
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
    Mounting,
    ObjectiveCalibrationConfig,
    ObjectiveCalibrationFileError,
    OffsetCalibrationSection,
    build_offset_records,
    build_pixel_record,
    clear_offset_records,
    clear_pixel_records,
    implied_steps_um,
    merge_offset_records,
    merge_pixel_records,
    offset_camera_key,
    offset_validity,
    parfocal_residuals_um,
    pixel_size_validity,
    review_save,
    step_cap_blockers,
    with_summary,
)
from squid.objective_calibration.engine import ObjectiveSpec, RunConfig, RunResult, cycle_report, run_calibration
from squid.objective_calibration.hardware import RunCancelled
from squid.objective_calibration.offsets import (
    OffsetsCycleResult,
    OffsetsPhase,
    OffsetSummary,
    offsets_report,
    summarize_offsets,
)
from squid.objective_calibration.pixel_size import decompose
from squid.objective_calibration.registration import SEARCH_MARGIN

log = squid.logging.get_logger(__name__)

RUNNING_MESSAGE = (
    "A calibration is running. Cancel it and wait for the stage and objective to be put back before closing."
)
GUIDANCE = (
    "Use a level stained section with varied texture in both directions, roughly in focus on the current "
    "objective, with the stage away from its travel limits. Avoid repeating patterns (a grating, a single "
    "bar group), bare glass and unstained cells in brightfield: they cannot be matched or focused reliably. "
    "A tilt s adds up to s × W/2 to the Z offsets, with W half the smallest field of view. "
    "Saved calibrations are recorded for review; this version does not apply them yet."
)
ORIENTATION_MESSAGE = (
    "Camera orientation does not match the mosaic; XY offsets were not saved. "
    "Fix the camera orientation settings, then recalibrate."
)
FIRST_RANGE_UM = 100.0  # spec C §6.1: a first calibration searches ±100 µm
RECALIBRATION_RANGE_UM = 20.0  # ±20 µm when a valid offset calibration predicts each focus
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
OFFSET_COLUMNS = [
    "Objective",
    "Δx (µm)",
    "Δy (µm)",
    "Δz (µm)",
    "± std x/y/z (µm)",
    "Match",
    "Runner-up",
    "Peak rise",
    "Closure (µm)",
    "Saved Δx/Δy/Δz (µm)",
    "Status",
]
STEP_COLUMNS = ["From", "To", "Z step (mm)"]


class CalibrationWorker(QThread):
    """Runs the engine off the GUI thread. Never raises into Qt: the RunResult, or the exception that
    ended the run, comes back through signal_finished."""

    signal_progress = Signal(str)
    signal_finished = Signal(object)

    def __init__(self, hardware, config: RunConfig, fine_metric, after_run=None, phase2=None, parent=None):
        super().__init__(parent)
        self.hardware = hardware
        self.config = config
        self.fine_metric = fine_metric
        self.after_run = after_run
        self.phase2 = phase2
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            result = run_calibration(
                self.hardware,
                self.config,
                fine_metric=self.fine_metric,
                phase2=self.phase2,
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


def _fmt(value: Optional[float], digits: int = 2) -> str:
    return "" if value is None else f"{value:.{digits}f}"


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
        get_mounting: Optional[Callable[[str], Tuple[str, Optional[int]]]] = None,
        pos2_offset_um: float = 0.0,
        max_step_um: float = 500.0,
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
        self._get_mounting = get_mounting or (lambda name: ("none", None))
        self.pos2_offset_um = pos2_offset_um
        self.max_step_um = max_step_um
        self.fine_metric = fine_metric
        self.manual_switch = manual_switch
        self.busy_reason = busy_reason
        self.after_run = after_run
        self.worker: Optional[CalibrationWorker] = None
        self.result: Optional[RunResult] = None
        self.phase: Optional[OffsetsPhase] = None  # the offsets phase of the last run, with its per-cycle results
        self.offsets: Dict[str, OffsetSummary] = {}  # the last run's offsets, averaged over its successful cycles
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
        self.spin_range.setValue(FIRST_RANGE_UM)
        self.spin_range.setPrefix("± ")
        self.spin_range.setSuffix(" µm")
        form.addRow("Focus search range", self.spin_range)
        self.spin_cycles = QSpinBox()
        self.spin_cycles.setRange(1, 5)
        self.spin_cycles.setValue(3)
        form.addRow("Cycles", self.spin_cycles)
        measure = QHBoxLayout()
        self.checkbox_pixel_size = QCheckBox("Pixel size")
        self.checkbox_pixel_size.setChecked(True)
        self.checkbox_offsets = QCheckBox("Offsets (Z and XY between objectives)")
        for box in (self.checkbox_pixel_size, self.checkbox_offsets):
            measure.addWidget(box)
        form.addRow("Measure", measure)

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
        self.label_offsets = QLabel("")
        self.label_offsets.setWordWrap(True)
        saved_layout.addWidget(self.label_offsets)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table_offsets = QTableWidget(0, len(OFFSET_COLUMNS))
        self.table_offsets.setHorizontalHeaderLabels(OFFSET_COLUMNS)
        self.table_offsets.verticalHeader().setVisible(False)
        self.label_ranges = QLabel("")
        self.label_ranges.setWordWrap(True)
        self.table_steps = QTableWidget(0, len(STEP_COLUMNS))
        self.table_steps.setHorizontalHeaderLabels(STEP_COLUMNS)
        self.table_steps.verticalHeader().setVisible(False)
        self.label_steps = QLabel(
            "Implied Z step on every objective switch (from the saved offsets; red: over the cap)"
        )
        self.label_uncalibrated = QLabel("")
        self.label_uncalibrated.setWordWrap(True)
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
        self.button_clear_offsets = QPushButton("Clear offsets")
        self.button_close = QPushButton("Close")
        for button in (
            self.button_calibrate,
            self.button_cancel,
            self.button_apply,
            self.button_clear,
            self.button_clear_offsets,
            self.button_close,
        ):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        self.button_calibrate.clicked.connect(self._start)
        self.button_cancel.clicked.connect(self._cancel)
        self.button_apply.clicked.connect(self._apply)
        self.button_clear.clicked.connect(self._clear)
        self.button_clear_offsets.clicked.connect(self._clear_offsets)
        self.button_close.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        for widget in (
            inputs,
            guidance,
            saved_box,
            self.table,
            self.table_offsets,
            self.label_ranges,
            self.label_steps,
            self.table_steps,
            self.label_uncalibrated,
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
        self.button_clear_offsets.setEnabled(not running and not self.file_error)
        self.button_close.setEnabled(not running)

    def _can_apply(self) -> bool:
        return (
            not self.file_error
            and self.result is not None
            and self.result.stopped is None
            and (bool(self.result.pixel_sizes) or bool(self.offsets))
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

    def _current_mountings(self) -> Dict[str, Mounting]:
        """Every installed objective's mounting now (spec A's get_mounting plus its serial), for the
        validity check and the records."""
        mountings = {}
        for name in self.checkboxes:
            declared = self._declared(name)
            try:
                changer, position = self._get_mounting(name)
            except KeyError:
                continue
            mountings[name] = Mounting(
                changer=changer, position=position, serial=declared["serial"] if declared else ""
            )
        return mountings

    def _saved_matrices_um_per_px(self) -> Dict[str, np.ndarray]:
        """The saved, directionally valid matrices for the current image pixels (spec B §4.5)."""
        if self.saved is None:
            return {}
        setup, binned = self._setup(), self.hardware.binned_sensor_pixel_um()
        matrices = {}
        for name, entry in self.saved.objectives.items():
            if pixel_size_validity(entry.pixel_size, setup, self._declared(name)).directional:
                matrices[name] = np.asarray(entry.pixel_size.matrix_norm, dtype=float) * binned
        return matrices

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
        mountings = self._current_mountings()
        validity = offset_validity(self.saved, mountings, offset_camera_key(self.hardware))
        section = self.saved.offset_calibration if self.saved is not None else None
        if section is None:
            self.label_offsets.setText("Offsets: not calibrated")
        else:
            head = f"Offsets (reference {section.reference_objective}, measured {section.measured_at}, {section.cycles} cycles)"
            if validity.z and validity.xy:
                self.label_offsets.setText(f"{head}: Z valid, XY valid")
            elif validity.z:
                self.label_offsets.setText(f"{head}: Z valid, XY invalid ({validity.reason})")
            else:
                self.label_offsets.setText(f"{head}: invalid ({validity.reason}); recalibrate")
        self.spin_range.setValue(RECALIBRATION_RANGE_UM if validity.z else FIRST_RANGE_UM)
        self._show_steps(self.saved if validity.z else None, mountings)

    def _show_steps(self, config: Optional[ObjectiveCalibrationConfig], mountings: Dict[str, Mounting]):
        """The implied Z step for every pair of installed objectives (spec C §6.8), over-cap rows in red,
        and the objectives the calibration does not cover (spec C §4)."""
        ordered = {name: mountings[name] for name in self.checkboxes if name in mountings}
        steps = implied_steps_um(config, ordered, self.pos2_offset_um) if config is not None else {}
        self.table_steps.setRowCount(len(steps))
        for i, ((a, b), step) in enumerate(steps.items()):
            for j, value in enumerate((a, b, f"{step / 1000:+.4f}")):
                item = QTableWidgetItem(value)
                if abs(step) > self.max_step_um:
                    item.setBackground(QColor("#ffb3b3"))
                self.table_steps.setItem(i, j, item)
        if config is None or config.offset_calibration is None:
            self.label_uncalibrated.setText("")
            return
        covered = {config.offset_calibration.reference_objective} | {
            name for name, entry in config.objectives.items() if entry.offset is not None
        }
        missing = [name for name in ordered if name not in covered]
        self.label_uncalibrated.setText(
            (
                f"Not in the offset calibration: {', '.join(missing)}. They keep today's frame (assumed parfocal "
                "and parcentric with the reference), so a switch between them and a calibrated objective applies "
                "the calibrated one's correction."
            )
            if missing
            else ""
        )

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
        measure_pixel_size, measure_offsets = self.checkbox_pixel_size.isChecked(), self.checkbox_offsets.isChecked()
        if not (measure_pixel_size or measure_offsets):
            self._say("Not started. Select a measurement.")
            return
        config = RunConfig(
            selected,
            self.combo_channel.currentText(),
            search_range_um=self.spin_range.value(),
            cycles=self.spin_cycles.value(),
            measure_pixel_size=measure_pixel_size,
        )
        mountings = self._current_mountings()
        if offset_validity(self.saved, mountings, offset_camera_key(self.hardware)).z:
            # Every run, not only one with Offsets checked: the ±20 um default range after an offsets save
            # relies on the prediction (spec C §6.2 step 2.2; the final review of C1).
            config.predicted_residual_um = parfocal_residuals_um(self.saved, mountings, self.pos2_offset_um)
        phase = None
        if measure_offsets:
            if len(selected) < 2:
                self._say("Not started. Offsets need at least two objectives.")
                return
            if not measure_pixel_size:
                config.saved_matrices_um_per_px = self._saved_matrices_um_per_px()
                missing = [spec.name for spec in selected if spec.name not in config.saved_matrices_um_per_px]
                if missing:
                    self._say(
                        f"Not started. Offsets alone need a valid saved pixel calibration for {', '.join(missing)}; "
                        "measure the pixel size too."
                    )
                    return
            phase = OffsetsPhase(config, fine_metric=self.fine_metric)
        hardware = _PromptedSwitch(self.hardware, self._ask_switch) if self.manual_switch else self.hardware
        self.result = None
        self.phase = phase
        self.offsets = {}
        self.table.setRowCount(0)
        self.table_offsets.setRowCount(0)
        self.label_ranges.setText("")
        self.label_orientation.setText("")
        self.log_view.clear()
        self._say("Calibrating...")
        self.worker = CalibrationWorker(
            hardware, config, self.fine_metric, after_run=self.after_run, phase2=phase, parent=self
        )
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
            if self.phase is not None:
                self.offsets = summarize_offsets(self.phase.results)
            self._show_result(result)
            report = cycle_report(result)  # every gate value, so the squid log is the bench record
            if self.phase is not None:
                report += offsets_report(result, self.phase.results)
            for line in report:
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
                parts = []
                if result.pixel_sizes:
                    parts.append(f"pixel size measured for {', '.join(sorted(result.pixel_sizes))}")
                if self.offsets:
                    parts.append(f"offsets measured for {', '.join(sorted(self.offsets))}")
                failed = [f"cycle {c.index + 1}: {c.error}" for c in result.cycles if c.error]
                if parts and self.phase is not None and not self.offsets:
                    # Offsets were asked for and none was measured: say so, not a plain finished run.
                    self._say(
                        f"Calibration finished: {'; '.join(parts)}. Offsets not measured ({'; '.join(failed)}); "
                        "the saved offsets are unchanged. Nothing has been saved yet."
                    )
                elif parts:
                    self._say(f"Calibration finished: {'; '.join(parts)}. Nothing has been saved yet.")
                else:
                    self._say("Calibration finished with nothing measured. " + "; ".join(failed))
        self._set_running(False)

    def _orientation_matches(self) -> bool:
        """Whether XY may be saved (spec C §5): every matrix the run registered with (this run's, or
        the saved ones it started with in an offsets-only run, even if cleared since) has F = I."""
        if self.result is not None and self.result.pixel_sizes:
            return all(s.orientation_matches_mosaic for s in self.result.pixel_sizes.values())
        matrices = self.phase.cfg.saved_matrices_um_per_px if self.phase is not None else {}
        return bool(matrices) and all(np.array_equal(decompose(m)[2], np.eye(2)) for m in matrices.values())

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
        if self.phase is not None:
            self._show_offsets(result, self.phase.results)

    def _show_offsets(self, result: RunResult, cycles: List[OffsetsCycleResult]):
        failed = [f"cycle {c.index + 1}: {c.error}" for c in result.cycles if c.error]
        warnings = sorted({w for c in cycles for w in c.warnings})
        reference = cycles[0].reference if cycles else self.phase.ordered[0]
        names = [reference] + [name for name in self.phase.ordered if name in self.offsets]
        self.table_offsets.setRowCount(len(names))
        for i, name in enumerate(names):
            entry = self.saved.objectives.get(name) if self.saved is not None else None
            block = entry.offset if entry is not None else None
            saved = f"{_fmt(block.dx_um)} / {_fmt(block.dy_um)} / {_fmt(block.dz_um)}" if block is not None else ""
            if name == reference:
                values = [name, "0", "0", "0", "", "", "", "", "", saved, "reference"]
            else:
                s = self.offsets[name]
                values = [
                    name,
                    _fmt(s.dx_um),
                    _fmt(s.dy_um),
                    _fmt(s.dz_um),
                    f"{_fmt(s.std_dx_um)} / {_fmt(s.std_dy_um)} / {_fmt(s.std_dz_um)}",
                    _fmt(s.match_score),
                    _fmt(s.runner_up_ratio),
                    _fmt(s.focus_peak_rise, 1),
                    _fmt(s.closure_error_um),
                    saved,
                    "; ".join(failed + warnings) or "ok",
                ]
            for j, value in enumerate(values):
                self.table_offsets.setItem(i, j, QTableWidgetItem(value))
        self._show_ranges(cycles)
        if not self.offsets:
            return
        if not self._orientation_matches():
            self.label_orientation.setText(ORIENTATION_MESSAGE)
        candidate, _ = self._candidate_offsets(datetime.now().isoformat(timespec="seconds"))
        self._show_steps(candidate, self._current_mountings())

    def _show_ranges(self, cycles: List[OffsetsCycleResult]):
        """The largest offset each reference pair could measure, from this camera's frame (spec C §6.4)."""
        pairs = [pair for pair in cycles[0].pairs if pair.lower == cycles[0].reference] if cycles else []
        if not pairs:
            self.label_ranges.setText("")
            return
        reach = "; ".join(
            f"{p.lower}-{p.higher} ±{p.range_um[0]:.1f} µm in x, ±{p.range_um[1]:.1f} µm in y" for p in pairs
        )
        self.label_ranges.setText(
            f"Largest offset each pair can measure on this camera's frame: {reach}. Objectives of equal or nearly "
            f"equal magnification are compared through a central crop that keeps {SEARCH_MARGIN:.0%} of the field "
            f"free on every side, so for them it is {SEARCH_MARGIN:.0%} of the field."
        )

    # ---------------------------------------------------------------- save
    def _declared_by_name(self, config) -> Dict[str, Optional[dict]]:
        names = set(config.objectives) | set(self.specs)
        return {name: self._declared(name) for name in names}

    def _candidate_pixel_records(self, measured_at: str, setup: CurrentSetup):
        binned = self.hardware.binned_sensor_pixel_um()
        binning = self.hardware.binning()[0]
        return {
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

    def _candidate_offsets(self, measured_at: str, base: Optional[ObjectiveCalibrationConfig] = None):
        """The config a save would write for this run's offsets, and whether XY is in it."""
        mountings = self._current_mountings()
        reference = self.phase.results[0].reference
        section = OffsetCalibrationSection(
            reference_objective=reference,
            reference_mounting=mountings[reference],
            measured_at=measured_at,
            channel=self.phase.cfg.channel,  # the run's, not the selector's now: it stays editable
            cycles=len(self.phase.results),
            camera_key=offset_camera_key(self.hardware),
        )
        save_xy = self._orientation_matches()
        records = build_offset_records(self.offsets, mountings, save_xy=save_xy)
        return merge_offset_records(base if base is not None else self.saved, section, records), save_xy

    def _apply(self):
        if self._running() or not self._can_apply():
            return
        setup = self._setup()
        measured_at = datetime.now().isoformat(timespec="seconds")
        merged = self.saved
        saved_what, notes = [], []
        if self.result.pixel_sizes:
            records = self._candidate_pixel_records(measured_at, setup)
            merged = merge_pixel_records(merged, records)
            blockers, warnings = review_save(
                merged,
                {name: s.pixel_size_um for name, s in self.result.pixel_sizes.items()},
                {name: self.specs[name].nominal_px_um for name in self.result.pixel_sizes},
                setup,
                self._declared_by_name(merged),
            )
            if blockers:
                self._say("Not saved. " + " ".join(blockers))
                return
            saved_what.append(f"the pixel size of {', '.join(sorted(records))}")
            notes.extend(warnings)
        if self.offsets:
            merged, save_xy = self._candidate_offsets(measured_at, base=merged)
            mountings = self._current_mountings()
            blockers = step_cap_blockers(implied_steps_um(merged, mountings, self.pos2_offset_um), self.max_step_um)
            if blockers:
                self._say("Not saved. " + " ".join(blockers))
                return
            saved_what.append(f"the {'Z and XY' if save_xy else 'Z'} offsets of {', '.join(sorted(self.offsets))}")
            if not save_xy:
                notes.append(ORIENTATION_MESSAGE)
        elif self.phase is not None:
            notes.append("Offsets were not measured; the saved offsets are unchanged.")
        merged = with_summary(merged, setup, self._declared_by_name(merged))
        try:
            self.config_repo.save_objective_calibration(merged)
        except OSError as e:
            self._say(f"Not saved: {e}")
            return
        self.saved = merged
        self.result = None
        self.offsets = {}
        self._refresh_saved()
        self._set_running(False)
        self._say(" ".join([f"Saved {' and '.join(saved_what)}."] + notes))

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

    def _clear_offsets(self):
        """Spec C §5: removes only the offset section and blocks; the pixel-size records stay."""
        if self._running() or self.file_error or self.saved is None or self.saved.offset_calibration is None:
            self._say("No saved offset calibration to clear.")
            return
        reply = QMessageBox.question(
            self, "Clear offsets", "Clear the saved objective offsets?", QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        cleared = clear_offset_records(self.saved)
        try:
            self.config_repo.save_objective_calibration(cleared)
        except OSError as e:
            self._say(f"Not cleared: {e}")
            return
        self.saved = cleared
        self._refresh_saved()
        self._say("Cleared the saved objective offsets.")

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
