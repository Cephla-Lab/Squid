# Camera-related widgets
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import squid.logging
from squid.events import event_bus, ExposureTimeChanged, AnalogGainChanged
from qtpy.QtCore import Signal, Qt

if TYPE_CHECKING:
    from squid.services import CameraService
from qtpy.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
    QSlider,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
)
from qtpy.QtGui import QIcon

from control._def import (
    TriggerMode,
    CAMERA_CONFIG,
    DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS,
    DEFAULT_SAVING_PATH,
)
import control.utils as utils
from squid.abc import AbstractCamera
from squid.config import CameraPixelFormat


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
        # TODO(Task 5): Remove self.camera - route all calls through service
        self.camera = camera_service._camera

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
        self.entry_exposureTime.setMinimum(self.camera.get_exposure_limits()[0])
        self.entry_exposureTime.setMaximum(self.camera.get_exposure_limits()[1])
        self.entry_exposureTime.setSingleStep(1)
        default_exposure = 20.0
        self.entry_exposureTime.setValue(default_exposure)
        self._service.set_exposure_time(default_exposure)

        self.entry_analogGain = QDoubleSpinBox()
        try:
            gain_range = self.camera.get_gain_range()
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
            pixel_formats = self.camera.get_available_pixel_formats()
            pixel_formats = [pf.name for pf in pixel_formats]
        except NotImplementedError:
            pixel_formats = ["MONO8", "MONO12", "MONO14", "MONO16", "BAYER_RG8", "BAYER_RG12"]
        self.dropdown_pixelFormat.addItems(pixel_formats)
        if self.camera.get_pixel_format() is not None:
            self.dropdown_pixelFormat.setCurrentText(self.camera.get_pixel_format().name)
        else:
            print("setting camera's default pixel format")
            self._service.set_pixel_format(CameraPixelFormat.from_string(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT))
            self.dropdown_pixelFormat.setCurrentText(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT)
        self.dropdown_pixelFormat.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed))
        # to do: load and save pixel format in configurations

        self.entry_ROI_offset_x = QSpinBox()
        roi_info = self.camera.get_region_of_interest()
        (max_x, max_y) = self.camera.get_resolution()
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
            current_binning = self.camera.get_binning()
            current_binning_string = "x".join([str(current_binning[0]), str(current_binning[1])])
            binning_options = [f"{binning[0]}x{binning[1]}" for binning in self.camera.get_binning_options()]
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

        if include_camera_auto_wb_setting and CameraPixelFormat.is_color_format(self.camera.get_pixel_format()):
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
        offset_x = (self.camera.get_resolution()[0] - self.entry_ROI_width.value()) / 2
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
        offset_y = (self.camera.get_resolution()[1] - self.entry_ROI_height.value()) / 2
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

        (x_offset, y_offset, width, height) = self.camera.get_region_of_interest()
        (x_max, y_max) = self.camera.get_resolution()
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


class LiveControlWidget(QFrame):

    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_autoLevelSetting = Signal(bool)
    signal_live_configuration = Signal(object)
    signal_start_live = Signal()

    def __init__(
        self,
        streamHandler,
        liveController,
        objectiveStore,
        channelConfigurationManager,
        show_trigger_options=True,
        show_display_options=False,
        show_autolevel=False,
        autolevel=False,
        stretch=True,
        main=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.liveController = liveController
        self.camera = self.liveController.microscope.camera
        self.streamHandler = streamHandler
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.fps_trigger = 10
        self.fps_display = 10
        self.liveController.set_trigger_fps(self.fps_trigger)
        self.streamHandler.set_display_fps(self.fps_display)

        self.currentConfiguration = self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        )[0]

        self.add_components(show_trigger_options, show_display_options, show_autolevel, autolevel, stretch)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.liveController.set_microscope_mode(self.currentConfiguration)
        self.update_ui_for_mode(self.currentConfiguration)

        self.is_switching_mode = False  # flag used to prevent from settings being set by twice - from both mode change slot and value change slot; another way is to use blockSignals(True)

    def add_components(self, show_trigger_options, show_display_options, show_autolevel, autolevel, stretch):
        # line 0: trigger mode
        self.dropdown_triggerManu = QComboBox()
        self.dropdown_triggerManu.addItems([TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS])
        self.dropdown_triggerManu.setCurrentText(self.camera.get_acquisition_mode().value)
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
        for mode in self.channelConfigurationManager.get_channel_configurations_for_objective(
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
            self._log.info("Camera does not support analog gain, disabling analog gain control.")
            self.entry_analogGain.setValue(0)
            self.entry_analogGain.setEnabled(False)

        self.slider_illuminationIntensity = QSlider(Qt.Horizontal)
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
        self.dropdown_triggerManu.currentTextChanged.connect(self.liveController.set_trigger_mode)
        self.entry_triggerFPS.valueChanged.connect(self.liveController.set_trigger_fps)
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)
        self.dropdown_modeSelection.currentTextChanged.connect(self.update_configuration)
        self.entry_exposureTime.valueChanged.connect(self.update_camera_exposure_time)
        self.entry_analogGain.valueChanged.connect(self.update_camera_analog_gain)
        self.slider_illuminationIntensity.valueChanged.connect(
            lambda x: self.entry_illuminationIntensity.setValue(x)
        )
        self.slider_illuminationIntensity.valueChanged.connect(self.update_illumination_intensity)
        self.entry_illuminationIntensity.valueChanged.connect(
            lambda x: self.slider_illuminationIntensity.setValue(int(x))
        )
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)
        self.btn_live.clicked.connect(self.toggle_live)

    def toggle_live(self, pressed):
        if pressed:
            self.signal_live_configuration.emit(self.currentConfiguration)
            self.signal_start_live.emit()
            self.btn_live.setText("Stop Live")
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")

    def update_configuration(self, conf_name):
        self.is_switching_mode = True
        # identify the mode selected (note that mode id is 1 indexed)
        self.currentConfiguration = self.channelConfigurationManager.get_channel_configuration_by_name(
            self.objectiveStore.current_objective, conf_name
        )

        self._log.info(f"Mode changed to {self.currentConfiguration.name} ({self.currentConfiguration.illumination_source})")
        self.update_ui_for_mode(self.currentConfiguration)
        self.signal_live_configuration.emit(self.currentConfiguration)
        self.liveController.set_microscope_mode(self.currentConfiguration)
        self.is_switching_mode = False

    def update_ui_for_mode(self, configuration):
        self.entry_exposureTime.blockSignals(True)
        self.entry_exposureTime.setValue(configuration.exposure_time)
        self.entry_exposureTime.blockSignals(False)

        self.entry_analogGain.blockSignals(True)
        self.entry_analogGain.setValue(configuration.analog_gain)
        self.entry_analogGain.blockSignals(False)

        self.slider_illuminationIntensity.blockSignals(True)
        self.slider_illuminationIntensity.setValue(int(configuration.illumination_intensity))
        self.slider_illuminationIntensity.blockSignals(False)

        self.entry_illuminationIntensity.blockSignals(True)
        self.entry_illuminationIntensity.setValue(configuration.illumination_intensity)
        self.entry_illuminationIntensity.blockSignals(False)

    def update_camera_exposure_time(self, exposure_time):
        if not self.is_switching_mode:
            self.currentConfiguration.exposure_time = exposure_time
            self.liveController.set_microscope_mode(self.currentConfiguration)

    def update_camera_analog_gain(self, analog_gain):
        if not self.is_switching_mode:
            self.currentConfiguration.analog_gain = analog_gain
            self.liveController.set_microscope_mode(self.currentConfiguration)

    def update_illumination_intensity(self, intensity):
        if not self.is_switching_mode:
            self.currentConfiguration.illumination_intensity = intensity
            self.liveController.set_microscope_mode(self.currentConfiguration)

    def set_live_configuration(self, configuration):
        if configuration is None:
            return
        self.dropdown_modeSelection.setCurrentText(configuration.name)

    def set_trigger_mode(self, trigger_mode):
        self.dropdown_triggerManu.setCurrentText(trigger_mode)
        self.liveController.set_trigger_mode(self.dropdown_triggerManu.currentText())

    def refresh_mode_list(self):
        """Refresh the mode dropdown when profile changes."""
        current_text = self.dropdown_modeSelection.currentText()
        self.dropdown_modeSelection.clear()
        for mode in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.dropdown_modeSelection.addItem(mode.name)
        # Try to restore the previous selection if it still exists
        index = self.dropdown_modeSelection.findText(current_text)
        if index >= 0:
            self.dropdown_modeSelection.setCurrentIndex(index)
        elif self.dropdown_modeSelection.count() > 0:
            self.dropdown_modeSelection.setCurrentIndex(0)

    def update_camera_settings(self):
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


class RecordingWidget(QFrame):
    def __init__(self, streamHandler, imageSaver, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imageSaver = imageSaver  # for saving path control
        self.streamHandler = streamHandler
        self.base_path_is_set = False
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.imageSaver.set_base_path(DEFAULT_SAVING_PATH)

        self.lineEdit_experimentID = QLineEdit()

        self.entry_saveFPS = QDoubleSpinBox()
        self.entry_saveFPS.setKeyboardTracking(False)
        self.entry_saveFPS.setMinimum(0.02)
        self.entry_saveFPS.setMaximum(1000)
        self.entry_saveFPS.setSingleStep(1)
        self.entry_saveFPS.setValue(1)
        self.streamHandler.set_save_fps(1)

        self.entry_timeLimit = QSpinBox()
        self.entry_timeLimit.setKeyboardTracking(False)
        self.entry_timeLimit.setMinimum(-1)
        self.entry_timeLimit.setMaximum(60 * 60 * 24 * 30)
        self.entry_timeLimit.setSingleStep(1)
        self.entry_timeLimit.setValue(-1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setCheckable(True)
        self.btn_record.setChecked(False)
        self.btn_record.setDefault(False)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("Saving Path"))
        grid_line1.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line1.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line2 = QGridLayout()
        grid_line2.addWidget(QLabel("Experiment ID"), 0, 0)
        grid_line2.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line3 = QGridLayout()
        grid_line3.addWidget(QLabel("Saving FPS"), 0, 0)
        grid_line3.addWidget(self.entry_saveFPS, 0, 1)
        grid_line3.addWidget(QLabel("Time Limit (s)"), 0, 2)
        grid_line3.addWidget(self.entry_timeLimit, 0, 3)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line2)
        self.grid.addLayout(grid_line3)
        self.grid.addWidget(self.btn_record)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_record.clicked.connect(self.toggle_recording)
        self.entry_saveFPS.valueChanged.connect(self.streamHandler.set_save_fps)
        self.entry_timeLimit.valueChanged.connect(self.imageSaver.set_recording_time_limit)
        self.imageSaver.stop_recording.connect(self.stop_recording)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.imageSaver.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_recording(self, pressed):
        if self.base_path_is_set == False:
            self.btn_record.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            self.lineEdit_experimentID.setEnabled(False)
            self.btn_setSavingDir.setEnabled(False)
            self.imageSaver.start_new_experiment(self.lineEdit_experimentID.text())
            self.streamHandler.start_recording()
        else:
            self.streamHandler.stop_recording()
            self.lineEdit_experimentID.setEnabled(True)
            self.btn_setSavingDir.setEnabled(True)

    # stop_recording can be called by imageSaver
    def stop_recording(self):
        self.lineEdit_experimentID.setEnabled(True)
        self.btn_record.setChecked(False)
        self.streamHandler.stop_recording()
        self.btn_setSavingDir.setEnabled(True)


class MultiCameraRecordingWidget(QFrame):
    def __init__(self, streamHandler, imageSaver, channels, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imageSaver = imageSaver  # for saving path control
        self.streamHandler = streamHandler
        self.channels = channels
        self.base_path_is_set = False
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_experimentID = QLineEdit()

        self.entry_saveFPS = QDoubleSpinBox()
        self.entry_saveFPS.setKeyboardTracking(False)
        self.entry_saveFPS.setMinimum(0.02)
        self.entry_saveFPS.setMaximum(1000)
        self.entry_saveFPS.setSingleStep(1)
        self.entry_saveFPS.setValue(1)
        for channel in self.channels:
            self.streamHandler[channel].set_save_fps(1)

        self.entry_timeLimit = QSpinBox()
        self.entry_timeLimit.setKeyboardTracking(False)
        self.entry_timeLimit.setMinimum(-1)
        self.entry_timeLimit.setMaximum(60 * 60 * 24 * 30)
        self.entry_timeLimit.setSingleStep(1)
        self.entry_timeLimit.setValue(-1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setCheckable(True)
        self.btn_record.setChecked(False)
        self.btn_record.setDefault(False)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("Saving Path"))
        grid_line1.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line1.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line2 = QGridLayout()
        grid_line2.addWidget(QLabel("Experiment ID"), 0, 0)
        grid_line2.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line3 = QGridLayout()
        grid_line3.addWidget(QLabel("Saving FPS"), 0, 0)
        grid_line3.addWidget(self.entry_saveFPS, 0, 1)
        grid_line3.addWidget(QLabel("Time Limit (s)"), 0, 2)
        grid_line3.addWidget(self.entry_timeLimit, 0, 3)
        grid_line3.addWidget(self.btn_record, 0, 4)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line1, 0, 0)
        self.grid.addLayout(grid_line2, 1, 0)
        self.grid.addLayout(grid_line3, 2, 0)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_record.clicked.connect(self.toggle_recording)
        for channel in self.channels:
            self.entry_saveFPS.valueChanged.connect(self.streamHandler[channel].set_save_fps)
            self.entry_timeLimit.valueChanged.connect(self.imageSaver[channel].set_recording_time_limit)
            self.imageSaver[channel].stop_recording.connect(self.stop_recording)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        for channel in self.channels:
            self.imageSaver[channel].set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.save_dir_base = save_dir_base
        self.base_path_is_set = True

    def toggle_recording(self, pressed):
        if self.base_path_is_set == False:
            self.btn_record.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            self.lineEdit_experimentID.setEnabled(False)
            self.btn_setSavingDir.setEnabled(False)
            experiment_ID = self.lineEdit_experimentID.text()
            experiment_ID = experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
            utils.ensure_directory_exists(os.path.join(self.save_dir_base, experiment_ID))
            for channel in self.channels:
                self.imageSaver[channel].start_new_experiment(os.path.join(experiment_ID, channel), add_timestamp=False)
                self.streamHandler[channel].start_recording()
        else:
            for channel in self.channels:
                self.streamHandler[channel].stop_recording()
            self.lineEdit_experimentID.setEnabled(True)
            self.btn_setSavingDir.setEnabled(True)

    # stop_recording can be called by imageSaver
    def stop_recording(self):
        self.lineEdit_experimentID.setEnabled(True)
        self.btn_record.setChecked(False)
        for channel in self.channels:
            self.streamHandler[channel].stop_recording()
        self.btn_setSavingDir.setEnabled(True)
