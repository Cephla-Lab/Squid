import abc
import hashlib
import multiprocessing
import queue
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
import json
from typing import Optional, Generic, TypeVar, List, Dict, Any
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - platform without fcntl
    fcntl = None

from dataclasses import dataclass, field

import imageio as iio
import numpy as np
import tifffile

from control import _def, utils_acquisition, utils
import squid.abc
import squid.logging
from control.utils_config import ChannelMode


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


def _temp_paths_for_capture(info: CaptureInfo, base_name: str) -> tuple[str, str]:
    base_identifier = info.experiment_path or info.save_directory
    key = f"{base_identifier}:{base_name}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    temp_dir = tempfile.gettempdir()
    memmap_path = os.path.join(temp_dir, f"ome_{digest}_tczyx.dat")
    metadata_path = os.path.join(temp_dir, f"ome_{digest}_metadata.json")
    return memmap_path, metadata_path


def _ome_output_folder(info: CaptureInfo) -> str:
    base_dir = info.experiment_path or os.path.dirname(info.save_directory)
    return os.path.join(base_dir, "ome_tiff")


def _load_metadata(metadata_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(metadata_path):
        return None
    with open(metadata_path, "r", encoding="utf-8") as metadata_file:
        return json.load(metadata_file)


def _write_metadata(metadata_path: str, metadata: Dict[str, Any]) -> None:
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file)


def _ome_base_name(info: CaptureInfo) -> str:
    return f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}"


def _validate_capture_info_for_ome(info: CaptureInfo, image: np.ndarray) -> None:
    if info.time_point is None:
        raise ValueError("CaptureInfo.time_point is required for OME-TIFF saving")
    if info.total_time_points is None:
        raise ValueError("CaptureInfo.total_time_points is required for OME-TIFF saving")
    if info.total_z_levels is None:
        raise ValueError("CaptureInfo.total_z_levels is required for OME-TIFF saving")
    if info.total_channels is None:
        raise ValueError("CaptureInfo.total_channels is required for OME-TIFF saving")
    if image.ndim != 2:
        raise NotImplementedError("OME-TIFF saving currently supports 2D grayscale images only")


def _initialize_metadata(info: CaptureInfo, image: np.ndarray) -> Dict[str, Any]:
    channel_names = info.channel_names or []
    return {
        "dtype": np.dtype(image.dtype).str,
        "axes": "TCZYX",
        "shape": [
            int(info.total_time_points),
            int(info.total_channels),
            int(info.total_z_levels),
            int(image.shape[-2]),
            int(image.shape[-1]),
        ],
        "channel_names": channel_names,
        "written_indices": [],
        "saved_count": 0,
        "expected_count": int(info.total_time_points) * int(info.total_z_levels) * int(info.total_channels),
        "planes": {},
        "start_time": info.capture_time,
        "completed": False,
    }


def _update_plane_metadata(metadata: Dict[str, Any], info: CaptureInfo) -> Dict[str, Any]:
    plane_key = f"{info.time_point}-{info.configuration_idx}-{info.z_index}"
    plane_data = {
        "TheT": int(info.time_point),
        "TheZ": int(info.z_index),
        "TheC": int(info.configuration_idx),
    }
    if info.position is not None:
        if getattr(info.position, "x_mm", None) is not None:
            plane_data["PositionX"] = float(info.position.x_mm)
        if getattr(info.position, "y_mm", None) is not None:
            plane_data["PositionY"] = float(info.position.y_mm)
        if getattr(info.position, "z_mm", None) is not None:
            plane_data["PositionZ"] = float(info.position.z_mm)
    if metadata.get("start_time") is not None and info.capture_time is not None:
        plane_data["DeltaT"] = float(info.capture_time - metadata["start_time"])
    if info.z_piezo_um is not None:
        plane_data["PositionZPiezo"] = float(info.z_piezo_um)
    metadata.setdefault("planes", {})[plane_key] = plane_data
    return metadata


def _build_ome_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    channel_names = metadata.get("channel_names") or []
    ome_metadata: Dict[str, Any] = {
        "axes": "TCZYX",
        "Channel": [{"Name": name} for name in channel_names] if channel_names else [],
    }

    planes = metadata.get("planes", {})
    if planes:
        ome_metadata["Plane"] = sorted(
            planes.values(),
            key=lambda plane: (plane.get("TheT", 0), plane.get("TheC", 0), plane.get("TheZ", 0)),
        )

    return ome_metadata


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
        _validate_capture_info_for_ome(info, image)

        ome_folder = _ome_output_folder(info)
        utils.ensure_directory_exists(ome_folder)

        base_name = _ome_base_name(info)
        output_path = os.path.join(ome_folder, base_name + "_stack.ome.tiff")
        memmap_path, metadata_path = _temp_paths_for_capture(info, base_name)
        lock_path = _metadata_lock_path(metadata_path)

        with _acquire_file_lock(lock_path):
            metadata = _load_metadata(metadata_path)
            if metadata is None:
                metadata = _initialize_metadata(info, image)
                mode = "w+"
            else:
                expected_shape = tuple(metadata["shape"])
                if expected_shape[-2:] != image.shape[-2:]:
                    raise ValueError("Image dimensions do not match existing OME memmap stack")
                if not metadata.get("channel_names") and info.channel_names:
                    metadata["channel_names"] = info.channel_names
                mode = "r+"
            target_dtype = np.dtype(metadata["dtype"])
            image_to_store = image if image.dtype == target_dtype else image.astype(target_dtype)

            time_point = int(info.time_point)
            z_index = int(info.z_index)
            channel_index = int(info.configuration_idx)
            shape = tuple(metadata["shape"])
            if not (0 <= time_point < shape[0]):
                raise ValueError("Time point index out of range for OME stack")
            if not (0 <= channel_index < shape[1]):
                raise ValueError("Channel index out of range for OME stack")
            if not (0 <= z_index < shape[2]):
                raise ValueError("Z index out of range for OME stack")

            stack = np.memmap(memmap_path, dtype=target_dtype, mode=mode, shape=shape)
            try:
                stack[time_point, channel_index, z_index, :, :] = image_to_store
                stack.flush()
            finally:
                del stack

            metadata = _update_plane_metadata(metadata, info)
            index_key = f"{time_point}-{channel_index}-{z_index}"
            if index_key not in metadata["written_indices"]:
                metadata["written_indices"].append(index_key)
                metadata["saved_count"] = len(metadata["written_indices"])

            _write_metadata(metadata_path, metadata)

            if metadata["saved_count"] >= metadata["expected_count"]:
                metadata["completed"] = True
                _write_metadata(metadata_path, metadata)
                stack = np.memmap(memmap_path, dtype=target_dtype, mode="r", shape=shape)
                try:
                    tczyx_view = np.asarray(stack)
                    ome_metadata = _build_ome_metadata(metadata)
                    tifffile.imwrite(
                        output_path,
                        tczyx_view,
                        ome=True,
                        metadata=ome_metadata,
                    )
                finally:
                    del stack
                if os.path.exists(memmap_path):
                    os.remove(memmap_path)
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
