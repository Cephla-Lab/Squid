"""Motion self-test window: runs squid.motion_selftest.ZMotionSelfTest on the open controller connection.

Opened from the GUI's Utils menu. It homes Z and moves it up to the working depth plus 2.5 mm, so it asks
first; the log streams into the window and the report ends with the ini values the measurements recommend.
"""

from qtpy.QtCore import QObject, QThread, Signal
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

import squid.logging
from squid.motion_selftest import ZMotionSelfTest

_log = squid.logging.get_logger(__name__)


class _SelfTestWorker(QObject):
    signal_log = Signal(str)
    signal_done = Signal(object)

    def __init__(self, test: ZMotionSelfTest):
        super().__init__()
        self.test = test

    def run(self):
        report = self.test.run()
        self.signal_done.emit(report)


class MotionSelfTestDialog(QDialog):
    signal_finished = Signal(bool)   # report.passed, once the run is over

    def __init__(self, microcontroller, axis_config, stage=None, parent=None):
        super().__init__(parent)
        self.stage = stage
        self.setWindowTitle("Motion self-test (Z)")
        self.setMinimumSize(720, 520)
        self.microcontroller = microcontroller
        self.axis_config = axis_config
        self._thread = None
        self._worker = None
        self._cancel = False
        self._report = None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Verifies the Z axis against its configuration: homing, encoder scale and sign, the gap above home, "
            "lost steps at the configured speed, and the closed loop (stack, step, hold). It homes Z and moves it "
            "up to about 4.5 mm from the switch. Keep hands and samples clear. The report ends with the ini values "
            "the measurements recommend; nothing is written to the ini automatically."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Press Start to run the self-test.")
        layout.addWidget(self.log_view, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_copy = QPushButton("Copy report")
        self.btn_close = QPushButton("Close")
        self.btn_cancel.setEnabled(False)
        self.btn_copy.setEnabled(False)
        buttons.addWidget(self.btn_start)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_copy)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)

        self.btn_start.clicked.connect(self.start)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_copy.clicked.connect(self.copy_report)
        self.btn_close.clicked.connect(self.close)

    # ------------------------------------------------------------------ actions
    def start(self, confirm: bool = True):
        if self._thread is not None:
            return
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Motion self-test")
        msg.setText("The self-test homes Z and moves it. Continue?")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        if confirm and msg.exec_() != QMessageBox.Ok:
            return
        self.log_view.clear()
        self.summary.setText("running...")
        self._cancel = False
        self._report = None
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_copy.setEnabled(False)
        self.btn_close.setEnabled(False)

        self._worker = _SelfTestWorker(None)
        test = ZMotionSelfTest(
            self.microcontroller, self.axis_config, log=self._worker.signal_log.emit, cancel=lambda: self._cancel,
            stage=self.stage,
        )
        self._worker.test = test
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._worker.signal_log.connect(self._append)
        self._worker.signal_done.connect(self._done)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def cancel(self):
        self._cancel = True
        self.btn_cancel.setEnabled(False)
        self._append("cancel requested; stopping after the current move")

    def copy_report(self):
        if self._report is not None:
            QApplication.clipboard().setText(self._report.text())

    # ------------------------------------------------------------------ slots
    def _append(self, line: str):
        self.log_view.appendPlainText(line)

    def _done(self, report):
        self._report = report
        self.summary.setText(
            ("PASS" if report.passed else "FAIL")
            + (f" - {report.aborted}" if report.aborted else "")
            + (f" - recommended: " + ", ".join(f"{k} = {v}" for k, v in report.recommendations.items())
               if report.recommendations else "")
        )
        _log.info("motion self-test finished: " + ("PASS" if report.passed else "FAIL"))
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_copy.setEnabled(True)
        self.btn_close.setEnabled(True)
        self._thread.quit()
        self._thread.wait(2000)
        self._thread = None
        self._worker = None
        self.signal_finished.emit(report.passed)

    def closeEvent(self, event):
        if self._thread is not None:
            event.ignore()
            return
        super().closeEvent(event)
