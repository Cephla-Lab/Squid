"""GUI tab for the Cephla laser engine, carrier rev 1 (Squid's "Laser Engine" tab; the bench app embeds it too).

Top: the engine state as a coloured pill, the engine buttons, the startup state and the last few notices. Then one row per
fitted line, and on DF the 560 nm laser box: the operator sets the laser power here (saved in the machine .ini, rulings
2026-10-06); Squid's 560 intensity drives the AOM; the shutter is safety only.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from control.laser_engine_rev1 import save_source_power_mw
from control.laser_engine_rev1_status import SOURCE_560_LINE, EngineRev1Status, LineInfo, LineState, SourceStatus

GREEN, AMBER, RED, GREY = "#1e8449", "#b9770e", "#c0392b", "#707b7c"

_STATE_COLOURS = {
    LineState.READY: GREEN,
    LineState.STARTING: AMBER,
    LineState.WARMING_UP: AMBER,
    LineState.PAUSED: AMBER,
    LineState.NEEDS_KEY: AMBER,
    LineState.OFF: GREY,
    LineState.NOT_ARMED: GREY,
    LineState.SOURCE_OFF: GREY,
    LineState.NOT_CONFIGURED: GREY,
    LineState.BLOCKED: RED,
    LineState.FAULT: RED,
}
_UNITS = {"WLD": "A", "CHASSIS": "A", "VOLT": "V"}  # set-point unit per line kind (VOLT = DF line 3, the AOM input)
SHUTTER_NOTE = "Shutter: safety only — open while the 560 is in use"


def engine_state(status: Optional[EngineRev1Status], lost: bool) -> Tuple[str, str]:
    """(pill text, colour) for the engine as a whole."""
    if lost:
        return "Connection lost", RED
    if status is None:
        return "Waiting…", GREY
    if status.any_error():
        return "Fault", RED
    if not status.interlock_ok:
        return "Paused (cover open)", AMBER
    if not status.armed:
        return "Disarmed", GREY
    return "Armed", GREEN


def state_text(state: LineState) -> str:
    return state.name.replace("_", " ")


def source_state(status: Optional[SourceStatus]) -> Tuple[str, str, str]:
    """(state, detail, colour) of the 560 nm laser: OFF / STARTING / READY / NEEDS KEY / FAULT / LINK LOST."""
    if status is None:
        return "—", "no status yet", GREY
    if not status.link_ok:
        return "LINK LOST", status.detail or "not responding", RED
    if status.needs_key:
        return "NEEDS KEY", "turn the 560 key OFF then ON", AMBER
    if status.fault:
        return "FAULT", status.detail, RED
    if status.ready:
        return ("READY", "", GREEN) if status.settled else ("STARTING", "settling to the set power", AMBER)
    if status.off:
        return "OFF", "", GREY
    return "STARTING", status.detail, AMBER


def _coloured(label: QLabel, text: str, colour: str) -> None:
    label.setText(text)
    label.setStyleSheet(f"color: {colour}; font-weight: bold;")


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Never takes the mouse wheel: scrolling the tab must not change the laser power."""

    def wheelEvent(self, event) -> None:
        event.ignore()


@dataclass
class _LineRow:
    line: QLabel
    wavelength: QLabel
    state: QLabel
    setpoint: QLabel
    max: QLabel
    reason: QLabel

    def widgets(self) -> List[QLabel]:
        return [self.line, self.wavelength, self.state, self.setpoint, self.max, self.reason]


class LaserEngineRev1Widget(QWidget):
    NOTICES_SHOWN = 4
    COLUMNS = ("Line", "Wavelength", "State", "Set-point", "Max", "Reason")

    def __init__(
        self,
        engine,
        parent: Optional[QWidget] = None,
        save_power: Callable[[float], bool] = save_source_power_mw,
    ):
        super().__init__(parent)
        self._engine = engine
        self._save_power = save_power  # remembers the 560 power across sessions (the machine .ini)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.state_pill = QLabel()
        self._show_engine_state(None, False)
        top.addWidget(self.state_pill)
        top.addStretch(1)
        self.arm_btn = QPushButton("Arm")
        self.disarm_btn = QPushButton("Disarm")
        self.reset_btn = QPushButton("Reset faults")
        self.wake_btn = QPushButton("Wake all")
        self.sleep_btn = QPushButton("Sleep all")
        for btn, fn in (
            (self.arm_btn, engine.arm),
            (self.disarm_btn, engine.disarm),
            (self.reset_btn, engine.fault_reset),
            (self.wake_btn, engine.wake_up_all),
            (self.sleep_btn, engine.sleep_all),
        ):
            btn.clicked.connect(lambda _=False, f=fn: self._run(f))
            top.addWidget(btn)
        layout.addLayout(top)

        self.startup_label = QLabel("")
        self.event_label = QLabel("")
        for label in (self.startup_label, self.event_label):
            label.setStyleSheet(f"color: {GREY};")
            layout.addWidget(label)
        self._set_line(self.startup_label, self._startup_text(engine.bringup_state))  # the bring-up may be running
        self._set_line(self.event_label, "")
        self.notice_label = QLabel("")  # the last few operator notices: connect-time reset, bring-up, 560 power
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)

        lines_box = QGroupBox("Lines")
        grid = QGridLayout(lines_box)
        for col, text in enumerate(self.COLUMNS):
            grid.addWidget(QLabel(f"<b>{text}</b>"), 0, col)
        self.rows: Dict[str, _LineRow] = {}
        for n in range(1, 6):
            row = _LineRow(*(QLabel("—") for _ in self.COLUMNS))
            row.line.setText(f"L{n}")
            row.reason.setWordWrap(True)
            row.reason.setStyleSheet(f"color: {GREY};")
            self.rows[f"L{n}"] = row
            for col, widget in enumerate(row.widgets()):
                grid.addWidget(widget, n, col)
        grid.setColumnStretch(len(self.COLUMNS) - 1, 1)
        layout.addWidget(lines_box)

        self.source_box: Optional[QGroupBox] = None
        limits = engine.source_limits_mw
        if engine.variant == "DF" and limits is not None:
            layout.addWidget(self._build_source_box(limits))
        layout.addStretch(1)

        self._notices = list(engine.notices[-self.NOTICES_SHOWN :])  # e.g. the connect-time reset, before this tab
        self._show_notices()
        engine.status_updated.connect(self._on_status)
        engine.connection_lost.connect(self._on_lost)
        engine.notice_added.connect(self._on_notice)
        latest = engine.get_latest_status()
        if latest is not None:
            self._on_status(latest)
        else:
            self._update_source_box()

    def _build_source_box(self, limits: Tuple[float, float]) -> QGroupBox:
        engine = self._engine
        self.source_box = QGroupBox("560 nm laser")
        grid = QGridLayout(self.source_box)
        self.source_state_label = QLabel("—")
        self.source_detail_label = QLabel("")
        self.source_detail_label.setWordWrap(True)
        self.source_power_label = QLabel("—")  # measured vs set
        self.source_limits_label = QLabel(f"{limits[0]:.0f} – {limits[1]:.0f} mW")
        self.power_spin = _NoWheelDoubleSpinBox()
        self.power_spin.setDecimals(0)
        self.power_spin.setSingleStep(10.0)
        self.power_spin.setSuffix(" mW")
        self.power_spin.setRange(limits[0], limits[1])
        self.power_spin.setKeyboardTracking(False)
        self.power_spin.setFocusPolicy(Qt.StrongFocus)
        self.power_spin.setValue(engine.source_power_setpoint_mw or limits[0])
        self.power_set_btn = QPushButton("Set")
        self.power_set_btn.clicked.connect(lambda _=False: self.set_source_power())
        self.idle_off_spin = QSpinBox()
        self.idle_off_spin.setRange(0, 24 * 60)
        self.idle_off_spin.setSuffix(" min")
        self.idle_off_spin.setSpecialValueText("24 h")  # shown at 0: there is no "never off"
        self.idle_off_spin.setValue(round(engine.source_idle_off_s / 60) % (24 * 60))
        self.idle_off_spin.valueChanged.connect(lambda v: self._run(lambda: engine.set_source_idle_off_min(v)))
        self.shutter_note = QLabel(SHUTTER_NOTE)
        self.shutter_note.setStyleSheet(f"color: {GREY};")

        state_row = QHBoxLayout()
        state_row.addWidget(self.source_state_label)
        state_row.addWidget(self.source_detail_label, 1)
        power_row = QHBoxLayout()
        power_row.addWidget(self.power_spin)
        power_row.addWidget(self.power_set_btn)
        power_row.addStretch(1)
        idle_row = QHBoxLayout()
        idle_row.addWidget(self.idle_off_spin)
        idle_row.addStretch(1)
        for r, (name, item) in enumerate(
            (
                ("State", state_row),
                ("Power", self.source_power_label),
                ("Limits", self.source_limits_label),
                ("Laser power", power_row),
                ("Idle-off", idle_row),
            )
        ):
            grid.addWidget(QLabel(name), r, 0)
            if isinstance(item, QHBoxLayout):
                grid.addLayout(item, r, 1)
            else:
                grid.addWidget(item, r, 1)
        grid.addWidget(self.shutter_note, 5, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        return self.source_box

    @staticmethod
    def _startup_text(state: str) -> str:
        return f"Startup: {state}" if state else ""

    @staticmethod
    def _set_line(label: QLabel, text: str) -> None:
        """An info line shown only when it has something to say (no empty gaps in the tab)."""
        label.setText(text)
        label.setVisible(bool(text))

    def _run(self, fn) -> bool:
        try:
            fn()
        except Exception as e:  # the engine's refusal reason is the useful message
            QMessageBox.warning(self, "Laser engine", str(e))
            return False
        return True

    # ---- 560 nm laser power ----------------------------------------------------------------------------------------------
    def set_source_power(self) -> None:
        """Set: the power goes to the engine (clamped to the laser's limits), then into the machine .ini."""
        applied: List[float] = []
        if not self._run(lambda: applied.append(self._engine.set_source_power_mw(self.power_spin.value()))):
            return
        self.power_spin.setValue(applied[0])
        if not self._save_power(applied[0]):
            self._on_notice("560 nm laser power not saved to the machine .ini (see the log): this session only")
        self._update_source_box()

    def _update_source_box(self) -> None:
        if self.source_box is None:
            return
        status = self._engine.source_status
        setpoint = self._engine.source_power_setpoint_mw
        if setpoint is None:  # the engine has closed its source
            _coloured(self.source_state_label, "CLOSED", GREY)
            self.source_detail_label.setText("")
            self.source_power_label.setText("—")
            return
        state, detail, colour = source_state(status)
        _coloured(self.source_state_label, state, colour)
        self.source_detail_label.setText(detail)
        measured = f"{status.power_mw:.0f} mW" if status is not None and status.ready else "—"
        self.source_power_label.setText(f"measured {measured} · set {setpoint:.0f} mW")

    # ---- status ------------------------------------------------------------------------------------------------------
    def _on_notice(self, text: str) -> None:
        self._notices = (self._notices + [text])[-self.NOTICES_SHOWN :]
        self._show_notices()

    def _show_notices(self) -> None:
        self.notice_label.setText("\n".join(self._notices))

    def _show_engine_state(self, status: Optional[EngineRev1Status], lost: bool) -> None:
        text, colour = engine_state(status, lost)
        self.state_pill.setText(text)
        self.state_pill.setStyleSheet(
            f"background-color: {colour}; color: white; font-weight: bold; border-radius: 9px; padding: 2px 12px;"
        )

    def _wavelength_text(self, info: LineInfo) -> str:
        wavelengths = self._engine.wavelengths_for_line(info.line)
        text = " / ".join(str(w) for w in wavelengths) + " nm" if wavelengths else "—"
        if self._engine.variant == "DF" and info.line == SOURCE_560_LINE:
            text += " (AOM)"
        return text

    def _setpoint_texts(self, info: LineInfo) -> Tuple[str, str]:
        unit = _UNITS.get(info.kind, "")
        digits = 2 if unit == "V" else 3
        setpoint = f"{info.target:.{digits}f} {unit}".strip()
        if self._engine.variant == "DF" and info.line == SOURCE_560_LINE:
            setpoint += f" ({self._engine.aom_percent_for_volts(info.target):.0f} %)"
        return setpoint, f"{info.max:.{digits}f} {unit}".strip()

    def _on_status(self, status: EngineRev1Status) -> None:
        self._show_engine_state(status, self._engine.is_connection_lost())
        self._set_line(self.startup_label, self._startup_text(self._engine.bringup_state))
        self._set_line(self.event_label, f"Last engine event: {status.last_event}" if status.last_event else "")
        for key, row in self.rows.items():
            info = status.channels.get(key)
            visible = info is not None and info.state != LineState.UNUSED  # UNUSED: nothing on this line
            for widget in row.widgets():
                widget.setVisible(visible)
            if not visible:
                continue
            row.wavelength.setText(self._wavelength_text(info))
            _coloured(row.state, state_text(info.state), _STATE_COLOURS.get(info.state, GREY))
            setpoint, maximum = self._setpoint_texts(info)
            row.setpoint.setText(setpoint)
            row.max.setText(maximum)
            row.reason.setText(info.reason)
        self._update_source_box()

    def _on_lost(self, message: str) -> None:
        self._show_engine_state(None, True)
        self.state_pill.setToolTip(message)
        self._on_notice(f"Connection lost: {message}")
