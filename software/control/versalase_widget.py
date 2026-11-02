"""GUI tab showing live status of the Vortran Stradus VersaLase heads."""

import threading
from typing import Callable, List, Optional

from qtpy.QtCore import QTimer, Signal
from qtpy.QtGui import QFontDatabase
from qtpy.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from control.illumination_versalase import HeadStatus

POLL_INTERVAL_MS = 3000
# A full status read is ~9 queries per head; long enough for one in flight to finish.
STOP_TIMEOUT_S = 15.0
RED = "#c0392b"
AMBER = "#daa520"
GREEN = "#2e8b57"
GREY = "#888888"

# ?FC laser states below the fault codes
STANDBY = 1
WARMUP = 2


def _summary(statuses: List[HeadStatus]):
    """Return (color, text) for the summary line."""
    if not statuses:
        return GREY, "No lasers"
    if any(s.faulted or not s.interlock_closed for s in statuses):
        return RED, "Fault"
    if all(s.emission for s in statuses):
        return GREEN, "Ready"
    return AMBER, "Emission off"


def _head_color(status: HeadStatus) -> str:
    if status.faulted or not status.interlock_closed:
        return RED
    if not status.emission:
        return GREY
    if status.fault_code & (STANDBY | WARMUP):
        return AMBER
    return GREEN


def _head_line(status: HeadStatus) -> str:
    if not status.interlock_closed:
        state = "INTERLOCK OPEN"
    else:
        state = status.fault_description or "OK"
    return (
        f"{status.wavelength:>3} nm {'ON ' if status.emission else 'OFF'} {'TTL' if status.modulation else 'CW ':<3} "
        f"{status.set_power_mw:5.1f}/{status.max_power_mw:5.1f} mW  "
        f"{status.base_plate_temp_c:4.1f}/{status.optical_block_temp_c:4.1f} °C  {state}"
    )


def _head_tooltip(status: HeadStatus) -> str:
    return (
        f"S/N {status.serial_number}\n"
        f"{'Digital modulation (TTL on the SMB input)' if status.modulation else 'CW'}\n"
        f"Set {status.set_power_mw:.1f} mW of {status.max_power_mw:.1f} mW max, "
        f"measured {status.measured_power_mw:.1f} mW (near 0 while the TTL line is off)\n"
        f"Base plate {status.base_plate_temp_c:.1f} °C, optical block {status.optical_block_temp_c:.1f} °C\n"
        f"Interlock {'closed' if status.interlock_closed else 'OPEN'}, fault code {status.fault_code}"
    )


class VersaLaseWidget(QWidget):
    """Tab content showing live status of a VersaLase, one line per laser head.

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
        self._stopped = False
        self._head_dots: List[QLabel] = []
        self._head_lines: List[QLabel] = []
        self._build_ui()
        # One line per head from the start: the settings panel sizes a tab from its size hint when the
        # tab is selected, before the first status read would otherwise add the lines.
        heads = sorted(getattr(versalase, "heads", []), key=lambda h: h.wavelength)
        self._ensure_head_rows(len(heads))
        for head, line in zip(heads, self._head_lines):
            line.setText(f"{head.wavelength:>3} nm reading status…")
        self._status_ready.connect(self._on_status)
        self._poll_failed.connect(self._on_poll_failed)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll)

    def _build_ui(self) -> None:
        self._layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"font-size: 16pt; color: {GREY};")
        self._summary_label = QLabel("VersaLase: Waiting…")
        self._paused_label = QLabel("")
        top_row.addWidget(self._dot)
        top_row.addWidget(self._summary_label)
        top_row.addStretch(1)
        top_row.addWidget(self._paused_label)
        self._layout.addLayout(top_row)

        self._banner = QLabel("")
        self._banner.setStyleSheet(f"background-color: {RED}; color: white; padding: 4px;")
        self._banner.setWordWrap(True)
        self._banner.setVisible(False)
        self._layout.addWidget(self._banner)

        self._heads_layout = QVBoxLayout()
        self._layout.addLayout(self._heads_layout)
        self._layout.addStretch(1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stopped:
            return
        self.poll()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def stop(self) -> None:
        """Stop polling for good and wait for a status read in flight, before the lasers are shut down."""
        self._stopped = True
        self._timer.stop()
        if self._poll_thread is not None:
            self._poll_thread.join(STOP_TIMEOUT_S)

    def poll(self) -> None:
        """Start a status read unless stopped, one is running, or an acquisition is in progress."""
        if self._stopped:
            return
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

    def _ensure_head_rows(self, count: int) -> None:
        while len(self._head_lines) < count:
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {GREY};")
            line = QLabel("—")
            line.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))  # "monospace" is not fixed on Windows
            row.addWidget(dot)
            row.addWidget(line)
            row.addStretch(1)
            self._heads_layout.addLayout(row)
            self._head_dots.append(dot)
            self._head_lines.append(line)
        for i, (dot, line) in enumerate(zip(self._head_dots, self._head_lines)):
            dot.setVisible(i < count)
            line.setVisible(i < count)

    def _on_status(self, statuses: List[HeadStatus]) -> None:
        self._banner.setVisible(False)
        self._ensure_head_rows(len(statuses))
        for status, dot, line in zip(statuses, self._head_dots, self._head_lines):
            dot.setStyleSheet(f"color: {_head_color(status)};")
            line.setText(_head_line(status))
            line.setToolTip(_head_tooltip(status))
        color, text = _summary(statuses)
        self._set_summary(color, text)

    def _on_poll_failed(self, message: str) -> None:
        self._banner.setText(f"Lost contact with the VersaLase: {message}")
        self._banner.setVisible(True)
        self._set_summary(RED, "Disconnected")

    def _set_summary(self, color: str, text: str) -> None:
        self._dot.setStyleSheet(f"font-size: 16pt; color: {color};")
        self._summary_label.setText(f"VersaLase: {text}")
