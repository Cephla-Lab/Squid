"""PR #540 filter-wheel silent-fail repro GUI.

Standalone PyQt5 window. Imports scenarios and firmware manager.
"""

import sys
import time
import traceback
from pathlib import Path

import serial.tools.list_ports
from PyQt5.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

import control.microcontroller as mc
from tools import pr540_repro_scenarios as scen
from tools import pr540_firmware_manager as fwm


class Worker(QObject):
    log_line = pyqtSignal(str)
    done = pyqtSignal(str, object)  # job_id, result-or-exception

    @pyqtSlot(str, object)
    def run(self, job_id, fn):
        try:
            result = fn(self.log_line.emit)
        except Exception as e:
            self.log_line.emit(f"[worker] EXCEPTION: {e!r}")
            self.log_line.emit(traceback.format_exc())
            self.done.emit(job_id, e)
            return
        self.done.emit(job_id, result)


class MainWindow(QMainWindow):
    request_run = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PR #540 Filter Wheel Silent-Fail Repro")
        self.resize(800, 900)

        self.micro = None
        self.t_baseline = None
        self.busy = False

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(self._build_connection_panel())
        layout.addWidget(self._build_firmware_panel())
        layout.addWidget(self._build_setup_panel())
        layout.addWidget(self._build_scenarios_panel())
        layout.addWidget(self._build_log_panel(), stretch=1)

        # Worker thread
        self.worker_thread = QThread()
        self.worker = Worker()
        self.worker.moveToThread(self.worker_thread)
        self.worker.log_line.connect(self._append_log)
        self.worker.done.connect(self._on_worker_done)
        self.request_run.connect(self.worker.run)
        self.worker_thread.start()

    # ---------- Connection panel ----------
    def _build_connection_panel(self) -> QGroupBox:
        box = QGroupBox("Connection")
        v = QVBoxLayout(box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Port:"))
        self.port_combo = QComboBox()
        self._refresh_ports()
        row1.addWidget(self.port_combo, stretch=1)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_ports)
        row1.addWidget(btn_refresh)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self._do_connect)
        row1.addWidget(self.btn_connect)
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.clicked.connect(self._do_disconnect)
        self.btn_disconnect.setEnabled(False)
        row1.addWidget(self.btn_disconnect)
        v.addLayout(row1)

        self.lbl_firmware = QLabel("Firmware: -.-     Status: disconnected")
        v.addWidget(self.lbl_firmware)
        self.lbl_expected = QLabel("Expected: -")
        v.addWidget(self.lbl_expected)
        return box

    def _refresh_ports(self):
        self.port_combo.clear()
        for p in serial.tools.list_ports.comports():
            self.port_combo.addItem(f"{p.device} — {p.description}", userData=p.device)

    def _do_connect(self):
        if self.busy:
            return
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "No port", "No serial port selected.")
            return

        def job(log):
            log(f"[connect] opening {port}")
            from control.microcontroller import MicrocontrollerSerial, Microcontroller

            ser = MicrocontrollerSerial(port, baudrate=2000000)
            micro = Microcontroller(serial_device=ser)
            return micro

        self._dispatch("connect", job)

    def _do_disconnect(self):
        if self.micro is not None:
            try:
                self.micro._serial._serial.close()  # best-effort
            except Exception:
                pass
            self.micro = None
        self.lbl_firmware.setText("Firmware: -.-     Status: disconnected")
        self.lbl_expected.setText("Expected: -")
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)

    # ---------- Firmware panel ----------
    def _build_firmware_panel(self) -> QGroupBox:
        box = QGroupBox("Firmware")
        v = QVBoxLayout(box)

        self.rb_post = QRadioButton(fwm.POST_FIX.label)
        self.rb_post.setChecked(True)
        self.rb_pre = QRadioButton(fwm.PRE_FIX.label)
        self.rb_custom = QRadioButton("custom git ref:")
        self.edit_custom = QLineEdit()
        self.edit_custom.setPlaceholderText("e.g. HEAD~5 or abc1234")
        v.addWidget(self.rb_post)
        v.addWidget(self.rb_pre)
        row_custom = QHBoxLayout()
        row_custom.addWidget(self.rb_custom)
        row_custom.addWidget(self.edit_custom, stretch=1)
        v.addLayout(row_custom)

        row_path = QHBoxLayout()
        row_path.addWidget(QLabel("Worktree (auto):"))
        self.lbl_worktree = QLabel(str(fwm.POST_FIX.worktree_path))
        row_path.addWidget(self.lbl_worktree, stretch=1)
        v.addLayout(row_path)
        self.rb_post.toggled.connect(self._update_worktree_label)
        self.rb_pre.toggled.connect(self._update_worktree_label)
        self.rb_custom.toggled.connect(self._update_worktree_label)
        self.edit_custom.textChanged.connect(self._update_worktree_label)

        row_btns = QHBoxLayout()
        self.btn_build = QPushButton("Build")
        self.btn_build.clicked.connect(self._do_build)
        row_btns.addWidget(self.btn_build)
        self.btn_flash = QPushButton("Flash")
        self.btn_flash.clicked.connect(self._do_flash)
        row_btns.addWidget(self.btn_flash)
        self.btn_bfr = QPushButton("Build + Flash + Reconnect")
        self.btn_bfr.clicked.connect(self._do_build_flash_reconnect)
        row_btns.addWidget(self.btn_bfr)
        v.addLayout(row_btns)
        return box

    def _selected_ref(self) -> fwm.FirmwareRef:
        if self.rb_post.isChecked():
            return fwm.POST_FIX
        if self.rb_pre.isChecked():
            return fwm.PRE_FIX
        text = self.edit_custom.text().strip() or "HEAD"
        return fwm.custom_ref(text)

    def _update_worktree_label(self):
        self.lbl_worktree.setText(str(self._selected_ref().worktree_path))

    def _confirm_worktree_reset(self, exc: fwm.WorktreeMismatch) -> bool:
        return (
            QMessageBox.question(
                self,
                "Worktree mismatch",
                f"Worktree at {exc.path} is at {exc.current_sha[:10]} but the chosen ref "
                f"resolves to {exc.expected_sha[:10]}.\n\nReset --hard to {exc.expected_sha[:10]}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        )

    def _do_build(self):
        ref = self._selected_ref()

        def job(log):
            wt = fwm.ensure_worktree(ref, allow_reset=False, log_cb=log)
            fwm.build_firmware(wt, log_cb=log)

        self._dispatch("build", job)

    def _do_flash(self):
        ref = self._selected_ref()

        def job(log):
            wt = fwm.ensure_worktree(ref, allow_reset=False, log_cb=log)
            fwm.flash_firmware(wt, log_cb=log)

        self._dispatch("flash", job)

    def _do_build_flash_reconnect(self):
        ref = self._selected_ref()
        port = self.port_combo.currentData()
        was_connected = self.micro is not None
        if was_connected:
            self._do_disconnect()

        def job(log):
            wt = fwm.ensure_worktree(ref, allow_reset=False, log_cb=log)
            fwm.build_firmware(wt, log_cb=log)
            fwm.flash_firmware(wt, log_cb=log)
            if port and was_connected:
                log("[firmware] waiting 3s for Teensy reboot")
                time.sleep(3.0)
                from control.microcontroller import MicrocontrollerSerial, Microcontroller

                ser = MicrocontrollerSerial(port, baudrate=2000000)
                return Microcontroller(serial_device=ser)
            return None

        self._dispatch("bfr", job)

    # ---------- Log panel ----------
    def _build_log_panel(self) -> QGroupBox:
        box = QGroupBox("Log")
        v = QVBoxLayout(box)
        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setStyleSheet("font-family: Menlo, Consolas, monospace; font-size: 11px;")
        v.addWidget(self.log_widget, stretch=1)

        row = QHBoxLayout()
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.log_widget.clear)
        row.addWidget(btn_clear)
        btn_save = QPushButton("Save log…")
        btn_save.clicked.connect(self._save_log)
        row.addWidget(btn_save)
        row.addStretch(1)
        v.addLayout(row)
        return box

    def _append_log(self, line: str):
        ts = time.strftime("%H:%M:%S")
        self.log_widget.appendPlainText(f"{ts}  {line}")

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save log", "pr540_repro_log.txt", "Text (*.txt)")
        if path:
            Path(path).write_text(self.log_widget.toPlainText())

    # ---------- Worker dispatch ----------
    def _dispatch(self, job_id: str, fn):
        self.busy = True
        self.btn_connect.setEnabled(False)
        self.request_run.emit(job_id, fn)

    # ---------- Setup panel ----------
    def _build_setup_panel(self) -> QGroupBox:
        box = QGroupBox("Setup")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        self.spin_transitions = QSpinBox()
        self.spin_transitions.setRange(1, 64)
        self.spin_transitions.setValue(8)
        row.addWidget(QLabel("Transitions per revolution:"))
        row.addWidget(self.spin_transitions)
        row.addStretch(1)
        v.addLayout(row)
        row2 = QHBoxLayout()
        self.btn_baseline = QPushButton("Init filter wheel + measure baseline")
        self.btn_baseline.clicked.connect(self._do_baseline)
        row2.addWidget(self.btn_baseline)
        self.lbl_baseline = QLabel("Baseline single-slot time: -- ms")
        row2.addWidget(self.lbl_baseline)
        row2.addStretch(1)
        v.addLayout(row2)
        return box

    def _usteps_per_slot(self) -> int:
        from control._def import FULLSTEPS_PER_REV_W, MICROSTEPPING_DEFAULT_W

        return int(FULLSTEPS_PER_REV_W * MICROSTEPPING_DEFAULT_W / self.spin_transitions.value())

    def _do_baseline(self):
        if not self._require_connected():
            return
        usteps = self._usteps_per_slot()
        micro = self.micro

        def job(log):
            return scen.measure_baseline(micro, usteps_per_slot=usteps, log_cb=log)

        self._dispatch("baseline", job)

    # ---------- Scenarios panel ----------
    def _build_scenarios_panel(self) -> QGroupBox:
        box = QGroupBox("Scenarios")
        v = QVBoxLayout(box)

        # Scenario A
        rowA = QHBoxLayout()
        self.btn_a = QPushButton("Run A (pre-INIT MOVE_W)")
        self.btn_a.clicked.connect(self._do_a)
        rowA.addWidget(self.btn_a)
        self.lbl_a = QLabel("Result: -")
        rowA.addWidget(self.lbl_a, stretch=1)
        v.addLayout(rowA)

        # Scenario B controls
        rowB = QHBoxLayout()
        self.btn_b = QPushButton("Run B (rapid burst)")
        self.btn_b.clicked.connect(self._do_b)
        rowB.addWidget(self.btn_b)
        rowB.addWidget(QLabel("Burst:"))
        self.spin_burst = QSpinBox()
        self.spin_burst.setRange(2, 20)
        self.spin_burst.setValue(3)
        rowB.addWidget(self.spin_burst)
        rowB.addWidget(QLabel("Iters:"))
        self.spin_b_iters = QSpinBox()
        self.spin_b_iters.setRange(1, 1000)
        self.spin_b_iters.setValue(20)
        rowB.addWidget(self.spin_b_iters)
        self.lbl_b = QLabel("Result: -")
        rowB.addWidget(self.lbl_b, stretch=1)
        v.addLayout(rowB)

        # Scenario C controls
        rowC = QHBoxLayout()
        self.btn_c = QPushButton("Run C (soak)")
        self.btn_c.clicked.connect(self._do_c)
        rowC.addWidget(self.btn_c)
        rowC.addWidget(QLabel("Iters:"))
        self.spin_c_iters = QSpinBox()
        self.spin_c_iters.setRange(10, 10000)
        self.spin_c_iters.setValue(1000)
        rowC.addWidget(self.spin_c_iters)
        rowC.addWidget(QLabel("Threshold:"))
        self.spin_threshold = QDoubleSpinBox()
        self.spin_threshold.setRange(0.1, 0.95)
        self.spin_threshold.setSingleStep(0.05)
        self.spin_threshold.setValue(0.5)
        rowC.addWidget(self.spin_threshold)
        self.lbl_c = QLabel("Result: -")
        rowC.addWidget(self.lbl_c, stretch=1)
        v.addLayout(rowC)

        # Version gate
        rowG = QHBoxLayout()
        self.btn_gate = QPushButton("Run gate test")
        self.btn_gate.clicked.connect(self._do_gate)
        rowG.addWidget(self.btn_gate)
        self.lbl_gate = QLabel("Result: -")
        rowG.addWidget(self.lbl_gate, stretch=1)
        v.addLayout(rowG)

        # Run all
        self.btn_all = QPushButton("Run all scenarios")
        self.btn_all.clicked.connect(self._do_all)
        v.addWidget(self.btn_all)

        return box

    def _require_connected(self):
        if self.micro is None:
            QMessageBox.warning(self, "Not connected", "Connect to a microcontroller first.")
            return False
        return True

    def _require_baseline(self):
        if self.t_baseline is None:
            QMessageBox.warning(self, "No baseline", "Run 'Init + measure baseline' first.")
            return False
        return True

    def _do_a(self):
        if not self._require_connected():
            return
        micro = self.micro
        self._dispatch("scenario_a", lambda log: scen.scenario_a_pre_init_move(micro, log))

    def _do_b(self):
        if not self._require_connected() or not self._require_baseline():
            return
        burst = self.spin_burst.value()
        iters = self.spin_b_iters.value()
        usteps = self._usteps_per_slot()
        threshold = self.spin_threshold.value()
        t_base = self.t_baseline
        micro = self.micro
        self._dispatch(
            "scenario_b",
            lambda log: scen.scenario_b_rapid_burst(
                micro,
                log,
                burst_size=burst,
                iterations=iters,
                usteps_per_slot=usteps,
                t_baseline=t_base,
                threshold=threshold,
            ),
        )

    def _do_c(self):
        if not self._require_connected() or not self._require_baseline():
            return
        iters = self.spin_c_iters.value()
        usteps = self._usteps_per_slot()
        threshold = self.spin_threshold.value()
        t_base = self.t_baseline
        micro = self.micro
        self._dispatch(
            "scenario_c",
            lambda log: scen.scenario_c_soak(
                micro,
                log,
                iterations=iters,
                usteps_per_slot=usteps,
                t_baseline=t_base,
                threshold=threshold,
            ),
        )

    def _do_gate(self):
        if not self._require_connected():
            return
        from squid.config import SquidFilterWheelConfig

        cfg = SquidFilterWheelConfig(
            max_index=8,
            min_index=1,
            offset=0.0,
            motor_slot_index=3,
            transitions_per_revolution=self.spin_transitions.value(),
        )
        micro = self.micro
        self._dispatch("gate", lambda log: scen.host_version_gate(micro, cfg, log))

    def _do_all(self):
        if not self._require_connected() or not self._require_baseline():
            return
        burst = self.spin_burst.value()
        b_iters = self.spin_b_iters.value()
        c_iters = self.spin_c_iters.value()
        usteps = self._usteps_per_slot()
        threshold = self.spin_threshold.value()
        t_base = self.t_baseline
        micro = self.micro
        from squid.config import SquidFilterWheelConfig

        cfg = SquidFilterWheelConfig(
            max_index=8,
            min_index=1,
            offset=0.0,
            motor_slot_index=3,
            transitions_per_revolution=self.spin_transitions.value(),
        )

        def job(log):
            return {
                "A": scen.scenario_a_pre_init_move(micro, log),
                "B": scen.scenario_b_rapid_burst(
                    micro,
                    log,
                    burst_size=burst,
                    iterations=b_iters,
                    usteps_per_slot=usteps,
                    t_baseline=t_base,
                    threshold=threshold,
                ),
                "C": scen.scenario_c_soak(
                    micro,
                    log,
                    iterations=c_iters,
                    usteps_per_slot=usteps,
                    t_baseline=t_base,
                    threshold=threshold,
                ),
                "Gate": scen.host_version_gate(micro, cfg, log),
            }

        self._dispatch("all", job)

    @pyqtSlot(str, object)
    def _on_worker_done(self, job_id: str, result):
        self.busy = False
        self.btn_connect.setEnabled(self.micro is None)

        if job_id == "connect":
            if isinstance(result, Exception):
                QMessageBox.warning(self, "Connect failed", str(result))
                return
            self.micro = result
            fw = self.micro.firmware_version
            expected = scen.expected_verdict_for_firmware(fw)
            self.lbl_firmware.setText(f"Firmware: v{fw[0]}.{fw[1]}     Status: connected")
            self.lbl_expected.setText(
                f"Expected: {expected} — " + ("PASS on all" if expected == "post-fix" else "OBSERVED-BUG on A/B/C")
            )
            self.btn_disconnect.setEnabled(True)
            return

        if job_id == "baseline":
            if isinstance(result, Exception):
                self.lbl_baseline.setText("Baseline: ERROR — see log")
                return
            self.t_baseline = float(result)
            self.lbl_baseline.setText(f"Baseline single-slot time: {self.t_baseline*1000:.1f} ms")
            return

        scenario_label_map = {
            "scenario_a": self.lbl_a,
            "scenario_b": self.lbl_b,
            "scenario_c": self.lbl_c,
            "gate": self.lbl_gate,
        }
        if job_id in scenario_label_map:
            label = scenario_label_map[job_id]
            if isinstance(result, Exception):
                label.setText(f"Result: ERROR — {result!r}")
            else:
                label.setText(f"Result: {result.verdict} — {result.summary}")
            return

        if job_id == "all":
            if isinstance(result, Exception):
                QMessageBox.warning(self, "Run-all failed", str(result))
                return
            for key, lbl in (("A", self.lbl_a), ("B", self.lbl_b), ("C", self.lbl_c), ("Gate", self.lbl_gate)):
                r = result[key]
                lbl.setText(f"Result: {r.verdict} — {r.summary}")
            return

        if job_id in ("build", "flash"):
            if isinstance(result, fwm.WorktreeMismatch):
                if self._confirm_worktree_reset(result):
                    self._append_log("[firmware] retrying with allow_reset=True")
                    ref = self._selected_ref()

                    def job(log):
                        wt = fwm.ensure_worktree(ref, allow_reset=True, log_cb=log)
                        if job_id == "build":
                            fwm.build_firmware(wt, log_cb=log)
                        else:
                            fwm.flash_firmware(wt, log_cb=log)

                    self._dispatch(job_id, job)
                return
            if isinstance(result, Exception):
                QMessageBox.warning(self, f"{job_id} failed", repr(result))
                return
            self._append_log(f"[firmware] {job_id} OK")
            return

        if job_id == "bfr":
            if isinstance(result, fwm.WorktreeMismatch):
                if self._confirm_worktree_reset(result):
                    self._append_log("[firmware] retrying build+flash+reconnect with allow_reset=True")
                    ref = self._selected_ref()
                    port = self.port_combo.currentData()

                    def job(log):
                        wt = fwm.ensure_worktree(ref, allow_reset=True, log_cb=log)
                        fwm.build_firmware(wt, log_cb=log)
                        fwm.flash_firmware(wt, log_cb=log)
                        if port:
                            log("[firmware] waiting 3s for Teensy reboot")
                            time.sleep(3.0)
                            from control.microcontroller import MicrocontrollerSerial, Microcontroller

                            ser = MicrocontrollerSerial(port, baudrate=2000000)
                            return Microcontroller(serial_device=ser)
                        return None

                    self._dispatch("bfr", job)
                return
            if isinstance(result, Exception):
                QMessageBox.warning(self, "build+flash+reconnect failed", repr(result))
                return
            if result is not None:
                self.micro = result
                fw = self.micro.firmware_version
                expected = scen.expected_verdict_for_firmware(fw)
                self.lbl_firmware.setText(f"Firmware: v{fw[0]}.{fw[1]}     Status: connected")
                self.lbl_expected.setText(
                    f"Expected: {expected} — " + ("PASS on all" if expected == "post-fix" else "OBSERVED-BUG on A/B/C")
                )
                self.btn_disconnect.setEnabled(True)
            return

    def closeEvent(self, e):
        self._do_disconnect()
        self.worker_thread.quit()
        self.worker_thread.wait(2000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
