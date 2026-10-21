"""GUI tab showing live status of the Vortran Stradus VersaLase heads."""

import threading
from typing import Callable, List, Optional

from qtpy.QtCore import QTimer, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control.illumination_versalase import HeadStatus

POLL_INTERVAL_MS = 3000
COLUMNS = [
    "Laser",
    "Emission",
    "Mode",
    "Set / max (mW)",
    "Measured (mW)",
    "Base plate (°C)",
    "Optical block (°C)",
    "Interlock",
    "Hours",
    "Status",
]
RED = "#c0392b"
AMBER = "#daa520"
GREEN = "#2e8b57"
GREY = "#888888"


def _summary(statuses: List[HeadStatus]):
    """Return (color, text) for the summary line."""
    if not statuses:
        return GREY, "No lasers"
    if any(s.faulted for s in statuses) or not all(s.interlock_closed for s in statuses):
        return RED, "Fault"
    if all(s.emission for s in statuses):
        return GREEN, "Ready"
    return AMBER, "Emission off"


def _row(status: HeadStatus) -> List[str]:
    return [
        f"{status.wavelength} nm",
        "On" if status.emission else "Off",
        "TTL" if status.modulation else "CW",
        f"{status.set_power_mw:.1f} / {status.max_power_mw:.1f}",
        f"{status.measured_power_mw:.1f}",
        f"{status.base_plate_temp_c:.1f}",
        f"{status.optical_block_temp_c:.1f}",
        "Closed" if status.interlock_closed else "OPEN",
        f"{status.hours:.0f}",
        status.fault_description or "OK",
    ]


class VersaLaseWidget(QWidget):
    """Tab content showing live status of a VersaLase.

    Status is read on a worker thread every few seconds, only while the tab is visible, and not while
    is_busy() returns True (an acquisition is running), so status queries never hold up a head when
    the acquisition switches channels.
    """

    _status_ready = Signal(object)
    _poll_failed = Signal(str)

    def __init__(self, versalase, is_busy: Callable[[], bool] = lambda: False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._versalase = versalase
        self._is_busy = is_busy
        self._poll_thread: Optional[threading.Thread] = None
        self._build_ui()
        self._status_ready.connect(self._on_status)
        self._poll_failed.connect(self._on_poll_failed)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"font-size: 16pt; color: {GREY};")
        self._summary_label = QLabel("VersaLase: Waiting…")
        self._paused_label = QLabel("")
        top_row.addWidget(self._dot)
        top_row.addWidget(self._summary_label)
        top_row.addStretch(1)
        top_row.addWidget(self._paused_label)
        layout.addLayout(top_row)

        self._banner = QLabel("")
        self._banner.setStyleSheet(f"background-color: {RED}; color: white; padding: 4px;")
        self._banner.setWordWrap(True)
        self._banner.setVisible(False)
        layout.addWidget(self._banner)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.poll()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def poll(self) -> None:
        """Start a status read unless one is running or an acquisition is in progress."""
        if self._is_busy():
            self._paused_label.setText("Paused during acquisition")
            return
        self._paused_label.setText("")
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_thread = threading.Thread(target=self._read_status, name="VersaLase status", daemon=True)
        self._poll_thread.start()

    def _read_status(self) -> None:
        try:
            statuses = self._versalase.get_status()
        except Exception as e:
            self._emit(self._poll_failed, str(e))
            return
        self._emit(self._status_ready, statuses)

    @staticmethod
    def _emit(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:
            pass  # the widget was deleted while the read was in flight

    def _on_status(self, statuses: List[HeadStatus]) -> None:
        self._banner.setVisible(False)
        self._table.setRowCount(len(statuses))
        for row, status in enumerate(statuses):
            for column, text in enumerate(_row(status)):
                item = QTableWidgetItem(text)
                if (COLUMNS[column] == "Status" and status.faulted) or (
                    COLUMNS[column] == "Interlock" and not status.interlock_closed
                ):
                    item.setForeground(QColor(RED))
                self._table.setItem(row, column, item)
        color, text = _summary(statuses)
        self._set_summary(color, text)

    def _on_poll_failed(self, message: str) -> None:
        self._banner.setText(f"Lost contact with the VersaLase: {message}")
        self._banner.setVisible(True)
        self._set_summary(RED, "Disconnected")

    def _set_summary(self, color: str, text: str) -> None:
        self._dot.setStyleSheet(f"font-size: 16pt; color: {color};")
        self._summary_label.setText(f"VersaLase: {text}")
