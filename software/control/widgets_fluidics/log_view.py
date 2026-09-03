"""The Fluidics Log tab (always autoscrolls) and the Reagents table.

The log pane polls a squid.logging.BufferingHandler on a QTimer — the WarningErrorWidget pattern.
The handler is attached to the fluidics loggers only (the library's fluidics.*/XCaliburD and Squid's
fluidics packages), so the pane catches fluidics records and never sees the rest of Squid's logging."""

import csv
import logging
from typing import List, Optional, Tuple

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import squid.logging

_LEVELS = {"INFO": logging.INFO, "DEBUG": logging.DEBUG}


class FluidicsLogView(QWidget):
    POLL_INTERVAL_MS = 200
    MAX_BLOCKS = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(__name__)
        self._handler: Optional[squid.logging.BufferingHandler] = None
        self._poll_timer: Optional[QTimer] = None

        self.level_combo = QComboBox()
        self.level_combo.addItems(list(_LEVELS))
        self.level_combo.currentTextChanged.connect(self._on_level_changed)
        self.save_button = QPushButton("Save log…")
        self.save_button.clicked.connect(self._save_log)
        top = QHBoxLayout()
        top.addWidget(QLabel("Level:"))
        top.addWidget(self.level_combo)
        top.addStretch(1)
        top.addWidget(self.save_button)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(self.MAX_BLOCKS)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.text_edit)
        self.setLayout(layout)

    def _fluidics_loggers(self) -> list:
        """The loggers whose subtrees are fluidics — the library's (forwarded records keep
        their own names) and Squid's fluidics packages. Records propagate up to a handler
        here, so the pane catches fluidics only without touching the rest of Squid."""
        # The two library names mirror control.fluidics_system.LIBRARY_LOGGER_NAMES; kept as
        # literals on purpose so this pane (and its tests) stay importable when the fluidics
        # library isn't installed. If that tuple gains a logger, add it here too.
        return [
            logging.getLogger("fluidics"),
            logging.getLogger("XCaliburD"),
            squid.logging.get_logger("control.widgets_fluidics"),
            squid.logging.get_logger("control.core.fluidics_protocol"),
            squid.logging.get_logger("control.fluidics_system"),
        ]

    def connect_logging(self) -> None:
        """Attach the buffering handler to the fluidics loggers and start polling. GUI thread only."""
        self.disconnect_logging()
        self._handler = squid.logging.BufferingHandler(min_level=_LEVELS[self.level_combo.currentText()])
        for logger in self._fluidics_loggers():
            logger.addHandler(self._handler)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(self.POLL_INTERVAL_MS)

    def disconnect_logging(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if self._handler is not None:
            for logger in self._fluidics_loggers():
                logger.removeHandler(self._handler)
            self._handler = None

    def _on_level_changed(self, text: str) -> None:
        if self._handler is not None:
            self._handler.setLevel(_LEVELS.get(text, logging.INFO))

    def _poll(self) -> None:
        if self._handler is None:
            return
        try:
            pending = self._handler.get_pending()
        except Exception as e:  # Qt swallows timer-slot exceptions: log explicitly
            self._log.error(f"Error polling the fluidics log: {e}")
            return
        if not pending:
            return
        for _level, _name, message in pending:
            self.text_edit.appendPlainText(message)
        bar = self.text_edit.verticalScrollBar()
        bar.setValue(bar.maximum())  # the log always follows the newest line

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save log", "fluidics.log", "Log files (*.log *.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text_edit.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "Save failed", f"Could not save the log:\n{e}")


class ReagentsTable(QWidget):
    """Reagent drawn per port — last step, this run, since Initialize. A dumb view: the record widget
    computes the totals from the run manifest and the library's usage ledger."""

    COLUMNS = ("Port", "Reagent", "Last step (µL)", "This run (µL)", "Since init (µL)")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(self._export)
        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self.export_button)
        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self._rows: List[Tuple] = []

    def set_rows(self, rows: List[Tuple[int, Optional[str], float, float, float]]) -> None:
        self._rows = list(rows)
        self.table.setRowCount(len(self._rows))
        for r, (port, reagent, last_step, this_run, since_init) in enumerate(self._rows):
            values = (str(port), reagent or "—", f"{last_step:.0f}", f"{this_run:.0f}", f"{since_init:.0f}")
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(value))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export reagent use", "reagents.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["port", "reagent", "last_step_ul", "this_run_ul", "since_init_ul"])
                for port, reagent, last_step, this_run, since_init in self._rows:
                    writer.writerow([port, reagent or "", last_step, this_run, since_init])
        except OSError as e:
            QMessageBox.warning(self, "Export failed", f"Could not export the table:\n{e}")
