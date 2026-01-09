import dataclasses
import json
import math
import os
import pathlib
import tempfile
import time
import yaml
from datetime import datetime
from enum import Enum
from threading import Thread
from typing import Optional, Tuple, Any

import numpy as np
import pandas as pd

from control import utils, utils_acquisition
import control._def
from control.core.auto_focus_controller import AutoFocusController
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.multi_point_utils import MultiPointControllerFunctions, ScanPositionInformation, AcquisitionParameters
from control.core.scan_coordinates import ScanCoordinates
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.microscope import Microscope
from control.core.multi_point_worker import MultiPointWorker
from control.core.objective_store import ObjectiveStore
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from squid.abc import CameraFrame, AbstractCamera, AbstractStage
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


def _serialize_for_yaml(obj):
    """Recursively serialize objects to YAML-compatible types."""
    if obj is None:
        return None
    elif isinstance(obj, Enum):
        return obj.value
    # Handle numpy types - convert to native Python types
    elif isinstance(obj, np.ndarray):
        return [_serialize_for_yaml(item) for item in obj.tolist()]
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()  # Convert numpy scalar to Python scalar
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize_for_yaml(v) for k, v in dataclasses.asdict(obj).items()}
    elif hasattr(obj, "model_dump"):
        return _serialize_for_yaml(obj.model_dump())
    elif isinstance(obj, dict):
        return {k: _serialize_for_yaml(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_for_yaml(item) for item in obj]
    else:
        return obj


def _save_acquisition_yaml(
    params: "AcquisitionParameters",
    experiment_path: str,
    region_shapes: dict = None,
    widget_type: str = "wellplate",
    objective_info: dict = None,
    wellplate_format: str = None,
    scan_size_mm: float = 0.0,
    overlap_percent: float = 10.0,
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
                }
                for name, center in zip(
                    params.scan_position_information.scan_region_names,
                    params.scan_position_information.scan_region_coords_mm,
                )
            ],
        }

    # Add remaining common sections
    yaml_dict["downsampled_views"] = {
        "enabled": params.generate_downsampled_views,
        "save_well_images": params.save_downsampled_well_images,
        "well_resolutions_um": _serialize_for_yaml(params.downsampled_well_resolutions_um),
        "plate_resolution_um": params.downsampled_plate_resolution_um,
        "z_projection": _serialize_for_yaml(params.downsampled_z_projection),
        "interpolation_method": _serialize_for_yaml(params.downsampled_interpolation_method),
    }
    yaml_dict["plate"] = {
        "num_rows": params.plate_num_rows,
        "num_cols": params.plate_num_cols,
    }
    yaml_dict["fluidics"] = {
        "enabled": params.use_fluidics,
    }

    yaml_path = os.path.join(experiment_path, "acquisition.yaml")
    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(f"# Acquisition Parameters - {params.experiment_ID}\n\n")
            yaml.dump(yaml_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except (OSError, yaml.YAMLError) as exc:
        _log = squid.logging.get_logger(__name__)
        _log.error("Failed to write acquisition YAML file '%s': %s", yaml_path, exc)


class MultiPointController:
    def __init__(
        self,
        microscope: Microscope,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        channel_configuration_mananger: ChannelConfigurationManager,
        callbacks: MultiPointControllerFunctions,
        scan_coordinates: Optional[ScanCoordinates] = None,
        laser_autofocus_controller: Optional[LaserAutofocusController] = None,
    ):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.microscope: Microscope = microscope
        self.camera: AbstractCamera = microscope.camera
        self.stage: AbstractStage = microscope.stage
        self.piezo: Optional[PiezoStage] = microscope.addons.piezo_stage
        self.microcontroller: Microcontroller = microscope.low_level_drivers.microcontroller
        self.liveController: LiveController = live_controller
        self.autofocusController: AutoFocusController = autofocus_controller
        self.laserAutoFocusController: LaserAutofocusController = laser_autofocus_controller
        self.objectiveStore: ObjectiveStore = objective_store
        self.channelConfigurationManager: ChannelConfigurationManager = channel_configuration_mananger
        self.callbacks: MultiPointControllerFunctions = callbacks
        self.multiPointWorker: Optional[MultiPointWorker] = None
        self.fluidics: Optional[Any] = microscope.addons.fluidics
        self.thread: Optional[Thread] = None
        self._per_acq_log_handler = None

        self.NX = 1
        self.deltaX = control._def.Acquisition.DX
        self.NY = 1
        self.deltaY = control._def.Acquisition.DY
        self.NZ = 1
        # TODO(imo): Switch all to consistent mm units
        self.deltaZ = control._def.Acquisition.DZ / 1000
        self.Nt = 1
        self.deltat = 0

        self.deltaX = control._def.Acquisition.DX
        self.deltaY = control._def.Acquisition.DY

        self.do_autofocus = False
        self.do_reflection_af = False
        self.display_resolution_scaling = control._def.Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
        self.use_piezo = control._def.MULTIPOINT_USE_PIEZO_FOR_ZSTACKS
        self.experiment_ID = None
        self.use_manual_focus_map = False
        self.base_path = None
        self.use_fluidics = False
        self.skip_saving = False
        self.xy_mode = "Current Position"
        self.widget_type = "wellplate"  # "wellplate" or "flexible"
        self.scan_size_mm = 0.0  # For wellplate mode: size of scan area per region
        self.overlap_percent = 10.0  # FOV overlap percentage

        self.focus_map = None
        self.gen_focus_map = False
        self.focus_map_storage = []
        self.already_using_fmap = False
        self.selected_configurations = []
        self.scanCoordinates = scan_coordinates
        self.old_images_per_page = 1
        self.z_range: Tuple[float, float] = None
        self.z_stacking_config = control._def.Z_STACKING_CONFIG

        self._start_position: Optional[squid.abc.Pos] = None

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
        print(f"z-stacking configuration set to {self.z_stacking_config}")

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

    def set_manual_focus_map_flag(self, flag):
        self.use_manual_focus_map = flag

    def set_gen_focus_map_flag(self, flag):
        self.gen_focus_map = flag
        if not flag:
            self.autofocusController.set_focus_map_use(False)

    def set_focus_map(self, focusMap):
        self.focus_map = focusMap  # None if dont use focusMap

    def set_base_path(self, path):
        self.base_path = path

    def set_use_fluidics(self, use_fluidics):
        self.use_fluidics = use_fluidics

    def set_skip_saving(self, skip_saving):
        self.skip_saving = skip_saving

    def set_xy_mode(self, xy_mode):
        self.xy_mode = xy_mode

    def set_widget_type(self, widget_type: str):
        self.widget_type = widget_type

    def set_scan_size(self, scan_size_mm: float):
        self.scan_size_mm = scan_size_mm

    def set_overlap_percent(self, overlap_percent: float):
        self.overlap_percent = overlap_percent

    def start_new_experiment(self, experiment_ID):  # @@@ to do: change name to prepare_folder_for_new_experiment
        # generate unique experiment ID
        self.experiment_ID = experiment_ID.replace(" ", "_") + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
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
            objective_info = self.objectiveStore.objectives_dict.get(current_objective, {})
            acquisition_parameters["objective"] = {}
            for k in objective_info.keys():
                acquisition_parameters["objective"][k] = objective_info[k]
            acquisition_parameters["objective"]["name"] = current_objective
        except:
            try:
                objective_info = control._def.OBJECTIVES[control._def.DEFAULT_OBJECTIVE]
                acquisition_parameters["objective"] = {}
                for k in objective_info.keys():
                    acquisition_parameters["objective"][k] = objective_info[k]
                acquisition_parameters["objective"]["name"] = control._def.DEFAULT_OBJECTIVE
            except:
                pass
        # TODO: USE OBJECTIVE STORE DATA
        acquisition_parameters["sensor_pixel_size_um"] = self.camera.get_pixel_size_binned_um()
        acquisition_parameters["tube_lens_mm"] = control._def.TUBE_LENS_MM
        acquisition_parameters["confocal_mode"] = self.channelConfigurationManager.is_confocal_mode()
        f = open(os.path.join(self.base_path, self.experiment_ID) + "/acquisition parameters.json", "w")
        f.write(json.dumps(acquisition_parameters))
        f.close()

    def set_selected_configurations(self, selected_configurations_name):
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.channelConfigurationManager.get_channel_configuration_by_name(
                self.objectiveStore.current_objective, configuration_name
            )
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

    def _temporary_get_an_image_hack(self) -> Tuple[np.array, bool]:
        was_streaming = self.camera.get_is_streaming()
        callbacks_were_enabled = self.camera.get_callbacks_enabled()
        self.camera.enable_callbacks(False)
        test_frame = None
        if not was_streaming:
            self.camera.start_streaming()
        try:
            config = self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)[0]
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
        if not len(self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)):
            raise ValueError("Cannot calculate disk space requirements without any valid configurations.")
        first_config = self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)[0]

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
        if not self.validate_acquisition_settings():
            # emit acquisition finished signal to re-enable the UI
            self.callbacks.signal_acquisition_finished()
            return
        self._start_per_acquisition_log()
        thread_started = False
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

            # run the acquisition
            self.timestamp_acquisition_started = time.time()
            if self.focus_map:
                self._log.info("Using focus surface for Z interpolation")
                for region_id in scan_position_information.scan_region_names:
                    region_fov_coords = scan_position_information.scan_region_fov_coords_mm[region_id]
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
                    self.callbacks.signal_acquisition_finished()
                finally:
                    self._stop_per_acquisition_log()

            updated_callbacks = dataclasses.replace(self.callbacks, signal_acquisition_finished=finish_fn)

            acquisition_params = self.build_params(scan_position_information=scan_position_information)

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
            )

            self.callbacks.signal_acquisition_start(acquisition_params)
            self.multiPointWorker = MultiPointWorker(
                scope=self.microscope,
                live_controller=self.liveController,
                auto_focus_controller=self.autofocusController,
                laser_auto_focus_controller=self.laserAutoFocusController,
                objective_store=self.objectiveStore,
                channel_configuration_mananger=self.channelConfigurationManager,
                acquisition_parameters=acquisition_params,
                callbacks=updated_callbacks,
                abort_requested_fn=lambda: self.abort_acqusition_requested,
                request_abort_fn=self.request_abort_aquisition,
                extra_job_classes=[],
            )

            self.thread = Thread(target=self.multiPointWorker.run, name="Acquisition thread", daemon=True)
            thread_started = True
            self.thread.start()
        finally:
            if not thread_started:
                self._stop_per_acquisition_log()

    def build_params(self, scan_position_information: ScanPositionInformation) -> AcquisitionParameters:
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
            use_piezo=self.use_piezo,
            display_resolution_scaling=self.display_resolution_scaling,
            z_stacking_config=self.z_stacking_config,
            z_range=self.z_range,
            use_fluidics=self.use_fluidics,
            skip_saving=self.skip_saving,
            # Downsampled view generation parameters
            generate_downsampled_views=control._def.SAVE_DOWNSAMPLED_WELL_IMAGES or control._def.DISPLAY_PLATE_VIEW,
            save_downsampled_well_images=control._def.SAVE_DOWNSAMPLED_WELL_IMAGES,
            downsampled_well_resolutions_um=control._def.DOWNSAMPLED_WELL_RESOLUTIONS_UM,
            downsampled_plate_resolution_um=control._def.DOWNSAMPLED_PLATE_RESOLUTION_UM,
            downsampled_z_projection=control._def.DOWNSAMPLED_Z_PROJECTION,
            downsampled_interpolation_method=control._def.DOWNSAMPLED_INTERPOLATION_METHOD,
            plate_num_rows=plate_num_rows,
            plate_num_cols=plate_num_cols,
            xy_mode=self.xy_mode,
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

        # emit the acquisition finished signal to enable the UI
        self._log.info(f"total time for acquisition + processing + reset: {time.time() - self.recording_start_time}")
        utils.create_done_file(os.path.join(self.base_path, self.experiment_ID))

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
