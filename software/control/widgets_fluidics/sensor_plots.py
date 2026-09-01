"""The Temperature tab: the fluidics module's own per-channel plot widgets
(fluidics.qt.sensor_plots), plus Squid's step-labeled run recording — the shared
fluidics.sensor_recorder fed straight from the TEC subscription, so a protocol run's
CSV carries the step each sample belongs to."""

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import squid.logging
from fluidics.sensor_recorder import SensorRecorder


class TemperatureTab(QWidget):
    REFRESH_MS = 1000

    def __init__(self, temperature_controller, recorder: SensorRecorder, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._tc = temperature_controller
        self._recorder = recorder

        from fluidics.qt.sensor_plots import TemperatureControlWidget

        self.control_widget = TemperatureControlWidget(temperature_controller)

        # Producer thread -> recorder only (no Qt); the closure holds the thread-safe
        # recorder alone, never the widget. The plot widgets subscribe separately.
        def on_temps(temps, recorder=recorder):
            for i, value in enumerate(temps):
                recorder.record(f"channel_{i + 1}", float(value))

        self._tc.subscribe(on_temps)

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

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(self.REFRESH_MS)

    def set_run_active(self, active: bool) -> None:
        """A running protocol owns the TEC: the manual Set/Save/Output controls go dead."""
        for channel_widget in self.control_widget.plot_widgets:
            for control in (
                channel_widget.temp_input,
                channel_widget.set_btn,
                channel_widget.save_btn,
                channel_widget.output_btn,
            ):
                control.setEnabled(not active)

    def _toggle_recording(self, checked: bool) -> None:
        if checked:
            path, _ = QFileDialog.getSaveFileName(self, "Record temperatures", "temperature.csv", "CSV files (*.csv)")
            if not path or not self._recorder.start_recording(path):
                self.record_button.setChecked(False)
                return
            self.record_button.setText("Stop recording")
        else:
            self._recorder.stop_recording()
            self.record_button.setText("Record run to CSV…")

    def _refresh(self) -> None:
        if self.record_button.isChecked() and not self._recorder.recording:
            # The recorder stopped on its own (a CSV write failed and was logged):
            # the button must not keep promising a recording.
            self.record_button.setChecked(False)
