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

    @pyqtSlot(str, object)
    def _on_worker_done(self, job_id: str, result):
        self.busy = False
        if job_id == "connect":
            if isinstance(result, Exception):
                self.btn_connect.setEnabled(True)
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
