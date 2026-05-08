"""GUI tab showing live status of the Squid laser engine.

Driven by `SquidLaserEngine.status_updated` — no QTimer-based polling here.
"""

import time
from typing import Optional

from qtpy.QtCore import QTimer
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control.serial_peripherals import (
    LASER_CHANNEL_ORDER,
    LaserChannelState,
    SquidLaserEngineStatus,
)

_COLUMN_HEADERS = ("Ch", "State", "Temp", "ΔT", "Laser TTL")

_STATE_COLORS = {
    LaserChannelState.ACTIVE: QColor("#2e8b57"),  # green
    LaserChannelState.CHECK_ACTIVE: QColor("#daa520"),  # amber-ish
    LaserChannelState.WARMING_UP: QColor("#daa520"),
    LaserChannelState.WAKE_UP: QColor("#daa520"),
    LaserChannelState.SLEEP: QColor("#888888"),
    LaserChannelState.PREPARE_SLEEP: QColor("#888888"),
    LaserChannelState.CHECK_ERROR: QColor("#c0392b"),
    LaserChannelState.ERROR: QColor("#c0392b"),
}


def _engine_summary_color(status: Optional[SquidLaserEngineStatus], connection_lost: bool) -> QColor:
    if connection_lost or status is None:
        return QColor("#c0392b")
    if status.any_error():
        return QColor("#c0392b")
    states = [info.display_state for info in status.channels.values()]
    if any(s in (LaserChannelState.SLEEP, LaserChannelState.PREPARE_SLEEP) for s in states):
        return QColor("#888888")
    if all(s == LaserChannelState.ACTIVE for s in states):
        return QColor("#2e8b57")
    return QColor("#daa520")


def _engine_summary_label(status: Optional[SquidLaserEngineStatus], connection_lost: bool) -> str:
    if connection_lost:
        return "Disconnected"
    if status is None:
        return "Waiting…"
    if status.any_error():
        return "Error"
    states = [info.display_state for info in status.channels.values()]
    if all(s == LaserChannelState.ACTIVE for s in states):
        return "Ready"
    if any(s in (LaserChannelState.SLEEP, LaserChannelState.PREPARE_SLEEP) for s in states):
        return "Sleeping"
    return "Warming up"


def _format_temp_cell(info) -> str:
    if len(info.modules) == 1:
        return f"{info.modules[0].temperature_c:.2f} °C"
    # 55x: show both
    return " / ".join(f"{m.temperature_c:.2f}" for m in info.modules)


def _format_diff_cell(info) -> str:
    if len(info.modules) == 1:
        return f"{info.modules[0].setpoint_diff_c:+.2f}"
    return " / ".join(f"{m.setpoint_diff_c:+.2f}" for m in info.modules)


class LaserEngineWidget(QWidget):
    """Tab content showing live status of a SquidLaserEngine."""

    def __init__(self, engine, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._engine = engine
        self._last_status: Optional[SquidLaserEngineStatus] = None
        self._last_status_received_s: Optional[float] = None
        self._build_ui()
        engine.status_updated.connect(self._on_status_updated)
        engine.connection_lost.connect(self._on_connection_lost)

        # 1s timer just to refresh "Last update: X s ago" — no I/O.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh_age_label)
        self._refresh_timer.start()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Top row: engine status + buttons.
        top_row = QHBoxLayout()
        self._engine_dot = QLabel("●")
        self._engine_dot.setStyleSheet("font-size: 16pt; color: #888888;")
        self._engine_label = QLabel("Engine: Waiting…")
        self._wake_btn = QPushButton("Wake All")
        self._sleep_btn = QPushButton("Sleep All")
        # Wake All / Sleep All call serial.write() up to 5 times on the GUI thread.
        # pyserial.Serial.write() returns when the kernel accepts the bytes, so the
        # block is sub-millisecond per call — well under the threshold that would
        # warrant a worker thread.
        self._wake_btn.clicked.connect(self._engine.wake_up_all)
        self._sleep_btn.clicked.connect(self._engine.sleep_all)
        top_row.addWidget(self._engine_dot)
        top_row.addWidget(self._engine_label)
        top_row.addStretch(1)
        top_row.addWidget(self._wake_btn)
        top_row.addWidget(self._sleep_btn)
        layout.addLayout(top_row)

        # Disconnect banner (hidden by default).
        self._banner = QLabel("")
        self._banner.setStyleSheet("background-color: #c0392b; color: white; padding: 4px;")
        self._banner.setVisible(False)
        layout.addWidget(self._banner)

        # Per-channel table.
        self._table = QTableWidget(len(LASER_CHANNEL_ORDER), len(_COLUMN_HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(_COLUMN_HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        for row, key in enumerate(LASER_CHANNEL_ORDER):
            self._table.setItem(row, 0, QTableWidgetItem(key))
            for col in range(1, len(_COLUMN_HEADERS)):
                self._table.setItem(row, col, QTableWidgetItem(""))
        self._table.resizeColumnsToContents()
        layout.addWidget(self._table)

        self._age_label = QLabel("Last update: —")
        layout.addWidget(self._age_label)
        layout.addStretch(1)

    # ── Slots ───────────────────────────────────────────────────────────────

    def _on_status_updated(self, status: SquidLaserEngineStatus) -> None:
        self._last_status = status
        self._last_status_received_s = time.monotonic()
        self._refresh_table()
        self._refresh_summary()
        self._refresh_age_label()

    def _on_connection_lost(self, message: str) -> None:
        self._banner.setText(f"Laser engine disconnected: {message}")
        self._banner.setVisible(True)
        self._refresh_summary()

    def _refresh_table(self) -> None:
        if self._last_status is None:
            return
        for row, key in enumerate(LASER_CHANNEL_ORDER):
            info = self._last_status.channels.get(key)
            if info is None:
                continue
            state = info.display_state
            state_item = self._table.item(row, 1)
            state_item.setText(state.name)
            state_item.setForeground(_STATE_COLORS.get(state, QColor("black")))
            self._table.item(row, 2).setText(_format_temp_cell(info))
            self._table.item(row, 3).setText(_format_diff_cell(info))
            self._table.item(row, 4).setText("ON" if info.laser_ttl_on else "OFF")

    def _refresh_summary(self) -> None:
        connection_lost = self._engine.is_connection_lost()
        color = _engine_summary_color(self._last_status, connection_lost)
        self._engine_dot.setStyleSheet(f"font-size: 16pt; color: {color.name()};")
        self._engine_label.setText(f"Engine: {_engine_summary_label(self._last_status, connection_lost)}")

    def _refresh_age_label(self) -> None:
        if self._last_status_received_s is None:
            self._age_label.setText("Last update: —")
            return
        age = time.monotonic() - self._last_status_received_s
        self._age_label.setText(f"Last update: {age:.1f} s ago")
