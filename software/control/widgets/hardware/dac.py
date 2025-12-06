# DAC control widget
from typing import TYPE_CHECKING

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QDoubleSpinBox,
    QSlider,
)

from squid.events import event_bus, DACValueChanged

if TYPE_CHECKING:
    from squid.services import PeripheralService


class DACControWidget(QFrame):
    def __init__(self, peripheral_service: "PeripheralService", *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._service = peripheral_service

        # Subscribe to state updates
        event_bus.subscribe(DACValueChanged, self._on_dac_changed)

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self) -> None:
        self.slider_DAC0 = QSlider(Qt.Orientation.Horizontal)
        self.slider_DAC0.setTickPosition(QSlider.TicksBelow)
        self.slider_DAC0.setMinimum(0)
        self.slider_DAC0.setMaximum(100)
        self.slider_DAC0.setSingleStep(1)
        self.slider_DAC0.setValue(0)

        self.entry_DAC0 = QDoubleSpinBox()
        self.entry_DAC0.setMinimum(0)
        self.entry_DAC0.setMaximum(100)
        self.entry_DAC0.setSingleStep(0.1)
        self.entry_DAC0.setValue(0)
        self.entry_DAC0.setKeyboardTracking(False)

        self.slider_DAC1 = QSlider(Qt.Orientation.Horizontal)
        self.slider_DAC1.setTickPosition(QSlider.TicksBelow)
        self.slider_DAC1.setMinimum(0)
        self.slider_DAC1.setMaximum(100)
        self.slider_DAC1.setValue(0)
        self.slider_DAC1.setSingleStep(1)

        self.entry_DAC1 = QDoubleSpinBox()
        self.entry_DAC1.setMinimum(0)
        self.entry_DAC1.setMaximum(100)
        self.entry_DAC1.setSingleStep(0.1)
        self.entry_DAC1.setValue(0)
        self.entry_DAC1.setKeyboardTracking(False)

        # connections
        self.entry_DAC0.valueChanged.connect(self.set_DAC0)
        self.entry_DAC0.valueChanged.connect(self.slider_DAC0.setValue)
        self.slider_DAC0.valueChanged.connect(self.entry_DAC0.setValue)
        self.entry_DAC1.valueChanged.connect(self.set_DAC1)
        self.entry_DAC1.valueChanged.connect(self.slider_DAC1.setValue)
        self.slider_DAC1.valueChanged.connect(self.entry_DAC1.setValue)

        # layout
        grid_line1 = QHBoxLayout()
        grid_line1.addWidget(QLabel("DAC0"))
        grid_line1.addWidget(self.slider_DAC0)
        grid_line1.addWidget(self.entry_DAC0)
        grid_line1.addWidget(QLabel("DAC1"))
        grid_line1.addWidget(self.slider_DAC1)
        grid_line1.addWidget(self.entry_DAC1)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line1, 1, 0)
        self.setLayout(self.grid)

    def set_DAC0(self, value: float) -> None:
        """Set DAC0 output (0-100%)."""
        self._service.set_dac(channel=0, percentage=value)

    def set_DAC1(self, value: float) -> None:
        """Set DAC1 output (0-100%)."""
        self._service.set_dac(channel=1, percentage=value)

    def _on_dac_changed(self, event: DACValueChanged) -> None:
        """Handle DAC value changed event."""
        # Update UI without triggering signal loops
        if event.channel == 0:
            self.entry_DAC0.blockSignals(True)
            self.slider_DAC0.blockSignals(True)
            self.entry_DAC0.setValue(event.value)
            self.slider_DAC0.setValue(int(event.value))
            self.entry_DAC0.blockSignals(False)
            self.slider_DAC0.blockSignals(False)
        elif event.channel == 1:
            self.entry_DAC1.blockSignals(True)
            self.slider_DAC1.blockSignals(True)
            self.entry_DAC1.setValue(event.value)
            self.slider_DAC1.setValue(int(event.value))
            self.entry_DAC1.blockSignals(False)
            self.slider_DAC1.blockSignals(False)
