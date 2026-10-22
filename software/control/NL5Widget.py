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
    QAbstractSpinBox,
)
from qtpy.QtCore import Qt, Signal

import control._def
from control.NL5 import SLIT_POSITIONS_UM, LINE_SPEED_MIN, LINE_SPEED_MAX


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

    def accept(self):
        self.nl5.set_scan_amplitude(self.scan_amplitude_input.value())
        self.nl5.set_offset_x(self.offset_x_input.value())
        self.nl5.set_bypass_offset(self.bypass_offset_input.value())
        if self.slit_offset_input is not None:
            self.nl5.set_slit_offset(self.slit_offset_input.value())
        self.nl5.save_settings()
        super().accept()


class NL5Widget(QWidget):
    # The exposure delay, scan amplitude or FOV changed: the scan timing behind a line speed, and so the exposure the
    # unit gives the camera, changed with it.
    signal_scan_timing_changed = Signal()

    def __init__(self, nl5):
        super().__init__()
        self._line_speed_follows_exposure = False

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
        self.line_speed_input.setToolTip(self._speed_tooltip())
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
        self.signal_scan_timing_changed.emit()

    def set_line_speed_follows_exposure(self, follows: bool):
        """When the unit level-triggers the camera (hardware trigger, NL5_USE_DOUT), the camera's exposure is the
        exposure delay plus the scan, so the line speed is set from the channel's exposure time and this box
        only shows it (as a line speed or a frame rate, per the mode combo)."""
        self._line_speed_follows_exposure = follows
        box = self.line_speed_input
        box.setReadOnly(follows)
        box.setButtonSymbols(QAbstractSpinBox.NoButtons if follows else QAbstractSpinBox.UpDownArrows)
        box.setToolTip(self._speed_tooltip())
        self._refresh_speed_display()

    def show_line_speed(self, _exposure_ms: float, _line_speed: int):
        """Show the line speed the unit was set to (from a channel exposure) without sending it again."""
        self._refresh_speed_display()

    def update_bypass(self, checked):
        self.nl5.set_bypass(checked)
        self.start_acquisition_button.setEnabled(not checked)

    def update_exposure_delay(self, value):
        self.nl5.set_exposure_delay(value)
        self.signal_scan_timing_changed.emit()

    def _speed_mode(self):
        return self.speed_mode_combo.currentData() or "line_speed"

    def _speed_tooltip(self):
        how = (
            "Set by the channel's exposure time: line speed = scan sweep / (exposure - exposure delay). "
            "Edit the exposure time to change it."
            if self._line_speed_follows_exposure
            else "Press Enter to apply."
        )
        return (
            f"{how}\n"
            f"Line speed: {LINE_SPEED_MIN}-{LINE_SPEED_MAX} mrad/s.\n"
            "Frame rate = line speed / scan sweep, where the sweep is 2 x the scan amplitude (the scanner\n"
            "travels +/- amplitude) scaled by FOV / the unit's camera width. Scanner-limited: excludes the\n"
            "exposure delay, the jump back and the camera's readout and transfer (~56 ms for a full frame)."
        )

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
        if self._line_speed_follows_exposure:
            self._refresh_speed_display()  # read-only: the channel's exposure time sets it
            return
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
        self.signal_scan_timing_changed.emit()

    def update_slit_width(self, _index):
        width = self.slit_combo.currentData()
        if width is not None:
            self.nl5.set_slit_width(width)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    import NL5

    nl5 = NL5.NL5()
    widget = NL5Widget(nl5)
    widget.show()

    sys.exit(app.exec_())
