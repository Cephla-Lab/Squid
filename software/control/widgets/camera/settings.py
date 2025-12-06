from control.widgets.camera._common import *

class CameraSettingsWidget(QFrame):

    signal_binning_changed = Signal()

    def __init__(
        self,
        camera_service: "CameraService",
        include_gain_exposure_time=False,
        include_camera_temperature_setting=False,
        include_camera_auto_wb_setting=False,
        main=None,
        *args,
        **kwargs,
    ):

        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)

        self._service = camera_service

        # Subscribe to state updates
        event_bus.subscribe(ExposureTimeChanged, self._on_exposure_changed)
        event_bus.subscribe(AnalogGainChanged, self._on_gain_changed)

        self.add_components(
            include_gain_exposure_time, include_camera_temperature_setting, include_camera_auto_wb_setting
        )
        # set frame style
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(
        self, include_gain_exposure_time, include_camera_temperature_setting, include_camera_auto_wb_setting
    ):

        # add buttons and input fields
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setKeyboardTracking(False)
        self.entry_exposureTime.setMinimum(self._service.get_exposure_limits()[0])
        self.entry_exposureTime.setMaximum(self._service.get_exposure_limits()[1])
        self.entry_exposureTime.setSingleStep(1)
        default_exposure = 20.0
        self.entry_exposureTime.setValue(default_exposure)
        self._service.set_exposure_time(default_exposure)

        self.entry_analogGain = QDoubleSpinBox()
        try:
            gain_range = self._service.get_gain_range()
            self.entry_analogGain.setMinimum(gain_range.min_gain)
            self.entry_analogGain.setMaximum(gain_range.max_gain)
            self.entry_analogGain.setSingleStep(gain_range.gain_step)
            self.entry_analogGain.setValue(gain_range.min_gain)
            self._service.set_analog_gain(gain_range.min_gain)
        except NotImplementedError:
            self._log.info("Camera does not support analog gain, disabling analog gain control.")
            self.entry_analogGain.setValue(0)
            self.entry_analogGain.setEnabled(False)

        self.dropdown_pixelFormat = QComboBox()
        try:
            pixel_formats = self._service.get_available_pixel_formats()
            pixel_formats = [pf.name for pf in pixel_formats]
        except NotImplementedError:
            pixel_formats = ["MONO8", "MONO12", "MONO14", "MONO16", "BAYER_RG8", "BAYER_RG12"]
        self.dropdown_pixelFormat.addItems(pixel_formats)
        if self._service.get_pixel_format() is not None:
            self.dropdown_pixelFormat.setCurrentText(self._service.get_pixel_format().name)
        else:
            print("setting camera's default pixel format")
            self._service.set_pixel_format(CameraPixelFormat.from_string(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT))
            self.dropdown_pixelFormat.setCurrentText(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT)
        self.dropdown_pixelFormat.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed))
        # to do: load and save pixel format in configurations

        self.entry_ROI_offset_x = QSpinBox()
        roi_info = self._service.get_region_of_interest()
        (max_x, max_y) = self._service.get_resolution()
        self.entry_ROI_offset_x.setValue(roi_info[0])
        self.entry_ROI_offset_x.setSingleStep(8)
        self.entry_ROI_offset_x.setFixedWidth(60)
        self.entry_ROI_offset_x.setMinimum(0)
        self.entry_ROI_offset_x.setMaximum(max_x)
        self.entry_ROI_offset_x.setKeyboardTracking(False)
        self.entry_ROI_offset_y = QSpinBox()
        self.entry_ROI_offset_y.setValue(roi_info[1])
        self.entry_ROI_offset_y.setSingleStep(8)
        self.entry_ROI_offset_y.setFixedWidth(60)
        self.entry_ROI_offset_y.setMinimum(0)
        self.entry_ROI_offset_y.setMaximum(max_y)
        self.entry_ROI_offset_y.setKeyboardTracking(False)
        self.entry_ROI_width = QSpinBox()
        self.entry_ROI_width.setMinimum(16)
        self.entry_ROI_width.setMaximum(max_x)
        self.entry_ROI_width.setValue(roi_info[2])
        self.entry_ROI_width.setSingleStep(8)
        self.entry_ROI_width.setFixedWidth(60)
        self.entry_ROI_width.setKeyboardTracking(False)
        self.entry_ROI_height = QSpinBox()
        self.entry_ROI_height.setSingleStep(8)
        self.entry_ROI_height.setMinimum(16)
        self.entry_ROI_height.setMaximum(max_y)
        self.entry_ROI_height.setValue(roi_info[3])
        self.entry_ROI_height.setFixedWidth(60)
        self.entry_ROI_height.setKeyboardTracking(False)
        self.entry_temperature = QDoubleSpinBox()
        self.entry_temperature.setKeyboardTracking(False)
        self.entry_temperature.setMaximum(25)
        self.entry_temperature.setMinimum(-50)
        self.entry_temperature.setDecimals(1)
        self.label_temperature_measured = QLabel()
        # self.label_temperature_measured.setNum(0)
        self.label_temperature_measured.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        # connection
        self.entry_exposureTime.valueChanged.connect(self._service.set_exposure_time)
        self.entry_analogGain.valueChanged.connect(self._set_analog_gain_via_service)
        self.dropdown_pixelFormat.currentTextChanged.connect(
            lambda s: self._service.set_pixel_format(CameraPixelFormat.from_string(s))
        )
        self.entry_ROI_offset_x.valueChanged.connect(self.set_ROI_offset)
        self.entry_ROI_offset_y.valueChanged.connect(self.set_ROI_offset)
        self.entry_ROI_height.valueChanged.connect(self.set_Height)
        self.entry_ROI_width.valueChanged.connect(self.set_Width)

        # layout
        self.camera_layout = QVBoxLayout()
        if include_gain_exposure_time:
            exposure_line = QHBoxLayout()
            exposure_line.addWidget(QLabel("Exposure Time (ms)"))
            exposure_line.addWidget(self.entry_exposureTime)
            self.camera_layout.addLayout(exposure_line)
            gain_line = QHBoxLayout()
            gain_line.addWidget(QLabel("Analog Gain"))
            gain_line.addWidget(self.entry_analogGain)
            self.camera_layout.addLayout(gain_line)

        format_line = QHBoxLayout()
        format_line.addWidget(QLabel("Pixel Format"))
        format_line.addWidget(self.dropdown_pixelFormat)
        try:
            current_binning = self._service.get_binning()
            current_binning_string = "x".join([str(current_binning[0]), str(current_binning[1])])
            binning_options = [f"{binning[0]}x{binning[1]}" for binning in self._service.get_binning_options()]
            self.dropdown_binning = QComboBox()
            self.dropdown_binning.addItems(binning_options)
            self.dropdown_binning.setCurrentText(current_binning_string)

            self.dropdown_binning.currentTextChanged.connect(self.set_binning)
        except AttributeError as ae:
            print(ae)
            self.dropdown_binning = QComboBox()
            self.dropdown_binning.setEnabled(False)
            pass
        format_line.addWidget(QLabel("Binning"))
        format_line.addWidget(self.dropdown_binning)
        self.camera_layout.addLayout(format_line)

        if include_camera_temperature_setting:
            temp_line = QHBoxLayout()
            temp_line.addWidget(QLabel("Set Temperature (C)"))
            temp_line.addWidget(self.entry_temperature)
            temp_line.addWidget(QLabel("Actual Temperature (C)"))
            temp_line.addWidget(self.label_temperature_measured)
            try:
                self.entry_temperature.valueChanged.connect(self.set_temperature)
                self._service.set_temperature_reading_callback(self.update_measured_temperature)
            except AttributeError:
                pass
            self.camera_layout.addLayout(temp_line)

        roi_line = QHBoxLayout()
        roi_line.addWidget(QLabel("Height"))
        roi_line.addWidget(self.entry_ROI_height)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("Y-offset"))
        roi_line.addWidget(self.entry_ROI_offset_y)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("Width"))
        roi_line.addWidget(self.entry_ROI_width)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("X-offset"))
        roi_line.addWidget(self.entry_ROI_offset_x)
        self.camera_layout.addLayout(roi_line)

        if DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS is True:
            blacklevel_line = QHBoxLayout()
            blacklevel_line.addWidget(QLabel("Black Level"))

            self.label_blackLevel = QSpinBox()
            self.label_blackLevel.setKeyboardTracking(False)
            self.label_blackLevel.setMinimum(0)
            self.label_blackLevel.setMaximum(31)
            self.label_blackLevel.valueChanged.connect(self.update_blacklevel)
            self.label_blackLevel.setSuffix(" ")

            blacklevel_line.addWidget(self.label_blackLevel)

            self.camera_layout.addLayout(blacklevel_line)

        if include_camera_auto_wb_setting and CameraPixelFormat.is_color_format(self._service.get_pixel_format()):
            # auto white balance
            self.btn_auto_wb = QPushButton("Auto White Balance")
            self.btn_auto_wb.setCheckable(True)
            self.btn_auto_wb.setChecked(False)
            self.btn_auto_wb.clicked.connect(self.toggle_auto_wb)

            self.camera_layout.addWidget(self.btn_auto_wb)

        self.setLayout(self.camera_layout)

    def set_analog_gain_if_supported(self, gain):
        try:
            self._service.set_analog_gain(gain)
        except NotImplementedError:
            self._log.warning(f"Cannot set gain to {gain}, gain not supported.")

    def _set_analog_gain_via_service(self, gain):
        """Set analog gain through service layer."""
        self._service.set_analog_gain(gain)

    def _on_exposure_changed(self, event: ExposureTimeChanged):
        """Handle exposure time changed event."""
        self.entry_exposureTime.blockSignals(True)
        self.entry_exposureTime.setValue(event.exposure_time_ms)
        self.entry_exposureTime.blockSignals(False)

    def _on_gain_changed(self, event: AnalogGainChanged):
        """Handle analog gain changed event."""
        self.entry_analogGain.blockSignals(True)
        self.entry_analogGain.setValue(event.gain)
        self.entry_analogGain.blockSignals(False)

    def toggle_auto_wb(self, pressed):
        # 0: OFF  1:CONTINUOUS  2:ONCE
        if pressed:
            # Run auto white balance once, then uncheck
            self._service.set_auto_white_balance(True)
        else:
            self._service.set_auto_white_balance(False)
            r, g, b = self._service.get_white_balance_gains()
            self._service.set_white_balance_gains(r, g, b)

    def set_exposure_time(self, exposure_time):
        self.entry_exposureTime.setValue(exposure_time)

    def set_analog_gain(self, analog_gain):
        self.entry_analogGain.setValue(analog_gain)

    def set_Width(self):
        width = int(self.entry_ROI_width.value() // 8) * 8
        self.entry_ROI_width.blockSignals(True)
        self.entry_ROI_width.setValue(width)
        self.entry_ROI_width.blockSignals(False)
        offset_x = (self._service.get_resolution()[0] - self.entry_ROI_width.value()) / 2
        offset_x = int(offset_x // 8) * 8
        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_x.setValue(offset_x)
        self.entry_ROI_offset_x.blockSignals(False)
        self._service.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_Height(self):
        height = int(self.entry_ROI_height.value() // 8) * 8
        self.entry_ROI_height.blockSignals(True)
        self.entry_ROI_height.setValue(height)
        self.entry_ROI_height.blockSignals(False)
        offset_y = (self._service.get_resolution()[1] - self.entry_ROI_height.value()) / 2
        offset_y = int(offset_y // 8) * 8
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_offset_y.setValue(offset_y)
        self.entry_ROI_offset_y.blockSignals(False)
        self._service.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_ROI_offset(self):
        self._service.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_temperature(self):
        try:
            self._service.set_temperature(float(self.entry_temperature.value()))
        except AttributeError:
            self._log.warning("Cannot set temperature - not supported.")

    def update_measured_temperature(self, temperature):
        self.label_temperature_measured.setNum(temperature)

    def set_binning(self, binning_text):
        binning_parts = binning_text.split("x")
        binning_x = int(binning_parts[0])
        binning_y = int(binning_parts[1])

        self._service.set_binning(binning_x, binning_y)

        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_height.blockSignals(True)
        self.entry_ROI_width.blockSignals(True)

        # TODO: move these calculations to camera class as they can be different for different cameras
        def round_to_8(val):
            return int(8 * val // 8)

        (x_offset, y_offset, width, height) = self._service.get_region_of_interest()
        (x_max, y_max) = self._service.get_resolution()
        self.entry_ROI_height.setMaximum(y_max)
        self.entry_ROI_width.setMaximum(x_max)

        self.entry_ROI_offset_x.setMaximum(x_max)
        self.entry_ROI_offset_y.setMaximum(y_max)

        self.entry_ROI_offset_x.setValue(round_to_8(x_offset))
        self.entry_ROI_offset_y.setValue(round_to_8(y_offset))
        self.entry_ROI_height.setValue(round_to_8(height))
        self.entry_ROI_width.setValue(round_to_8(width))

        self.entry_ROI_offset_x.blockSignals(False)
        self.entry_ROI_offset_y.blockSignals(False)
        self.entry_ROI_height.blockSignals(False)
        self.entry_ROI_width.blockSignals(False)

        self.signal_binning_changed.emit()

    def update_blacklevel(self, blacklevel):
        try:
            self._service.set_black_level(blacklevel)
        except AttributeError:
            self._log.warning("Cannot set black level - not supported.")


