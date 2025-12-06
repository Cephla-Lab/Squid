# set QT_API environment variable
import os

from control.core.autofocus import AutoFocusController
from control.core.autofocus import LaserAutofocusController
from control.core.navigation.scan_coordinates import (
    ScanCoordinates,
    ScanCoordinatesUpdate,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
)
from software.squid.services import ServiceRegistry

os.environ["QT_API"] = "pyqt5"
from typing import Any, Optional

# qt libraries
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from control._def import *

# app specific libraries
from control.widgets.nl5 import NL5Widget
from control.core.configuration import ChannelConfigurationManager
from control.core.configuration import ConfigurationManager
from control.core.configuration import ContrastManager
from control.core.autofocus import LaserAFSettingManager
from control.core.display import LiveController
from control.core.navigation import ObjectiveStore
from control.core.display import StreamHandler
from control.microcontroller import Microcontroller
from squid.abc import AbstractCamera, AbstractStage, AbstractFilterWheelController
import control.microscope
import control.widgets as widgets
import pyqtgraph.dockarea as dock
import squid.abc
import control.peripherals.cameras.camera_utils
import squid.config
import squid.logging
import control.peripherals.stage.stage_utils

log = squid.logging.get_logger(__name__)

if USE_PRIOR_STAGE:
    import control.peripherals.stage.prior
else:
    import control.peripherals.stage.cephla
from control.peripherals.piezo import PiezoStage

if USE_XERYON:
    pass

import control.core.core as core
import control.peripherals.lighting as serial_peripherals

if SUPPORT_LASER_AUTOFOCUS:
    import control.core.tracking.displacement_measurement as core_displacement_measurement

SINGLE_WINDOW = True  # set to False if use separate windows for display and control

if USE_JUPYTER_CONSOLE:
    from control.console import JupyterWidget

if RUN_FLUIDICS:
    from control.peripherals.fluidics import Fluidics

# Import the custom widget
from control.widgets.custom_multipoint import TemplateMultiPointWidget

# Import Qt controller wrappers
from control.gui.qt_controllers import (
    MovementUpdater,
    QtAutoFocusController,
    QtMultiPointController,
)

# Import helper modules for widget creation, layout, and signal connections
from control.gui import widget_factory, layout_builder, signal_connector


class HighContentScreeningGui(QMainWindow):
    fps_software_trigger = 100
    LASER_BASED_FOCUS_TAB_NAME = "Laser-Based Focus"

    def __init__(
        self,
        microscope: control.microscope.Microscope,
        services: ServiceRegistry = None,  # ServiceRegistry from ApplicationContext
        is_simulation: bool = False,
        live_only_mode: bool = False,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.log = squid.logging.get_logger(self.__class__.__name__)
        self._services = services  # Store for passing to widgets

        self.microscope: control.microscope.Microscope = microscope
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

        self.liveController_focus_camera: Optional[AbstractCamera] = None
        self.streamHandler_focus_camera: Optional[StreamHandler] = None
        self.imageDisplayWindow_focus: Optional[core.ImageDisplayWindow] = None
        self.displacementMeasurementController: Optional[
            core_displacement_measurement.DisplacementMeasurementController
        ] = None
        self.laserAutofocusController: Optional[LaserAutofocusController] = None

        if SUPPORT_LASER_AUTOFOCUS:
            self.liveController_focus_camera = self.microscope.live_controller_focus
            self.streamHandler_focus_camera = core.QtStreamHandler(
                accept_new_frame_fn=lambda: self.liveController_focus_camera.is_live
            )
            self.imageDisplayWindow_focus = core.ImageDisplayWindow(
                show_LUT=False, autoLevels=False
            )
            self.displacementMeasurementController = (
                core_displacement_measurement.DisplacementMeasurementController()
            )
            self.laserAutofocusController = LaserAutofocusController(
                self.microcontroller,
                self.camera_focus,
                self.liveController_focus_camera,
                self.stage,
                self.piezo,
                self.objectiveStore,
                self.laserAFSettingManager,
            )

        self.live_only_mode = live_only_mode or LIVE_ONLY_MODE
        self.is_live_scan_grid_on = False
        self.live_scan_grid_was_on = None
        self.performance_mode = False
        self.napari_connections = {}
        self.well_selector_visible = (
            False  # Add this line to track well selector visibility
        )

        self.multipointController: QtMultiPointController = None
        self.streamHandler: core.QtStreamHandler = None
        self.autofocusController: AutoFocusController = None
        self.imageSaver: core.ImageSaver = core.ImageSaver()
        self.imageDisplay: core.ImageDisplay = core.ImageDisplay()
        self.trackingController: core.TrackingController = None
        self.navigationViewer: core.NavigationViewer = None
        self.scanCoordinates: Optional[ScanCoordinates] = None
        self.load_objects(is_simulation=is_simulation)
        self.setup_hardware()

        self.setup_movement_updater()

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
        self.imageDisplayWindow: Optional[core.ImageDisplayWindow] = None
        self.imageDisplayWindow_focus: Optional[core.ImageDisplayWindow] = None
        self.napariMultiChannelWidget: Optional[widgets.NapariMultiChannelWidget] = None
        self.imageArrayDisplayWindow: Optional[core.ImageArrayDisplayWindow] = None
        self.zPlotWidget: Optional[widgets.SurfacePlotWidget] = None

        self.recordTabWidget: QTabWidget = QTabWidget()
        self.cameraTabWidget: QTabWidget = QTabWidget()
        self.load_widgets()
        self.setup_layout()
        self.make_connections()

        # Initialize live scan grid state
        self.wellplateMultiPointWidget.initialize_live_scan_grid_state()

        # TODO(imo): Why is moving to the cached position after boot hidden behind homing?
        if HOMING_ENABLED_X and HOMING_ENABLED_Y and HOMING_ENABLED_Z:
            if (
                cached_pos
                := control.peripherals.stage.stage_utils.get_cached_position()
            ):
                self.log.info(
                    f"Cache position exists.  Moving to: ({cached_pos.x_mm},{cached_pos.y_mm},{cached_pos.z_mm}) [mm]"
                )
                self.stage.move_x_to(cached_pos.x_mm)
                self.stage.move_y_to(cached_pos.y_mm)

                if (int(Z_HOME_SAFETY_POINT) / 1000.0) < cached_pos.z_mm:
                    self.stage.move_z_to(cached_pos.z_mm)
                else:
                    self.log.info(
                        "Cache z position is smaller than Z_HOME_SAFETY_POINT, move to Z_HOME_SAFETY_POINT"
                    )
                    self.stage.move_z_to(int(Z_HOME_SAFETY_POINT) / 1000.0)
            else:
                self.log.info(
                    "Cache position is not exists.  Moving Z axis to safety position"
                )
                control.peripherals.stage.stage_utils.move_z_axis_to_safety_position(
                    self.stage
                )

            if ENABLE_WELLPLATE_MULTIPOINT:
                self.wellplateMultiPointWidget.init_z()
            self.flexibleMultiPointWidget.init_z()

        # Create the menu bar
        menubar = self.menuBar()
        settings_menu = menubar.addMenu("Settings")
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

    def load_objects(self, is_simulation: bool) -> None:
        self.streamHandler = core.QtStreamHandler(
            accept_new_frame_fn=lambda: self.liveController.is_live
        )
        self.autofocusController = QtAutoFocusController(
            self.camera, self.stage, self.liveController, self.microcontroller, self.nl5
        )
        if ENABLE_TRACKING:
            self.trackingController = core.TrackingController(
                self.camera,
                self.microcontroller,
                self.stage,
                self.objectiveStore,
                self.channelConfigurationManager,
                self.liveController,
                self.autofocusController,
                self.imageDisplayWindow,
            )
        if WELLPLATE_FORMAT == "glass slide" and IS_HCS:
            self.navigationViewer = core.NavigationViewer(
                self.objectiveStore, self.camera, sample="4 glass slide"
            )
        else:
            self.navigationViewer = core.NavigationViewer(
                self.objectiveStore, self.camera, sample=WELLPLATE_FORMAT
            )

        def scan_coordinate_callback(update: ScanCoordinatesUpdate) -> None:
            if isinstance(update, AddScanCoordinateRegion):
                self.navigationViewer.register_fovs_to_image(update.fov_centers)
            elif isinstance(update, RemovedScanCoordinateRegion):
                self.navigationViewer.deregister_fovs_from_image(update.fov_centers)
            elif isinstance(update, ClearedScanCoordinates):
                self.navigationViewer.clear_overlay()
            if self.focusMapWidget:
                self.focusMapWidget.on_regions_updated()

        self.scanCoordinates = ScanCoordinates(
            objectiveStore=self.objectiveStore,
            stage=self.stage,
            camera=self.camera,
            update_callback=scan_coordinate_callback,
        )
        self.multipointController = QtMultiPointController(
            self.microscope,
            self.liveController,
            self.autofocusController,
            self.objectiveStore,
            self.channelConfigurationManager,
            scan_coordinates=self.scanCoordinates,
            laser_autofocus_controller=self.laserAutofocusController,
            fluidics=self.fluidics,
        )

    def setup_hardware(self) -> None:
        # Setup hardware components
        if not self.microcontroller:
            raise ValueError("Microcontroller must be none-None for hardware setup.")

        try:
            x_config = self.stage.get_config().X_AXIS
            y_config = self.stage.get_config().Y_AXIS
            z_config = self.stage.get_config().Z_AXIS
            self.log.info(
                f"Setting stage limits to:"
                f" x=[{x_config.MIN_POSITION},{x_config.MAX_POSITION}],"
                f" y=[{y_config.MIN_POSITION},{y_config.MAX_POSITION}],"
                f" z=[{z_config.MIN_POSITION},{z_config.MAX_POSITION}]"
            )

            self.stage.set_limits(
                x_pos_mm=x_config.MAX_POSITION,
                x_neg_mm=x_config.MIN_POSITION,
                y_pos_mm=y_config.MAX_POSITION,
                y_neg_mm=y_config.MIN_POSITION,
                z_pos_mm=z_config.MAX_POSITION,
                z_neg_mm=z_config.MIN_POSITION,
            )

            self.microscope.home_xyz()

        except TimeoutError as e:
            # If we can't recover from a timeout, at least do our best to make sure the system is left in a safe
            # and restartable state.
            self.log.error(
                "Setup timed out, resetting microcontroller before failing gui setup"
            )
            self.microcontroller.reset()
            raise e
        if DEFAULT_TRIGGER_MODE == TriggerMode.HARDWARE:
            print("Setting acquisition mode to HARDWARE_TRIGGER")
            self.camera.set_acquisition_mode(
                squid.abc.CameraAcquisitionMode.HARDWARE_TRIGGER
            )
        else:
            self.camera.set_acquisition_mode(
                squid.abc.CameraAcquisitionMode.SOFTWARE_TRIGGER
            )
        self.camera.add_frame_callback(self.streamHandler.get_frame_callback())
        self.camera.enable_callbacks(enabled=True)

        if self.camera_focus:
            self.camera_focus.set_acquisition_mode(
                squid.abc.CameraAcquisitionMode.SOFTWARE_TRIGGER
            )  # self.camera.set_continuous_acquisition()
            self.camera_focus.add_frame_callback(
                self.streamHandler_focus_camera.get_frame_callback()
            )
            self.camera_focus.enable_callbacks(enabled=True)
            self.camera_focus.start_streaming()

        if self.objective_changer:
            self.objective_changer.home()
            self.objective_changer.setSpeed(XERYON_SPEED)
            if DEFAULT_OBJECTIVE in XERYON_OBJECTIVE_SWITCHER_POS_1:
                self.objective_changer.moveToPosition1(move_z=False)
            elif DEFAULT_OBJECTIVE in XERYON_OBJECTIVE_SWITCHER_POS_2:
                self.objective_changer.moveToPosition2(move_z=False)

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
                self.imageDisplayWindow = core.ImageDisplayWindow(
                    self.liveController, self.contrastManager
                )
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = core.ImageDisplayWindow(
                    self.liveController,
                    self.contrastManager,
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
            self.napariLiveWidget = widgets.NapariLiveWidget(
                self.streamHandler,
                self.liveController,
                self.stage,
                self.objectiveStore,
                self.channelConfigurationManager,
                self.contrastManager,
                camera_service=self._services.get("camera"),
                wellSelectionWidget=self.wellSelectionWidget,
            )
            self.imageDisplayTabs.addTab(self.napariLiveWidget, "Live View")
        else:
            if ENABLE_TRACKING:
                self.imageDisplayWindow = core.ImageDisplayWindow(
                    self.liveController, self.contrastManager
                )
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = core.ImageDisplayWindow(
                    self.liveController,
                    self.contrastManager,
                    show_LUT=True,
                    autoLevels=True,
                )
            self.imageDisplayTabs.addTab(self.imageDisplayWindow.widget, "Live View")

        if not self.live_only_mode:
            if USE_NAPARI_FOR_MULTIPOINT:
                self.napariMultiChannelWidget = widgets.NapariMultiChannelWidget(
                    self.objectiveStore,
                    self._services.get("camera"),
                    self.contrastManager,
                )
                self.imageDisplayTabs.addTab(
                    self.napariMultiChannelWidget, "Multichannel Acquisition"
                )
            else:
                self.imageArrayDisplayWindow = core.ImageArrayDisplayWindow()
                self.imageDisplayTabs.addTab(
                    self.imageArrayDisplayWindow.widget, "Multichannel Acquisition"
                )

            if USE_NAPARI_FOR_MOSAIC_DISPLAY:
                self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(
                    self.objectiveStore,
                    self._services.get("camera"),
                    self.contrastManager,
                )
                self.imageDisplayTabs.addTab(
                    self.napariMosaicDisplayWidget, "Mosaic View"
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
        self.streamHandler.signal_new_frame_received.connect(
            self.liveController.on_new_frame
        )
        self.streamHandler.packet_image_to_write.connect(self.imageSaver.enqueue)

        # Use helper functions for signal connections
        signal_connector.connect_acquisition_signals(self)
        signal_connector.connect_live_control_signals(self)
        signal_connector.connect_slide_position_controller(self)
        signal_connector.connect_navigation_signals(self)
        signal_connector.connect_tab_signals(self)
        signal_connector.connect_display_signals(self)

        # Napari connections (complex, kept as method)
        self.makeNapariConnections()

        signal_connector.connect_wellplate_signals(self)
        signal_connector.connect_profile_signals(self)
        signal_connector.connect_laser_autofocus_signals(self)
        signal_connector.connect_confocal_signals(self)
        signal_connector.connect_plot_signals(self)
        signal_connector.connect_well_selector_button(self)

    def setup_movement_updater(self):
        # We provide a few signals about the system's physical movement to other parts of the UI.  Ideally, they other
        # parts would register their interest (instead of us needing to know that they want to hear about the movements
        # here), but as an intermediate pumping it all from one location is better than nothing.
        self.movement_updater = MovementUpdater(stage=self.stage, piezo=self.piezo)
        self.movement_update_timer = QTimer()
        self.movement_update_timer.setInterval(100)
        self.movement_update_timer.timeout.connect(self.movement_updater.do_update)
        self.movement_update_timer.start()

    def makeNapariConnections(self) -> None:
        """Initialize all Napari connections in one place"""
        self.napari_connections = {
            "napariLiveWidget": [],
            "napariMultiChannelWidget": [],
            "napariMosaicDisplayWidget": [],
        }

        # Setup live view connections
        if USE_NAPARI_FOR_LIVE_VIEW and not self.live_only_mode:
            self.napari_connections["napariLiveWidget"] = [
                (
                    self.multipointController.signal_current_configuration,
                    self.napariLiveWidget.update_ui_for_mode,
                ),
                (
                    self.autofocusController.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(
                        image, from_autofocus=True
                    ),
                ),
                (
                    self.streamHandler.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(
                        image, from_autofocus=False
                    ),
                ),
                (
                    self.multipointController.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(
                        image, from_autofocus=False
                    ),
                ),
                (
                    self.napariLiveWidget.signal_coordinates_clicked,
                    self.move_from_click_image,
                ),
                (
                    self.liveControlWidget.signal_live_configuration,
                    self.napariLiveWidget.set_live_configuration,
                ),
            ]

            if USE_NAPARI_FOR_LIVE_CONTROL:
                self.napari_connections["napariLiveWidget"].extend(
                    [
                        (
                            self.napariLiveWidget.signal_newExposureTime,
                            self.cameraSettingWidget.set_exposure_time,
                        ),
                        (
                            self.napariLiveWidget.signal_newAnalogGain,
                            self.cameraSettingWidget.set_analog_gain,
                        ),
                        (
                            self.napariLiveWidget.signal_autoLevelSetting,
                            self.imageDisplayWindow.set_autolevel,
                        ),
                    ]
                )
        else:
            # Non-Napari display connections
            self.streamHandler.image_to_display.connect(self.imageDisplay.enqueue)
            self.imageDisplay.image_to_display.connect(
                self.imageDisplayWindow.display_image
            )
            self.autofocusController.image_to_display.connect(
                self.imageDisplayWindow.display_image
            )
            self.multipointController.image_to_display.connect(
                self.imageDisplayWindow.display_image
            )
            self.liveControlWidget.signal_autoLevelSetting.connect(
                self.imageDisplayWindow.set_autolevel
            )
            self.imageDisplayWindow.image_click_coordinates.connect(
                self.move_from_click_image
            )

        if not self.live_only_mode:
            # Setup multichannel widget connections
            if USE_NAPARI_FOR_MULTIPOINT:
                self.napari_connections["napariMultiChannelWidget"] = [
                    (
                        self.multipointController.napari_layers_init,
                        self.napariMultiChannelWidget.initLayers,
                    ),
                    (
                        self.multipointController.napari_layers_update,
                        self.napariMultiChannelWidget.updateLayers,
                    ),
                ]

                if ENABLE_FLEXIBLE_MULTIPOINT:
                    self.napari_connections["napariMultiChannelWidget"].extend(
                        [
                            (
                                self.flexibleMultiPointWidget.signal_acquisition_channels,
                                self.napariMultiChannelWidget.initChannels,
                            ),
                            (
                                self.flexibleMultiPointWidget.signal_acquisition_shape,
                                self.napariMultiChannelWidget.initLayersShape,
                            ),
                        ]
                    )

                if ENABLE_WELLPLATE_MULTIPOINT:
                    self.napari_connections["napariMultiChannelWidget"].extend(
                        [
                            (
                                self.wellplateMultiPointWidget.signal_acquisition_channels,
                                self.napariMultiChannelWidget.initChannels,
                            ),
                            (
                                self.wellplateMultiPointWidget.signal_acquisition_shape,
                                self.napariMultiChannelWidget.initLayersShape,
                            ),
                        ]
                    )
                if RUN_FLUIDICS:
                    self.napari_connections["napariMultiChannelWidget"].extend(
                        [
                            (
                                self.multiPointWithFluidicsWidget.signal_acquisition_channels,
                                self.napariMultiChannelWidget.initChannels,
                            ),
                            (
                                self.multiPointWithFluidicsWidget.signal_acquisition_shape,
                                self.napariMultiChannelWidget.initLayersShape,
                            ),
                        ]
                    )
            else:
                self.multipointController.image_to_display_multi.connect(
                    self.imageArrayDisplayWindow.display_image
                )

            # Setup mosaic display widget connections
            if USE_NAPARI_FOR_MOSAIC_DISPLAY:
                self.napari_connections["napariMosaicDisplayWidget"] = [
                    (
                        self.multipointController.napari_layers_update,
                        self.napariMosaicDisplayWidget.updateMosaic,
                    ),
                    (
                        self.napariMosaicDisplayWidget.signal_coordinates_clicked,
                        self.move_from_click_mm,
                    ),
                    (
                        self.napariMosaicDisplayWidget.signal_clear_viewer,
                        self.navigationViewer.clear_slide,
                    ),
                ]

                if ENABLE_FLEXIBLE_MULTIPOINT:
                    self.napari_connections["napariMosaicDisplayWidget"].extend(
                        [
                            (
                                self.flexibleMultiPointWidget.signal_acquisition_channels,
                                self.napariMosaicDisplayWidget.initChannels,
                            ),
                            (
                                self.flexibleMultiPointWidget.signal_acquisition_shape,
                                self.napariMosaicDisplayWidget.initLayersShape,
                            ),
                        ]
                    )

                if ENABLE_WELLPLATE_MULTIPOINT:
                    self.napari_connections["napariMosaicDisplayWidget"].extend(
                        [
                            (
                                self.wellplateMultiPointWidget.signal_acquisition_channels,
                                self.napariMosaicDisplayWidget.initChannels,
                            ),
                            (
                                self.wellplateMultiPointWidget.signal_acquisition_shape,
                                self.napariMosaicDisplayWidget.initLayersShape,
                            ),
                            (
                                self.wellplateMultiPointWidget.signal_manual_shape_mode,
                                self.napariMosaicDisplayWidget.enable_shape_drawing,
                            ),
                            (
                                self.napariMosaicDisplayWidget.signal_shape_drawn,
                                self.wellplateMultiPointWidget.update_manual_shape,
                            ),
                        ]
                    )

                if RUN_FLUIDICS:
                    self.napari_connections["napariMosaicDisplayWidget"].extend(
                        [
                            (
                                self.multiPointWithFluidicsWidget.signal_acquisition_channels,
                                self.napariMosaicDisplayWidget.initChannels,
                            ),
                            (
                                self.multiPointWithFluidicsWidget.signal_acquisition_shape,
                                self.napariMosaicDisplayWidget.initLayersShape,
                            ),
                        ]
                    )

            # Make initial connections
            self.updateNapariConnections()

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
        self.scanCoordinates.clear_regions()

        if is_wellplate_acquisition:
            if (
                self.wellplateMultiPointWidget.combobox_xy_mode.currentText()
                == "Manual"
            ):
                # trigger manual shape update
                if self.wellplateMultiPointWidget.shapes_mm:
                    self.wellplateMultiPointWidget.update_manual_shape(
                        self.wellplateMultiPointWidget.shapes_mm
                    )
            else:
                # trigger wellplate update
                self.wellplateMultiPointWidget.update_coordinates()
        elif is_flexible_acquisition:
            # trigger flexible regions update
            self.flexibleMultiPointWidget.update_fov_positions()

        self.toggleWellSelector(
            is_wellplate_acquisition
            and self.wellSelectionWidget.format != "glass slide"
        )
        acquisitionWidget = self.recordTabWidget.widget(index)
        acquisitionWidget.emit_selected_channels()

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

    def onWellplateChanged(self, format_: str) -> None:
        if isinstance(format_, QVariant):
            format_ = format_.value()

        # TODO(imo): Not sure why glass slide is so special here?  It seems like it's just a "1 well plate".
        if format_ == "glass slide":
            self.toggleWellSelector(False)
            self.stageUtils.is_wellplate = False
        else:
            self.toggleWellSelector(True)
            self.stageUtils.is_wellplate = True

            # replace and reconnect new well selector
            if format_ == "1536 well plate":
                self.replaceWellSelectionWidget(widgets.Well1536SelectionWidget())
                self.connectWellSelectionWidget()
            elif isinstance(self.wellSelectionWidget, widgets.Well1536SelectionWidget):
                self.replaceWellSelectionWidget(
                    widgets.WellSelectionWidget(format_, self.wellplateFormatWidget)
                )
                self.connectWellSelectionWidget()

        if ENABLE_FLEXIBLE_MULTIPOINT:  # clear regions
            self.flexibleMultiPointWidget.clear_only_location_list()
        if (
            ENABLE_WELLPLATE_MULTIPOINT
        ):  # reset regions onto new wellplate with default size/shape
            self.scanCoordinates.clear_regions()
            self.wellplateMultiPointWidget.set_default_scan_size()

    def toggle_live_scan_grid(self, on: bool) -> None:
        if on:
            self.movement_updater.position_after_move.connect(
                self.wellplateMultiPointWidget.update_live_coordinates
            )
            self.is_live_scan_grid_on = True
        else:
            try:
                self.movement_updater.position_after_move.disconnect(
                    self.wellplateMultiPointWidget.update_live_coordinates
                )
            except TypeError:
                # Signal was not connected, ignore
                pass
            self.is_live_scan_grid_on = False

    def replaceWellSelectionWidget(
        self, new_widget: widgets.WellSelectionWidget
    ) -> None:
        self.wellSelectionWidget.setParent(None)
        self.wellSelectionWidget.deleteLater()
        self.wellSelectionWidget = new_widget
        self.scanCoordinates.add_well_selector(self.wellSelectionWidget)
        if (
            USE_NAPARI_WELL_SELECTION
            and not self.performance_mode
            and not self.live_only_mode
        ):
            self.napariLiveWidget.replace_well_selector(self.wellSelectionWidget)
        else:
            self.dock_wellSelection.addWidget(self.wellSelectionWidget)

    def connectWellSelectionWidget(self):
        self.wellSelectionWidget.signal_wellSelectedPos.connect(self.move_to_mm)
        self.wellplateFormatWidget.signalWellplateSettings.connect(
            self.wellSelectionWidget.onWellplateChanged
        )
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.wellSelectionWidget.signal_wellSelected.connect(
                self.wellplateMultiPointWidget.update_well_coordinates
            )

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

    def toggleAcquisitionStart(self, acquisition_started: bool) -> None:
        self.log.debug(f"toggleAcquisitionStarted({acquisition_started=})")
        if acquisition_started:
            self.log.info("STARTING ACQUISITION")
            if self.is_live_scan_grid_on:
                self.toggle_live_scan_grid(on=False)
                self.live_scan_grid_was_on = True
            else:
                self.live_scan_grid_was_on = False
        else:
            self.log.info("FINISHED ACQUISITION")
            if self.live_scan_grid_was_on:
                self.toggle_live_scan_grid(on=True)
                self.live_scan_grid_was_on = False

        # click to move off during acquisition
        self.navigationWidget.set_click_to_move(not acquisition_started)

        # disable other acqusiition tabs during acquisition
        current_index = self.recordTabWidget.currentIndex()
        for index in range(self.recordTabWidget.count()):
            self.recordTabWidget.setTabEnabled(
                index, not acquisition_started or index == current_index
            )

        # disable autolevel once acquisition started
        if acquisition_started:
            self.liveControlWidget.toggle_autolevel(not acquisition_started)

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

        # display acquisition progress bar during acquisition
        self.recordTabWidget.currentWidget().display_progress_bar(acquisition_started)

    def onStartLive(self) -> None:
        self.imageDisplayTabs.setCurrentIndex(0)

    def move_from_click_image(
        self, click_x: float, click_y: float, image_width: int, image_height: int
    ) -> None:
        if self.navigationWidget.get_click_to_move_enabled():
            pixel_size_um = (
                self.objectiveStore.get_pixel_size_factor()
                * self.camera.get_pixel_size_binned_um()
            )

            pixel_sign_x = 1
            pixel_sign_y = 1 if INVERTED_OBJECTIVE else -1

            delta_x = pixel_sign_x * pixel_size_um * click_x / 1000.0
            delta_y = pixel_sign_y * pixel_size_um * click_y / 1000.0

            self.log.debug(
                f"Click to move enabled, click at {click_x=}, {click_y=} results in relative move of {delta_x=} [mm], {delta_y=} [mm]"
            )
            self.stage.move_x(delta_x)
            self.stage.move_y(delta_y)
        else:
            self.log.debug(
                f"Click to move disabled, ignoring click at {click_x=}, {click_y=}"
            )

    def move_from_click_mm(self, x_mm: float, y_mm: float) -> None:
        if self.navigationWidget.get_click_to_move_enabled():
            self.log.debug(f"Click to move enabled, moving to {x_mm=}, {y_mm=}")
            self.move_to_mm(x_mm, y_mm)
        else:
            self.log.debug(
                f"Click to move disabled, ignoring click request for {x_mm=}, {y_mm=}"
            )

    def move_to_mm(self, x_mm: float, y_mm: float) -> None:
        self.stage.move_x_to(x_mm)
        self.stage.move_y_to(y_mm)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Show confirmation dialog
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

        try:
            control.peripherals.stage.stage_utils.cache_position(
                pos=self.stage.get_pos(), stage_config=self.stage.get_config()
            )
        except ValueError as e:
            self.log.error(
                f"Couldn't cache position while closing.  Ignoring and continuing. Error is: {e}"
            )
        self.movement_update_timer.stop()

        if self.emission_filter_wheel:
            self.emission_filter_wheel.set_filter_wheel_position({1: 1})
            self.emission_filter_wheel.close()
        if SUPPORT_LASER_AUTOFOCUS:
            self.liveController_focus_camera.stop_live()
            self.imageDisplayWindow_focus.close()

        self.liveController.stop_live()
        self.camera.stop_streaming()
        self.camera.close()

        # retract z
        self.stage.move_z_to(OBJECTIVE_RETRACTED_POS_MM)

        # reset objective changer
        if USE_XERYON:
            self.objective_changer.moveToZero()

        self.microcontroller.turn_off_all_pid()

        if ENABLE_CELLX:
            for channel in [1, 2, 3, 4]:
                self.cellx.turn_off(channel)
            self.cellx.close()

        if RUN_FLUIDICS:
            self.fluidics.close()

        self.imageSaver.close()
        self.imageDisplay.close()
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
