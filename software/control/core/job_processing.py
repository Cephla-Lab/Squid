import abc
import multiprocessing
import queue
import os
import time
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Generic, TypeVar, List, Dict, Any
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - platform without fcntl
    fcntl = None

from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Optional, Tuple, Union

import imageio as iio
import numpy as np
import tifffile

from control import _def, utils_acquisition
from control._def import ZProjectionMode
import squid.abc
import squid.logging
from control.utils_config import ChannelMode
from . import utils_ome_tiff_writer as ome_tiff_writer
from .downsampled_views import (
    crop_overlap,
    downsample_tile,
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
    total_time_points: Optional[int] = None
    total_z_levels: Optional[int] = None
    total_channels: Optional[int] = None
    channel_names: Optional[List[str]] = None
    experiment_path: Optional[str] = None
    time_increment_s: Optional[float] = None
    physical_size_z_um: Optional[float] = None
    physical_size_x_um: Optional[float] = None
    physical_size_y_um: Optional[float] = None


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


def _metadata_lock_path(metadata_path: str) -> str:
    return metadata_path + ".lock"


@contextmanager
def _acquire_file_lock(lock_path: str):
    lock_file = open(lock_path, "w")
    try:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


class SaveImageJob(Job):
    def run(self) -> bool:
        is_color = len(self.image_array().shape) > 2
        return self.save_image(self.image_array(), self.capture_info, is_color)

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
        elif _def.FILE_SAVING_OPTION == _def.FileSavingOption.OME_TIFF:
            self._save_ome_tiff(image, info)
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

    def _save_ome_tiff(self, image: np.ndarray, info: CaptureInfo) -> None:
        # with reference to Talley's https://github.com/pymmcore-plus/pymmcore-plus/blob/main/src/pymmcore_plus/mda/handlers/_ome_tiff_writer.py and Christoph's https://forum.image.sc/t/how-to-create-an-image-series-ome-tiff-from-python/42730/7
        ome_tiff_writer.validate_capture_info(info, image)

        ome_folder = ome_tiff_writer.ome_output_folder(info)
        ome_tiff_writer.ensure_output_directory(ome_folder)

        base_name = ome_tiff_writer.ome_base_name(info)
        output_path = os.path.join(ome_folder, base_name + ".ome.tiff")
        metadata_path = ome_tiff_writer.metadata_temp_path(info, base_name)
        lock_path = _metadata_lock_path(metadata_path)

        with _acquire_file_lock(lock_path):
            metadata = ome_tiff_writer.load_metadata(metadata_path)
            if metadata is None:
                metadata = ome_tiff_writer.initialize_metadata(info, image)
                target_dtype = np.dtype(metadata["dtype"])
                if os.path.exists(output_path):
                    os.remove(output_path)
                tifffile.imwrite(
                    output_path,
                    shape=tuple(metadata["shape"]),
                    dtype=target_dtype,
                    metadata=ome_tiff_writer.metadata_for_imwrite(metadata),
                    ome=True,
                )
            else:
                expected_shape = tuple(metadata["shape"])
                if expected_shape[-2:] != image.shape[-2:]:
                    raise ValueError("Image dimensions do not match existing OME memmap stack")
                if not metadata.get("channel_names") and info.channel_names:
                    metadata["channel_names"] = info.channel_names

            target_dtype = np.dtype(metadata["dtype"])
            image_to_store = image if image.dtype == target_dtype else image.astype(target_dtype)

            time_point = int(info.time_point)
            z_index = int(info.z_index)
            channel_index = int(info.configuration_idx)
            shape = tuple(metadata["shape"])
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
            if index_key not in metadata["written_indices"]:
                metadata["written_indices"].append(index_key)
                metadata["saved_count"] = len(metadata["written_indices"])

            ome_tiff_writer.write_metadata(metadata_path, metadata)

            if metadata["saved_count"] >= metadata["expected_count"]:
                metadata["completed"] = True
                ome_tiff_writer.write_metadata(metadata_path, metadata)
                with tifffile.TiffFile(output_path) as tif:
                    current_xml = tif.ome_metadata
                ome_xml = ome_tiff_writer.augment_ome_xml(current_xml, metadata)
                tifffile.tiffcomment(output_path, ome_xml.encode("utf-8"))
                if os.path.exists(metadata_path):
                    os.remove(metadata_path)

        if os.path.exists(lock_path):
            os.remove(lock_path)


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

        # Crop overlap from tile
        tile = self.image_array()
        cropped = crop_overlap(tile, self.overlap_pixels)

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

        # If not all FOVs for all channels received yet, return None
        if not accumulator.is_complete():
            z_info = f" z {self.z_index + 1}/{self.total_z_levels}" if self.total_z_levels > 1 else ""
            log.debug(
                f"Well {self.well_id}: channel {self.channel_idx} FOV {self.fov_index + 1}/{self.total_fovs_in_well}{z_info}, "
                f"channels: {accumulator.get_channel_count()}/{self.total_channels}"
            )
            return None

        # All FOVs for all channels (and z-levels for MIP) received - stitch and save
        z_info = f" x {self.total_z_levels} z-levels ({self.z_projection_mode})" if self.total_z_levels > 1 else ""
        log.info(
            f"Well {self.well_id}: all {self.total_fovs_in_well} FOVs x {self.total_channels} channels{z_info} received, stitching..."
        )

        try:
            # Stitch all channels
            stitched_channels = accumulator.stitch_all_channels()

            # Get channel names for metadata
            channel_names = accumulator.channel_names

            # Generate plate view images first (at plate resolution only)
            well_images_for_plate: Dict[int, np.ndarray] = {}
            for ch_idx in sorted(stitched_channels.keys()):
                downsampled = downsample_tile(stitched_channels[ch_idx], self.pixel_size_um, self.plate_resolution_um)
                well_images_for_plate[ch_idx] = downsampled

            # Save TIFFs only if not skipping
            if not self.skip_saving:
                wells_dir = os.path.join(self.output_dir, "wells")
                os.makedirs(wells_dir, exist_ok=True)

                for resolution in self.target_resolutions_um:
                    # Downsample each channel
                    downsampled_stack = []
                    for ch_idx in sorted(stitched_channels.keys()):
                        if resolution == self.plate_resolution_um:
                            # Reuse already computed plate resolution
                            downsampled_stack.append(well_images_for_plate[ch_idx])
                        else:
                            downsampled = downsample_tile(stitched_channels[ch_idx], self.pixel_size_um, resolution)
                            downsampled_stack.append(downsampled)

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
    def __init__(self):
        super().__init__()
        self._log = squid.logging.get_logger(__class__.__name__)

        self._input_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._input_timeout = 1.0
        self._output_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._shutdown_event: multiprocessing.Event = multiprocessing.Event()

    def dispatch(self, job: Job):
        self._input_queue.put_nowait(job)

        return True

    def output_queue(self) -> multiprocessing.Queue:
        return self._output_queue

    def has_pending(self):
        return not self._input_queue.empty()

    def shutdown(self, timeout_s=1.0):
        self._shutdown_event.set()
        self.join(timeout=timeout_s)

    def run(self):
        while not self._shutdown_event.is_set():
            job = None
            try:
                job = self._input_queue.get(timeout=self._input_timeout)
                self._log.info(f"Running job {job.job_id}...")
                result = job.run()
                # Only queue non-None results (DownsampledViewJob returns None for intermediate FOVs)
                if result is not None:
                    self._log.info(f"Job {job.job_id} returned. Sending result to output queue.")
                    self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=result, exception=None))
                    self._log.debug(f"Result for {job.job_id} is on output queue.")
                else:
                    self._log.debug(f"Job {job.job_id} returned None, not queuing.")
            except queue.Empty:
                pass
            except Exception as e:
                if job:
                    self._log.exception(f"Job {job.job_id} failed! Returning exception result.")
                    self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=None, exception=e))
        self._log.info("Shutdown request received, exiting run.")
