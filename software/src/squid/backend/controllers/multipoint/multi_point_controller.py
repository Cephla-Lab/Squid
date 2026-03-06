import dataclasses
import json
import math
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
from squid.backend.io import utils_acquisition
from squid.backend.io.acquisition_yaml import save_acquisition_yaml
import _def
from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.managers import ChannelConfigService
from squid.backend.controllers.multipoint.multi_point_utils import (
    ScanPositionInformation,
    AcquisitionParameters,
)
from squid.backend.controllers.multipoint.acquisition_config import AcquisitionConfig
from squid.backend.controllers.multipoint.acquisition_context import acquisition_context, AcquisitionContext
from squid.backend.controllers.multipoint.focus_operations import AutofocusExecutor
from squid.backend.controllers.multipoint.dependencies import AcquisitionDependencies
from squid.backend.managers import ScanCoordinates
from squid.backend.controllers.autofocus import LaserAutofocusController
from squid.backend.controllers.live_controller import LiveController
from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker
from squid.backend.managers import ObjectiveStore
from squid.core.state_machine import StateMachine, InvalidStateTransition
from squid.core.mode_gate import GlobalMode, GlobalModeGate
from squid.core.config.feature_flags import get_feature_flags
import squid.core.logging

from typing import TYPE_CHECKING

from squid.core.events import (
    handles,
    AutofocusMode,
    FocusLockSettings,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    SetAcquisitionChannelsCommand,
    SetFocusLockAutoSearchCommand,
    SetFocusLockParamsCommand,
    SetFluidicsRoundsCommand,
    StartNewExperimentCommand,
    StartAcquisitionCommand,
    StopAcquisitionCommand,
    AcquisitionFinished,
    AcquisitionStateChanged,
    AcquisitionProgress,
    AcquisitionRegionProgress,
    AcquisitionWorkerFinished,
    AcquisitionWorkerProgress,
    AcquisitionPaused,
    AcquisitionResumed,
    NDViewerStartAcquisition,
    NDViewerStartZarrAcquisition,
    NDViewerStartZarrAcquisition6D,
    NDViewerAcquisitionEnded,
)
from squid.backend.controllers.multipoint.events import (
    JumpToFovCommand,
    SkipFovCommand,
    RequeueFovCommand,
    DeferFovCommand,
    ReorderFovsCommand,
)

if TYPE_CHECKING:
    from squid.backend.services import (
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
    from squid.backend.controllers.autofocus.continuous_focus_lock import ContinuousFocusLockController
    from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator


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
        channel_configuration_manager: ChannelConfigService,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        event_bus: "EventBus",
        *,
        scan_coordinates: Optional[ScanCoordinates] = None,
        laser_autofocus_controller: Optional[LaserAutofocusController] = None,
        focus_lock_controller: Optional["ContinuousFocusLockController | FocusLockSimulator"] = None,
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
        self._feature_flags = get_feature_flags()
        self.liveController: LiveController = live_controller
        self.autofocusController: AutoFocusController = autofocus_controller
        self._autofocus_executor = AutofocusExecutor(
            autofocus_controller=self.autofocusController
        )
        self.laserAutoFocusController: LaserAutofocusController = (
            laser_autofocus_controller
        )
        self.objectiveStore: ObjectiveStore = objective_store
        self.channelConfigurationManager: ChannelConfigService = (
            channel_configuration_manager
        )
        self.multiPointWorker: Optional[MultiPointWorker] = None
        self.thread: Optional[Thread] = None
        self._current_round_index: int = 0
        self._start_fov_index: int = 0  # FOV index to start from (for resume)
        self._active_worker_experiment_id: Optional[str] = None

        # Store services and event bus
        self._camera_service = camera_service
        self._stage_service = stage_service
        self._peripheral_service = peripheral_service
        self._piezo_service = piezo_service
        self._focus_lock_controller = focus_lock_controller
        self._fluidics_service = fluidics_service
        self._nl5_service = nl5_service
        self._illumination_service = illumination_service
        self._filter_wheel_service = filter_wheel_service
        self._mode_gate = mode_gate
        self._stream_handler = stream_handler
        self._alignment_widget: Optional[object] = None

        if self._stage_service is None or self._camera_service is None or self._peripheral_service is None:
            raise ValueError(
                "MultiPointController requires StageService, CameraService, and PeripheralService"
            )

        self._config = AcquisitionConfig()
        self.experiment_ID: Optional[str] = None
        self.base_path: Optional[str] = None

        # NDViewer mode tracking for zarr 5D/6D display
        self._ndviewer_mode: str = "inactive"  # "inactive", "tiff", "zarr_5d", "zarr_6d"
        self._ndviewer_region_idx_offset: List[int] = []  # cumulative FOV offsets per region

        self._focus_map: Optional[Any] = None
        self.focus_map_storage: List[Tuple[float, float, float]] = []
        self.focus_map_surface_storage: Optional[Any] = None
        self.already_using_fmap: bool = False
        self.selected_configurations: List[Any] = []
        self.scanCoordinates: Optional[ScanCoordinates] = scan_coordinates
        # Expose stage for convenience (delegates to scanCoordinates)
        self.stage = scan_coordinates.stage if scan_coordinates else None
        self.old_images_per_page: int = 1
        self._start_position: Optional[squid.core.abc.Pos] = None
        self._acquisition_context: Optional[AcquisitionContext] = None
        self._per_acq_log_handler: Optional[Any] = None

        # Widget context for YAML saving (set by UI widgets before acquisition)
        self._widget_type: str = "wellplate"  # "wellplate" or "flexible"
        self._scan_size_mm: float = 0.0  # For wellplate: scan size per region
        self._overlap_percent: float = 10.0  # FOV overlap percentage

    def _start_per_acquisition_log(self) -> None:
        """Start per-acquisition logging if enabled.

        Creates a log file in the acquisition folder that captures all log messages
        during this acquisition. This is useful for debugging and audit trail purposes.
        """
        if not self._feature_flags.is_enabled("ENABLE_PER_ACQUISITION_LOG"):
            return
        if self._per_acq_log_handler is not None:
            return
        if not self.base_path or not self.experiment_ID:
            return

        acq_dir = os.path.join(self.base_path, self.experiment_ID)
        log_path = os.path.join(acq_dir, "acquisition.log")
        try:
            self._per_acq_log_handler = squid.core.logging.add_file_handler(
                log_path, replace_existing=True, level=squid.core.logging.py_logging.DEBUG
            )
        except Exception:
            self._log.exception("Failed to start per-acquisition logging")
            self._per_acq_log_handler = None

    def _stop_per_acquisition_log(self) -> None:
        """Stop per-acquisition logging and close the handler.

        Safe to call even if logging was never started or already stopped.
        """
        if self._per_acq_log_handler is None:
            return
        try:
            squid.core.logging.remove_handler(self._per_acq_log_handler)
        except Exception:
            self._log.exception("Failed to stop per-acquisition logging")
        finally:
            self._per_acq_log_handler = None

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

    def set_alignment_widget(self, widget: Optional[object]) -> None:
        """Set the alignment widget for offset application during acquisition.

        Args:
            widget: AlignmentWidget instance with has_offset and apply_offset methods
        """
        self._alignment_widget = widget

    def update_config(self, **updates: Any) -> None:
        """Update acquisition configuration and apply runtime updates if possible."""
        new_config = self._config.with_updates(**updates)
        new_config.validate()
        self._config = new_config

        if not self.multiPointWorker or not self.acquisition_in_progress():
            return

        runtime_keys = set(updates.keys())
        if "zstack.use_piezo" in runtime_keys:
            self.multiPointWorker.update_use_piezo(self._config.zstack.use_piezo)
            runtime_keys.remove("zstack.use_piezo")

        if runtime_keys:
            self._log.warning(
                "Ignored runtime updates during acquisition: %s",
                sorted(runtime_keys),
            )

    @property
    def NX(self) -> int:  # legacy attribute
        return self._config.grid.nx

    @property
    def NY(self) -> int:  # legacy attribute
        return self._config.grid.ny

    @property
    def NZ(self) -> int:  # legacy attribute
        return self._config.zstack.nz

    @property
    def Nt(self) -> int:  # legacy attribute
        return self._config.timing.nt

    @property
    def deltaX(self) -> float:  # legacy attribute (mm)
        return self._config.grid.dx_mm

    @property
    def deltaY(self) -> float:  # legacy attribute (mm)
        return self._config.grid.dy_mm

    @property
    def deltaZ(self) -> float:  # legacy attribute (mm)
        return self._config.zstack.delta_z_mm

    @property
    def deltat(self) -> float:  # legacy attribute (s)
        return self._config.timing.dt_s

    @property
    def autofocus_mode(self) -> AutofocusMode:
        return self._config.focus.mode

    @property
    def display_resolution_scaling(self) -> float:  # legacy attribute
        return self._config.display_resolution_scaling

    @property
    def use_piezo(self) -> bool:  # legacy attribute
        return self._config.zstack.use_piezo

    @property
    def use_manual_focus_map(self) -> bool:  # legacy attribute
        return self._config.focus.use_manual_focus_map

    @property
    def gen_focus_map(self) -> bool:  # legacy attribute
        return self._config.focus.gen_focus_map

    @property
    def z_range(self) -> Optional[Tuple[float, float]]:  # legacy attribute
        return self._config.zstack.z_range

    @property
    def z_stacking_config(self) -> str:  # legacy attribute
        return self._config.zstack.stacking_direction

    @property
    def use_fluidics(self) -> bool:  # legacy attribute
        return self._config.use_fluidics

    @property
    def skip_saving(self) -> bool:  # legacy attribute
        return self._config.skip_saving

    @property
    def xy_mode(self) -> str:  # legacy attribute
        return self._config.xy_mode

    @property
    def focus_map(self) -> Optional[Any]:  # legacy attribute
        return self._focus_map

    def set_use_piezo(self, checked: bool) -> None:
        if checked and (self._piezo_service is None or not self._piezo_service.is_available):
            raise ValueError("Cannot enable piezo - no piezo stage configured")
        self.update_config(**{"zstack.use_piezo": checked})

    def set_z_stacking_config(self, z_stacking_config_index: int) -> None:
        if z_stacking_config_index in _def.Z_STACKING_CONFIG_MAP:
            self.update_config(
                **{
                    "zstack.stacking_direction": _def.Z_STACKING_CONFIG_MAP[
                        z_stacking_config_index
                    ]
                }
            )
        self._log.info(f"z-stacking configuration set to {self._config.zstack.stacking_direction}")

    def set_z_range(self, minZ: float, maxZ: float) -> None:
        self.update_config(**{"zstack.z_range": (minZ, maxZ)})

    def set_NX(self, N: int) -> None:
        self.update_config(**{"grid.nx": N})

    def set_NY(self, N: int) -> None:
        self.update_config(**{"grid.ny": N})

    def set_NZ(self, N: int) -> None:
        self.update_config(**{"zstack.nz": N})

    def set_Nt(self, N: int) -> None:
        self.update_config(**{"timing.nt": N})

    def set_deltaX(self, delta: float) -> None:
        self.update_config(**{"grid.dx_mm": delta})

    def set_deltaY(self, delta: float) -> None:
        self.update_config(**{"grid.dy_mm": delta})

    def set_deltaZ(self, delta_um: float) -> None:
        self.update_config(**{"zstack.delta_z_um": delta_um})

    def set_deltat(self, delta: float) -> None:
        self.update_config(**{"timing.dt_s": delta})

    def set_autofocus_mode(self, mode: AutofocusMode) -> None:
        self.update_config(**{"focus.mode": AutofocusMode(mode)})

    def set_autofocus_interval(self, interval_fovs: int) -> None:
        self.update_config(**{"focus.interval_fovs": int(interval_fovs)})

    def set_focus_lock_settings(self, settings: FocusLockSettings) -> None:
        self.update_config(**{"focus.focus_lock": settings})
        if getattr(self, "_autofocus_executor", None) is not None:
            self._autofocus_executor.apply_focus_lock_settings(settings)
        if self._event_bus:
            self._event_bus.publish(
                SetFocusLockParamsCommand(
                    buffer_length=settings.buffer_length,
                    recovery_attempts=settings.recovery_attempts,
                    min_spot_snr=settings.min_spot_snr,
                    acquire_threshold_um=settings.acquire_threshold_um,
                    maintain_threshold_um=settings.maintain_threshold_um,
                )
            )
            self._event_bus.publish(
                SetFocusLockAutoSearchCommand(enabled=settings.auto_search_enabled)
            )

    def set_manual_focus_map_flag(self, flag: bool) -> None:
        self.update_config(**{"focus.use_manual_focus_map": flag})

    def set_gen_focus_map_flag(self, flag: bool) -> None:
        self.update_config(**{"focus.gen_focus_map": flag})
        if not flag:
            self.autofocusController.set_focus_map_use(False)

    def set_focus_map(self, focusMap: Optional[Any]) -> None:
        self._focus_map = focusMap  # None if dont use focusMap

    def set_base_path(self, path: str) -> None:
        self.base_path = path

    def set_use_fluidics(self, use_fluidics: bool) -> None:
        self.update_config(**{"use_fluidics": use_fluidics})

    def set_skip_saving(self, skip_saving: bool) -> None:
        self.update_config(**{"skip_saving": skip_saving})

    def set_xy_mode(self, xy_mode: Optional[str]) -> None:
        if xy_mode is None:
            return
        self.update_config(**{"xy_mode": xy_mode})

    def set_widget_type(self, widget_type: str) -> None:
        """Set the widget type for YAML saving context.

        Args:
            widget_type: Either "wellplate" or "flexible"
        """
        self._widget_type = widget_type

    def set_scan_size(self, scan_size_mm: float) -> None:
        """Set scan size for YAML saving context (wellplate mode).

        Args:
            scan_size_mm: Scan area size in mm
        """
        self._scan_size_mm = scan_size_mm

    def set_overlap_percent(self, overlap_percent: float) -> None:
        """Set FOV overlap percentage for YAML saving context.

        Args:
            overlap_percent: Overlap percentage (e.g., 10.0 for 10%)
        """
        self._overlap_percent = overlap_percent

    def get_plate_view(self) -> Optional[np.ndarray]:
        """Get the current plate view array from the acquisition.

        Returns:
            Copy of the plate view array, or None if not available.
        """
        if self.multiPointWorker is not None:
            return self.multiPointWorker.get_plate_view()
        return None

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
        if hasattr(self.channelConfigurationManager, "save_acquisition_output"):
            self.channelConfigurationManager.save_acquisition_output(
                pathlib.Path(os.path.join(self.base_path, self.experiment_ID)),
                self.objectiveStore.current_objective,
                self.selected_configurations,
            )  # save the configuration for the experiment
        # Prepare acquisition parameters
        acquisition_parameters = {
            "dx(mm)": self._config.grid.dx_mm,
            "Nx": self._config.grid.nx,
            "dy(mm)": self._config.grid.dy_mm,
            "Ny": self._config.grid.ny,
            "dz(um)": self._config.zstack.delta_z_um,
            "Nz": self._config.zstack.nz,
            "dt(s)": self._config.timing.dt_s,
            "Nt": self._config.timing.nt,
            "autofocus_mode": self._config.focus.mode.value,
            "autofocus_interval_fovs": self._config.focus.interval_fovs,
            "with manual focus map": self._config.focus.use_manual_focus_map,
        }
        try:  # write objective data if it is available
            current_objective = self.objectiveStore.current_objective
            objective_info = self.objectiveStore.get_current_objective_info()
            acquisition_parameters["objective"] = dict(objective_info)
            acquisition_parameters["objective"]["name"] = current_objective
        except Exception:
            self._log.exception("Failed to attach objective metadata to acquisition parameters")
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
        self.update_config(selected_channels=tuple(selected_configurations_name))

    def set_resolved_configurations(self, configurations) -> None:
        """Set pre-resolved AcquisitionChannel objects directly.

        Use this instead of set_selected_configurations() when channels
        have already been resolved (e.g., by resolve_protocol_channels).
        This avoids mutating global channel config state.

        Args:
            configurations: List of AcquisitionChannel objects
        """
        self.selected_configurations = list(configurations)
        self.update_config(selected_channels=tuple(c.name for c in configurations))

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
                self._config.timing.nt
                * self._config.zstack.nz
                * all_regions_coord_count
                * len(self.selected_configurations)
            )
            # When capturing merged images, we capture 1 per fov (where all the configurations are merged)
            merged_images = (
                self._config.timing.nt * self._config.zstack.nz * all_regions_coord_count
                if self._feature_flags.is_enabled("MERGE_CHANNELS")
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
            width, height = self._camera_service.get_crop_size()
            if width is None or height is None:
                width, height = self._camera_service.get_resolution()
            if width is None or height is None:
                raise RuntimeError("Camera resolution unavailable for disk usage estimate")
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

    def get_estimated_mosaic_ram_bytes(self) -> int:
        """
        Estimate the RAM (in bytes) required to hold the mosaic view in memory.

        The estimate is based on:

        * The mosaic scan bounds in stage space (mm) derived from ``self.scanCoordinates``.
        * The effective camera pixel size at the sample, computed from the objective
          magnification factor and the binned camera pixel size in microns.
        * A downsampling factor chosen so that the effective mosaic pixel size is at
          least ``_def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM`` (in um). The scan
          extents are divided by this downsampled pixel size to obtain the mosaic width
          and height in pixels.

        Assumptions:

        * Each mosaic pixel is stored as a 16-bit unsigned integer (2 bytes per pixel).
        * The returned value includes memory for all mosaic channel layers, by
          multiplying by ``len(self.selected_configurations)``.
        * The estimate only applies when ``USE_NAPARI_FOR_MOSAIC_DISPLAY``
          is enabled and when valid scan coordinates with regions are available;
          otherwise, it returns 0.
        """
        if not self._feature_flags.is_enabled("USE_NAPARI_FOR_MOSAIC_DISPLAY"):
            return 0

        if not self.scanCoordinates or not self.scanCoordinates.has_regions():
            return 0

        bounds = self.scanCoordinates.get_scan_bounds()
        if not bounds:
            return 0

        # Calculate scan extents in mm
        width_mm = bounds["x"][1] - bounds["x"][0]
        height_mm = bounds["y"][1] - bounds["y"][0]

        # Get effective pixel size (with downsampling)
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self._camera_service.get_pixel_size_binned_um()
        downsample_factor = max(1, int(_def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um))
        viewer_pixel_size_mm = (pixel_size_um * downsample_factor) / 1000

        # Calculate mosaic dimensions in pixels
        mosaic_width = int(math.ceil(width_mm / viewer_pixel_size_mm))
        mosaic_height = int(math.ceil(height_mm / viewer_pixel_size_mm))

        # Assume 2 bytes per pixel component (uint16), adjust for color and multiply by number of channels
        bytes_per_pixel = 2

        # If the camera provides color images (e.g. RGB), account for multiple components per pixel.
        # Mirror the logic used in get_estimated_acquisition_disk_storage to keep estimates consistent.
        try:
            # Common patterns: a boolean property or a zero-arg method named "is_color"
            is_color_attr = getattr(self._camera_service, "is_color", None)
            if callable(is_color_attr):
                if is_color_attr():
                    bytes_per_pixel *= 3
            elif isinstance(is_color_attr, bool) and is_color_attr:
                bytes_per_pixel *= 3
        except Exception:
            # If color information isn't available, fall back to the monochrome assumption.
            pass

        num_channels = len(self.selected_configurations)
        if num_channels == 0:
            # No channels selected; this is likely an invalid acquisition state.
            # Log a warning (similar to disk storage estimation) and return 0 as a sentinel.
            self._log.warning(
                "Estimated mosaic RAM is 0 because no channel configurations are selected."
            )
            return 0

        return mosaic_width * mosaic_height * bytes_per_pixel * num_channels

    def run_acquisition(
        self,
        acquire_current_fov: bool = False,
    ) -> bool:
        # Check if in IDLE state
        if not self._is_in_state(AcquisitionControllerState.IDLE):
            self._log.warning(f"Cannot start acquisition - state is {self.state.name}")
            return False

        try:
            # Transition to PREPARING
            self._transition_to(AcquisitionControllerState.PREPARING)

            if self._mode_gate:
                self._mode_gate.set_mode(GlobalMode.ACQUIRING, reason="acquisition start")

            # Ensure we have an experiment ID before publishing any acquisition events
            self._require_experiment_id()

            # Start per-acquisition logging if enabled
            self._start_per_acquisition_log()

            # Build scan coordinates for validation.
            # Normal acquisitions use global self.scanCoordinates (already populated
            # by ClearScanCoordinatesCommand / AddFlexibleRegionCommand before start).
            # acquire_current_fov creates a one-off single-FOV set (Snap Images).
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

            if not self.validate_acquisition_settings(acquisition_scan_coordinates):
                self._publish_acquisition_state(in_progress=False, allow_missing_experiment_id=True)
                if self._mode_gate:
                    self._mode_gate.set_mode(GlobalMode.IDLE, reason="acquisition start failed")
                self._transition_to(AcquisitionControllerState.FAILED)
                self._transition_to(AcquisitionControllerState.IDLE)
                return False

            # Publish acquisition started state
            self._publish_acquisition_state(in_progress=True)
            self._acquisition_context = acquisition_context(
                self.liveController,
                self._camera_service,
                self._stage_service,
            )
            self.liveController_was_live_before_multipoint = self._acquisition_context.was_live
            self.camera_callback_was_enabled_before_multipoint = (
                self._acquisition_context.callbacks_enabled
            )
            self._start_position = self._acquisition_context.start_position

            if self._config.zstack.z_range is None:
                z_range = (
                    self._start_position.z_mm,
                    self._start_position.z_mm
                    + self._config.zstack.delta_z_mm * (self._config.zstack.nz - 1),
                )
                self._config = self._config.with_updates(**{"zstack.z_range": z_range})

            scan_coordinates_target = acquisition_scan_coordinates
            scan_position_information: ScanPositionInformation = (
                ScanPositionInformation.from_scan_coordinates(acquisition_scan_coordinates)
            )

            # Save coordinates to CSV in top level folder
            try:
                coordinates_df: pd.DataFrame = pd.DataFrame(
                    columns=["region", "fov", "fov_id", "x (mm)", "y (mm)", "z (mm)"]
                )
                for (
                    region_id,
                    coords_list,
                ) in scan_position_information.scan_region_fov_coords_mm.items():
                    for index, coord in enumerate(coords_list):
                        row = {
                            "region": region_id,
                            "fov": index,
                            "fov_id": f"{region_id}_{index:04d}",
                            "x (mm)": coord[0],
                            "y (mm)": coord[1],
                        }
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

            # run the acquisition
            self.timestamp_acquisition_started: float = time.time()

            if self._focus_map:
                self._log.info("Using focus surface for Z interpolation")
                for region_id in scan_position_information.scan_region_names:
                    region_fov_coords = scan_position_information.scan_region_fov_coords_mm[
                        region_id
                    ]
                    # Convert each tuple to list for modification
                    for i, coords in enumerate(region_fov_coords):
                        x, y = coords[:2]  # This handles both (x,y) and (x,y,z) formats
                        z = self._focus_map.interpolate(x, y, region_id)
                        # Modify the list directly
                        region_fov_coords[i] = (x, y, z)
                        scan_coordinates_target.update_fov_z_level(region_id, i, z)

            elif (
                self._config.focus.gen_focus_map
                and self._config.focus.mode != AutofocusMode.LASER_REFLECTION
                and self._config.focus.mode != AutofocusMode.FOCUS_LOCK
            ):
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
                    return False
                try:
                    # Store existing AF map if any
                    self.focus_map_storage = []
                    self.already_using_fmap = self.autofocusController.use_focus_map
                    self.focus_map_surface_storage = getattr(
                        self.autofocusController, "focus_map_surface", None
                    )
                    for x, y, z in self.autofocusController.focus_map_coords:
                        self.focus_map_storage.append((x, y, z))

                    center = self._autofocus_executor.generate_focus_map_for_acquisition(
                        bounds,
                        dx_mm=self._config.focus.focus_map_dx_mm,
                        dy_mm=self._config.focus.focus_map_dy_mm,
                    )
                    if center is None:
                        raise RuntimeError("Autofocus controller unavailable for focus map")

                    # Return to center position
                    self._stage_service.move_x_to(center[0])
                    self._stage_service.move_y_to(center[1])

                except ValueError as exc:
                    raise RuntimeError("Invalid coordinates for autofocus plane") from exc

            acquisition_params: AcquisitionParameters = self.build_params(
                scan_position_information=scan_position_information
            )

            # Save acquisition parameters to YAML for reproducibility
            try:
                current_objective = self.objectiveStore.current_objective
                objective_dict = self.objectiveStore.objectives_dict.get(current_objective, {})
                pixel_size_um = (
                    self.objectiveStore.get_pixel_size_factor()
                    * self._camera_service.get_pixel_size_binned_um()
                )
                objective_info = {
                    "name": current_objective,
                    "magnification": objective_dict.get("magnification"),
                    "NA": objective_dict.get("NA"),
                    "pixel_size_um": pixel_size_um,
                    "camera_binning": list(self._camera_service.get_binning()),
                    "sensor_pixel_size_um": self._camera_service.get_pixel_size_binned_um(),
                }
                wellplate_format = getattr(self.scanCoordinates, "format", None)
                region_shapes = getattr(self.scanCoordinates, "region_shapes", None)
                experiment_path = os.path.join(self.base_path, self.experiment_ID)

                save_acquisition_yaml(
                    params=acquisition_params,
                    experiment_path=experiment_path,
                    region_shapes=region_shapes,
                    widget_type=self._widget_type,
                    objective_info=objective_info,
                    wellplate_format=wellplate_format,
                    scan_size_mm=self._scan_size_mm,
                    overlap_percent=self._overlap_percent,
                )
            except Exception as exc:
                self._log.warning(f"Failed to save acquisition YAML (non-fatal): {exc}")

            dependencies = AcquisitionDependencies.create(
                camera=self._camera_service,
                stage=self._stage_service,
                peripheral=self._peripheral_service,
                event_bus=self._event_bus,
                illumination=self._illumination_service,
                filter_wheel=self._filter_wheel_service,
                piezo=self._piezo_service,
                fluidics=self._fluidics_service,
                nl5=self._nl5_service,
                stream_handler=self._stream_handler,
                autofocus=self.autofocusController,
                laser_autofocus=self.laserAutoFocusController,
                focus_lock=self._focus_lock_controller,
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
                focus_lock_controller=self._focus_lock_controller,
                dependencies=dependencies,
                alignment_widget=self._alignment_widget,
            )
            self.multiPointWorker.set_current_round_index(self._current_round_index)
            # Set start FOV index for resume support
            if self._start_fov_index > 0:
                self.multiPointWorker.set_start_fov_index(self._start_fov_index)
                self._start_fov_index = 0  # Reset after passing to worker
            # Allow tests/simulation to override long frame wait timeouts.
            if hasattr(self, "frame_wait_timeout_override_s"):
                self.multiPointWorker.frame_wait_timeout_override_s = getattr(
                    self, "frame_wait_timeout_override_s"
                )

            self.thread: Thread = Thread(
                target=self.multiPointWorker.run, name="Acquisition thread", daemon=True
            )
            # Transition to RUNNING BEFORE starting worker to avoid race condition
            # where worker finishes immediately (e.g., zero FOVs) and publishes
            # AcquisitionWorkerFinished before we've transitioned to RUNNING state.
            # The _on_worker_finished handler filters events if not in RUNNING state.
            self._transition_to(AcquisitionControllerState.RUNNING)
            self._active_worker_experiment_id = acquisition_params.experiment_ID
            self.thread.start()
            # Publish NDViewer start event for push-mode display
            self._publish_ndviewer_start(acquisition_params)
            return True
        except Exception:
            self._log.exception("Failed to start acquisition")
            # Stop per-acquisition logging if it was started
            self._stop_per_acquisition_log()
            if self._acquisition_context is not None:
                self._acquisition_context.restore(resume_live=False)
                self._acquisition_context = None
                self._start_position = None
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
            self._active_worker_experiment_id = None
            return False

    def build_params(
        self, scan_position_information: ScanPositionInformation
    ) -> AcquisitionParameters:
        # Determine plate dimensions from wellplate format if available
        plate_num_rows = 8  # Default for 96-well
        plate_num_cols = 12
        wellplate_format = getattr(self.scanCoordinates, "format", None)
        self._log.info(f"build_params: wellplate format = {wellplate_format}")
        if wellplate_format:
            from _def import get_wellplate_settings
            format_settings = get_wellplate_settings(wellplate_format)
            self._log.info(f"build_params: format_settings = {format_settings}")
            if format_settings:
                plate_num_rows = format_settings.get("rows", 8)
                plate_num_cols = format_settings.get("cols", 12)
            else:
                self._log.warning(
                    f"Unknown wellplate format '{wellplate_format}', "
                    f"using default 96-well dimensions"
                )
        self._log.info(f"build_params: plate dimensions = {plate_num_rows}x{plate_num_cols}")

        generate_downsampled_views = (
            not self.run_acquisition_current_fov
            and (
                self._feature_flags.is_enabled("SAVE_DOWNSAMPLED_WELL_IMAGES")
                or self._feature_flags.is_enabled("DISPLAY_PLATE_VIEW")
            )
        )

        return AcquisitionParameters(
            experiment_ID=self.experiment_ID,
            base_path=self.base_path,
            selected_configurations=self.selected_configurations,
            acquisition_start_time=self.timestamp_acquisition_started,
            scan_position_information=scan_position_information,
            NX=self._config.grid.nx,
            deltaX=self._config.grid.dx_mm,
            NY=self._config.grid.ny,
            deltaY=self._config.grid.dy_mm,
            NZ=self._config.zstack.nz,
            deltaZ=self._config.zstack.delta_z_um,
            Nt=self._config.timing.nt,
            deltat=self._config.timing.dt_s,
            autofocus_mode=self._config.focus.mode,
            autofocus_interval_fovs=self._config.focus.interval_fovs,
            focus_lock_settings=self._config.focus.focus_lock,
            use_piezo=self._config.zstack.use_piezo,
            display_resolution_scaling=self._config.display_resolution_scaling,
            z_stacking_config=self._config.zstack.stacking_direction,
            z_range=self._config.zstack.z_range,
            use_fluidics=self._config.use_fluidics,
            skip_saving=self._config.skip_saving,
            acquisition_order=self._config.acquisition_order,
            # Downsampled view generation parameters
            generate_downsampled_views=generate_downsampled_views,
            save_downsampled_well_images=(
                generate_downsampled_views
                and self._feature_flags.is_enabled("SAVE_DOWNSAMPLED_WELL_IMAGES")
            ),
            downsampled_well_resolutions_um=_def.DOWNSAMPLED_WELL_RESOLUTIONS_UM,
            downsampled_plate_resolution_um=_def.DOWNSAMPLED_PLATE_RESOLUTION_UM,
            downsampled_z_projection=_def.DOWNSAMPLED_Z_PROJECTION,
            downsampled_interpolation_method=_def.DOWNSAMPLED_INTERPOLATION_METHOD,
            plate_num_rows=plate_num_rows,
            plate_num_cols=plate_num_cols,
            xy_mode=self._config.xy_mode,
        )

    def _on_acquisition_completed(
        self,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
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
            if self._config.focus.gen_focus_map:
                self.autofocusController.clear_focus_map()
                for x, y, z in self.focus_map_storage:
                    self.autofocusController.focus_map_coords.append((x, y, z))
                if hasattr(self.autofocusController, "set_focus_map_surface"):
                    self.autofocusController.set_focus_map_surface(self.focus_map_surface_storage)
                self.autofocusController.set_focus_map_use(self.already_using_fmap)
            if self.configuration_before_running_multipoint is not None:
                self.liveController.set_microscope_mode(
                    self.configuration_before_running_multipoint
                )

            # Restore callbacks and stage position to pre-acquisition state
            if self._acquisition_context is not None:
                self._acquisition_context.restore(resume_live=False)
                self._acquisition_context = None
                self._start_position = None

            self._log.info(
                f"total time for acquisition + processing + reset: {time.time() - self.recording_start_time}"
            )
            utils.create_done_file(os.path.join(self.base_path, self.experiment_ID))

            if self.run_acquisition_current_fov:
                self.run_acquisition_current_fov = False

            # re-enable live AFTER stage has returned to start position
            if self._mode_gate and self._mode_gate.get_mode() in (GlobalMode.ACQUIRING, GlobalMode.ABORTING):
                self._mode_gate.set_mode(GlobalMode.IDLE, reason="acquisition complete")
            if (
                self.liveController_was_live_before_multipoint
                and self._feature_flags.is_enabled("RESUME_LIVE_AFTER_ACQUISITION")
            ):
                self.liveController.start_live()
        except Exception:
            # Never let cleanup errors block UI re-enabling
            self._log.exception("Error during acquisition cleanup")
        finally:
            # Clear active worker token so stale finish events from old runs are ignored.
            self._active_worker_experiment_id = None
            # Stop per-acquisition logging
            self._stop_per_acquisition_log()

            # Publish acquisition finished state even if cleanup fails
            self._publish_acquisition_state(in_progress=False)
            self._publish_acquisition_finished(success=success, error=error)

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
        # Also stop fluidics operations
        if self._fluidics_service is not None:
            self._fluidics_service.abort()
        if self._mode_gate:
            self._mode_gate.set_mode(GlobalMode.ABORTING, reason="acquisition abort requested")
        # Transition to ABORTING state
        self._transition_to(AcquisitionControllerState.ABORTING)
        # Publish aborting state
        self._publish_acquisition_state(in_progress=True, is_aborting=True)

    def request_pause(self) -> bool:
        """Request a pause at the next safe boundary."""
        if not self._is_in_state(AcquisitionControllerState.RUNNING):
            self._log.warning(f"Cannot pause - state is {self.state.name}")
            return False
        if self.multiPointWorker is None:
            return False
        self.multiPointWorker.request_pause()
        if self._event_bus:
            self._event_bus.publish(AcquisitionPaused())
        return True

    def resume_acquisition(self) -> bool:
        """Resume a paused acquisition."""
        if self.multiPointWorker is None:
            return False
        self.multiPointWorker.resume()
        if self._event_bus:
            self._event_bus.publish(AcquisitionResumed())
        return True

    def validate_acquisition_settings(self, scan_coordinates: Optional[ScanCoordinates] = None) -> bool:
        """Validate settings before starting acquisition.

        Args:
            scan_coordinates: ScanCoordinates to validate against. If None, uses self.scanCoordinates.
        """
        try:
            self._config.validate()
        except ValueError as exc:
            self._log.error("Invalid acquisition configuration: %s", exc)
            return False

        # Check for zero FOVs - must have at least one FOV to acquire
        coords = scan_coordinates if scan_coordinates is not None else self.scanCoordinates
        if coords is None or not coords.region_fov_coordinates:
            self._log.error(
                "No FOVs defined - please add scan positions before starting acquisition"
            )
            return False
        total_fovs = sum(
            len(c) for c in coords.region_fov_coordinates.values()
        )
        if total_fovs == 0:
            self._log.error(
                "No FOVs defined - please add scan positions before starting acquisition"
            )
            return False

        if self._config.focus.mode in (AutofocusMode.LASER_REFLECTION, AutofocusMode.FOCUS_LOCK):
            if self.laserAutoFocusController is None:
                self._log.error(
                    "Laser Autofocus Not Ready - Laser AF controller not configured."
                )
                return False
            laser_props = getattr(self.laserAutoFocusController, "laser_af_properties", None)
            if laser_props is None or not getattr(laser_props, "has_reference", False):
                self._log.error(
                    "Laser Autofocus Not Ready - Please set the laser autofocus reference position before starting acquisition with laser AF enabled."
                )
                return False
        if self._config.focus.mode == AutofocusMode.FOCUS_LOCK:
            if self._focus_lock_controller is None:
                self._log.error(
                    "Focus Lock Not Ready - focus lock controller not configured."
                )
                return False
            if self._piezo_service is None or not self._piezo_service.is_available:
                self._log.error(
                    "Focus Lock Not Ready - piezo stage is required for focus lock."
                )
                return False
        return True

    # =========================================================================
    # EventBus Command Handlers
    # =========================================================================

    @handles(SetAcquisitionParametersCommand)
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
        if cmd.autofocus_mode is not None:
            self.set_autofocus_mode(cmd.autofocus_mode)
        if cmd.autofocus_interval_fovs is not None:
            self.set_autofocus_interval(cmd.autofocus_interval_fovs)
        if cmd.focus_lock_settings is not None:
            self.set_focus_lock_settings(cmd.focus_lock_settings)
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
        if cmd.skip_saving is not None:
            self.set_skip_saving(cmd.skip_saving)
        if cmd.z_stacking_config is not None:
            self.set_z_stacking_config(cmd.z_stacking_config)
        # Widget context for YAML saving
        if cmd.widget_type is not None:
            self.set_widget_type(cmd.widget_type)
        if cmd.scan_size_mm is not None:
            self.set_scan_size(cmd.scan_size_mm)
        if cmd.overlap_percent is not None:
            self.set_overlap_percent(cmd.overlap_percent)

    @handles(SetAcquisitionPathCommand)
    def _on_set_acquisition_path(self, cmd: SetAcquisitionPathCommand) -> None:
        """Handle SetAcquisitionPathCommand from EventBus."""
        self.set_base_path(cmd.base_path)

    @handles(SetAcquisitionChannelsCommand)
    def _on_set_acquisition_channels(self, cmd: SetAcquisitionChannelsCommand) -> None:
        """Handle SetAcquisitionChannelsCommand from EventBus."""
        self.set_selected_configurations(cmd.channel_names)

    @handles(SetFluidicsRoundsCommand)
    def _on_set_fluidics_rounds(self, cmd: SetFluidicsRoundsCommand) -> None:
        """Handle SetFluidicsRoundsCommand from EventBus."""
        if self._fluidics_service is None:
            self._log.warning("Fluidics service not available; ignoring rounds update")
            return
        try:
            self._fluidics_service.set_rounds(cmd.rounds)
        except Exception as exc:
            self._log.exception("Failed to set fluidics rounds", exc_info=exc)

    @handles(StartNewExperimentCommand)
    def _on_start_new_experiment(self, cmd: StartNewExperimentCommand) -> None:
        """Handle StartNewExperimentCommand from EventBus."""
        if self.acquisition_in_progress():
            self._log.warning(
                "Ignoring StartNewExperimentCommand while acquisition is in progress "
                "(state=%s, requested_id=%s)",
                self.state.name,
                cmd.experiment_id,
            )
            return
        self.start_new_experiment(cmd.experiment_id)

    @handles(StartAcquisitionCommand)
    def _on_start_acquisition(self, cmd: StartAcquisitionCommand) -> None:
        """Handle StartAcquisitionCommand from EventBus."""
        # Set xy_mode from command before building acquisition params
        self.set_xy_mode(cmd.xy_mode)
        # Ensure an experiment ID exists before running; auto-create if none exists.
        self._ensure_experiment_ready(cmd.experiment_id)
        self.run_acquisition(acquire_current_fov=cmd.acquire_current_fov)

    @handles(StopAcquisitionCommand)
    def _on_stop_acquisition(self, cmd: StopAcquisitionCommand) -> None:
        """Handle StopAcquisitionCommand from EventBus."""
        self.request_abort_aquisition()

    @handles(AcquisitionWorkerFinished)
    def _on_worker_finished(self, event: AcquisitionWorkerFinished) -> None:
        """Handle AcquisitionWorkerFinished event from worker thread.

        This is the primary mechanism for the worker to signal completion.
        Uses experiment_id to filter out stale events from previous acquisitions.
        """
        expected_id = self._active_worker_experiment_id or self.experiment_ID
        if expected_id is None:
            return
        if event.experiment_id != expected_id:
            return

        if not self._is_in_state(AcquisitionControllerState.RUNNING, AcquisitionControllerState.ABORTING):
            self._log.warning(
                "Processing AcquisitionWorkerFinished for experiment '%s' while controller state is %s",
                event.experiment_id,
                self.state.name,
            )

        self._log.info(
            f"Worker finished: success={event.success}, "
            f"fov_count={event.final_fov_count}, error={event.error}"
        )

        # Publish NDViewer end event before cleanup
        self._publish_ndviewer_end()

        # Call cleanup logic with success flag from worker
        self._on_acquisition_completed(success=event.success, error=event.error)

    @handles(AcquisitionWorkerProgress)
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

        # Progress events are consumed by UI handlers; no controller-side logging needed.

    def _publish_acquisition_state(
        self,
        in_progress: bool,
        is_aborting: bool = False,
        allow_missing_experiment_id: bool = False,
    ) -> None:
        """Publish acquisition state changed event."""
        experiment_id = self.experiment_ID
        if allow_missing_experiment_id and not experiment_id:
            self._log.warning(
                "Skipping AcquisitionStateChanged publish because experiment_id is missing"
            )
            return
        experiment_id = self._require_experiment_id()
        if self._event_bus:
            self._event_bus.publish(AcquisitionStateChanged(
                in_progress=in_progress,
                experiment_id=experiment_id,
                is_aborting=is_aborting
            ))

    def _publish_acquisition_finished(
        self,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Publish AcquisitionFinished event for external subscribers."""
        if not self._event_bus:
            return
        try:
            experiment_id = self._require_experiment_id()
        except RuntimeError:
            return
        exc = RuntimeError(error) if error else None
        self._event_bus.publish(
            AcquisitionFinished(
                success=success,
                experiment_id=experiment_id,
                error=exc,
            )
        )

    def _publish_ndviewer_start(self, acquisition_params: "AcquisitionParameters") -> None:
        """Publish NDViewerStartAcquisition event for push-mode display.

        Builds FOV labels from scan position information and publishes
        the event to configure NDViewer for real-time image display.
        For ZARR_V3 format, publishes NDViewerStartZarrAcquisition instead.
        """
        self._log.info("_publish_ndviewer_start called")
        if not self._event_bus:
            self._log.warning("_publish_ndviewer_start: no event_bus, skipping")
            return

        # NDViewer push mode is only meaningful when frames are registered incrementally.
        # OME-TIFF and multi-page TIFF paths do not emit NDViewerImageRegistered events,
        # so starting push mode there can leave stale UI state between runs.
        if _def.FILE_SAVING_OPTION not in (
            _def.FileSavingOption.INDIVIDUAL_IMAGES,
            _def.FileSavingOption.ZARR_V3,
        ):
            self._log.info(
                "Skipping NDViewer push start for file mode %s",
                _def.FILE_SAVING_OPTION.value,
            )
            self._ndviewer_mode = "inactive"
            return
        try:
            experiment_id = self._require_experiment_id()
        except RuntimeError as e:
            self._log.warning(f"_publish_ndviewer_start: no experiment_id ({e}), skipping")
            return

        # Build channel names from selected configurations
        channels = [config.name for config in self.selected_configurations]

        # Get image dimensions from camera
        width, height = self._camera_service.get_crop_size()
        if width is None or height is None:
            width, height = self._camera_service.get_resolution()
        if width is None or height is None:
            self._log.warning("_publish_ndviewer_start: Cannot get camera dimensions, skipping")
            return

        # Build FOV labels: "region:fov_index" format, and region offset list
        fov_labels = []
        self._ndviewer_region_idx_offset = []
        scan_info = acquisition_params.scan_position_information
        for region_id in scan_info.scan_region_names:
            self._ndviewer_region_idx_offset.append(len(fov_labels))
            fov_coords = scan_info.scan_region_fov_coords_mm.get(region_id, [])
            for fov_idx in range(len(fov_coords)):
                fov_labels.append(f"{region_id}:{fov_idx}")

        # For ZARR_V3 format, publish zarr-specific start event
        if _def.FILE_SAVING_OPTION == _def.FileSavingOption.ZARR_V3:
            self._publish_ndviewer_start_zarr(
                acquisition_params, channels, fov_labels, height, width, experiment_id
            )
            return

        self._ndviewer_mode = "tiff"
        self._event_bus.publish(
            NDViewerStartAcquisition(
                channels=channels,
                num_z=acquisition_params.NZ,
                height=height,
                width=width,
                fov_labels=fov_labels,
                experiment_id=experiment_id,
            )
        )
        self._log.info(
            f"Published NDViewerStartAcquisition: {len(channels)} channels, "
            f"{acquisition_params.NZ} z, {len(fov_labels)} FOVs"
        )

    def _publish_ndviewer_start_zarr(
        self,
        acquisition_params: "AcquisitionParameters",
        channels: List[str],
        fov_labels: List[str],
        height: int,
        width: int,
        experiment_id: str,
    ) -> None:
        """Publish NDViewerStartZarrAcquisition or NDViewerStartZarrAcquisition6D."""
        from squid.backend.io.writers.zarr_writer import (
            build_hcs_zarr_fov_path,
            build_per_fov_zarr_path,
            build_6d_zarr_path,
        )

        scan_info = acquisition_params.scan_position_information
        base_path = os.path.join(self.base_path, self.experiment_ID) if self.base_path and self.experiment_ID else ""

        # Detect HCS mode from region names
        import re
        well_pattern = re.compile(r"^[A-Z]+\d+$")
        is_hcs = len(scan_info.scan_region_names) > 0 and all(
            well_pattern.match(name) for name in scan_info.scan_region_names
        )

        use_6d = _def.ZARR_USE_6D_FOV_DIMENSION and not is_hcs

        if use_6d:
            # 6D mode: one zarr store per region with FOV dimension
            self._ndviewer_mode = "zarr_6d"
            region_paths, region_labels, fovs_per_region = self._build_6d_region_info(
                scan_info, base_path, build_6d_zarr_path
            )
            self._event_bus.publish(
                NDViewerStartZarrAcquisition6D(
                    region_paths=region_paths,
                    channels=channels,
                    num_z=acquisition_params.NZ,
                    fovs_per_region=fovs_per_region,
                    height=height,
                    width=width,
                    region_labels=region_labels,
                    experiment_id=experiment_id,
                )
            )
            self._log.info(
                f"Published NDViewerStartZarrAcquisition6D: {len(channels)} channels, "
                f"{acquisition_params.NZ} z, {len(region_paths)} regions"
            )
        else:
            # 5D mode: one zarr store per FOV
            self._ndviewer_mode = "zarr_5d"
            fov_paths = []
            for region_id in scan_info.scan_region_names:
                num_fovs = len(scan_info.scan_region_fov_coords_mm.get(region_id, []))
                for fov_idx in range(num_fovs):
                    if is_hcs:
                        path = build_hcs_zarr_fov_path(base_path, region_id, fov_idx)
                    else:
                        path = build_per_fov_zarr_path(base_path, region_id, fov_idx)
                    fov_paths.append(path)

            self._event_bus.publish(
                NDViewerStartZarrAcquisition(
                    fov_paths=fov_paths,
                    channels=channels,
                    num_z=acquisition_params.NZ,
                    fov_labels=fov_labels,
                    height=height,
                    width=width,
                    experiment_id=experiment_id,
                )
            )
            self._log.info(
                f"Published NDViewerStartZarrAcquisition: {len(channels)} channels, "
                f"{acquisition_params.NZ} z, {len(fov_paths)} FOV paths"
            )

    def _build_6d_region_info(
        self,
        scan_info: "ScanPositionInformation",
        base_path: str,
        build_6d_zarr_path,
    ) -> tuple:
        """Build region paths, labels, and FOV counts for 6D zarr mode.

        Returns:
            (region_paths, region_labels, fovs_per_region)
        """
        region_paths = []
        region_labels = []
        fovs_per_region = []
        for region_id in scan_info.scan_region_names:
            num_fovs = len(scan_info.scan_region_fov_coords_mm.get(region_id, []))
            region_paths.append(build_6d_zarr_path(base_path, region_id))
            region_labels.append(str(region_id))
            fovs_per_region.append(num_fovs)
        return region_paths, region_labels, fovs_per_region

    def _publish_ndviewer_end(self) -> None:
        """Publish NDViewerAcquisitionEnded event."""
        if not self._event_bus:
            self._ndviewer_mode = "inactive"
            return
        try:
            experiment_id = self._require_experiment_id()
        except RuntimeError:
            self._ndviewer_mode = "inactive"
            return

        # Build dataset path for file-based loading (used when push-mode isn't available).
        # When skip_saving is enabled (e.g., Quick Scan), no files will exist, so avoid
        # triggering NDViewer fallback retries against an empty folder.
        dataset_path = None
        if not self._config.skip_saving and self.base_path and self.experiment_ID:
            dataset_path = os.path.join(self.base_path, self.experiment_ID)

        self._event_bus.publish(
            NDViewerAcquisitionEnded(
                experiment_id=experiment_id,
                dataset_path=dataset_path,
            )
        )
        self._log.info(f"Published NDViewerAcquisitionEnded: experiment={experiment_id}, path={dataset_path}")
        self._ndviewer_mode = "inactive"

    def set_current_round_index(self, round_index: int) -> None:
        """Set the current round index for downstream FOV events."""
        self._current_round_index = round_index
        if self.multiPointWorker is not None:
            self.multiPointWorker.set_current_round_index(round_index)

    def set_start_fov_index(self, fov_index: int) -> None:
        """Set the FOV index to start from (for resume support).

        This must be called before run_acquisition(). The worker will
        skip to this FOV index when starting the acquisition.

        Args:
            fov_index: The FOV index to start from (0-based)
        """
        self._start_fov_index = fov_index
        if self.multiPointWorker is not None:
            self.multiPointWorker.set_start_fov_index(fov_index)

    # =========================================================================
    # FOV Task Command Handlers
    # =========================================================================

    @handles(JumpToFovCommand)
    def _on_jump_to_fov(self, cmd: JumpToFovCommand) -> None:
        """Handle JumpToFovCommand - non-destructive cursor move."""
        if self.multiPointWorker is not None:
            self.multiPointWorker.queue_fov_command(cmd)
            self._log.info(f"Queued JumpToFovCommand for fov_id={cmd.fov_id}")

    @handles(SkipFovCommand)
    def _on_skip_fov(self, cmd: SkipFovCommand) -> None:
        """Handle SkipFovCommand - mark FOV as skipped."""
        if self.multiPointWorker is not None:
            self.multiPointWorker.queue_fov_command(cmd)
            self._log.info(f"Queued SkipFovCommand for fov_id={cmd.fov_id}")

    @handles(RequeueFovCommand)
    def _on_requeue_fov(self, cmd: RequeueFovCommand) -> None:
        """Handle RequeueFovCommand - requeue FOV with incremented attempt."""
        if self.multiPointWorker is not None:
            self.multiPointWorker.queue_fov_command(cmd)
            self._log.info(
                f"Queued RequeueFovCommand for fov_id={cmd.fov_id}, "
                f"before_current={cmd.before_current}"
            )

    @handles(DeferFovCommand)
    def _on_defer_fov(self, cmd: DeferFovCommand) -> None:
        """Handle DeferFovCommand - mark FOV as deferred."""
        if self.multiPointWorker is not None:
            self.multiPointWorker.queue_fov_command(cmd)
            self._log.info(f"Queued DeferFovCommand for fov_id={cmd.fov_id}")

    @handles(ReorderFovsCommand)
    def _on_reorder_fovs(self, cmd: ReorderFovsCommand) -> None:
        """Handle ReorderFovsCommand - reorder pending FOVs."""
        if self.multiPointWorker is not None:
            self.multiPointWorker.queue_fov_command(cmd)
            self._log.info("Queued ReorderFovsCommand")

    def get_fov_task_list(self) -> Optional[Any]:
        """Get the current FovTaskList from the worker for inspection.

        Returns:
            The FovTaskList if acquisition is in progress, None otherwise.
        """
        if self.multiPointWorker is not None:
            return self.multiPointWorker.get_fov_task_list()
        return None
