"""Bench GUI for the Cephla laser engine v2: drive the engine through the Squid driver, no microscope.

Reusable panels take an engine (LaserEngineV2ServicePanel, LogPane: for Squid's "Laser Engine" tab later);
BenchWindow and main() are the standalone app (tools/laser_engine_v2_bench.py). The bench has no Squid controller,
so no TTL: the per-line "Gate (bench, no TTL)" checkbox holds the line's gate on in firmware instead. Emission still
needs the hardware permits. The 560 nm laser power and idle-off are set in the embedded Laser Engine tab, which
saves them in cache/laser_engine_v2.yaml (the file Squid reads too).
"""

import logging
import re
import sys
import time
import weakref
from dataclasses import dataclass
from typing import Dict, List, Optional

from qtpy.compat import isalive
from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

import squid.logging
from control._def import port_index_to_source_code
from control.laser_engine_v2 import EngineOptions, LaserEngineV2, _production_source_factory, options_from_cache
from control.laser_engine_v2_link import EngineCommandError, EngineLink
from control.laser_engine_v2_sim import build_simulated_engine
from control.laser_engine_v2_status import (
    SOURCE_560_LINE,
    EngineV2Status,
    LineState,
    SourceStatus,
    _source_state,
)
from control.laser_engine_v2_widget import LaserEngineV2Widget

# bench only: Squid takes the wavelengths from its channel configs
DF_WAVELENGTHS = {1: 405, 2: 488, 3: 560, 4: 638, 5: 730}
# the bench's TTL port map (engine line n = port Dn): the tab's wavelength column, and wavelength -> line
BENCH_TTL_MAP = {wavelength: port_index_to_source_code(n - 1) for n, wavelength in DF_WAVELENGTHS.items()}
_UNITS = {"WLD": "A", "CHASSIS": "A", "VOLT": "V"}  # set-point unit per line kind (VOLT = DF L3, the AOM input)
_RAW_GATE = re.compile(r"LINE(\d):GATE\s+([01]|ON|OFF)", re.IGNORECASE)
L3_GATE_TIP = (
    "Bench gate on L3 drives the AOM analog path; the AOM's on/off input is the controller's D3 TTL (none on the "
    "bench) and the shutter opens only while the 560 is in use, so this alone may give no 560 light."
)
_TEENSY_VID = 0x16C0  # PJRC: preselected in the port list


def source_state_text(status: Optional[SourceStatus]) -> str:
    """SOURCE_OFF / STARTING / READY / NEEDS_KEY / FAULT + detail: the same judgement as line 3's state."""
    if status is None:
        return "no status yet"
    state, reason = _source_state(status)
    return f"{state.name}  {reason}".strip()


class _IntensitySpinBox(QDoubleSpinBox):
    """Takes the mouse wheel only when focused: scrolling past the panel must not change a laser's intensity."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _set_quietly(widget, value) -> None:
    """Show a value without emitting its change signal (nothing is sent to the engine)."""
    widget.blockSignals(True)
    try:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        else:
            widget.setValue(value)
    finally:
        widget.blockSignals(False)


@dataclass
class _LineRow:
    key: str
    wavelength: int
    name: QLabel
    state: QLabel
    spin: QDoubleSpinBox
    readback: QLabel
    wake: QPushButton
    sleep: QPushButton
    gate: QCheckBox

    def widgets(self) -> List[QWidget]:
        return [self.name, self.state, self.spin, self.readback, self.wake, self.sleep, self.gate]


class LaserEngineV2ServicePanel(QWidget):
    """Per-line intensity / wake / sleep / bench gate, the engine's 560 source, and a raw command line."""

    RAW_HISTORY = 5

    def __init__(self, engine: LaserEngineV2, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._engine = engine
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._raw_history: List[str] = []
        self._accepted: Dict[str, float] = {}  # per row: the last intensity the engine accepted (or was seeded with)
        self._seeded = False
        self._gate_cmd_t: Dict[int, float] = {}  # per line: when this panel last sent a GATE command
        self.rows: Dict[str, _LineRow] = {}
        if engine.variant != "DF":
            self._log.warning(f"variant {engine.variant!r}: the bench wavelength labels (L3 = 560 nm ...) assume DF")
        layout = QVBoxLayout(self)

        lines_box = QGroupBox("Lines (bench)")
        grid = QGridLayout(lines_box)
        for col, text in enumerate(("Line", "State", "Intensity", "Target / now", "", "", "")):
            grid.addWidget(QLabel(f"<b>{text}</b>"), 0, col)
        for n, wavelength in DF_WAVELENGTHS.items():
            row = self._make_row(n, wavelength)
            self.rows[row.key] = row
            for col, widget in enumerate(row.widgets()):
                grid.addWidget(widget, n, col)
        grid.setColumnStretch(1, 1)
        layout.addWidget(lines_box)

        self.message_label = QLabel("")  # the last refusal / error from this panel (also logged)
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color: #c0392b;")
        layout.addWidget(self.message_label)

        self.source_box: Optional[QGroupBox] = None
        limits = engine.source_limits_mw
        if limits is not None:
            self.source_box = QGroupBox("560 nm source")
            form = QFormLayout(self.source_box)
            self.source_state_label = QLabel("")
            self.source_setpoint_label = QLabel("—")  # the operator's power (set in the Laser Engine tab)
            self.source_measured_label = QLabel("—")
            self.source_limits_label = QLabel(f"{limits[0]:.0f} – {limits[1]:.0f} mW")
            form.addRow("State", self.source_state_label)
            form.addRow("Set-point", self.source_setpoint_label)
            form.addRow("Measured", self.source_measured_label)
            form.addRow("Limits", self.source_limits_label)
            layout.addWidget(self.source_box)

        raw_box = QGroupBox("Raw command")
        raw_layout = QVBoxLayout(raw_box)
        raw_row = QHBoxLayout()
        self.raw_edit = QLineEdit()
        self.raw_edit.setPlaceholderText("e.g. STAT?  VAR?  LINE1:EN 1  (a trailing ? = query)")
        self.raw_send_btn = QPushButton("Send")
        raw_row.addWidget(self.raw_edit, 1)
        raw_row.addWidget(self.raw_send_btn)
        raw_layout.addLayout(raw_row)
        note = QLabel("bench: sent straight to the engine; the hardware permits still apply")
        note.setStyleSheet("color: gray;")
        raw_layout.addWidget(note)
        self.raw_reply = QLabel("")
        self.raw_reply.setWordWrap(True)
        self.raw_reply.setTextInteractionFlags(Qt.TextSelectableByMouse)
        raw_layout.addWidget(self.raw_reply)
        self.raw_history_label = QLabel("")
        self.raw_history_label.setStyleSheet("font-family: monospace; color: gray;")
        self.raw_history_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        raw_layout.addWidget(self.raw_history_label)
        self.raw_send_btn.clicked.connect(lambda _=False: self.send_raw())
        self.raw_edit.returnPressed.connect(self.send_raw)
        layout.addWidget(raw_box)
        layout.addStretch(1)

        engine.status_updated.connect(self._on_status)
        latest = engine.get_latest_status()  # also seeds the spinboxes
        if latest is not None:
            self._on_status(latest)
        else:
            self._update_source_box()

    def _make_row(self, n: int, wavelength: int) -> _LineRow:
        key = f"L{n}"
        spin = _IntensitySpinBox()
        spin.setDecimals(1)
        spin.setSuffix(" %")
        spin.setKeyboardTracking(False)  # send on Enter / arrows / focus out, not on every keystroke
        spin.setRange(0.0, 100.0)
        spin.setValue(0.0)  # before connecting: building the panel sends nothing (seeded from the status below)
        self._accepted[key] = 0.0
        spin.valueChanged.connect(lambda pct, n=n, wl=wavelength: self._set_intensity(n, wl, pct))
        wake = QPushButton("Wake")
        wake.clicked.connect(lambda _=False, k=key: self._run(f"{k} wake", lambda: self._engine.wake_up(k)))
        sleep = QPushButton("Sleep")
        sleep.clicked.connect(lambda _=False, k=key: self._run(f"{k} sleep", lambda: self._engine.put_to_sleep(k)))
        gate = QCheckBox("Gate (bench, no TTL)")
        gate.setToolTip(
            L3_GATE_TIP
            if n == 3
            else f"LINE{n}:GATE 1: holds the line's gate on without a TTL (emission still needs the hardware permits)"
        )
        gate.toggled.connect(lambda on, n=n: self._set_gate(n, on))
        state = QLabel("—")
        state.setWordWrap(True)
        return _LineRow(
            key=key,
            wavelength=wavelength,
            name=QLabel(f"L{n} · {wavelength} nm"),
            state=state,
            spin=spin,
            readback=QLabel("—"),
            wake=wake,
            sleep=sleep,
            gate=gate,
        )

    # ---- actions: errors shown (and logged), never raised ----------------------------------------------------------
    def _show_error(self, text: str) -> None:
        self._log.warning(text)
        self.message_label.setText(text)

    def _run(self, what: str, fn) -> bool:
        try:
            fn()
        except Exception as e:
            self._show_error(f"{what}: {e}")
            return False
        self.message_label.setText("")
        return True

    def _is_source_line(self, n: int) -> bool:
        return self._engine.variant == "DF" and n == SOURCE_560_LINE

    def _set_intensity(self, n: int, wavelength: int, pct: float) -> None:
        key = f"L{n}"
        if not self._run(
            f"{key} intensity {pct:.1f} %", lambda: self._engine.set_wavelength_intensity(wavelength, pct)
        ):
            _set_quietly(self.rows[key].spin, self._accepted[key])  # show what the engine still has
            return
        self._accepted[key] = pct

    def _seed_from(self, status: EngineV2Status) -> None:
        """Show each line's current set-point (% of its ceiling; the 560: the AOM's %) without sending anything."""
        for key, row in self.rows.items():
            info = status.channels.get(key)
            if info is None or info.state == LineState.UNUSED:
                continue
            if self._is_source_line(info.line):
                value = self._engine.aom_percent_for_volts(info.target)  # the AOM amplitude, not the laser power
            else:
                value = 100.0 * info.target / info.max if info.max > 0 else 0.0
            _set_quietly(row.spin, value)
            self._accepted[key] = row.spin.value()
        self._seeded = True

    GATE_SYNC_GUARD_S = 1.5  # a status polled around our own GATE command may predate it: don't let it flip the box

    def _set_gate(self, n: int, on: bool) -> None:
        self._gate_cmd_t[n] = time.time()
        try:
            self._engine.link.command(f"LINE{n}:GATE {int(on)}")
        except Exception as e:
            reason = e.reason if isinstance(e, EngineCommandError) else str(e)
            _set_quietly(self.rows[f"L{n}"].gate, not on)  # the box shows what the engine still has
            if on:
                self._show_error(f"L{n} GATE 1 refused ({reason}) - gate stays off")
            else:
                self._show_error(f"L{n} GATE 0 refused ({reason}) - gate may still be ON: Sleep the line or Disconnect")
            return
        self.message_label.setText("")

    def release_gates(self) -> None:
        """Uncheck every bench gate (each sends LINE<n>:GATE 0). Call before the engine closes."""
        for row in self.rows.values():
            if row.gate.isChecked():
                row.gate.setChecked(False)

    def closeEvent(self, event) -> None:
        self.release_gates()
        super().closeEvent(event)

    def send_raw(self, text: Optional[str] = None) -> str:
        """A trailing "?" = query (the reply as sent); anything else = command (OK / ERR). Returns the reply shown."""
        text = (self.raw_edit.text() if text is None else text).strip()
        if not text:
            return ""
        try:
            if text.endswith("?"):
                reply = self._engine.link.query(text)
            else:
                reply = f"OK {self._engine.link.command(text)}".strip()
                gate = _RAW_GATE.fullmatch(text)
                if gate is not None and f"L{gate.group(1)}" in self.rows:  # keep the bench gate box in step
                    _set_quietly(self.rows[f"L{gate.group(1)}"].gate, gate.group(2).upper() in ("1", "ON"))
                    self._gate_cmd_t[int(gate.group(1))] = time.time()
        except EngineCommandError as e:
            reply = f"ERR {e.reason}"
        except Exception as e:
            reply = f"error: {e}"
        self.raw_reply.setText(reply)
        short = reply if len(reply) <= 80 else reply[:77] + "..."
        self._raw_history = (self._raw_history + [f"> {text}  ->  {short}"])[-self.RAW_HISTORY :]
        self.raw_history_label.setText("\n".join(self._raw_history))
        return reply

    # ---- status ----------------------------------------------------------------------------------------------------
    def _on_status(self, status: EngineV2Status) -> None:
        if not self._seeded:
            self._seed_from(status)
        for key, row in self.rows.items():
            info = status.channels.get(key)
            if info is None or info.state == LineState.UNUSED:  # nothing on this line in this variant
                for widget in row.widgets():
                    widget.setVisible(False)
                continue
            row.state.setText(f"{info.state.name}  {info.reason}".strip())
            if status.timestamp_s > self._gate_cmd_t.get(info.line, 0.0) + self.GATE_SYNC_GUARD_S:
                _set_quietly(row.gate, info.gate)  # the box always ends up showing the engine's gate
            unit = _UNITS.get(info.kind, "")
            row.readback.setText(f"{info.target:.3f} / {info.now:.3f} {unit}".strip())
        self._update_source_box()

    def _update_source_box(self) -> None:
        if self.source_box is None:
            return
        limits = self._engine.source_limits_mw
        if limits is None:  # the engine has closed its source
            self.source_state_label.setText("closed")
            return
        status = self._engine.source_status
        self.source_state_label.setText(source_state_text(status))
        self.source_measured_label.setText("—" if status is None else f"{status.power_mw:.1f} mW")
        setpoint = self._engine.source_power_setpoint_mw
        self.source_setpoint_label.setText("—" if setpoint is None else f"{setpoint:.0f} mW")


class _LogBridge(QObject):
    text = Signal(str)


class QtLogHandler(logging.Handler):
    """Forwards log records (from any thread) as text through a Qt signal: queued to the GUI thread's receivers.
    With an owner widget it goes quiet once the owner is destroyed (alive() is False); LogPane prunes such handlers."""

    def __init__(self, level: int = logging.INFO, owner: Optional[QWidget] = None):
        super().__init__(level)
        self.bridge = _LogBridge()
        self._owner = weakref.ref(owner) if owner is not None else None
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))

    def alive(self) -> bool:
        if self._owner is None:
            return True
        owner = self._owner()
        return owner is not None and isalive(owner)

    def emit(self, record: logging.LogRecord) -> None:
        if not self.alive():
            return
        try:
            self.bridge.text.emit(self.format(record))
        except RuntimeError:
            pass  # the Qt side is already gone (shutdown)
        except Exception:
            self.handleError(record)


class LogPane(QPlainTextEdit):
    """The squid logger hierarchy's records (the engine's poll / source threads included), newest at the bottom."""

    MAX_LINES = 5000

    def __init__(self, parent: Optional[QWidget] = None, level: int = logging.INFO):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(self.MAX_LINES)
        self.setStyleSheet("font-family: monospace;")
        self.handler = QtLogHandler(level, owner=self)
        self.handler.bridge.text.connect(self.appendPlainText)
        root = squid.logging.get_logger()
        # No hook on our own `destroyed` signal: PyQt can free a Python slot before Qt calls it, and the call then
        # crashes (a deferred delete during a later event loop). Handlers of panes destroyed without detach() went
        # quiet on their own; remove them here instead.
        for old in list(root.handlers):
            if isinstance(old, QtLogHandler) and not old.alive():
                root.removeHandler(old)
        root.addHandler(self.handler)

    def detach(self) -> None:
        squid.logging.get_logger().removeHandler(self.handler)


class BenchWindow(QMainWindow):
    """Standalone bench app: connection bar, the Squid Laser Engine tab + the service panel, and the log."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Laser engine v2 - bench")
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.engine: Optional[LaserEngineV2] = None
        self.engine_widget: Optional[LaserEngineV2Widget] = None
        self.service_panel: Optional[LaserEngineV2ServicePanel] = None

        central = QWidget()
        layout = QVBoxLayout(central)
        bar1 = QHBoxLayout()
        bar1.addWidget(QLabel("Teensy"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(360)
        bar1.addWidget(self.port_combo, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(lambda _=False: self.refresh_ports())
        bar1.addWidget(self.refresh_btn)
        self.simulate_cb = QCheckBox("Simulate (no hardware)")
        bar1.addWidget(self.simulate_cb)
        self.bringup_cb = QCheckBox("Bring up on connect")
        self.bringup_cb.setChecked(True)
        bar1.addWidget(self.bringup_cb)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(lambda _=False: self.connect_engine())
        bar1.addWidget(self.connect_btn)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(lambda _=False: self.disconnect_engine())
        bar1.addWidget(self.disconnect_btn)
        layout.addLayout(bar1)

        bar2 = QHBoxLayout()
        bar2.addStretch(1)
        self.link_label = QLabel("")
        self.link_label.setStyleSheet("color: #c0392b; font-weight: bold;")
        bar2.addWidget(self.link_label)
        layout.addLayout(bar2)

        self.panel_splitter = QSplitter(Qt.Horizontal)
        self.log_pane = LogPane()
        vertical = QSplitter(Qt.Vertical)
        vertical.addWidget(self.panel_splitter)
        vertical.addWidget(self.log_pane)
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 1)
        layout.addWidget(vertical, 1)
        self.setCentralWidget(central)
        self.refresh_ports()
        self._update_enabled()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.disconnect_engine)  # GATE 0 + DISARM however the app ends

    def refresh_ports(self) -> None:
        self.port_combo.clear()
        preferred = -1
        for p in sorted(list_ports.comports(), key=lambda p: p.device):
            self.port_combo.addItem(f"{p.device} — {p.serial_number or '-'} — {p.description or ''}", p.device)
            if preferred < 0 and p.vid == _TEENSY_VID:
                preferred = self.port_combo.count() - 1
        if preferred >= 0:
            self.port_combo.setCurrentIndex(preferred)

    def _update_enabled(self) -> None:
        idle = self.engine is None
        for widget in (
            self.port_combo,
            self.refresh_btn,
            self.simulate_cb,
            self.bringup_cb,
            self.connect_btn,
        ):
            widget.setEnabled(idle)
        self.disconnect_btn.setEnabled(not idle)

    def _options(self) -> EngineOptions:
        """The 560 power and idle-off as last set in the Laser Engine tab (cache/laser_engine_v2.yaml), as in Squid."""
        return options_from_cache()

    def _build_engine(self, options: EngineOptions) -> LaserEngineV2:
        if self.simulate_cb.isChecked():
            return build_simulated_engine(options)
        device = self.port_combo.currentData()
        if not device:
            raise RuntimeError("no Teensy port selected (Refresh, or tick Simulate)")
        source_factory = _production_source_factory()
        if source_factory is None:
            self._log.warning("no 560 nm source driver in this build (supplied separately): L3 reads NOT_CONFIGURED")
        return LaserEngineV2(
            link_factory=lambda: EngineLink.open(device=device), source_factory=source_factory, options=options
        )

    def _connect_failed(self, text: str) -> None:
        self._log.error(text)
        QMessageBox.warning(self, "Laser engine", text)

    def _zero_set_points(self, engine: LaserEngineV2) -> None:
        """The engine keeps each line's set-point across a DISARM or a lost host, and the bring-up ramps back to it:
        start every fitted line at 0 (raw drive, no calibration). On DF line 3 that is the AOM at 0 V (dark); the 560
        laser power is the operator's (the tab), not a set-point here. Without a 560 source line 3 is left alone.
        """
        status = engine.poll_once()
        if status is None:
            self._log.error("set-points not zeroed at connect: no engine status")
            return
        for key, info in status.channels.items():
            if info.state == LineState.UNUSED:
                continue
            if engine.variant == "DF" and info.line == SOURCE_560_LINE and engine.source_limits_mw is None:
                continue  # no 560 source: line 3 reads NOT_CONFIGURED and takes no intensity
            try:
                engine.set_line_intensity(info.line, 0.0)
            except Exception as e:
                self._log.error(f"{key} set-point not zeroed at connect: {e}")

    def connect_engine(self) -> None:
        if self.engine is not None:
            return
        try:
            engine = self._build_engine(self._options())
            engine.ttl_map_provider = lambda: dict(BENCH_TTL_MAP)  # no microscope: the bench's DF wavelengths
        except Exception as e:
            self._connect_failed(f"connect failed: {e}")
            return
        try:
            engine.start()
            self._zero_set_points(engine)  # before the bring-up enables anything
            if self.bringup_cb.isChecked():
                engine.on_startup()
            engine.poll_once()  # the panel seeds its spinboxes from the latest status
            self.engine = engine
            self.engine_widget = LaserEngineV2Widget(engine)
            self.service_panel = LaserEngineV2ServicePanel(engine)
            self.panel_splitter.addWidget(self.engine_widget)
            self.panel_splitter.addWidget(self.service_panel)
            engine.connection_lost.connect(self._on_link_lost)
            if engine.is_connection_lost():  # lost before the signal was connected (during the zeroing / bring-up)
                self._on_link_lost("lost during connect")
        except Exception as e:
            self._drop_panels()
            try:
                engine.close()  # disarms and switches the 560 off if it got that far
            except Exception:
                self._log.exception("closing the engine after a failed connect")
            self.engine = None
            self._update_enabled()
            self._connect_failed(f"connect failed: {e}")
            return
        self.link_label.setText("")
        self._update_enabled()
        self._log.info(f"connected: variant {engine.variant or '?'}")

    def _on_link_lost(self, message: str) -> None:
        engine = self.engine
        if engine is None or not engine.is_connection_lost():  # a late signal from an engine already closed
            return
        self.link_label.setText(f"DISCONNECTED - link lost: {message}")
        if self.service_panel is not None:
            self.service_panel.setEnabled(False)
        self._log.error(f"DISCONNECTED - link lost ({message}): Disconnect, check USB / power, then Connect")

    def _drop_panels(self) -> None:
        for widget in (self.engine_widget, self.service_panel):
            if widget is not None:
                widget.hide()
                widget.deleteLater()  # Qt drops it from the splitter
        self.engine_widget = self.service_panel = None

    def disconnect_engine(self) -> None:
        """Idempotent (Disconnect, window close, application quit)."""
        engine = self.engine
        if engine is None:
            return
        self.engine = None
        if self.service_panel is not None:
            if engine.is_connection_lost():  # nothing reaches the engine; each GATE 0 would only time out
                self._log.warning(
                    "link lost: bench gates not released - the engine disarms itself on the host timeout "
                    "and the next connect sets GATE 0 on every line"
                )
            else:
                self.service_panel.release_gates()  # GATE 0 while the link is still open
        try:
            engine.close()  # DISARM, 560 off
        except Exception:
            self._log.exception("closing the laser engine")
        self._drop_panels()
        self._update_enabled()
        self._log.info("disconnected")

    def closeEvent(self, event) -> None:
        self.disconnect_engine()
        self.log_pane.detach()
        super().closeEvent(event)


def main() -> None:
    squid.logging.setup_uncaught_exception_logging()  # a slot's exception is logged instead of aborting the app
    app = QApplication.instance() or QApplication(sys.argv)
    window = BenchWindow()
    window.resize(1500, 900)
    window.show()
    sys.exit(app.exec_())
