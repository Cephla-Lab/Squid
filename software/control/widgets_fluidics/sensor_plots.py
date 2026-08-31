"""The Temperature tab: per-channel readouts/controls and a pyqtgraph plot fed by the Qt-free
SensorRecorder (producer threads write, the GUI repaints on a timer — never per-sample signals).
CSV is written only while Record is pressed."""

import time
from typing import List, Optional

import pyqtgraph as pg
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import squid.logging
from control.core.fluidics_protocol.sensor_recorder import SensorRecorder

_CHANNEL_COLORS = ("#1f77b4", "#d62728")
_WINDOWS = {"10 min": 600.0, "1 h": 3600.0, "all": None}


class TemperatureTab(QWidget):
    REFRESH_MS = 500

    def __init__(self, temperature_controller, recorder: SensorRecorder, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._tc = temperature_controller
        self._recorder = recorder
        self._run_active = False

        grid = QGridLayout()
        self.actual_labels: List[QLabel] = []
        self.target_labels: List[QLabel] = []
        self.target_spinboxes: List[QDoubleSpinBox] = []
        self.set_buttons: List[QPushButton] = []
        self.output_buttons: List[QPushButton] = []
        for i in range(self._tc.channels):
            grid.addWidget(QLabel(f"Channel {i + 1}:"), i, 0)
            actual = QLabel("— °C")
            target = QLabel("target — °C")
            spin = QDoubleSpinBox()
            spin.setRange(-20.0, 100.0)
            spin.setDecimals(1)
            spin.setValue(float(self._tc.target_temperatures[i]))
            set_btn = QPushButton("Set")
            out_btn = QPushButton("Output ON" if self._tc.output_enabled[i] else "Output OFF")
            set_btn.clicked.connect(lambda _=False, ch=i: self._set_target(ch))
            out_btn.clicked.connect(lambda _=False, ch=i: self._toggle_output(ch))
            for col, w in enumerate((actual, target, spin, set_btn, out_btn), start=1):
                grid.addWidget(w, i, col)
            self.actual_labels.append(actual)
            self.target_labels.append(target)
            self.target_spinboxes.append(spin)
            self.set_buttons.append(set_btn)
            self.output_buttons.append(out_btn)

        self.window_combo = QComboBox()
        self.window_combo.addItems(list(_WINDOWS))
        self.record_button = QPushButton("Record to CSV…")
        self.record_button.setCheckable(True)
        self.record_button.toggled.connect(self._toggle_recording)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Window:"))
        controls.addWidget(self.window_combo)
        controls.addStretch(1)
        controls.addWidget(self.record_button)

        self.plot_widget = pg.GraphicsLayoutWidget()
        self.plot = self.plot_widget.addPlot()
        self.plot.setLabel("left", "°C")
        self.plot.setLabel("bottom", "minutes ago")
        self.plot.addLegend()

        layout = QVBoxLayout()
        layout.addLayout(grid)
        layout.addLayout(controls)
        layout.addWidget(self.plot_widget)
        self.setLayout(layout)

        # Producer thread -> recorder only (no Qt); the GUI repaints on its own clock.
        def on_temps(temps):
            for i, value in enumerate(temps):
                self._recorder.record(f"channel_{i + 1}", float(value))

        self._tc.subscribe(on_temps)
        self._tc.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(self.REFRESH_MS)

    def set_run_active(self, active: bool) -> None:
        """A running protocol owns the TEC: the manual Set/Output controls go dead."""
        self._run_active = active
        for button in self.set_buttons + self.output_buttons:
            button.setEnabled(not active)
        for spin in self.target_spinboxes:
            spin.setEnabled(not active)

    def _set_target(self, channel_index: int) -> None:
        try:
            self._tc.set_target_temperature(channel_index + 1, float(self.target_spinboxes[channel_index].value()))
        except Exception as e:
            self._log.error(f"Failed to set the TEC target: {e}")

    def _toggle_output(self, channel_index: int) -> None:
        try:
            self._tc.set_output_enabled(channel_index + 1, not self._tc.output_enabled[channel_index])
        except Exception as e:
            self._log.error(f"Failed to switch the TEC output: {e}")

    def _toggle_recording(self, checked: bool) -> None:
        if checked:
            path, _ = QFileDialog.getSaveFileName(self, "Record temperatures", "temperature.csv", "CSV files (*.csv)")
            if not path or not self._recorder.start_recording(path):
                self.record_button.setChecked(False)
                return
            self.record_button.setText("Stop recording")
        else:
            self._recorder.stop_recording()
            self.record_button.setText("Record to CSV…")

    def _refresh(self) -> None:
        try:
            now = time.time()
            window = _WINDOWS[self.window_combo.currentText()]
            self.plot.clear()
            for i in range(self._tc.channels):
                ts, vs = self._recorder.channel(f"channel_{i + 1}").window(window)
                if ts:
                    x = [(t - now) / 60.0 for t in ts]
                    self.plot.plot(x, vs, pen=pg.mkPen(color=_CHANNEL_COLORS[i % 2], width=2), name=f"Ch {i + 1}")
                self.actual_labels[i].setText(f"{self._tc.actual_temperatures[i]:.1f} °C")
                self.target_labels[i].setText(f"target {self._tc.target_temperatures[i]:.1f} °C")
                self.output_buttons[i].setText("Output ON" if self._tc.output_enabled[i] else "Output OFF")
        except Exception as e:  # Qt swallows timer-slot exceptions: log explicitly
            self._log.error(f"Temperature tab refresh failed: {e}")
