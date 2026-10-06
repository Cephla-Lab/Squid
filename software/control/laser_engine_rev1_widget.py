"""GUI tab for the Cephla laser engine, carrier rev 1 (Squid's "Laser Engine" tab; the bench app embeds it too).

Top: the engine state as a coloured pill, the engine buttons, the startup state and the last notices. Then one row per
fitted line, and on DF the 560 nm laser box: the operator sets the laser power here (saved in the machine .ini, rulings
2026-10-06); Squid's 560 intensity drives the AOM; the shutter is safety only.
Compact, and inside a scroll area: Squid's side panel can be shorter than the tab (it caps the panel at the tab's size
hint, and the column may have no room left), and a short panel must scroll, never squash the rows.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from qtpy.QtCore import QSize, Qt
from qtpy.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import squid.logging
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


def _not_saved(mw: float) -> bool:
    squid.logging.get_logger(__name__).info(f"simulation: 560 nm laser power {mw:.0f} mW not saved to the machine .ini")
    return False


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
    NOTICES_SHOWN = 2  # the latest; NOTICES_KEPT of them in the tooltip
    NOTICES_KEPT = 10
    PANE_ALLOWANCE_PX = 12  # Squid caps the panel at this hint + the tab bar, without the tab pane's frame
    COLUMNS = ("Line", "Wavelength", "State", "Set-point", "Max", "Reason")

    def __init__(
        self,
        engine,
        parent: Optional[QWidget] = None,
        save_power: Optional[Callable[[float], bool]] = None,
    ):
        super().__init__(parent)
        self._engine = engine
        # a simulated session keeps its power to itself (FakeSource's limits are not the machine's)
        if save_power is None:
            save_power = _not_saved if getattr(engine, "simulated", False) is True else save_source_power_mw
        self._save_power = save_power  # remembers the 560 power across sessions (the machine .ini)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._content = QWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self._content)
        outer.addWidget(self.scroll)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

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

        lines_box = QFrame()  # the header row names it; a group-box title would cost a row
        lines_box.setFrameShape(QFrame.StyledPanel)
        grid = QGridLayout(lines_box)
        grid.setContentsMargins(8, 4, 8, 4)
        grid.setVerticalSpacing(1)
        grid.setHorizontalSpacing(14)
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

        self._notices = list(engine.notices[-self.NOTICES_KEPT :])  # e.g. the connect-time reset, before this tab
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
        self.source_limits_label.setStyleSheet(f"color: {GREY};")

        rows = QVBoxLayout(self.source_box)
        rows.setContentsMargins(8, 4, 8, 4)
        rows.setSpacing(4)
        state_row = QHBoxLayout()  # READY  <detail>  measured 200 mW · set 200 mW
        state_row.addWidget(self.source_state_label)
        state_row.addWidget(self.source_detail_label, 1)
        state_row.addWidget(self.source_power_label)
        set_row = QHBoxLayout()  # Laser power [200 mW] [Set] 200 – 1000 mW    Idle-off [60 min]
        set_row.addWidget(QLabel("Laser power"))
        set_row.addWidget(self.power_spin)
        set_row.addWidget(self.power_set_btn)
        set_row.addWidget(self.source_limits_label)
        set_row.addSpacing(16)
        set_row.addWidget(QLabel("Idle-off"))
        set_row.addWidget(self.idle_off_spin)
        set_row.addStretch(1)
        rows.addLayout(state_row)
        rows.addLayout(set_row)
        rows.addWidget(self.shutter_note)
        return self.source_box

    @staticmethod
    def _startup_text(state: str) -> str:
        """Shown while the bring-up runs or when it did not finish; "done" says nothing the pill and the lines do not."""
        return f"Startup: {state}" if state and state != "done" else ""

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
        self.power_spin.interpretText()  # a typed value not yet committed (Enter / focus-out; a Mac button takes no focus)
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
        self._notices = (self._notices + [text])[-self.NOTICES_KEPT :]
        self._show_notices()

    def _show_notices(self) -> None:
        self.notice_label.setText("\n".join(self._notices[-self.NOTICES_SHOWN :]))
        self.notice_label.setToolTip("\n".join(self._notices))

    def sizeHint(self) -> QSize:
        """What the content needs at the tab's width (wrapped notices and reasons add lines; a QScrollArea would cap its own
        hint at 24 text lines) + the allowance for Squid's tab pane. Squid asks on every tab switch, the tab shown."""
        hint = self._content.sizeHint()
        width = self.width() if self.isVisible() else hint.width()
        height = max(hint.height(), self._content.heightForWidth(width))
        return QSize(hint.width(), height + self.PANE_ALLOWANCE_PX)

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
