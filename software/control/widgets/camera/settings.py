from control.widgets.camera._common import *


class CameraSettingsWidget(EventBusFrame):
    """Camera settings widget using EventBus.

    Publishes command events for camera settings changes.
    Subscribes to state events to update UI.
    Does not access services directly.
    """

    signal_binning_changed: Signal = Signal()

    def __init__(
        self,
        event_bus: "EventBus",
        exposure_limits: tuple[float, float],
        gain_range: Optional["CameraGainRange"] = None,
        pixel_format_names: Optional[list[str]] = None,
        current_pixel_format: Optional[str] = None,
        roi_info: tuple[int, int, int, int] = (0, 0, 64, 64),
        resolution: tuple[int, int] = (64, 64),
        binning_options: Optional[list[tuple[int, int]]] = None,
        current_binning: Optional[tuple[int, int]] = None,
        include_gain_exposure_time: bool = False,
        include_camera_temperature_setting: bool = False,
        include_camera_auto_wb_setting: bool = False,
        main: Optional[Any] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(event_bus, *args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)

        # Stored configuration (read-only)
        self._exposure_limits = exposure_limits
        self._gain_range = gain_range
        self._pixel_format_names = pixel_format_names or [
            "MONO8",
            "MONO12",
            "MONO14",
            "MONO16",
            "BAYER_RG8",
            "BAYER_RG12",
        ]
        self._current_pixel_format = current_pixel_format
        self._roi_info = roi_info
        self._resolution = resolution
        self._binning_options = binning_options or []
        self._current_binning = current_binning

        # Subscribe to state updates using base class helper
        self._subscribe(ExposureTimeChanged, self._on_exposure_changed)
        self._subscribe(AnalogGainChanged, self._on_gain_changed)
        self._subscribe(ROIChanged, self._on_roi_changed)
        self._subscribe(BinningChanged, self._on_binning_changed)
        self._subscribe(PixelFormatChanged, self._on_pixel_format_changed)
        self._subscribe(BlackLevelChanged, self._on_black_level_changed)
        self._subscribe(AutoWhiteBalanceChanged, self._on_auto_wb_changed)

        self.add_components(
            include_gain_exposure_time,
            include_camera_temperature_setting,
            include_camera_auto_wb_setting,
        )
        # set frame style
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(
        self,
        include_gain_exposure_time: bool,
        include_camera_temperature_setting: bool,
        include_camera_auto_wb_setting: bool,
    ) -> None:
        # add buttons and input fields
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setKeyboardTracking(False)
        self.entry_exposureTime.setMinimum(self._exposure_limits[0])
        self.entry_exposureTime.setMaximum(self._exposure_limits[1])
        self.entry_exposureTime.setSingleStep(1)
        default_exposure = 20.0
        self.entry_exposureTime.setValue(default_exposure)
        self._publish(SetExposureTimeCommand(exposure_time_ms=default_exposure))

        self.entry_analogGain = QDoubleSpinBox()
        gain_range = self._gain_range
        if gain_range is not None:
            self.entry_analogGain.setMinimum(gain_range.min_gain)
            self.entry_analogGain.setMaximum(gain_range.max_gain)
            self.entry_analogGain.setSingleStep(gain_range.gain_step)
            self.entry_analogGain.setValue(gain_range.min_gain)
            self._publish(SetAnalogGainCommand(gain=gain_range.min_gain))
        else:
            self._log.info(
                "Camera does not support analog gain, disabling analog gain control."
            )
            self.entry_analogGain.setValue(0)
            self.entry_analogGain.setEnabled(False)

        self.dropdown_pixelFormat = QComboBox()
        pixel_format_names = self._pixel_format_names
        self.dropdown_pixelFormat.addItems(pixel_format_names)
        if self._current_pixel_format is not None:
            self.dropdown_pixelFormat.setCurrentText(self._current_pixel_format)
        self.dropdown_pixelFormat.setSizePolicy(
            QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        )
        # to do: load and save pixel format in configurations

        self.entry_ROI_offset_x = QSpinBox()
        roi_info = self._roi_info
        (max_x, max_y) = self._resolution
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

        # connection - use _publish for events
        self.entry_exposureTime.valueChanged.connect(self._publish_exposure_time)
        self.entry_analogGain.valueChanged.connect(self._publish_analog_gain)
        self.dropdown_pixelFormat.currentTextChanged.connect(self._publish_pixel_format)
        self.entry_ROI_offset_x.valueChanged.connect(self._publish_roi_offset)
        self.entry_ROI_offset_y.valueChanged.connect(self._publish_roi_offset)
        self.entry_ROI_height.valueChanged.connect(self._publish_height)
        self.entry_ROI_width.valueChanged.connect(self._publish_width)

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
        current_binning_string = (
            "x".join([str(self._current_binning[0]), str(self._current_binning[1])])
            if self._current_binning
            else None
        )
        binning_options = [
            f"{binning[0]}x{binning[1]}" for binning in self._binning_options
        ]
        self.dropdown_binning = QComboBox()
        if binning_options:
            self.dropdown_binning.addItems(binning_options)
        else:
            self.dropdown_binning.setEnabled(False)
        if current_binning_string:
            self.dropdown_binning.setCurrentText(current_binning_string)

        self.dropdown_binning.currentTextChanged.connect(self._publish_binning)
        format_line.addWidget(QLabel("Binning"))
        format_line.addWidget(self.dropdown_binning)
        self.camera_layout.addLayout(format_line)

        if include_camera_temperature_setting:
            temp_line = QHBoxLayout()
            temp_line.addWidget(QLabel("Set Temperature (C)"))
            temp_line.addWidget(self.entry_temperature)
            temp_line.addWidget(QLabel("Actual Temperature (C)"))
            temp_line.addWidget(self.label_temperature_measured)
            self.entry_temperature.valueChanged.connect(self._publish_temperature)
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
            self.label_blackLevel.valueChanged.connect(self._publish_blacklevel)
            self.label_blackLevel.setSuffix(" ")

            blacklevel_line.addWidget(self.label_blackLevel)

            self.camera_layout.addLayout(blacklevel_line)

        current_pixel_format = self._service.get_pixel_format()
        if (
            include_camera_auto_wb_setting
            and current_pixel_format is not None
            and CameraPixelFormat.is_color_format(current_pixel_format)
        ):
            # auto white balance
            self.btn_auto_wb = QPushButton("Auto White Balance")
            self.btn_auto_wb.setCheckable(True)
            self.btn_auto_wb.setChecked(False)
            self.btn_auto_wb.clicked.connect(self._toggle_auto_wb)

            self.camera_layout.addWidget(self.btn_auto_wb)

        self.setLayout(self.camera_layout)

    # ============================================================
    # Event handlers (state events from services)
    # ============================================================

    def _on_exposure_changed(self, event: ExposureTimeChanged) -> None:
        """Handle exposure time changed event."""
        self.entry_exposureTime.blockSignals(True)
        self.entry_exposureTime.setValue(event.exposure_time_ms)
        self.entry_exposureTime.blockSignals(False)

    def _on_gain_changed(self, event: AnalogGainChanged) -> None:
        """Handle analog gain changed event."""
        self.entry_analogGain.blockSignals(True)
        self.entry_analogGain.setValue(event.gain)
        self.entry_analogGain.blockSignals(False)

    def _on_roi_changed(self, event: ROIChanged) -> None:
        """Handle ROI changed event."""
        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_width.blockSignals(True)
        self.entry_ROI_height.blockSignals(True)

        self.entry_ROI_offset_x.setValue(event.x_offset)
        self.entry_ROI_offset_y.setValue(event.y_offset)
        self.entry_ROI_width.setValue(event.width)
        self.entry_ROI_height.setValue(event.height)

        self.entry_ROI_offset_x.blockSignals(False)
        self.entry_ROI_offset_y.blockSignals(False)
        self.entry_ROI_width.blockSignals(False)
        self.entry_ROI_height.blockSignals(False)

    def _on_binning_changed(self, event: BinningChanged) -> None:
        """Handle binning changed event."""
        binning_string = f"{event.binning_x}x{event.binning_y}"
        self.dropdown_binning.blockSignals(True)
        self.dropdown_binning.setCurrentText(binning_string)
        self.dropdown_binning.blockSignals(False)
        self.signal_binning_changed.emit()

    def _on_pixel_format_changed(self, event: PixelFormatChanged) -> None:
        """Handle pixel format changed event."""
        self.dropdown_pixelFormat.blockSignals(True)
        self.dropdown_pixelFormat.setCurrentText(event.pixel_format.name)
        self.dropdown_pixelFormat.blockSignals(False)

    def _on_black_level_changed(self, event: BlackLevelChanged) -> None:
        """Handle black level changed event."""
        if hasattr(self, "label_blackLevel"):
            self.label_blackLevel.blockSignals(True)
            self.label_blackLevel.setValue(event.level)
            self.label_blackLevel.blockSignals(False)

    def _on_auto_wb_changed(self, event: AutoWhiteBalanceChanged) -> None:
        """Handle auto white balance changed event."""
        if hasattr(self, "btn_auto_wb"):
            self.btn_auto_wb.blockSignals(True)
            self.btn_auto_wb.setChecked(event.enabled)
            self.btn_auto_wb.blockSignals(False)

    # ============================================================
    # Command publishers (user actions -> command events)
    # ============================================================

    def _publish_exposure_time(self, exposure_time: float) -> None:
        """Publish exposure time change via event bus."""
        self._publish(SetExposureTimeCommand(exposure_time_ms=exposure_time))

    def _publish_analog_gain(self, analog_gain: float) -> None:
        """Publish analog gain change via event bus."""
        self._publish(SetAnalogGainCommand(gain=analog_gain))

    def _publish_pixel_format(self, format_name: str) -> None:
        """Publish pixel format change via event bus."""
        self._publish(SetPixelFormatCommand(pixel_format=format_name))

    def _publish_binning(self, binning_text: str) -> None:
        """Publish binning change via event bus."""
        binning_parts = binning_text.split("x")
        binning_x = int(binning_parts[0])
        binning_y = int(binning_parts[1])
        self._publish(SetBinningCommand(binning_x=binning_x, binning_y=binning_y))

    def _publish_temperature(self) -> None:
        """Publish temperature change via event bus."""
        try:
            self._publish(
                SetCameraTemperatureCommand(
                    temperature_celsius=float(self.entry_temperature.value())
                )
            )
        except AttributeError:
            self._log.warning("Cannot set temperature - not supported.")

    def _publish_blacklevel(self, blacklevel: int) -> None:
        """Publish black level change via event bus."""
        try:
            self._publish(SetBlackLevelCommand(level=blacklevel))
        except AttributeError:
            self._log.warning("Cannot set black level - not supported.")

    def _toggle_auto_wb(self, pressed: bool) -> None:
        """Toggle auto white balance via event bus."""
        self._publish(SetAutoWhiteBalanceCommand(enabled=pressed))

    def _publish_width(self) -> None:
        """Publish width change - auto-centers ROI."""
        width = int(self.entry_ROI_width.value() // 8) * 8
        self.entry_ROI_width.blockSignals(True)
        self.entry_ROI_width.setValue(width)
        self.entry_ROI_width.blockSignals(False)

        # Auto-center X offset
        offset_x = (self._resolution[0] - width) / 2
        offset_x = int(offset_x // 8) * 8
        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_x.setValue(offset_x)
        self.entry_ROI_offset_x.blockSignals(False)

        self._publish(
            SetROICommand(
                x_offset=self.entry_ROI_offset_x.value(),
                y_offset=self.entry_ROI_offset_y.value(),
                width=self.entry_ROI_width.value(),
                height=self.entry_ROI_height.value(),
            )
        )

    def _publish_height(self) -> None:
        """Publish height change - auto-centers ROI."""
        height = int(self.entry_ROI_height.value() // 8) * 8
        self.entry_ROI_height.blockSignals(True)
        self.entry_ROI_height.setValue(height)
        self.entry_ROI_height.blockSignals(False)

        # Auto-center Y offset
        offset_y = (self._resolution[1] - height) / 2
        offset_y = int(offset_y // 8) * 8
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_offset_y.setValue(offset_y)
        self.entry_ROI_offset_y.blockSignals(False)

        self._publish(
            SetROICommand(
                x_offset=self.entry_ROI_offset_x.value(),
                y_offset=self.entry_ROI_offset_y.value(),
                width=self.entry_ROI_width.value(),
                height=self.entry_ROI_height.value(),
            )
        )

    def _publish_roi_offset(self) -> None:
        """Publish ROI offset change via event bus."""
        self._publish(
            SetROICommand(
                x_offset=self.entry_ROI_offset_x.value(),
                y_offset=self.entry_ROI_offset_y.value(),
                width=self.entry_ROI_width.value(),
                height=self.entry_ROI_height.value(),
            )
        )

    # ============================================================
    # Public API (for programmatic use)
    # ============================================================

    def set_analog_gain_if_supported(self, gain: float) -> None:
        try:
            self._publish(SetAnalogGainCommand(gain=gain))
        except NotImplementedError:
            self._log.warning(f"Cannot set gain to {gain}, gain not supported.")

    def set_exposure_time(self, exposure_time: float) -> None:
        self.entry_exposureTime.setValue(exposure_time)

    def set_analog_gain(self, analog_gain: float) -> None:
        self.entry_analogGain.setValue(analog_gain)

    def update_measured_temperature(self, temperature: float) -> None:
        self.label_temperature_measured.setNum(temperature)
