from squid.ui.widgets.tracking._common import *
from squid.core.events import (
    EventBus,
    SetDisplacementMeasurementSettingsCommand,
    SetWaveformDisplayNCommand,
    DisplacementReadingsChanged,
)


class DisplacementMeasurementWidget(QFrame):
    entry_x_offset: QDoubleSpinBox
    entry_y_offset: QDoubleSpinBox
    entry_x_scaling: QDoubleSpinBox
    entry_y_scaling: QDoubleSpinBox
    entry_N_average: QSpinBox
    entry_N: QSpinBox
    reading_x: QLabel
    reading_y: QLabel
    grid: QGridLayout

    def __init__(
        self,
        event_bus: EventBus,
        main: Optional[Any] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._event_bus = event_bus
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Subscribe to displacement readings events
        self._event_bus.subscribe(DisplacementReadingsChanged, self._on_readings_changed)

    def add_components(self) -> None:
        self.entry_x_offset = QDoubleSpinBox()
        self.entry_x_offset.setMinimum(0)
        self.entry_x_offset.setMaximum(3000)
        self.entry_x_offset.setSingleStep(0.2)
        self.entry_x_offset.setDecimals(3)
        self.entry_x_offset.setValue(0)
        self.entry_x_offset.setKeyboardTracking(False)

        self.entry_y_offset = QDoubleSpinBox()
        self.entry_y_offset.setMinimum(0)
        self.entry_y_offset.setMaximum(3000)
        self.entry_y_offset.setSingleStep(0.2)
        self.entry_y_offset.setDecimals(3)
        self.entry_y_offset.setValue(0)
        self.entry_y_offset.setKeyboardTracking(False)

        self.entry_x_scaling = QDoubleSpinBox()
        self.entry_x_scaling.setMinimum(-100)
        self.entry_x_scaling.setMaximum(100)
        self.entry_x_scaling.setSingleStep(0.1)
        self.entry_x_scaling.setDecimals(3)
        self.entry_x_scaling.setValue(1)
        self.entry_x_scaling.setKeyboardTracking(False)

        self.entry_y_scaling = QDoubleSpinBox()
        self.entry_y_scaling.setMinimum(-100)
        self.entry_y_scaling.setMaximum(100)
        self.entry_y_scaling.setSingleStep(0.1)
        self.entry_y_scaling.setDecimals(3)
        self.entry_y_scaling.setValue(1)
        self.entry_y_scaling.setKeyboardTracking(False)

        self.entry_N_average = QSpinBox()
        self.entry_N_average.setMinimum(1)
        self.entry_N_average.setMaximum(25)
        self.entry_N_average.setSingleStep(1)
        self.entry_N_average.setValue(1)
        self.entry_N_average.setKeyboardTracking(False)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(1)
        self.entry_N.setMaximum(5000)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(1000)
        self.entry_N.setKeyboardTracking(False)

        self.reading_x = QLabel()
        self.reading_x.setNum(0)
        self.reading_x.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        self.reading_y = QLabel()
        self.reading_y.setNum(0)
        self.reading_y.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        # layout
        grid_line0 = QGridLayout()
        grid_line0.addWidget(QLabel("x offset"), 0, 0)
        grid_line0.addWidget(self.entry_x_offset, 0, 1)
        grid_line0.addWidget(QLabel("x scaling"), 0, 2)
        grid_line0.addWidget(self.entry_x_scaling, 0, 3)
        grid_line0.addWidget(QLabel("y offset"), 0, 4)
        grid_line0.addWidget(self.entry_y_offset, 0, 5)
        grid_line0.addWidget(QLabel("y scaling"), 0, 6)
        grid_line0.addWidget(self.entry_y_scaling, 0, 7)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("d from x"), 0, 0)
        grid_line1.addWidget(self.reading_x, 0, 1)
        grid_line1.addWidget(QLabel("d from y"), 0, 2)
        grid_line1.addWidget(self.reading_y, 0, 3)
        grid_line1.addWidget(QLabel("N average"), 0, 4)
        grid_line1.addWidget(self.entry_N_average, 0, 5)
        grid_line1.addWidget(QLabel("N"), 0, 6)
        grid_line1.addWidget(self.entry_N, 0, 7)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.grid.addLayout(grid_line1, 1, 0)
        self.setLayout(self.grid)

        # connections
        self.entry_x_offset.valueChanged.connect(self._on_settings_changed)
        self.entry_y_offset.valueChanged.connect(self._on_settings_changed)
        self.entry_x_scaling.valueChanged.connect(self._on_settings_changed)
        self.entry_y_scaling.valueChanged.connect(self._on_settings_changed)
        self.entry_N_average.valueChanged.connect(self._on_settings_changed)
        self.entry_N.valueChanged.connect(self._on_settings_changed)
        self.entry_N.valueChanged.connect(self._on_n_changed)

    def _on_settings_changed(self, new_value: float) -> None:
        """Publish settings change via event."""
        print("update settings")
        self._event_bus.publish(SetDisplacementMeasurementSettingsCommand(
            x_offset=self.entry_x_offset.value(),
            y_offset=self.entry_y_offset.value(),
            x_scaling=self.entry_x_scaling.value(),
            y_scaling=self.entry_y_scaling.value(),
            n_average=self.entry_N_average.value(),
            n=self.entry_N.value(),
        ))

    def _on_n_changed(self, n: int) -> None:
        """Publish N value change for waveform display."""
        self._event_bus.publish(SetWaveformDisplayNCommand(n=n))

    def _on_readings_changed(self, event: DisplacementReadingsChanged) -> None:
        """Handle displacement readings changed event."""
        self.reading_x.setText("{:.2f}".format(event.readings[0]))
        self.reading_y.setText("{:.2f}".format(event.readings[1]))

    # Keep legacy method for backwards compatibility during transition
    def display_readings(self, readings: List[float]) -> None:
        self.reading_x.setText("{:.2f}".format(readings[0]))
        self.reading_y.setText("{:.2f}".format(readings[1]))
