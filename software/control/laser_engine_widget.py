"""GUI tab showing live status of the Squid laser engine.

Driven by `SquidLaserEngine.status_updated` — no QTimer-based polling here.
"""

from typing import Optional

from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from control.serial_peripherals import (
    LASER_CHANNEL_ORDER,
    LaserChannelState,
    SquidLaserEngineStatus,
)


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


def _format_temp(info) -> str:
    # Use one trailing °C even for multi-module channels (55x) to keep the line short.
    temps = " / ".join(f"{m.temperature_c:.1f}" for m in info.modules)
    return f"{temps} °C"


def _format_diff(info) -> str:
    return " / ".join(f"{m.setpoint_diff_c:+.1f}" for m in info.modules)


class LaserEngineWidget(QWidget):
    """Tab content showing live status of a SquidLaserEngine."""

    def __init__(self, engine, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._engine = engine
        self._last_status: Optional[SquidLaserEngineStatus] = None
        self._channel_lines: dict = {}
        self._build_ui()
        engine.status_updated.connect(self._on_status_updated)
        engine.connection_lost.connect(self._on_connection_lost)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

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

        self._banner = QLabel("")
        self._banner.setStyleSheet("background-color: #c0392b; color: white; padding: 4px;")
        self._banner.setVisible(False)
        layout.addWidget(self._banner)

        # One QLabel per channel, plain monospaced text so column padding aligns.
        for key in LASER_CHANNEL_ORDER:
            line = QLabel("—")
            line.setStyleSheet("font-family: monospace;")
            self._channel_lines[key] = line
            layout.addWidget(line)

        layout.addStretch(1)

    def _on_status_updated(self, status: SquidLaserEngineStatus) -> None:
        self._last_status = status
        self._refresh_channel_lines()
        self._refresh_summary()

    def _on_connection_lost(self, message: str) -> None:
        self._banner.setText(f"Laser engine disconnected: {message}")
        self._banner.setVisible(True)
        self._refresh_summary()

    def _refresh_channel_lines(self) -> None:
        if self._last_status is None:
            return
        for key in LASER_CHANNEL_ORDER:
            info = self._last_status.channels.get(key)
            if info is None:
                continue
            state = info.display_state
            on_off = "ON" if info.laser_ttl_on else "OFF"
            self._channel_lines[key].setText(
                f"{key:>4}  {on_off:<3}  {state.name:<14}  {_format_temp(info)}  ΔT {_format_diff(info)}"
            )

    def _refresh_summary(self) -> None:
        connection_lost = self._engine.is_connection_lost()
        color = _engine_summary_color(self._last_status, connection_lost)
        self._engine_dot.setStyleSheet(f"font-size: 16pt; color: {color.name()};")
        self._engine_label.setText(f"Engine: {_engine_summary_label(self._last_status, connection_lost)}")
