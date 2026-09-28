"""GUI tab for the Cephla laser engine, carrier rev 1."""

from typing import Dict, Optional

from qtpy.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget

from control.laser_engine_rev1_status import EngineRev1Status, LineState


def summary_text(status: Optional[EngineRev1Status], lost: bool) -> str:
    if lost:
        return "Engine: Disconnected"
    if status is None:
        return "Engine: Waiting…"
    if status.any_error():
        return "Engine: Error"
    if not status.interlock_ok:
        return "Engine: Paused (cover open)"
    if not status.armed:
        return "Engine: Not armed"
    return "Engine: Armed"


class LaserEngineRev1Widget(QWidget):
    NOTICES_SHOWN = 4

    def __init__(self, engine, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._engine = engine
        self.line_labels: Dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.summary_label = QLabel(summary_text(None, False))
        top.addWidget(self.summary_label)
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
        self.banner = QLabel("")
        self.banner.setStyleSheet("background-color: #c0392b; color: white; padding: 4px;")
        self.banner.setVisible(False)
        layout.addWidget(self.banner)
        self.startup_label = QLabel("")
        layout.addWidget(self.startup_label)
        self.event_label = QLabel("")
        layout.addWidget(self.event_label)
        self.notice_label = QLabel("")  # the last few operator notices: connect-time reset, bring-up, 560 clamp
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("560 idle-off"))
        self.idle_off_spin = QSpinBox()
        self.idle_off_spin.setRange(0, 24 * 60)
        self.idle_off_spin.setSuffix(" min")
        self.idle_off_spin.setSpecialValueText("24 h")  # shown at 0: there is no "never off"
        self.idle_off_spin.setValue(round(engine.source_idle_off_s / 60) % (24 * 60))
        self.idle_off_spin.valueChanged.connect(lambda v: self._run(lambda: engine.set_source_idle_off_min(v)))
        source_row.addWidget(self.idle_off_spin)
        source_row.addWidget(QLabel("Shutter with AOM"))
        self.shutter_combo = QComboBox()
        self.shutter_combo.addItem("gates each exposure", "gate")
        self.shutter_combo.addItem("held open, AOM gates", "open")
        self.shutter_combo.setCurrentIndex(self.shutter_combo.findData(engine.shutter_with_aom))
        self.shutter_combo.setEnabled(engine.options.aom_in_path)  # without the AOM the shutter always gates
        self.shutter_combo.currentIndexChanged.connect(
            lambda i: self._run(lambda: engine.set_shutter_with_aom(self.shutter_combo.itemData(i)))
        )
        source_row.addWidget(self.shutter_combo)
        source_row.addStretch(1)
        if engine.variant == "DF":
            layout.addLayout(source_row)
        for n in range(1, 6):
            label = QLabel(f"L{n}  —")
            label.setStyleSheet("font-family: monospace;")
            self.line_labels[f"L{n}"] = label
            layout.addWidget(label)
        layout.addStretch(1)
        self._notices = list(
            engine.notices[-self.NOTICES_SHOWN :]
        )  # e.g. the connect-time reset, before this tab existed
        self._show_notices()
        engine.status_updated.connect(self._on_status)
        engine.connection_lost.connect(self._on_lost)
        engine.notice_added.connect(self._on_notice)

    def _run(self, fn) -> None:
        try:
            fn()
        except Exception as e:  # the engine's refusal reason is the useful message
            QMessageBox.warning(self, "Laser engine", str(e))

    def _on_notice(self, text: str) -> None:
        self._notices = (self._notices + [text])[-self.NOTICES_SHOWN :]
        self._show_notices()

    def _show_notices(self) -> None:
        self.notice_label.setText("\n".join(self._notices))

    def _on_status(self, status: EngineRev1Status) -> None:
        self.summary_label.setText(summary_text(status, self._engine.is_connection_lost()))
        state = self._engine.bringup_state
        self.startup_label.setText(f"startup: {state}" if state else "")
        self.event_label.setText(f"last engine event: {status.last_event}" if status.last_event else "")
        for key, info in status.channels.items():
            if info.state == LineState.UNUSED:
                text = f"{key}  (not fitted)"
            else:
                text = f"{key}  {info.label:<20} {info.state.name:<14} {info.target:7.3f}/{info.max:.3f}  {info.reason}"
            self.line_labels[key].setText(text)

    def _on_lost(self, message: str) -> None:
        self.banner.setText(f"Laser engine disconnected: {message}")
        self.banner.setVisible(True)
        self.summary_label.setText(summary_text(None, True))
