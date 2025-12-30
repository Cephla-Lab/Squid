# set QT_API environment variable
from __future__ import annotations
import os

from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.controllers.autofocus import LaserAutofocusController
from squid.backend.services import ServiceRegistry

os.environ["QT_API"] = "pyqt5"
from typing import Any, Optional

# qt libraries
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from _def import *

# app specific libraries
from squid.ui.widgets.nl5 import NL5Widget
from squid.backend.managers import ChannelConfigurationManager
from squid.backend.managers import ConfigurationManager
from squid.backend.managers import ContrastManager
from squid.backend.controllers.autofocus import LaserAFSettingManager
from squid.backend.controllers.live_controller import LiveController
from squid.backend.managers import ObjectiveStore
from squid.backend.io.stream_handler import StreamHandler
from squid.backend.microcontroller import Microcontroller
from squid.core.abc import AbstractCamera, AbstractStage, AbstractFilterWheelController
import squid.backend.microscope
import squid.ui.widgets as widgets
import pyqtgraph.dockarea as dock
import squid.core.abc
import squid.backend.drivers.cameras.camera_utils
import squid.core.config
import squid.core.logging
import squid.backend.drivers.stages.stage_utils
from squid.core.events import (
    event_bus,
    MoveStageCommand,
    MoveStageToCommand,
    HomeStageCommand,
    StopLiveCommand,
    ImageCoordinateClickedCommand,
    ClickToMoveEnabledChanged,
    WellplateFormatChanged,
    AcquisitionStateChanged,
    ClearScanCoordinatesCommand,
    ActiveAcquisitionTabChanged,
    AutoLevelCommand,
    LiveStateChanged,
)

log = squid.core.logging.get_logger(__name__)

if USE_PRIOR_STAGE:
    import squid.backend.drivers.stages.prior
else:
    import squid.backend.drivers.stages.cephla
from squid.backend.drivers.peripherals.piezo import PiezoStage

if USE_XERYON:
    pass

# control.core.core was a shim - import classes directly
from squid.ui.qt_stream_handler import QtStreamHandler
from squid.ui.image_saver import ImageSaver
from squid.ui.widgets.display.image_display import ImageDisplay, ImageDisplayWindow, ImageArrayDisplayWindow
from squid.backend.managers.focus_map import FocusMap
from squid.ui.widgets.display.navigation_viewer import NavigationViewer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.backend.controllers.tracking_controller import TrackingControllerCore
import squid.backend.drivers.lighting as serial_peripherals

if SUPPORT_LASER_AUTOFOCUS:
    import squid.ui.displacement_measurement as core_displacement_measurement

SINGLE_WINDOW = True  # set to False if use separate windows for display and control

if USE_JUPYTER_CONSOLE:
    from squid.ui.console import JupyterWidget

if RUN_FLUIDICS:
    from squid.backend.drivers.fluidics.fluidics import Fluidics

# Import the custom widget
from squid.ui.widgets.custom_multipoint import TemplateMultiPointWidget


# Import helper modules for widget creation, layout, and signal connections
from squid.ui.gui import widget_factory, layout_builder

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.application import Controllers


class HighContentScreeningGui(QMainWindow):
    fps_software_trigger = 100
    LASER_BASED_FOCUS_TAB_NAME = "Laser-Based Focus"

    def __init__(
        self,
        microscope: squid.backend.microscope.Microscope,
        controllers: Optional["Controllers"] = None,
        services: ServiceRegistry = None,  # ServiceRegistry from ApplicationContext
        is_simulation: bool = False,
        live_only_mode: bool = False,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        if services is None:
            raise ValueError(
                "HighContentScreeningGui requires a ServiceRegistry. "
                "Pass ApplicationContext.services."
        )

        self.log = squid.core.logging.get_logger(self.__class__.__name__)
        self._services = services  # Store for passing to widgets
        self._controllers = controllers
        # Use the registry's bus if it exposes one, otherwise fall back to the global instance
        self._event_bus = getattr(services, "_event_bus", None) or event_bus

        # Create UIEventBus for thread-safe widget subscriptions
        # Must be done in main thread after QApplication exists
        self._ui_event_bus = services.ui_event_bus
        if self._ui_event_bus is None:
            # Fallback: create from global event_bus
            from squid.ui.qt_event_dispatcher import QtEventDispatcher
            from squid.ui.ui_event_bus import UIEventBus
            self._qt_dispatcher = QtEventDispatcher()
            self._ui_event_bus = UIEventBus(event_bus, self._qt_dispatcher)
            self.log.info("Created UIEventBus for thread-safe widget updates (fallback)")
        else:
            self._qt_dispatcher = None  # Owned by ApplicationContext
            self.log.info("Using UIEventBus from ServiceRegistry")

        self.microscope: squid.backend.microscope.Microscope = microscope
        self.stage: AbstractStage = microscope.stage
        self.camera: AbstractCamera = microscope.camera
        self.microcontroller: Microcontroller = (
            microscope.low_level_drivers.microcontroller
        )

        self.xlight: Optional[serial_peripherals.XLight] = microscope.addons.xlight
        self.dragonfly: Optional[serial_peripherals.Dragonfly] = (
            microscope.addons.dragonfly
        )
        self.nl5: Optional[Any] = microscope.addons.nl5
        self.cellx: Optional[serial_peripherals.CellX] = microscope.addons.cellx
        self.emission_filter_wheel: Optional[AbstractFilterWheelController] = (
            microscope.addons.emission_filter_wheel
        )
        self.objective_changer: Optional[Any] = microscope.addons.objective_changer
        self.camera_focus: Optional[AbstractCamera] = microscope.addons.camera_focus
        self.fluidics: Optional[Fluidics] = microscope.addons.fluidics
        self.piezo: Optional[PiezoStage] = microscope.addons.piezo_stage

        self.channelConfigurationManager: ChannelConfigurationManager = (
            microscope.channel_configuration_manager
        )
        self.laserAFSettingManager: LaserAFSettingManager = (
            microscope.laser_af_settings_manager
        )
        self.configurationManager: ConfigurationManager = (
            microscope.configuration_manager
        )
        self.contrastManager: ContrastManager = microscope.contrast_manager
        self.liveController: LiveController = microscope.live_controller
        self.objectiveStore: ObjectiveStore = microscope.objective_store

        if self._controllers:
            if self._controllers.live:
                self.liveController = self._controllers.live
            if self._controllers.objective_store:
                self.objectiveStore = self._controllers.objective_store
            if self._controllers.channel_config_manager:
                self.channelConfigurationManager = self._controllers.channel_config_manager

        self.liveController_focus_camera: Optional[LiveController] = None
        self.streamHandler_focus_camera: Optional[StreamHandler] = None
        self.imageDisplayWindow_focus: Optional[ImageDisplayWindow] = None
        self.displacementMeasurementController: Optional[
            core_displacement_measurement.DisplacementMeasurementController
        ] = None
        self.laserAutofocusController: Optional[LaserAutofocusController] = None

        if SUPPORT_LASER_AUTOFOCUS:
            if self._controllers and self._controllers.live_focus:
                self.liveController_focus_camera = self._controllers.live_focus
            else:
                self.liveController_focus_camera = self.microscope.live_controller_focus
            core_focus_stream = (
                self._controllers.stream_handler_focus
                if (self._controllers and self._controllers.stream_handler_focus)
                else None
            )
            self.streamHandler_focus_camera = QtStreamHandler(
                # Focus camera frames are streamed continuously (used by laser AF); do not gate on LiveController.is_live.
                accept_new_frame_fn=lambda: True,
                handler=core_focus_stream,
            )
            self.imageDisplayWindow_focus = ImageDisplayWindow(
                show_LUT=False, autoLevels=False
            )
            self.displacementMeasurementController = (
                core_displacement_measurement.DisplacementMeasurementController()
            )
            core_laser_af = (
                self._controllers.laser_autofocus
                if self._controllers
                else None
            )
            if core_laser_af:
                self.laserAutofocusController = core_laser_af
            else:
                raise RuntimeError(
                    "LaserAutofocusController must be constructed in ApplicationContext"
                )

        self.live_only_mode = live_only_mode or LIVE_ONLY_MODE
        self.performance_mode = False
        self.napari_connections = {}
        self.well_selector_visible = (
            False  # Add this line to track well selector visibility
        )

        self.streamHandler: QtStreamHandler = None
        self.autofocusController: AutoFocusController = None
        self.imageSaver: ImageSaver = ImageSaver()
        self.imageDisplay: ImageDisplay = ImageDisplay()
        self.trackingController: Optional["TrackingControllerCore"] = None
        self.navigationViewer: NavigationViewer = None
        self.load_objects(is_simulation=is_simulation)

        # Pre-declare and give types to all our widgets so type hinting tools work.  You should
        # add to this as you add widgets.
        self.spinningDiskConfocalWidget: Optional[
            widgets.SpinningDiskConfocalWidget
        ] = None
        self.nl5Wdiget: Optional[NL5Widget] = None
        self.cameraSettingWidget: Optional[widgets.CameraSettingsWidget] = None
        self.profileWidget: Optional[widgets.ProfileWidget] = None
        self.liveControlWidget: Optional[widgets.LiveControlWidget] = None
        self.navigationWidget: Optional[widgets.NavigationWidget] = None
        self.stageUtils: Optional[widgets.StageUtils] = None
        self.dacControlWidget: Optional[widgets.DACControWidget] = None
        self.autofocusWidget: Optional[widgets.AutoFocusWidget] = None
        self.piezoWidget: Optional[widgets.PiezoWidget] = None
        self.objectivesWidget: Optional[widgets.ObjectivesWidget] = None
        self.filterControllerWidget: Optional[widgets.FilterControllerWidget] = None
        self.squidFilterWidget: Optional[widgets.SquidFilterWidget] = None
        self.recordingControlWidget: Optional[widgets.RecordingWidget] = None
        self.wellplateFormatWidget: Optional[widgets.WellplateFormatWidget] = None
        self.wellSelectionWidget: Optional[widgets.WellSelectionWidget] = None
        self.focusMapWidget: Optional[widgets.FocusMapWidget] = None
        self.cameraSettingWidget_focus_camera: Optional[
            widgets.CameraSettingsWidget
        ] = None
        self.laserAutofocusSettingWidget: Optional[
            widgets.LaserAutofocusSettingWidget
        ] = None
        self.waveformDisplay: Optional[widgets.WaveformDisplay] = None
        self.displacementMeasurementWidget: Optional[
            widgets.DisplacementMeasurementWidget
        ] = None
        self.laserAutofocusControlWidget: Optional[
            widgets.LaserAutofocusControlWidget
        ] = None
        self.fluidicsWidget: Optional[widgets.FluidicsWidget] = None
        self.flexibleMultiPointWidget: Optional[widgets.FlexibleMultiPointWidget] = None
        self.wellplateMultiPointWidget: Optional[widgets.WellplateMultiPointWidget] = (
            None
        )
        self.templateMultiPointWidget: Optional[TemplateMultiPointWidget] = None
        self.multiPointWithFluidicsWidget: Optional[
            widgets.MultiPointWithFluidicsWidget
        ] = None
        self.sampleSettingsWidget: Optional[widgets.SampleSettingsWidget] = None
        self.trackingControlWidget: Optional[widgets.TrackingControllerWidget] = None
        self.napariLiveWidget: Optional[widgets.NapariLiveWidget] = None
        self.imageDisplayWindow: Optional[ImageDisplayWindow] = None
        self.imageDisplayWindow_focus: Optional[ImageDisplayWindow] = None
        self.napariMultiChannelWidget: Optional[widgets.NapariMultiChannelWidget] = None
        self.imageArrayDisplayWindow: Optional[ImageArrayDisplayWindow] = None
        self.zPlotWidget: Optional[widgets.SurfacePlotWidget] = None

        self.recordTabWidget: QTabWidget = QTabWidget()
        self.cameraTabWidget: QTabWidget = QTabWidget()
        self.load_widgets()
        self.setup_layout()
        self.make_connections()

        # Acquisition UI should follow backend truth via AcquisitionStateChanged
        self._ui_event_bus.subscribe(
            AcquisitionStateChanged, self._on_acquisition_state_changed
        )

        # Subscribe to WellplateFormatChanged for event-driven wellplate handling (Phase 8)
        self._ui_event_bus.subscribe(
            WellplateFormatChanged, self._on_wellplate_format_changed
        )

        if HOMING_ENABLED_X and HOMING_ENABLED_Y and HOMING_ENABLED_Z:
            # Hardware-owned cached-position restore runs in ApplicationContext._initialize_hardware().
            # Widgets may still need to initialize their own Z tracking state.
            if ENABLE_WELLPLATE_MULTIPOINT:
                self.wellplateMultiPointWidget.init_z()
            self.flexibleMultiPointWidget.init_z()

        # Create the menu bar
        # On macOS, disable native menu bar so it appears in the window
        # (native macOS menu bars can be unreliable with PyQt5)
        menubar = self.menuBar()
        menubar.setNativeMenuBar(False)
        settings_menu = menubar.addMenu("Settings")

        # Configuration action
        config_action = QAction("Configuration...", self)
        config_action.setMenuRole(QAction.NoRole)
        config_action.triggered.connect(self.openPreferences)
        settings_menu.addAction(config_action)

        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            led_matrix_action = QAction("LED Matrix", self)
            led_matrix_action.triggered.connect(self.openLedMatrixSettings)
            settings_menu.addAction(led_matrix_action)

        if USE_JUPYTER_CONSOLE:
            # Create namespace to expose to Jupyter
            self.namespace = {
                "microscope": self.microscope,
            }

            # Create Jupyter widget as a dock widget
            self.jupyter_dock = QDockWidget("Jupyter Console", self)
            self.jupyter_widget = JupyterWidget(namespace=self.namespace)
            self.jupyter_dock.setWidget(self.jupyter_widget)
            self.addDockWidget(Qt.LeftDockWidgetArea, self.jupyter_dock)

        # Main window should act as a UI container: widgets talk via UIEventBus.
        # Drop direct access to the controllers/services registries after initialization.
        self._controllers = None
        self._services = None

    def load_objects(self, is_simulation: bool) -> None:
        core_stream_handler = (
            self._controllers.stream_handler if self._controllers else None
        )
        self.streamHandler = QtStreamHandler(
            accept_new_frame_fn=lambda: self.liveController.is_live,
            handler=core_stream_handler,
        )
        if self._controllers and self._controllers.autofocus:
            self.autofocusController = self._controllers.autofocus
        else:
            raise RuntimeError(
                "AutoFocusController must be constructed in ApplicationContext"
            )
        if ENABLE_TRACKING:
            # TrackingControllerCore is constructed in ApplicationContext; UI only publishes commands.
            tracking = getattr(self._controllers, "tracking", None) if self._controllers else None
            if tracking is None:
                raise RuntimeError(
                    "TrackingControllerCore must be constructed in ApplicationContext"
                )
            self.trackingController = tracking
        if WELLPLATE_FORMAT == "glass slide" and IS_HCS:
            self.navigationViewer = NavigationViewer(
                self.objectiveStore, self.camera, sample="4 glass slide",
                event_bus=self._ui_event_bus,
            )
        else:
            self.navigationViewer = NavigationViewer(
                self.objectiveStore, self.camera, sample=WELLPLATE_FORMAT,
                event_bus=self._ui_event_bus,
            )
        # Acquisition display frames are routed through StreamHandler.capture (no separate acquisition stream).

    def waitForMicrocontroller(
        self, timeout: float = 5.0, error_message: Optional[str] = None
    ) -> None:
        try:
            self.microcontroller.wait_till_operation_is_completed(timeout)
        except TimeoutError as e:
            self.log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def load_widgets(self) -> None:
        # Initialize all GUI widgets using helper functions
        widget_factory.create_hardware_widgets(self)
        widget_factory.create_wellplate_widgets(self)
        widget_factory.create_laser_autofocus_widgets(self)
        widget_factory.create_fluidics_widget(self)

        # Setup image display tabs
        self.imageDisplayTabs = QTabWidget(parent=self)
        if self.live_only_mode:
            if ENABLE_TRACKING:
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager, event_bus=self._ui_event_bus
                )
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager,
                    event_bus=self._ui_event_bus,
                    show_LUT=True,
                    autoLevels=True,
                )
            self.imageDisplayTabs = self.imageDisplayWindow.widget
            self.napariMosaicDisplayWidget = None
        else:
            self.setupImageDisplayTabs()

        # Create acquisition widgets (depends on napariMosaicDisplayWidget from above)
        widget_factory.create_acquisition_widgets(self)

        self.setupRecordTabWidget()
        self.setupCameraTabWidget()

    def setupImageDisplayTabs(self) -> None:
        if USE_NAPARI_FOR_LIVE_VIEW:
            # Get exposure limits from camera service for widget initialization
            camera_service = self._services.get("camera") if self._services else None
            exposure_limits = camera_service.get_exposure_limits() if camera_service else (0.1, 1000.0)
            # Seed initial state for the event-driven widget
            initial_config = self.liveController.currentConfiguration
            objective_name = getattr(self.objectiveStore, "current_objective", None)
            channel_configs = (
                self.channelConfigurationManager.get_configurations(objective_name)
                if objective_name
                else []
            )
            if initial_config is None and channel_configs:
                initial_config = channel_configs[0]
            initial_channel_names = [mode.name for mode in channel_configs]

            self.napariLiveWidget = widgets.NapariLiveWidget(
                self._ui_event_bus,
                self.streamHandler,
                self.contrastManager,
                exposure_limits=exposure_limits,
                initial_configuration=initial_config,
                initial_objective=objective_name,
                initial_channel_configs=initial_channel_names,
                wellSelectionWidget=self.wellSelectionWidget,
            )
            self.imageDisplayTabs.addTab(self.napariLiveWidget, "Live View")
        else:
            if ENABLE_TRACKING:
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager, event_bus=self._ui_event_bus
                )
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = ImageDisplayWindow(
                    contrastManager=self.contrastManager,
                    event_bus=self._ui_event_bus,
                    show_LUT=True,
                    autoLevels=True,
                )
            self.imageDisplayTabs.addTab(self.imageDisplayWindow.widget, "Live View")

        if not self.live_only_mode:
            if USE_NAPARI_FOR_MULTIPOINT:
                # Get initial values for pixel size calculation
                camera_service = self._services.get("camera")
                initial_pixel_size_binned = (
                    camera_service.get_pixel_size_binned_um()
                    if camera_service
                    else 1.0
                )
                initial_pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
                self.napariMultiChannelWidget = widgets.NapariMultiChannelWidget(
                    event_bus=self._ui_event_bus,
                    contrastManager=self.contrastManager,
                    initial_pixel_size_factor=initial_pixel_size_factor,
                    initial_pixel_size_binned_um=initial_pixel_size_binned,
                )
                self.imageDisplayTabs.addTab(
                    self.napariMultiChannelWidget, "Multichannel Acquisition"
                )
            else:
                self.imageArrayDisplayWindow = ImageArrayDisplayWindow()
                self.imageDisplayTabs.addTab(
                    self.imageArrayDisplayWindow.widget, "Multichannel Acquisition"
                )

            if USE_NAPARI_FOR_MOSAIC_DISPLAY:
                self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(
                    contrastManager=self.contrastManager,
                    event_bus=self._ui_event_bus,
                )
                self.imageDisplayTabs.addTab(
                    self.napariMosaicDisplayWidget, "Mosaic View"
                )

            # Plate view for well-based acquisitions (only if enabled)
            if DISPLAY_PLATE_VIEW:
                self.napariPlateViewWidget = widgets.NapariPlateViewWidget(
                    contrastManager=self.contrastManager,
                )
                self.imageDisplayTabs.addTab(
                    self.napariPlateViewWidget, "Plate View"
                )

            # z plot
            self.zPlotWidget = widgets.SurfacePlotWidget()
            dock_surface_plot = dock.Dock("Z Plot", autoOrientation=False)
            dock_surface_plot.showTitleBar()
            dock_surface_plot.addWidget(self.zPlotWidget)
            dock_surface_plot.setStretch(x=100, y=100)

            surface_plot_dockArea = dock.DockArea()
            surface_plot_dockArea.addDock(dock_surface_plot)

            self.imageDisplayTabs.addTab(surface_plot_dockArea, "Plots")

            # Connect the point clicked signal to move the stage
            self.zPlotWidget.signal_point_clicked.connect(self.move_to_mm)

        if SUPPORT_LASER_AUTOFOCUS:
            dock_laserfocus_image_display = dock.Dock(
                "Focus Camera Image Display", autoOrientation=False
            )
            dock_laserfocus_image_display.showTitleBar()
            dock_laserfocus_image_display.addWidget(
                self.imageDisplayWindow_focus.widget
            )
            dock_laserfocus_image_display.setStretch(x=100, y=100)

            dock_laserfocus_liveController = dock.Dock(
                "Laser Autofocus Settings", autoOrientation=False
            )
            dock_laserfocus_liveController.showTitleBar()
            dock_laserfocus_liveController.addWidget(self.laserAutofocusSettingWidget)
            dock_laserfocus_liveController.setStretch(x=100, y=100)
            dock_laserfocus_liveController.setFixedWidth(
                self.laserAutofocusSettingWidget.minimumSizeHint().width()
            )

            dock_waveform = dock.Dock("Displacement Measurement", autoOrientation=False)
            dock_waveform.showTitleBar()
            dock_waveform.addWidget(self.waveformDisplay)
            dock_waveform.setStretch(x=100, y=40)

            dock_displayMeasurement = dock.Dock(
                "Displacement Measurement Control", autoOrientation=False
            )
            dock_displayMeasurement.showTitleBar()
            dock_displayMeasurement.addWidget(self.displacementMeasurementWidget)
            dock_displayMeasurement.setStretch(x=100, y=40)
            dock_displayMeasurement.setFixedWidth(
                self.displacementMeasurementWidget.minimumSizeHint().width()
            )

            laserfocus_dockArea = dock.DockArea()
            laserfocus_dockArea.addDock(dock_laserfocus_image_display)
            laserfocus_dockArea.addDock(
                dock_laserfocus_liveController,
                "right",
                relativeTo=dock_laserfocus_image_display,
            )
            if SHOW_LEGACY_DISPLACEMENT_MEASUREMENT_WINDOWS:
                laserfocus_dockArea.addDock(
                    dock_waveform, "bottom", relativeTo=dock_laserfocus_liveController
                )
                laserfocus_dockArea.addDock(
                    dock_displayMeasurement, "bottom", relativeTo=dock_waveform
                )

            self.imageDisplayTabs.addTab(
                laserfocus_dockArea, self.LASER_BASED_FOCUS_TAB_NAME
            )

        if RUN_FLUIDICS:
            self.imageDisplayTabs.addTab(self.fluidicsWidget, "Fluidics")

    def setupRecordTabWidget(self) -> None:
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.recordTabWidget.addTab(
                self.wellplateMultiPointWidget, "Wellplate Multipoint"
            )
        if ENABLE_FLEXIBLE_MULTIPOINT:
            self.recordTabWidget.addTab(
                self.flexibleMultiPointWidget, "Flexible Multipoint"
            )
        if USE_TEMPLATE_MULTIPOINT:
            self.recordTabWidget.addTab(
                self.templateMultiPointWidget, "Template Multipoint"
            )
        if RUN_FLUIDICS:
            self.recordTabWidget.addTab(
                self.multiPointWithFluidicsWidget, "Multipoint with Fluidics"
            )
        if ENABLE_TRACKING:
            self.recordTabWidget.addTab(self.trackingControlWidget, "Tracking")
        if ENABLE_RECORDING:
            self.recordTabWidget.addTab(self.recordingControlWidget, "Simple Recording")
        self.recordTabWidget.currentChanged.connect(
            lambda: self.resizeCurrentTab(self.recordTabWidget)
        )
        self.resizeCurrentTab(self.recordTabWidget)

    def setupCameraTabWidget(self) -> None:
        if not USE_NAPARI_FOR_LIVE_CONTROL or self.live_only_mode:
            self.cameraTabWidget.addTab(self.navigationWidget, "Stages")
        if self.piezoWidget:
            self.cameraTabWidget.addTab(self.piezoWidget, "Piezo")
        if ENABLE_NL5:
            self.cameraTabWidget.addTab(self.nl5Wdiget, "NL5")
        if ENABLE_SPINNING_DISK_CONFOCAL:
            self.cameraTabWidget.addTab(self.spinningDiskConfocalWidget, "Confocal")
        if self.emission_filter_wheel:
            self.cameraTabWidget.addTab(self.filterControllerWidget, "Emission Filter")
        self.cameraTabWidget.addTab(self.cameraSettingWidget, "Camera")
        self.cameraTabWidget.addTab(self.autofocusWidget, "Contrast AF")
        if SUPPORT_LASER_AUTOFOCUS:
            self.cameraTabWidget.addTab(self.laserAutofocusControlWidget, "Laser AF")
        self.cameraTabWidget.addTab(self.focusMapWidget, "Focus Map")
        self.cameraTabWidget.currentChanged.connect(
            lambda: self.resizeCurrentTab(self.cameraTabWidget)
        )
        self.resizeCurrentTab(self.cameraTabWidget)

    def setup_layout(self):
        # Setup the control panel layout
        layout_builder.setup_control_panel_layout(self)

        # Setup single or multi window layout
        if SINGLE_WINDOW:
            layout_builder.setup_single_window_layout(self)
        else:
            layout_builder.setup_multi_window_layout(self)

    def make_connections(self) -> None:
        # Core stream handler connections
        self.streamHandler.packet_image_to_write.connect(self.imageSaver.enqueue)

        # Napari connections (complex, kept as method)
        self.makeNapariConnections()
        self._connect_tab_signals()
        self._connect_plot_signals()
        self._connect_plate_view_signals()
        self._connect_well_selector_button()
        self._connect_laser_autofocus_signals()
        # Confocal widgets publish commands/events directly.

        # UI nicety: switch to live tab when live starts.
        self._ui_event_bus.subscribe(
            LiveStateChanged,
            lambda e: self.imageDisplayTabs.setCurrentIndex(0)
            if (getattr(e, "camera", "main") == "main" and e.is_live)
            else None,
        )

    def _connect_tab_signals(self) -> None:
        self.recordTabWidget.currentChanged.connect(self.onTabChanged)
        if not self.live_only_mode:
            self.imageDisplayTabs.currentChanged.connect(self.onDisplayTabChanged)

    def _connect_plot_signals(self) -> None:
        if self._ui_event_bus is None or getattr(self, "zPlotWidget", None) is None:
            return
        from squid.core.events import AcquisitionCoordinates, AcquisitionWorkerFinished

        self._ui_event_bus.subscribe(
            AcquisitionCoordinates,
            lambda e: self.zPlotWidget.add_point(e.x_mm, e.y_mm, e.z_mm, e.region_id),
        )
        self._ui_event_bus.subscribe(
            AcquisitionWorkerFinished,
            lambda _e: self.zPlotWidget.plot(),
        )

    def _connect_plate_view_signals(self) -> None:
        """Connect PlateViewInit and PlateViewUpdate events to the plate view widget."""
        if self._ui_event_bus is None:
            return
        if not getattr(self, "napariPlateViewWidget", None):
            return

        from squid.core.events import PlateViewInit, PlateViewUpdate

        self._ui_event_bus.subscribe(
            PlateViewInit,
            lambda e: self.napariPlateViewWidget.initPlateLayout(
                e.num_rows, e.num_cols, e.well_slot_shape, e.fov_grid_shape, e.channel_names
            ),
        )
        self._ui_event_bus.subscribe(
            PlateViewUpdate,
            lambda e: self.napariPlateViewWidget.updatePlateView(
                e.channel_idx, e.channel_name, e.plate_image
            ),
        )

    def _connect_well_selector_button(self) -> None:
        if hasattr(self.imageDisplayWindow, "btn_well_selector"):
            self.imageDisplayWindow.btn_well_selector.clicked.connect(
                lambda: self.toggleWellSelector(not self.dock_wellSelection.isVisible())
            )

    def _connect_laser_autofocus_signals(self) -> None:
        if not SUPPORT_LASER_AUTOFOCUS:
            return

        if self.cameraSettingWidget_focus_camera:
            self.laserAutofocusSettingWidget.signal_newExposureTime.connect(
                self.cameraSettingWidget_focus_camera.set_exposure_time
            )
            self.laserAutofocusSettingWidget.signal_newAnalogGain.connect(
                self.cameraSettingWidget_focus_camera.set_analog_gain
            )
        self.laserAutofocusSettingWidget.signal_apply_settings.connect(
            self.laserAutofocusControlWidget.update_init_state
        )
        self.laserAutofocusSettingWidget.update_exposure_time(
            self.laserAutofocusSettingWidget.exposure_spinbox.value()
        )
        self.laserAutofocusSettingWidget.update_analog_gain(
            self.laserAutofocusSettingWidget.analog_gain_spinbox.value()
        )
        self.streamHandler_focus_camera.image_to_display.connect(
            self.imageDisplayWindow_focus.display_image
        )
        self.streamHandler_focus_camera.image_to_display.connect(
            self.displacementMeasurementController.update_measurement
        )
        self.displacementMeasurementController.signal_plots.connect(self.waveformDisplay.plot)
        self.displacementMeasurementController.signal_readings.connect(
            self.displacementMeasurementWidget.display_readings
        )

    def makeNapariConnections(self) -> None:
        """Initialize all Napari connections in one place"""
        self.napari_connections = {
            "napariLiveWidget": [],
            "napariMultiChannelWidget": [],
            "napariMosaicDisplayWidget": [],
        }
        # NOTE: Avoid connecting Qt signals to lambdas for cross-thread delivery:
        # lambdas are not QObjects, so Qt may execute them on the emitter thread.
        # Keep capture fanout as QObject methods to ensure queued delivery on the GUI thread.

        # Setup live view connections
        if USE_NAPARI_FOR_LIVE_VIEW and not self.live_only_mode:
            self.napari_connections["napariLiveWidget"] = [
                (
                    self.streamHandler.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(
                        image, from_autofocus=False
                    ),
                ),
            ]

            if USE_NAPARI_FOR_LIVE_CONTROL:
                self.napari_connections["napariLiveWidget"].extend(
                    [
                        # Napari live control publishes commands/events directly.
                    ]
                )
        else:
            # Non-Napari display connections
            self.streamHandler.image_to_display.connect(self.imageDisplay.enqueue)
            self.imageDisplay.image_to_display.connect(
                self.imageDisplayWindow.display_image
            )
            self.imageDisplayWindow.image_click_coordinates.connect(
                lambda x, y, w, h: self._ui_event_bus.publish(
                    ImageCoordinateClickedCommand(
                        x_pixel=x,
                        y_pixel=y,
                        image_width=w,
                        image_height=h,
                        from_napari=False,
                    )
                )
            )

        if not self.live_only_mode:
            # Setup multichannel widget connections
            if USE_NAPARI_FOR_MULTIPOINT:
                self.napari_connections["napariMultiChannelWidget"] = [
                    (
                        self.streamHandler.capture,
                        self._on_stream_capture_multichannel,
                    )
                ]
            else:
                self.streamHandler.capture.connect(
                    lambda image, info: self.imageArrayDisplayWindow.display_image(
                        image, info.configuration.illumination_source
                    )
                )

            # Setup mosaic display widget connections
            if USE_NAPARI_FOR_MOSAIC_DISPLAY:
                self.napari_connections["napariMosaicDisplayWidget"] = []
                self.napari_connections["napariMosaicDisplayWidget"].append(
                    (
                        self.streamHandler.capture,
                        self._on_stream_capture_mosaic,
                    )
                )
                self.napari_connections["napariMosaicDisplayWidget"].extend(
                    [
                        (
                            self.napariMosaicDisplayWidget.signal_clear_viewer,
                            self.navigationViewer.clear_overlay,
                        ),
                    ]
                )

            # Make initial connections
            self.updateNapariConnections()

    def _layer_name_from_capture_info(self, info) -> str:
        try:
            objective_mag = str(
                int(self.objectiveStore.get_current_objective_info()["magnification"])
            )
            return objective_mag + "x " + info.configuration.name
        except Exception:
            return getattr(getattr(info, "configuration", None), "name", "Unknown")

    def _on_stream_capture_mosaic(self, image, info) -> None:
        if getattr(self, "napariMosaicDisplayWidget", None) is None:
            return
        channel_name = self._layer_name_from_capture_info(info)
        self.napariMosaicDisplayWidget.updateMosaic(image, info, channel_name)

    def _on_stream_capture_multichannel(self, image, info) -> None:
        if getattr(self, "napariMultiChannelWidget", None) is None:
            return
        channel_name = self._layer_name_from_capture_info(info)
        self.napariMultiChannelWidget.updateLayers(image, 0, 0, info.z_index, channel_name)

    def updateNapariConnections(self) -> None:
        # Update Napari connections based on performance mode. Live widget connections are preserved
        for widget_name, connections in self.napari_connections.items():
            if (
                widget_name != "napariLiveWidget"
            ):  # Always keep the live widget connected
                widget = getattr(self, widget_name, None)
                if widget:
                    for signal, slot in connections:
                        if self.performance_mode:
                            try:
                                signal.disconnect(slot)
                            except TypeError:
                                # Connection might not exist, which is fine
                                pass
                        else:
                            try:
                                signal.connect(slot)
                            except TypeError:
                                # Connection might already exist, which is fine
                                pass

    def toggleNapariTabs(self) -> None:
        # Enable/disable Napari tabs based on performance mode
        for i in range(1, self.imageDisplayTabs.count()):
            if self.imageDisplayTabs.tabText(i) != self.LASER_BASED_FOCUS_TAB_NAME:
                self.imageDisplayTabs.setTabEnabled(i, not self.performance_mode)

        if self.performance_mode:
            # Switch to the NapariLiveWidget tab if it exists
            for i in range(self.imageDisplayTabs.count()):
                if isinstance(
                    self.imageDisplayTabs.widget(i), widgets.NapariLiveWidget
                ):
                    self.imageDisplayTabs.setCurrentIndex(i)
                    break

    def togglePerformanceMode(self) -> None:
        self.performance_mode = self.performanceModeToggle.isChecked()
        button_txt = "Disable" if self.performance_mode else "Enable"
        self.performanceModeToggle.setText(button_txt + " Performance Mode")
        self.updateNapariConnections()
        self.toggleNapariTabs()
        print(f"Performance mode {'enabled' if self.performance_mode else 'disabled'}")

    def setAcquisitionDisplayTabs(self, selected_configurations: list, Nz: int) -> None:
        if self.performance_mode:
            self.imageDisplayTabs.setCurrentIndex(0)
        elif not self.live_only_mode:
            configs = [config.name for config in selected_configurations]
            print(configs)
            if USE_NAPARI_FOR_MOSAIC_DISPLAY and Nz == 1:
                self.imageDisplayTabs.setCurrentWidget(self.napariMosaicDisplayWidget)

            elif USE_NAPARI_FOR_MULTIPOINT:
                self.imageDisplayTabs.setCurrentWidget(self.napariMultiChannelWidget)
            else:
                self.imageDisplayTabs.setCurrentIndex(0)

    def openLedMatrixSettings(self) -> None:
        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            dialog = widgets.LedMatrixSettingsDialog(self.liveController.led_array)
            dialog.exec_()

    def openPreferences(self) -> None:
        from configparser import ConfigParser
        from _def import CACHED_CONFIG_FILE_PATH
        import os

        if CACHED_CONFIG_FILE_PATH and os.path.exists(CACHED_CONFIG_FILE_PATH):
            config = ConfigParser()
            config.read(CACHED_CONFIG_FILE_PATH)
            dialog = widgets.PreferencesDialog(config, CACHED_CONFIG_FILE_PATH, self)
            dialog.exec_()
        else:
            self.log.warning("No configuration file found")

    def onTabChanged(self, index: int) -> None:
        is_flexible_acquisition = (
            (index == self.recordTabWidget.indexOf(self.flexibleMultiPointWidget))
            if ENABLE_FLEXIBLE_MULTIPOINT
            else False
        )
        is_wellplate_acquisition = (
            (index == self.recordTabWidget.indexOf(self.wellplateMultiPointWidget))
            if ENABLE_WELLPLATE_MULTIPOINT
            else False
        )
        is_fluidics_acquisition = (
            (index == self.recordTabWidget.indexOf(self.multiPointWithFluidicsWidget))
            if RUN_FLUIDICS
            else False
        )
        self._ui_event_bus.publish(ClearScanCoordinatesCommand())

        self.toggleWellSelector(
            is_wellplate_acquisition
            and self.wellSelectionWidget.format != "glass slide"
        )
        active_tab = (
            "wellplate"
            if is_wellplate_acquisition
            else "flexible"
            if is_flexible_acquisition
            else "fluidics"
            if is_fluidics_acquisition
            else "other"
        )
        self._ui_event_bus.publish(ActiveAcquisitionTabChanged(active_tab=active_tab))

    def resizeCurrentTab(self, tabWidget: QTabWidget) -> None:
        current_widget = tabWidget.currentWidget()
        if current_widget:
            total_height = (
                current_widget.sizeHint().height() + tabWidget.tabBar().height()
            )
            tabWidget.resize(tabWidget.width(), total_height)
            tabWidget.setMaximumHeight(total_height)
            tabWidget.updateGeometry()
            self.updateGeometry()

    def onDisplayTabChanged(self, index: int) -> None:
        current_widget = self.imageDisplayTabs.widget(index)
        if hasattr(current_widget, "viewer"):
            current_widget.activate()

        # Stop focus camera live if not on laser focus tab
        if SUPPORT_LASER_AUTOFOCUS:
            is_laser_focus_tab = (
                self.imageDisplayTabs.tabText(index) == self.LASER_BASED_FOCUS_TAB_NAME
            )

            if hasattr(self, "dock_wellSelection"):
                self.dock_wellSelection.setVisible(not is_laser_focus_tab)

            if not is_laser_focus_tab:
                self.laserAutofocusSettingWidget.stop_live()

        # Only show well selector in Live View tab if it was previously shown
        if self.imageDisplayTabs.tabText(index) == "Live View":
            self.toggleWellSelector(
                self.well_selector_visible
            )  # Use stored visibility state
        else:
            self.toggleWellSelector(False)

    def _on_wellplate_format_changed(self, event: WellplateFormatChanged) -> None:
        """Handle WellplateFormatChanged from EventBus.

        This is the event-driven entry point for wellplate format changes.
        Widgets should publish this event instead of using Qt signals.
        """
        format_name = event.format_name

        if format_name == "glass slide":
            self.toggleWellSelector(False)
            self.stageUtils.is_wellplate = False
        else:
            self.toggleWellSelector(True)
            self.stageUtils.is_wellplate = True

        # Swap well selector widget type for 1536 vs other formats (UI-only).
        if format_name == "1536 well plate" and not isinstance(
            self.wellSelectionWidget, widgets.Well1536SelectionWidget
        ):
            self.replaceWellSelectionWidget(widgets.Well1536SelectionWidget(self._ui_event_bus))
        elif format_name != "1536 well plate" and isinstance(
            self.wellSelectionWidget, widgets.Well1536SelectionWidget
        ):
            self.replaceWellSelectionWidget(
                widgets.WellSelectionWidget(
                    self._ui_event_bus,
                    format_name,
                    rows=event.rows,
                    cols=event.cols,
                    well_spacing_mm=event.well_spacing_mm,
                    well_size_mm=event.well_size_mm,
                    a1_x_mm=event.a1_x_mm,
                    a1_y_mm=event.a1_y_mm,
                    a1_x_pixel=event.a1_x_pixel,
                    a1_y_pixel=event.a1_y_pixel,
                    number_of_skip=event.number_of_skip,
                )
            )

    def replaceWellSelectionWidget(
        self, new_widget: widgets.WellSelectionWidget
    ) -> None:
        self.wellSelectionWidget.setParent(None)
        self.wellSelectionWidget.deleteLater()
        self.wellSelectionWidget = new_widget
        if (
            USE_NAPARI_WELL_SELECTION
            and not self.performance_mode
            and not self.live_only_mode
        ):
            self.napariLiveWidget.replace_well_selector(self.wellSelectionWidget)
        else:
            self.dock_wellSelection.addWidget(self.wellSelectionWidget)

    def connectWellSelectionWidget(self):
        # Legacy hook retained for older call sites; well selection is now event-driven.
        return

    def toggleWellSelector(self, show: bool, remember_state: bool = True) -> None:
        if (
            show
            and self.imageDisplayTabs.tabText(self.imageDisplayTabs.currentIndex())
            == "Live View"
        ):
            self.dock_wellSelection.setVisible(True)
        else:
            self.dock_wellSelection.setVisible(False)

        # Only update visibility state if we're in Live View tab and we want to remember the state
        # remember_state is False when we're toggling the well selector for starting/stopping an acquisition
        if (
            self.imageDisplayTabs.tabText(self.imageDisplayTabs.currentIndex())
            == "Live View"
            and remember_state
        ):
            self.well_selector_visible = show

        # Update button text
        if hasattr(self.imageDisplayWindow, "btn_well_selector"):
            self.imageDisplayWindow.btn_well_selector.setText(
                "Hide Well Selector" if show else "Show Well Selector"
            )

    def _on_acquisition_state_changed(self, event: AcquisitionStateChanged) -> None:
        """Handle backend acquisition state changes (UI truth-from-backend)."""
        self._apply_acquisition_ui_state(event.in_progress)

    def _apply_acquisition_ui_state(self, acquisition_started: bool) -> None:
        """Apply acquisition start/stop UI state changes from backend truth."""
        self.log.debug(f"_apply_acquisition_ui_state({acquisition_started=})")
        if acquisition_started:
            self.log.info("STARTING ACQUISITION")
        else:
            self.log.info("FINISHED ACQUISITION")

        # Click to move off during acquisition
        # Update both the widget and publish event for ImageClickController
        self.navigationWidget.set_click_to_move(not acquisition_started)
        self._ui_event_bus.publish(ClickToMoveEnabledChanged(enabled=not acquisition_started))

        # disable other acquisition tabs during acquisition
        current_index = self.recordTabWidget.currentIndex()
        for index in range(self.recordTabWidget.count()):
            self.recordTabWidget.setTabEnabled(
                index, not acquisition_started or index == current_index
            )

        # disable autolevel once acquisition started
        if acquisition_started:
            self._ui_event_bus.publish(AutoLevelCommand(enabled=False))

        # hide well selector during acquisition
        is_wellplate_acquisition = (
            (
                current_index
                == self.recordTabWidget.indexOf(self.wellplateMultiPointWidget)
            )
            if ENABLE_WELLPLATE_MULTIPOINT
            else False
        )
        if (
            is_wellplate_acquisition
            and self.wellSelectionWidget.format != "glass slide"
        ):
            self.toggleWellSelector(not acquisition_started, remember_state=False)
        else:
            self.toggleWellSelector(False)

        # Progress bars are driven by AcquisitionStateChanged in the acquisition widgets.

    def move_to_mm(self, x_mm: float, y_mm: float) -> None:
        self._ui_event_bus.publish(MoveStageToCommand(x_mm=x_mm, y_mm=y_mm))

    def closeEvent(self, event: QCloseEvent) -> None:
        if not getattr(self, "_skip_close_confirmation", False):
            reply = QMessageBox.question(
                self,
                "Confirm Exit",
                "Are you sure you want to exit the software?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return

        # UI publishes commands only; hardware cleanup is handled by ApplicationContext.shutdown().
        self._ui_event_bus.publish(StopLiveCommand())

        if SUPPORT_LASER_AUTOFOCUS and self.imageDisplayWindow_focus is not None:
            try:
                self.imageDisplayWindow_focus.close()
            except Exception:
                pass

        try:
            self.imageSaver.close()
        except Exception:
            pass
        try:
            self.imageDisplay.close()
        except Exception:
            pass
        if not SINGLE_WINDOW:
            self.imageDisplayWindow.close()
            self.imageArrayDisplayWindow.close()
            self.tabbedImageDisplayWindow.close()

        self.microcontroller.close()
        try:
            self.cswWindow.closeForReal(event)
        except AttributeError:
            pass

        try:
            self.cswfcWindow.closeForReal(event)
        except AttributeError:
            pass

        event.accept()
