# Napari live view widget
import numpy as np
from typing import TYPE_CHECKING, List, Optional

from squid.backend.managers import ContrastManager
from squid.backend.io.stream_handler import StreamHandler
import squid.core.logging
import pyqtgraph as pg
import napari
from napari.layers import Layer
from napari.utils.events import Event

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDoubleSpinBox,
    QComboBox,
    QPushButton,
    QSlider,
    QSpacerItem,
    QSizePolicy,
    QDockWidget,
)

from _def import (
    TriggerMode,
    USE_NAPARI_FOR_LIVE_CONTROL,
    USE_NAPARI_WELL_SELECTION,
)

from squid.core.utils.config_utils import ChannelMode as ChannelConfiguration

from squid.core.events import (
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
    SetTriggerFPSCommand,
    SetMicroscopeModeCommand,
    UpdateIlluminationCommand,
    SetDisplayResolutionScalingCommand,
    TriggerFPSChanged,
    MicroscopeModeChanged,
    ObjectiveChanged,
    ChannelConfigurationsChanged,
    UpdateChannelConfigurationCommand,
    AutoLevelCommand,
    ImageCoordinateClickedCommand,
)

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


class NapariLiveWidget(QWidget):

    def __init__(
        self,
        event_bus: "UIEventBus",
        streamHandler: StreamHandler,
        contrastManager: ContrastManager,
        exposure_limits: tuple[float, float],
        initial_configuration: ChannelConfiguration,
        initial_objective: str,
        initial_channel_configs: List[str],
        wellSelectionWidget: QWidget = None,
        show_trigger_options: bool = True,
        show_display_options: bool = True,
        show_autolevel: bool = False,
        autolevel: bool = False,
        parent: QWidget = None,
    ):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        self.streamHandler = streamHandler
        self.wellSelectionWidget = wellSelectionWidget
        self._exposure_limits = exposure_limits
        self.live_configuration = initial_configuration

        # Cached state from events
        self._current_objective = initial_objective
        self._channel_config_names = list(initial_channel_configs)

        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels: set[str] = set()
        self.init_live = False
        self.init_live_rgb = False
        self.init_scale = False
        self.previous_scale = None
        self.previous_center = None
        self.last_was_autofocus = False
        self.fps_trigger = 10
        self.fps_display = 10
        self.contrastManager = contrastManager

        self.initNapariViewer()
        self.addNapariGrayclipColormap()
        self.initControlWidgets(
            show_trigger_options, show_display_options, show_autolevel, autolevel
        )
        self.update_ui_for_mode(self.live_configuration)

        # Subscribe to state changes from the bus
        self._event_bus.subscribe(LiveStateChanged, self._on_live_state_changed)
        self._event_bus.subscribe(TriggerFPSChanged, self._on_trigger_fps_changed)
        self._event_bus.subscribe(MicroscopeModeChanged, self._on_microscope_mode_changed)
        self._event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)
        self._event_bus.subscribe(ChannelConfigurationsChanged, self._on_channel_configs_changed)
        self._event_bus.subscribe(AutoLevelCommand, self._on_autolevel_command)

    def _on_autolevel_command(self, event: AutoLevelCommand) -> None:
        self.btn_autolevel.blockSignals(True)
        self.btn_autolevel.setChecked(bool(event.enabled))
        self.btn_autolevel.blockSignals(False)

    def initNapariViewer(self) -> None:
        self.viewer = napari.Viewer(show=False)
        self.viewerWidget = self.viewer.window._qt_window
        self.viewer.dims.axis_labels = ["Y-axis", "X-axis"]
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewerWidget)
        self.setLayout(self.layout)
        self.customizeViewer()

    def customizeViewer(self) -> None:
        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def updateHistogram(self, layer: napari.layers.Image) -> None:
        if self.histogram_widget is not None and layer.data is not None:
            self.pg_image_item.setImage(layer.data, autoLevels=False)
            self.histogram_widget.setLevels(*layer.contrast_limits)
            self.histogram_widget.setHistogramRange(layer.data.min(), layer.data.max())

            # Set the histogram widget's region to match the layer's contrast limits
            self.histogram_widget.region.setRegion(layer.contrast_limits)

            # Update colormap only if it has changed
            if (
                hasattr(self, "last_colormap")
                and self.last_colormap != layer.colormap.name
            ):
                self.histogram_widget.gradient.setColorMap(
                    self.createColorMap(layer.colormap)
                )
            self.last_colormap = layer.colormap.name

    def createColorMap(self, colormap: napari.utils.colormaps.Colormap) -> pg.ColorMap:
        colors = colormap.colors
        positions = np.linspace(0, 1, len(colors))
        return pg.ColorMap(positions, colors)

    def initControlWidgets(
        self,
        show_trigger_options: bool,
        show_display_options: bool,
        show_autolevel: bool,
        autolevel: bool,
    ) -> None:
        # Initialize histogram widget
        self.pg_image_item = pg.ImageItem()
        self.histogram_widget = pg.HistogramLUTWidget(image=self.pg_image_item)
        self.histogram_widget.setFixedWidth(100)
        self.histogram_dock = self.viewer.window.add_dock_widget(
            self.histogram_widget, area="right", name="hist"
        )
        self.histogram_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.histogram_dock.setTitleBarWidget(QWidget())
        self.histogram_widget.region.sigRegionChanged.connect(
            self.on_histogram_region_changed
        )
        self.histogram_widget.region.sigRegionChangeFinished.connect(
            self.on_histogram_region_changed
        )

        # Microscope Configuration
        self.dropdown_modeSelection = QComboBox()
        for config_name in self._channel_config_names:
            self.dropdown_modeSelection.addItem(config_name)
        self.dropdown_modeSelection.setCurrentText(self.live_configuration.name)
        self.dropdown_modeSelection.activated.connect(self.select_new_microscope_mode_by_name)

        # Live button
        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        gradient_style = """
            QPushButton {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #D6D6FF, stop:1 #C2C2FF);
                border-radius: 5px;
                color: black;
                border: 1px solid #A0A0A0;
            }
            QPushButton:checked {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #FFD6D6, stop:1 #FFC2C2);
                border: 1px solid #A0A0A0;
            }
            QPushButton:hover {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #E0E0FF, stop:1 #D0D0FF);
            }
            QPushButton:pressed {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #9090C0, stop:1 #8080B0);
            }
        """
        self.btn_live.setStyleSheet(gradient_style)
        current_height = self.btn_live.sizeHint().height()
        self.btn_live.setFixedHeight(int(current_height * 1.5))
        self.btn_live.clicked.connect(self.toggle_live)

        # Exposure Time
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setRange(*self._exposure_limits)
        self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
        self.entry_exposureTime.setSuffix(" ms")
        self.entry_exposureTime.valueChanged.connect(self.update_config_exposure_time)

        # Analog Gain
        self.entry_analogGain = QDoubleSpinBox()
        self.entry_analogGain.setRange(0, 24)
        self.entry_analogGain.setSingleStep(0.1)
        self.entry_analogGain.setValue(self.live_configuration.analog_gain)
        self.entry_analogGain.valueChanged.connect(self.update_config_analog_gain)

        # Illumination Intensity
        self.slider_illuminationIntensity = QSlider(Qt.Horizontal)
        self.slider_illuminationIntensity.setRange(0, 100)
        self.slider_illuminationIntensity.setValue(
            int(self.live_configuration.illumination_intensity)
        )
        self.slider_illuminationIntensity.setTickPosition(QSlider.TicksBelow)
        self.slider_illuminationIntensity.setTickInterval(10)
        self.slider_illuminationIntensity.valueChanged.connect(
            self.update_config_illumination_intensity
        )
        self.label_illuminationIntensity = QLabel(
            str(self.slider_illuminationIntensity.value()) + "%"
        )
        self.slider_illuminationIntensity.valueChanged.connect(
            lambda v: self.label_illuminationIntensity.setText(str(v) + "%")
        )

        # Trigger mode
        self.dropdown_triggerMode = QComboBox()
        trigger_modes = [
            ("Software", TriggerMode.SOFTWARE),
            ("Hardware", TriggerMode.HARDWARE),
            ("Continuous", TriggerMode.CONTINUOUS),
        ]
        for display_name, mode in trigger_modes:
            self.dropdown_triggerMode.addItem(display_name, mode)
        self.dropdown_triggerMode.currentIndexChanged.connect(
            self.on_trigger_mode_changed
        )

        # Trigger FPS
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setRange(0.02, 1000)
        self.entry_triggerFPS.setValue(self.fps_trigger)
        self.entry_triggerFPS.valueChanged.connect(
            lambda fps: self._event_bus.publish(SetTriggerFPSCommand(fps=fps))
        )

        # Display FPS
        self.entry_displayFPS = QDoubleSpinBox()
        self.entry_displayFPS.setRange(1, 240)
        self.entry_displayFPS.setValue(self.fps_display)
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)

        # Resolution Scaling
        self.slider_resolutionScaling = QSlider(Qt.Horizontal)
        self.slider_resolutionScaling.setRange(10, 100)
        self.slider_resolutionScaling.setValue(100)
        self.slider_resolutionScaling.setTickPosition(QSlider.TicksBelow)
        self.slider_resolutionScaling.setTickInterval(10)
        self.slider_resolutionScaling.valueChanged.connect(
            self.update_resolution_scaling
        )
        self.label_resolutionScaling = QLabel(
            str(self.slider_resolutionScaling.value()) + "%"
        )
        self.slider_resolutionScaling.valueChanged.connect(
            lambda v: self.label_resolutionScaling.setText(str(v) + "%")
        )

        # Autolevel
        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(autolevel)
        self.btn_autolevel.toggled.connect(
            lambda enabled: self._event_bus.publish(AutoLevelCommand(enabled=enabled))
        )

        def make_row(label_widget, entry_widget, value_label=None):
            row = QHBoxLayout()
            row.addWidget(label_widget)
            row.addWidget(entry_widget)
            if value_label:
                row.addWidget(value_label)
            return row

        control_layout = QVBoxLayout()

        # Add widgets to layout
        control_layout.addWidget(self.dropdown_modeSelection)
        control_layout.addWidget(self.btn_live)
        control_layout.addSpacerItem(
            QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )

        row1 = make_row(QLabel("Exposure Time"), self.entry_exposureTime)
        control_layout.addLayout(row1)

        row2 = make_row(
            QLabel("Illumination"),
            self.slider_illuminationIntensity,
            self.label_illuminationIntensity,
        )
        control_layout.addLayout(row2)

        row3 = make_row((QLabel("Analog Gain")), self.entry_analogGain)
        control_layout.addLayout(row3)
        control_layout.addSpacerItem(
            QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )

        if show_trigger_options:
            row0 = make_row(QLabel("Trigger Mode"), self.dropdown_triggerMode)
            control_layout.addLayout(row0)
            row00 = make_row(QLabel("Trigger FPS"), self.entry_triggerFPS)
            control_layout.addLayout(row00)
            control_layout.addSpacerItem(
                QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding)
            )

        if show_display_options:
            row4 = make_row((QLabel("Display FPS")), self.entry_displayFPS)
            control_layout.addLayout(row4)
            row5 = make_row(
                QLabel("Display Resolution"),
                self.slider_resolutionScaling,
                self.label_resolutionScaling,
            )
            control_layout.addLayout(row5)
            control_layout.addSpacerItem(
                QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding)
            )

        if show_autolevel:
            control_layout.addWidget(self.btn_autolevel)
            control_layout.addSpacerItem(
                QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding)
            )

        control_layout.addStretch(1)

        add_live_controls = False
        if USE_NAPARI_FOR_LIVE_CONTROL or add_live_controls:
            live_controls_widget = QWidget()
            live_controls_widget.setLayout(control_layout)

            layer_controls_widget = (
                self.viewer.window._qt_viewer.dockLayerControls.widget()
            )
            layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

            self.viewer.window._qt_viewer.layerButtons.hide()
            self.viewer.window.remove_dock_widget(
                self.viewer.window._qt_viewer.dockLayerControls
            )
            self.viewer.window.remove_dock_widget(
                self.viewer.window._qt_viewer.dockLayerList
            )

            # Add the actual dock widgets
            self.dock_layer_controls = self.viewer.window.add_dock_widget(
                layer_controls_widget, area="left", name="layer controls", tabify=True
            )
            self.dock_layer_list = self.viewer.window.add_dock_widget(
                layer_list_widget, area="left", name="layer list", tabify=True
            )
            self.dock_live_controls = self.viewer.window.add_dock_widget(
                live_controls_widget, area="left", name="live controls", tabify=True
            )

            self.viewer.window.window_menu.addAction(
                self.dock_live_controls.toggleViewAction()
            )

        if USE_NAPARI_WELL_SELECTION:
            well_selector_layout = QVBoxLayout()

            well_selector_row = QHBoxLayout()
            well_selector_row.addStretch(1)
            well_selector_row.addWidget(self.wellSelectionWidget)
            well_selector_row.addStretch(1)
            well_selector_layout.addLayout(well_selector_row)
            well_selector_layout.addStretch()

            well_selector_dock_widget = QWidget()
            well_selector_dock_widget.setLayout(well_selector_layout)
            self.dock_well_selector = self.viewer.window.add_dock_widget(
                well_selector_dock_widget, area="bottom", name="well selector"
            )
            self.dock_well_selector.setFixedHeight(
                self.dock_well_selector.minimumSizeHint().height()
            )

        layer_controls_widget = self.viewer.window._qt_viewer.dockLayerControls.widget()
        layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

        self.viewer.window._qt_viewer.layerButtons.hide()
        self.viewer.window.remove_dock_widget(
            self.viewer.window._qt_viewer.dockLayerControls
        )
        self.viewer.window.remove_dock_widget(
            self.viewer.window._qt_viewer.dockLayerList
        )
        self.print_window_menu_items()

    def print_window_menu_items(self) -> None:
        print("Items in window_menu:")
        for action in self.viewer.window.window_menu.actions():
            print(action.text())

    def on_histogram_region_changed(self) -> None:
        if self.live_configuration.name:
            min_val, max_val = self.histogram_widget.region.getRegion()
            self.updateContrastLimits(self.live_configuration.name, min_val, max_val)

    def toggle_live(self, pressed: bool) -> None:
        if pressed:
            self._event_bus.publish(StartLiveCommand(configuration=self.live_configuration.name))
        else:
            self._event_bus.publish(StopLiveCommand())

    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Handle live state changes from the event bus."""
        if getattr(event, "camera", "main") != "main":
            return
        if event.is_live:
            self.btn_live.setChecked(True)
            self.btn_live.setText("Stop Live")
        else:
            self.btn_live.setChecked(False)
            self.btn_live.setText("Start Live")

    def _on_trigger_fps_changed(self, event: TriggerFPSChanged) -> None:
        """Handle trigger FPS change from service."""
        if getattr(event, "camera", "main") != "main":
            return
        self.entry_triggerFPS.blockSignals(True)
        self.entry_triggerFPS.setValue(event.fps)
        self.entry_triggerFPS.blockSignals(False)

    def _on_microscope_mode_changed(self, event: MicroscopeModeChanged) -> None:
        """Handle microscope mode change from service."""
        self.dropdown_modeSelection.blockSignals(True)
        self.dropdown_modeSelection.setCurrentText(event.configuration_name)
        self.dropdown_modeSelection.blockSignals(False)

        # Update live_configuration with values from the event
        self.live_configuration.name = event.configuration_name
        if event.exposure_time_ms is not None:
            self.live_configuration.exposure_time = event.exposure_time_ms
            self.entry_exposureTime.blockSignals(True)
            self.entry_exposureTime.setValue(event.exposure_time_ms)
            self.entry_exposureTime.blockSignals(False)
        if event.analog_gain is not None:
            self.live_configuration.analog_gain = event.analog_gain
            self.entry_analogGain.blockSignals(True)
            self.entry_analogGain.setValue(event.analog_gain)
            self.entry_analogGain.blockSignals(False)
        if event.illumination_intensity is not None:
            self.live_configuration.illumination_intensity = event.illumination_intensity
            self.slider_illuminationIntensity.blockSignals(True)
            self.slider_illuminationIntensity.setValue(int(event.illumination_intensity))
            self.slider_illuminationIntensity.blockSignals(False)

    def _on_objective_changed(self, event: ObjectiveChanged) -> None:
        """Handle objective change event."""
        if event.objective_name:
            self._current_objective = event.objective_name
            # Re-apply current configuration for the new objective.
            self._event_bus.publish(
                SetMicroscopeModeCommand(
                    configuration_name=self.live_configuration.name,
                    objective=self._current_objective,
                )
            )

    def _on_channel_configs_changed(self, event: ChannelConfigurationsChanged) -> None:
        """Handle channel configurations changed event."""
        if event.objective_name == self._current_objective:
            self._channel_config_names = list(event.configuration_names)
            # Update dropdown
            current_selection = self.dropdown_modeSelection.currentText()
            self.dropdown_modeSelection.blockSignals(True)
            self.dropdown_modeSelection.clear()
            self.dropdown_modeSelection.addItems(self._channel_config_names)
            # Try to restore selection
            if current_selection in self._channel_config_names:
                self.dropdown_modeSelection.setCurrentText(current_selection)
            elif self._channel_config_names:
                self.dropdown_modeSelection.setCurrentIndex(0)
            self.dropdown_modeSelection.blockSignals(False)

    def toggle_live_controls(self, show: bool) -> None:
        if show:
            self.dock_live_controls.show()
        else:
            self.dock_live_controls.hide()

    def toggle_well_selector(self, show: bool) -> None:
        if show:
            self.dock_well_selector.show()
        else:
            self.dock_well_selector.hide()

    def replace_well_selector(self, wellSelector: QWidget) -> None:
        self.viewer.window.remove_dock_widget(self.dock_well_selector)
        self.wellSelectionWidget = wellSelector
        well_selector_layout = QHBoxLayout()
        well_selector_layout.addStretch(1)
        well_selector_layout.addWidget(self.wellSelectionWidget)
        well_selector_layout.addStretch(1)
        well_selector_dock_widget = QWidget()
        well_selector_dock_widget.setLayout(well_selector_layout)
        self.dock_well_selector = self.viewer.window.add_dock_widget(
            well_selector_dock_widget, area="bottom", name="well selector", tabify=True
        )

    def select_new_microscope_mode_by_name(self, config_index: int) -> None:
        config_name = self.dropdown_modeSelection.itemText(config_index)

        if config_name not in self._channel_config_names:
            self._log.error(
                f"User attempted to select config named '{config_name}' but it does not exist!"
            )
            return

        # Publish command - MicroscopeModeController will handle the actual mode change
        # and publish MicroscopeModeChanged with the config details
        self._event_bus.publish(
            SetMicroscopeModeCommand(
                configuration_name=config_name,
                objective=self._current_objective,
            )
        )
        # UI will be updated via _on_microscope_mode_changed event handler

    def update_ui_for_mode(self, config: ChannelConfiguration) -> None:
        self.live_configuration = config
        self.dropdown_modeSelection.setCurrentText(config.name if config else "Unknown")
        if self.live_configuration:
            self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
            self.entry_analogGain.setValue(self.live_configuration.analog_gain)
            self.slider_illuminationIntensity.setValue(
                int(self.live_configuration.illumination_intensity)
            )

    def update_config_exposure_time(self, new_value: float) -> None:
        self.live_configuration.exposure_time = new_value
        self._event_bus.publish(UpdateChannelConfigurationCommand(
            objective_name=self._current_objective,
            config_name=self.live_configuration.name,
            exposure_time_ms=new_value,
        ))
        self._event_bus.publish(
            SetMicroscopeModeCommand(
                configuration_name=self.live_configuration.name,
                objective=self._current_objective,
            )
        )

    def update_config_analog_gain(self, new_value: float) -> None:
        self.live_configuration.analog_gain = new_value
        self._event_bus.publish(UpdateChannelConfigurationCommand(
            objective_name=self._current_objective,
            config_name=self.live_configuration.name,
            analog_gain=new_value,
        ))
        self._event_bus.publish(
            SetMicroscopeModeCommand(
                configuration_name=self.live_configuration.name,
                objective=self._current_objective,
            )
        )

    def update_config_illumination_intensity(self, new_value: float) -> None:
        self.live_configuration.illumination_intensity = new_value
        self._event_bus.publish(UpdateChannelConfigurationCommand(
            objective_name=self._current_objective,
            config_name=self.live_configuration.name,
            illumination_intensity=new_value,
        ))
        self._event_bus.publish(UpdateIlluminationCommand())

    def update_resolution_scaling(self, value: float) -> None:
        self.streamHandler.set_display_resolution_scaling(value)
        self._event_bus.publish(SetDisplayResolutionScalingCommand(scaling=value))

    def on_trigger_mode_changed(self, index: int) -> None:
        # Get the actual value using user data
        actual_value = self.dropdown_triggerMode.itemData(index)
        print(
            f"Selected: {self.dropdown_triggerMode.currentText()} (actual value: {actual_value})"
        )

    def addNapariGrayclipColormap(self) -> None:
        if hasattr(napari.utils.colormaps.AVAILABLE_COLORMAPS, "grayclip"):
            return
        grayclip = []
        for i in range(255):
            grayclip.append([i / 255, i / 255, i / 255])
        grayclip.append([1, 0, 0])
        napari.utils.colormaps.AVAILABLE_COLORMAPS["grayclip"] = napari.utils.Colormap(
            name="grayclip", colors=grayclip
        )

    def initLiveLayer(
        self,
        channel: str,
        image_height: int,
        image_width: int,
        image_dtype: np.dtype,
        rgb: bool = False,
    ) -> None:
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        self.viewer.layers.clear()
        self.image_width = image_width
        self.image_height = image_height
        if self.dtype != np.dtype(image_dtype):
            self.contrastManager.scale_contrast_limits(
                np.dtype(image_dtype)
            )  # Fix This to scale existing contrast limits to new dtype range
            self.dtype = image_dtype

        self.channels.add(channel)
        self.live_configuration.name = channel

        if rgb:
            canvas = np.zeros((image_height, image_width, 3), dtype=self.dtype)
        else:
            canvas = np.zeros((image_height, image_width), dtype=self.dtype)
        limits = self.getContrastLimits(self.dtype)
        layer = self.viewer.add_image(
            canvas,
            name="Live View",
            visible=True,
            rgb=rgb,
            colormap="grayclip",
            contrast_limits=limits,
            blending="additive",
        )
        layer.contrast_limits = self.contrastManager.get_limits(
            self.live_configuration.name, self.dtype
        )
        layer.mouse_double_click_callbacks.append(self.onDoubleClick)
        layer.events.contrast_limits.connect(self.signalContrastLimits)
        self.updateHistogram(layer)

        if not self.init_scale:
            self.resetView()
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
        else:
            self.viewer.camera.zoom = self.previous_scale
            self.viewer.camera.center = self.previous_center

    def updateLiveLayer(self, image: np.ndarray, from_autofocus: bool = False) -> None:
        """Updates the canvas with the new image data."""
        if self.dtype != np.dtype(image.dtype):
            self.contrastManager.scale_contrast_limits(np.dtype(image.dtype))
            self.dtype = np.dtype(image.dtype)
            self.init_live = False
            self.init_live_rgb = False

        # Note: live_configuration is synchronized via MicroscopeModeChanged events
        # No fallback to liveController needed - trust event-driven state
        rgb = len(image.shape) >= 3

        if not rgb and not self.init_live or "Live View" not in self.viewer.layers:
            self.initLiveLayer(
                self.live_configuration.name,
                image.shape[0],
                image.shape[1],
                image.dtype,
                rgb,
            )
            self.init_live = True
            self.init_live_rgb = False
            print("init live")
        elif rgb and not self.init_live_rgb:
            self.initLiveLayer(
                self.live_configuration.name,
                image.shape[0],
                image.shape[1],
                image.dtype,
                rgb,
            )
            self.init_live_rgb = True
            self.init_live = False
            print("init live rgb")

        layer = self.viewer.layers["Live View"]
        layer.data = image
        layer.contrast_limits = self.contrastManager.get_limits(
            self.live_configuration.name
        )
        self.updateHistogram(layer)

        if from_autofocus:
            # save viewer scale
            if not self.last_was_autofocus:
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
            # resize to cropped view
            self.resetView()
            self.last_was_autofocus = True
        else:
            if not self.init_scale:
                # init viewer scale
                self.resetView()
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
                self.init_scale = True
            elif self.last_was_autofocus:
                # return to to original view
                self.viewer.camera.zoom = self.previous_scale
                self.viewer.camera.center = self.previous_center
            # save viewer scale
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
            self.last_was_autofocus = False
        layer.refresh()

    def onDoubleClick(self, layer: Layer, event: Event) -> None:
        """Handle double-click events and emit centered coordinates if within the data range."""
        coords = layer.world_to_data(event.position)
        layer_shape = (
            layer.data.shape[0:2] if len(layer.data.shape) >= 3 else layer.data.shape
        )

        if coords is not None and (
            0 <= int(coords[-1]) < layer_shape[-1]
            and (0 <= int(coords[-2]) < layer_shape[-2])
        ):
            x_centered = int(coords[-1] - layer_shape[-1] / 2)
            y_centered = int(coords[-2] - layer_shape[-2] / 2)
            self._event_bus.publish(
                ImageCoordinateClickedCommand(
                    x_pixel=x_centered,
                    y_pixel=y_centered,
                    image_width=layer_shape[-1],
                    image_height=layer_shape[-2],
                    from_napari=True,
                )
            )

    def set_live_configuration(self, live_configuration: ChannelConfiguration) -> None:
        self.live_configuration = live_configuration

    def updateContrastLimits(
        self, channel: str, min_val: float, max_val: float
    ) -> None:
        self.contrastManager.update_limits(channel, min_val, max_val)
        if "Live View" in self.viewer.layers:
            self.viewer.layers["Live View"].contrast_limits = (min_val, max_val)

    def signalContrastLimits(self, event: Event) -> None:
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(
            self.live_configuration.name, min_val, max_val
        )

    def getContrastLimits(self, dtype: np.dtype) -> tuple:
        return self.contrastManager.get_default_limits()

    def resetView(self) -> None:
        self.viewer.reset_view()

    def activate(self) -> None:
        print("ACTIVATING NAPARI LIVE WIDGET")
        self.viewer.window.activate()
