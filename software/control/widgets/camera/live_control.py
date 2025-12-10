from control.widgets.camera._common import *


class LiveControlWidget(QFrame):
    signal_newExposureTime: Signal = Signal(float)
    signal_newAnalogGain: Signal = Signal(float)
    signal_autoLevelSetting: Signal = Signal(bool)
    signal_live_configuration: Signal = Signal(object)
    signal_start_live: Signal = Signal()

    def __init__(
        self,
        streamHandler: StreamHandler,
        liveController: LiveController,
        objectiveStore: ObjectiveStore,
        channelConfigurationManager: ChannelConfigurationManager,
        show_trigger_options: bool = True,
        show_display_options: bool = False,
        show_autolevel: bool = False,
        autolevel: bool = False,
        stretch: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.liveController = liveController
        self.camera = self.liveController.microscope.camera
        self.streamHandler = streamHandler
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.fps_trigger = 10
        self.fps_display = 10
        event_bus.publish(SetTriggerFPSCommand(fps=self.fps_trigger))
        self.streamHandler.set_display_fps(self.fps_display)

        self.currentConfiguration = (
            self.channelConfigurationManager.get_channel_configurations_for_objective(
                self.objectiveStore.current_objective
            )[0]
        )

        self.add_components(
            show_trigger_options,
            show_display_options,
            show_autolevel,
            autolevel,
            stretch,
        )
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        event_bus.publish(
            SetMicroscopeModeCommand(
                configuration_name=self.currentConfiguration.name,
                objective=self.objectiveStore.current_objective,
            )
        )
        self.update_ui_for_mode(self.currentConfiguration)

        self.is_switching_mode = False  # flag used to prevent from settings being set by twice - from both mode change slot and value change slot; another way is to use blockSignals(True)

        # Subscribe to state changes from the bus
        event_bus.subscribe(LiveStateChanged, self._on_live_state_changed)
        event_bus.subscribe(TriggerModeChanged, self._on_trigger_mode_changed)
        event_bus.subscribe(TriggerFPSChanged, self._on_trigger_fps_changed)
        event_bus.subscribe(MicroscopeModeChanged, self._on_microscope_mode_changed)

    def add_components(
        self,
        show_trigger_options: bool,
        show_display_options: bool,
        show_autolevel: bool,
        autolevel: bool,
        stretch: bool,
    ) -> None:
        # line 0: trigger mode
        self.dropdown_triggerManu = QComboBox()
        self.dropdown_triggerManu.addItems(
            [TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS]
        )
        self.dropdown_triggerManu.setCurrentText(
            self.camera.get_acquisition_mode().value
        )
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown_triggerManu.setSizePolicy(sizePolicy)

        # line 1: fps
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setKeyboardTracking(False)
        self.entry_triggerFPS.setMinimum(0.02)
        self.entry_triggerFPS.setMaximum(1000)
        self.entry_triggerFPS.setSingleStep(1)
        self.entry_triggerFPS.setValue(self.fps_trigger)

        self.entry_displayFPS = QDoubleSpinBox()
        self.entry_displayFPS.setKeyboardTracking(False)
        self.entry_displayFPS.setMinimum(1)
        self.entry_displayFPS.setMaximum(240)
        self.entry_displayFPS.setSingleStep(1)
        self.entry_displayFPS.setValue(self.fps_display)

        # line 2: choose microscope mode / channel
        self.dropdown_modeSelection = QComboBox()
        for (
            mode
        ) in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.dropdown_modeSelection.addItem(mode.name)
        self.dropdown_modeSelection.setCurrentText(self.currentConfiguration.name)

        # line 3: exposure time and analog gain associated with the current mode
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setKeyboardTracking(False)
        self.entry_exposureTime.setMinimum(self.camera.get_exposure_limits()[0])
        self.entry_exposureTime.setMaximum(self.camera.get_exposure_limits()[1])
        self.entry_exposureTime.setSingleStep(1)
        self.entry_exposureTime.setValue(self.currentConfiguration.exposure_time)

        self.entry_analogGain = QDoubleSpinBox()
        self.entry_analogGain.setKeyboardTracking(False)
        try:
            gain_range = self.camera.get_gain_range()
            self.entry_analogGain.setMinimum(gain_range.min_gain)
            self.entry_analogGain.setMaximum(gain_range.max_gain)
            self.entry_analogGain.setSingleStep(gain_range.gain_step)
            self.entry_analogGain.setValue(self.currentConfiguration.analog_gain)
        except NotImplementedError:
            self._log.info(
                "Camera does not support analog gain, disabling analog gain control."
            )
            self.entry_analogGain.setValue(0)
            self.entry_analogGain.setEnabled(False)

        self.slider_illuminationIntensity = QSlider(Qt.Orientation.Horizontal)
        self.slider_illuminationIntensity.setTickPosition(QSlider.TicksBelow)
        self.slider_illuminationIntensity.setMinimum(0)
        self.slider_illuminationIntensity.setMaximum(100)
        self.slider_illuminationIntensity.setValue(100)
        self.slider_illuminationIntensity.setSingleStep(1)

        self.entry_illuminationIntensity = QDoubleSpinBox()
        self.entry_illuminationIntensity.setKeyboardTracking(False)
        self.entry_illuminationIntensity.setMinimum(0)
        self.entry_illuminationIntensity.setMaximum(100)
        self.entry_illuminationIntensity.setSingleStep(1)
        self.entry_illuminationIntensity.setValue(100)

        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setChecked(False)
        self.btn_live.setDefault(False)

        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(autolevel)

        # layout
        self.grid = QVBoxLayout()

        if show_trigger_options:
            grid_line0 = QHBoxLayout()
            grid_line0.addWidget(QLabel("Trigger Mode"))
            grid_line0.addWidget(self.dropdown_triggerManu)
            self.grid.addLayout(grid_line0)

            grid_line1 = QHBoxLayout()
            grid_line1.addWidget(QLabel("Trigger FPS"))
            grid_line1.addWidget(self.entry_triggerFPS)
            self.grid.addLayout(grid_line1)

        if show_display_options:
            grid_line15 = QHBoxLayout()
            grid_line15.addWidget(QLabel("Display FPS"))
            grid_line15.addWidget(self.entry_displayFPS)
            self.grid.addLayout(grid_line15)

        grid_line2 = QHBoxLayout()
        grid_line2.addWidget(QLabel("Microscope Configuration"))
        grid_line2.addWidget(self.dropdown_modeSelection, 3)
        self.grid.addLayout(grid_line2)

        grid_line3 = QHBoxLayout()
        grid_line3.addWidget(QLabel("Exposure Time (ms)"))
        grid_line3.addWidget(self.entry_exposureTime)
        grid_line3.addWidget(QLabel("Analog Gain"))
        grid_line3.addWidget(self.entry_analogGain)
        self.grid.addLayout(grid_line3)

        grid_line4 = QHBoxLayout()
        grid_line4.addWidget(QLabel("Illumination"))
        grid_line4.addWidget(self.slider_illuminationIntensity)
        grid_line4.addWidget(self.entry_illuminationIntensity)
        self.grid.addLayout(grid_line4)

        grid_line5 = QHBoxLayout()
        if show_autolevel:
            grid_line5.addWidget(self.btn_autolevel)
        grid_line5.addWidget(self.btn_live)
        self.grid.addLayout(grid_line5)

        if stretch:
            self.grid.addStretch()
        self.setLayout(self.grid)

        # connections
        self.dropdown_triggerManu.currentTextChanged.connect(
            lambda mode: event_bus.publish(SetTriggerModeCommand(mode=mode))
        )
        self.entry_triggerFPS.valueChanged.connect(
            lambda fps: event_bus.publish(SetTriggerFPSCommand(fps=fps))
        )
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)
        self.dropdown_modeSelection.currentTextChanged.connect(
            self.update_configuration
        )
        self.entry_exposureTime.valueChanged.connect(self.update_camera_exposure_time)
        self.entry_analogGain.valueChanged.connect(self.update_camera_analog_gain)
        self.slider_illuminationIntensity.valueChanged.connect(
            lambda x: self.entry_illuminationIntensity.setValue(x)
        )
        self.slider_illuminationIntensity.valueChanged.connect(
            self.update_illumination_intensity
        )
        self.entry_illuminationIntensity.valueChanged.connect(
            lambda x: self.slider_illuminationIntensity.setValue(int(x))
        )
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)
        self.btn_live.clicked.connect(self.toggle_live)

    def toggle_live(self, pressed: bool) -> None:
        self._log.info(f"toggle_live called with pressed={pressed}")
        if pressed:
            self.signal_live_configuration.emit(self.currentConfiguration)
            self.signal_start_live.emit()
            event_bus.publish(StartLiveCommand(configuration=self.currentConfiguration.name))
        else:
            self._log.info("Publishing StopLiveCommand")
            event_bus.publish(StopLiveCommand())

    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Handle live state changes from the event bus."""
        self._log.info(f"_on_live_state_changed: is_live={event.is_live}")
        if event.is_live:
            self.btn_live.setChecked(True)
            self.btn_live.setText("Stop Live")
        else:
            self.btn_live.setChecked(False)
            self.btn_live.setText("Start Live")

    def _on_trigger_mode_changed(self, event: TriggerModeChanged) -> None:
        """Handle trigger mode change from service."""
        self.dropdown_triggerManu.blockSignals(True)
        self.dropdown_triggerManu.setCurrentText(event.mode)
        self.dropdown_triggerManu.blockSignals(False)

    def _on_trigger_fps_changed(self, event: TriggerFPSChanged) -> None:
        """Handle trigger FPS change from service."""
        self.entry_triggerFPS.blockSignals(True)
        self.entry_triggerFPS.setValue(event.fps)
        self.entry_triggerFPS.blockSignals(False)

    def _on_microscope_mode_changed(self, event: MicroscopeModeChanged) -> None:
        """Handle microscope mode change from service."""
        self.dropdown_modeSelection.blockSignals(True)
        self.dropdown_modeSelection.setCurrentText(event.configuration_name)
        self.dropdown_modeSelection.blockSignals(False)

    def update_configuration(self, conf_name: str) -> None:
        self.is_switching_mode = True
        # identify the mode selected (note that mode id is 1 indexed)
        self.currentConfiguration = (
            self.channelConfigurationManager.get_channel_configuration_by_name(
                self.objectiveStore.current_objective, conf_name
            )
        )

        self._log.info(
            f"Mode changed to {self.currentConfiguration.name} ({self.currentConfiguration.illumination_source})"
        )
        self.update_ui_for_mode(self.currentConfiguration)
        self.signal_live_configuration.emit(self.currentConfiguration)
        event_bus.publish(
            SetMicroscopeModeCommand(
                configuration_name=self.currentConfiguration.name,
                objective=self.objectiveStore.current_objective,
            )
        )
        self.is_switching_mode = False

    def update_ui_for_mode(self, configuration: "ChannelMode") -> None:
        self.entry_exposureTime.blockSignals(True)
        self.entry_exposureTime.setValue(configuration.exposure_time)
        self.entry_exposureTime.blockSignals(False)

        self.entry_analogGain.blockSignals(True)
        self.entry_analogGain.setValue(configuration.analog_gain)
        self.entry_analogGain.blockSignals(False)

        self.slider_illuminationIntensity.blockSignals(True)
        self.slider_illuminationIntensity.setValue(
            int(configuration.illumination_intensity)
        )
        self.slider_illuminationIntensity.blockSignals(False)

        self.entry_illuminationIntensity.blockSignals(True)
        self.entry_illuminationIntensity.setValue(configuration.illumination_intensity)
        self.entry_illuminationIntensity.blockSignals(False)

    def update_camera_exposure_time(self, exposure_time: float) -> None:
        if not self.is_switching_mode:
            self.currentConfiguration.exposure_time = exposure_time
            self.liveController.set_microscope_mode(self.currentConfiguration)

    def update_camera_analog_gain(self, analog_gain: float) -> None:
        if not self.is_switching_mode:
            self.currentConfiguration.analog_gain = analog_gain
            self.liveController.set_microscope_mode(self.currentConfiguration)

    def update_illumination_intensity(self, intensity: float) -> None:
        if not self.is_switching_mode:
            self.currentConfiguration.illumination_intensity = intensity
            self.liveController.set_microscope_mode(self.currentConfiguration)

    def set_live_configuration(self, configuration: Optional["ChannelMode"]) -> None:
        if configuration is None:
            return
        self.dropdown_modeSelection.setCurrentText(configuration.name)

    def set_trigger_mode(self, trigger_mode: str) -> None:
        self.dropdown_triggerManu.setCurrentText(trigger_mode)
        event_bus.publish(SetTriggerModeCommand(mode=self.dropdown_triggerManu.currentText()))

    def refresh_mode_list(self) -> None:
        """Refresh the mode dropdown when profile changes."""
        current_text = self.dropdown_modeSelection.currentText()
        self.dropdown_modeSelection.clear()
        for (
            mode
        ) in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.dropdown_modeSelection.addItem(mode.name)
        # Try to restore the previous selection if it still exists
        index = self.dropdown_modeSelection.findText(current_text)
        if index >= 0:
            self.dropdown_modeSelection.setCurrentIndex(index)
        elif self.dropdown_modeSelection.count() > 0:
            self.dropdown_modeSelection.setCurrentIndex(0)

    def toggle_autolevel(self, enabled: bool) -> None:
        """Toggle autolevel on or off."""
        self.btn_autolevel.setChecked(enabled)

    def update_camera_settings(self) -> None:
        """Update UI to reflect current camera settings."""
        self.entry_exposureTime.blockSignals(True)
        self.entry_exposureTime.setValue(self.camera.get_exposure_time())
        self.entry_exposureTime.blockSignals(False)

        self.entry_analogGain.blockSignals(True)
        try:
            self.entry_analogGain.setValue(self.camera.get_analog_gain())
        except NotImplementedError:
            pass
        self.entry_analogGain.blockSignals(False)

    def select_new_microscope_mode_by_name(self, mode_name: str) -> None:
        """Select a microscope mode by name in the dropdown.

        If the mode doesn't exist in the current list, selects the first available mode.
        """
        index = self.dropdown_modeSelection.findText(mode_name)
        if index >= 0:
            self.dropdown_modeSelection.setCurrentIndex(index)
        elif self.dropdown_modeSelection.count() > 0:
            self.dropdown_modeSelection.setCurrentIndex(0)
