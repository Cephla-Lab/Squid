# Camera trigger control widget
from typing import Optional, TYPE_CHECKING

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QGridLayout,
    QLabel,
    QDoubleSpinBox,
    QComboBox,
    QPushButton,
)

from _def import TriggerMode
from squid.ui.widgets.base import EventBusFrame
from squid.core.events import (
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    SetCameraTriggerFrequencyCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
)

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


class TriggerControlWidget(EventBusFrame):
    """Camera trigger control widget using UIEventBus.

    Publishes trigger command events.
    Does not require direct service access.
    """

    # for synchronized trigger
    signal_toggle_live: Signal = Signal(bool)
    signal_trigger_mode: Signal = Signal(str)
    signal_trigger_fps: Signal = Signal(float)

    def __init__(self, event_bus: "UIEventBus") -> None:
        super().__init__(event_bus)
        self.fps_trigger: float = 10
        self.fps_display: float = 10
        self.triggerMode: Optional[str] = TriggerMode.SOFTWARE
        self.add_components()
        self.setFrameStyle(self.Panel | self.Raised)
        self._subscribe(TriggerModeChanged, self._on_trigger_mode_changed)
        self._subscribe(TriggerFPSChanged, self._on_trigger_fps_changed)

    def add_components(self) -> None:
        # line 0: trigger mode
        self.triggerMode = None
        self.dropdown_triggerManu = QComboBox()
        self.dropdown_triggerManu.addItems([TriggerMode.SOFTWARE, TriggerMode.HARDWARE])

        # line 1: fps
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setKeyboardTracking(False)
        self.entry_triggerFPS.setMinimum(0.02)
        self.entry_triggerFPS.setMaximum(1000)
        self.entry_triggerFPS.setSingleStep(1)
        self.entry_triggerFPS.setValue(self.fps_trigger)

        self.btn_live = QPushButton("Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setChecked(False)
        self.btn_live.setDefault(False)

        # connections
        self.dropdown_triggerManu.currentIndexChanged.connect(self.update_trigger_mode)
        self.btn_live.clicked.connect(self.toggle_live)
        self.entry_triggerFPS.valueChanged.connect(self.update_trigger_fps)

        # initialization
        self._publish(SetTriggerFPSCommand(fps=float(self.entry_triggerFPS.value())))

        # layout
        grid_line0 = QGridLayout()
        grid_line0.addWidget(QLabel("Trigger Mode"), 0, 0)
        grid_line0.addWidget(self.dropdown_triggerManu, 0, 1)
        grid_line0.addWidget(QLabel("Trigger FPS"), 0, 2)
        grid_line0.addWidget(self.entry_triggerFPS, 0, 3)
        grid_line0.addWidget(self.btn_live, 1, 0, 1, 4)
        self.setLayout(grid_line0)

    def toggle_live(self, pressed: bool) -> None:
        self.signal_toggle_live.emit(pressed)
        # Legacy start/stop trigger commands removed; rely on trigger mode/FPS events.

    def update_trigger_mode(self) -> None:
        self.signal_trigger_mode.emit(self.dropdown_triggerManu.currentText())
        self._publish(SetTriggerModeCommand(mode=self.dropdown_triggerManu.currentText()))

    def update_trigger_fps(self, fps: float) -> None:
        self.fps_trigger = fps
        self.signal_trigger_fps.emit(fps)
        self._publish(SetCameraTriggerFrequencyCommand(fps=self.fps_trigger))
        self._publish(SetTriggerFPSCommand(fps=self.fps_trigger))

    def _on_trigger_mode_changed(self, event: TriggerModeChanged) -> None:
        """Sync UI from trigger mode state changes."""
        if getattr(event, "camera", "main") != "main":
            return
        self.dropdown_triggerManu.blockSignals(True)
        self.dropdown_triggerManu.setCurrentText(event.mode)
        self.dropdown_triggerManu.blockSignals(False)

    def _on_trigger_fps_changed(self, event: TriggerFPSChanged) -> None:
        """Sync UI from trigger FPS state changes."""
        if getattr(event, "camera", "main") != "main":
            return
        self.entry_triggerFPS.blockSignals(True)
        self.entry_triggerFPS.setValue(event.fps)
        self.entry_triggerFPS.blockSignals(False)
