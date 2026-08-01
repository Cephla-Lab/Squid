import collections
import contextlib
import dataclasses
import json
import math
import os
import pathlib
import shutil
import tempfile
import time
import yaml
from datetime import datetime
from enum import Enum
from threading import Thread
from typing import List, Optional, Tuple, Any

import numpy as np
import pandas as pd

from control import utils, utils_acquisition
import control._def
from control.core.auto_focus_controller import AutoFocusController
from control.core.multi_point_utils import MultiPointControllerFunctions, ScanPositionInformation, AcquisitionParameters
from control.core.scan_coordinates import ScanCoordinates
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.microscope import Microscope
from control.core.multi_point_worker import MultiPointWorker
from control.core.objective_store import ObjectiveStore
from control.core.memory_profiler import MemoryMonitor, log_memory
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from squid.abc import CameraFrame, AbstractCamera, AbstractStage
import squid.acquisition_state
import squid.logging


NoOpCallbacks = MultiPointControllerFunctions(
    signal_acquisition_start=lambda *a, **kw: None,
    signal_acquisition_finished=lambda *a, **kw: None,
    signal_new_image=lambda *a, **kw: None,
    signal_current_configuration=lambda *a, **kw: None,
    signal_current_fov=lambda *a, **kw: None,
    signal_overall_progress=lambda *a, **kw: None,
    signal_region_progress=lambda *a, **kw: None,
)


# Local alias kept for diff stability. The shared helper now lives in
# control.utils.serialize_for_yaml.
from control.utils import serialize_for_yaml as _serialize_for_yaml  # noqa: E402


def _save_acquisition_yaml(
    params: "AcquisitionParameters",
    experiment_path: str,
    region_shapes: dict = None,
    widget_type: str = "wellplate",
    objective_info: dict = None,
    wellplate_format: str = None,
    scan_size_mm: float = 0.0,
    overlap_percent: float = 10.0,
    protocol: Optional[dict] = None,
) -> None:
    """Save acquisition parameters to YAML file.

    Args:
        params: AcquisitionParameters dataclass
        experiment_path: Path to experiment folder
        region_shapes: Optional dict of {region_id: shape} from ScanCoordinates
        widget_type: "wellplate" or "flexible"
        objective_info: Dict with objective name, magnification, pixel_size_um
        wellplate_format: String like "384 well plate" or None
        scan_size_mm: Scan size in mm (for wellplate mode)
        overlap_percent: FOV overlap percentage
    """
    # Build common sections
    yaml_dict = {
        "acquisition": {
            "experiment_id": params.experiment_ID,
            "start_time": params.acquisition_start_time,
            "widget_type": widget_type,
            "xy_mode": params.xy_mode,
            "skip_saving": params.skip_saving,
        },
        "objective": objective_info or {},
        "sample": {
            "wellplate_format": wellplate_format,
        },
        "z_stack": {
            "nz": params.NZ,
            "delta_z_mm": params.deltaZ,
            "config": params.z_stacking_config,
            "z_range_mm": _serialize_for_yaml(params.z_range) if params.z_range else None,
            "use_piezo": params.use_piezo,
        },
        "time_series": {
            "nt": params.Nt,
            "delta_t_s": params.deltat,
        },
        "autofocus": {
            "contrast_af": params.do_autofocus,
            "laser_af": params.do_reflection_autofocus,
        },
        "channels": [_serialize_for_yaml(ch) for ch in params.selected_configurations],
    }

    # Add widget-specific scan section
    if widget_type == "wellplate":
        yaml_dict["wellplate_scan"] = {
            "scan_size_mm": scan_size_mm,
            "overlap_percent": overlap_percent,
            "regions": [
                {
                    "name": name,
                    "center_mm": _serialize_for_yaml(center),
                    "shape": region_shapes.get(name) if region_shapes else None,
                    "fovs": [
                        _serialize_for_yaml(c)
                        for c in params.scan_position_information.scan_region_fov_coords_mm.get(name, [])
                    ],
                }
                for name, center in zip(
                    params.scan_position_information.scan_region_names,
                    params.scan_position_information.scan_region_coords_mm,
                )
            ],
        }
    else:  # flexible
        yaml_dict["flexible_scan"] = {
            "nx": params.NX,
            "ny": params.NY,
            "delta_x_mm": params.deltaX,
            "delta_y_mm": params.deltaY,
            "overlap_percent": overlap_percent,
            "positions": [
                {
                    "name": name,
                    "center_mm": _serialize_for_yaml(center),
                    "fovs": [
                        _serialize_for_yaml(c)
                        for c in params.scan_position_information.scan_region_fov_coords_mm.get(name, [])
                    ],
                }
                for name, center in zip(
                    params.scan_position_information.scan_region_names,
                    params.scan_position_information.scan_region_coords_mm,
                )
            ],
        }

    # Add remaining common sections
    yaml_dict["mosaic_view"] = {
        "save_overview": control._def.SAVE_DOWNSAMPLED_OVERVIEW,
        "save_well_images": control._def.SAVE_DOWNSAMPLED_WELL_IMAGES,
        "resolution_um": control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM,
    }
    yaml_dict["plate"] = {
        "num_rows": params.plate_num_rows,
        "num_cols": params.plate_num_cols,
    }

    if protocol:
        yaml_dict["protocol"] = protocol

    yaml_path = os.path.join(experiment_path, "acquisition.yaml")
    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(f"# Acquisition Parameters - {params.experiment_ID}\n\n")
            yaml.dump(yaml_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except (OSError, yaml.YAMLError) as exc:
        _log = squid.logging.get_logger(__name__)
        _log.error("Failed to write acquisition YAML file '%s': %s", yaml_path, exc)


def _noop(*_args, **_kwargs):
    return None


# Every callback the worker can fire, silenced.  A timing probe must not reach
# QtMultiPointController's signal bridge, or it would trigger the whole
# acquisition-finished fan-out: mosaic auto-save, NDViewer end_acquisition, the RAM and
# backpressure monitor teardown, the z-plot, and the workflow runner.
_QUIET_CALLBACKS = MultiPointControllerFunctions(
    signal_acquisition_start=_noop,
    signal_acquisition_finished=_noop,
    signal_new_image=_noop,
    signal_current_configuration=_noop,
    signal_current_fov=_noop,
    signal_overall_progress=_noop,
    signal_region_progress=_noop,
)


@dataclasses.dataclass
class TimingProbeResult:
    """Outcome of a no-illumination timing probe.  See run_timing_probe()."""

    ok: bool
    per_fov_s: Optional[float] = None
    move_s: Optional[float] = None
    acquire_s: Optional[float] = None
    af_s: Optional[float] = None
    n_fovs_probed: int = 0
    laser_af_failures: int = 0
    aborted: bool = False
    note: str = ""

    def usable(self) -> bool:
        """True when this measurement should be trusted as a per-FOV duration.

        An aborted probe measured a partial FOV, and a probe whose autofocus failed did
        not do the work a real FOV will do -- neither is representative.
        """
        return bool(self.ok and self.per_fov_s and not self.aborted and not self.laser_af_failures)


class MultiPointController:
    def __init__(
        self,
        microscope: Microscope,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        callbacks: MultiPointControllerFunctions,
        scan_coordinates: Optional[ScanCoordinates] = None,
        laser_autofocus_controller: Optional[LaserAutofocusController] = None,
        alignment_widget=None,
    ):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._alignment_widget = alignment_widget  # Optional AlignmentWidget for coordinate offset
        self.microscope: Microscope = microscope
        self.camera: AbstractCamera = microscope.camera
        self.stage: AbstractStage = microscope.stage
        self.piezo: Optional[PiezoStage] = microscope.addons.piezo_stage
        self.microcontroller: Microcontroller = microscope.low_level_drivers.microcontroller
        self.liveController: LiveController = live_controller
        self.autofocusController: AutoFocusController = autofocus_controller
        self.laserAutoFocusController: LaserAutofocusController = laser_autofocus_controller
        self.objectiveStore: ObjectiveStore = objective_store
        self.callbacks: MultiPointControllerFunctions = callbacks
        self.multiPointWorker: Optional[MultiPointWorker] = None
        self.thread: Optional[Thread] = None
        self._per_acq_log_handler = None
        self._memory_monitor: Optional[MemoryMonitor] = None
        self._slack_notifier = None  # Optional SlackNotifier for notifications

        # Pre-warm job runner subprocess at controller init (reduces acquisition start delay)
        # Backpressure values (tuple) are created here and shared with both the pre-warmed runner
        # and the BackpressureController in the worker, ensuring consistent tracking.
        self._prewarmed_job_runner: Optional["JobRunner"] = None
        self._prewarmed_bp_values: Optional["BackpressureValues"] = None
        if control._def.Acquisition.USE_MULTIPROCESSING:
            self._start_prewarmed_job_runner()

        self.NX = 1
        self.deltaX = control._def.Acquisition.DX
        self.NY = 1
        self.deltaY = control._def.Acquisition.DY
        self.NZ = 1
        # TODO(imo): Switch all to consistent mm units
        self.deltaZ = control._def.Acquisition.DZ / 1000
        self.Nt = 1
        self.deltat = 0

        self.do_autofocus = False
        self.do_reflection_af = False
        # GUI-facing default: OFF, so the per-channel Z-offset applies only when the user
        # opts in (the GUI checkbox seeds this flag on construction). build_params() copies
        # this flag into AcquisitionParameters.apply_channel_offset at acquisition time, so
        # the two are coupled at runtime; only the dataclass *default* stays True, as the
        # direct-constructor contract for TCP/MCP callers that build AcquisitionParameters
        # without going through this controller.
        self.apply_channel_offset = False
        self.display_resolution_scaling = control._def.Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
        self.use_piezo = control._def.MULTIPOINT_USE_PIEZO_FOR_ZSTACKS
        self.experiment_ID = None
        # Outcome of the last run_acquisition(): None while in flight; "completed" |
        # "completed_with_errors" | "user_abort" | "error" from the worker, or "failed_to_start" when the
        # run never launched a worker. Read by finished-callback consumers (no payload on the signal).
        self.last_end_reason: Optional[str] = None
        self.last_image_count: int = 0
        self.protocol_info: Optional[dict] = None  # fluidics protocol context for the next run's acquisition.yaml
        self.use_manual_focus_map = False
        self.base_path = None
        self.skip_saving = False
        self.dry_run = False
        self.xy_mode = "Current Position"
        self.widget_type = "wellplate"  # "wellplate" or "flexible"
        self.scan_size_mm = 0.0  # For wellplate mode: size of scan area per region
        self.overlap_percent = 10.0  # FOV overlap percentage

        self.focus_map = None
        self.region_laser_af_offsets = {}
        self.gen_focus_map = False
        self.focus_map_storage = []
        self.already_using_fmap = False
        self.selected_configurations = []
        self.scanCoordinates = scan_coordinates
        self.old_images_per_page = 1
        self.z_range: Tuple[float, float] = None
        self.z_stacking_config = control._def.Z_STACKING_CONFIG

        self._start_position: Optional[squid.abc.Pos] = None

    def _start_prewarmed_job_runner(self):
        """Start a job runner subprocess that warms up in the background.

        This reduces acquisition start delay by having the subprocess already
        running when the user clicks 'Start Acquisition'.

        Also creates backpressure values that will be used by both the
        pre-warmed runner and the BackpressureController in the worker.

        Known limitation: Pre-warming for the NEXT acquisition is started when
        the CURRENT acquisition begins (i.e., when ``get_prewarmed_job_runner()``
        is called). If the user starts another acquisition before pre-warming
        finishes (~1.2s), the worker will wait for the subprocess. This only
        affects rapid-fire manual clicking; real workloads (full plate scans,
        time-lapse with intervals >2s) are unaffected.
        """
        from control.core.job_processing import JobRunner
        from control.core.backpressure import create_backpressure_values

        self._log.info("Pre-warming job runner subprocess...")
        # Create shared backpressure values for cross-process tracking
        self._prewarmed_bp_values = create_backpressure_values()

        self._prewarmed_job_runner = JobRunner(
            bp_pending_jobs=self._prewarmed_bp_values[0],
            bp_pending_bytes=self._prewarmed_bp_values[1],
            bp_capacity_event=self._prewarmed_bp_values[2],
        )
        self._prewarmed_job_runner.start()

    def _cleanup_prewarmed_runner(
        self,
        runner: Optional["JobRunner"],
        timeout_s: float = 1.0,
        context: str = "",
    ) -> None:
        """Shutdown a pre-warmed job runner.

        Args:
            runner: JobRunner to shutdown, or None
            timeout_s: Timeout for runner shutdown
            context: Context string for error messages (e.g., "during close")
        """
        if runner is not None:
            try:
                runner.shutdown(timeout_s=timeout_s)
            except Exception as e:
                self._log.error(f"Error shutting down pre-warmed runner {context}: {e}")

    def get_prewarmed_job_runner(self) -> Tuple[Optional["JobRunner"], Optional["BackpressureValues"]]:
        """Get the pre-warmed job runner and its shared backpressure values.

        Returns:
            Tuple of (runner, bp_values) where:
            - runner: JobRunner instance or None if not available
            - bp_values: BackpressureValues tuple or None

        The runner and values are cleared (so they're only used once).

        Usage:
            runner, bp_values = controller.get_prewarmed_job_runner()
            worker = MultiPointWorker(..., prewarmed_job_runner=runner,
                                      prewarmed_bp_values=bp_values)
        """
        runner = self._prewarmed_job_runner
        bp_values = self._prewarmed_bp_values

        # Clear references (so they're only used once)
        self._prewarmed_job_runner = None
        self._prewarmed_bp_values = None

        # Start warming up a new one for the next acquisition
        if control._def.Acquisition.USE_MULTIPROCESSING:
            self._start_prewarmed_job_runner()

        return runner, bp_values

    def set_alignment_widget(self, alignment_widget):
        """Set the alignment widget for coordinate offset during acquisitions."""
        self._alignment_widget = alignment_widget

    def set_slack_notifier(self, slack_notifier):
        """Set the Slack notifier for acquisition notifications."""
        self._slack_notifier = slack_notifier

    def _start_per_acquisition_log(self) -> None:
        if not control._def.ENABLE_PER_ACQUISITION_LOG:
            return
        if self._per_acq_log_handler is not None:
            return
        if not self.base_path or not self.experiment_ID:
            return

        acq_dir = os.path.join(self.base_path, self.experiment_ID)
        log_path = os.path.join(acq_dir, "acquisition.log")
        try:
            self._per_acq_log_handler = squid.logging.add_file_handler(
                log_path, replace_existing=True, level=squid.logging.py_logging.DEBUG
            )
        except Exception:
            self._log.exception("Failed to start per-acquisition logging")
            self._per_acq_log_handler = None

    def _stop_per_acquisition_log(self) -> None:
        if self._per_acq_log_handler is None:
            return
        try:
            squid.logging.remove_handler(self._per_acq_log_handler)
        except Exception:
            self._log.exception("Failed to stop per-acquisition logging")
        finally:
            self._per_acq_log_handler = None

    def acquisition_in_progress(self):
        if self.thread and self.thread.is_alive() and self.multiPointWorker:
            return True
        return False

    def set_use_piezo(self, checked):
        if checked and self.piezo is None:
            raise ValueError("Cannot enable piezo - no piezo stage configured")
        self.use_piezo = checked
        # TODO(imo): Why do we only allow runtime updates of use_piezo (not all the other params?)
        if self.multiPointWorker:
            self.multiPointWorker.update_use_piezo(checked)

    def set_z_stacking_config(self, z_stacking_config_index):
        if z_stacking_config_index in control._def.Z_STACKING_CONFIG_MAP:
            self.z_stacking_config = control._def.Z_STACKING_CONFIG_MAP[z_stacking_config_index]
        self._log.info(f"z-stacking configuration set to {self.z_stacking_config}")

    def set_z_range(self, minZ, maxZ):
        self.z_range = [minZ, maxZ]

    def set_NX(self, N):
        self.NX = N

    def set_NY(self, N):
        self.NY = N

    def set_NZ(self, N):
        self.NZ = N

    def set_Nt(self, N):
        self.Nt = N

    def set_deltaX(self, delta):
        self.deltaX = delta

    def set_deltaY(self, delta):
        self.deltaY = delta

    def set_deltaZ(self, delta_um):
        self.deltaZ = delta_um / 1000

    def set_deltat(self, delta):
        self.deltat = delta

    def set_af_flag(self, flag):
        self.do_autofocus = flag

    def set_reflection_af_flag(self, flag):
        self.do_reflection_af = flag

    def set_apply_channel_offset(self, flag: bool):
        self.apply_channel_offset = flag

    def set_manual_focus_map_flag(self, flag):
        self.use_manual_focus_map = flag

    def set_gen_focus_map_flag(self, flag):
        self.gen_focus_map = flag
        if not flag:
            self.autofocusController.set_focus_map_use(False)

    def set_focus_map(self, focusMap):
        self.focus_map = focusMap  # None if dont use focusMap

    def set_region_laser_af_offsets(self, offsets):
        # region_id -> µm offset from the global laser-AF reference plane. Empty dict means
        # every FOV targets the reference (displacement 0), i.e. current behavior.
        self.region_laser_af_offsets = dict(offsets or {})

    def set_base_path(self, path):
        self.base_path = path

    def set_skip_saving(self, skip_saving):
        self.skip_saving = skip_saving

    def set_dry_run(self, dry_run: bool):
        """Dry run: the real acquisition path runs, but the illumination is never opened.

        Set ONLY by run_timing_probe().  The flag on its own does not make a run safe --
        timing_probe_refusal_reason() holds the guards that do, and _illumination_fuse()
        is the runtime backstop.
        """
        self.dry_run = dry_run

    def set_xy_mode(self, xy_mode):
        self.xy_mode = xy_mode

    def set_widget_type(self, widget_type: str):
        self.widget_type = widget_type

    def set_scan_size(self, scan_size_mm: float):
        self.scan_size_mm = scan_size_mm

    def set_overlap_percent(self, overlap_percent: float):
        self.overlap_percent = overlap_percent

    def start_new_experiment(self, experiment_ID, add_timestamp=True):
        """Create the experiment folder and write its parameter files.

        add_timestamp=True (default): folder = experiment_ID (spaces -> underscores) + "_" + now.
        add_timestamp=False: folder = experiment_ID verbatim; raises FileExistsError if it already exists
        (the fluidics protocol runner names session folders itself and never reuses one).
        """
        if add_timestamp:
            self.experiment_ID = experiment_ID.replace(" ", "_") + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        else:
            self.experiment_ID = experiment_ID
        self.recording_start_time = time.time()
        # create a new folder
        experiment_dir = os.path.join(self.base_path, self.experiment_ID)
        if not add_timestamp and os.path.exists(experiment_dir):
            raise FileExistsError(experiment_dir)
        utils.ensure_directory_exists(experiment_dir)
        # Save acquisition configuration via ConfigRepository
        self.liveController.microscope.config_repo.save_acquisition_output(
            output_dir=experiment_dir,
            objective=self.objectiveStore.current_objective,
            channels=self.selected_configurations,
            confocal_mode=self.liveController.is_confocal_mode(),
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
            objective_info = self.objectiveStore.objectives_dict.get(current_objective, {})
            acquisition_parameters["objective"] = {}
            for k in objective_info.keys():
                acquisition_parameters["objective"][k] = objective_info[k]
            acquisition_parameters["objective"]["name"] = current_objective
        except (AttributeError, KeyError) as e:
            self._log.debug(f"Could not get objective from objectiveStore: {e}, trying defaults")
            try:
                objective_info = control._def.OBJECTIVES[control._def.DEFAULT_OBJECTIVE]
                acquisition_parameters["objective"] = {}
                for k in objective_info.keys():
                    acquisition_parameters["objective"][k] = objective_info[k]
                acquisition_parameters["objective"]["name"] = control._def.DEFAULT_OBJECTIVE
            except (AttributeError, KeyError) as e:
                self._log.debug(f"Could not get default objective info: {e}")
        # TODO: USE OBJECTIVE STORE DATA
        acquisition_parameters["sensor_pixel_size_um"] = self.camera.get_pixel_size_binned_um()
        acquisition_parameters["tube_lens_mm"] = control._def.TUBE_LENS_MM
        acquisition_parameters["confocal_mode"] = self.liveController.is_confocal_mode()
        f = open(os.path.join(self.base_path, self.experiment_ID) + "/acquisition parameters.json", "w")
        f.write(json.dumps(acquisition_parameters))
        f.close()

    def set_selected_configurations(self, selected_configurations_name):
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.liveController.get_channel_by_name(self.objectiveStore.current_objective, configuration_name)
            if config:
                self.selected_configurations.append(config)

    def get_acquisition_image_count(self):
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
                len(region_coords) for (region_id, region_coords) in self.scanCoordinates.region_fov_coordinates.items()
            ]
            all_regions_coord_count = sum(coords_per_region)

            non_merged_images = self.Nt * self.NZ * all_regions_coord_count * len(self.selected_configurations)
            # When capturing merged images, we capture 1 per fov (where all the configurations are merged)
            merged_images = self.Nt * self.NZ * all_regions_coord_count if control._def.MERGE_CHANNELS else 0

            return non_merged_images + merged_images
        except AttributeError:
            # We don't init all fields in __init__, so it's easy to get attribute errors.  We consider
            # this "not configured" and want it to be a ValueError.
            raise ValueError("Not properly configured for an acquisition, cannot calculate image count.")

    # Rough per-operation costs used by get_estimated_time_point_duration_s().  Calibrated
    # against a real 4-FOV / 10-z / 1-channel / 500 ms / laser-AF run that took 31.5 [s].
    _EST_XY_MOVE_OVERHEAD_S = 0.22  # blocking stage.move_x_to + move_y_to, beyond the stabilization sleeps
    _EST_FRAME_OVERHEAD_S = 0.115  # per frame, beyond the exposure (config switch, trigger, dispatch)
    _EST_LASER_AF_S = 1.0  # per FOV, laser reflection AF
    _EST_CONTRAST_AF_S = 3.0  # per AF event, contrast-based AF (very rough)
    _EST_TIME_POINT_OVERHEAD_S = 0.5  # folder creation, coordinates.csv, .done file

    def get_estimated_time_point_duration_s(self) -> float:
        """
        Return a ROUGH estimate, in seconds, of how long ONE time point of the currently
        configured acquisition will take.

        This is a coarse, open-loop model built from exposure times, FOV count, Z levels,
        channel count and autofocus settings plus a handful of empirically calibrated
        per-operation constants.  It does NOT model stage travel distance, disk or queue
        backpressure, filter wheel moves, fluidics, laser engine warmup, or AF retries.
        Treat +/- 30% as normal.  It exists only to warn the operator pre-flight when a time
        point plainly cannot fit inside the requested dt interval.

        Raises a ValueError if the class is not configured for a valid acquisition.
        """
        try:
            n_fovs = sum(
                len(region_coords)
                for (_region_id, region_coords) in self.scanCoordinates.region_fov_coordinates.items()
            )
            n_channels = len(self.selected_configurations)
            if n_fovs <= 0 or n_channels <= 0:
                raise ValueError("Cannot estimate time point duration with no FOVs or no channels selected.")

            # Every channel costs its own exposure plus a fixed per-frame overhead.
            exposure_s = sum(float(config.exposure_time) / 1000.0 for config in self.selected_configurations)
            per_z_level_s = exposure_s + n_channels * self._EST_FRAME_OVERHEAD_S

            if self.use_piezo:
                z_step_s = control._def.MULTIPOINT_PIEZO_DELAY_MS / 1000.0
            else:
                z_step_s = control._def.SCAN_STABILIZATION_TIME_MS_Z / 1000.0

            xy_move_s = (
                control._def.SCAN_STABILIZATION_TIME_MS_X + control._def.SCAN_STABILIZATION_TIME_MS_Y
            ) / 1000.0 + self._EST_XY_MOVE_OVERHEAD_S

            # Laser AF runs at every FOV.  Contrast AF only runs when the z-stack shape allows
            # it, and then only once every NUMBER_OF_FOVS_PER_AF FOVs -- see
            # MultiPointWorker.perform_autofocus.
            if self.do_reflection_af:
                af_per_fov_s = self._EST_LASER_AF_S
            elif self.do_autofocus and (self.NZ == 1 or self.z_stacking_config == "FROM CENTER"):
                af_per_fov_s = self._EST_CONTRAST_AF_S / max(1, control._def.Acquisition.NUMBER_OF_FOVS_PER_AF)
            else:
                af_per_fov_s = 0.0

            per_fov_s = xy_move_s + af_per_fov_s + self.NZ * (z_step_s + per_z_level_s)
            return n_fovs * per_fov_s + self._EST_TIME_POINT_OVERHEAD_S
        except AttributeError:
            # We don't init all fields in __init__, so it's easy to get attribute errors.  We
            # consider this "not configured" and want it to be a ValueError.
            raise ValueError("Not properly configured for an acquisition, cannot estimate time point duration.")

    # ─────────────────────────────────────────────────────────────────────────────
    # Timing probe: measure per-FOV duration on real hardware, without illuminating
    # ─────────────────────────────────────────────────────────────────────────────

    _TIMING_PROBE_DIR_PREFIX = "_timing_probe_"

    def _flatten_planned_fovs(self) -> List[Tuple[str, Tuple[float, ...]]]:
        """Planned FOVs in the order run_coordinate_acquisition() will visit them."""
        flattened = []
        for region_id, coords in self.scanCoordinates.region_fov_coordinates.items():
            for coord in coords:
                flattened.append((region_id, tuple(coord)))
        return flattened

    def timing_probe_refusal_reason(self) -> Optional[str]:
        """None if a no-illumination timing probe is safe here, else an operator-facing reason."""
        if self.acquisition_in_progress():
            return "An acquisition is already running."
        if not self.base_path:
            return "No saving directory set."
        if not self.selected_configurations:
            return "No channels selected."
        if not self._flatten_planned_fovs():
            return "No FOVs planned."
        if not self.validate_acquisition_settings():
            return "Laser autofocus has no reference position."
        if control._def.ENABLE_NL5 and control._def.NL5_USE_DOUT:
            # nl5.start_acquisition() fires the laser and triggers the camera together.
            return "NL5 confocal has no non-illuminating acquisition path."
        if (
            self.liveController.trigger_mode == control._def.TriggerMode.HARDWARE
            and control._def.HARDWARE_TRIGGER_MODE == control._def.HardwareTriggerMode.LEVEL
        ):
            # Under LEVEL the camera trigger pulse width IS illumination_on_time, so we cannot
            # zero the strobe without also destroying the trigger.
            return "Hardware LEVEL trigger cannot be run without illumination."
        if self.do_autofocus and not self.do_reflection_af:
            # Contrast AF is a contrast search: run dark it never early-exits and parks z at
            # the bottom of its range.  Laser AF uses a separate reflection laser and is fine.
            return "Contrast autofocus needs illumination; simulate with laser AF or AF off."
        if self.protocol_info is not None:
            # A fluidics protocol run is queued: the ProtocolRunner drives reagents in step with
            # imaging, and a dry run would advance the sequence without acquiring anything.
            return "Fluidics runs are not simulated."
        return None

    @contextlib.contextmanager
    def _illumination_fuse(self):
        """Make every illumination-opening path RAISE for the duration of a dry run.

        Suppression by flag is only as good as the last refactor, and the failure mode here
        is silent: a leak bleaches the operator's sample with no error.  This turns any
        unanticipated attempt to open the light into a loud abort instead.

        Deliberately NOT fused: microcontroller.turn_on_AF_laser().  That is the separate
        reflection-autofocus laser, which must fire for laser AF to run at all.
        """
        live_controller = self.liveController
        microcontroller = self.microcontroller

        def _blocked(name):
            def _raise(*_args, **_kwargs):
                raise RuntimeError(f"Timing probe attempted to open illumination via {name}")

            return _raise

        live_controller.turn_on_illumination = _blocked("LiveController.turn_on_illumination")
        microcontroller.turn_on_illumination = _blocked("Microcontroller.turn_on_illumination")
        try:
            # Start from a known-dark state so nothing can re-assert a gate pin mid-probe.
            microcontroller.turn_off_illumination()
            microcontroller.wait_till_operation_is_completed()
            yield
        finally:
            # These are instance attributes shadowing the bound methods; deleting restores them.
            for obj, name in ((live_controller, "turn_on_illumination"), (microcontroller, "turn_on_illumination")):
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass
            try:
                microcontroller.turn_off_illumination()
                microcontroller.wait_till_operation_is_completed()
            except Exception:
                self._log.exception("Timing probe: failed to force illumination off on exit")

    def _build_probe_scan_plan(self, probe_fovs) -> ScanPositionInformation:
        """A two-FOV scan plan built directly, so the real plan is never touched.

        run_acquisition() applies the focus map by mutating the live ScanCoordinates; a probe
        must not do that, so the z interpolation is applied to this copy only.
        """
        fov_coords = collections.OrderedDict()
        for region_id, coord in probe_fovs:
            fov_coords.setdefault(region_id, []).append(coord)

        if self.focus_map:
            for region_id, coords in fov_coords.items():
                fov_coords[region_id] = [
                    (c[0], c[1], self.focus_map.interpolate(c[0], c[1], region_id)) for c in coords
                ]

        return ScanPositionInformation(
            scan_region_coords_mm=[self.scanCoordinates.region_centers[r] for r in fov_coords],
            scan_region_names=list(fov_coords),
            scan_region_fov_coords_mm=fov_coords,
        )

    def _harvest_probe_timing(self, worker, n_probed: int) -> TimingProbeResult:
        """Turn the worker's timers into a per-FOV duration.

        Uses the SECOND FOV when there is one, because its move_to_coordinate is a real
        inter-FOV hop at the true scan step; the first FOV's move starts from wherever the
        stage happened to be and also absorbs job-runner cold start.
        """
        timing = worker._timing
        moves = timing.get_intervals("move_to_coordinate")
        acquires = timing.get_intervals("acquire_at_position")
        autofocuses = timing.get_intervals("perform_autofocus")

        if not moves or not acquires:
            return TimingProbeResult(
                ok=False, aborted=worker.abort_requested_fn(), note="Simulation produced no timings"
            )

        index = 1 if (len(moves) > 1 and len(acquires) > 1) else 0
        move_s = moves[index]
        acquire_s = acquires[index]
        af_s = autofocuses[index] if len(autofocuses) > index else 0.0
        per_fov_s = move_s + acquire_s

        note = ""
        if index == 0:
            # Only one FOV was planned, so its move was a no-op.  Fall back to the model's
            # XY term so the number stays comparable with a multi-FOV estimate.
            per_fov_s += (
                control._def.SCAN_STABILIZATION_TIME_MS_X + control._def.SCAN_STABILIZATION_TIME_MS_Y
            ) / 1000.0 + self._EST_XY_MOVE_OVERHEAD_S
            note = "single-FOV plan: XY move modelled, not measured"

        return TimingProbeResult(
            ok=True,
            per_fov_s=per_fov_s,
            move_s=move_s,
            acquire_s=acquire_s,
            af_s=af_s,
            n_fovs_probed=n_probed,
            laser_af_failures=getattr(worker, "_laser_af_failures", 0),
            aborted=worker.abort_requested_fn(),
            note=note,
        )

    def _finish_timing_probe(self, probe_dir: str, saved: dict) -> None:
        """Always runs: restore the stage, the live view, the settings, and delete the folder."""
        try:
            if self._start_position:
                position = self._start_position
                self._log.info(f"Timing probe: returning to {position}")
                self.stage.move_x_to(position.x_mm)
                self.stage.move_y_to(position.y_mm)
                self.stage.move_z_to(position.z_mm)
                self._start_position = None
        except Exception:
            self._log.exception("Timing probe: failed to restore the stage position")

        try:
            self.camera.enable_callbacks(self.camera_callback_was_enabled_before_multipoint)
            if self.configuration_before_running_multipoint is not None:
                self.liveController.set_microscope_mode(self.configuration_before_running_multipoint)
            if self.liveController_was_live_before_multipoint and control._def.RESUME_LIVE_AFTER_ACQUISITION:
                self.liveController.start_live()
        except Exception:
            self._log.exception("Timing probe: failed to restore the live/camera state")

        try:
            shutil.rmtree(probe_dir, ignore_errors=True)
        except Exception:
            self._log.exception(f"Timing probe: failed to delete {probe_dir}")

        for attribute, value in saved.items():
            setattr(self, attribute, value)
        self.multiPointWorker = None
        self.thread = None

    def sweep_stale_timing_probe_dirs(self, older_than_s: float = 3600.0) -> None:
        """Delete timing-probe folders orphaned by a crash mid-probe."""
        if not self.base_path or not os.path.isdir(self.base_path):
            return
        try:
            for name in os.listdir(self.base_path):
                if not name.startswith(self._TIMING_PROBE_DIR_PREFIX):
                    continue
                path = os.path.join(self.base_path, name)
                if os.path.isdir(path) and (time.time() - os.path.getmtime(path)) > older_than_s:
                    self._log.info(f"Removing stale timing probe folder {path}")
                    shutil.rmtree(path, ignore_errors=True)
        except Exception:
            self._log.exception("Failed to sweep stale timing probe folders")

    def run_timing_probe(self, on_finished) -> Optional[str]:
        """Run the first two planned FOVs on real hardware, dark, and time the second.

        Every real cost is exercised -- stage moves, autofocus, channel switching (including
        the emission filter wheel), exposures and disk writes -- so the resulting per-FOV
        duration reflects this instrument rather than a table of constants.  The sample is
        never illuminated; see MultiPointWorker._illumination_on_for_frame().

        KNOWN LIMIT: two FOVs measure write dispatch and first-touch latency but not
        steady-state disk backpressure, so on a slow or network volume the number still reads
        optimistic.

        Returns None once the probe thread is running (on_finished(TimingProbeResult) is
        called when it ends), or a refusal string if the probe cannot safely run.
        """
        reason = self.timing_probe_refusal_reason()
        if reason:
            return reason

        probe_fovs = self._flatten_planned_fovs()[:2]
        probe_scan_plan = self._build_probe_scan_plan(probe_fovs)
        experiment_id = self._TIMING_PROBE_DIR_PREFIX + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        probe_dir = os.path.join(self.base_path, experiment_id)

        saved = {
            "Nt": self.Nt,
            "deltat": self.deltat,
            "experiment_ID": self.experiment_ID,
            "dry_run": self.dry_run,
            "z_range": self.z_range,
        }

        try:
            self.Nt = 1
            self.deltat = 0
            self.dry_run = True
            self.experiment_ID = experiment_id
            utils.ensure_directory_exists(probe_dir)

            self.abort_acqusition_requested = False
            self._start_position = self.stage.get_pos()
            if self.z_range is None:
                self.z_range = (
                    self._start_position.z_mm,
                    self._start_position.z_mm + self.deltaZ * (self.NZ - 1),
                )

            self.configuration_before_running_multipoint = self.liveController.currentConfiguration
            self.liveController_was_live_before_multipoint = self.liveController.is_live
            if self.liveController.is_live:
                self.liveController.stop_live()
            self.camera_callback_was_enabled_before_multipoint = self.camera.get_callbacks_enabled()
            self.camera.enable_callbacks(True)

            self.timestamp_acquisition_started = time.time()
            acquisition_params = self.build_params(
                scan_position_information=probe_scan_plan,
                # Read, do NOT consume: run_acquisition() clears these because it owns the run.
                region_laser_af_offsets=dict(self.region_laser_af_offsets),
            )

            prewarmed_runner, prewarmed_bp_values = self.get_prewarmed_job_runner()
            self.multiPointWorker = MultiPointWorker(
                scope=self.microscope,
                live_controller=self.liveController,
                auto_focus_controller=self.autofocusController,
                laser_auto_focus_controller=self.laserAutoFocusController,
                objective_store=self.objectiveStore,
                acquisition_parameters=acquisition_params,
                callbacks=_QUIET_CALLBACKS,
                abort_requested_fn=lambda: self.abort_acqusition_requested,
                request_abort_fn=self.request_abort_aquisition,
                extra_job_classes=[],
                alignment_widget=self._alignment_widget,
                slack_notifier=None,  # no Slack traffic for a probe
                prewarmed_job_runner=prewarmed_runner,
                prewarmed_bp_values=prewarmed_bp_values,
                run_state_writer=None,  # -> NullRunStateWriter, no watchdog breadcrumb
            )
            worker = self.multiPointWorker
        except Exception:
            self._finish_timing_probe(probe_dir, saved)
            raise

        def _probe_thread():
            result = TimingProbeResult(ok=False, note="Simulation failed")
            try:
                with self._illumination_fuse():
                    worker.run()
                result = self._harvest_probe_timing(worker, len(probe_fovs))
            except Exception:
                self._log.exception("Timing probe failed")
            finally:
                self._finish_timing_probe(probe_dir, saved)
                on_finished(result)

        self.thread = Thread(target=_probe_thread, name="Timing probe thread", daemon=True)
        self.thread.start()
        return None

    def _temporary_get_an_image_hack(self) -> Tuple[np.array, bool]:
        was_streaming = self.camera.get_is_streaming()
        callbacks_were_enabled = self.camera.get_callbacks_enabled()
        self.camera.enable_callbacks(False)
        test_frame = None
        if not was_streaming:
            self.camera.start_streaming()
        try:
            channels = self.liveController.get_channels(self.objectiveStore.current_objective)
            if not channels:
                self._log.warning("No channels available in _temporary_get_an_image_hack")
                return (None, False)
            # Note: config is currently unused but kept for potential future use
            config = channels[0]
            if (
                self.liveController.trigger_mode == control._def.TriggerMode.SOFTWARE
                or self.liveController.trigger_mode == control._def.TriggerMode.HARDWARE
            ):
                self.camera.send_trigger()
            test_frame = self.camera.read_camera_frame()
        finally:
            self.camera.enable_callbacks(callbacks_were_enabled)
            if not was_streaming:
                self.camera.stop_streaming()
        return (test_frame.frame, test_frame.is_color()) if test_frame else (None, False)

    def get_estimated_acquisition_disk_storage(self):
        """
        This does its best to return the number of bytes needed to store the settings for the currently
        configured acquisition on disk.  If you don't have at least this amount of disk space available
        when starting this acquisition, it is likely it will fail with an "out of disk space" error.
        """
        # TODO(imo): This needs updating for AbstractCamera
        if not len(self.liveController.get_channels(self.objectiveStore.current_objective)):
            raise ValueError("Cannot calculate disk space requirements without any valid configurations.")
        first_config = self.liveController.get_channels(self.objectiveStore.current_objective)[0]

        # Our best bet is to grab an image, and use that for our size estimate.
        test_image = None
        is_color = True
        try:
            test_image, is_color = self._temporary_get_an_image_hack()
        except Exception as e:
            self._log.exception("Couldn't capture image from camera for size estimate, using worst cast image.")
            # Not ideal that we need to catch Exception, but the camera implementations vary wildly...
            pass

        if test_image is None:
            is_color = squid.abc.CameraPixelFormat.is_color_format(self.camera.get_pixel_format())
            # Do our best to create a fake image with the correct properties.
            # TODO(imo): It'd be better to pull this from our camera but need to wait for AbstractCamera for a consistent way to do that.
            width, height = self.camera.get_crop_size()
            bytes_per_pixel = 3 if is_color else 2  # Worst case assumptions: 24 bit color, 16 bit grayscale

            test_image = np.random.randint(2**16 - 1, size=(height, width, (3 if is_color else 1)), dtype=np.uint16)

        # Depending on settings, we modify the image before saving.  This means we need to actually save an image
        # to see how much disk space it takes up.  This can be very wrong (eg: if we compress during saving, then
        # it is dependent on the data), but is better than just guessing based on raw image size.
        with tempfile.TemporaryDirectory() as temp_save_dir:
            file_id = "test_id"
            test_config = first_config
            size_before = utils.get_directory_disk_usage(pathlib.Path(temp_save_dir))
            saved_image = utils_acquisition.save_image(test_image, file_id, temp_save_dir, test_config, is_color)
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
          least ``control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM`` (in µm). The scan
          extents are divided by this downsampled pixel size to obtain the mosaic width
          and height in pixels.

        Assumptions:

        * Each mosaic pixel is stored as a 16‑bit unsigned integer (2 bytes per pixel).
        * The returned value includes memory for all mosaic channel layers, by
          multiplying by ``len(self.selected_configurations)``.
        * The estimate only applies when ``control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY``
          is enabled and when valid scan coordinates with regions are available;
          otherwise, it returns 0.
        """
        if not control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
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
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        downsample_factor = max(1, int(control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um))
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
            is_color_attr = getattr(self.camera, "is_color", None)
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
            squid.logging.get_logger(__name__).warning(
                "Estimated mosaic RAM is 0 because no channel configurations are selected."
            )
            return 0

        return mosaic_width * mosaic_height * bytes_per_pixel * num_channels

    def run_acquisition(self, acquire_current_fov=False):
        # Consume the per-region laser-AF offsets for THIS run and clear the sticky controller
        # copy up-front. Any early return below — or a prior GUI abort that pushed offsets but
        # never reached run_acquisition (e.g. aborted on the disk/RAM dialog) — then cannot leak
        # them into a later acquisition from an entry point that never sets them (fluidics
        # widget, TCP control server).
        run_region_laser_af_offsets = self.region_laser_af_offsets
        self.region_laser_af_offsets = {}
        # Same one-run consumption for the protocol context: an early return must not leak it into a later run.
        run_protocol_info = self.protocol_info
        self.protocol_info = None
        self.last_end_reason = None
        self.last_image_count = 0
        if not self.validate_acquisition_settings():
            # emit acquisition finished signal to re-enable the UI
            self.last_end_reason = "failed_to_start"
            self.callbacks.signal_acquisition_finished()
            return
        self._start_per_acquisition_log()

        # Start memory monitoring for the acquisition (if enabled)
        if control._def.ENABLE_MEMORY_PROFILING:
            self._memory_monitor = MemoryMonitor(
                sample_interval_ms=200,
                process_name="main",
                track_children=True,
                log_interval_s=30.0,  # Log every 30 seconds during acquisition
            )
            self._memory_monitor.start("ACQUISITION_START")
            log_memory("ACQUISITION START", include_children=True)

        thread_started = False
        hardware_prepared = False
        self._run_state_writer = squid.acquisition_state.NullRunStateWriter()
        try:
            self._log.info("start multipoint")
            self._start_position = self.stage.get_pos()

            if self.z_range is None:
                self.z_range = (self._start_position.z_mm, self._start_position.z_mm + self.deltaZ * (self.NZ - 1))

            acquisition_scan_coordinates = self.scanCoordinates
            self.run_acquisition_current_fov = False
            if acquire_current_fov:
                pos = self.stage.get_pos()
                # No callback - we don't want to clobber existing info with this one off fov acquisition
                acquisition_scan_coordinates = ScanCoordinates(
                    objectiveStore=self.scanCoordinates.objectiveStore,
                    stage=self.scanCoordinates.stage,
                    camera=self.scanCoordinates.camera,
                )
                acquisition_scan_coordinates.clear_regions()
                acquisition_scan_coordinates.add_single_fov_region(
                    "current", center_x=pos.x_mm, center_y=pos.y_mm, center_z=pos.z_mm
                )
                self.run_acquisition_current_fov = True

            scan_position_information = ScanPositionInformation.from_scan_coordinates(acquisition_scan_coordinates)

            # Save coordinates to CSV in top level folder
            coordinates_df = pd.DataFrame(columns=["region", "x (mm)", "y (mm)", "z (mm)"])
            for region_id, coords_list in scan_position_information.scan_region_fov_coords_mm.items():
                for coord in coords_list:
                    row = {"region": region_id, "x (mm)": coord[0], "y (mm)": coord[1]}
                    # Add z coordinate if available
                    if len(coord) > 2:
                        row["z (mm)"] = coord[2]
                    coordinates_df = pd.concat([coordinates_df, pd.DataFrame([row])], ignore_index=True)
            coordinates_df.to_csv(os.path.join(self.base_path, self.experiment_ID, "coordinates.csv"), index=False)

            self._log.info(
                f"num fovs: {sum(len(coords) for coords in scan_position_information.scan_region_fov_coords_mm)}"
            )
            self._log.info(f"num regions: {len(scan_position_information.scan_region_coords_mm)}")
            self._log.info(f"region ids: {scan_position_information.scan_region_names}")
            self._log.info(f"region centers: {scan_position_information.scan_region_coords_mm}")

            self.abort_acqusition_requested = False

            self.configuration_before_running_multipoint = self.liveController.currentConfiguration
            # stop live
            if self.liveController.is_live:
                self.liveController_was_live_before_multipoint = True
                self.liveController.stop_live()  # @@@ to do: also uncheck the live button
            else:
                self.liveController_was_live_before_multipoint = False

            self.camera_callback_was_enabled_before_multipoint = self.camera.get_callbacks_enabled()
            # We need callbacks, because we trigger and then use callbacks for image processing.  This
            # lets us do overlapping triggering (soon).
            self.camera.enable_callbacks(True)
            hardware_prepared = True

            # run the acquisition
            self.timestamp_acquisition_started = time.time()
            if self.focus_map and not acquire_current_fov:
                self._log.info("Using focus surface for Z interpolation")
                for region_id in scan_position_information.scan_region_names:
                    region_fov_coords = scan_position_information.scan_region_fov_coords_mm[region_id]
                    # Rewrite this acquisition's private snapshot; the GUI's
                    # ScanCoordinates keeps the user-configured coordinates.
                    for i, coords in enumerate(region_fov_coords):
                        x, y = coords[:2]  # This handles both (x,y) and (x,y,z) formats
                        z = self.focus_map.interpolate(x, y, region_id)
                        region_fov_coords[i] = (x, y, z)

            elif self.gen_focus_map and not self.do_reflection_af:
                self._log.info("Generating autofocus plane for multipoint grid")
                bounds = self.scanCoordinates.get_scan_bounds()
                if not bounds:
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
                    self.stage.move_x_to(x_center)
                    self.stage.move_y_to(y_center)

                except ValueError:
                    self._log.exception("Invalid coordinates for autofocus plane, aborting.")
                    return

            def finish_fn():
                try:
                    self._on_acquisition_completed()
                    # Note: signal_acquisition_finished is called inside _on_acquisition_completed()
                finally:
                    self._stop_per_acquisition_log()

            updated_callbacks = dataclasses.replace(self.callbacks, signal_acquisition_finished=finish_fn)

            acquisition_params = self.build_params(
                scan_position_information=scan_position_information,
                region_laser_af_offsets=run_region_laser_af_offsets,
            )

            # Gather objective and camera info for YAML
            current_objective = self.objectiveStore.current_objective
            objective_dict = self.objectiveStore.objectives_dict.get(current_objective, {})
            pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
            objective_info = {
                "name": current_objective,
                "magnification": objective_dict.get("magnification"),
                "NA": objective_dict.get("NA"),
                "pixel_size_um": pixel_size_um,
                "camera_binning": list(self.camera.get_binning()) if hasattr(self.camera, "get_binning") else None,
                "sensor_pixel_size_um": self.camera.get_pixel_size_binned_um(),
            }

            # Get wellplate format if available
            wellplate_format = getattr(self.scanCoordinates, "format", None)

            # Save acquisition parameters to YAML
            experiment_path = os.path.join(self.base_path, self.experiment_ID)
            region_shapes = getattr(self.scanCoordinates, "region_shapes", None)
            _save_acquisition_yaml(
                acquisition_params,
                experiment_path,
                region_shapes,
                self.widget_type,
                objective_info,
                wellplate_format,
                self.scan_size_mm,
                self.overlap_percent,
                protocol=run_protocol_info,
            )

            # Acquisition watchdog: drop the "running" breadcrumb (covers GUI + MCP-server runs).
            # self._run_state_writer is already a NullRunStateWriter (set before the outer try); it
            # stays one if start() below fails, so a breadcrumb write failure never breaks acquisition.
            try:
                expected = {
                    "timepoints": self.Nt,
                    "regions": len(scan_position_information.scan_region_coords_mm),
                    "fovs": sum(len(c) for c in scan_position_information.scan_region_fov_coords_mm.values()),
                    "channels": len(self.selected_configurations),
                    "z": self.NZ,
                }
                config_path = (getattr(control._def, "CACHED_CONFIG_FILE_PATH", None) or "").strip() or None
                self._run_state_writer = squid.acquisition_state.RunStateWriter.start(
                    experiment_id=self.experiment_ID,
                    pid=os.getpid(),
                    config_path=config_path,
                    output_path=experiment_path,
                    expected=expected,
                )
            except Exception as e:
                self._log.warning(f"Failed to write acquisition watchdog start state: {e}")

            # Get pre-warmed job runner and its shared backpressure values
            # (starts a new one warming for next acquisition)
            prewarmed_runner, prewarmed_bp_values = self.get_prewarmed_job_runner()

            # Worker creation can fail - ensure runner is cleaned up on error
            try:
                self.multiPointWorker = MultiPointWorker(
                    scope=self.microscope,
                    live_controller=self.liveController,
                    auto_focus_controller=self.autofocusController,
                    laser_auto_focus_controller=self.laserAutoFocusController,
                    objective_store=self.objectiveStore,
                    acquisition_parameters=acquisition_params,
                    callbacks=updated_callbacks,
                    abort_requested_fn=lambda: self.abort_acqusition_requested,
                    request_abort_fn=self.request_abort_aquisition,
                    extra_job_classes=[],
                    alignment_widget=self._alignment_widget,
                    slack_notifier=self._slack_notifier,
                    prewarmed_job_runner=prewarmed_runner,
                    prewarmed_bp_values=prewarmed_bp_values,
                    run_state_writer=self._run_state_writer,
                )
            except Exception:
                # Clean up pre-warmed runner if worker creation failed.
                # Note: get_prewarmed_job_runner() already started a NEW pre-warmed runner,
                # so we're cleaning up the one that was handed off to us.
                self._cleanup_prewarmed_runner(
                    prewarmed_runner,
                    context="after worker creation failure",
                )
                raise

            # Signal after worker creation so backpressure_controller is available
            self.callbacks.signal_acquisition_start(acquisition_params)

            self.thread = Thread(target=self.multiPointWorker.run, name="Acquisition thread", daemon=True)
            self.thread.start()
            thread_started = True
        finally:
            if not thread_started:
                # Acquisition never launched a worker — close out the breadcrumb so the
                # watchdog doesn't later misread the lingering "running" state as a hang.
                self._run_state_writer.end("error", None)
                self._stop_per_acquisition_log()
                # Stop memory monitor if acquisition setup failed
                if self._memory_monitor is not None:
                    self._memory_monitor.stop()
                    self._memory_monitor = None
                # The run never launched a worker (validation passed but a focus-map early return or a
                # pre-worker exception ended it). Undo what was prepared and report exactly once, so every
                # caller - GUI widgets, TCP server, the fluidics protocol runner - sees acquisition_finished.
                if hardware_prepared:
                    try:
                        self.camera.enable_callbacks(self.camera_callback_was_enabled_before_multipoint)
                        if (
                            self.liveController_was_live_before_multipoint
                            and control._def.RESUME_LIVE_AFTER_ACQUISITION
                        ):
                            self.liveController.start_live()
                    except Exception:
                        self._log.exception("Failed to restore camera/live state after a failed acquisition start")
                self.last_end_reason = "failed_to_start"
                try:
                    self.callbacks.signal_acquisition_finished()
                except Exception:
                    self._log.exception("acquisition_finished callback failed after a failed acquisition start")

    def build_params(
        self, scan_position_information: ScanPositionInformation, region_laser_af_offsets: Optional[dict] = None
    ) -> AcquisitionParameters:
        # Determine plate dimensions from wellplate format if available
        plate_num_rows = 8  # Default for 96-well
        plate_num_cols = 12
        if hasattr(self.scanCoordinates, "format") and self.scanCoordinates.format:
            format_settings = control._def.get_wellplate_settings(self.scanCoordinates.format)
            if format_settings:
                plate_num_rows = format_settings.get("rows", 8)
                plate_num_cols = format_settings.get("cols", 12)
            else:
                self._log.debug(
                    f"Unknown wellplate format '{self.scanCoordinates.format}', using default 96-well dimensions"
                )

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
            apply_channel_offset=self.apply_channel_offset,
            use_piezo=self.use_piezo,
            display_resolution_scaling=self.display_resolution_scaling,
            z_stacking_config=self.z_stacking_config,
            z_range=self.z_range,
            skip_saving=self.skip_saving,
            dry_run=self.dry_run,
            plate_num_rows=plate_num_rows,
            plate_num_cols=plate_num_cols,
            xy_mode=self.xy_mode,
            region_laser_af_offsets=region_laser_af_offsets if region_laser_af_offsets is not None else {},
        )

    def _on_acquisition_completed(self):
        self._log.debug("MultiPointController._on_acquisition_completed called")
        # Note: Plate views are saved per timepoint in the worker's run_single_time_point method

        # restore the previous selected mode
        if self.gen_focus_map:
            self.autofocusController.clear_focus_map()
            for x, y, z in self.focus_map_storage:
                self.autofocusController.focus_map_coords.append((x, y, z))
            self.autofocusController.use_focus_map = self.already_using_fmap
        self.callbacks.signal_current_configuration(self.configuration_before_running_multipoint)
        self.liveController.set_microscope_mode(self.configuration_before_running_multipoint)

        # Restore callbacks to pre-acquisition state
        self.camera.enable_callbacks(self.camera_callback_was_enabled_before_multipoint)

        # re-enable live if it's previously on
        if self.liveController_was_live_before_multipoint and control._def.RESUME_LIVE_AFTER_ACQUISITION:
            self.liveController.start_live()

        # Stop memory monitoring and log final report (in background to not delay acquisition finish)
        if self._memory_monitor is not None:
            monitor = self._memory_monitor
            self._memory_monitor = None

            def _stop_monitor_background():
                try:
                    if control._def.ENABLE_MEMORY_PROFILING:
                        log_memory("ACQUISITION COMPLETE", include_children=True)
                    monitor.stop()
                except Exception as e:
                    self._log.error(f"Error stopping memory monitor in background: {e}")

            Thread(target=_stop_monitor_background, daemon=True).start()

        # emit the acquisition finished signal to enable the UI
        self._log.info(f"total time for acquisition + processing + reset: {time.time() - self.recording_start_time}")
        utils.create_done_file(os.path.join(self.base_path, self.experiment_ID))
        worker = self.multiPointWorker
        self.last_end_reason = getattr(worker, "end_reason", None) or "completed"
        self.last_image_count = int(getattr(worker, "image_count", 0) or 0)

        if self.run_acquisition_current_fov:
            self.run_acquisition_current_fov = False

        if self._start_position:
            x_mm = self._start_position.x_mm
            y_mm = self._start_position.y_mm
            z_mm = self._start_position.z_mm
            self._log.info(f"Moving back to start position: (x,y,z) [mm] = ({x_mm}, {y_mm}, {z_mm})")
            self.stage.move_x_to(x_mm)
            self.stage.move_y_to(y_mm)
            self.stage.move_z_to(z_mm)
            self._start_position = None

        ending_pos = self.stage.get_pos()
        self.callbacks.signal_current_fov(ending_pos.x_mm, ending_pos.y_mm)

        self.callbacks.signal_acquisition_finished()

    def request_abort_aquisition(self):
        self.abort_acqusition_requested = True

    def validate_acquisition_settings(self) -> bool:
        """Validate settings before starting acquisition"""
        if self.do_reflection_af and not self.laserAutoFocusController.laser_af_properties.has_reference:
            self._log.error(
                "Laser Autofocus Not Ready - Please set the laser autofocus reference position before starting acquisition with laser AF enabled."
            )
            return False
        return True

    def get_plate_view(self) -> np.ndarray:
        """Get the current plate view array from the acquisition.

        Returns:
            Copy of the plate view array, or None if not available.
        """
        if self.multiPointWorker is not None:
            return self.multiPointWorker.get_plate_view()
        return None

    @property
    def backpressure_controller(self) -> Optional["BackpressureController"]:
        """Get the backpressure controller from the current worker.

        Returns:
            BackpressureController if worker exists, None otherwise.
        """
        if self.multiPointWorker is not None:
            return getattr(self.multiPointWorker, "_backpressure", None)
        return None

    _PROCESS_TERMINATE_TIMEOUT_S = 1.0

    def close(self, timeout_s: float = 5.0) -> None:
        """Clean up resources on application shutdown.

        Aborts any running acquisition and waits for cleanup to complete.
        If job runner processes do not terminate gracefully, they are forcefully
        terminated (SIGTERM) then killed (SIGKILL). This may result in incomplete
        well images or unsaved data.

        Args:
            timeout_s: Maximum time to wait for acquisition thread to finish.
                      Job runner processes have a separate timeout defined by
                      _PROCESS_TERMINATE_TIMEOUT_S.
        """
        # Clean up pre-warmed job runner if it exists
        if self._prewarmed_job_runner is not None:
            self._log.info("Shutting down pre-warmed job runner...")
        self._cleanup_prewarmed_runner(
            self._prewarmed_job_runner,
            timeout_s=self._PROCESS_TERMINATE_TIMEOUT_S,
            context="during close",
        )
        self._prewarmed_job_runner = None
        self._prewarmed_bp_values = None

        # Abort any running acquisition
        try:
            if self.acquisition_in_progress():
                self.request_abort_aquisition()
                if self.thread is not None:
                    self.thread.join(timeout=timeout_s)
                    if self.thread.is_alive():
                        self._log.warning(f"Acquisition thread did not stop within {timeout_s}s")
        except Exception:
            self._log.exception("Error aborting acquisition during close")

        # Stop memory monitor if running
        try:
            if self._memory_monitor is not None:
                self._memory_monitor.stop()
                self._memory_monitor = None
        except Exception:
            self._log.exception("Error stopping memory monitor during close")

        # Forcefully terminate any remaining job runner processes
        if self.multiPointWorker is not None:
            job_runners = getattr(self.multiPointWorker, "_job_runners", [])
            for job_class, job_runner in job_runners:
                try:
                    if job_runner is not None and job_runner.is_alive():
                        self._log.warning(f"Terminating {job_class.__name__} job runner (abnormal shutdown)")
                        job_runner.terminate()
                        job_runner.join(timeout=self._PROCESS_TERMINATE_TIMEOUT_S)
                        # If still alive after terminate, force kill
                        if job_runner.is_alive():
                            self._log.warning(f"Force killing {job_class.__name__} job runner")
                            job_runner.kill()
                            job_runner.join(timeout=self._PROCESS_TERMINATE_TIMEOUT_S)
                            # Final check - warn if zombie process remains
                            if job_runner.is_alive():
                                self._log.error(
                                    f"{job_class.__name__} job runner could not be terminated - "
                                    "zombie process may remain"
                                )
                except Exception:
                    self._log.exception(f"Error terminating {job_class.__name__} job runner")

            # Release backpressure controller resources to prevent semaphore leaks
            try:
                backpressure = getattr(self.multiPointWorker, "_backpressure", None)
                if backpressure is not None:
                    backpressure.close()
            except Exception:
                self._log.exception("Error closing backpressure controller during shutdown")

        # Clear worker reference
        self.multiPointWorker = None
        self.thread = None
