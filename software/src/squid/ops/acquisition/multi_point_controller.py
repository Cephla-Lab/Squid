import dataclasses
import json
import os
import pathlib
import tempfile
import time
from datetime import datetime
from enum import Enum, auto
from threading import Thread
from typing import Optional, Tuple, Any, List, Set

import numpy as np
import pandas as pd

import squid.core.utils.hardware_utils as utils
from squid.storage import utils_acquisition
import _def
from squid.mcs.controllers.autofocus import AutoFocusController
from squid.ops.configuration import ChannelConfigurationManager
from squid.ops.acquisition.multi_point_utils import (
    ScanPositionInformation,
    AcquisitionParameters,
)
from squid.ops.navigation import ScanCoordinates
from squid.mcs.controllers.autofocus import LaserAutofocusController
from squid.mcs.controllers.live_controller import LiveController
from squid.ops.acquisition.multi_point_worker import MultiPointWorker
from squid.ops.navigation import ObjectiveStore
from squid.core.state_machine import StateMachine, InvalidStateTransition
from squid.core.mode_gate import GlobalMode, GlobalModeGate
import squid.core.logging

from typing import TYPE_CHECKING

from squid.core.events import (
    SetFluidicsRoundsCommand,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    SetAcquisitionChannelsCommand,
    StartNewExperimentCommand,
    StartAcquisitionCommand,
    StopAcquisitionCommand,
    AcquisitionStateChanged,
    AcquisitionProgress,
    AcquisitionRegionProgress,
    AcquisitionWorkerFinished,
    AcquisitionWorkerProgress,
)

if TYPE_CHECKING:
    from squid.mcs.services import (
        CameraService,
        StageService,
        PeripheralService,
        PiezoService,
        FluidicsService,
        NL5Service,
        IlluminationService,
        FilterWheelService,
    )
    from squid.core.events import EventBus


class AcquisitionControllerState(Enum):
    """State machine states for MultiPointController."""

    IDLE = auto()
    PREPARING = auto()  # Setting up acquisition
    RUNNING = auto()  # Worker thread running
    ABORTING = auto()  # Abort requested
    COMPLETED = auto()  # Successfully completed
    FAILED = auto()  # Error occurred


class MultiPointController(StateMachine[AcquisitionControllerState]):
    def __init__(
        self,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        channel_configuration_manager: ChannelConfigurationManager,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        event_bus: "EventBus",
        *,
        scan_coordinates: Optional[ScanCoordinates] = None,
        laser_autofocus_controller: Optional[LaserAutofocusController] = None,
        piezo_service: Optional["PiezoService"] = None,
        fluidics_service: Optional["FluidicsService"] = None,
        nl5_service: Optional["NL5Service"] = None,
        illumination_service: Optional["IlluminationService"] = None,
        filter_wheel_service: Optional["FilterWheelService"] = None,
        mode_gate: Optional[GlobalModeGate] = None,
        stream_handler: Optional[object] = None,
    ):
        # Initialize state machine with transitions
        # IDLE -> PREPARING: start acquisition
        # PREPARING -> RUNNING: worker started
        # PREPARING -> FAILED: setup failed
        # RUNNING -> ABORTING: abort requested
        # RUNNING -> COMPLETED: normal completion
        # RUNNING -> FAILED: error during acquisition
        # ABORTING -> COMPLETED: abort finished
        # ABORTING -> FAILED: abort with error
        # COMPLETED/FAILED -> IDLE: ready for next
        transitions = {
            AcquisitionControllerState.IDLE: {AcquisitionControllerState.PREPARING},
            AcquisitionControllerState.PREPARING: {
                AcquisitionControllerState.RUNNING,
                AcquisitionControllerState.FAILED,
            },
            AcquisitionControllerState.RUNNING: {
                AcquisitionControllerState.ABORTING,
                AcquisitionControllerState.COMPLETED,
                AcquisitionControllerState.FAILED,
            },
            AcquisitionControllerState.ABORTING: {
                AcquisitionControllerState.COMPLETED,
                AcquisitionControllerState.FAILED,
            },
            AcquisitionControllerState.COMPLETED: {AcquisitionControllerState.IDLE},
            AcquisitionControllerState.FAILED: {AcquisitionControllerState.IDLE},
        }
        super().__init__(
            initial_state=AcquisitionControllerState.IDLE,
            transitions=transitions,
            event_bus=event_bus,
            name="MultiPointController",
        )
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.liveController: LiveController = live_controller
        self.autofocusController: AutoFocusController = autofocus_controller
        self.laserAutoFocusController: LaserAutofocusController = (
            laser_autofocus_controller
        )
        self.objectiveStore: ObjectiveStore = objective_store
        self.channelConfigurationManager: ChannelConfigurationManager = (
            channel_configuration_manager
        )
        self.multiPointWorker: Optional[MultiPointWorker] = None
        self.thread: Optional[Thread] = None

        # Store services and event bus
        self._camera_service = camera_service
        self._stage_service = stage_service
        self._peripheral_service = peripheral_service
        self._piezo_service = piezo_service
        self._fluidics_service = fluidics_service
        self._nl5_service = nl5_service
        self._illumination_service = illumination_service
        self._filter_wheel_service = filter_wheel_service
        self._mode_gate = mode_gate
        self._stream_handler = stream_handler

        if self._stage_service is None or self._camera_service is None or self._peripheral_service is None:
            raise ValueError(
                "MultiPointController requires StageService, CameraService, and PeripheralService"
            )

        self.NX: int = 1
        self.deltaX: float = _def.Acquisition.DX
        self.NY: int = 1
        self.deltaY: float = _def.Acquisition.DY
        self.NZ: int = 1
        # TODO(imo): Switch all to consistent mm units
        self.deltaZ: float = _def.Acquisition.DZ / 1000
        self.Nt: int = 1
        self.deltat: float = 0

        self.do_autofocus: bool = False
        self.do_reflection_af: bool = False
        self.display_resolution_scaling: float = (
            _def.Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
        )
        self.use_piezo: bool = _def.MULTIPOINT_USE_PIEZO_FOR_ZSTACKS
        self.experiment_ID: Optional[str] = None
        self.use_manual_focus_map: bool = False
        self.base_path: Optional[str] = None
        self.use_fluidics: bool = False

        self.focus_map: Optional[Any] = None
        self.gen_focus_map: bool = False
        self.focus_map_storage: List[Tuple[float, float, float]] = []
        self.already_using_fmap: bool = False
        self.selected_configurations: List[Any] = []
        self.scanCoordinates: Optional[ScanCoordinates] = scan_coordinates
        self.old_images_per_page: int = 1
        self.z_range: Optional[Tuple[float, float]] = None
        self.z_stacking_config: str = _def.Z_STACKING_CONFIG

        self._start_position: Optional[squid.core.abc.Pos] = None

        # Subscribe to EventBus commands
        if self._event_bus:
            self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        if self._event_bus is None:
            return
        # Command handlers
        self._event_bus.subscribe(SetFluidicsRoundsCommand, self._on_set_fluidics_rounds)
        self._event_bus.subscribe(SetAcquisitionParametersCommand, self._on_set_acquisition_parameters)
        self._event_bus.subscribe(SetAcquisitionPathCommand, self._on_set_acquisition_path)
        self._event_bus.subscribe(SetAcquisitionChannelsCommand, self._on_set_acquisition_channels)
        self._event_bus.subscribe(StartNewExperimentCommand, self._on_start_new_experiment)
        self._event_bus.subscribe(StartAcquisitionCommand, self._on_start_acquisition)
        self._event_bus.subscribe(StopAcquisitionCommand, self._on_stop_acquisition)
        # Worker event handlers
        self._event_bus.subscribe(AcquisitionWorkerFinished, self._on_worker_finished)
        self._event_bus.subscribe(AcquisitionWorkerProgress, self._on_worker_progress)

    def _publish_state_changed(
        self, old_state: AcquisitionControllerState, new_state: AcquisitionControllerState
    ) -> None:
        """Publish state change event (StateMachine abstract method)."""
        # The AcquisitionStateChanged event is already published via _publish_acquisition_state
        # This method provides additional state machine state info if needed
        pass  # State publishing handled by _publish_acquisition_state for compatibility

    def _require_experiment_id(self) -> str:
        """Ensure an experiment ID exists before publishing acquisition events."""
        if not self.experiment_ID:
            raise RuntimeError(
                "Experiment ID is not set. Call start_new_experiment before running acquisition."
            )
        return self.experiment_ID

    def _ensure_experiment_ready(self, requested_id: Optional[str]) -> None:
        """
        Ensure an experiment folder/ID exists before starting acquisition.

        If no experiment is active, create one (using requested_id if provided,
        otherwise a default label). If a different id is requested than the
        current one, start a new experiment with that id.
        """
        if self.experiment_ID and (requested_id is None or requested_id == self.experiment_ID):
            return  # Already prepared
        if self.base_path is None:
            raise RuntimeError(
                "Cannot start acquisition without a base path. Set base_path via set_base_path or SetAcquisitionPathCommand first."
            )
        next_id = requested_id or "auto_experiment"
        self.start_new_experiment(next_id)

    def acquisition_in_progress(self) -> bool:
        """Check if acquisition is running (uses state machine)."""
        return self._is_in_state(
            AcquisitionControllerState.PREPARING,
            AcquisitionControllerState.RUNNING,
            AcquisitionControllerState.ABORTING,
        )

    def set_use_piezo(self, checked: bool) -> None:
        if checked and (self._piezo_service is None or not self._piezo_service.is_available):
            raise ValueError("Cannot enable piezo - no piezo stage configured")
        self.use_piezo = checked
        # TODO(imo): Why do we only allow runtime updates of use_piezo (not all the other params?)
        if self.multiPointWorker:
            self.multiPointWorker.update_use_piezo(checked)

    def set_z_stacking_config(self, z_stacking_config_index: int) -> None:
        if z_stacking_config_index in _def.Z_STACKING_CONFIG_MAP:
            self.z_stacking_config = _def.Z_STACKING_CONFIG_MAP[
                z_stacking_config_index
            ]
        print(f"z-stacking configuration set to {self.z_stacking_config}")

    def set_z_range(self, minZ: float, maxZ: float) -> None:
        self.z_range = (minZ, maxZ)

    def set_NX(self, N: int) -> None:
        self.NX = N

    def set_NY(self, N: int) -> None:
        self.NY = N

    def set_NZ(self, N: int) -> None:
        self.NZ = N

    def set_Nt(self, N: int) -> None:
        self.Nt = N

    def set_deltaX(self, delta: float) -> None:
        self.deltaX = delta

    def set_deltaY(self, delta: float) -> None:
        self.deltaY = delta

    def set_deltaZ(self, delta_um: float) -> None:
        self.deltaZ = delta_um / 1000

    def set_deltat(self, delta: float) -> None:
        self.deltat = delta

    def set_af_flag(self, flag: bool) -> None:
        self.do_autofocus = flag

    def set_reflection_af_flag(self, flag: bool) -> None:
        self.do_reflection_af = flag

    def set_manual_focus_map_flag(self, flag: bool) -> None:
        self.use_manual_focus_map = flag

    def set_gen_focus_map_flag(self, flag: bool) -> None:
        self.gen_focus_map = flag
        if not flag:
            self.autofocusController.set_focus_map_use(False)

    def set_focus_map(self, focusMap: Optional[Any]) -> None:
        self.focus_map = focusMap  # None if dont use focusMap

    def set_base_path(self, path: str) -> None:
        self.base_path = path

    def set_use_fluidics(self, use_fluidics: bool) -> None:
        self.use_fluidics = use_fluidics

    def start_new_experiment(
        self, experiment_ID: str
    ) -> None:  # @@@ to do: change name to prepare_folder_for_new_experiment
        # generate unique experiment ID
        self.experiment_ID = (
            experiment_ID.replace(" ", "_")
            + "_"
            + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        )
        self.recording_start_time = time.time()
        # create a new folder
        utils.ensure_directory_exists(os.path.join(self.base_path, self.experiment_ID))
        self.channelConfigurationManager.write_configuration_selected(
            self.objectiveStore.current_objective,
            self.selected_configurations,
            os.path.join(self.base_path, self.experiment_ID) + "/configurations.xml",
        )  # save the configuration for the experiment
        # Prepare acquisition parameters
        acquisition_parameters = {
            "dx(mm)": self.deltaX,
            "Nx": self.NX,
            "dy(mm)": self.deltaY,
            "Ny": self.NY,
            "dz(um)": self.deltaZ * 1000 if self.deltaZ != 0 else 1,
            "Nz": self.NZ,
            "dt(s)": self.deltat,
            "Nt": self.Nt,
            "with AF": self.do_autofocus,
            "with reflection AF": self.do_reflection_af,
            "with manual focus map": self.use_manual_focus_map,
        }
        try:  # write objective data if it is available
            current_objective = self.objectiveStore.current_objective
            objective_info = self.objectiveStore.objectives_dict.get(
                current_objective, {}
            )
            acquisition_parameters["objective"] = {}
            for k in objective_info.keys():
                acquisition_parameters["objective"][k] = objective_info[k]
            acquisition_parameters["objective"]["name"] = current_objective
        except Exception:
            try:
                objective_info = _def.OBJECTIVES[_def.DEFAULT_OBJECTIVE]
                acquisition_parameters["objective"] = {}
                for k in objective_info.keys():
                    acquisition_parameters["objective"][k] = objective_info[k]
                acquisition_parameters["objective"]["name"] = (
                    _def.DEFAULT_OBJECTIVE
                )
            except Exception:
                pass
        # TODO: USE OBJECTIVE STORE DATA
        acquisition_parameters["sensor_pixel_size_um"] = (
            self._camera_service.get_pixel_size_binned_um()
        )
        acquisition_parameters["tube_lens_mm"] = _def.TUBE_LENS_MM
        f = open(
            os.path.join(self.base_path, self.experiment_ID)
            + "/acquisition parameters.json",
            "w",
        )
        f.write(json.dumps(acquisition_parameters))
        f.close()

    def set_selected_configurations(
        self, selected_configurations_name: List[str]
    ) -> None:
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.channelConfigurationManager.get_channel_configuration_by_name(
                self.objectiveStore.current_objective, configuration_name
            )
            if config:
                self.selected_configurations.append(config)

    def get_acquisition_image_count(self) -> int:
        """
        Given the current settings on this controller, return how many images an acquisition will
        capture and save to disk.

        NOTE: This does not cover debug images (eg: auto focus) or user created images (eg: custom scripts).

        NOTE: This does attempt to include the "merged" image if that config is enabled.

        Raises a ValueError if the class is not configured for a valid acquisition.
        """
        try:
            # We have Nt timepoints.  For each timepoint, we capture images at all the regions.  Each
            # region has a list of coordinates that we capture at, and at each coordinate we need to
            # do a capture for each requested camera + lighting + other configuration selected.  So
            # total image count is:
            coords_per_region = [
                len(region_coords)
                for (
                    region_id,
                    region_coords,
                ) in self.scanCoordinates.region_fov_coordinates.items()
            ]
            all_regions_coord_count = sum(coords_per_region)

            non_merged_images = (
                self.Nt
                * self.NZ
                * all_regions_coord_count
                * len(self.selected_configurations)
            )
            # When capturing merged images, we capture 1 per fov (where all the configurations are merged)
            merged_images = (
                self.Nt * self.NZ * all_regions_coord_count
                if _def.MERGE_CHANNELS
                else 0
            )

            return non_merged_images + merged_images
        except AttributeError:
            # We don't init all fields in __init__, so it's easy to get attribute errors.  We consider
            # this "not configured" and want it to be a ValueError.
            raise ValueError(
                "Not properly configured for an acquisition, cannot calculate image count."
            )

    def _temporary_get_an_image_hack(self) -> Tuple[Optional[np.ndarray], bool]:
        was_streaming: bool = self._camera_service.get_is_streaming()
        callbacks_were_enabled: bool = self._camera_service.get_callbacks_enabled()
        self._camera_service.enable_callbacks(False)
        test_frame: Optional[CameraFrame] = None
        if not was_streaming:
            self._camera_service.start_streaming()
        try:
            if (
                self.liveController.trigger_mode == _def.TriggerMode.SOFTWARE
                or self.liveController.trigger_mode == _def.TriggerMode.HARDWARE
            ):
                self._camera_service.send_trigger()
            test_frame = self._camera_service.read_camera_frame()
        finally:
            self._camera_service.enable_callbacks(callbacks_were_enabled)
            if not was_streaming:
                self._camera_service.stop_streaming()
        return (
            (test_frame.frame, test_frame.is_color()) if test_frame else (None, False)
        )

    def get_estimated_acquisition_disk_storage(self) -> int:
        """
        This does its best to return the number of bytes needed to store the settings for the currently
        configured acquisition on disk.  If you don't have at least this amount of disk space available
        when starting this acquisition, it is likely it will fail with an "out of disk space" error.
        """
        # TODO(imo): This needs updating for AbstractCamera
        if not len(
            self.channelConfigurationManager.get_configurations(
                self.objectiveStore.current_objective
            )
        ):
            raise ValueError(
                "Cannot calculate disk space requirements without any valid configurations."
            )
        first_config = self.channelConfigurationManager.get_configurations(
            self.objectiveStore.current_objective
        )[0]

        # Our best bet is to grab an image, and use that for our size estimate.
        test_image: Optional[np.ndarray] = None
        is_color: bool = True
        try:
            test_image, is_color = self._temporary_get_an_image_hack()
        except Exception:
            self._log.exception(
                "Couldn't capture image from camera for size estimate, using worst cast image."
            )
            # Not ideal that we need to catch Exception, but the camera implementations vary wildly...
            pass

        if test_image is None:
            is_color = squid.core.abc.CameraPixelFormat.is_color_format(
                self._camera_service.get_pixel_format()
            )
            # Do our best to create a fake image with the correct properties.
            # TODO(imo): It'd be better to pull this from our camera but need to wait for AbstractCamera for a consistent way to do that.
            width, height = self._camera_service.get_crop_size()
            test_image = np.random.randint(
                2**16 - 1, size=(height, width, (3 if is_color else 1)), dtype=np.uint16
            )

        # Depending on settings, we modify the image before saving.  This means we need to actually save an image
        # to see how much disk space it takes up.  This can be very wrong (eg: if we compress during saving, then
        # it is dependent on the data), but is better than just guessing based on raw image size.
        with tempfile.TemporaryDirectory() as temp_save_dir:
            file_id = "test_id"
            test_config = first_config
            size_before = utils.get_directory_disk_usage(pathlib.Path(temp_save_dir))
            utils_acquisition.save_image(
                test_image, file_id, temp_save_dir, test_config, is_color
            )
            size_after = utils.get_directory_disk_usage(pathlib.Path(temp_save_dir))

            size_per_image = size_after - size_before

        # Add in 100kB for non-image files.  This is normally more like 10k total, so this gives us extra.
        non_image_file_size = 100 * 1024

        return size_per_image * self.get_acquisition_image_count() + non_image_file_size

    def run_acquisition(self, acquire_current_fov: bool = False) -> None:
        import time as _time
        self._log.info("run_acquisition: ENTER")
        _t0 = _time.perf_counter()

        # Check if in IDLE state
        if not self._is_in_state(AcquisitionControllerState.IDLE):
            self._log.warning(f"Cannot start acquisition - state is {self.state.name}")
            return

        try:
            # Transition to PREPARING
            self._transition_to(AcquisitionControllerState.PREPARING)

            if self._mode_gate:
                self._mode_gate.set_mode(GlobalMode.ACQUIRING, reason="acquisition start")

            # Ensure we have an experiment ID before publishing any acquisition events
            self._require_experiment_id()

            self._log.info(f"run_acquisition: passed state check ({(_time.perf_counter()-_t0)*1000:.1f}ms)")

            if not self.validate_acquisition_settings():
                self._publish_acquisition_state(in_progress=False, allow_missing_experiment_id=True)
                if self._mode_gate:
                    self._mode_gate.set_mode(GlobalMode.IDLE, reason="acquisition start failed")
                self._transition_to(AcquisitionControllerState.FAILED)
                self._transition_to(AcquisitionControllerState.IDLE)
                return

            self._log.info(f"run_acquisition: passed validate_acquisition_settings ({(_time.perf_counter()-_t0)*1000:.1f}ms)")

            # Publish acquisition started state
            self._log.info("run_acquisition: about to publish in_progress=True")
            self._publish_acquisition_state(in_progress=True)
            self._log.info(f"run_acquisition: published in_progress=True ({(_time.perf_counter()-_t0)*1000:.1f}ms)")

            self._log.info("start multipoint")
            self._start_position = self._stage_service.get_position()

            if self.z_range is None:
                self.z_range = (
                    self._start_position.z_mm,
                    self._start_position.z_mm + self.deltaZ * (self.NZ - 1),
                )

            acquisition_scan_coordinates: ScanCoordinates = self.scanCoordinates
            self.run_acquisition_current_fov: bool = False
            if acquire_current_fov:
                pos = self._stage_service.get_position()
                # No callback - we don't want to clobber existing info with this one off fov acquisition
                # Don't pass event_bus to avoid publishing ClearedScanCoordinates globally
                acquisition_scan_coordinates = ScanCoordinates(
                    objectiveStore=self.scanCoordinates.objectiveStore,
                    stage=self.scanCoordinates.stage,
                    camera=self.scanCoordinates.camera,
                    event_bus=None,
                )
                acquisition_scan_coordinates.add_single_fov_region(
                    "current", center_x=pos.x_mm, center_y=pos.y_mm, center_z=pos.z_mm
                )
                self.run_acquisition_current_fov = True

            scan_position_information: ScanPositionInformation = (
                ScanPositionInformation.from_scan_coordinates(acquisition_scan_coordinates)
            )

            # Save coordinates to CSV in top level folder
            try:
                coordinates_df: pd.DataFrame = pd.DataFrame(
                    columns=["region", "x (mm)", "y (mm)", "z (mm)"]
                )
                for (
                    region_id,
                    coords_list,
                ) in scan_position_information.scan_region_fov_coords_mm.items():
                    for coord in coords_list:
                        row = {"region": region_id, "x (mm)": coord[0], "y (mm)": coord[1]}
                        # Add z coordinate if available
                        if len(coord) > 2:
                            row["z (mm)"] = coord[2]
                        coordinates_df = pd.concat(
                            [coordinates_df, pd.DataFrame([row])], ignore_index=True
                        )
                coordinates_df.to_csv(
                    os.path.join(self.base_path, self.experiment_ID, "coordinates.csv"),
                    index=False,
                )
            except Exception as exc:
                raise RuntimeError("Failed to prepare coordinates for acquisition") from exc

            self._log.info(
                f"num fovs: {sum(len(coords) for coords in scan_position_information.scan_region_fov_coords_mm.values())}"
            )
            self._log.info(
                f"num regions: {len(scan_position_information.scan_region_coords_mm)}"
            )
            self._log.info(f"region ids: {scan_position_information.scan_region_names}")
            self._log.info(
                f"region centers: {scan_position_information.scan_region_coords_mm}"
            )
            # Debug: show FOV counts per region
            for region_id, coords in scan_position_information.scan_region_fov_coords_mm.items():
                self._log.info(f"  region '{region_id}': {len(coords)} FOVs")

            self.configuration_before_running_multipoint: Any = (
                self.liveController.currentConfiguration
            )
            # stop live
            if self.liveController.is_live:
                self.liveController_was_live_before_multipoint: bool = True
                self.liveController.stop_live()  # @@@ to do: also uncheck the live button
            else:
                self.liveController_was_live_before_multipoint: bool = False

            self.camera_callback_was_enabled_before_multipoint: bool = (
                self._camera_service.get_callbacks_enabled()
            )
            # We need callbacks, because we trigger and then use callbacks for image processing.  This
            # lets us do overlapping triggering (soon).
            self._camera_service.enable_callbacks(True)

            # run the acquisition
            self.timestamp_acquisition_started: float = time.time()

            if self.focus_map:
                self._log.info("Using focus surface for Z interpolation")
                for region_id in scan_position_information.scan_region_names:
                    region_fov_coords = scan_position_information.scan_region_fov_coords_mm[
                        region_id
                    ]
                    # Convert each tuple to list for modification
                    for i, coords in enumerate(region_fov_coords):
                        x, y = coords[:2]  # This handles both (x,y) and (x,y,z) formats
                        z = self.focus_map.interpolate(x, y, region_id)
                        # Modify the list directly
                        region_fov_coords[i] = (x, y, z)
                        self.scanCoordinates.update_fov_z_level(region_id, i, z)

            elif self.gen_focus_map and not self.do_reflection_af:
                self._log.info("Generating autofocus plane for multipoint grid")
                bounds = self.scanCoordinates.get_scan_bounds()
                if not bounds:
                    self._publish_acquisition_state(in_progress=False, is_aborting=False, allow_missing_experiment_id=True)
                    if self._event_bus:
                        try:
                            experiment_id = self._require_experiment_id()
                            self._event_bus.publish(
                                AcquisitionWorkerFinished(
                                    experiment_id=experiment_id,
                                    success=False,
                                    error="Invalid scan bounds",
                                    final_fov_count=0,
                                )
                            )
                        except RuntimeError:
                            pass
                    if self._mode_gate:
                        self._mode_gate.set_mode(GlobalMode.IDLE, reason="acquisition start failed")
                    self._transition_to(AcquisitionControllerState.FAILED)
                    self._transition_to(AcquisitionControllerState.IDLE)
                    return
                x_min, x_max = bounds["x"]
                y_min, y_max = bounds["y"]

                # Calculate scan dimensions and center
                x_span = abs(x_max - x_min)
                y_span = abs(y_max - y_min)
                x_center = (x_max + x_min) / 2
                y_center = (y_max + y_min) / 2

                # Determine grid size based on scan dimensions
                if x_span < self.deltaX:
                    fmap_Nx = 2
                    fmap_dx = self.deltaX  # Force deltaX spacing for small scans
                else:
                    fmap_Nx = min(4, max(2, int(x_span / self.deltaX) + 1))
                    fmap_dx = max(self.deltaX, x_span / (fmap_Nx - 1))

                if y_span < self.deltaY:
                    fmap_Ny = 2
                    fmap_dy = self.deltaY  # Force deltaY spacing for small scans
                else:
                    fmap_Ny = min(4, max(2, int(y_span / self.deltaY) + 1))
                    fmap_dy = max(self.deltaY, y_span / (fmap_Ny - 1))

                # Calculate starting corner position (top-left of the AF map grid)
                starting_x_mm = x_center - (fmap_Nx - 1) * fmap_dx / 2
                starting_y_mm = y_center - (fmap_Ny - 1) * fmap_dy / 2
                # TODO(sm): af map should be a grid mapped to a surface, instead of just corners mapped to a plane
                try:
                    # Store existing AF map if any
                    self.focus_map_storage = []
                    self.already_using_fmap = self.autofocusController.use_focus_map
                    for x, y, z in self.autofocusController.focus_map_coords:
                        self.focus_map_storage.append((x, y, z))

                    # Define grid corners for AF map
                    coord1 = (starting_x_mm, starting_y_mm)  # Starting corner
                    coord2 = (
                        starting_x_mm + (fmap_Nx - 1) * fmap_dx,
                        starting_y_mm,
                    )  # X-axis corner
                    coord3 = (
                        starting_x_mm,
                        starting_y_mm + (fmap_Ny - 1) * fmap_dy,
                    )  # Y-axis corner

                    self._log.info(f"Generating AF Map: Nx={fmap_Nx}, Ny={fmap_Ny}")
                    self._log.info(f"Spacing: dx={fmap_dx:.3f}mm, dy={fmap_dy:.3f}mm")
                    self._log.info(f"Center:  x=({x_center:.3f}mm, y={y_center:.3f}mm)")

                    # Generate and enable the AF map
                    self.autofocusController.gen_focus_map(coord1, coord2, coord3)
                    self.autofocusController.set_focus_map_use(True)

                    # Return to center position
                    self._stage_service.move_x_to(x_center)
                    self._stage_service.move_y_to(y_center)

                except ValueError as exc:
                    raise RuntimeError("Invalid coordinates for autofocus plane") from exc

            acquisition_params: AcquisitionParameters = self.build_params(
                scan_position_information=scan_position_information
            )
            self.multiPointWorker = MultiPointWorker(
                auto_focus_controller=self.autofocusController,
                laser_auto_focus_controller=self.laserAutoFocusController,
                objective_store=self.objectiveStore,
                channel_configuration_mananger=self.channelConfigurationManager,
                acquisition_parameters=acquisition_params,
                extra_job_classes=[],
                # Pass services and event bus
                camera_service=self._camera_service,
                stage_service=self._stage_service,
                peripheral_service=self._peripheral_service,
                trigger_mode=getattr(self.liveController, "trigger_mode", _def.TriggerMode.SOFTWARE),
                illumination_service=self._illumination_service,
                filter_wheel_service=self._filter_wheel_service,
                enable_channel_auto_filter_switching=getattr(
                    self.liveController, "enable_channel_auto_filter_switching", True
                ),
                piezo_service=self._piezo_service,
                fluidics_service=self._fluidics_service,
                nl5_service=self._nl5_service,
                event_bus=self._event_bus,
                stream_handler=self._stream_handler,
            )
            # Allow tests/simulation to override long frame wait timeouts.
            if hasattr(self, "frame_wait_timeout_override_s"):
                self.multiPointWorker.frame_wait_timeout_override_s = getattr(
                    self, "frame_wait_timeout_override_s"
                )

            self.thread: Thread = Thread(
                target=self.multiPointWorker.run, name="Acquisition thread", daemon=True
            )
            self._log.info(f"run_acquisition: starting worker thread ({(_time.perf_counter()-_t0)*1000:.1f}ms)")
            self.thread.start()
            # Transition to RUNNING now that worker is started
            self._transition_to(AcquisitionControllerState.RUNNING)
            self._log.info(f"run_acquisition: worker thread started, returning ({(_time.perf_counter()-_t0)*1000:.1f}ms)")
        except Exception:
            self._log.exception("Failed to start acquisition")
            # Always try to notify listeners that we're no longer running
            self._publish_acquisition_state(in_progress=False, allow_missing_experiment_id=True)
            if self._mode_gate:
                self._mode_gate.set_mode(GlobalMode.IDLE, reason="acquisition start failed")

            if self._is_in_state(AcquisitionControllerState.PREPARING, AcquisitionControllerState.RUNNING):
                try:
                    self._transition_to(AcquisitionControllerState.FAILED)
                except InvalidStateTransition:
                    self._force_state(
                        AcquisitionControllerState.FAILED, reason="cleanup after failed start"
                    )

            if self._is_in_state(AcquisitionControllerState.FAILED):
                try:
                    self._transition_to(AcquisitionControllerState.IDLE)
                except InvalidStateTransition:
                    self._force_state(AcquisitionControllerState.IDLE, reason="cleanup after failed start")
            elif self._is_in_state(AcquisitionControllerState.PREPARING):
                self._force_state(AcquisitionControllerState.IDLE, reason="cleanup after failed start")

    def build_params(
        self, scan_position_information: ScanPositionInformation
    ) -> AcquisitionParameters:
        return AcquisitionParameters(
            experiment_ID=self.experiment_ID,
            base_path=self.base_path,
            selected_configurations=self.selected_configurations,
            acquisition_start_time=self.timestamp_acquisition_started,
            scan_position_information=scan_position_information,
            NX=self.NX,
            deltaX=self.deltaX,
            NY=self.NY,
            deltaY=self.deltaY,
            NZ=self.NZ,
            deltaZ=self.deltaZ,
            Nt=self.Nt,
            deltat=self.deltat,
            do_autofocus=self.do_autofocus,
            do_reflection_autofocus=self.do_reflection_af,
            use_piezo=self.use_piezo,
            display_resolution_scaling=self.display_resolution_scaling,
            z_stacking_config=self.z_stacking_config,
            z_range=self.z_range,
            use_fluidics=self.use_fluidics,
        )

    def _on_acquisition_completed(self, success: bool = True) -> None:
        """Cleanup after acquisition and publish finished state."""
        import threading
        thread_name = threading.current_thread().name
        self._log.info(f"MultiPointController._on_acquisition_completed called from thread {thread_name}")
        try:
            # Defensive: ensure camera streaming is stopped before any state restoration.
            # The worker should have stopped streaming, but this handles edge cases.
            if self._camera_service:
                self._camera_service.stop_streaming()

            # restore the previous selected mode
            if self.gen_focus_map:
                self.autofocusController.clear_focus_map()
                for x, y, z in self.focus_map_storage:
                    self.autofocusController.focus_map_coords.append((x, y, z))
                self.autofocusController.use_focus_map = self.already_using_fmap
            if self.configuration_before_running_multipoint is not None:
                self.liveController.set_microscope_mode(
                    self.configuration_before_running_multipoint
                )

            # Restore callbacks to pre-acquisition state
            self._camera_service.enable_callbacks(self.camera_callback_was_enabled_before_multipoint)

            self._log.info(
                f"total time for acquisition + processing + reset: {time.time() - self.recording_start_time}"
            )
            utils.create_done_file(os.path.join(self.base_path, self.experiment_ID))

            if self.run_acquisition_current_fov:
                self.run_acquisition_current_fov = False

            # Move stage back to start position BEFORE re-enabling live mode
            # This prevents live frames from being captured at intermediate positions
            if self._start_position:
                x_mm: float = self._start_position.x_mm
                y_mm: float = self._start_position.y_mm
                z_mm: float = self._start_position.z_mm
                self._log.info(
                    f"Moving back to start position: (x,y,z) [mm] = ({x_mm}, {y_mm}, {z_mm})"
                )
                self._stage_service.move_x_to(x_mm)
                self._stage_service.move_y_to(y_mm)
                self._stage_service.move_z_to(z_mm)
                self._start_position = None

            # re-enable live AFTER stage has returned to start position
            if self._mode_gate and self._mode_gate.get_mode() in (GlobalMode.ACQUIRING, GlobalMode.ABORTING):
                self._mode_gate.set_mode(GlobalMode.IDLE, reason="acquisition complete")
            if (
                self.liveController_was_live_before_multipoint
                and _def.RESUME_LIVE_AFTER_ACQUISITION
            ):
                self.liveController.start_live()
        except Exception:
            # Never let cleanup errors block UI re-enabling
            self._log.exception("Error during acquisition cleanup")
        finally:
            # Publish acquisition finished state even if cleanup fails
            self._publish_acquisition_state(in_progress=False)

            # Transition to COMPLETED or FAILED based on worker result
            # Only transition if we're in a state that can transition
            if self._is_in_state(AcquisitionControllerState.RUNNING, AcquisitionControllerState.ABORTING):
                if self._is_in_state(AcquisitionControllerState.ABORTING):
                    self._transition_to(AcquisitionControllerState.COMPLETED)
                elif success:
                    self._transition_to(AcquisitionControllerState.COMPLETED)
                else:
                    self._transition_to(AcquisitionControllerState.FAILED)

                if self._mode_gate and self._mode_gate.get_mode() in (GlobalMode.ACQUIRING, GlobalMode.ABORTING):
                    self._mode_gate.set_mode(GlobalMode.IDLE, reason="acquisition complete")
                self._transition_to(AcquisitionControllerState.IDLE)

    def request_abort_aquisition(self) -> None:
        # Only abort if we're actually running
        if not self._is_in_state(AcquisitionControllerState.RUNNING):
            self._log.warning(f"Cannot abort - state is {self.state.name}")
            return

        if self.multiPointWorker is not None:
            try:
                self.multiPointWorker.request_abort()
            except Exception:  # pragma: no cover - defensive
                self._log.exception("Failed to signal worker abort")
        if self._mode_gate:
            self._mode_gate.set_mode(GlobalMode.ABORTING, reason="acquisition abort requested")
        # Transition to ABORTING state
        self._transition_to(AcquisitionControllerState.ABORTING)
        # Publish aborting state
        self._publish_acquisition_state(in_progress=True, is_aborting=True)

    def validate_acquisition_settings(self) -> bool:
        """Validate settings before starting acquisition"""
        if (
            self.do_reflection_af
            and not self.laserAutoFocusController.laser_af_properties.has_reference
        ):
            self._log.error(
                "Laser Autofocus Not Ready - Please set the laser autofocus reference position before starting acquisition with laser AF enabled."
            )
            return False
        return True

    # =========================================================================
    # EventBus Command Handlers
    # =========================================================================

    def _on_set_fluidics_rounds(self, cmd: SetFluidicsRoundsCommand) -> None:
        """Handle SetFluidicsRoundsCommand from EventBus."""
        if self._fluidics_service is not None:
            self._fluidics_service.set_rounds(cmd.rounds)

    def _on_set_acquisition_parameters(self, cmd: SetAcquisitionParametersCommand) -> None:
        """Handle SetAcquisitionParametersCommand from EventBus."""
        if cmd.delta_z_um is not None:
            self.set_deltaZ(cmd.delta_z_um)
        if cmd.n_z is not None:
            self.set_NZ(cmd.n_z)
        if cmd.n_x is not None:
            self.set_NX(cmd.n_x)
        if cmd.n_y is not None:
            self.set_NY(cmd.n_y)
        if cmd.delta_x_mm is not None:
            self.set_deltaX(cmd.delta_x_mm)
        if cmd.delta_y_mm is not None:
            self.set_deltaY(cmd.delta_y_mm)
        if cmd.delta_t_s is not None:
            self.set_deltat(cmd.delta_t_s)
        if cmd.n_t is not None:
            self.set_Nt(cmd.n_t)
        if cmd.use_piezo is not None:
            self.set_use_piezo(cmd.use_piezo)
        if cmd.use_autofocus is not None:
            self.set_af_flag(cmd.use_autofocus)
        if cmd.use_reflection_af is not None:
            self.set_reflection_af_flag(cmd.use_reflection_af)
        if cmd.gen_focus_map is not None:
            self.set_gen_focus_map_flag(cmd.gen_focus_map)
        if cmd.use_manual_focus_map is not None:
            self.set_manual_focus_map_flag(cmd.use_manual_focus_map)
        if cmd.z_range is not None:
            self.set_z_range(cmd.z_range[0], cmd.z_range[1])
        if cmd.focus_map is not None:
            self.set_focus_map(cmd.focus_map)
        if cmd.use_fluidics is not None:
            self.set_use_fluidics(cmd.use_fluidics)

    def _on_set_acquisition_path(self, cmd: SetAcquisitionPathCommand) -> None:
        """Handle SetAcquisitionPathCommand from EventBus."""
        self.set_base_path(cmd.base_path)

    def _on_set_acquisition_channels(self, cmd: SetAcquisitionChannelsCommand) -> None:
        """Handle SetAcquisitionChannelsCommand from EventBus."""
        self.set_selected_configurations(cmd.channel_names)

    def _on_start_new_experiment(self, cmd: StartNewExperimentCommand) -> None:
        """Handle StartNewExperimentCommand from EventBus."""
        self.start_new_experiment(cmd.experiment_id)

    def _on_start_acquisition(self, cmd: StartAcquisitionCommand) -> None:
        """Handle StartAcquisitionCommand from EventBus."""
        # Ensure an experiment ID exists before running; auto-create if none exists.
        self._ensure_experiment_ready(cmd.experiment_id)
        self.run_acquisition(acquire_current_fov=cmd.acquire_current_fov)

    def _on_stop_acquisition(self, cmd: StopAcquisitionCommand) -> None:
        """Handle StopAcquisitionCommand from EventBus."""
        self.request_abort_aquisition()

    def _on_worker_finished(self, event: AcquisitionWorkerFinished) -> None:
        """Handle AcquisitionWorkerFinished event from worker thread.

        This is the primary mechanism for the worker to signal completion.
        Uses experiment_id to filter out stale events from previous acquisitions.
        """
        # Filter out stale events from previous acquisitions
        if event.experiment_id != self.experiment_ID:
            self._log.debug(
                f"Ignoring stale worker finished event: "
                f"expected {self.experiment_ID}, got {event.experiment_id}"
            )
            return

        # Only process if we're in a running/aborting state
        if not self._is_in_state(AcquisitionControllerState.RUNNING, AcquisitionControllerState.ABORTING):
            self._log.debug(
                f"Ignoring worker finished event - state is {self.state.name}"
            )
            return

        self._log.info(
            f"Worker finished: success={event.success}, "
            f"fov_count={event.final_fov_count}, error={event.error}"
        )

        # Call cleanup logic with success flag from worker
        self._on_acquisition_completed(success=event.success)

    def _on_worker_progress(self, event: AcquisitionWorkerProgress) -> None:
        """Handle AcquisitionWorkerProgress event from worker thread.

        Used for internal tracking. Validates experiment_id to filter stale events.
        """
        # Filter out stale events from previous acquisitions
        if event.experiment_id != self.experiment_ID:
            return

        # Only track if we're in a running state
        if not self._is_in_state(AcquisitionControllerState.RUNNING, AcquisitionControllerState.ABORTING):
            return

        # Log progress at debug level for tracking
        self._log.debug(
            f"Worker progress: region {event.current_region}/{event.total_regions}, "
            f"fov {event.current_fov}/{event.total_fovs}, "
            f"timepoint {event.current_timepoint}/{event.total_timepoints}"
        )

    def _publish_acquisition_state(
        self,
        in_progress: bool,
        is_aborting: bool = False,
        allow_missing_experiment_id: bool = False,
    ) -> None:
        """Publish acquisition state changed event."""
        import threading
        thread_name = threading.current_thread().name
        experiment_id = self.experiment_ID
        if allow_missing_experiment_id and not experiment_id:
            self._log.warning(
                "Skipping AcquisitionStateChanged publish because experiment_id is missing"
            )
            return
        experiment_id = self._require_experiment_id()
        self._log.info(f"_publish_acquisition_state(in_progress={in_progress}, is_aborting={is_aborting}, experiment_id={experiment_id}) from thread {thread_name}")
        if self._event_bus:
            self._event_bus.publish(AcquisitionStateChanged(
                in_progress=in_progress,
                experiment_id=experiment_id,
                is_aborting=is_aborting
            ))
            self._log.info(f"Published AcquisitionStateChanged(in_progress={in_progress}, experiment_id={experiment_id})")
