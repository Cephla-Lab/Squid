"""The Temperature tab: the fluidics module's own per-channel plot widgets
(fluidics.qt.sensor_plots), plus Squid's step-labeled run recording — the shared
fluidics.sensor_recorder fed straight from the TEC subscription, so a protocol run's
CSV carries the step each sample belongs to."""

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from fluidics.qt.sensor_plots import TemperatureControlWidget
from fluidics.sensor_recorder import SensorRecorder


class TemperatureTab(QWidget):
    REFRESH_MS = 1000

    def __init__(self, temperature_controller, recorder: SensorRecorder, parent=None):
        super().__init__(parent)
        self._tc = temperature_controller
        self._recorder = recorder

        self.control_widget = TemperatureControlWidget(temperature_controller)

        # Producer thread -> recorder only (no Qt); the closure holds the thread-safe
        # recorder alone, never the widget. Subscribed only while a recording is open
        # (the plot widgets keep their own subscription); detached on destroyed.
        def on_temps(temps, recorder=recorder):
            for i, value in enumerate(temps):
                recorder.record(f"channel_{i + 1}", float(value))

        self._on_temps = on_temps
        tc = temperature_controller
        self.destroyed.connect(lambda: tc.unsubscribe(on_temps))

        self.record_button = QPushButton("Record run to CSV…")
        self.record_button.setCheckable(True)
        self.record_button.toggled.connect(self._toggle_recording)
        record_row = QHBoxLayout()
        record_row.addWidget(QLabel("All channels, labeled with the running protocol step:"))
        record_row.addStretch(1)
        record_row.addWidget(self.record_button)

        layout = QVBoxLayout()
        layout.addWidget(self.control_widget, 1)
        layout.addLayout(record_row)
        self.setLayout(layout)

        # Runs only while a recording is open: its one job is following a recorder
        # that stopped itself on a write failure.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)

    def set_run_active(self, active: bool) -> None:
        """A running protocol owns the TEC: the manual setpoint controls go dead."""
        self.control_widget.set_controls_enabled(not active)

    def _toggle_recording(self, checked: bool) -> None:
        if checked:
            path, _ = QFileDialog.getSaveFileName(self, "Record temperatures", "temperature.csv", "CSV files (*.csv)")
            if not path or not self._recorder.start_recording(path):
                self.record_button.setChecked(False)
                return
            self._tc.subscribe(self._on_temps)
            self._timer.start(self.REFRESH_MS)
            self.record_button.setText("Stop recording")
        else:
            self._tc.unsubscribe(self._on_temps)
            self._timer.stop()
            self._recorder.stop_recording()
            self.record_button.setText("Record run to CSV…")

    def _refresh(self) -> None:
        if self.record_button.isChecked() and not self._recorder.recording:
            # The recorder stopped on its own (a CSV write failed and was logged):
            # the button must not keep promising a recording.
            self.record_button.setChecked(False)
