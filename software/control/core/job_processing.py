import abc
import multiprocessing
import queue
import os
import time
import json
from datetime import datetime
from contextlib import contextmanager
from typing import ClassVar, Dict, Generic, List, Optional, Tuple, TypeVar, Union
from uuid import uuid4

from dataclasses import dataclass, field
from filelock import FileLock, Timeout as FileLockTimeout

import imageio as iio
import numpy as np
import tifffile

from control import _def, utils_acquisition
from control._def import ZProjectionMode, DownsamplingMethod
import squid.abc
import squid.logging
from control.utils_config import ChannelMode
from control.core import utils_ome_tiff_writer as ome_tiff_writer
from control.core.memory_profiler import (
    start_worker_monitoring,
    stop_worker_monitoring,
    set_worker_operation,
    log_memory,
)


@dataclass
class AcquisitionInfo:
    """Acquisition-wide metadata for OME-TIFF file generation.

    This class holds metadata that remains constant across all images in a
    multi-dimensional acquisition (time, z, channel). It is separate from
    CaptureInfo, which holds per-image metadata (position, timestamp, etc.).

    AcquisitionInfo is created once at acquisition start and injected into
    SaveOMETiffJob instances by JobRunner.dispatch() before job execution.

    Attributes:
        total_time_points: Number of time points in the acquisition.
        total_z_levels: Number of z-slices per stack.
        total_channels: Number of imaging channels.
        channel_names: List of channel names for OME-XML metadata.
        experiment_path: Base directory for the experiment output.
        time_increment_s: Time between timepoints in seconds (for OME-XML).
        physical_size_z_um: Z step size in micrometers (for OME-XML).
        physical_size_x_um: Pixel size in X in micrometers (for OME-XML).
        physical_size_y_um: Pixel size in Y in micrometers (for OME-XML).
    """

    total_time_points: int
    total_z_levels: int
    total_channels: int
    channel_names: List[str]
    experiment_path: Optional[str] = None
    time_increment_s: Optional[float] = None
    physical_size_z_um: Optional[float] = None
    physical_size_x_um: Optional[float] = None
    physical_size_y_um: Optional[float] = None


from .downsampled_views import (
    crop_overlap,
    downsample_tile,
    downsample_to_resolutions,
    WellTileAccumulator,
)


# NOTE(imo): We want this to be fast.  But pydantic does not support numpy serialization natively, which means
# that we need a custom serializer (which will be slow!).  So, use dataclass here instead.
@dataclass
class CaptureInfo:
    position: squid.abc.Pos
    z_index: int
    capture_time: float
    configuration: ChannelMode
    save_directory: str
    file_id: str
    region_id: int
    fov: int
    configuration_idx: int
    z_piezo_um: Optional[float] = None
    time_point: Optional[int] = None


@dataclass()
class JobImage:
    image_array: Optional[np.array]


T = TypeVar("T")


@dataclass
class Job(abc.ABC, Generic[T]):
    capture_info: CaptureInfo
    capture_image: JobImage

    job_id: str = field(default_factory=lambda: str(uuid4()))

    def image_array(self) -> np.array:
        if self.capture_image.image_array is not None:
            return self.capture_image.image_array

        raise NotImplementedError("Only np array JobImages are supported right now.")

    @abc.abstractmethod
    def run(self) -> T:
        raise NotImplementedError("You must implement run for your job type.")


@dataclass
class JobResult(Generic[T]):
    job_id: str
    result: Optional[T]
    exception: Optional[Exception]


# Timeout in seconds for acquiring file locks during OME-TIFF writing
FILE_LOCK_TIMEOUT_SECONDS = 10


def _metadata_lock_path(metadata_path: str) -> str:
    return metadata_path + ".lock"


@contextmanager
def _acquire_file_lock(lock_path: str, context: str = ""):
    """Acquire a file lock with timeout, providing a clear error message on failure.

    Args:
        lock_path: Path to the lock file.
        context: Optional context string (e.g., output file path) included in error messages.
    """
    lock = FileLock(lock_path, timeout=FILE_LOCK_TIMEOUT_SECONDS)
    try:
        with lock:
            yield
    except FileLockTimeout as exc:
        context_msg = f" (writing to: {context})" if context else ""
        raise TimeoutError(
            f"Failed to acquire file lock '{lock_path}' within {FILE_LOCK_TIMEOUT_SECONDS} seconds{context_msg}. "
            f"Another process may be holding the lock."
        ) from exc


class SaveImageJob(Job):
    _log: ClassVar = squid.logging.get_logger("SaveImageJob")

    def run(self) -> bool:
        from control.core.io_simulation import is_simulation_enabled, simulated_tiff_write

        image = self.image_array()

        # Simulated disk I/O mode - encode to buffer, throttle, discard
        if is_simulation_enabled():
            bytes_written = simulated_tiff_write(image)
            self._log.debug(
                f"SaveImageJob {self.job_id}: simulated write of {bytes_written} bytes " f"(image shape={image.shape})"
            )
            return True

        is_color = len(image.shape) > 2
        return self.save_image(image, self.capture_info, is_color)

    def save_image(self, image: np.array, info: CaptureInfo, is_color: bool):
        # NOTE(imo): We silently fall back to individual image saving here.  We should warn or do something.
        if _def.FILE_SAVING_OPTION == _def.FileSavingOption.MULTI_PAGE_TIFF:
            metadata = {
                "z_level": info.z_index,
                "channel": info.configuration.name,
                "channel_index": info.configuration_idx,
                "region_id": info.region_id,
                "fov": info.fov,
                "x_mm": info.position.x_mm,
                "y_mm": info.position.y_mm,
                "z_mm": info.position.z_mm,
            }
            # Add requested fields: human-readable time and optional piezo position
            try:
                metadata["time"] = datetime.fromtimestamp(info.capture_time).strftime("%Y-%m-%d %H:%M:%S.%f")
            except Exception:
                metadata["time"] = info.capture_time
            if info.z_piezo_um is not None:
                metadata["z_piezo (um)"] = info.z_piezo_um
            output_path = os.path.join(
                info.save_directory, f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}_stack.tiff"
            )
            # Ensure channel information is preserved across common TIFF readers by:
            # - embedding full metadata as JSON in ImageDescription (description=)
            # - setting PageName (tag 285) to the channel name via extratags
            description = json.dumps(metadata)
            page_name = str(info.configuration.name)

            # extratags format: (code, dtype, count, value, writeonce)
            # PageName (285) expects ASCII; dtype 's' denotes a null-terminated string in tifffile
            extratags = [(285, "s", 0, page_name, False)]

            with tifffile.TiffWriter(output_path, append=True) as tiff_writer:
                tiff_writer.write(
                    image,
                    metadata=metadata,
                    description=description,
                    extratags=extratags,
                )
        else:
            saved_image = utils_acquisition.save_image(
                image=image,
                file_id=info.file_id,
                save_directory=info.save_directory,
                config=info.configuration,
                is_color=is_color,
            )

            if _def.MERGE_CHANNELS:
                # TODO(imo): Add this back in
                raise NotImplementedError("Image merging not supported yet")

        return True


@dataclass
class SaveOMETiffJob(Job):
    """Job for saving images to OME-TIFF format.

    The acquisition_info field is injected by JobRunner.dispatch() before the job runs.
    """

    _log: ClassVar = squid.logging.get_logger("SaveOMETiffJob")
    acquisition_info: Optional[AcquisitionInfo] = field(default=None)

    def run(self) -> bool:
        if self.acquisition_info is None:
            raise ValueError(
                "SaveOMETiffJob.run() requires acquisition_info but it is None. "
                "This job must be dispatched via JobRunner.dispatch(), which injects acquisition_info. "
                "If running directly, set job.acquisition_info before calling run()."
            )

        from control.core.io_simulation import is_simulation_enabled, simulated_ome_tiff_write

        image = self.image_array()

        # Simulated disk I/O mode - encode to buffer, throttle, discard
        if is_simulation_enabled():
            # Build stack key from output path
            ome_folder = ome_tiff_writer.ome_output_folder(self.acquisition_info, self.capture_info)
            base_name = ome_tiff_writer.ome_base_name(self.capture_info)
            stack_key = os.path.join(ome_folder, base_name)

            # Determine 5D shape (T, Z, C, Y, X)
            shape = (
                self.acquisition_info.total_time_points,
                self.acquisition_info.total_z_levels,
                self.acquisition_info.total_channels,
                image.shape[0],
                image.shape[1],
            )

            bytes_written = simulated_ome_tiff_write(
                image=image,
                stack_key=stack_key,
                shape=shape,
                time_point=self.capture_info.time_point or 0,
                z_index=self.capture_info.z_index,
                channel_index=self.capture_info.configuration_idx,
            )
            self._log.debug(
                f"SaveOMETiffJob {self.job_id}: simulated write of {bytes_written} bytes "
                f"(image shape={image.shape})"
            )
            return True

        self._save_ome_tiff(image, self.capture_info)
        return True

    def _save_ome_tiff(self, image: np.ndarray, info: CaptureInfo) -> None:
        # with reference to Talley's https://github.com/pymmcore-plus/pymmcore-plus/blob/main/src/pymmcore_plus/mda/handlers/_ome_tiff_writer.py and Christoph's https://forum.image.sc/t/how-to-create-an-image-series-ome-tiff-from-python/42730/7
        ome_tiff_writer.validate_capture_info(info, self.acquisition_info, image)

        ome_folder = ome_tiff_writer.ome_output_folder(self.acquisition_info, info)
        ome_tiff_writer.ensure_output_directory(ome_folder)

        base_name = ome_tiff_writer.ome_base_name(info)
        output_path = os.path.join(ome_folder, base_name + ".ome.tiff")
        metadata_path = ome_tiff_writer.metadata_temp_path(self.acquisition_info, info, base_name)
        lock_path = _metadata_lock_path(metadata_path)

        with _acquire_file_lock(lock_path, context=output_path):
            metadata = ome_tiff_writer.load_metadata(metadata_path)
            if metadata is None:
                metadata = ome_tiff_writer.initialize_metadata(self.acquisition_info, info, image)
                target_dtype = np.dtype(metadata[ome_tiff_writer.DTYPE_KEY])
                if os.path.exists(output_path):
                    os.remove(output_path)
                tifffile.imwrite(
                    output_path,
                    shape=tuple(metadata[ome_tiff_writer.SHAPE_KEY]),
                    dtype=target_dtype,
                    metadata=ome_tiff_writer.metadata_for_imwrite(metadata),
                    ome=True,
                )
            else:
                expected_shape = tuple(metadata[ome_tiff_writer.SHAPE_KEY])
                if expected_shape[-2:] != image.shape[-2:]:
                    raise ValueError("Image dimensions do not match existing OME memmap stack")
                # acquisition_info is guaranteed non-None here (validated in run())
                if not metadata.get(ome_tiff_writer.CHANNEL_NAMES_KEY) and self.acquisition_info.channel_names:
                    metadata[ome_tiff_writer.CHANNEL_NAMES_KEY] = self.acquisition_info.channel_names

            target_dtype = np.dtype(metadata[ome_tiff_writer.DTYPE_KEY])
            image_to_store = image if image.dtype == target_dtype else image.astype(target_dtype)

            time_point = int(info.time_point)
            z_index = int(info.z_index)
            channel_index = int(info.configuration_idx)
            shape = tuple(metadata[ome_tiff_writer.SHAPE_KEY])
            if not (0 <= time_point < shape[0]):
                raise ValueError("Time point index out of range for OME stack")
            if not (0 <= z_index < shape[1]):
                raise ValueError("Z index out of range for OME stack")
            if not (0 <= channel_index < shape[2]):
                raise ValueError("Channel index out of range for OME stack")

            stack = tifffile.memmap(output_path, dtype=target_dtype, mode="r+")
            if stack.shape != shape:
                stack.shape = shape
            try:
                stack[time_point, z_index, channel_index, :, :] = image_to_store
                stack.flush()
            finally:
                del stack

            metadata = ome_tiff_writer.update_plane_metadata(metadata, info)
            index_key = f"{time_point}-{channel_index}-{z_index}"
            if index_key not in metadata[ome_tiff_writer.WRITTEN_INDICES_KEY]:
                metadata[ome_tiff_writer.WRITTEN_INDICES_KEY].append(index_key)
                metadata[ome_tiff_writer.SAVED_COUNT_KEY] = len(metadata[ome_tiff_writer.WRITTEN_INDICES_KEY])

            # Check if all images have been saved
            is_complete = metadata[ome_tiff_writer.SAVED_COUNT_KEY] >= metadata[ome_tiff_writer.EXPECTED_COUNT_KEY]
            if is_complete:
                metadata[ome_tiff_writer.COMPLETED_KEY] = True

            # Write metadata (includes completed flag if acquisition is done)
            ome_tiff_writer.write_metadata(metadata_path, metadata)

            if is_complete:
                # Finalize OME-XML and clean up temporary files
                with tifffile.TiffFile(output_path) as tif:
                    current_xml = tif.ome_metadata
                ome_xml = ome_tiff_writer.augment_ome_xml(current_xml, metadata)
                tifffile.tiffcomment(output_path, ome_xml.encode("utf-8"))
                if os.path.exists(metadata_path):
                    os.remove(metadata_path)

        # Clean up lock file after lock is released (only when acquisition completed).
        # Race condition note: Between releasing the lock and this cleanup, another process
        # could theoretically acquire the same lock path. However:
        # 1. We only attempt removal if metadata_path is gone (acquisition completed)
        # 2. If another process holds the lock, os.remove fails with OSError (caught below)
        # 3. This is best-effort cleanup; stale locks are also cleaned by cleanup_stale_metadata_files
        try:
            if not os.path.exists(metadata_path):
                os.remove(lock_path)
        except OSError:
            pass  # Lock held by another process, already removed, or platform-specific issue


# These are debugging jobs - they should not be used in normal usage!
class HangForeverJob(Job):
    def run(self) -> bool:
        while True:
            time.sleep(1)

        return True  # noqa


class ThrowImmediatelyJobException(RuntimeError):
    pass


class ThrowImmediatelyJob(Job):
    def run(self) -> bool:
        raise ThrowImmediatelyJobException("ThrowImmediatelyJob threw")


@dataclass
class DownsampledViewResult:
    """Result from DownsampledViewJob containing well images for plate view update."""

    well_id: str
    well_row: int
    well_col: int
    well_images: Dict[int, np.ndarray]  # channel_idx -> downsampled image
    channel_names: List[str]


@dataclass
class DownsampledViewJob(Job):
    """Job to generate downsampled well images and contribute to plate view.

    This job:
    1. Crops overlap from the tile
    2. Accumulates tiles for the well (using class-level storage per process)
    3. When all FOVs for all channels are received, stitches and saves as multipage TIFF
    4. Returns the first channel 10um image via queue for plate view update in main process

    Warning:
        This class uses a mutable class-level accumulator (_well_accumulators) that is
        only safe because each JobRunner runs in its own *process* (via multiprocessing).
        Each worker has its own independent copy of this attribute.

        Do NOT use DownsampledViewJob in a threading context (e.g., with
        ThreadPoolExecutor or other in-process thread runners) without adding
        proper synchronization or refactoring to avoid shared mutable class
        state, as that would lead to race conditions and data corruption.
    """

    # All fields must have defaults because parent class Job has job_id with default
    well_id: str = ""
    well_row: int = 0
    well_col: int = 0
    fov_index: int = 0
    total_fovs_in_well: int = 1
    channel_idx: int = 0
    total_channels: int = 1
    channel_name: str = ""
    fov_position_in_well: Tuple[float, float] = (0.0, 0.0)  # (x_mm, y_mm) relative to well origin
    overlap_pixels: Tuple[int, int, int, int] = field(default=(0, 0, 0, 0))  # (top, bottom, left, right)
    pixel_size_um: float = 1.0
    target_resolutions_um: List[float] = field(default_factory=lambda: [5.0, 10.0, 20.0])
    plate_resolution_um: float = 10.0
    output_dir: str = ""
    channel_names: List[str] = field(default_factory=list)
    z_index: int = 0
    total_z_levels: int = 1
    z_projection_mode: Union[ZProjectionMode, str] = ZProjectionMode.MIP
    interpolation_method: Union[DownsamplingMethod, str] = DownsamplingMethod.INTER_AREA_FAST
    skip_saving: bool = False  # Skip TIFF file saving (just generate for display)

    # Class-level accumulator storage keyed by well_id.
    # Note: This runs inside JobRunner (a multiprocessing.Process), so each worker
    # process has its own copy of this class variable. It is process-local and
    # safe to mutate without cross-process synchronization.
    _well_accumulators: ClassVar[Dict[str, WellTileAccumulator]] = {}
    # Track wells that encountered errors during processing
    _failed_wells: ClassVar[Dict[str, str]] = {}  # well_id -> error message

    @classmethod
    def clear_accumulators(cls) -> None:
        """Clear all accumulated well data and error tracking.

        Call this at the start of a new acquisition to ensure no stale state
        from previous (potentially aborted) acquisitions remains.

        This method is safe to call even if no accumulators exist.
        Performance: O(1) - just clears the dictionaries.
        """
        cls._well_accumulators.clear()
        cls._failed_wells.clear()

    @classmethod
    def get_accumulator_count(cls) -> int:
        """Get the number of wells currently being accumulated.

        Useful for monitoring memory pressure during acquisition.
        """
        return len(cls._well_accumulators)

    @classmethod
    def get_failed_wells(cls) -> Dict[str, str]:
        """Get a copy of the failed wells dictionary.

        Returns:
            Dict mapping well_id to error message for wells that failed processing.
        """
        return cls._failed_wells.copy()

    def run(self) -> Optional[DownsampledViewResult]:
        log = squid.logging.get_logger(self.__class__.__name__)

        t_start = time.perf_counter()

        # Get image array (may involve unpickling)
        tile = self.image_array()
        t_get_image = time.perf_counter()

        # Crop overlap from tile
        cropped = crop_overlap(tile, self.overlap_pixels)

        t_crop = time.perf_counter()

        # Get or create accumulator for this well
        if self.well_id not in self._well_accumulators:
            self._well_accumulators[self.well_id] = WellTileAccumulator(
                well_id=self.well_id,
                total_fovs=self.total_fovs_in_well,
                total_channels=self.total_channels,
                pixel_size_um=self.pixel_size_um,
                channel_names=self.channel_names if self.channel_names else None,
                total_z_levels=self.total_z_levels,
                z_projection_mode=self.z_projection_mode,
            )

        accumulator = self._well_accumulators[self.well_id]
        accumulator.add_tile(
            cropped,
            self.fov_position_in_well,
            self.channel_idx,
            fov_idx=self.fov_index,
            z_index=self.z_index,
        )

        t_accumulate = time.perf_counter()

        # If not all FOVs for all channels received yet, return None
        if not accumulator.is_complete():
            t_intermediate = time.perf_counter()
            z_info = f" z {self.z_index + 1}/{self.total_z_levels}" if self.total_z_levels > 1 else ""
            log.debug(
                f"Well {self.well_id}: channel {self.channel_idx} FOV {self.fov_index + 1}/{self.total_fovs_in_well}{z_info}, "
                f"channels: {accumulator.get_channel_count()}/{self.total_channels} | "
                f"tile={tile.shape}, get_img={t_get_image - t_start:.3f}s, crop={t_crop - t_get_image:.3f}s, "
                f"accum={t_accumulate - t_crop:.3f}s, total={t_intermediate - t_start:.3f}s"
            )
            return None

        # All FOVs for all channels (and z-levels for MIP) received - stitch and save
        z_info = f" x {self.total_z_levels} z-levels ({self.z_projection_mode})" if self.total_z_levels > 1 else ""
        log.info(
            f"Well {self.well_id}: all {self.total_fovs_in_well} FOVs x {self.total_channels} channels{z_info} received, stitching..."
        )

        try:
            t_stitch_start = time.perf_counter()

            # Memory tracking: stitching is memory-intensive
            set_worker_operation(f"STITCH_{self.well_id}")

            # Stitch all channels
            stitched_channels = accumulator.stitch_all_channels()

            t_stitch_end = time.perf_counter()

            # Get channel names for metadata
            channel_names = accumulator.channel_names

            # Convert interpolation_method to enum if string
            interp_method = (
                DownsamplingMethod.convert_to_enum(self.interpolation_method)
                if isinstance(self.interpolation_method, str)
                else self.interpolation_method
            )

            # Memory tracking: downsampling phase
            set_worker_operation(f"DOWNSAMPLE_{self.well_id}")

            # Generate plate view images first (at plate resolution only)
            t_downsample_plate_start = time.perf_counter()
            well_images_for_plate: Dict[int, np.ndarray] = {}
            for ch_idx in sorted(stitched_channels.keys()):
                downsampled = downsample_tile(
                    stitched_channels[ch_idx], self.pixel_size_um, self.plate_resolution_um, interp_method
                )
                well_images_for_plate[ch_idx] = downsampled
            t_downsample_plate_end = time.perf_counter()

            # Memory tracking: save phase
            set_worker_operation(f"SAVE_{self.well_id}")

            # Save TIFFs only if not skipping
            t_save_start = time.perf_counter()
            if not self.skip_saving:
                wells_dir = os.path.join(self.output_dir, "wells")
                os.makedirs(wells_dir, exist_ok=True)

                # Downsample each channel to all target resolutions
                # downsample_to_resolutions handles cascading for INTER_AREA
                # Initialize resolution stacks before the loop to avoid UnboundLocalError if stitched_channels is empty
                resolution_stacks: Dict[float, List[np.ndarray]] = {r: [] for r in self.target_resolutions_um}
                for ch_idx in sorted(stitched_channels.keys()):
                    # Get all resolutions for this channel (may include plate_resolution)
                    resolutions_to_compute = [r for r in self.target_resolutions_um if r != self.plate_resolution_um]
                    downsampled_images = downsample_to_resolutions(
                        stitched_channels[ch_idx], self.pixel_size_um, resolutions_to_compute, interp_method
                    )
                    # Add already-computed plate resolution
                    downsampled_images[self.plate_resolution_um] = well_images_for_plate[ch_idx]

                    # Store for stacking
                    for resolution in self.target_resolutions_um:
                        resolution_stacks[resolution].append(downsampled_images[resolution])

                # Save each resolution as multipage TIFF
                for resolution in self.target_resolutions_um:
                    downsampled_stack = resolution_stacks[resolution]
                    if not downsampled_stack:
                        continue

                    # Stack channels into multipage array (C, H, W)
                    stacked = np.stack(downsampled_stack, axis=0)

                    filename = f"{self.well_id}_{int(resolution)}um.tiff"
                    filepath = os.path.join(wells_dir, filename)

                    # Save as multipage TIFF with channel metadata
                    tifffile.imwrite(
                        filepath,
                        stacked,
                        metadata={
                            "axes": "CYX",
                            "Channel": {"Name": channel_names[: len(downsampled_stack)]},
                        },
                    )
                    log.debug(f"Saved {filepath} with shape {stacked.shape} ({len(downsampled_stack)} channels)")

            t_save_end = time.perf_counter()

            # Log timing summary for performance analysis
            t_total = t_save_end - t_start
            stitched_shape = list(stitched_channels.values())[0].shape if stitched_channels else (0, 0)
            plate_shape = list(well_images_for_plate.values())[0].shape if well_images_for_plate else (0, 0)
            log.debug(
                f"[PERF] Well {self.well_id} complete: "
                f"get_img={t_get_image - t_start:.3f}s, crop={t_crop - t_get_image:.3f}s, "
                f"accum={t_accumulate - t_crop:.3f}s, stitch={t_stitch_end - t_stitch_start:.3f}s, "
                f"downsample_plate={t_downsample_plate_end - t_downsample_plate_start:.3f}s, "
                f"save={t_save_end - t_save_start:.3f}s, "
                f"TOTAL={t_total:.3f}s | "
                f"tile={tile.shape}, stitched={stitched_shape}, plate={plate_shape}, "
                f"channels={len(stitched_channels)}, skip_saving={self.skip_saving}"
            )

            return DownsampledViewResult(
                well_id=self.well_id,
                well_row=self.well_row,
                well_col=self.well_col,
                well_images=well_images_for_plate,
                channel_names=channel_names,
            )

        except Exception as e:
            log.exception(f"Error processing well {self.well_id}: {e}")
            # Track failed well for reporting
            self._failed_wells[self.well_id] = str(e)
            raise
        finally:
            # Ensure accumulator is always cleaned up after processing a complete well
            self._well_accumulators.pop(self.well_id, None)


class JobRunner(multiprocessing.Process):
    def __init__(
        self,
        acquisition_info: Optional[AcquisitionInfo] = None,
        cleanup_stale_ome_files: bool = False,
        log_file_path: Optional[str] = None,
        # Backpressure shared values (from BackpressureController)
        bp_pending_jobs: Optional[multiprocessing.Value] = None,
        bp_pending_bytes: Optional[multiprocessing.Value] = None,
        bp_capacity_event: Optional[multiprocessing.Event] = None,
    ):
        super().__init__()
        self._log = squid.logging.get_logger(__class__.__name__)
        self._acquisition_info = acquisition_info
        self._log_file_path = log_file_path  # Will be used in subprocess to set up file logging

        self._input_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._input_timeout = 1.0
        self._output_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._shutdown_event: multiprocessing.Event = multiprocessing.Event()
        # Track jobs in flight (dispatched but not yet completed)
        self._pending_count = multiprocessing.Value("i", 0)

        # Backpressure tracking (shared with BackpressureController)
        self._bp_pending_jobs = bp_pending_jobs
        self._bp_pending_bytes = bp_pending_bytes
        self._bp_capacity_event = bp_capacity_event
        # Track accumulated bytes per well for DownsampledViewJob
        # (image stays in accumulator until well completes)
        self._well_accumulated_bytes: Dict[str, int] = {}

        # Clean up stale metadata files from previous crashed acquisitions
        # Only run when explicitly requested (i.e., when OME-TIFF saving is being used)
        if cleanup_stale_ome_files:
            removed = ome_tiff_writer.cleanup_stale_metadata_files()
            if removed:
                self._log.info(f"Cleaned up {len(removed)} stale OME-TIFF metadata files")

    def dispatch(self, job: Job):
        # Inject acquisition_info into SaveOMETiffJob instances before serialization.
        # The job object is pickled when placed in the queue, so injection must happen here.
        if isinstance(job, SaveOMETiffJob):
            if self._acquisition_info is None:
                raise ValueError(
                    "Cannot dispatch SaveOMETiffJob: JobRunner was initialized without acquisition_info. "
                    "When using OME-TIFF saving, initialize JobRunner with an AcquisitionInfo instance."
                )
            job.acquisition_info = self._acquisition_info

        # Calculate image bytes for backpressure tracking
        image_bytes = 0
        if self._bp_pending_jobs is not None:
            if job.capture_image and job.capture_image.image_array is not None:
                image_bytes = job.capture_image.image_array.nbytes

        # Increment counters BEFORE putting job in queue to prevent race condition
        # where worker processes job before counter is incremented, causing
        # has_pending() to return False while job is still in flight.
        with self._pending_count.get_lock():
            self._pending_count.value += 1
        if self._bp_pending_jobs is not None:
            with self._bp_pending_jobs.get_lock():
                self._bp_pending_jobs.value += 1
            with self._bp_pending_bytes.get_lock():
                self._bp_pending_bytes.value += image_bytes

        try:
            self._input_queue.put_nowait(job)
        except Exception as original_exc:
            # Roll back ALL counters if enqueue fails
            try:
                with self._pending_count.get_lock():
                    self._pending_count.value -= 1
                if self._bp_pending_jobs is not None:
                    with self._bp_pending_jobs.get_lock():
                        self._bp_pending_jobs.value = max(0, self._bp_pending_jobs.value - 1)
                    with self._bp_pending_bytes.get_lock():
                        self._bp_pending_bytes.value = max(0, self._bp_pending_bytes.value - image_bytes)
            except Exception as rollback_exc:
                self._log.error(
                    f"Failed to rollback counters after dispatch failure: {rollback_exc}. "
                    f"Counters may be inconsistent. Original error: {original_exc}"
                )
            raise original_exc
        return True

    def output_queue(self) -> multiprocessing.Queue:
        return self._output_queue

    def has_pending(self):
        with self._pending_count.get_lock():
            return self._pending_count.value > 0

    def shutdown(self, timeout_s=1.0):
        # Guard against double shutdown
        if self._shutdown_event is None:
            return
        self._shutdown_event.set()
        self.join(timeout=timeout_s)
        # If process is still alive after timeout, terminate it
        if self.is_alive():
            self.terminate()
            self.join(timeout=1.0)
        # Clean up multiprocessing primitives to avoid semaphore leaks
        self._input_queue.close()
        self._input_queue.join_thread()
        self._output_queue.close()
        self._output_queue.join_thread()
        # Clear references to allow garbage collection of Event and Value semaphores
        self._input_queue = None
        self._output_queue = None
        self._shutdown_event = None
        self._pending_count = None

    def run(self):
        import logging

        # Configure logging in subprocess - the squid.logging module sets up console logging
        # on import, but we need to ensure it's properly initialized in this process.
        # Default to INFO for stdout in the worker, and allow overriding via
        # the SQUID_WORKER_LOG_LEVEL environment variable (e.g. "DEBUG").
        stdout_level = logging.INFO
        env_level = os.environ.get("SQUID_WORKER_LOG_LEVEL")
        if env_level:
            env_level_upper = env_level.upper()
            if hasattr(logging, env_level_upper):
                stdout_level = getattr(logging, env_level_upper)
        squid.logging.set_stdout_log_level(stdout_level)

        # Set up file logging if a log file path was provided
        # Use a separate file for the worker to avoid multiprocess file write conflicts
        worker_log_path = None
        if self._log_file_path:
            base, ext = os.path.splitext(self._log_file_path)
            worker_log_path = f"{base}_worker{ext}"
            squid.logging.add_file_handler(worker_log_path, replace_existing=True, level=logging.DEBUG)

        self._log = squid.logging.get_logger(self.__class__.__name__)
        worker_log_msg = f", worker_log={worker_log_path}" if worker_log_path else ""
        self._log.info(f"JobRunner subprocess started (PID={os.getpid()}{worker_log_msg})")

        # Clear any stale tracking from previous acquisition (defensive)
        self._well_accumulated_bytes = {}

        # Start memory monitoring for the worker process
        start_worker_monitoring(sample_interval_ms=200)
        log_memory("WORKER_START", include_children=False)

        while not self._shutdown_event.is_set():
            job = None
            try:
                t_wait_start = time.perf_counter()
                job = self._input_queue.get(timeout=self._input_timeout)
                t_got_job = time.perf_counter()

                self._log.info(f"Running job {job.job_id} (waited {(t_got_job - t_wait_start)*1000:.1f}ms in queue)...")

                # Set operation context for memory tracking
                if isinstance(job, DownsampledViewJob):
                    set_worker_operation(f"DOWNSAMPLE_{job.well_id}")
                else:
                    set_worker_operation(job.__class__.__name__)

                t_run_start = time.perf_counter()
                result = job.run()
                t_run_end = time.perf_counter()

                # Only queue non-None results (DownsampledViewJob returns None for intermediate FOVs)
                if result is not None:
                    self._log.info(
                        f"Job {job.job_id} returned in {(t_run_end - t_run_start)*1000:.1f}ms. "
                        f"Sending result to output queue."
                    )
                    self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=result, exception=None))
                    self._log.debug(f"Result for {job.job_id} is on output queue.")
                else:
                    self._log.debug(
                        f"Job {job.job_id} returned None in {(t_run_end - t_run_start)*1000:.1f}ms, not queuing."
                    )
            except queue.Empty:
                pass
            except Exception as e:
                if job:
                    self._log.exception(f"Job {job.job_id} failed! Returning exception result.")
                    self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=None, exception=e))
            finally:
                # Clear operation context after job completes
                set_worker_operation("")
                # Decrement pending count when job completes (success, None result, or exception)
                if job is not None:
                    with self._pending_count.get_lock():
                        self._pending_count.value -= 1

                    # Backpressure tracking
                    if self._bp_pending_jobs is not None:
                        # Job count decrements for all jobs
                        with self._bp_pending_jobs.get_lock():
                            self._bp_pending_jobs.value = max(0, self._bp_pending_jobs.value - 1)

                        # Calculate image bytes
                        image_bytes = 0
                        if job.capture_image and job.capture_image.image_array is not None:
                            image_bytes = job.capture_image.image_array.nbytes
                        else:
                            self._log.debug(
                                f"Job {job.job_id} has no capture_image or image_array, byte tracking skipped"
                            )

                        # Byte tracking: DownsampledViewJob needs special handling
                        # because images are held in accumulator until well completes
                        if isinstance(job, DownsampledViewJob):
                            # Track accumulated bytes per well
                            well_id = job.well_id
                            self._well_accumulated_bytes[well_id] = (
                                self._well_accumulated_bytes.get(well_id, 0) + image_bytes
                            )

                            # Check if this is the final FOV for the well
                            # (last FOV index, last channel, last z-level)
                            # We use indices instead of checking result because:
                            # - If exception occurs, result is None but accumulator IS cleared
                            #   (DownsampledViewJob.run() has a finally block that pops the accumulator
                            #    when is_complete() returns True, regardless of success/exception)
                            # - We must decrement bytes when accumulator is cleared (final FOV)
                            is_final_fov = (
                                job.fov_index == job.total_fovs_in_well - 1
                                and job.channel_idx == job.total_channels - 1
                                and job.z_index == job.total_z_levels - 1
                            )

                            if is_final_fov:
                                # Final FOV: decrement ALL accumulated bytes for this well
                                total_well_bytes = self._well_accumulated_bytes.pop(well_id, 0)
                                with self._bp_pending_bytes.get_lock():
                                    self._bp_pending_bytes.value = max(
                                        0, self._bp_pending_bytes.value - total_well_bytes
                                    )
                            # Signal capacity available - job count decreased even for intermediate FOVs
                            if self._bp_capacity_event is not None:
                                self._bp_capacity_event.set()
                        else:
                            # Normal jobs: decrement bytes immediately
                            with self._bp_pending_bytes.get_lock():
                                self._bp_pending_bytes.value = max(0, self._bp_pending_bytes.value - image_bytes)
                            if self._bp_capacity_event is not None:
                                self._bp_capacity_event.set()
        # Stop memory monitoring and log final report
        log_memory("WORKER_SHUTDOWN", include_children=False)
        stop_worker_monitoring()
        self._log.info("Shutdown request received, exiting run.")
