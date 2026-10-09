"""Utils > Illumination Power Calibration...: measure each DAC-driven illumination channel with a power meter and make
its intensity linear in optical power.

The work lives in squid/intensity_calibration_run.py (CalibrationSession), which tools/generate_intensity_calibrations.py
drives too; this file is the dialog around it. A run happens in a worker thread on the application's own controller,
so the window stays responsive and Cancel is answered after the current point. Every run ends with the light off and
the DAC at 0. Nothing is written until Save.
"""

from typing import Callable, Dict, List, Optional, Union

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt, QThread, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import squid.logging
from squid.intensity_calibration import VERIFY_REL_TOL
from squid.intensity_calibration_run import (
    TEST_BEAM_DEFAULT_PERCENT,
    TEST_BEAM_MAX_S,
    CalibrationSession,
    ChannelResult,
    ChannelTarget,
)
from squid.power_meter import PowerMeterError

log = squid.logging.get_logger(__name__)


class CalibrationWorker(QThread):
    """Runs CalibrationSession.run off the GUI thread. Never raises into Qt: the outcome comes back on a signal."""

    signal_progress = Signal(str, int, int)
    signal_channel_done = Signal(str, object)  # channel name, ChannelResult or the Exception that stopped it
    signal_finished = Signal(object)  # the results dict, or the Exception that ended the run

    def __init__(self, session: CalibrationSession, targets, measured_in: str, sensor_limit_mw: float, parent=None):
        super().__init__(parent)
        self.session = session
        self.targets = targets
        self.measured_in = measured_in
        self.sensor_limit_mw = sensor_limit_mw

    def run(self):
        try:
            result = self.session.run(
                self.targets,
                measured_in=self.measured_in,
                sensor_limit_mw=self.sensor_limit_mw,
                progress=self.signal_progress.emit,
                on_channel_done=self.signal_channel_done.emit,
            )
        except Exception as e:  # noqa: BLE001 - reported in the dialog
            log.error("Illumination power calibration failed", exc_info=True)
            result = e
        self.signal_finished.emit(result)


class IntensityCalibrationDialog(QDialog):
    """Connect a meter, tick channels, Run, review, Save.

    Args:
        session: the CalibrationSession on the application's controller and config repository.
        measured_in: "confocal", "widefield" or "n/a"; recorded in each file (design Q2).
        busy_reason: returns why the instrument is busy (live view, acquisition), or None. Run, Test beam and Save
            are refused while it returns a reason.
        restore_illumination: called once when the dialog closes, if a run or the test beam drove the light: they
            leave the controller on their last port at DAC 0, and live start / snap only turn the light on, so the
            GUI re-sends its live channel (through the new calibration, if one was saved).
    """

    COL_USE, COL_CHANNEL, COL_PORT, COL_CEILING, COL_CURRENT, COL_RUN = range(6)

    def __init__(
        self,
        session: CalibrationSession,
        measured_in: str,
        busy_reason: Optional[Callable[[], Optional[str]]] = None,
        restore_illumination: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Illumination Power Calibration")
        self.session = session
        self.measured_in = measured_in
        self.busy_reason = busy_reason
        self.restore_illumination = restore_illumination
        self._shut_down = False
        self.worker: Optional[CalibrationWorker] = None
        self.targets: List[ChannelTarget] = session.targets()
        self.results: Dict[str, Union[ChannelResult, Exception]] = {}
        self._test_beam_target: Optional[ChannelTarget] = None
        self._test_beam_confirmed = False
        self.reading_timer = QTimer(self)
        self.reading_timer.setInterval(500)
        self.reading_timer.timeout.connect(self._update_reading)
        # the test beam turns itself off; meanwhile the reading timer feeds the watchdog (session.feed_watchdog), so a
        # frozen GUI lets the controller's watchdog turn it off within its timeout
        self.test_beam_timer = QTimer(self)
        self.test_beam_timer.setSingleShot(True)
        self.test_beam_timer.setInterval(int(TEST_BEAM_MAX_S * 1000))
        self.test_beam_timer.timeout.connect(self._test_beam_timed_out)
        self._build()
        self._fill_table()

    # ---------------------------------------------------------------- layout
    def _build(self):
        layout = QVBoxLayout()

        meter_row = QHBoxLayout()
        self.label_meter = QLabel("Power meter: not connected")
        self.button_connect = QPushButton("Connect")
        self.button_connect.clicked.connect(self._connect)
        self.label_reading = QLabel("")
        meter_row.addWidget(self.label_meter, 1)
        meter_row.addWidget(self.label_reading)
        meter_row.addWidget(self.button_connect)
        layout.addLayout(meter_row)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel(f"Imaging mode: {self.measured_in} (recorded in the calibration)"))
        settings_row.addStretch(1)
        settings_row.addWidget(QLabel("Sensor limit:"))
        self.spin_sensor_limit = QDoubleSpinBox()
        self.spin_sensor_limit.setRange(0, 0)  # set from the sensor on Connect; can only be lowered from there
        self.spin_sensor_limit.setDecimals(1)
        self.spin_sensor_limit.setSuffix(" mW")
        self.spin_sensor_limit.setSpecialValueText("connect a meter")
        self.spin_sensor_limit.setToolTip(
            "The run stops at the first reading above this. It starts at the sensor's own maximum; lower it for a "
            "smaller rating or for power density at a focused beam."
        )
        settings_row.addWidget(self.spin_sensor_limit)
        layout.addLayout(settings_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Channel", "Port", "Ceiling", "Current calibration", "This run"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.currentCellChanged.connect(lambda row, column, previous_row, previous_column: self._show_plots(row))
        layout.addWidget(self.table)

        self.label_note = QLabel("")
        self.label_note.setWordWrap(True)
        layout.addWidget(self.label_note)

        beam_row = QHBoxLayout()
        self.button_test_beam = QPushButton("Test beam")
        self.button_test_beam.setCheckable(True)
        self.button_test_beam.setToolTip(
            "Turn the selected channel on at the level on the right (raw DAC, % of its ceiling) to center the "
            "sensor; watch the reading."
        )
        self.button_test_beam.toggled.connect(self._toggle_test_beam)
        self.spin_test_beam = QDoubleSpinBox()
        self.spin_test_beam.setRange(1, 100)
        self.spin_test_beam.setSuffix(" % of ceiling")
        self.spin_test_beam.setValue(TEST_BEAM_DEFAULT_PERCENT)
        beam_row.addWidget(self.button_test_beam)
        beam_row.addWidget(self.spin_test_beam)
        beam_row.addStretch(1)
        layout.addLayout(beam_row)

        plots = QHBoxLayout()
        self.plot_curve = pg.PlotWidget()
        self.plot_curve.setLabel("bottom", "DAC (% commanded)")
        self.plot_curve.setLabel("left", "power (mW)")
        self.plot_verify = pg.PlotWidget()
        self.plot_verify.setLabel("bottom", "requested (% power)")
        self.plot_verify.setLabel("left", "measured (% of max power)")
        plots.addWidget(self.plot_curve)
        plots.addWidget(self.plot_verify)
        layout.addLayout(plots, 1)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.label_progress = QLabel("")
        self.button_run = QPushButton("Run")
        self.button_run.clicked.connect(self._run)
        self.button_cancel = QPushButton("Cancel")
        self.button_cancel.setEnabled(False)
        self.button_cancel.setToolTip("Stop after the current point; the light is turned off either way.")
        self.button_cancel.clicked.connect(self._cancel)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.label_progress)
        progress_row.addWidget(self.button_run)
        progress_row.addWidget(self.button_cancel)
        layout.addLayout(progress_row)

        self.label_result = QLabel("")
        self.label_result.setWordWrap(True)
        self.label_result.setTextFormat(Qt.PlainText)
        layout.addWidget(self.label_result)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.button_save = QPushButton("Save...")
        self.button_save.setEnabled(False)
        self.button_save.setToolTip("Write the calibrations and use them now (a previous file is backed up first).")
        self.button_save.clicked.connect(self._save)
        self.button_close = QPushButton("Close")
        self.button_close.clicked.connect(self.close)
        close_row.addWidget(self.button_save)
        close_row.addWidget(self.button_close)
        layout.addLayout(close_row)

        self.setLayout(layout)
        self.resize(960, 760)

    def _readonly_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _fill_table(self):
        self.table.setRowCount(len(self.targets))
        for row, target in enumerate(self.targets):
            use = QCheckBox()
            use.setChecked(True)
            self.table.setCellWidget(row, self.COL_USE, use)
            self.table.setItem(row, self.COL_CHANNEL, self._readonly_item(target.name))
            self.table.setItem(row, self.COL_PORT, self._readonly_item(target.controller_port))
            self.table.setItem(row, self.COL_CEILING, self._readonly_item(f"{target.ceiling_percent:g} %"))
            self.table.setItem(row, self.COL_CURRENT, self._readonly_item(self.session.current_status(target)))
            self.table.setItem(row, self.COL_RUN, self._readonly_item(""))
        if not self.session.dac_driven:
            self.label_note.setText(
                "This instrument sets illumination power in the light source itself (LDI, CELESTA or Andor), not "
                "through the controller DAC, so there is nothing to calibrate here."
            )
        elif not self.targets:
            self.label_note.setText(
                "No epi-illumination channel with a wavelength on a controller port (D1-D8) is configured. Add one in "
                "Settings > Advanced > Illumination Channel Configuration."
            )
        else:
            self.label_note.setText(
                "Place the sensor at the sample plane, in the light path. At a focused beam use a sensor rated for it "
                "(a microscope-slide sensor): power density can damage a sensor below its power rating. Each channel "
                "is swept from DAC 0 upward to its ceiling (Max Output). If a channel's Max Output was set only to keep "
                "the output linear, raise it to 1.0 in the channel editor first: calibration makes the full range linear."
            )
            if not self.session.watchdog_protected:
                self.label_note.setText(
                    self.label_note.text() + " This controller has no illumination watchdog (firmware before 1.1): "
                    "if the software stops responding during a run, turn the light off at the controller."
                )
            self.table.selectRow(0)

    def _refresh_current(self):
        # Re-read the channels: Save just pointed their intensity_calibration_file at the new files
        fresh = {target.name: target for target in self.session.targets()}
        self.targets = [fresh.get(target.name, target) for target in self.targets]
        for row, target in enumerate(self.targets):
            self.table.item(row, self.COL_CURRENT).setText(self.session.current_status(target))

    # ---------------------------------------------------------------- meter and test beam
    def _say(self, message: str):
        self.label_result.setText(message)

    def _busy(self) -> Optional[str]:
        return self.busy_reason() if self.busy_reason else None

    def _connect(self):
        # the session would stop feeding the watchdog while a slow meter answers Connect
        self.button_test_beam.setChecked(False)
        try:
            info = self.session.connect()
        except PowerMeterError as e:
            self._say(f"Power meter not connected: {e}")
            return
        text = f"Power meter: {info.meter}, sensor {info.sensor}"
        if not info.validated:
            text += " (model not yet validated on a bench: readings wait longer to settle)"
        self.label_meter.setText(text)
        if info.max_power_mw is not None:
            self.spin_sensor_limit.setRange(0, info.max_power_mw)
            self.spin_sensor_limit.setValue(info.max_power_mw)
        else:
            self.spin_sensor_limit.setRange(0, 100000)
            self.spin_sensor_limit.setValue(0)
            self._say("The meter did not report the sensor's maximum power: enter it as the sensor limit before Run.")
        self.reading_timer.start()

    def _update_reading(self):
        if self._test_beam_target is not None:
            self.session.feed_watchdog()
        if self._running() or self.session.meter is None:
            return
        try:
            reading = self.session.read_mw()
        except PowerMeterError as e:
            self.label_reading.setText(f"reading failed: {e}")
            if self._test_beam_target is not None:
                # without a reading there is no sensor-limit check, and an overrange means the sensor is already
                # past its range: the beam must not stay on until its timer runs out
                self.button_test_beam.setChecked(False)
                self._say(f"Test beam turned off: the meter reading failed ({e}).")
            return
        self.label_reading.setText(f"{reading:.4g} mW")
        limit = self.spin_sensor_limit.value()
        if self._test_beam_target is not None and limit > 0 and reading > limit:
            self.button_test_beam.setChecked(False)
            self._say(f"Test beam turned off: {reading:.4g} mW is above the sensor limit ({limit:g} mW).")

    def _test_beam_timed_out(self):
        if self._test_beam_target is not None:
            self.button_test_beam.setChecked(False)
            self._say(f"Test beam turned off after {TEST_BEAM_MAX_S:g} s.")

    def _selected_target(self) -> Optional[ChannelTarget]:
        row = self.table.currentRow()
        return self.targets[row] if 0 <= row < len(self.targets) else None

    def _toggle_test_beam(self, on: bool):
        if not on:
            if self._test_beam_target is not None:
                try:
                    self.session.test_beam_off(self._test_beam_target)
                except Exception as e:  # noqa: BLE001 - reported in the dialog
                    log.error("Turning the test beam off failed", exc_info=True)
                    self._say(f"Turning the test beam off failed: {e}")
                self._test_beam_target = None
            self.test_beam_timer.stop()
            return
        target = self._selected_target()
        reason = self._busy() or (
            None
            if self.session.meter is not None
            else "Connect a power meter first: the test beam is for centering it."
        )
        if target is None or self._running() or reason or not self._confirm_test_beam(target):
            if reason:
                self._say(f"Test beam not turned on. {reason}")
            self.button_test_beam.setChecked(False)
            return
        try:
            self.session.test_beam_on(target, self.spin_test_beam.value())
            self._test_beam_target = target
            self.test_beam_timer.start()
        except Exception as e:  # noqa: BLE001 - reported in the dialog
            log.error("Turning the test beam on failed", exc_info=True)
            self._say(f"Test beam failed: {e}")
            self.button_test_beam.setChecked(False)

    def _confirm_test_beam(self, target: ChannelTarget) -> bool:
        if self._test_beam_confirmed:
            return True
        answer = QMessageBox.warning(
            self,
            "Laser safety",
            f"Test beam turns {target.name} on until you turn it off. Make sure nobody is looking into the beam and "
            "this instrument's laser safety rules are followed.\n\nTurn it on?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        self._test_beam_confirmed = answer == QMessageBox.Yes
        return self._test_beam_confirmed

    # ---------------------------------------------------------------- run
    def _running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _checked_targets(self) -> List[ChannelTarget]:
        return [t for row, t in enumerate(self.targets) if self.table.cellWidget(row, self.COL_USE).isChecked()]

    def _confirm_run(self, targets: List[ChannelTarget]) -> bool:
        top = max(t.ceiling_percent for t in targets)
        answer = QMessageBox.warning(
            self,
            "Laser safety",
            f"The run turns on {', '.join(t.name for t in targets)} one at a time, stepping up to {top:g} % DAC, "
            f"for a few minutes each, and stops at the first reading above {self.spin_sensor_limit.value():g} mW."
            "\n\nBefore starting: the power meter sensor is in the light path at the sample plane and rated for the "
            "beam there, nobody is looking into the beam, and this instrument's laser safety rules are followed."
            "\n\nStart?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _run(self):
        if self._running():
            return
        if self.session.meter is None:
            self._say("Connect a power meter first.")
            return
        reason = self._busy()
        if reason:
            self._say(f"Not started. {reason}")
            return
        if self.spin_sensor_limit.value() <= 0:
            self._say("Enter the sensor limit (the sensor's maximum power) before Run.")
            return
        targets = self._checked_targets()
        if not targets:
            self._say("Tick at least one channel.")
            return
        if not self._confirm_run(targets):
            return
        self.button_test_beam.setChecked(False)
        self.results = {}
        for row in range(self.table.rowCount()):
            self.table.item(row, self.COL_RUN).setText("")
        self._say("")
        self._set_running(True)
        self.worker = CalibrationWorker(
            self.session, targets, self.measured_in, self.spin_sensor_limit.value(), parent=self
        )
        self.worker.signal_progress.connect(self._on_progress)
        self.worker.signal_channel_done.connect(self._on_channel_done)
        self.worker.signal_finished.connect(self._finished)
        # the thread is done only when QThread.finished arrives (after signal_finished): clear it then, so Close
        # can never destroy a thread that is still running
        self.worker.finished.connect(self._worker_stopped)
        self.worker.start()

    def _cancel(self):
        if self._running():
            self.session.cancel()
            self.button_cancel.setEnabled(False)
            self._say("Cancelling after the current point; the light is turned off either way.")

    def _on_progress(self, message: str, done: int, total: int):
        self.label_progress.setText(message)
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)

    def _row_of(self, name: str) -> Optional[int]:
        return next((row for row, target in enumerate(self.targets) if target.name == name), None)

    @staticmethod
    def _result_text(result) -> str:
        if isinstance(result, Exception):
            return f"✗ {result}"
        calibration = result.calibration
        mark = "✓" if calibration.verification == "pass" else "✗"
        text = f"{mark} {calibration.p_max_mw:.4g} mW, {calibration.verification_summary()}"
        if calibration.rollover:
            text += f"  ⚠ peak at DAC {calibration.top_dac_percent:.1f} %"
        for warning in result.warnings:
            text += f"  ⚠ {warning}"
        return text

    def _on_channel_done(self, name: str, result):
        self.results[name] = result
        row = self._row_of(name)
        if row is not None:
            self.table.item(row, self.COL_RUN).setText(self._result_text(result))
            self.table.selectRow(row)
            self._show_plots(row)

    def _finished(self, result):
        self.session.clear_cancel()
        if isinstance(result, Exception):
            self._say(f"Run failed: {result}")
        else:
            calibrated = [name for name, r in result.items() if isinstance(r, ChannelResult)]
            passed = [name for name in calibrated if result[name].calibration.verification == "pass"]
            message = f"{len(passed)} of {len(result)} channels passed verification."
            not_calibrated = [f"{name} ({r})" for name, r in result.items() if not isinstance(r, ChannelResult)]
            if not_calibrated:
                message += " Not calibrated: " + "; ".join(not_calibrated) + "."
            if calibrated:
                message += " Nothing is saved until you click Save."
            self._say(message)

    def _worker_stopped(self):
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        self._set_running(False)

    def _set_running(self, running: bool):
        self.button_run.setEnabled(not running)
        self.button_connect.setEnabled(not running)
        self.button_test_beam.setEnabled(not running)
        self.button_cancel.setEnabled(running)
        self.button_close.setEnabled(not running)
        self.table.setEnabled(not running)
        self.button_save.setEnabled(not running and any(isinstance(r, ChannelResult) for r in self.results.values()))

    def _show_plots(self, row: int):
        self.plot_curve.clear()
        self.plot_verify.clear()
        if not 0 <= row < len(self.targets):
            return
        result = self.results.get(self.targets[row].name)
        if not isinstance(result, ChannelResult):
            return
        c = result.calibration
        self.plot_curve.plot(c.dac_percent_commanded, c.power_mw, pen=None, symbol="o", symbolSize=3)
        valid = ~np.isnan(c.power_mw_fit)
        self.plot_curve.plot(c.dac_percent_commanded[valid], c.power_mw_fit[valid], pen=pg.mkPen(width=2))
        if c.rollover:
            self.plot_curve.addLine(x=c.top_dac_percent, pen=pg.mkPen("r", style=Qt.DashLine))
        line = np.array([0.0, 100.0])
        self.plot_verify.plot(line, line, pen=pg.mkPen("w"))
        self.plot_verify.plot(line, line * (1 + VERIFY_REL_TOL), pen=pg.mkPen("g", style=Qt.DashLine))
        self.plot_verify.plot(line, line * (1 - VERIFY_REL_TOL), pen=pg.mkPen("g", style=Qt.DashLine))
        requested = np.array([point[0] for point in c.verification_points])
        errors = np.array([point[1] for point in c.verification_points])
        if requested.size:
            self.plot_verify.plot(requested, requested * (1 + errors / 100.0), pen=None, symbol="o")

    # ---------------------------------------------------------------- save
    def _save(self):
        if self._running():
            return
        reason = self._busy()
        if reason:
            self._say(f"Not saved. {reason}")
            return
        calibrations = [r.calibration for r in self.results.values() if isinstance(r, ChannelResult)]
        if not calibrations:
            return
        failed = [c for c in calibrations if c.verification != "pass"]
        if failed:
            lines = "\n".join(f"{c.channel}: {c.verification_summary()}" for c in failed)
            answer = QMessageBox.question(
                self,
                "Save failed calibrations?",
                f"These channels failed verification:\n{lines}\n\nSave anyway? A failed calibration is usually still "
                "more linear than none; the failure is recorded in the file and shown in the channel editor.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        try:
            saved = self.session.save(calibrations)
        except Exception as e:  # noqa: BLE001 - reported in the dialog
            log.error("Saving illumination calibrations failed", exc_info=True)
            self._say(f"Not saved: {e}")
            return
        message = f"Saved {', '.join(path.name for path, _ in saved)}; in use now (live channel re-sent on close)."
        backups = [str(backup) for _, backup in saved if backup is not None]
        if backups:
            message += f" Previous files moved to {', '.join(backups)}."
        self._say(message)
        self.results = {}
        self._refresh_current()
        self._set_running(False)

    # ---------------------------------------------------------------- Qt
    def _refuse_close_while_running(self) -> bool:
        if self._running():
            self._say("A run is in progress. Cancel it and wait for it to stop before closing.")
            return True
        return False

    def _shutdown(self):
        """Runs once, though QDialog.closeEvent calls reject() too: light off, meter closed, live channel back."""
        if self._shut_down:
            return
        self._shut_down = True
        self.reading_timer.stop()
        self.test_beam_timer.stop()
        self.button_test_beam.setChecked(False)  # light off
        self.session.disconnect()
        if self.session.hardware_touched and self.restore_illumination is not None:
            try:
                self.restore_illumination()
            except Exception as e:  # noqa: BLE001 - closing must not fail; the next channel switch re-sends it
                log.error(f"Re-applying the live channel after the calibration failed: {e}", exc_info=True)

    def reject(self):
        if self._refuse_close_while_running():
            return
        self._shutdown()
        super().reject()

    def closeEvent(self, event):
        if self._refuse_close_while_running():
            event.ignore()
            return
        self._shutdown()
        super().closeEvent(event)
