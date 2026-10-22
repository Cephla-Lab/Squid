import sys
from qtpy.QtWidgets import (
    QApplication,
    QWidget,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QSizePolicy,
)
from qtpy.QtCore import Qt, QTimer

import control._def
import squid.logging
from control.NL5 import (
    SLIT_POSITIONS_UM,
    LINE_SPEED_MIN,
    LINE_SPEED_MAX,
    SLIT_OSCILLATION_MAX_UM,
    SLIT_OSCILLATION_MIN_HZ,
    SLIT_OSCILLATION_MAX_HZ,
    SLIT_SWEEP_MIN_UM_S,
    SLIT_SWEEP_MAX_UM_S,
    SlitOscillation,
)


class NL5SettingsDialog(QDialog):
    def __init__(self, nl5):
        super().__init__()
        self.nl5 = nl5
        self.setWindowTitle(f"{getattr(self.nl5, 'device_name', 'NL5')} Settings")

        layout = QGridLayout()

        # Scan amplitude control
        layout.addWidget(QLabel("Scan Amplitude"), 0, 0)
        self.scan_amplitude_input = QDoubleSpinBox()
        self.scan_amplitude_input.setValue(self.nl5.scan_amplitude)
        layout.addWidget(self.scan_amplitude_input, 0, 1)

        # Offset X control
        layout.addWidget(QLabel("Offset X"), 1, 0)
        self.offset_x_input = QDoubleSpinBox()
        self.offset_x_input.setMinimum(-30)
        self.offset_x_input.setValue(self.nl5.offset_x)
        layout.addWidget(self.offset_x_input, 1, 1)

        # Bypass offset control
        layout.addWidget(QLabel("Bypass Offset"), 2, 0)
        self.bypass_offset_input = QDoubleSpinBox()
        self.bypass_offset_input.setMinimum(-30)
        self.bypass_offset_input.setValue(self.nl5.bypass_offset)
        layout.addWidget(self.bypass_offset_input, 2, 1)

        # Slit offset control and home button (only when internal stage is present)
        self.slit_offset_input = None
        self.home_stage_button = None
        next_row = 3
        if self.nl5.internal_stage is not None:
            layout.addWidget(QLabel("Slit Offset"), next_row, 0)
            self.slit_offset_input = QDoubleSpinBox()
            self.slit_offset_input.setRange(-500, 500)
            self.slit_offset_input.setSuffix(" µm")
            self.slit_offset_input.setValue(self.nl5.slit_offset_um)
            layout.addWidget(self.slit_offset_input, next_row, 1)
            next_row += 1

            next_row = self._add_slit_oscillation_rows(layout, next_row)

            self.home_stage_button = QPushButton("Home Stage")
            self.home_stage_button.clicked.connect(self.nl5.home_internal_stage)
            layout.addWidget(self.home_stage_button, next_row, 0, 1, 2)
            next_row += 1

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, next_row, 0, 1, 2)

        self.setLayout(layout)

    def _add_slit_oscillation_rows(self, layout, row):
        """Slit oscillation settings; the on/off button is on the device tab. Applied on OK, also to a running
        oscillation (from its next move). Not saved."""
        osc = self.nl5.slit_oscillation

        def spinbox(minimum, maximum, value, decimals, suffix):
            box = QDoubleSpinBox()
            box.setRange(minimum, maximum)
            box.setDecimals(decimals)
            box.setValue(value)
            box.setSuffix(suffix)
            return box

        self.slit_osc_plus_input = spinbox(0, SLIT_OSCILLATION_MAX_UM, osc.plus_um, 0, " µm")
        self.slit_osc_minus_input = spinbox(0, SLIT_OSCILLATION_MAX_UM, osc.minus_um, 0, " µm")
        self.slit_osc_mode_combo = QComboBox()
        self.slit_osc_mode_combo.addItem("Jump", "jump")
        self.slit_osc_mode_combo.addItem("Sweep", "sweep")
        self.slit_osc_mode_combo.setCurrentIndex(max(0, self.slit_osc_mode_combo.findData(osc.mode)))
        self.slit_osc_mode_combo.setToolTip(
            "Jump: move to each end and hold it, at the set frequency.\n"
            "Sweep: travel between the ends at the set speed, waiting at each end until the slit is there."
        )
        self.slit_osc_freq_input = spinbox(SLIT_OSCILLATION_MIN_HZ, SLIT_OSCILLATION_MAX_HZ, osc.frequency_hz, 1, " Hz")
        self.slit_osc_freq_input.setToolTip(
            "The stage needs ~134 ms to settle a 100 µm move: at 3 Hz a +/-50 µm swing reaches both ends,\n"
            "larger swings need a lower frequency or they turn around short of the set positions."
        )
        self.slit_osc_speed_input = spinbox(SLIT_SWEEP_MIN_UM_S, SLIT_SWEEP_MAX_UM_S, osc.speed_um_s, 0, " µm/s")
        self.slit_osc_speed_input.setToolTip(
            "The slit follows a new position every 20 ms, about 100 ms behind it, and stops at each end until\n"
            "it is there (typically 50-150 ms)."
        )
        self.slit_osc_sweep_hz_label = QLabel()

        for label, widget in (
            ("Oscillation +", self.slit_osc_plus_input),
            ("Oscillation −", self.slit_osc_minus_input),
            ("Oscillation Mode", self.slit_osc_mode_combo),
            ("Oscillation Frequency", self.slit_osc_freq_input),
            ("Sweep Speed", self.slit_osc_speed_input),
            ("", self.slit_osc_sweep_hz_label),
        ):
            layout.addWidget(QLabel(label), row, 0)
            layout.addWidget(widget, row, 1)
            row += 1

        for signal in (
            self.slit_osc_plus_input.valueChanged,
            self.slit_osc_minus_input.valueChanged,
            self.slit_osc_speed_input.valueChanged,
            self.slit_osc_mode_combo.currentIndexChanged,
        ):
            signal.connect(self._update_slit_oscillation_rows)
        self._update_slit_oscillation_rows()
        return row

    def _slit_oscillation_from_inputs(self):
        return SlitOscillation(
            plus_um=self.slit_osc_plus_input.value(),
            minus_um=self.slit_osc_minus_input.value(),
            mode=self.slit_osc_mode_combo.currentData(),
            frequency_hz=self.slit_osc_freq_input.value(),
            speed_um_s=self.slit_osc_speed_input.value(),
        )

    def _update_slit_oscillation_rows(self, *_):
        osc = self._slit_oscillation_from_inputs()
        sweep = osc.mode == "sweep"
        self.slit_osc_freq_input.setEnabled(not sweep)
        self.slit_osc_speed_input.setEnabled(sweep)
        self.slit_osc_sweep_hz_label.setVisible(sweep)
        self.slit_osc_sweep_hz_label.setText(f"Sweep frequency ≤ {osc.sweep_frequency_hz():.2f} Hz (plus the stops)")

    def accept(self):
        self.nl5.set_scan_amplitude(self.scan_amplitude_input.value())
        self.nl5.set_offset_x(self.offset_x_input.value())
        self.nl5.set_bypass_offset(self.bypass_offset_input.value())
        if self.slit_offset_input is not None:
            self.nl5.set_slit_offset(self.slit_offset_input.value())
            self.nl5.set_slit_oscillation(self._slit_oscillation_from_inputs())
        self.nl5.save_settings()
        super().accept()


class NL5Widget(QWidget):
    def __init__(self, nl5):
        super().__init__()

        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.nl5 = nl5

        # Create layout
        layout1 = QHBoxLayout()
        layout2 = QHBoxLayout()

        # Exposure delay control
        layout1.addWidget(QLabel("Exposure Delay"))
        self.exposure_delay_input = QSpinBox()
        self.exposure_delay_input.setValue(self.nl5.exposure_delay_ms)
        self.exposure_delay_input.setSuffix(" ms")
        self.exposure_delay_input.valueChanged.connect(self.update_exposure_delay)
        layout1.addWidget(self.exposure_delay_input)
        layout1.addStretch()

        # Scan speed control: entered either as scanner line speed (mrad/s) or as the scan-limited
        # frame rate (fps). The value is sent to the device only when Enter is pressed.
        self.speed_mode_combo = QComboBox()
        self.speed_mode_combo.addItem("Line Speed", "line_speed")
        self.speed_mode_combo.addItem("Frame Rate", "fps")
        self.speed_mode_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        layout1.addWidget(self.speed_mode_combo)
        self.line_speed_input = QDoubleSpinBox()
        self.line_speed_input.setKeyboardTracking(False)
        self.line_speed_input.setToolTip(
            "Press Enter to apply.\n"
            f"Line speed: {LINE_SPEED_MIN}-{LINE_SPEED_MAX} mrad/s.\n"
            "Frame rate = line speed / scan sweep, where the sweep is 2 x the scan amplitude (the scanner\n"
            "travels +/- amplitude) scaled by FOV / camera width. Scanner-limited: excludes the exposure\n"
            "delay, the jump back and the camera's readout and transfer (~30 ms for a full frame)."
        )
        self.line_speed_input.lineEdit().returnPressed.connect(self.apply_speed_input)
        layout1.addWidget(self.line_speed_input)
        mode_index = self.speed_mode_combo.findData(getattr(self.nl5, "speed_input_mode", "line_speed"))
        self.speed_mode_combo.setCurrentIndex(max(0, mode_index))
        self._configure_speed_input()
        self.speed_mode_combo.currentIndexChanged.connect(self.update_speed_mode)
        layout1.addStretch()

        # FOV X control
        layout1.addWidget(QLabel("FOV"))
        # layout1.addWidget(QLabel("FOV X"))
        self.fov_x_input = QSpinBox()
        self.fov_x_input.setMaximum(4000)
        self.fov_x_input.setValue(self.nl5.fov_x)
        self.fov_x_input.setSuffix(" px")
        self.fov_x_input.valueChanged.connect(self.update_fov_x)
        layout1.addWidget(self.fov_x_input)

        # Slit selector (only when internal stage is present)
        self.slit_combo = None
        if self.nl5.internal_stage is not None:
            slit_label = QLabel("Slit")
            slit_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            layout2.addWidget(slit_label)
            self.slit_combo = QComboBox()
            for width in SLIT_POSITIONS_UM:
                self.slit_combo.addItem(f"{width} µm", width)
            index = self.slit_combo.findData(self.nl5.slit_width_um)
            if index >= 0:
                self.slit_combo.setCurrentIndex(index)
            self.slit_combo.currentIndexChanged.connect(self.update_slit_width)
            self.slit_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            layout2.addWidget(self.slit_combo)

            # Slit oscillation on/off; its settings (+ / −, jump or sweep, frequency, speed) are in the settings dialog.
            self.slit_osc_button = QPushButton("Oscillate Slit")
            self.slit_osc_button.setCheckable(True)
            self.slit_osc_button.setToolTip(
                "Move the slit back and forth around its centre (slit position + slit offset), as set in the\n"
                "settings dialog. Unchecking returns it to the centre."
            )
            self.slit_osc_button.toggled.connect(self.update_slit_oscillation)
            layout2.addWidget(self.slit_osc_button)
            # The oscillation can end on its own (serial error, failed homing); keep the button honest.
            self.slit_osc_sync_timer = QTimer(self)
            self.slit_osc_sync_timer.setInterval(1000)
            self.slit_osc_sync_timer.timeout.connect(self._sync_slit_oscillation_button)

        # Bypass control (hidden on systems without a brightfield path, see NL5_SHOW_BYPASS_BUTTON)
        self.bypass_button = QPushButton("Enable Bypass")
        self.bypass_button.setCheckable(True)
        self.bypass_button.toggled.connect(self.update_bypass)
        if getattr(control._def, "NL5_SHOW_BYPASS_BUTTON", True):
            layout2.addWidget(self.bypass_button)
        else:
            self.bypass_button.hide()

        # Start acquisition button
        self.start_acquisition_button = QPushButton("Start Acquisition")
        self.start_acquisition_button.clicked.connect(self.nl5.start_acquisition)
        # layout2.addWidget(self.start_acquisition_button, 3, 0, 1, 4)

        # NL5 Settings button
        self.settings_button = QPushButton(f"Edit {getattr(self.nl5, 'device_name', 'NL5')} Settings")
        self.settings_button.clicked.connect(self.show_settings_dialog)
        layout2.addWidget(self.settings_button)

        layout = QVBoxLayout()
        layout.addLayout(layout1)
        layout.addLayout(layout2)
        self.setLayout(layout)

    def show_settings_dialog(self):
        dialog = NL5SettingsDialog(self.nl5)
        dialog.exec_()
        # the scan amplitude may have changed, which changes the frame rate for the same line speed
        self._refresh_speed_display()
        if self.slit_combo is not None:
            self._sync_slit_oscillation_button()  # homing pauses the oscillation and may fail to resume it

    def update_bypass(self, checked):
        self.nl5.set_bypass(checked)
        self.start_acquisition_button.setEnabled(not checked)

    def update_exposure_delay(self, value):
        self.nl5.set_exposure_delay(value)

    def _speed_mode(self):
        return self.speed_mode_combo.currentData() or "line_speed"

    def _configure_speed_input(self):
        """Set range, decimals and suffix for the current mode and show the device's current speed."""
        box = self.line_speed_input
        box.blockSignals(True)
        if self._speed_mode() == "fps":
            box.setDecimals(2)
            box.setSingleStep(0.5)
            box.setRange(self.nl5.line_speed_to_fps(LINE_SPEED_MIN), self.nl5.line_speed_to_fps(LINE_SPEED_MAX))
            box.setSuffix(" fps")
        else:
            box.setDecimals(0)
            box.setSingleStep(100)
            box.setRange(LINE_SPEED_MIN, LINE_SPEED_MAX)
            box.setSuffix(" mrad/s")
        box.blockSignals(False)
        self._refresh_speed_display()

    def _refresh_speed_display(self):
        """Show the device's current line speed in the selected unit without sending anything."""
        box = self.line_speed_input
        box.blockSignals(True)
        if self._speed_mode() == "fps":
            box.setRange(self.nl5.line_speed_to_fps(LINE_SPEED_MIN), self.nl5.line_speed_to_fps(LINE_SPEED_MAX))
            box.setValue(self.nl5.line_speed_to_fps(self.nl5.line_speed))
        else:
            box.setValue(self.nl5.line_speed)
        box.blockSignals(False)

    def update_speed_mode(self, _index):
        self.nl5.set_speed_input_mode(self._speed_mode())
        self._configure_speed_input()

    def apply_speed_input(self):
        """Enter pressed: send the entered value to the device as a line speed."""
        value = self.line_speed_input.value()
        if self._speed_mode() == "fps":
            line_speed = self.nl5.fps_to_line_speed(value)
        else:
            line_speed = value
        self.nl5.set_line_speed(line_speed, save_setting=True)
        # show what was actually applied (clamped / rounded)
        self._refresh_speed_display()

    def update_fov_x(self, value):
        self.nl5.set_fov_x(value)
        self._refresh_speed_display()

    def update_slit_width(self, _index):
        width = self.slit_combo.currentData()
        if width is not None:
            self.nl5.set_slit_width(width)

    def update_slit_oscillation(self, checked):
        try:
            if checked:
                self.nl5.start_slit_oscillation()
            else:
                self.nl5.stop_slit_oscillation()
        except Exception as e:
            self._log.error(f"Slit oscillation failed: {e}")
        self._sync_slit_oscillation_button()

    def _sync_slit_oscillation_button(self):
        """Show whether the slit is actually oscillating, and watch it while it does."""
        oscillating = self.nl5.is_slit_oscillating()
        if self.slit_osc_button.isChecked() and not oscillating:
            # It ended on its own (serial error, failed homing): put the slit back at its centre, which also
            # applies a slit or offset chosen while it oscillated.
            try:
                self.nl5.stop_slit_oscillation()
            except Exception as e:
                self._log.error(f"Could not return the slit to its centre: {e}")
        self.slit_osc_button.blockSignals(True)
        self.slit_osc_button.setChecked(oscillating)
        self.slit_osc_button.blockSignals(False)
        if oscillating:
            self.slit_osc_sync_timer.start()
        else:
            self.slit_osc_sync_timer.stop()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    import NL5

    nl5 = NL5.NL5()
    widget = NL5Widget(nl5)
    widget.show()

    sys.exit(app.exec_())
