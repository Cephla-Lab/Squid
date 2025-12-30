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

from dataclasses import dataclass, field
from filelock import FileLock, Timeout as FileLockTimeout

import imageio as iio
import numpy as np
import tifffile

from control import _def, utils_acquisition
import squid.abc
import squid.logging
from control.utils_config import ChannelMode
from control.core import utils_ome_tiff_writer as ome_tiff_writer


@dataclass
class AcquisitionInfo:
    total_time_points: int
    total_z_levels: int
    total_channels: int
    channel_names: List[str]
    experiment_path: Optional[str] = None
    time_increment_s: Optional[float] = None
    physical_size_z_um: Optional[float] = None
    physical_size_x_um: Optional[float] = None
    physical_size_y_um: Optional[float] = None


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


def _metadata_lock_path(metadata_path: str) -> str:
    return metadata_path + ".lock"


@contextmanager
def _acquire_file_lock(lock_path: str):
    """Acquire a file lock with timeout, providing a clear error message on failure."""
    lock = FileLock(lock_path, timeout=10)
    try:
        with lock:
            yield
    except FileLockTimeout as exc:
        raise TimeoutError(
            f"Failed to acquire file lock '{lock_path}' within 10 seconds. Another process may be holding the lock."
        ) from exc


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

    acquisition_info: Optional[AcquisitionInfo] = field(default=None)

    def run(self) -> bool:
        if self.acquisition_info is None:
            raise ValueError("SaveOMETiffJob requires acquisition_info to be set by JobRunner")
        self._save_ome_tiff(self.image_array(), self.capture_info)
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

        with _acquire_file_lock(lock_path):
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

            ome_tiff_writer.write_metadata(metadata_path, metadata)

            if metadata[ome_tiff_writer.SAVED_COUNT_KEY] >= metadata[ome_tiff_writer.EXPECTED_COUNT_KEY]:
                metadata[ome_tiff_writer.COMPLETED_KEY] = True
                ome_tiff_writer.write_metadata(metadata_path, metadata)
                with tifffile.TiffFile(output_path) as tif:
                    current_xml = tif.ome_metadata
                ome_xml = ome_tiff_writer.augment_ome_xml(current_xml, metadata)
                tifffile.tiffcomment(output_path, ome_xml.encode("utf-8"))
                if os.path.exists(metadata_path):
                    os.remove(metadata_path)
                # Note: filelock does not remove lock files; stale lock/metadata files are cleaned up
                # by ome_tiff_writer.cleanup_stale_metadata_files() on JobRunner initialization.


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


class JobRunner(multiprocessing.Process):
    def __init__(
        self,
        acquisition_info: Optional[AcquisitionInfo] = None,
        cleanup_stale_ome_files: bool = False,
    ):
        super().__init__()
        self._log = squid.logging.get_logger(__class__.__name__)
        self._acquisition_info = acquisition_info

        self._input_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._input_timeout = 1.0
        self._output_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._shutdown_event: multiprocessing.Event = multiprocessing.Event()

        # Clean up stale metadata files from previous crashed acquisitions
        # Only run when explicitly requested (i.e., when OME-TIFF saving is being used)
        if cleanup_stale_ome_files:
            removed = ome_tiff_writer.cleanup_stale_metadata_files()
            if removed:
                self._log.info(f"Cleaned up {len(removed)} stale OME-TIFF metadata files")

    def dispatch(self, job: Job):
        # Inject acquisition_info into SaveOMETiffJob instances
        if isinstance(job, SaveOMETiffJob) and self._acquisition_info is not None:
            job.acquisition_info = self._acquisition_info

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
                self._log.info(f"Job {job.job_id} returned. Sending result to output queue.")
                self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=result, exception=None))
                self._log.debug(f"Result for {job.job_id} is on output queue.")
            except queue.Empty:
                pass
            except Exception as e:
                if job:
                    self._log.exception(f"Job {job.job_id} failed! Returning exception result.")
                    self._output_queue.put_nowait(JobResult(job_id=job.job_id, result=None, exception=e))
        self._log.info("Shutdown request received, exiting run.")
