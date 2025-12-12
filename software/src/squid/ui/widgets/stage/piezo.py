from typing import Callable, List, Tuple, Type, TYPE_CHECKING
from squid.ui.widgets.stage._common import *
from squid.core.events import PiezoPositionChanged

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


class PiezoWidget(QFrame):
    def __init__(
        self,
        piezo: PiezoStage,
        event_bus: Optional["UIEventBus"] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.piezo: PiezoStage = piezo
        self.piezo_displacement_um: float = 0.00
        self._event_bus = event_bus
        self._subscriptions: List[Tuple[Type, Callable]] = []

        # Subscribe to piezo position events via UIEventBus (thread-safe)
        if self._event_bus is not None:
            self._subscribe(PiezoPositionChanged, self._on_piezo_position_changed)

        # UI components
        self.slider: QSlider
        self.spinBox: QDoubleSpinBox
        self.home_btn: QPushButton
        self.increment_spinBox: QDoubleSpinBox
        self.move_up_btn: QPushButton
        self.move_down_btn: QPushButton

        self.add_components()

    def add_components(self) -> None:
        # Row 1: Slider and Double Spin Box for direct control
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setMinimum(0)
        self.slider.setMaximum(
            int(self.piezo.range_um * 100)
        )  # Multiplied by 100 for 0.01 precision
        self.slider.setValue(int(self.piezo._home_position_um * 100))

        self.spinBox = QDoubleSpinBox(self)
        self.spinBox.setRange(0.0, self.piezo.range_um)
        self.spinBox.setDecimals(2)
        self.spinBox.setSingleStep(1)
        self.spinBox.setSuffix(" μm")
        self.spinBox.setKeyboardTracking(False)
        self.spinBox.setValue(self.piezo._home_position_um)

        # Row 3: Home Button
        self.home_btn = QPushButton(f" Set to {self.piezo._home_position_um} μm ", self)

        hbox1 = QHBoxLayout()
        hbox1.addWidget(self.home_btn)
        hbox1.addWidget(self.slider)
        hbox1.addWidget(self.spinBox)

        # Row 2: Increment Double Spin Box, Move Up and Move Down Buttons
        self.increment_spinBox = QDoubleSpinBox(self)
        self.increment_spinBox.setKeyboardTracking(False)
        self.increment_spinBox.setRange(0.0, 100.0)
        self.increment_spinBox.setDecimals(2)
        self.increment_spinBox.setSingleStep(1)
        self.increment_spinBox.setValue(1.00)
        self.increment_spinBox.setSuffix(" μm")
        self.move_up_btn = QPushButton("Move Up", self)
        self.move_down_btn = QPushButton("Move Down", self)

        hbox2 = QHBoxLayout()
        hbox2.addWidget(self.increment_spinBox)
        hbox2.addWidget(self.move_up_btn)
        hbox2.addWidget(self.move_down_btn)

        # Vertical Layout to include all HBoxes
        vbox = QVBoxLayout()
        vbox.addLayout(hbox1)
        vbox.addLayout(hbox2)

        self.setLayout(vbox)

        # Connect signals and slots
        self.slider.valueChanged.connect(self.update_from_slider)
        self.spinBox.valueChanged.connect(self.update_from_spinBox)
        self.move_up_btn.clicked.connect(lambda: self.adjust_position(True))
        self.move_down_btn.clicked.connect(lambda: self.adjust_position(False))
        self.home_btn.clicked.connect(self.home)

    def update_from_slider(self, value: int) -> None:
        self.piezo_displacement_um = (
            value / 100
        )  # Convert back to float with two decimal places
        self.update_spinBox()
        self.update_piezo_position()

    def update_from_spinBox(self, value: float) -> None:
        self.piezo_displacement_um = value
        self.update_slider()
        self.update_piezo_position()

    def update_spinBox(self) -> None:
        self.spinBox.blockSignals(True)
        self.spinBox.setValue(self.piezo_displacement_um)
        self.spinBox.blockSignals(False)

    def update_slider(self) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(self.piezo_displacement_um * 100))
        self.slider.blockSignals(False)

    def update_piezo_position(self) -> None:
        self.piezo.move_to(self.piezo_displacement_um)

    def adjust_position(self, up: bool) -> None:
        increment = self.increment_spinBox.value()
        if up:
            self.piezo_displacement_um = min(
                self.piezo.range_um, self.spinBox.value() + increment
            )
        else:
            self.piezo_displacement_um = max(0, self.spinBox.value() - increment)
        self.update_spinBox()
        self.update_slider()
        self.update_piezo_position()

    def home(self) -> None:
        self.piezo.home()
        self.piezo_displacement_um = self.piezo._home_position_um
        self.update_spinBox()
        self.update_slider()

    def update_displacement_um_display(
        self, displacement: Optional[float] = None
    ) -> None:
        if displacement is None:
            displacement = self.piezo.position
        self.piezo_displacement_um = round(displacement, 2)
        self.update_spinBox()
        self.update_slider()

    # -------------------------------------------------------------------------
    # EventBus subscription methods
    # -------------------------------------------------------------------------

    def _subscribe(self, event_type: Type, handler: Callable) -> None:
        """Subscribe to an event type with automatic cleanup tracking."""
        if self._event_bus is not None:
            self._event_bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))

    def _cleanup_subscriptions(self) -> None:
        """Unsubscribe all tracked subscriptions."""
        if self._event_bus is not None:
            for event_type, handler in self._subscriptions:
                self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

    def _on_piezo_position_changed(self, event: PiezoPositionChanged) -> None:
        """Handle piezo position changed event - update display."""
        self.update_displacement_um_display(event.position_um)

    def closeEvent(self, event: Any) -> None:
        """Clean up subscriptions when widget is closed."""
        self._cleanup_subscriptions()
        super().closeEvent(event)
