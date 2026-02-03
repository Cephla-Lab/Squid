# set QT_API environment variable
from __future__ import annotations
import os

from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.controllers.autofocus import LaserAutofocusController
from squid.backend.drivers.cameras.settings_cache import (
    save_camera_settings,
    load_camera_settings,
)
from squid.backend.services import ServiceRegistry

os.environ["QT_API"] = "pyqt5"
from typing import Any, Optional

# qt libraries
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAction,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
)
from qtpy.QtGui import QCloseEvent

from _def import (
    ENABLE_NDVIEWER,
    ENABLE_NL5,
    ENABLE_WELLPLATE_MULTIPOINT,
    HOMING_ENABLED_X,
    HOMING_ENABLED_Y,
    HOMING_ENABLED_Z,
    IS_HCS,
    LIVE_ONLY_MODE,
    RUN_FLUIDICS,
    SHOW_LEGACY_DISPLACEMENT_MEASUREMENT_WINDOWS,
    SIMULATED_DISK_IO_ENABLED,
    SIMULATION_FORCE_SAVE_IMAGES,
    SUPPORT_SCIMICROSCOPY_LED_ARRAY,
    USE_JUPYTER_CONSOLE,
    USE_NAPARI_WELL_SELECTION,
    USE_PRIOR_STAGE,
    USE_TEMPLATE_MULTIPOINT,
    USE_XERYON,
    WELLPLATE_FORMAT,
)
from squid.core.config.feature_flags import get_feature_flags

# app specific libraries
from squid.ui.widgets.nl5 import NL5Widget
from squid.backend.managers import ChannelConfigService
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
    auto_subscribe,
    auto_unsubscribe,
    handles,
    event_bus,
    AcquisitionCoordinates,
    AcquisitionStarted,
    MoveStageCommand,
    MoveStageToCommand,
    HomeStageCommand,
    StopLiveCommand,
    ImageCoordinateClickedCommand,
    ClickToMoveEnabledChanged,
    WellplateFormatChanged,
    AcquisitionStateChanged,
    AcquisitionWorkerFinished,
    ClearScanCoordinatesCommand,
    ActiveAcquisitionTabChanged,
    AutoLevelCommand,
    LiveStateChanged,
    PlateViewInit,
    PlateViewUpdate,
)

from squid.backend.controllers.workflow_runner.state import WorkflowRunnerStateChanged

log = squid.core.logging.get_logger(__name__)
_FEATURE_FLAGS = get_feature_flags()

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

if _FEATURE_FLAGS.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
    import squid.ui.displacement_measurement as core_displacement_measurement

SINGLE_WINDOW = True  # set to False if use separate windows for display and control

if USE_JUPYTER_CONSOLE:
    from squid.ui.console import JupyterWidget

# Legacy Fluidics import removed - now using FluidicsService from ApplicationContext
# See: squid/backend/services/fluidics_service.py

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
        self._feature_flags = get_feature_flags()
        self._services = services  # Store for passing to widgets
        self._controllers = controllers
        self._is_simulation = is_simulation
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
        self._subscriptions = []

        self.microscope: squid.backend.microscope.Microscope = microscope
        self.stage: AbstractStage = microscope.stage
        self.camera: AbstractCamera = microscope.camera

        # Restore cached camera settings from previous session
        self._restore_cached_camera_settings()

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
        # Legacy self.fluidics removed - FluidicsService now accessed via self._services.get("fluidics")
        self.piezo: Optional[PiezoStage] = microscope.addons.piezo_stage

        self.channelConfigurationManager: ChannelConfigService = (
            microscope.channel_config_service
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
            if self._controllers.channel_config_service:
                self.channelConfigurationManager = self._controllers.channel_config_service

        self.liveController_focus_camera: Optional[LiveController] = None
        self.streamHandler_focus_camera: Optional[StreamHandler] = None
        self.imageDisplayWindow_focus: Optional[ImageDisplayWindow] = None
        self.displacementMeasurementController: Optional[
            core_displacement_measurement.DisplacementMeasurementController
        ] = None
        self.laserAutofocusController: Optional[LaserAutofocusController] = None

        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
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
                # Gate on focus camera live view - Focus Lock widget has its own preview
                accept_new_frame_fn=lambda: self.liveController_focus_camera.is_live if self.liveController_focus_camera else False,
                handler=core_focus_stream,
            )
            self.imageDisplayWindow_focus = ImageDisplayWindow(
                event_bus=self._ui_event_bus, show_LUT=False, autoLevels=False
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
        self.alignmentWidget: Optional[widgets.AlignmentWidget] = None
        self.imageDisplayWindow: Optional[ImageDisplayWindow] = None
        self.imageDisplayWindow_focus: Optional[ImageDisplayWindow] = None
        self.napariMultiChannelWidget: Optional[widgets.NapariMultiChannelWidget] = None
        self.imageArrayDisplayWindow: Optional[ImageArrayDisplayWindow] = None
        self.zPlotWidget: Optional[widgets.SurfacePlotWidget] = None

        self.recordTabWidget: QTabWidget = QTabWidget()
        self.cameraTabWidget: QTabWidget = QTabWidget()

        # Warning banner for simulated disk I/O mode (only if force save is not enabled)
        self.simulated_io_warning_banner: Optional[QLabel] = None
        if SIMULATED_DISK_IO_ENABLED and not SIMULATION_FORCE_SAVE_IMAGES:
            self.simulated_io_warning_banner = QLabel(
                "SIMULATED DISK I/O: Images are NOT being saved to disk!"
            )
            self.simulated_io_warning_banner.setStyleSheet(
                "background-color: #cc0000; color: white; font-weight: bold; "
                "padding: 8px; font-size: 14px;"
            )
            self.simulated_io_warning_banner.setAlignment(Qt.AlignCenter)

        # Warning/Error display widget (auto-hides when empty)
        from squid.ui.widgets.warning_error_widget import WarningErrorWidget
        self.warningErrorWidget = WarningErrorWidget()
        self.warningErrorWidget.setVisible(False)
        self._warning_handler = None

        self.load_widgets()
        self.setup_layout()
        self.make_connections()

        if self._ui_event_bus is not None:
            self._subscriptions = auto_subscribe(self, self._ui_event_bus)

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

        # Settings menu (exposed as self.settings_menu so entry-point scripts can add items)
        self.settings_menu = menubar.addMenu("Settings")

        config_action = QAction("Preferences...", self)
        config_action.setMenuRole(QAction.NoRole)
        config_action.triggered.connect(self.openPreferences)
        self.settings_menu.addAction(config_action)

        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            led_matrix_action = QAction("LED Matrix", self)
            led_matrix_action.triggered.connect(self.openLedMatrixSettings)
            self.settings_menu.addAction(led_matrix_action)

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

        # Add warning/error widget to status bar
        if self.warningErrorWidget is not None:
            self.statusBar().addWidget(self.warningErrorWidget)

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
        if self._feature_flags.is_enabled("ENABLE_TRACKING"):
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
            if self._feature_flags.is_enabled("ENABLE_TRACKING"):
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
        if self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_VIEW"):
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

            # Create alignment widget for sample registration (uses napari viewer)
            self._setup_alignment_widget()
        else:
            if self._feature_flags.is_enabled("ENABLE_TRACKING"):
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
            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MULTIPOINT"):
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

            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MOSAIC_DISPLAY"):
                self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(
                    contrastManager=self.contrastManager,
                    event_bus=self._ui_event_bus,
                )
                self.imageDisplayTabs.addTab(
                    self.napariMosaicDisplayWidget, "Mosaic View"
                )

            # Plate view for well-based acquisitions (only if enabled)
            if self._feature_flags.is_enabled("DISPLAY_PLATE_VIEW"):
                self.napariPlateViewWidget = widgets.NapariPlateViewWidget(
                    contrastManager=self.contrastManager,
                )
                self.imageDisplayTabs.addTab(
                    self.napariPlateViewWidget, "Plate View"
                )

            # Embedded NDViewer - initialized AFTER napari widgets because
            # NDV and napari both use vispy for OpenGL rendering. Initializing NDV first
            # can cause OpenGL context conflicts since both libraries share vispy state.
            self.ndviewerTab: Optional[widgets.NDViewerTab] = None
            if ENABLE_NDVIEWER:
                try:
                    self.ndviewerTab = widgets.NDViewerTab(event_bus=self._event_bus)
                    self.imageDisplayTabs.addTab(self.ndviewerTab, "NDViewer")
                except ImportError:
                    self.log.warning("NDViewer tab unavailable: ndviewer_light module not installed")
                except (RuntimeError, OSError) as e:
                    self.log.exception(f"Failed to initialize NDViewer tab due to system error: {e}")
                except Exception:
                    self.log.exception("Failed to initialize NDViewer tab - unexpected error")

            # Connect plate view double-click to NDViewer navigation
            if (
                getattr(self, "napariPlateViewWidget", None) is not None
                and self.ndviewerTab is not None
            ):
                if hasattr(self.napariPlateViewWidget, "signal_well_fov_clicked"):
                    self.napariPlateViewWidget.signal_well_fov_clicked.connect(
                        self._on_plate_view_fov_clicked
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

        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
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

        if RUN_FLUIDICS and self.fluidicsWidget is not None:
            self.imageDisplayTabs.addTab(self.fluidicsWidget, "Fluidics")

        # Orchestrator tab for multi-round experiment automation
        self._setup_orchestrator_tab()

    def _setup_orchestrator_tab(self) -> None:
        """Create and add the Orchestrator tab for multi-round experiment automation."""
        # Only create if orchestrator controller is available
        if self._controllers is None:
            self.log.debug("Orchestrator tab not created: controllers is None")
            return
        if self._controllers.orchestrator is None:
            self.log.info("Orchestrator tab not created: orchestrator controller not available")
            return

        from squid.ui.widgets.orchestrator.orchestrator_widget import (
            OrchestratorControlPanel,
            OrchestratorWorkflowTree,
        )
        from squid.ui.widgets.orchestrator.warning_panel import WarningPanel
        from squid.ui.widgets.orchestrator.parameter_panel import ParameterInspectionPanel

        if self._ui_event_bus is None:
            self.log.warning("UIEventBus not available for OrchestratorWidget")
            return

        # Create the control panel (status, buttons, progress)
        self.orchestratorControlPanel = OrchestratorControlPanel(
            event_bus=self._ui_event_bus,
            orchestrator=self._controllers.orchestrator,
            parent=self,
        )

        # Create the workflow tree
        self.orchestratorWorkflowTree = OrchestratorWorkflowTree(
            event_bus=self._ui_event_bus,
            parent=self,
        )

        # Create the warning panel
        self.orchestratorWarningPanel = WarningPanel(
            event_bus=self._ui_event_bus,
            parent=self,
        )

        # Create the parameter inspection panel
        self.orchestratorParameterPanel = ParameterInspectionPanel(
            parent=self,
        )

        # Connect control panel to workflow tree
        self.orchestratorControlPanel.fov_positions_changed.connect(
            self.orchestratorWorkflowTree.set_fov_positions
        )
        self.orchestratorControlPanel.protocol_loaded.connect(
            self.orchestratorWorkflowTree.populate_from_protocol
        )

        # Connect workflow tree selection to parameter panel
        self.orchestratorWorkflowTree.tree.itemClicked.connect(
            self._on_workflow_item_clicked
        )

        # Connect warning panel navigation to workflow tree
        self.orchestratorWarningPanel.navigate_to_fov.connect(
            self._on_warning_navigate_to_fov
        )

        # Create docks
        dock_workflow = dock.Dock("Workflow", autoOrientation=False)
        dock_workflow.showTitleBar()
        dock_workflow.addWidget(self.orchestratorWorkflowTree)
        dock_workflow.setStretch(x=100, y=100)

        dock_controls = dock.Dock("Controls", autoOrientation=False)
        dock_controls.showTitleBar()
        dock_controls.addWidget(self.orchestratorControlPanel)
        dock_controls.setStretch(x=100, y=100)

        dock_params = dock.Dock("Parameters", autoOrientation=False)
        dock_params.showTitleBar()
        dock_params.addWidget(self.orchestratorParameterPanel)
        dock_params.setStretch(x=100, y=50)

        dock_warnings = dock.Dock("Warnings", autoOrientation=False)
        dock_warnings.showTitleBar()
        dock_warnings.addWidget(self.orchestratorWarningPanel)
        dock_warnings.setStretch(x=100, y=50)

        # Create dock area and arrange docks
        # Layout: Workflow (left) | Controls (top-right) / Parameters (mid-right) / Warnings (bottom-right)
        orchestrator_dockArea = dock.DockArea()
        orchestrator_dockArea.addDock(dock_workflow)
        orchestrator_dockArea.addDock(dock_controls, "right", relativeTo=dock_workflow)
        orchestrator_dockArea.addDock(dock_params, "bottom", relativeTo=dock_controls)
        orchestrator_dockArea.addDock(dock_warnings, "bottom", relativeTo=dock_params)

        self.imageDisplayTabs.addTab(orchestrator_dockArea, "Orchestrator")

    def _on_workflow_item_clicked(self, item, column) -> None:
        """Handle workflow tree item click to show parameters."""
        _ = column  # Unused
        if not hasattr(self, "orchestratorParameterPanel"):
            return

        # Get the item data to determine what type it is
        item_data = item.data(0, Qt.UserRole)
        if item_data is None:
            return

        if isinstance(item_data, dict):
            item_type = item_data.get("type", "")
            if item_type == "round":
                # Round item
                self.orchestratorParameterPanel.show_round(
                    item_data["round_index"],
                    item_data["round_data"],
                )
            elif item_type == "operation":
                # Operation item
                self.orchestratorParameterPanel.show_operation(
                    item_data.get("round_data", {}),
                    item_data["operation_data"],
                    item_data.get("op_index", 0),
                )
            elif item_type == "fov":
                # FOV item
                self.orchestratorParameterPanel.show_fov_summary(
                    fov_id=item_data["fov_id"],
                    region_id=item_data.get("region_id", ""),
                    fov_index=item_data.get("fov_index", 0),
                    x_mm=item_data.get("x_mm", 0.0),
                    y_mm=item_data.get("y_mm", 0.0),
                    status=item_data.get("status", "PENDING"),
                    z_mm=item_data.get("z_mm", 0.0),
                )

    def _on_warning_navigate_to_fov(self, fov_id: str) -> None:
        """Handle warning panel navigation request."""
        if hasattr(self, "orchestratorWorkflowTree"):
            # Try to find and select the FOV in the tree
            tree = self.orchestratorWorkflowTree.tree
            for i in range(tree.topLevelItemCount()):
                round_item = tree.topLevelItem(i)
                for j in range(round_item.childCount()):
                    op_item = round_item.child(j)
                    for k in range(op_item.childCount()):
                        fov_item = op_item.child(k)
                        item_data = fov_item.data(0, Qt.UserRole)
                        if isinstance(item_data, dict) and item_data.get("fov_id") == fov_id:
                            tree.setCurrentItem(fov_item)
                            tree.scrollToItem(fov_item)
                            return

    def setupRecordTabWidget(self) -> None:
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.recordTabWidget.addTab(
                self.wellplateMultiPointWidget, "Wellplate Multipoint"
            )
        if self._feature_flags.is_enabled("ENABLE_FLEXIBLE_MULTIPOINT"):
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
        if self._feature_flags.is_enabled("ENABLE_TRACKING"):
            self.recordTabWidget.addTab(self.trackingControlWidget, "Tracking")
        if self._feature_flags.is_enabled("ENABLE_RECORDING"):
            self.recordTabWidget.addTab(self.recordingControlWidget, "Simple Recording")
        self.recordTabWidget.currentChanged.connect(
            lambda: self.resizeCurrentTab(self.recordTabWidget)
        )
        self.resizeCurrentTab(self.recordTabWidget)

    def setupCameraTabWidget(self) -> None:
        if not self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_CONTROL") or self.live_only_mode:
            self.cameraTabWidget.addTab(self.navigationWidget, "Stages")
        if self.piezoWidget:
            self.cameraTabWidget.addTab(self.piezoWidget, "Piezo")
        if ENABLE_NL5:
            self.cameraTabWidget.addTab(self.nl5Wdiget, "NL5")
        if self._feature_flags.is_enabled("ENABLE_SPINNING_DISK_CONFOCAL"):
            self.cameraTabWidget.addTab(self.spinningDiskConfocalWidget, "Confocal")
        if self.emission_filter_wheel:
            self.cameraTabWidget.addTab(self.filterControllerWidget, "Emission Filter")
        self.cameraTabWidget.addTab(self.cameraSettingWidget, "Camera")
        self.cameraTabWidget.addTab(self.autofocusWidget, "Contrast AF")
        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
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

    def _connect_tab_signals(self) -> None:
        self.recordTabWidget.currentChanged.connect(self.onTabChanged)
        if not self.live_only_mode:
            self.imageDisplayTabs.currentChanged.connect(self.onDisplayTabChanged)

    def _connect_plot_signals(self) -> None:
        # EventBus subscriptions handled via @handles methods.
        return

    def _connect_plate_view_signals(self) -> None:
        """Connect PlateViewInit and PlateViewUpdate events to the plate view widget."""
        # EventBus subscriptions handled via @handles methods.
        return

    def _connect_well_selector_button(self) -> None:
        if hasattr(self.imageDisplayWindow, "btn_well_selector"):
            self.imageDisplayWindow.btn_well_selector.clicked.connect(
                lambda: self.toggleWellSelector(not self.dock_wellSelection.isVisible())
            )

    def _connect_laser_autofocus_signals(self) -> None:
        if not self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
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
        if self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_VIEW") and not self.live_only_mode:
            self.napari_connections["napariLiveWidget"] = [
                (
                    self.streamHandler.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(
                        image, from_autofocus=False
                    ),
                ),
            ]

            if self._feature_flags.is_enabled("USE_NAPARI_FOR_LIVE_CONTROL"):
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
            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MULTIPOINT"):
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
            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MOSAIC_DISPLAY"):
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
            if self._feature_flags.is_enabled("USE_NAPARI_FOR_MOSAIC_DISPLAY") and Nz == 1:
                self.imageDisplayTabs.setCurrentWidget(self.napariMosaicDisplayWidget)

            elif self._feature_flags.is_enabled("USE_NAPARI_FOR_MULTIPOINT"):
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
            if self._feature_flags.is_enabled("ENABLE_FLEXIBLE_MULTIPOINT")
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
        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
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

    @handles(WellplateFormatChanged)
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

    @handles(AcquisitionStateChanged)
    def _on_acquisition_state_changed(self, event: AcquisitionStateChanged) -> None:
        """Handle backend acquisition state changes (UI truth-from-backend)."""
        self._apply_acquisition_ui_state(event.in_progress)

    @handles(LiveStateChanged)
    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Switch to live tab when main camera goes live and enable alignment widget."""
        if getattr(event, "camera", "main") != "main":
            return
        if hasattr(self, "imageDisplayTabs") and event.is_live:
            self.imageDisplayTabs.setCurrentIndex(0)
        # Enable/disable alignment widget based on live state
        if self.alignmentWidget is not None:
            if event.is_live:
                self.alignmentWidget.enable()
            else:
                self.alignmentWidget.disable()

    @handles(AcquisitionCoordinates)
    def _on_acquisition_coordinates(self, event: AcquisitionCoordinates) -> None:
        if getattr(self, "zPlotWidget", None) is None:
            return
        self.zPlotWidget.add_point(event.x_mm, event.y_mm, event.z_mm, event.region_id)

    @handles(AcquisitionWorkerFinished)
    def _on_acquisition_worker_finished(self, _event: AcquisitionWorkerFinished) -> None:
        if getattr(self, "zPlotWidget", None) is None:
            return
        self.zPlotWidget.plot()

    @handles(PlateViewInit)
    def _on_plate_view_init(self, event: PlateViewInit) -> None:
        if not getattr(self, "napariPlateViewWidget", None):
            return
        self.napariPlateViewWidget.initPlateLayout(
            event.num_rows,
            event.num_cols,
            event.well_slot_shape,
            event.fov_grid_shape,
            event.channel_names,
        )

    @handles(PlateViewUpdate)
    def _on_plate_view_update(self, event: PlateViewUpdate) -> None:
        if not getattr(self, "napariPlateViewWidget", None):
            return
        self.napariPlateViewWidget.updatePlateView(
            event.channel_idx,
            event.channel_name,
            event.plate_image,
        )

    @handles(AcquisitionStarted)
    def _on_acquisition_started(self, event: AcquisitionStarted) -> None:
        """Update NDViewer tab when acquisition starts."""
        if getattr(self, "ndviewerTab", None) is None:
            return

        try:
            base_path = event.base_path
            experiment_id = event.experiment_id
            self.log.debug(f"_on_acquisition_started: base_path={base_path}, experiment_id={experiment_id}")

            if base_path and experiment_id:
                dataset_path = os.path.join(base_path, experiment_id)
                self.log.debug(f"Setting NDViewer dataset path to: {dataset_path}")
                self.ndviewerTab.set_dataset_path(dataset_path)
            else:
                self.log.debug("_on_acquisition_started: base_path or experiment_id not set in event")
        except Exception:
            self.log.exception("Failed to update NDViewer tab for new acquisition")

    def _on_plate_view_fov_clicked(self, well_id: str, fov_index: int) -> None:
        """Handle double-click on plate view: navigate NDViewer to FOV and switch tab."""
        if getattr(self, "ndviewerTab", None) is None:
            self.log.debug("FOV click ignored: NDViewer tab not available")
            return

        if not self.ndviewerTab.go_to_fov(well_id, fov_index):
            self.log.debug(f"Could not navigate to FOV well={well_id}, fov={fov_index} - may not exist in dataset")
            return

        # Switch to NDViewer tab
        ndviewer_tab_idx = self.imageDisplayTabs.indexOf(self.ndviewerTab)
        if ndviewer_tab_idx >= 0:
            self.imageDisplayTabs.setCurrentIndex(ndviewer_tab_idx)
        else:
            self.log.warning("NDViewer tab exists but not found in tab widget")

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

    def _restore_cached_camera_settings(self) -> None:
        """Restore cached camera settings (binning, pixel format) from previous session.

        Silently returns if no cached settings exist. Errors are logged but do not
        prevent application startup.
        """
        cached_settings = load_camera_settings()
        if not cached_settings:
            return

        # Apply binning
        try:
            self.camera.set_binning(*cached_settings.binning)
            self.log.info(f"Restored camera binning: {cached_settings.binning}")
        except ValueError as e:
            self.log.warning(f"Cannot restore binning {cached_settings.binning} - not supported: {e}")
        except Exception as e:
            self.log.error(f"Error restoring camera binning: {e}")

        # Apply pixel format if available
        if cached_settings.pixel_format:
            try:
                from squid.core.config import CameraPixelFormat
                pixel_format = CameraPixelFormat.from_string(cached_settings.pixel_format)
                self.camera.set_pixel_format(pixel_format)
                self.log.info(f"Restored camera pixel format: {cached_settings.pixel_format}")
            except (KeyError, ValueError) as e:
                self.log.warning(f"Cannot restore pixel format '{cached_settings.pixel_format}': {e}")
            except Exception as e:
                self.log.error(f"Error restoring camera pixel format: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Alignment Widget Setup
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_alignment_widget(self) -> None:
        """Setup the alignment widget for sample registration with previous acquisitions.

        Only created when using napari for live view.
        """
        if self.napariLiveWidget is None:
            return

        try:
            napari_viewer = self.napariLiveWidget.viewer
            if napari_viewer is None:
                self._log.warning("Napari viewer not available for alignment widget")
                return

            self.alignmentWidget = widgets.AlignmentWidget(napari_viewer)

            # Connect signals
            self.alignmentWidget.signal_move_to_position.connect(self._alignment_move_to)
            self.alignmentWidget.signal_request_current_position.connect(
                self._alignment_provide_position
            )
            self.alignmentWidget.signal_offset_set.connect(
                lambda x, y: self._log.info(f"Alignment offset set: ({x:.4f}, {y:.4f}) mm")
            )
            self.alignmentWidget.signal_offset_cleared.connect(
                lambda: self._log.info("Alignment offset cleared")
            )

            # Add alignment widget to napari viewer as a dock widget
            napari_viewer.window.add_dock_widget(
                self.alignmentWidget,
                name="Alignment",
                area="left",
            )

            # Set alignment widget on multipoint controller
            multipoint = getattr(self._controllers, "multipoint", None) if self._controllers else None
            if multipoint is not None:
                multipoint.set_alignment_widget(self.alignmentWidget)
            else:
                self._log.debug("MultiPoint controller not available for alignment widget")

            self._log.info("Alignment widget created and connected")
        except Exception:
            self._log.exception("Failed to setup alignment widget")
            self.alignmentWidget = None

    def _alignment_move_to(self, x_mm: float, y_mm: float) -> None:
        """Handle alignment widget request to move stage to position."""
        try:
            stage_service = self._services.get("stage") if self._services else None
            if stage_service is not None:
                stage_service.move_x_to(x_mm)
                stage_service.move_y_to(y_mm)
                stage_service.wait_for_idle()
                self._log.debug(f"Alignment: moved to ({x_mm:.4f}, {y_mm:.4f})")
            else:
                self._log.warning("Stage service not available for alignment move")
        except Exception:
            self._log.exception("Error during alignment move")

    def _alignment_provide_position(self) -> None:
        """Provide current stage position to alignment widget."""
        try:
            stage_service = self._services.get("stage") if self._services else None
            if stage_service is not None and self.alignmentWidget is not None:
                x_mm = stage_service.x_mm
                y_mm = stage_service.y_mm
                self.alignmentWidget.set_current_position(x_mm, y_mm)
                self._log.debug(f"Alignment: provided position ({x_mm:.4f}, {y_mm:.4f})")
            else:
                self._log.warning("Stage service or alignment widget not available")
        except Exception:
            self._log.exception("Error providing alignment position")

    def showEvent(self, event) -> None:
        """Connect warning/error handler when window is shown."""
        super().showEvent(event)
        self._connect_warning_handler()

    def _connect_warning_handler(self) -> None:
        """Connect logging handler to warning/error widget."""
        if self.warningErrorWidget is None:
            return

        self._warning_handler = squid.core.logging.BufferingHandler()
        squid.core.logging.get_logger().addHandler(self._warning_handler)
        self.warningErrorWidget.connect_handler(self._warning_handler)
        self.log.debug("Warning/error widget: connected logging handler")

    def _disconnect_warning_handler(self) -> None:
        """Disconnect logging handler from warning/error widget.

        Uses robust error handling to ensure cleanup completes even if
        individual operations fail (e.g., handler already removed).
        """
        if self._warning_handler is not None:
            try:
                squid.core.logging.get_logger().removeHandler(self._warning_handler)
            except Exception:
                pass
            if self.warningErrorWidget is not None:
                self.warningErrorWidget.disconnect_handler()
            self._warning_handler = None
            self.log.debug("Warning/error widget: disconnected logging handler")

    # ========================================================================
    # Workflow Runner
    # ========================================================================

    def _open_workflow_runner(self) -> None:
        """Open the Workflow Runner dialog (lazy creation)."""
        if not hasattr(self, "_workflow_runner_dialog") or self._workflow_runner_dialog is None:
            from squid.ui.widgets.workflow.workflow_runner_dialog import WorkflowRunnerDialog

            self._workflow_runner_dialog = WorkflowRunnerDialog(
                event_bus=self._ui_event_bus, parent=self
            )
        self._workflow_runner_dialog.show()
        self._workflow_runner_dialog.raise_()
        self._workflow_runner_dialog.activateWindow()

    @handles(WorkflowRunnerStateChanged)
    def _on_workflow_runner_state_changed(self, event: "WorkflowRunnerStateChanged") -> None:
        """Enable/disable main window controls during workflow execution."""
        new_state = event.new_state
        if new_state in ("RUNNING_SCRIPT", "RUNNING_ACQUISITION"):
            self._set_workflow_controls_enabled(False)
        elif new_state == "PAUSED":
            self._set_workflow_controls_enabled(True)
        elif new_state in ("COMPLETED", "FAILED", "ABORTED", "IDLE"):
            self._set_workflow_controls_enabled(True)

    def _set_workflow_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable main window controls during workflow execution."""
        for widget_name in (
            "navigationWidget",
            "liveControlWidget",
            "autofocusWidget",
            "objectivesWidget",
            "recordTabWidget",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

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

        if self._subscriptions and self._ui_event_bus is not None:
            auto_unsubscribe(self._subscriptions, self._ui_event_bus)
            self._subscriptions.clear()

        # Disconnect warning/error widget logging handler
        self._disconnect_warning_handler()

        # UI publishes commands only; hardware cleanup is handled by ApplicationContext.shutdown().
        self._ui_event_bus.publish(StopLiveCommand())

        if self._feature_flags.is_enabled("SUPPORT_LASER_AUTOFOCUS") and self.imageDisplayWindow_focus is not None:
            try:
                self.imageDisplayWindow_focus.close()
            except Exception:
                pass

        # Clean up NDViewer resources (file handles, timers)
        if getattr(self, "ndviewerTab", None) is not None:
            try:
                self.ndviewerTab.cleanup()
            except Exception:
                self.log.exception("Error closing NDViewer tab during shutdown")

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

        # Save camera settings for next session
        try:
            save_camera_settings(self.camera)
        except Exception as e:
            self._log.warning(f"Could not save camera settings: {e}")

        event.accept()
