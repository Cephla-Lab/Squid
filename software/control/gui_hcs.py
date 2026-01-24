# set QT_API environment variable
import os
from configparser import ConfigParser

from control.core.auto_focus_controller import AutoFocusController
from control.core.job_processing import CaptureInfo
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.scan_coordinates import (
    ScanCoordinates,
    ScanCoordinatesUpdate,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
)

os.environ["QT_API"] = "pyqt5"
import serial
import time
from typing import Any, Optional, Tuple
import numpy as np

# qt libraries
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from control._def import *

# app specific libraries
from control.NL5Widget import NL5Widget
from control.core.contrast_manager import ContrastManager
from control.core.live_controller import LiveController
from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_utils import (
    MultiPointControllerFunctions,
    AcquisitionParameters,
    OverallProgressUpdate,
    RegionProgressUpdate,
    PlateViewInit,
    PlateViewUpdate,
)
from control.core.objective_store import ObjectiveStore
from control.core.stream_handler import StreamHandler
from control.lighting import LightSourceType, IntensityControlMode, ShutterControlMode, IlluminationController
from control.microcontroller import Microcontroller
from control.microscope import Microscope
from control.models import AcquisitionChannel
from squid.abc import AbstractCamera, AbstractStage, AbstractFilterWheelController
import control._def
import control.lighting
import control.utils_acquisition
import control.microscope
import control.widgets as widgets
import pyqtgraph.dockarea as dock
import squid.abc
import squid.camera.settings_cache
import squid.camera.utils
import squid.config
import squid.logging
import squid.stage.utils

log = squid.logging.get_logger(__name__)

if USE_PRIOR_STAGE:
    import squid.stage.prior
else:
    import squid.stage.cephla
from control.piezo import PiezoStage

if USE_XERYON:
    from control.objective_changer_2_pos_controller import (
        ObjectiveChanger2PosController,
        ObjectiveChanger2PosController_Simulation,
    )

import control.core.core as core
import control.microcontroller as microcontroller
import control.serial_peripherals as serial_peripherals

if SUPPORT_LASER_AUTOFOCUS:
    import control.core_displacement_measurement as core_displacement_measurement

SINGLE_WINDOW = True  # set to False if use separate windows for display and control

if USE_JUPYTER_CONSOLE:
    from control.console import JupyterWidget

if RUN_FLUIDICS:
    from control.fluidics import Fluidics

# Import the custom widget
from control.custom_multipoint_widget import TemplateMultiPointWidget

# Slack notifications
from control.slack_notifier import SlackNotifier, TimepointStats, AcquisitionStats
from control.widgets_slack import SlackSettingsDialog, load_slack_settings_from_cache


class MovementUpdater(QObject):
    position_after_move = Signal(squid.abc.Pos)
    position = Signal(squid.abc.Pos)
    piezo_z_um = Signal(float)

    def __init__(
        self, stage: AbstractStage, piezo: Optional[PiezoStage], movement_threshhold_mm=0.0001, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.stage: AbstractStage = stage
        self.piezo: Optional[PiezoStage] = piezo
        self.movement_threshhold_mm = movement_threshhold_mm
        self.previous_pos: Optional[squid.abc.Pos] = None
        self.previous_piezo_pos: Optional[float] = None
        self.sent_after_stopped = False

    def do_update(self):
        if self.piezo:
            if not self.previous_piezo_pos:
                self.previous_piezo_pos = self.piezo.position
            else:
                current_piezo_position = self.piezo.position
                if self.previous_piezo_pos != current_piezo_position:
                    self.previous_piezo_pos = current_piezo_position
                    self.piezo_z_um.emit(current_piezo_position)

        pos = self.stage.get_pos()
        # Doing previous_pos initialization like this means we technically miss the first real update,
        # but that's okay since this is intended to be run frequently in the background.
        if not self.previous_pos:
            self.previous_pos = pos
            return

        abs_delta_x = abs(self.previous_pos.x_mm - pos.x_mm)
        abs_delta_y = abs(self.previous_pos.y_mm - pos.y_mm)

        if (
            abs_delta_y < self.movement_threshhold_mm
            and abs_delta_x < self.movement_threshhold_mm
            and not self.stage.get_state().busy
        ):
            # In here, send all the signals that must be sent once per stop of movement.  AKA once per arriving at a
            # new position for a while.
            self.sent_after_stopped = True
            self.position_after_move.emit(pos)
        else:
            self.sent_after_stopped = False

        # Here, emit all the signals that want higher fidelity movement updates.
        self.position.emit(pos)

        self.previous_pos = pos


class QtAutoFocusController(AutoFocusController, QObject):
    autofocusFinished = Signal()
    image_to_display = Signal(np.ndarray)

    def __init__(
        self,
        camera: AbstractCamera,
        stage: AbstractStage,
        liveController: LiveController,
        microcontroller: Microcontroller,
        nl5: Optional[control.microscope.NL5],
    ):
        QObject.__init__(self)
        AutoFocusController.__init__(
            self,
            camera,
            stage,
            liveController,
            microcontroller,
            lambda: self.autofocusFinished.emit(),
            lambda image: self.image_to_display.emit(image),
            nl5,
        )


class QtMultiPointController(MultiPointController, QObject):
    acquisition_finished = Signal()
    signal_acquisition_start = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(AcquisitionChannel)
    signal_register_current_fov = Signal(float, float)
    napari_layers_init = Signal(int, int, object)
    napari_layers_update = Signal(np.ndarray, float, float, int, str)  # image, x_mm, y_mm, k, channel
    signal_set_display_tabs = Signal(list, int, str)  # configs: list, Nz: int, xy_mode: str
    signal_acquisition_progress = Signal(int, int, int)
    signal_region_progress = Signal(int, int)
    signal_coordinates = Signal(float, float, float, int)  # x, y, z, region
    # Plate view signals
    plate_view_init = Signal(int, int, tuple, tuple, list)  # rows, cols, well_slot_shape, fov_grid_shape, channel_names
    plate_view_update = Signal(int, str, np.ndarray)  # channel_idx, channel_name, plate_image
    # Slack notification signals (allows main thread to capture screenshot and maintain ordering)
    signal_slack_timepoint = Signal(object)  # TimepointStats
    signal_slack_acq_finished = Signal(object)  # AcquisitionStats
    # NDViewer push-based API signals
    ndviewer_start_acquisition = Signal(list, int, int, int, list)  # channels, num_z, height, width, fov_labels
    ndviewer_register_image = Signal(int, int, int, str, str)  # t, fov_idx, z, channel, filepath

    def __init__(
        self,
        microscope: Microscope,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        scan_coordinates: Optional[ScanCoordinates] = None,
        laser_autofocus_controller: Optional[LaserAutofocusController] = None,
        fluidics: Optional[Any] = None,
        alignment_widget=None,
    ):
        MultiPointController.__init__(
            self,
            microscope=microscope,
            live_controller=live_controller,
            autofocus_controller=autofocus_controller,
            objective_store=objective_store,
            callbacks=MultiPointControllerFunctions(
                signal_acquisition_start=self._signal_acquisition_start_fn,
                signal_acquisition_finished=self._signal_acquisition_finished_fn,
                signal_new_image=self._signal_new_image_fn,
                signal_current_configuration=self._signal_current_configuration_fn,
                signal_current_fov=self._signal_current_fov_fn,
                signal_overall_progress=self._signal_overall_progress_fn,
                signal_region_progress=self._signal_region_progress_fn,
                signal_plate_view_init=self._signal_plate_view_init_fn,
                signal_plate_view_update=self._signal_plate_view_update_fn,
                signal_slack_timepoint_notification=self._signal_slack_timepoint_notification_fn,
                signal_slack_acquisition_finished=self._signal_slack_acquisition_finished_fn,
            ),
            scan_coordinates=scan_coordinates,
            laser_autofocus_controller=laser_autofocus_controller,
            alignment_widget=alignment_widget,
        )
        QObject.__init__(self)

        self._napari_inited_for_this_acquisition = False
        # NDViewer push-based API state
        self._ndviewer_fov_labels: list = []  # ["A1:0", "A1:1", "A2:0", ...]
        self._ndviewer_region_fov_offset: dict = {}  # {"A1": 0, "A2": 5, ...} for flat FOV index

    def _signal_acquisition_start_fn(self, parameters: AcquisitionParameters):
        # TODO mpc napari signals
        self._napari_inited_for_this_acquisition = False
        if not self.run_acquisition_current_fov:
            self.signal_set_display_tabs.emit(self.selected_configurations, self.NZ, self.xy_mode)
        else:
            self.signal_set_display_tabs.emit(self.selected_configurations, 2, self.xy_mode)
        self.signal_acquisition_start.emit()

        # NDViewer push-based API: emit start_acquisition signal
        scan_info = parameters.scan_position_information
        channels = [cfg.name for cfg in parameters.selected_configurations]
        num_z = parameters.NZ

        # Build FOV labels and region offset mapping
        self._ndviewer_fov_labels = []
        self._ndviewer_region_fov_offset = {}
        fov_idx = 0
        for region_name in scan_info.scan_region_names:
            self._ndviewer_region_fov_offset[region_name] = fov_idx
            num_fovs = len(scan_info.scan_region_fov_coords_mm.get(region_name, []))
            for i in range(num_fovs):
                self._ndviewer_fov_labels.append(f"{region_name}:{i}")
                fov_idx += 1

        # Get image dimensions from camera (after binning)
        width, height = self.microscope.camera.get_resolution()

        self.ndviewer_start_acquisition.emit(channels, num_z, height, width, self._ndviewer_fov_labels)

    def _signal_acquisition_finished_fn(self):
        self.acquisition_finished.emit()
        finish_pos = self.stage.get_pos()
        self.signal_register_current_fov.emit(finish_pos.x_mm, finish_pos.y_mm)

    def _signal_new_image_fn(self, frame: squid.abc.CameraFrame, info: CaptureInfo):
        self.image_to_display.emit(frame.frame)
        if not USE_NAPARI_FOR_MULTIPOINT:
            ill_config = self.microscope.config_repo.get_illumination_config()
            source_code = info.configuration.get_illumination_source_code(ill_config) if ill_config else 0
            self.image_to_display_multi.emit(frame.frame, source_code)
        # Z for plot in μm: piezo-only uses piezo position, mixed mode combines stepper + piezo
        stepper_z_um = info.position.z_mm * 1000
        if IS_PIEZO_ONLY:
            z_for_plot = info.z_piezo_um if info.z_piezo_um is not None else 0
        elif info.z_piezo_um is not None:
            z_for_plot = stepper_z_um + info.z_piezo_um
        else:
            z_for_plot = stepper_z_um
        self.signal_coordinates.emit(info.position.x_mm, info.position.y_mm, z_for_plot, info.region_id)

        if not self._napari_inited_for_this_acquisition:
            self._napari_inited_for_this_acquisition = True
            self.napari_layers_init.emit(frame.frame.shape[0], frame.frame.shape[1], frame.frame.dtype)

        objective_magnification = str(int(self.objectiveStore.get_current_objective_info()["magnification"]))
        napri_layer_name = objective_magnification + "x " + info.configuration.name
        self.napari_layers_update.emit(
            frame.frame, info.position.x_mm, info.position.y_mm, info.z_index, napri_layer_name
        )

        # NDViewer push-based API: register image file
        # Compute flat FOV index from region and fov within region
        region_offset = self._ndviewer_region_fov_offset.get(info.region_id)
        if region_offset is None:
            # This should not happen if start_acquisition was called correctly
            self.log.warning(
                f"Unknown region_id '{info.region_id}' in NDViewer registration. "
                f"Available: {list(self._ndviewer_region_fov_offset.keys())}. Skipping."
            )
            return
        flat_fov_idx = region_offset + info.fov

        filepath = control.utils_acquisition.get_image_filepath(
            info.save_directory, info.file_id, info.configuration.name, frame.frame.dtype
        )

        self.ndviewer_register_image.emit(
            info.time_point, flat_fov_idx, info.z_index, info.configuration.name, filepath
        )

    def _signal_current_configuration_fn(self, config: AcquisitionChannel):
        self.signal_current_configuration.emit(config)

    def _signal_current_fov_fn(self, x_mm: float, y_mm: float):
        self.signal_register_current_fov.emit(x_mm, y_mm)

    def _signal_overall_progress_fn(self, overall_progress: OverallProgressUpdate):
        self.signal_acquisition_progress.emit(
            overall_progress.current_region, overall_progress.total_regions, overall_progress.current_timepoint
        )

    def _signal_region_progress_fn(self, region_progress: RegionProgressUpdate):
        self.signal_region_progress.emit(region_progress.current_fov, region_progress.region_fovs)

    def _signal_plate_view_init_fn(self, plate_view_init: PlateViewInit):
        self.plate_view_init.emit(
            plate_view_init.num_rows,
            plate_view_init.num_cols,
            plate_view_init.well_slot_shape,
            plate_view_init.fov_grid_shape,
            plate_view_init.channel_names,
        )

    def _signal_plate_view_update_fn(self, plate_view_update: PlateViewUpdate):
        self.plate_view_update.emit(
            plate_view_update.channel_idx,
            plate_view_update.channel_name,
            plate_view_update.plate_image,
        )

    def _signal_slack_timepoint_notification_fn(self, stats: TimepointStats):
        self.signal_slack_timepoint.emit(stats)

    def _signal_slack_acquisition_finished_fn(self, stats: AcquisitionStats):
        self.signal_slack_acq_finished.emit(stats)


class HighContentScreeningGui(QMainWindow):
    fps_software_trigger = 100
    LASER_BASED_FOCUS_TAB_NAME = "Laser-Based Focus"
    signal_performance_mode_changed = Signal(bool)

    def __init__(
        self, microscope: control.microscope.Microscope, is_simulation=False, live_only_mode=False, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)

        self.log = squid.logging.get_logger(self.__class__.__name__)

        self.microscope: control.microscope.Microscope = microscope
        self.stage: AbstractStage = microscope.stage
        self.camera: AbstractCamera = microscope.camera
        self.microcontroller: Microcontroller = microscope.low_level_drivers.microcontroller

        self.xlight: Optional[serial_peripherals.XLight] = microscope.addons.xlight
        self.dragonfly: Optional[serial_peripherals.Dragonfly] = microscope.addons.dragonfly
        self.nl5: Optional[Any] = microscope.addons.nl5
        self.cellx: Optional[serial_peripherals.CellX] = microscope.addons.cellx
        self.emission_filter_wheel: Optional[serial_peripherals.Optospin | serial_peripherals.FilterController] = (
            microscope.addons.emission_filter_wheel
        )
        self.objective_changer: Optional[Any] = microscope.addons.objective_changer
        self.camera_focus: Optional[AbstractCamera] = microscope.addons.camera_focus
        self.fluidics: Optional[Fluidics] = microscope.addons.fluidics
        self.piezo: Optional[PiezoStage] = microscope.addons.piezo_stage

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
            self.imageDisplayWindow_focus = core.ImageDisplayWindow(show_LUT=False, autoLevels=False)
            self.displacementMeasurementController = core_displacement_measurement.DisplacementMeasurementController()
            self.laserAutofocusController = LaserAutofocusController(
                self.microcontroller,
                self.camera_focus,
                self.liveController_focus_camera,
                self.stage,
                self.piezo,
                self.objectiveStore,
            )

        self.live_only_mode = live_only_mode or LIVE_ONLY_MODE
        self.is_live_scan_grid_on = False
        self.live_scan_grid_was_on = None
        self.performance_mode = False
        self.napari_connections = {}
        self.well_selector_visible = False  # Add this line to track well selector visibility

        self.multipointController: QtMultiPointController = None
        self.streamHandler: core.QtStreamHandler = None
        self.autofocusController: AutoFocusController = None
        self.imageSaver: core.ImageSaver = core.ImageSaver()
        self.imageDisplay: core.ImageDisplay = core.ImageDisplay()
        self.trackingController: core.TrackingController = None
        self.navigationViewer: core.NavigationViewer = None
        self.scanCoordinates: Optional[ScanCoordinates] = None
        self.slackNotifier: Optional[SlackNotifier] = None
        self.slackSettingsDialog: Optional[SlackSettingsDialog] = None

        # Load Slack settings from cache
        load_slack_settings_from_cache()

        self.load_objects(is_simulation=is_simulation)
        self.setup_hardware()

        self.setup_movement_updater()

        # Pre-declare and give types to all our widgets so type hinting tools work.  You should
        # add to this as you add widgets.
        self.spinningDiskConfocalWidget: Optional[widgets.SpinningDiskConfocalWidget] = None
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
        self.cameraSettingWidget_focus_camera: Optional[widgets.CameraSettingsWidget] = None
        self.laserAutofocusSettingWidget: Optional[widgets.LaserAutofocusSettingWidget] = None
        self.waveformDisplay: Optional[widgets.WaveformDisplay] = None
        self.displacementMeasurementWidget: Optional[widgets.DisplacementMeasurementWidget] = None
        self.laserAutofocusControlWidget: Optional[widgets.LaserAutofocusControlWidget] = None
        self.fluidicsWidget: Optional[widgets.FluidicsWidget] = None
        self.flexibleMultiPointWidget: Optional[widgets.FlexibleMultiPointWidget] = None
        self.wellplateMultiPointWidget: Optional[widgets.WellplateMultiPointWidget] = None
        self.templateMultiPointWidget: Optional[TemplateMultiPointWidget] = None
        self.multiPointWithFluidicsWidget: Optional[widgets.MultiPointWithFluidicsWidget] = None
        self.sampleSettingsWidget: Optional[widgets.SampleSettingsWidget] = None
        self.trackingControlWidget: Optional[widgets.TrackingControllerWidget] = None
        self.napariLiveWidget: Optional[widgets.NapariLiveWidget] = None
        self.alignmentWidget: Optional[widgets.AlignmentWidget] = None
        self.imageDisplayWindow: Optional[core.ImageDisplayWindow] = None
        self.imageDisplayWindow_focus: Optional[core.ImageDisplayWindow] = None
        self.napariMultiChannelWidget: Optional[widgets.NapariMultiChannelWidget] = None
        self.imageArrayDisplayWindow: Optional[core.ImageArrayDisplayWindow] = None
        self.zPlotWidget: Optional[widgets.SurfacePlotWidget] = None
        self.ramMonitorWidget: Optional[widgets.RAMMonitorWidget] = None
        self.backpressureMonitorWidget: Optional[widgets.BackpressureMonitorWidget] = None

        self.recordTabWidget: QTabWidget = QTabWidget()
        self.cameraTabWidget: QTabWidget = QTabWidget()
        self.load_widgets()
        self.setup_layout()
        self.make_connections()

        # Emit initial performance mode state to sync widgets
        self.signal_performance_mode_changed.emit(self.performance_mode)

        # Initialize live scan grid state
        self.wellplateMultiPointWidget.initialize_live_scan_grid_state()

        # Initialize Slack notifier
        self._setup_slack_notifier()

        # TODO(imo): Why is moving to the cached position after boot hidden behind homing?
        if HOMING_ENABLED_X and HOMING_ENABLED_Y and HOMING_ENABLED_Z:
            if cached_pos := squid.stage.utils.get_cached_position():
                self.log.info(
                    f"Cache position exists.  Moving to: ({cached_pos.x_mm},{cached_pos.y_mm},{cached_pos.z_mm}) [mm]"
                )
                self.stage.move_x_to(cached_pos.x_mm)
                self.stage.move_y_to(cached_pos.y_mm)

                if (int(Z_HOME_SAFETY_POINT) / 1000.0) < cached_pos.z_mm:
                    self.stage.move_z_to(cached_pos.z_mm)
                else:
                    self.log.info(f"Cache z position is smaller than Z_HOME_SAFETY_POINT, move to Z_HOME_SAFETY_POINT")
                    self.stage.move_z_to(int(Z_HOME_SAFETY_POINT) / 1000.0)
            else:
                self.log.info(f"Cache position is not exists.  Moving Z axis to safety position")
                squid.stage.utils.move_z_axis_to_safety_position(self.stage)

            if ENABLE_WELLPLATE_MULTIPOINT:
                self.wellplateMultiPointWidget.init_z()
            self.flexibleMultiPointWidget.init_z()

        # Create the menu bar
        menubar = self.menuBar()
        settings_menu = menubar.addMenu("Settings")

        # Preferences action
        config_action = QAction("Preferences...", self)
        config_action.setMenuRole(QAction.NoRole)
        config_action.triggered.connect(self.openPreferences)
        settings_menu.addAction(config_action)

        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            led_matrix_action = QAction("LED Matrix", self)
            led_matrix_action.triggered.connect(self.openLedMatrixSettings)
            settings_menu.addAction(led_matrix_action)

        # Slack notifications
        slack_action = QAction("Slack Notifications...", self)
        slack_action.setMenuRole(QAction.NoRole)
        slack_action.triggered.connect(self.openSlackSettings)
        settings_menu.addAction(slack_action)

        # Advanced submenu
        advanced_menu = settings_menu.addMenu("Advanced")

        # Illumination Channel Configuration (in Advanced menu)
        channel_config_action = QAction("Illumination Channel Configuration", self)
        channel_config_action.triggered.connect(self.openChannelConfigurationEditor)
        advanced_menu.addAction(channel_config_action)

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

    def load_objects(self, is_simulation):
        self.streamHandler = core.QtStreamHandler(accept_new_frame_fn=lambda: self.liveController.is_live)
        self.autofocusController = QtAutoFocusController(
            self.camera, self.stage, self.liveController, self.microcontroller, self.nl5
        )
        if ENABLE_TRACKING:
            self.trackingController = core.TrackingController(
                self.camera,
                self.microcontroller,
                self.stage,
                self.objectiveStore,
                self.liveController,
                self.autofocusController,
                self.imageDisplayWindow,
            )
        if WELLPLATE_FORMAT == "glass slide" and IS_HCS:
            self.navigationViewer = core.NavigationViewer(self.objectiveStore, self.camera, sample="4 glass slide")
        else:
            self.navigationViewer = core.NavigationViewer(self.objectiveStore, self.camera, sample=WELLPLATE_FORMAT)

        def scan_coordinate_callback(update: ScanCoordinatesUpdate):
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
            scan_coordinates=self.scanCoordinates,
            laser_autofocus_controller=self.laserAutofocusController,
            fluidics=self.fluidics,
        )

    def setup_hardware(self):
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
            self.log.error("Setup timed out, resetting microcontroller before failing gui setup")
            self.microcontroller.reset()
            raise e
        if DEFAULT_TRIGGER_MODE == TriggerMode.HARDWARE:
            print("Setting acquisition mode to HARDWARE_TRIGGER")
            self.camera.set_acquisition_mode(squid.abc.CameraAcquisitionMode.HARDWARE_TRIGGER)
        else:
            self.camera.set_acquisition_mode(squid.abc.CameraAcquisitionMode.SOFTWARE_TRIGGER)
        self.camera.add_frame_callback(self.streamHandler.get_frame_callback())
        self.camera.enable_callbacks(enabled=True)

        if self.camera_focus:
            self.camera_focus.set_acquisition_mode(
                squid.abc.CameraAcquisitionMode.SOFTWARE_TRIGGER
            )  # self.camera.set_continuous_acquisition()
            self.camera_focus.add_frame_callback(self.streamHandler_focus_camera.get_frame_callback())
            self.camera_focus.enable_callbacks(enabled=True)
            self.camera_focus.start_streaming()

        if self.objective_changer:
            self.objective_changer.home()
            self.objective_changer.setSpeed(XERYON_SPEED)
            if DEFAULT_OBJECTIVE in XERYON_OBJECTIVE_SWITCHER_POS_1:
                self.objective_changer.moveToPosition1(move_z=False)
            elif DEFAULT_OBJECTIVE in XERYON_OBJECTIVE_SWITCHER_POS_2:
                self.objective_changer.moveToPosition2(move_z=False)

    def waitForMicrocontroller(self, timeout=5.0, error_message=None):
        try:
            self.microcontroller.wait_till_operation_is_completed(timeout)
        except TimeoutError as e:
            self.log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def load_widgets(self):
        # Initialize all GUI widgets
        if ENABLE_SPINNING_DISK_CONFOCAL:
            # TODO: For user compatibility, when ENABLE_SPINNING_DISK_CONFOCAL is True, we use XLight/Cicero on default.
            # This needs to be changed when we figure out better machine configuration structure.
            if USE_DRAGONFLY:
                self.spinningDiskConfocalWidget = widgets.DragonflyConfocalWidget(self.dragonfly)
            else:
                self.spinningDiskConfocalWidget = widgets.SpinningDiskConfocalWidget(self.xlight)
        if ENABLE_NL5:
            import control.NL5Widget as NL5Widget

            self.nl5Wdiget = NL5Widget.NL5Widget(self.nl5)

        if CAMERA_TYPE in ["Toupcam", "Tucsen", "Kinetix"]:
            self.cameraSettingWidget = widgets.CameraSettingsWidget(
                self.camera,
                include_gain_exposure_time=False,
                include_camera_temperature_setting=True,
                include_camera_auto_wb_setting=False,
            )
        else:
            self.cameraSettingWidget = widgets.CameraSettingsWidget(
                self.camera,
                include_gain_exposure_time=False,
                include_camera_temperature_setting=False,
                include_camera_auto_wb_setting=True,
            )

        self._restore_cached_camera_settings()

        self.profileWidget = widgets.ProfileWidget(self.microscope.config_repo)
        self.liveControlWidget = widgets.LiveControlWidget(
            self.streamHandler,
            self.liveController,
            self.objectiveStore,
            show_display_options=False,
            show_autolevel=True,
            autolevel=True,
        )
        self.navigationWidget = widgets.NavigationWidget(
            self.stage, widget_configuration=f"{WELLPLATE_FORMAT} well plate"
        )
        self.stageUtils = widgets.StageUtils(self.stage, self.liveController, is_wellplate=True)
        self.dacControlWidget = widgets.DACControWidget(self.microcontroller)
        self.autofocusWidget = widgets.AutoFocusWidget(self.autofocusController)
        if self.piezo:
            self.piezoWidget = widgets.PiezoWidget(self.piezo)

        if USE_XERYON:
            self.objectivesWidget = widgets.ObjectivesWidget(self.objectiveStore, self.objective_changer)
        else:
            self.objectivesWidget = widgets.ObjectivesWidget(self.objectiveStore)

        if self.emission_filter_wheel:
            self.filterControllerWidget = widgets.FilterControllerWidget(
                self.emission_filter_wheel, self.liveController
            )

        self.recordingControlWidget = widgets.RecordingWidget(self.streamHandler, self.imageSaver)
        self.wellplateFormatWidget = widgets.WellplateFormatWidget(
            self.stage, self.navigationViewer, self.streamHandler, self.liveController
        )
        if WELLPLATE_FORMAT != "1536 well plate":
            self.wellSelectionWidget = widgets.WellSelectionWidget(WELLPLATE_FORMAT, self.wellplateFormatWidget)
        else:
            self.wellSelectionWidget = widgets.Well1536SelectionWidget(self.wellplateFormatWidget)
        self.scanCoordinates.add_well_selector(self.wellSelectionWidget)
        self.focusMapWidget = widgets.FocusMapWidget(
            self.stage, self.navigationViewer, self.scanCoordinates, core.FocusMap()
        )

        if SUPPORT_LASER_AUTOFOCUS:
            if FOCUS_CAMERA_TYPE == "Toupcam":
                self.cameraSettingWidget_focus_camera = widgets.CameraSettingsWidget(
                    self.camera_focus,
                    include_gain_exposure_time=False,
                    include_camera_temperature_setting=True,
                    include_camera_auto_wb_setting=False,
                )
            else:
                self.cameraSettingWidget_focus_camera = widgets.CameraSettingsWidget(
                    self.camera_focus,
                    include_gain_exposure_time=False,
                    include_camera_temperature_setting=False,
                    include_camera_auto_wb_setting=True,
                )
            self.laserAutofocusSettingWidget = widgets.LaserAutofocusSettingWidget(
                self.streamHandler_focus_camera,
                self.liveController_focus_camera,
                self.laserAutofocusController,
                stretch=False,
            )  # ,show_display_options=True)
            self.waveformDisplay = widgets.WaveformDisplay(N=1000, include_x=True, include_y=False)
            self.displacementMeasurementWidget = widgets.DisplacementMeasurementWidget(
                self.displacementMeasurementController, self.waveformDisplay
            )
            self.laserAutofocusControlWidget: widgets.LaserAutofocusControlWidget = widgets.LaserAutofocusControlWidget(
                self.laserAutofocusController, self.liveController
            )
            self.imageDisplayWindow_focus = core.ImageDisplayWindow()

        if RUN_FLUIDICS:
            self.fluidicsWidget = widgets.FluidicsWidget(self.fluidics)

        self.imageDisplayTabs = QTabWidget(parent=self)
        if self.live_only_mode:
            if ENABLE_TRACKING:
                self.imageDisplayWindow = core.ImageDisplayWindow(self.liveController, self.contrastManager)
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = core.ImageDisplayWindow(
                    self.liveController, self.contrastManager, show_LUT=True, autoLevels=True
                )
            self.imageDisplayTabs = self.imageDisplayWindow.widget
            self.napariMosaicDisplayWidget = None
        else:
            self.setupImageDisplayTabs()

        # Setup alignment widget if using napari for live view
        if USE_NAPARI_FOR_LIVE_VIEW and self.napariLiveWidget is not None:
            self._setup_alignment_widget()

        self.flexibleMultiPointWidget = widgets.FlexibleMultiPointWidget(
            self.stage,
            self.navigationViewer,
            self.multipointController,
            self.objectiveStore,
            self.scanCoordinates,
            self.focusMapWidget,
            self.napariMosaicDisplayWidget,
        )
        self.wellplateMultiPointWidget = widgets.WellplateMultiPointWidget(
            self.stage,
            self.navigationViewer,
            self.multipointController,
            self.liveController,
            self.objectiveStore,
            self.scanCoordinates,
            self.focusMapWidget,
            self.napariMosaicDisplayWidget,
            tab_widget=self.recordTabWidget,
            well_selection_widget=self.wellSelectionWidget,
        )
        if USE_TEMPLATE_MULTIPOINT:
            self.templateMultiPointWidget = TemplateMultiPointWidget(
                self.stage,
                self.navigationViewer,
                self.multipointController,
                self.objectiveStore,
                self.scanCoordinates,
                self.focusMapWidget,
            )
        self.multiPointWithFluidicsWidget = widgets.MultiPointWithFluidicsWidget(
            self.stage,
            self.navigationViewer,
            self.multipointController,
            self.objectiveStore,
            self.scanCoordinates,
            self.napariMosaicDisplayWidget,
        )
        self.sampleSettingsWidget = widgets.SampleSettingsWidget(self.objectivesWidget, self.wellplateFormatWidget)

        if ENABLE_TRACKING:
            self.trackingControlWidget = widgets.TrackingControllerWidget(
                self.trackingController,
                self.objectiveStore,
                show_configurations=TRACKING_SHOW_MICROSCOPE_CONFIGURATIONS,
            )

        self.setupRecordTabWidget()
        self.setupCameraTabWidget()

    def _restore_cached_camera_settings(self) -> None:
        """Restore cached camera settings from disk and update UI widgets.

        Applies both hardware settings (via camera API) and synchronizes the UI
        dropdown widgets. Silently returns if no cached settings exist.
        Errors are logged but do not prevent application startup.
        """
        cached_settings = squid.camera.settings_cache.load_camera_settings()
        if not cached_settings:
            return

        binning_restored = self._restore_binning(cached_settings.binning)
        pixel_format_restored = self._restore_pixel_format(cached_settings.pixel_format)

        if binning_restored or pixel_format_restored:
            self.log.info(
                f"Restored camera settings: binning={cached_settings.binning}, "
                f"pixel_format={cached_settings.pixel_format}"
            )

    def _restore_binning(self, binning: Tuple[int, int]) -> bool:
        """Apply binning setting to camera and sync UI dropdown.

        Returns True if successfully applied, False otherwise.
        """
        try:
            self.camera.set_binning(*binning)
        except ValueError as e:
            self.log.warning(f"Cannot restore binning {binning} - not supported by camera: {e}")
            return False
        except (AttributeError, RuntimeError) as e:
            self.log.error(f"Camera error while restoring binning settings: {e}")
            return False

        binning_text = f"{binning[0]}x{binning[1]}"
        self.cameraSettingWidget.dropdown_binning.blockSignals(True)
        self.cameraSettingWidget.dropdown_binning.setCurrentText(binning_text)
        self.cameraSettingWidget.dropdown_binning.blockSignals(False)
        return True

    def _restore_pixel_format(self, pixel_format_str: Optional[str]) -> bool:
        """Apply pixel format setting to camera and sync UI dropdown.

        Returns True if successfully applied, False otherwise.
        """
        if not pixel_format_str:
            return False

        try:
            pixel_format = squid.config.CameraPixelFormat.from_string(pixel_format_str)
        except KeyError:
            self.log.warning(f"Cached pixel format '{pixel_format_str}' is not recognized")
            return False

        try:
            self.camera.set_pixel_format(pixel_format)
        except ValueError as e:
            self.log.warning(f"Cannot restore pixel format {pixel_format_str} - not supported by this camera: {e}")
            return False
        except (AttributeError, RuntimeError) as e:
            self.log.error(f"Camera error while restoring pixel format settings: {e}")
            return False

        self.cameraSettingWidget.dropdown_pixelFormat.blockSignals(True)
        self.cameraSettingWidget.dropdown_pixelFormat.setCurrentText(pixel_format_str)
        self.cameraSettingWidget.dropdown_pixelFormat.blockSignals(False)
        return True

    def setupImageDisplayTabs(self):
        if USE_NAPARI_FOR_LIVE_VIEW:
            self.napariLiveWidget = widgets.NapariLiveWidget(
                self.streamHandler,
                self.liveController,
                self.stage,
                self.objectiveStore,
                self.contrastManager,
                self.wellSelectionWidget,
            )
            self.imageDisplayTabs.addTab(self.napariLiveWidget, "Live View")
        else:
            if ENABLE_TRACKING:
                self.imageDisplayWindow = core.ImageDisplayWindow(self.liveController, self.contrastManager)
                self.imageDisplayWindow.show_ROI_selector()
            else:
                self.imageDisplayWindow = core.ImageDisplayWindow(
                    self.liveController, self.contrastManager, show_LUT=True, autoLevels=True
                )
            self.imageDisplayTabs.addTab(self.imageDisplayWindow.widget, "Live View")

        if not self.live_only_mode:
            if USE_NAPARI_FOR_MULTIPOINT:
                self.napariMultiChannelWidget = widgets.NapariMultiChannelWidget(
                    self.objectiveStore, self.camera, self.contrastManager
                )
                self.imageDisplayTabs.addTab(self.napariMultiChannelWidget, "Multichannel Acquisition")
            else:
                self.imageArrayDisplayWindow = core.ImageArrayDisplayWindow()
                self.imageDisplayTabs.addTab(self.imageArrayDisplayWindow.widget, "Multichannel Acquisition")

            self.napariMosaicDisplayWidget = None
            if USE_NAPARI_FOR_MOSAIC_DISPLAY:
                self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(
                    self.objectiveStore, self.camera, self.contrastManager
                )
                self.imageDisplayTabs.addTab(self.napariMosaicDisplayWidget, "Mosaic View")

            # Plate view for well-based acquisitions (independent of mosaic view)
            self.napariPlateViewWidget = None
            if DISPLAY_PLATE_VIEW:
                self.napariPlateViewWidget = widgets.NapariPlateViewWidget(self.contrastManager)
                self.imageDisplayTabs.addTab(self.napariPlateViewWidget, "Plate View")

            # Embedded NDViewer (lightweight) - initialized AFTER napari widgets because
            # NDV and napari both use vispy for OpenGL rendering. Initializing NDV first
            # can cause OpenGL context conflicts since both libraries share vispy state.
            self.ndviewerTab = None
            if control._def.ENABLE_NDVIEWER:
                try:
                    self.ndviewerTab = widgets.NDViewerTab()
                    self.imageDisplayTabs.addTab(self.ndviewerTab, "NDViewer")
                except ImportError:
                    self.log.warning("NDViewer tab unavailable: ndviewer_light module not installed")
                except (RuntimeError, OSError) as e:
                    self.log.exception(f"Failed to initialize NDViewer tab due to system error: {e}")
                except Exception:
                    self.log.exception("Failed to initialize NDViewer tab - unexpected error")

            # Connect plate view double-click to NDViewer navigation and tab switch
            if self.napariPlateViewWidget is not None and self.ndviewerTab is not None:
                self.napariPlateViewWidget.signal_well_fov_clicked.connect(self._on_plate_view_fov_clicked)
            elif self.napariPlateViewWidget is None:
                self.log.debug("Plate view not available, FOV click navigation disabled")
            elif self.ndviewerTab is None:
                self.log.debug("NDViewer tab not available, FOV click navigation disabled")

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
            dock_laserfocus_image_display = dock.Dock("Focus Camera Image Display", autoOrientation=False)
            dock_laserfocus_image_display.showTitleBar()
            dock_laserfocus_image_display.addWidget(self.imageDisplayWindow_focus.widget)
            dock_laserfocus_image_display.setStretch(x=100, y=100)

            dock_laserfocus_liveController = dock.Dock("Laser Autofocus Settings", autoOrientation=False)
            dock_laserfocus_liveController.showTitleBar()
            dock_laserfocus_liveController.addWidget(self.laserAutofocusSettingWidget)
            dock_laserfocus_liveController.setStretch(x=100, y=100)
            dock_laserfocus_liveController.setFixedWidth(self.laserAutofocusSettingWidget.minimumSizeHint().width())

            dock_waveform = dock.Dock("Displacement Measurement", autoOrientation=False)
            dock_waveform.showTitleBar()
            dock_waveform.addWidget(self.waveformDisplay)
            dock_waveform.setStretch(x=100, y=40)

            dock_displayMeasurement = dock.Dock("Displacement Measurement Control", autoOrientation=False)
            dock_displayMeasurement.showTitleBar()
            dock_displayMeasurement.addWidget(self.displacementMeasurementWidget)
            dock_displayMeasurement.setStretch(x=100, y=40)
            dock_displayMeasurement.setFixedWidth(self.displacementMeasurementWidget.minimumSizeHint().width())

            laserfocus_dockArea = dock.DockArea()
            laserfocus_dockArea.addDock(dock_laserfocus_image_display)
            laserfocus_dockArea.addDock(
                dock_laserfocus_liveController, "right", relativeTo=dock_laserfocus_image_display
            )
            if SHOW_LEGACY_DISPLACEMENT_MEASUREMENT_WINDOWS:
                laserfocus_dockArea.addDock(dock_waveform, "bottom", relativeTo=dock_laserfocus_liveController)
                laserfocus_dockArea.addDock(dock_displayMeasurement, "bottom", relativeTo=dock_waveform)

            self.imageDisplayTabs.addTab(laserfocus_dockArea, self.LASER_BASED_FOCUS_TAB_NAME)

        if RUN_FLUIDICS:
            self.imageDisplayTabs.addTab(self.fluidicsWidget, "Fluidics")

    def setupRecordTabWidget(self):
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.recordTabWidget.addTab(self.wellplateMultiPointWidget, "Wellplate Multipoint")
        if ENABLE_FLEXIBLE_MULTIPOINT:
            self.recordTabWidget.addTab(self.flexibleMultiPointWidget, "Flexible Multipoint")
        if USE_TEMPLATE_MULTIPOINT:
            self.recordTabWidget.addTab(self.templateMultiPointWidget, "Template Multipoint")
        if RUN_FLUIDICS:
            self.recordTabWidget.addTab(self.multiPointWithFluidicsWidget, "Multipoint with Fluidics")
        if ENABLE_TRACKING:
            self.recordTabWidget.addTab(self.trackingControlWidget, "Tracking")
        if ENABLE_RECORDING:
            self.recordTabWidget.addTab(self.recordingControlWidget, "Simple Recording")
        self.recordTabWidget.currentChanged.connect(lambda: self.resizeCurrentTab(self.recordTabWidget))
        self.resizeCurrentTab(self.recordTabWidget)

    def _setup_alignment_widget(self):
        """Setup alignment widget and connect to navigation viewer and multipoint controller."""
        if self.napariLiveWidget is None:
            self.log.warning("Cannot setup alignment widget: napariLiveWidget not available")
            return

        self.alignmentWidget = widgets.AlignmentWidget(
            napari_viewer=self.napariLiveWidget.viewer,
            parent=None,
        )

        self.alignmentWidget.signal_move_to_position.connect(self._alignment_move_to)
        self.alignmentWidget.signal_request_current_position.connect(self._alignment_provide_position)
        self.alignmentWidget.signal_offset_set.connect(
            lambda x, y: self.log.info(f"Alignment offset active: ({x:.4f}, {y:.4f})mm")
        )
        self.alignmentWidget.signal_offset_cleared.connect(lambda: self.log.info("Alignment offset cleared"))

        self.multipointController.set_alignment_widget(self.alignmentWidget)
        self.navigationViewer.set_alignment_widget(self.alignmentWidget)
        self.log.info("Alignment widget setup complete")

    def _alignment_move_to(self, x_mm: float, y_mm: float):
        """Handle alignment widget request to move stage."""
        self.stage.move_x_to(x_mm)
        self.stage.move_y_to(y_mm)

    def _alignment_provide_position(self):
        """Provide current stage position to alignment widget."""
        pos = self.stage.get_pos()
        self.alignmentWidget.set_current_position(pos.x_mm, pos.y_mm)

    def setupCameraTabWidget(self):
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
        self.cameraTabWidget.currentChanged.connect(lambda: self.resizeCurrentTab(self.cameraTabWidget))
        self.resizeCurrentTab(self.cameraTabWidget)

        # RAM monitor widget (always create, visibility controlled by setting)
        self.ramMonitorWidget = widgets.RAMMonitorWidget()
        self.ramMonitorWidget.setVisible(False)
        self._ram_monitor_should_show = False

        # Backpressure monitor widget (always create, visibility controlled during acquisition)
        self.backpressureMonitorWidget = widgets.BackpressureMonitorWidget()
        self.backpressureMonitorWidget.setVisible(False)
        self._bp_monitor_should_show = False

        # Warning/Error display widget (auto-hides when empty)
        self.warningErrorWidget = widgets.WarningErrorWidget()
        self.warningErrorWidget.setVisible(False)
        self._warning_handler = None

    def setup_layout(self):
        layout = QVBoxLayout()

        # Add warning banner if simulated disk I/O mode is enabled
        import control._def

        if control._def.SIMULATED_DISK_IO_ENABLED:
            simulated_io_banner = QLabel("  SIMULATED DISK I/O - Images are encoded but NOT saved to disk  ")
            simulated_io_banner.setStyleSheet(
                "background-color: #FF6B6B; color: white; font-weight: bold; padding: 8px;"
            )
            simulated_io_banner.setAlignment(Qt.AlignCenter)
            layout.addWidget(simulated_io_banner)

        if USE_NAPARI_FOR_LIVE_CONTROL and not self.live_only_mode:
            layout.addWidget(self.navigationWidget)
        else:
            layout.addWidget(self.profileWidget)
            layout.addWidget(self.liveControlWidget)

        layout.addWidget(self.cameraTabWidget)

        if SHOW_DAC_CONTROL:
            layout.addWidget(self.dacControlWidget)

        # Create a widget to hold sample settings and navigation viewer
        navigation_section_widget = QWidget()
        navigation_section_layout = QVBoxLayout()
        navigation_section_layout.setContentsMargins(0, 0, 0, 0)
        navigation_section_layout.setSpacing(0)
        navigation_section_layout.addWidget(self.sampleSettingsWidget)
        navigation_section_layout.addWidget(self.navigationViewer)
        navigation_section_widget.setLayout(navigation_section_layout)

        # Create a splitter between recordTabWidget and navigation section (50/50)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.recordTabWidget)
        splitter.addWidget(navigation_section_widget)
        splitter.setStretchFactor(0, 1)  # recordTabWidget 50%
        splitter.setStretchFactor(1, 1)  # navigation section 50%

        layout.addWidget(splitter)

        # Add performance mode toggle button at the bottom with natural height
        if not self.live_only_mode:
            self.performanceModeToggle = QPushButton("Enable Performance Mode")
            self.performanceModeToggle.setCheckable(True)
            self.performanceModeToggle.setChecked(self.performance_mode)
            self.performanceModeToggle.clicked.connect(self.togglePerformanceMode)
            layout.addWidget(self.performanceModeToggle)

        self.centralWidget = QWidget()
        self.centralWidget.setLayout(layout)
        self.centralWidget.setFixedWidth(self.centralWidget.minimumSizeHint().width())

        if SINGLE_WINDOW:
            self.setupSingleWindowLayout()
        else:
            self.setupMultiWindowLayout()

        # Add RAM monitor widget to left side of status bar
        # Status bar is hidden when RAM monitoring is disabled
        # Visibility update is deferred to showEvent since status bar isn't visible until window is shown
        if self.ramMonitorWidget is not None:
            self.statusBar().addWidget(self.ramMonitorWidget)  # Left-aligned

        # Add backpressure monitor widget to status bar (next to RAM monitor)
        # Only visible during acquisition when throttling is enabled
        if self.backpressureMonitorWidget is not None:
            self.statusBar().addWidget(self.backpressureMonitorWidget)  # Left-aligned

        # Add warning/error display widget to status bar
        # Auto-hides when no messages pending
        if self.warningErrorWidget is not None:
            self.statusBar().addWidget(self.warningErrorWidget)  # Left-aligned

    def _getMainWindowMinimumSize(self):
        """
        We want our main window to fit on the primary screen, so grab the users primary screen and return
        something slightly smaller than that.
        """
        desktop_info = QDesktopWidget()
        primary_screen_size = desktop_info.screen(desktop_info.primaryScreen()).size()

        height_min = int(0.9 * primary_screen_size.height())
        width_min = int(0.96 * primary_screen_size.width())

        return (width_min, height_min)

    def setupSingleWindowLayout(self):
        main_dockArea = dock.DockArea()

        dock_display = dock.Dock("Image Display", autoOrientation=False)
        dock_display.showTitleBar()
        dock_display.addWidget(self.imageDisplayTabs)
        dock_display.setStretch(x=100, y=100)
        main_dockArea.addDock(dock_display)

        self.dock_wellSelection = dock.Dock("Well Selector", autoOrientation=False)
        self.dock_wellSelection.showTitleBar()
        if not USE_NAPARI_WELL_SELECTION or self.live_only_mode:
            self.dock_wellSelection.addWidget(self.wellSelectionWidget)
            self.dock_wellSelection.setFixedHeight(self.dock_wellSelection.minimumSizeHint().height())
            main_dockArea.addDock(self.dock_wellSelection, "bottom")

        dock_controlPanel = dock.Dock("Controls", autoOrientation=False)
        dock_controlPanel.addWidget(self.centralWidget)
        dock_controlPanel.setStretch(x=1, y=None)
        dock_controlPanel.setFixedWidth(dock_controlPanel.minimumSizeHint().width())
        main_dockArea.addDock(dock_controlPanel, "right")
        self.setCentralWidget(main_dockArea)

        self.setMinimumSize(*self._getMainWindowMinimumSize())
        self.onTabChanged(self.recordTabWidget.currentIndex())

    def setupMultiWindowLayout(self):
        self.setCentralWidget(self.centralWidget)
        self.tabbedImageDisplayWindow = QMainWindow()
        self.tabbedImageDisplayWindow.setCentralWidget(self.imageDisplayTabs)
        self.tabbedImageDisplayWindow.setWindowFlags(self.windowFlags() | Qt.CustomizeWindowHint)
        self.tabbedImageDisplayWindow.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        (width_min, height_min) = self._getMainWindowMinimumSize()
        self.tabbedImageDisplayWindow.setFixedSize(width_min, height_min)
        self.tabbedImageDisplayWindow.show()

    def make_connections(self):
        self.streamHandler.signal_new_frame_received.connect(self.liveController.on_new_frame)
        self.streamHandler.packet_image_to_write.connect(self.imageSaver.enqueue)

        if ENABLE_FLEXIBLE_MULTIPOINT:
            self.flexibleMultiPointWidget.signal_acquisition_started.connect(self.toggleAcquisitionStart)
            self.signal_performance_mode_changed.connect(self.flexibleMultiPointWidget.set_performance_mode)

        if ENABLE_WELLPLATE_MULTIPOINT:
            self.wellplateMultiPointWidget.signal_acquisition_started.connect(self.toggleAcquisitionStart)
            self.wellplateMultiPointWidget.signal_toggle_live_scan_grid.connect(self.toggle_live_scan_grid)
            self.signal_performance_mode_changed.connect(self.wellplateMultiPointWidget.set_performance_mode)

        if RUN_FLUIDICS:
            self.multiPointWithFluidicsWidget.signal_acquisition_started.connect(self.toggleAcquisitionStart)
            self.fluidicsWidget.fluidics_initialized_signal.connect(self.multiPointWithFluidicsWidget.init_fluidics)
            self.signal_performance_mode_changed.connect(self.multiPointWithFluidicsWidget.set_performance_mode)

        self.profileWidget.signal_profile_changed.connect(self.liveControlWidget.refresh_mode_list)

        self.liveControlWidget.signal_newExposureTime.connect(self.cameraSettingWidget.set_exposure_time)
        self.liveControlWidget.signal_newAnalogGain.connect(self.cameraSettingWidget.set_analog_gain)
        if not self.live_only_mode:
            self.liveControlWidget.signal_start_live.connect(self.onStartLive)
        self.liveControlWidget.update_camera_settings()

        self.connectSlidePositionController()

        self.navigationViewer.signal_coordinates_clicked.connect(self.move_from_click_mm)
        self.objectivesWidget.signal_objective_changed.connect(self.navigationViewer.redraw_fov)
        self.cameraSettingWidget.signal_binning_changed.connect(self.navigationViewer.redraw_fov)
        if ENABLE_FLEXIBLE_MULTIPOINT:
            self.objectivesWidget.signal_objective_changed.connect(self.flexibleMultiPointWidget.update_fov_positions)
        # TODO(imo): Fix position updates after removal of navigation controller
        self.movement_updater.position_after_move.connect(self.navigationViewer.draw_fov_current_location)
        self.multipointController.signal_register_current_fov.connect(self.navigationViewer.register_fov)
        self.multipointController.signal_current_configuration.connect(self.liveControlWidget.update_ui_for_mode)
        if self.piezoWidget:
            self.movement_updater.piezo_z_um.connect(self.piezoWidget.update_displacement_um_display)
        self.multipointController.signal_set_display_tabs.connect(self.setAcquisitionDisplayTabs)

        # RAM monitor widget connections - use controller signals which fire AFTER memory monitor is created
        self.multipointController.signal_acquisition_start.connect(self._connect_ram_monitor_widget)
        self.multipointController.acquisition_finished.connect(self._disconnect_ram_monitor_widget)

        # Backpressure monitor widget connections - fires AFTER worker is created
        self.multipointController.signal_acquisition_start.connect(self._connect_backpressure_monitor_widget)
        self.multipointController.acquisition_finished.connect(self._disconnect_backpressure_monitor_widget)

        # NDViewer push-based API connections
        if self.ndviewerTab is not None:
            self.multipointController.ndviewer_start_acquisition.connect(self.ndviewerTab.start_acquisition)
            self.multipointController.ndviewer_register_image.connect(self.ndviewerTab.register_image)
            self.multipointController.acquisition_finished.connect(self.ndviewerTab.end_acquisition)

        self.recordTabWidget.currentChanged.connect(self.onTabChanged)
        if not self.live_only_mode:
            self.imageDisplayTabs.currentChanged.connect(self.onDisplayTabChanged)

        if USE_NAPARI_FOR_LIVE_VIEW and not self.live_only_mode:
            self.multipointController.signal_current_configuration.connect(self.napariLiveWidget.update_ui_for_mode)
            self.autofocusController.image_to_display.connect(
                lambda image: self.napariLiveWidget.updateLiveLayer(image, from_autofocus=True)
            )
            self.streamHandler.image_to_display.connect(
                lambda image: self.napariLiveWidget.updateLiveLayer(image, from_autofocus=False)
            )
            self.multipointController.image_to_display.connect(
                lambda image: self.napariLiveWidget.updateLiveLayer(image, from_autofocus=False)
            )
            self.napariLiveWidget.signal_coordinates_clicked.connect(self.move_from_click_image)
            self.liveControlWidget.signal_live_configuration.connect(self.napariLiveWidget.set_live_configuration)

            if USE_NAPARI_FOR_LIVE_CONTROL:
                self.napariLiveWidget.signal_newExposureTime.connect(self.cameraSettingWidget.set_exposure_time)
                self.napariLiveWidget.signal_newAnalogGain.connect(self.cameraSettingWidget.set_analog_gain)
                self.napariLiveWidget.signal_autoLevelSetting.connect(self.imageDisplayWindow.set_autolevel)
        else:
            self.streamHandler.image_to_display.connect(self.imageDisplay.enqueue)
            self.imageDisplay.image_to_display.connect(self.imageDisplayWindow.display_image)
            self.autofocusController.image_to_display.connect(self.imageDisplayWindow.display_image)
            self.multipointController.image_to_display.connect(self.imageDisplayWindow.display_image)
            self.liveControlWidget.signal_autoLevelSetting.connect(self.imageDisplayWindow.set_autolevel)
            self.imageDisplayWindow.image_click_coordinates.connect(self.move_from_click_image)

        self.makeNapariConnections()

        self.wellplateFormatWidget.signalWellplateSettings.connect(self.navigationViewer.update_wellplate_settings)
        self.wellplateFormatWidget.signalWellplateSettings.connect(self.scanCoordinates.update_wellplate_settings)
        self.wellplateFormatWidget.signalWellplateSettings.connect(self.wellSelectionWidget.onWellplateChanged)
        self.wellplateFormatWidget.signalWellplateSettings.connect(
            lambda format_, *args: self.onWellplateChanged(format_)
        )

        self.wellSelectionWidget.signal_wellSelectedPos.connect(self.move_to_mm)
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.wellSelectionWidget.signal_wellSelected.connect(self.wellplateMultiPointWidget.update_well_coordinates)
            self.objectivesWidget.signal_objective_changed.connect(
                self.wellplateMultiPointWidget.handle_objective_change
            )

        self.profileWidget.signal_profile_changed.connect(
            lambda: self.liveControlWidget.select_new_microscope_mode_by_name(
                self.liveControlWidget.currentConfiguration.name
            )
        )
        self.objectivesWidget.signal_objective_changed.connect(
            lambda: self.liveControlWidget.select_new_microscope_mode_by_name(
                self.liveControlWidget.currentConfiguration.name
            )
        )

        if SUPPORT_LASER_AUTOFOCUS:

            def slot_settings_changed_laser_af():
                self.laserAutofocusController.on_settings_changed()
                self.laserAutofocusControlWidget.update_init_state()
                self.laserAutofocusSettingWidget.update_values()

            self.profileWidget.signal_profile_changed.connect(slot_settings_changed_laser_af)
            self.objectivesWidget.signal_objective_changed.connect(slot_settings_changed_laser_af)
            self.laserAutofocusSettingWidget.signal_newExposureTime.connect(
                self.cameraSettingWidget_focus_camera.set_exposure_time
            )
            self.laserAutofocusSettingWidget.signal_newAnalogGain.connect(
                self.cameraSettingWidget_focus_camera.set_analog_gain
            )
            self.laserAutofocusSettingWidget.signal_apply_settings.connect(
                self.laserAutofocusControlWidget.update_init_state
            )
            self.laserAutofocusSettingWidget.signal_laser_spot_location.connect(self.imageDisplayWindow_focus.mark_spot)
            self.laserAutofocusSettingWidget.update_exposure_time(
                self.laserAutofocusSettingWidget.exposure_spinbox.value()
            )
            self.laserAutofocusSettingWidget.update_analog_gain(
                self.laserAutofocusSettingWidget.analog_gain_spinbox.value()
            )
            self.laserAutofocusController.signal_cross_correlation.connect(
                self.laserAutofocusSettingWidget.show_cross_correlation_result
            )

            self.streamHandler_focus_camera.signal_new_frame_received.connect(
                self.liveController_focus_camera.on_new_frame
            )
            self.streamHandler_focus_camera.image_to_display.connect(self.imageDisplayWindow_focus.display_image)

            self.streamHandler_focus_camera.image_to_display.connect(
                self.displacementMeasurementController.update_measurement
            )
            self.displacementMeasurementController.signal_plots.connect(self.waveformDisplay.plot)
            self.displacementMeasurementController.signal_readings.connect(
                self.displacementMeasurementWidget.display_readings
            )
            self.laserAutofocusController.image_to_display.connect(self.imageDisplayWindow_focus.display_image)

            # Add connection for piezo position updates
            if self.piezoWidget:
                self.laserAutofocusController.signal_piezo_position_update.connect(
                    self.piezoWidget.update_displacement_um_display
                )

        if ENABLE_SPINNING_DISK_CONFOCAL:
            self.spinningDiskConfocalWidget.signal_toggle_confocal_widefield.connect(
                self.liveController.toggle_confocal_widefield
            )
            self.spinningDiskConfocalWidget.signal_toggle_confocal_widefield.connect(
                lambda: self.liveControlWidget.select_new_microscope_mode_by_name(
                    self.liveControlWidget.currentConfiguration.name
                )
            )
            # INITIALIZATION ORDER: Confocal state sync happens in Microscope.__init__ BEFORE
            # this GUI code runs. The microscope queries hardware state and calls
            # live_controller.sync_confocal_mode_from_hardware() during init.
            # The signal connection above handles subsequent user-initiated toggles only.
            # See Microscope._sync_confocal_mode_from_hardware() for the initial sync logic.

        # Connect to plot xyz data when coordinates are saved
        self.multipointController.signal_coordinates.connect(self.zPlotWidget.add_point)

        def plot_after_each_region(current_region: int, total_regions: int, current_timepoint: int):
            if current_region > 1:
                self.zPlotWidget.plot()
            self.zPlotWidget.clear()

        self.multipointController.signal_acquisition_progress.connect(plot_after_each_region)
        # Since we don't get a region progress call after the last, make sure there's one last plot for
        # the final region.
        self.multipointController.acquisition_finished.connect(self.zPlotWidget.plot)

        # Connect well selector button
        if hasattr(self.imageDisplayWindow, "btn_well_selector"):
            self.imageDisplayWindow.btn_well_selector.clicked.connect(
                lambda: self.toggleWellSelector(not self.dock_wellSelection.isVisible())
            )

    def setup_movement_updater(self):
        # We provide a few signals about the system's physical movement to other parts of the UI.  Ideally, they other
        # parts would register their interest (instead of us needing to know that they want to hear about the movements
        # here), but as an intermediate pumping it all from one location is better than nothing.
        self.movement_updater = MovementUpdater(stage=self.stage, piezo=self.piezo)
        self.movement_update_timer = QTimer()
        self.movement_update_timer.setInterval(100)
        self.movement_update_timer.timeout.connect(self.movement_updater.do_update)
        self.movement_update_timer.start()

    def makeNapariConnections(self):
        """Initialize all Napari connections in one place"""
        self.napari_connections = {
            "napariLiveWidget": [],
            "napariMultiChannelWidget": [],
            "napariMosaicDisplayWidget": [],
        }

        # Setup live view connections
        if USE_NAPARI_FOR_LIVE_VIEW and not self.live_only_mode:
            self.napari_connections["napariLiveWidget"] = [
                (self.multipointController.signal_current_configuration, self.napariLiveWidget.update_ui_for_mode),
                (
                    self.autofocusController.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(image, from_autofocus=True),
                ),
                (
                    self.streamHandler.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(image, from_autofocus=False),
                ),
                (
                    self.multipointController.image_to_display,
                    lambda image: self.napariLiveWidget.updateLiveLayer(image, from_autofocus=False),
                ),
                (self.napariLiveWidget.signal_coordinates_clicked, self.move_from_click_image),
                (self.liveControlWidget.signal_live_configuration, self.napariLiveWidget.set_live_configuration),
            ]

            if USE_NAPARI_FOR_LIVE_CONTROL:
                self.napari_connections["napariLiveWidget"].extend(
                    [
                        (self.napariLiveWidget.signal_newExposureTime, self.cameraSettingWidget.set_exposure_time),
                        (self.napariLiveWidget.signal_newAnalogGain, self.cameraSettingWidget.set_analog_gain),
                        (self.napariLiveWidget.signal_autoLevelSetting, self.imageDisplayWindow.set_autolevel),
                    ]
                )
        else:
            # Non-Napari display connections
            self.streamHandler.image_to_display.connect(self.imageDisplay.enqueue)
            self.imageDisplay.image_to_display.connect(self.imageDisplayWindow.display_image)
            self.autofocusController.image_to_display.connect(self.imageDisplayWindow.display_image)
            self.multipointController.image_to_display.connect(self.imageDisplayWindow.display_image)
            self.liveControlWidget.signal_autoLevelSetting.connect(self.imageDisplayWindow.set_autolevel)
            self.imageDisplayWindow.image_click_coordinates.connect(self.move_from_click_image)

        if not self.live_only_mode:
            # Setup multichannel widget connections
            if USE_NAPARI_FOR_MULTIPOINT:
                self.napari_connections["napariMultiChannelWidget"] = [
                    (self.multipointController.napari_layers_init, self.napariMultiChannelWidget.initLayers),
                    (self.multipointController.napari_layers_update, self.napariMultiChannelWidget.updateLayers),
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
                self.multipointController.image_to_display_multi.connect(self.imageArrayDisplayWindow.display_image)

            # Setup mosaic display widget connections
            if USE_NAPARI_FOR_MOSAIC_DISPLAY:
                self.napari_connections["napariMosaicDisplayWidget"] = [
                    (self.multipointController.napari_layers_update, self.napariMosaicDisplayWidget.updateMosaic),
                    (self.napariMosaicDisplayWidget.signal_coordinates_clicked, self.move_from_click_mm),
                    (self.napariMosaicDisplayWidget.signal_clear_viewer, self.navigationViewer.clear_slide),
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

            # Setup plate view widget connections (independent of mosaic display)
            # Use Qt.QueuedConnection explicitly for thread safety since these signals
            # are emitted from the acquisition worker thread and received on the main thread.
            # This ensures the slot is invoked in the receiver's thread event loop.
            if self.napariPlateViewWidget is not None:
                self.napari_connections["napariPlateViewWidget"] = [
                    (
                        self.multipointController.plate_view_init,
                        self.napariPlateViewWidget.initPlateLayout,
                        Qt.QueuedConnection,
                    ),
                    (
                        self.multipointController.plate_view_update,
                        self.napariPlateViewWidget.updatePlateView,
                        Qt.QueuedConnection,
                    ),
                ]

            # Make initial connections
            self.updateNapariConnections()

    def updateNapariConnections(self):
        # Update Napari connections based on performance mode. Live widget connections are preserved
        # Connection tuples can be:
        #   (signal, slot) - uses default Qt.AutoConnection
        #   (signal, slot, connection_type) - uses specified connection type (e.g., Qt.QueuedConnection)
        for widget_name, connections in self.napari_connections.items():
            if widget_name != "napariLiveWidget":  # Always keep the live widget connected
                widget = getattr(self, widget_name, None)
                if widget:
                    for conn in connections:
                        signal = conn[0]
                        slot = conn[1]
                        connection_type = conn[2] if len(conn) > 2 else None
                        if self.performance_mode:
                            try:
                                signal.disconnect(slot)
                            except TypeError:
                                # Connection might not exist, which is fine
                                pass
                        else:
                            try:
                                if connection_type is not None:
                                    signal.connect(slot, connection_type)
                                else:
                                    signal.connect(slot)
                            except TypeError:
                                # Connection might already exist, which is fine
                                pass

    def toggleNapariTabs(self):
        # Enable/disable Napari tabs based on performance mode
        for i in range(1, self.imageDisplayTabs.count()):
            if self.imageDisplayTabs.tabText(i) != self.LASER_BASED_FOCUS_TAB_NAME:
                self.imageDisplayTabs.setTabEnabled(i, not self.performance_mode)

        if self.performance_mode:
            # Switch to the NapariLiveWidget tab if it exists
            for i in range(self.imageDisplayTabs.count()):
                if isinstance(self.imageDisplayTabs.widget(i), widgets.NapariLiveWidget):
                    self.imageDisplayTabs.setCurrentIndex(i)
                    break

    def togglePerformanceMode(self):
        self.performance_mode = self.performanceModeToggle.isChecked()
        button_txt = "Disable" if self.performance_mode else "Enable"
        self.performanceModeToggle.setText(button_txt + " Performance Mode")
        self.updateNapariConnections()
        self.toggleNapariTabs()
        self.signal_performance_mode_changed.emit(self.performance_mode)
        print(f"Performance mode {'enabled' if self.performance_mode else 'disabled'}")

    def setAcquisitionDisplayTabs(self, selected_configurations, Nz, xy_mode=None):
        if self.performance_mode:
            self.imageDisplayTabs.setCurrentIndex(0)
        elif not self.live_only_mode:
            configs = [config.name for config in selected_configurations]
            print(configs)
            # For well-based acquisitions (Select Wells or Load Coordinates), use Plate View if enabled
            is_well_based = xy_mode is not None and xy_mode in ("Select Wells", "Load Coordinates")
            if is_well_based and self.napariPlateViewWidget is not None and Nz == 1:
                self.imageDisplayTabs.setCurrentWidget(self.napariPlateViewWidget)
            elif USE_NAPARI_FOR_MOSAIC_DISPLAY and Nz == 1:
                self.imageDisplayTabs.setCurrentWidget(self.napariMosaicDisplayWidget)
            elif USE_NAPARI_FOR_MULTIPOINT:
                self.imageDisplayTabs.setCurrentWidget(self.napariMultiChannelWidget)
            else:
                self.imageDisplayTabs.setCurrentIndex(0)

    def openLedMatrixSettings(self):
        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            dialog = widgets.LedMatrixSettingsDialog(self.liveController.led_array)
            dialog.exec_()

    def openPreferences(self):
        if CACHED_CONFIG_FILE_PATH and os.path.exists(CACHED_CONFIG_FILE_PATH):
            config = ConfigParser()
            config.read(CACHED_CONFIG_FILE_PATH)
            dialog = widgets.PreferencesDialog(config, CACHED_CONFIG_FILE_PATH, self)
            dialog.signal_config_changed.connect(self._update_ram_monitor_visibility)
            dialog.exec_()
        else:
            self.log.warning("No configuration file found")

    def _setup_slack_notifier(self):
        """Initialize the Slack notifier and wire up connections."""
        # Create the slack notifier
        self.slackNotifier = SlackNotifier()

        # Set slack notifier on multipoint controller
        if self.multipointController is not None:
            self.multipointController.set_slack_notifier(self.slackNotifier)
            # Connect Slack notification signals to handlers (runs on main thread for proper ordering)
            self.multipointController.signal_slack_timepoint.connect(self._handle_slack_timepoint_notification)
            self.multipointController.signal_slack_acq_finished.connect(self._handle_slack_acquisition_finished)

        self.log.info("Slack notifier initialized")

    def _handle_slack_timepoint_notification(self, stats: TimepointStats):
        """Handle Slack timepoint notification on the main Qt thread.

        Captures screenshot from mosaic widget (if available) and sends notification.
        """
        try:
            # Capture screenshot from mosaic widget (must be done on main Qt thread)
            mosaic_image = None
            if self.napariMosaicDisplayWidget is not None:
                mosaic_image = self.napariMosaicDisplayWidget.get_screenshot()

            # Send notification with screenshot
            if self.slackNotifier is not None:
                self.slackNotifier.notify_timepoint_complete(stats, mosaic_image)
        except Exception as e:
            self.log.warning(f"Failed to send Slack timepoint notification: {e}")

    def _handle_slack_acquisition_finished(self, stats: AcquisitionStats):
        """Handle Slack acquisition finished notification on the main Qt thread."""
        try:
            if self.slackNotifier is not None:
                self.slackNotifier.notify_acquisition_finished(stats)
        except Exception as e:
            self.log.warning(f"Failed to send Slack acquisition finished notification: {e}")

    def openSlackSettings(self):
        """Open the Slack notifications settings dialog."""
        if self.slackSettingsDialog is None:
            self.slackSettingsDialog = SlackSettingsDialog(
                slack_notifier=self.slackNotifier,
                parent=self,
            )
        self.slackSettingsDialog.show()
        self.slackSettingsDialog.raise_()
        self.slackSettingsDialog.activateWindow()

    def openChannelConfigurationEditor(self):
        """Open the illumination channel configurator dialog"""
        from control.core.config import ConfigRepository

        config_repo = ConfigRepository()
        dialog = widgets.IlluminationChannelConfiguratorDialog(config_repo, self)
        dialog.signal_channels_updated.connect(self._refresh_channel_lists)
        dialog.exec_()

    def openAdvancedChannelMapping(self):
        """Open the advanced channel hardware mapping dialog"""
        dialog = widgets.AdvancedChannelMappingDialog(self.microscope.config_repo, self)
        dialog.signal_mappings_updated.connect(self._refresh_channel_lists)
        dialog.exec_()

    def _refresh_channel_lists(self):
        """Refresh channel lists in all widgets after channel configuration changes"""
        if self.liveControlWidget:
            self.liveControlWidget.refresh_mode_list()
        if self.napariLiveWidget:
            self.napariLiveWidget.refresh_mode_list()

    def onTabChanged(self, index):
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
            if self.wellplateMultiPointWidget.combobox_xy_mode.currentText() == "Manual":
                # trigger manual shape update
                if self.wellplateMultiPointWidget.shapes_mm:
                    self.wellplateMultiPointWidget.update_manual_shape(self.wellplateMultiPointWidget.shapes_mm)
            else:
                # trigger wellplate update
                self.wellplateMultiPointWidget.update_coordinates()
        elif is_flexible_acquisition:
            # trigger flexible regions update
            self.flexibleMultiPointWidget.update_fov_positions()

        self.toggleWellSelector(is_wellplate_acquisition and self.wellSelectionWidget.format != "glass slide")
        acquisitionWidget = self.recordTabWidget.widget(index)
        acquisitionWidget.emit_selected_channels()

    def resizeCurrentTab(self, tabWidget):
        current_widget = tabWidget.currentWidget()
        if current_widget:
            total_height = current_widget.sizeHint().height() + tabWidget.tabBar().height()
            tabWidget.resize(tabWidget.width(), total_height)
            tabWidget.setMaximumHeight(total_height)
            tabWidget.updateGeometry()
            self.updateGeometry()

    def onDisplayTabChanged(self, index):
        current_widget = self.imageDisplayTabs.widget(index)
        if hasattr(current_widget, "viewer"):
            current_widget.activate()

        # Stop focus camera live if not on laser focus tab
        if SUPPORT_LASER_AUTOFOCUS:
            is_laser_focus_tab = self.imageDisplayTabs.tabText(index) == self.LASER_BASED_FOCUS_TAB_NAME

            if hasattr(self, "dock_wellSelection"):
                self.dock_wellSelection.setVisible(not is_laser_focus_tab)

            if not is_laser_focus_tab:
                self.laserAutofocusSettingWidget.stop_live()

        # Only show well selector in Live View tab if it was previously shown
        if self.imageDisplayTabs.tabText(index) == "Live View":
            self.toggleWellSelector(self.well_selector_visible)  # Use stored visibility state
        else:
            self.toggleWellSelector(False)

    def onWellplateChanged(self, format_):
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
                self.replaceWellSelectionWidget(widgets.Well1536SelectionWidget(self.wellplateFormatWidget))
                self.connectWellSelectionWidget()
            elif isinstance(self.wellSelectionWidget, widgets.Well1536SelectionWidget):
                self.replaceWellSelectionWidget(widgets.WellSelectionWidget(format_, self.wellplateFormatWidget))
                self.connectWellSelectionWidget()

        if ENABLE_FLEXIBLE_MULTIPOINT:  # clear regions
            self.flexibleMultiPointWidget.clear_only_location_list()
        if ENABLE_WELLPLATE_MULTIPOINT:  # reset regions onto new wellplate with default size/shape
            self.scanCoordinates.clear_regions()
            self.wellplateMultiPointWidget.set_default_scan_size()

    def toggle_live_scan_grid(self, on):
        if on:
            self.movement_updater.position_after_move.connect(self.wellplateMultiPointWidget.update_live_coordinates)
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

    def connectSlidePositionController(self):
        if ENABLE_FLEXIBLE_MULTIPOINT:
            self.stageUtils.signal_loading_position_reached.connect(
                self.flexibleMultiPointWidget.disable_the_start_aquisition_button
            )
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.stageUtils.signal_loading_position_reached.connect(
                self.wellplateMultiPointWidget.disable_the_start_aquisition_button
            )
        if RUN_FLUIDICS:
            self.stageUtils.signal_loading_position_reached.connect(
                self.multiPointWithFluidicsWidget.disable_the_start_aquisition_button
            )

        if ENABLE_FLEXIBLE_MULTIPOINT:
            self.stageUtils.signal_scanning_position_reached.connect(
                self.flexibleMultiPointWidget.enable_the_start_aquisition_button
            )
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.stageUtils.signal_scanning_position_reached.connect(
                self.wellplateMultiPointWidget.enable_the_start_aquisition_button
            )
        if RUN_FLUIDICS:
            self.stageUtils.signal_scanning_position_reached.connect(
                self.multiPointWithFluidicsWidget.enable_the_start_aquisition_button
            )

        self.stageUtils.signal_scanning_position_reached.connect(self.navigationViewer.clear_slide)

    def replaceWellSelectionWidget(self, new_widget):
        self.wellSelectionWidget.setParent(None)
        self.wellSelectionWidget.deleteLater()
        self.wellSelectionWidget = new_widget
        self.scanCoordinates.add_well_selector(self.wellSelectionWidget)
        if USE_NAPARI_WELL_SELECTION and not self.performance_mode and not self.live_only_mode:
            self.napariLiveWidget.replace_well_selector(self.wellSelectionWidget)
        else:
            self.dock_wellSelection.addWidget(self.wellSelectionWidget)

    def connectWellSelectionWidget(self):
        self.wellSelectionWidget.signal_wellSelectedPos.connect(self.move_to_mm)
        self.wellplateFormatWidget.signalWellplateSettings.connect(self.wellSelectionWidget.onWellplateChanged)
        if ENABLE_WELLPLATE_MULTIPOINT:
            self.wellSelectionWidget.signal_wellSelected.connect(self.wellplateMultiPointWidget.update_well_coordinates)

    def toggleWellSelector(self, show, remember_state=True):
        if show and self.imageDisplayTabs.tabText(self.imageDisplayTabs.currentIndex()) == "Live View":
            self.dock_wellSelection.setVisible(True)
        else:
            self.dock_wellSelection.setVisible(False)

        # Only update visibility state if we're in Live View tab and we want to remember the state
        # remember_state is False when we're toggling the well selector for starting/stopping an acquisition
        if self.imageDisplayTabs.tabText(self.imageDisplayTabs.currentIndex()) == "Live View" and remember_state:
            self.well_selector_visible = show

        # Update button text
        if hasattr(self.imageDisplayWindow, "btn_well_selector"):
            self.imageDisplayWindow.btn_well_selector.setText("Hide Well Selector" if show else "Show Well Selector")

    def toggleAcquisitionStart(self, acquisition_started):
        self.log.debug(f"toggleAcquisitionStarted({acquisition_started=})")
        if acquisition_started:
            self.log.info("STARTING ACQUISITION")
            # NDViewer is now configured via push-based API signals (ndviewer_start_acquisition)
            if self.is_live_scan_grid_on:
                self.toggle_live_scan_grid(on=False)
                self.live_scan_grid_was_on = True
            else:
                self.live_scan_grid_was_on = False
            # NOTE: RAM monitor widget is connected via multipointController.signal_acquisition_start
            # which fires AFTER the memory monitor is created (see make_connections)
        else:
            self.log.info("FINISHED ACQUISITION")
            if self.live_scan_grid_was_on:
                self.toggle_live_scan_grid(on=True)
                self.live_scan_grid_was_on = False
            # NOTE: RAM monitor widget is disconnected via multipointController.acquisition_finished

        # click to move off during acquisition
        self.navigationWidget.set_click_to_move(not acquisition_started)

        # disable other acqusiition tabs during acquisition
        current_index = self.recordTabWidget.currentIndex()
        for index in range(self.recordTabWidget.count()):
            self.recordTabWidget.setTabEnabled(index, not acquisition_started or index == current_index)

        # disable autolevel once acquisition started
        if acquisition_started:
            self.liveControlWidget.toggle_autolevel(not acquisition_started)

        # hide well selector during acquisition
        is_wellplate_acquisition = (
            (current_index == self.recordTabWidget.indexOf(self.wellplateMultiPointWidget))
            if ENABLE_WELLPLATE_MULTIPOINT
            else False
        )
        if is_wellplate_acquisition and self.wellSelectionWidget.format != "glass slide":
            self.toggleWellSelector(not acquisition_started, remember_state=False)
        else:
            self.toggleWellSelector(False)

        # display acquisition progress bar during acquisition
        self.recordTabWidget.currentWidget().display_progress_bar(acquisition_started)

    def _update_ram_monitor_visibility(self):
        """Update RAM monitor widget visibility based on setting."""
        import control._def

        if self.ramMonitorWidget is None:
            return

        if control._def.ENABLE_MEMORY_PROFILING:
            self._ram_monitor_should_show = True
            self.ramMonitorWidget.setVisible(True)
            self.ramMonitorWidget.start_monitoring()
            self.log.info("RAM monitor: enabled, showing widget")
        else:
            self._ram_monitor_should_show = False
            self.ramMonitorWidget.stop_monitoring()
            self.ramMonitorWidget.setVisible(False)
            self.log.debug("RAM monitor: disabled, hiding widget")

        self._update_status_bar_visibility()

    def _update_status_bar_visibility(self):
        """Show or hide status bar based on whether any monitor widgets should be visible."""
        warning_has_messages = self.warningErrorWidget is not None and self.warningErrorWidget.has_messages()
        self.statusBar().setVisible(
            self._ram_monitor_should_show or self._bp_monitor_should_show or warning_has_messages
        )

    def _connect_ram_monitor_widget(self):
        """Connect RAM monitor widget to memory monitor during acquisition."""
        import control._def

        if not control._def.ENABLE_MEMORY_PROFILING:
            return

        if self.ramMonitorWidget is None:
            return

        # Connect to the memory monitor from the multipointController for more detailed acquisition tracking
        if self.multipointController is not None and self.multipointController._memory_monitor is not None:
            self.log.info("RAM monitor: connecting widget to memory monitor for acquisition")
            self.ramMonitorWidget.connect_monitor(self.multipointController._memory_monitor)

    def _disconnect_ram_monitor_widget(self):
        """Disconnect RAM monitor widget from acquisition memory monitor."""
        import control._def

        if self.ramMonitorWidget is not None:
            self.ramMonitorWidget.disconnect_monitor()
            # Control monitoring based on current profiling setting
            if control._def.ENABLE_MEMORY_PROFILING:
                # Resume background monitoring, preserve peak from acquisition
                self.ramMonitorWidget.start_monitoring(reset_peak=False)
                self.log.debug("RAM monitor: disconnected from acquisition, continuing background monitoring")
            else:
                # Stop monitoring entirely when profiling is disabled
                self.ramMonitorWidget.stop_monitoring()
                self.log.debug("RAM monitor: disconnected from acquisition, monitoring stopped (profiling disabled)")

    def _connect_backpressure_monitor_widget(self):
        """Connect backpressure monitor widget during acquisition."""
        import control._def

        if not control._def.ACQUISITION_THROTTLING_ENABLED:
            self.log.debug("Backpressure monitor: throttling disabled, skipping")
            return

        if self.backpressureMonitorWidget is None:
            self.log.warning("Backpressure monitor: widget not initialized")
            return

        # Get the backpressure controller from the multipoint worker
        bp_controller = self.multipointController.backpressure_controller
        if bp_controller is None:
            self.log.debug("Backpressure monitor: no controller available from worker")
            return

        if not bp_controller.enabled:
            self.log.debug("Backpressure monitor: controller exists but is disabled")
            return

        self.log.info("Backpressure monitor: connecting widget to backpressure controller")
        self._bp_monitor_should_show = True
        self.backpressureMonitorWidget.start_monitoring(bp_controller)
        self.backpressureMonitorWidget.setVisible(True)
        self._update_status_bar_visibility()

    def _disconnect_backpressure_monitor_widget(self):
        """Disconnect backpressure monitor widget after acquisition."""
        if self.backpressureMonitorWidget is None:
            return

        self._bp_monitor_should_show = False
        self.backpressureMonitorWidget.stop_monitoring()
        self.backpressureMonitorWidget.setVisible(False)
        self.log.debug("Backpressure monitor: disconnected from acquisition")
        self._update_status_bar_visibility()

    def _connect_warning_handler(self):
        """Connect logging handler to warning/error widget."""
        if self.warningErrorWidget is None:
            return

        self._warning_handler = widgets.QtLoggingHandler()
        self._warning_handler.signal_message_logged.connect(self.warningErrorWidget.add_message)
        squid.logging.get_logger().addHandler(self._warning_handler)
        self.log.debug("Warning/error widget: connected logging handler")

    def _disconnect_warning_handler(self):
        """Disconnect logging handler from warning/error widget.

        Uses robust error handling to ensure cleanup completes even if
        individual operations fail (e.g., handler already removed).
        """
        if self._warning_handler is not None:
            try:
                squid.logging.get_logger().removeHandler(self._warning_handler)
            except Exception as e:
                self.log.debug(f"Error removing warning handler (may already be removed): {e}")

            try:
                self._warning_handler.close()
            except Exception as e:
                self.log.debug(f"Error closing warning handler: {e}")

            self._warning_handler = None
            self.log.debug("Warning/error widget: disconnected logging handler")

    def onStartLive(self):
        self.imageDisplayTabs.setCurrentIndex(0)
        if self.alignmentWidget is not None:
            self.alignmentWidget.enable()

    def move_from_click_image(self, click_x, click_y, image_width, image_height):
        if self.navigationWidget.get_click_to_move_enabled():
            pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()

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
            self.log.debug(f"Click to move disabled, ignoring click at {click_x=}, {click_y=}")

    def move_from_click_mm(self, x_mm, y_mm):
        if self.navigationWidget.get_click_to_move_enabled():
            self.log.debug(f"Click to move enabled, moving to {x_mm=}, {y_mm=}")
            self.move_to_mm(x_mm, y_mm)
        else:
            self.log.debug(f"Click to move disabled, ignoring click request for {x_mm=}, {y_mm=}")

    def move_to_mm(self, x_mm, y_mm):
        self.stage.move_x_to(x_mm)
        self.stage.move_y_to(y_mm)

    def showEvent(self, event):
        """Handle window show event to initialize visibility-dependent widgets."""
        super().showEvent(event)
        # Initialize visibility-dependent widgets now that window is shown
        if hasattr(self, "_show_event_initialized") and self._show_event_initialized:
            return  # Only initialize once
        self._show_event_initialized = True
        self._update_ram_monitor_visibility()
        self._connect_warning_handler()

    def _on_plate_view_fov_clicked(self, well_id: str, fov_index: int) -> None:
        """Handle double-click on plate view: navigate NDViewer to FOV and switch tab."""
        if self.ndviewerTab is None:
            self.log.debug("FOV click ignored: NDViewer tab not available")
            return

        if not self.ndviewerTab.go_to_fov(well_id, fov_index):
            self.log.debug(f"Could not navigate to FOV well={well_id}, fov={fov_index} - may not exist in dataset")
            return

        ndviewer_tab_idx = self.imageDisplayTabs.indexOf(self.ndviewerTab)
        if ndviewer_tab_idx >= 0:
            self.imageDisplayTabs.setCurrentIndex(ndviewer_tab_idx)
        else:
            self.log.warning("NDViewer tab exists but not found in tab widget")

    def closeEvent(self, event):
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
            squid.stage.utils.cache_position(pos=self.stage.get_pos(), stage_config=self.stage.get_config())
        except ValueError as e:
            self.log.error(f"Couldn't cache position while closing.  Ignoring and continuing. Error is: {e}")

        # Save camera settings (binning, pixel format)
        squid.camera.settings_cache.save_camera_settings(self.camera)

        # Disconnect warning/error logging handler
        self._disconnect_warning_handler()

        # Clean up multipoint controller resources (queues, timers, process pools)
        if self.multipointController is not None:
            try:
                self.multipointController.close()
            except Exception:
                self.log.exception("Error closing multipoint controller during shutdown")

        # Clean up NDViewer resources (file handles, timers)
        if self.ndviewerTab is not None:
            try:
                self.ndviewerTab.close()
            except Exception:
                self.log.exception("Error closing NDViewer tab during shutdown")

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
