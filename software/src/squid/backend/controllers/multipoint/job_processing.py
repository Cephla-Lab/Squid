import abc
import multiprocessing
import queue
import os
import time
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Generic, TypeVar, List, Dict, Any, Set, Tuple, ClassVar, Union
from uuid import uuid4

from dataclasses import dataclass, field
from filelock import FileLock, Timeout as FileLockTimeout
import imageio

import numpy as np
import tifffile

import _def
from _def import ZProjectionMode, DownsamplingMethod
from squid.backend.io import utils_acquisition
import squid.core.abc
import squid.core.logging
from squid.core.config.feature_flags import get_feature_flags
from squid.core.config.models import AcquisitionChannel
from squid.backend.io.writers import utils_ome_tiff_writer as ome_tiff_writer
from squid.backend.controllers.multipoint.downsampled_views import (
    crop_overlap,
    downsample_tile,
    WellTileAccumulator,
)

_log = squid.core.logging.get_logger(__name__)
_feature_flags = get_feature_flags()

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


# NOTE(imo): We want this to be fast.  But pydantic does not support numpy serialization natively, which means
# that we need a custom serializer (which will be slow!).  So, use dataclass here instead.
@dataclass
class CaptureInfo:
    """Per-image metadata for acquisition jobs.

    Contains position, timing, and identification info specific to each captured image.
    Acquisition-wide metadata (totals, channel names, physical sizes) is now in AcquisitionInfo.
    """

    position: squid.core.abc.Pos
    z_index: int
    capture_time: float
    configuration: AcquisitionChannel
    save_directory: str
    file_id: str
    region_id: int
    fov: int
    configuration_idx: int
    z_piezo_um: Optional[float] = None
    time_point: Optional[int] = None
    pixel_size_um: Optional[float] = None  # Per-tile pixel size for mosaic display
    fov_id: Optional[str] = None  # Stable FOV identifier (e.g., "A1_0001")


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
def _acquire_file_lock(lock_path: str, context: str = "") -> Any:
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


def _merged_image_path(info: "CaptureInfo", image: np.ndarray) -> str:
    if image.dtype == np.uint16:
        ext = ".tiff"
    else:
        ext = "." + _def.Acquisition.IMAGE_FORMAT
    return os.path.join(info.save_directory, f"{info.file_id}_merged{ext}")


def _merged_lock_path(output_path: str) -> str:
    return output_path + ".lock"


def _prepare_merge_image(image: np.ndarray, info: "CaptureInfo") -> np.ndarray:
    if image.ndim == 2:
        return utils_acquisition.return_pseudo_colored_image(image, info.configuration)
    if image.ndim == 3 and image.shape[2] == 1:
        return utils_acquisition.return_pseudo_colored_image(
            image[:, :, 0], info.configuration
        )
    if image.ndim == 3 and image.shape[2] == 3:
        return image
    return utils_acquisition.return_pseudo_colored_image(image.squeeze(), info.configuration)


def _read_merged_image(path: str) -> np.ndarray:
    if path.lower().endswith((".tif", ".tiff")):
        return tifffile.imread(path)
    return imageio.imread(path)


def _write_merged_image(path: str, image: np.ndarray) -> None:
    if path.lower().endswith((".tif", ".tiff")):
        tifffile.imwrite(path, image)
    else:
        imageio.imwrite(path, image)


class SaveImageJob(Job):
    def run(self) -> bool:
        from squid.backend.io.io_simulation import is_simulation_enabled, simulated_tiff_write

        image = self.image_array()

        # Simulated disk I/O mode - encode to buffer, throttle, discard
        if is_simulation_enabled():
            bytes_written = simulated_tiff_write(image)
            _log.debug(
                f"SaveImageJob {self.job_id}: simulated write of {bytes_written} bytes "
                f"(image shape={image.shape})"
            )
            return True

        is_color: bool = len(image.shape) > 2
        return self.save_image(image, self.capture_info, is_color)

    def save_image(self, image: np.ndarray, info: CaptureInfo, is_color: bool) -> bool:
        # NOTE(imo): We silently fall back to individual image saving here.  We should warn or do something.
        if _def.FILE_SAVING_OPTION == _def.FileSavingOption.MULTI_PAGE_TIFF:
            metadata: Dict[str, Any] = {
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
                metadata["time"] = datetime.fromtimestamp(info.capture_time).strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                )
            except Exception:
                metadata["time"] = info.capture_time
            if info.z_piezo_um is not None:
                metadata["z_piezo (um)"] = info.z_piezo_um
            output_path: str = os.path.join(
                info.save_directory,
                f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}_stack.tiff",
            )
            # Ensure channel information is preserved across common TIFF readers by:
            # - embedding full metadata as JSON in ImageDescription (description=)
            # - setting PageName (tag 285) to the channel name via extratags
            description: str = json.dumps(metadata)
            page_name: str = str(info.configuration.name)

            # extratags format: (code, dtype, count, value, writeonce)
            # PageName (285) expects ASCII; dtype 's' denotes a null-terminated string in tifffile
            extratags: List[Tuple[int, str, int, str, bool]] = [
                (285, "s", 0, page_name, False)
            ]

            with tifffile.TiffWriter(output_path, append=True) as tiff_writer:
                tiff_writer.write(
                    image,
                    metadata=metadata,
                    description=description,
                    extratags=extratags,
                )
        else:
            # OME-TIFF is handled by SaveOMETiffJob, which requires AcquisitionInfo
            utils_acquisition.save_image(
                image=image,
                file_id=info.file_id,
                save_directory=info.save_directory,
                config=info.configuration,
                is_color=is_color,
            )

            if _feature_flags.is_enabled("MERGE_CHANNELS"):
                try:
                    self._merge_channels(image, info)
                except Exception:
                    _log.exception("Failed to merge channels for %s", info.file_id)

        return True

    def _merge_channels(self, image: np.ndarray, info: CaptureInfo) -> None:
        merged_path = _merged_image_path(info, image)
        merged_image = _prepare_merge_image(image, info)
        lock_path = _merged_lock_path(merged_path)

        with _acquire_file_lock(lock_path, context=merged_path):
            if os.path.exists(merged_path):
                existing = _read_merged_image(merged_path)
                if existing.shape != merged_image.shape:
                    _log.warning(
                        "Merged image shape mismatch for %s (existing=%s, new=%s); overwriting",
                        merged_path,
                        existing.shape,
                        merged_image.shape,
                    )
                    combined = merged_image
                else:
                    target_dtype = existing.dtype
                    max_value = (
                        np.iinfo(target_dtype).max
                        if np.issubdtype(target_dtype, np.integer)
                        else np.finfo(target_dtype).max
                    )
                    combined = existing.astype(np.float32) + merged_image.astype(
                        np.float32
                    )
                    combined = np.clip(combined, 0, max_value).astype(target_dtype)
            else:
                combined = merged_image

            _write_merged_image(merged_path, combined)


@dataclass
class SaveOMETiffJob(Job):
    """Job for saving images to OME-TIFF format.

    The acquisition_info field is injected by JobRunner.dispatch() before the job runs.
    """

    acquisition_info: Optional[AcquisitionInfo] = field(default=None)

    def run(self) -> bool:
        if self.acquisition_info is None:
            raise ValueError(
                "SaveOMETiffJob.run() requires acquisition_info but it is None. "
                "This job must be dispatched via JobRunner.dispatch(), which injects acquisition_info. "
                "If running directly, set job.acquisition_info before calling run()."
            )

        from squid.backend.io.io_simulation import is_simulation_enabled, simulated_ome_tiff_write

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
            _log.debug(
                f"SaveOMETiffJob {self.job_id}: simulated write of {bytes_written} bytes "
                f"(image shape={image.shape})"
            )
            return True

        self._save_ome_tiff(image, self.capture_info)
        return True

    def _save_ome_tiff(self, image: np.ndarray, info: CaptureInfo) -> None:
        # with reference to Talley's https://github.com/pymmcore-plus/pymmcore-plus/blob/main/src/pymmcore_plus/mda/handlers/_ome_tiff_writer.py
        # and Christoph's https://forum.image.sc/t/how-to-create-an-image-series-ome-tiff-from-python/42730/7
        ome_tiff_writer.validate_capture_info(info, self.acquisition_info, image)

        ome_folder: str = ome_tiff_writer.ome_output_folder(self.acquisition_info, info)
        ome_tiff_writer.ensure_output_directory(ome_folder)

        base_name: str = ome_tiff_writer.ome_base_name(info)
        output_path: str = os.path.join(ome_folder, base_name + ".ome.tiff")
        metadata_path: str = ome_tiff_writer.metadata_temp_path(self.acquisition_info, info, base_name)
        lock_path: str = _metadata_lock_path(metadata_path)

        with _acquire_file_lock(lock_path, context=output_path):
            metadata: Optional[Dict[str, Any]] = ome_tiff_writer.load_metadata(
                metadata_path
            )
            if metadata is None:
                metadata = ome_tiff_writer.initialize_metadata(self.acquisition_info, info, image)
                target_dtype: np.dtype = np.dtype(metadata[ome_tiff_writer.DTYPE_KEY])
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
                expected_shape: Tuple[int, ...] = tuple(metadata[ome_tiff_writer.SHAPE_KEY])
                if expected_shape[-2:] != image.shape[-2:]:
                    raise ValueError(
                        "Image dimensions do not match existing OME memmap stack"
                    )
                # acquisition_info is guaranteed non-None here (validated in run())
                if not metadata.get(ome_tiff_writer.CHANNEL_NAMES_KEY) and self.acquisition_info.channel_names:
                    metadata[ome_tiff_writer.CHANNEL_NAMES_KEY] = self.acquisition_info.channel_names

            target_dtype: np.dtype = np.dtype(metadata[ome_tiff_writer.DTYPE_KEY])
            image_to_store: np.ndarray = (
                image if image.dtype == target_dtype else image.astype(target_dtype)
            )

            time_point: int = int(info.time_point)
            z_index: int = int(info.z_index)
            channel_index: int = int(info.configuration_idx)
            shape: Tuple[int, ...] = tuple(metadata[ome_tiff_writer.SHAPE_KEY])
            if not (0 <= time_point < shape[0]):
                raise ValueError("Time point index out of range for OME stack")
            if not (0 <= z_index < shape[1]):
                raise ValueError("Z index out of range for OME stack")
            if not (0 <= channel_index < shape[2]):
                raise ValueError("Channel index out of range for OME stack")

            stack: np.ndarray = tifffile.memmap(
                output_path, dtype=target_dtype, mode="r+"
            )
            if stack.shape != shape:
                stack.shape = shape
            try:
                stack[time_point, z_index, channel_index, :, :] = image_to_store
                stack.flush()
            finally:
                del stack

            metadata = ome_tiff_writer.update_plane_metadata(metadata, info)
            index_key: str = f"{time_point}-{channel_index}-{z_index}"
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
                    current_xml: str = tif.ome_metadata
                ome_xml: str = ome_tiff_writer.augment_ome_xml(current_xml, metadata)
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


@dataclass
class ZarrWriterInfo:
    """Info for Zarr v3 saving, injected by JobRunner.

    Output path depends on acquisition mode:
    - HCS mode: {base_path}/plate.ome.zarr/{row}/{col}/{fov}/0  (5D per FOV, OME-NGFF compliant)
    - Non-HCS default: {base_path}/zarr/{region_id}/fov_{n}.ome.zarr  (5D per FOV, OME-NGFF compliant)
    - Non-HCS 6D: {base_path}/zarr/{region_id}/acquisition.zarr  (6D with FOV dimension, non-standard)

    Attributes:
        base_path: Base path for zarr outputs (e.g., experiment_path)
        t_size: Total time points
        c_size: Total channels
        z_size: Total z levels
        is_hcs: True for wellplate (HCS) acquisitions
        use_6d_fov: Use 6D (FOV, T, C, Z, Y, X) instead of per-FOV files (non-standard)
        region_fov_counts: Map of region_id -> num_fovs (for 6D shape calculation)
        pixel_size_um: Physical pixel size in micrometers
        z_step_um: Z step size in micrometers (optional)
        time_increment_s: Time between timepoints in seconds (optional)
        channel_names: List of channel names for metadata
        channel_colors: List of hex colors for channels (e.g., "#FF0000")
        channel_wavelengths: List of wavelengths in nm (None for brightfield)
    """

    base_path: str
    t_size: int
    c_size: int
    z_size: int
    is_hcs: bool = False
    use_6d_fov: bool = False
    region_fov_counts: Dict[str, int] = field(default_factory=dict)
    pixel_size_um: Optional[float] = None
    z_step_um: Optional[float] = None
    time_increment_s: Optional[float] = None
    channel_names: List[str] = field(default_factory=list)
    channel_colors: List[str] = field(default_factory=list)
    channel_wavelengths: List[Optional[int]] = field(default_factory=list)

    def get_output_path(self, region_id: str, fov: int) -> str:
        """Get output path for writing (array path).

        HCS mode: {base}/plate.ome.zarr/{row}/{col}/{fov}/0  (array at resolution level 0)
        Non-HCS per-FOV: {base}/zarr/{region_id}/fov_{n}.ome.zarr/0  (array at resolution level 0)
        Non-HCS 6D: {base}/zarr/{region_id}/acquisition.zarr  (6D with FOV dimension)
        """
        from squid.backend.io.writers.zarr_writer import (
            build_hcs_zarr_fov_path,
            build_per_fov_zarr_path,
            build_6d_zarr_path,
        )

        if self.is_hcs:
            # build_hcs_zarr_fov_path returns group path; append /0 for array
            group_path = build_hcs_zarr_fov_path(self.base_path, region_id, fov)
            return os.path.join(group_path, "0")
        elif self.use_6d_fov:
            return build_6d_zarr_path(self.base_path, region_id)
        else:
            # build_per_fov_zarr_path returns group path; append /0 for array
            group_path = build_per_fov_zarr_path(self.base_path, region_id, fov)
            return os.path.join(group_path, "0")

    def get_fov_count(self, region_id: str) -> int:
        """Get total FOV count for a region (for 6D shape calculation)."""
        return self.region_fov_counts.get(str(region_id), 1)

    def get_plate_path(self) -> str:
        """Get path to plate.ome.zarr directory (HCS mode only)."""
        return os.path.join(self.base_path, "plate.ome.zarr")

    def get_well_path(self, well_id: str) -> str:
        """Get path to well directory (HCS mode only)."""
        from squid.backend.io.writers.zarr_writer import parse_well_id

        row_letter, col_num = parse_well_id(well_id)
        return os.path.join(self.base_path, "plate.ome.zarr", row_letter, col_num)

    def get_hcs_structure(self) -> Tuple[List[str], List[int], List[Tuple[str, int]]]:
        """Extract HCS structure from region_fov_counts.

        Returns:
            Tuple of (rows, cols, wells) where:
            - rows: sorted unique row letters (e.g., ["A", "B", "C"])
            - cols: sorted unique column numbers (e.g., [1, 2, 3])
            - wells: list of (row, col) tuples for all wells
        """
        from squid.backend.io.writers.zarr_writer import parse_well_id

        rows_set: Set[str] = set()
        cols_set: Set[int] = set()
        wells: List[Tuple[str, int]] = []

        for well_id in self.region_fov_counts.keys():
            row_letter, col_num = parse_well_id(well_id)
            rows_set.add(row_letter)
            cols_set.add(int(col_num))
            wells.append((row_letter, int(col_num)))

        rows = sorted(rows_set)
        cols = sorted(cols_set)
        return rows, cols, wells


@dataclass
class ZarrWriteResult:
    """Result from a SaveZarrJob, containing frame info for viewer notification."""

    fov: int
    time_point: int
    z_index: int
    channel_name: str
    region_idx: int = 0


@dataclass
class SaveZarrJob(Job):
    """Job for saving images to Zarr v3 format using TensorStore.

    Uses a process-local ZarrWriter that is initialized lazily on first write.
    The zarr_writer_info field is injected by JobRunner.dispatch() before the job runs.
    """

    _log: ClassVar = squid.core.logging.get_logger("SaveZarrJob")
    zarr_writer_info: Optional[ZarrWriterInfo] = field(default=None)

    # Class-level writer storage keyed by output_path.
    # SAFETY: JobRunner runs in a multiprocessing.Process (not threads), so each
    # worker process has its own independent copy of this class variable.
    _zarr_writers: ClassVar[Dict[str, Any]] = {}

    # Track HCS metadata that has been written (plate path -> True, well path -> True)
    _hcs_plate_written: ClassVar[Set[str]] = set()
    _hcs_wells_written: ClassVar[Set[str]] = set()

    @classmethod
    def clear_writers(cls) -> None:
        """Clear all zarr writers, aborting any that are still active.

        Call at start of new acquisition to ensure clean state.
        """
        for writer in list(cls._zarr_writers.values()):
            if writer.is_initialized and not writer.is_finalized:
                try:
                    writer.abort()
                except Exception as e:
                    cls._log.warning(f"Error aborting writer during clear: {e}")
        cls._zarr_writers.clear()
        cls._hcs_plate_written.clear()
        cls._hcs_wells_written.clear()

    @classmethod
    def finalize_all_writers(cls) -> bool:
        """Finalize all active zarr writers.

        Call at end of acquisition to ensure all data is written.

        Returns:
            True if all writers finalized successfully, False if any failed.
        """
        failed_paths = []
        for path, writer in list(cls._zarr_writers.items()):
            if writer.is_initialized and not writer.is_finalized:
                try:
                    writer.finalize()
                    cls._log.info(f"Finalized zarr writer: {path}")
                except Exception as e:
                    cls._log.error(f"Error finalizing writer {path}: {e}")
                    failed_paths.append(path)
        cls._zarr_writers.clear()
        if failed_paths:
            cls._log.error(f"Failed to finalize {len(failed_paths)} zarr writers: {failed_paths}")
            return False
        return True

    def _write_hcs_metadata_if_needed(self, region_id: str, fov: int) -> None:
        """Write HCS plate and well metadata if not already written."""
        from squid.backend.io.writers.zarr_writer import write_plate_metadata, write_well_metadata

        info = self.zarr_writer_info

        # Write plate metadata (once per acquisition)
        plate_path = info.get_plate_path()
        if plate_path not in self._hcs_plate_written:
            rows, cols, wells = info.get_hcs_structure()
            write_plate_metadata(plate_path, rows, cols, wells, plate_name="plate")
            self._hcs_plate_written.add(plate_path)
            self._log.info(f"Wrote HCS plate metadata: {len(wells)} wells")

        # Write well metadata (once per well)
        well_path = info.get_well_path(region_id)
        if well_path not in self._hcs_wells_written:
            fov_count = info.get_fov_count(region_id)
            fields = list(range(fov_count))
            write_well_metadata(well_path, fields)
            self._hcs_wells_written.add(well_path)
            self._log.debug(f"Wrote HCS well metadata for {region_id}: {fov_count} fields")

    def run(self) -> ZarrWriteResult:
        if self.zarr_writer_info is None:
            raise ValueError(
                "SaveZarrJob.run() requires zarr_writer_info but it is None. "
                "This job must be dispatched via JobRunner.dispatch(), which injects zarr_writer_info. "
                "If running directly, set job.zarr_writer_info before calling run()."
            )

        from squid.backend.io.io_simulation import is_simulation_enabled, simulated_zarr_write

        image = self.image_array()
        info = self.capture_info

        # Get per-region/FOV output path to avoid overwriting between FOVs
        region_id = str(info.region_id) if info.region_id is not None else "0"
        fov = info.fov if info.fov is not None else 0
        output_path = self.zarr_writer_info.get_output_path(region_id, fov)

        # Build result with frame info for viewer notification
        region_names = list(self.zarr_writer_info.region_fov_counts.keys())
        result = ZarrWriteResult(
            fov=fov,
            time_point=info.time_point or 0,
            z_index=info.z_index,
            channel_name=info.configuration.name,
            region_idx=region_names.index(region_id) if region_id in region_names else 0,
        )

        # Determine shape based on acquisition mode
        is_hcs = self.zarr_writer_info.is_hcs
        use_6d_fov = self.zarr_writer_info.use_6d_fov
        if is_hcs or not use_6d_fov:
            # 5D shape: (T, C, Z, Y, X) - one writer per FOV
            shape = (
                self.zarr_writer_info.t_size,
                self.zarr_writer_info.c_size,
                self.zarr_writer_info.z_size,
                image.shape[0],
                image.shape[1],
            )
        else:
            # 6D shape: (FOV, T, C, Z, Y, X) - FOV first for contiguous per-FOV data
            fov_count = self.zarr_writer_info.get_fov_count(region_id)
            shape = (
                fov_count,
                self.zarr_writer_info.t_size,
                self.zarr_writer_info.c_size,
                self.zarr_writer_info.z_size,
                image.shape[0],
                image.shape[1],
            )

        # Simulated disk I/O mode
        if is_simulation_enabled():
            bytes_written = simulated_zarr_write(
                image=image,
                stack_key=output_path,
                shape=shape,
                time_point=info.time_point or 0,
                z_index=info.z_index,
                channel_index=info.configuration_idx,
            )
            self._log.debug(
                f"SaveZarrJob {self.job_id}: simulated write of {bytes_written} bytes "
                f"to {output_path} (image shape={image.shape})"
            )
            return result

        self._save_zarr(image, info, output_path)
        return result

    def _save_zarr(self, image: np.ndarray, info: CaptureInfo, output_path: str) -> None:
        """Write image to zarr dataset using TensorStore."""
        from squid.backend.io.writers.zarr_writer import ZarrWriter, ZarrAcquisitionConfig

        is_hcs = self.zarr_writer_info.is_hcs
        use_6d_fov = self.zarr_writer_info.use_6d_fov
        region_id = str(info.region_id) if info.region_id is not None else "0"
        fov = info.fov if info.fov is not None else 0

        # Key logic:
        # - HCS: unique per (region, fov) via output_path
        # - Non-HCS 6D: shared per region (all FOVs in one 6D array)
        # - Non-HCS default: unique per (region, fov) via output_path
        if not is_hcs and use_6d_fov:
            writer_key = f"{self.zarr_writer_info.base_path}:{region_id}"
        else:
            writer_key = output_path  # Unique per FOV

        if writer_key not in self._zarr_writers:
            if is_hcs or not use_6d_fov:
                # 5D shape: (T, C, Z, Y, X) - one writer per FOV
                shape = (
                    self.zarr_writer_info.t_size,
                    self.zarr_writer_info.c_size,
                    self.zarr_writer_info.z_size,
                    image.shape[0],
                    image.shape[1],
                )
            else:
                # 6D shape: (FOV, T, C, Z, Y, X)
                fov_count = self.zarr_writer_info.get_fov_count(region_id)
                shape = (
                    fov_count,
                    self.zarr_writer_info.t_size,
                    self.zarr_writer_info.c_size,
                    self.zarr_writer_info.z_size,
                    image.shape[0],
                    image.shape[1],
                )

            config = ZarrAcquisitionConfig(
                output_path=output_path,
                shape=shape,
                dtype=image.dtype,
                pixel_size_um=self.zarr_writer_info.pixel_size_um or 1.0,
                z_step_um=self.zarr_writer_info.z_step_um,
                time_increment_s=self.zarr_writer_info.time_increment_s,
                channel_names=self.zarr_writer_info.channel_names,
                channel_colors=self.zarr_writer_info.channel_colors,
                channel_wavelengths=self.zarr_writer_info.channel_wavelengths,
                chunk_mode=_def.ZARR_CHUNK_MODE,
                compression=_def.ZARR_COMPRESSION,
                is_hcs=is_hcs or not use_6d_fov,  # 5D for HCS and non-HCS default
            )
            try:
                writer = ZarrWriter(config)
                writer.initialize()
            except Exception as e:
                self._log.error(f"Failed to initialize zarr writer for {output_path}: {e}")
                raise
            self._zarr_writers[writer_key] = writer
            if is_hcs:
                # Write HCS plate and well metadata
                self._write_hcs_metadata_if_needed(region_id, fov)
            self._log.info(f"Initialized zarr writer: {output_path}")

        writer = self._zarr_writers[writer_key]

        # Write frame
        t = info.time_point or 0
        c = info.configuration_idx
        z = info.z_index

        if is_hcs or not use_6d_fov:
            # 5D write
            writer.write_frame(image, t=t, c=c, z=z)
        else:
            # 6D write with FOV index
            writer.write_frame(image, t=t, c=c, z=z, fov=fov)

        # Record FOV stage position (idempotent - only stores first call per FOV)
        writer.record_fov_position(
            x_mm=info.position.x_mm,
            y_mm=info.position.y_mm,
            z_mm=info.position.z_mm,
            fov=fov if (not is_hcs and use_6d_fov) else None,
        )
        self._log.debug(f"Wrote frame t={t}, c={c}, z={z} to {output_path}")


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
    fov_position_in_well: Tuple[float, float] = field(
        default=(0.0, 0.0)
    )  # (x_mm, y_mm) relative to well origin
    overlap_pixels: Tuple[int, int, int, int] = field(
        default=(0, 0, 0, 0)
    )  # (top, bottom, left, right)
    pixel_size_um: float = 1.0
    target_resolutions_um: List[float] = field(
        default_factory=lambda: [5.0, 10.0, 20.0]
    )
    plate_resolution_um: float = 10.0
    output_dir: str = ""
    channel_names: List[str] = field(default_factory=list)
    z_index: int = 0
    total_z_levels: int = 1
    z_projection_mode: Union[ZProjectionMode, str] = ZProjectionMode.MIP
    skip_saving: bool = False  # Skip TIFF file saving (just generate for display)
    save_well_images: bool = False  # Save individual well TIFFs (controlled by SAVE_DOWNSAMPLED_WELL_IMAGES)
    interpolation_method: DownsamplingMethod = DownsamplingMethod.INTER_AREA_FAST

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
        log = squid.core.logging.get_logger(self.__class__.__name__)

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
            z_info = (
                f" z {self.z_index + 1}/{self.total_z_levels}"
                if self.total_z_levels > 1
                else ""
            )
            log.debug(
                f"Well {self.well_id}: channel {self.channel_idx} FOV {self.fov_index + 1}/{self.total_fovs_in_well}{z_info}, "
                f"channels: {accumulator.get_channel_count()}/{self.total_channels}"
            )
            return None

        # All FOVs for all channels (and z-levels for MIP) received - stitch and save
        z_info = (
            f" x {self.total_z_levels} z-levels ({self.z_projection_mode})"
            if self.total_z_levels > 1
            else ""
        )
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
                downsampled = downsample_tile(
                    stitched_channels[ch_idx],
                    self.pixel_size_um,
                    self.plate_resolution_um,
                    self.interpolation_method,
                )
                well_images_for_plate[ch_idx] = downsampled

            # Save well TIFFs only if enabled and not skipping
            if self.save_well_images and not self.skip_saving:
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
                            downsampled = downsample_tile(
                                stitched_channels[ch_idx],
                                self.pixel_size_um,
                                resolution,
                                self.interpolation_method,
                            )
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
                    log.debug(
                        f"Saved {filepath} with shape {stacked.shape} ({len(downsampled_stack)} channels)"
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
        *,
        backpressure_jobs: Any = None,
        backpressure_bytes: Any = None,
        backpressure_event: Any = None,
        zarr_writer_info: Optional[ZarrWriterInfo] = None,
    ) -> None:
        super().__init__()
        self._log = squid.core.logging.get_logger(__class__.__name__)
        self._acquisition_info = acquisition_info
        self._zarr_writer_info = zarr_writer_info

        self._input_queue: Optional[multiprocessing.Queue] = multiprocessing.Queue()
        self._input_timeout: float = 1.0
        self._output_queue: Optional[multiprocessing.Queue] = multiprocessing.Queue()
        self._shutdown_event: Optional[multiprocessing.Event] = multiprocessing.Event()
        self._pending_count: Optional[multiprocessing.Value] = multiprocessing.Value("i", 0)
        self._shutdown_called: bool = False

        # Backpressure shared values (from BackpressureController)
        self._bp_jobs = backpressure_jobs
        self._bp_bytes = backpressure_bytes
        self._bp_event = backpressure_event

        # Clean up stale metadata files from previous crashed acquisitions
        # Only run when explicitly requested (i.e., when OME-TIFF saving is being used)
        if cleanup_stale_ome_files:
            removed = ome_tiff_writer.cleanup_stale_metadata_files()
            if removed:
                self._log.info(f"Cleaned up {len(removed)} stale OME-TIFF metadata files")

    def dispatch(self, job: Job) -> bool:
        """Dispatch a job to the worker process.

        Increments the pending counter before queuing to prevent race conditions
        where has_pending() returns False while a job is being processed.

        For SaveOMETiffJob instances, injects acquisition_info before serialization.

        Also updates backpressure counters if configured.
        """
        # Inject acquisition_info into SaveOMETiffJob instances before serialization.
        # The job object is pickled when placed in the queue, so injection must happen here.
        if isinstance(job, SaveOMETiffJob):
            if self._acquisition_info is None:
                raise ValueError(
                    "Cannot dispatch SaveOMETiffJob: JobRunner was initialized without acquisition_info. "
                    "When using OME-TIFF saving, initialize JobRunner with an AcquisitionInfo instance."
                )
            job.acquisition_info = self._acquisition_info

        # Inject zarr_writer_info into SaveZarrJob instances before serialization.
        if isinstance(job, SaveZarrJob):
            if self._zarr_writer_info is None:
                raise ValueError(
                    "Cannot dispatch SaveZarrJob: JobRunner was initialized without zarr_writer_info. "
                    "When using ZARR_V3 saving, initialize JobRunner with a ZarrWriterInfo instance."
                )
            job.zarr_writer_info = self._zarr_writer_info

        # Calculate image size for backpressure tracking
        image_bytes = 0
        if job.capture_image.image_array is not None:
            image_bytes = job.capture_image.image_array.nbytes

        # Increment counter BEFORE putting job in queue to prevent race condition
        # where worker processes job before counter is incremented, causing
        # has_pending() to return False while job is still in flight.
        with self._pending_count.get_lock():
            self._pending_count.value += 1

        # Update backpressure counters
        if self._bp_jobs is not None:
            with self._bp_jobs.get_lock():
                self._bp_jobs.value += 1
        if self._bp_bytes is not None and image_bytes > 0:
            with self._bp_bytes.get_lock():
                self._bp_bytes.value += image_bytes

        try:
            self._input_queue.put_nowait(job)
        except Exception:
            # Rollback all counters on queue failure
            with self._pending_count.get_lock():
                self._pending_count.value -= 1
            if self._bp_jobs is not None:
                with self._bp_jobs.get_lock():
                    self._bp_jobs.value -= 1
            if self._bp_bytes is not None and image_bytes > 0:
                with self._bp_bytes.get_lock():
                    self._bp_bytes.value -= image_bytes
            raise
        return True

    def output_queue(self) -> Optional[multiprocessing.Queue]:
        return self._output_queue

    def has_pending(self) -> bool:
        """Check if there are jobs pending or in progress.

        Uses a counter that is incremented when jobs are dispatched and
        decremented when jobs complete, ensuring jobs in flight are tracked.
        """
        if self._pending_count is None:
            return False
        with self._pending_count.get_lock():
            return self._pending_count.value > 0

    def shutdown(self, timeout_s: float = 1.0) -> None:
        """Shutdown the job runner and release multiprocessing resources.

        This method properly cleans up multiprocessing primitives (Queue, Event)
        to prevent "leaked semaphore objects" warnings on application exit.
        """
        if self._shutdown_called:
            return
        self._shutdown_called = True

        if self._shutdown_event is not None:
            self._shutdown_event.set()
        self.join(timeout=timeout_s)

        # Terminate if still alive after timeout
        if self.is_alive():
            self._log.warning("JobRunner still alive after timeout, terminating")
            self.terminate()
            self.join(timeout=0.5)

        # Close queues and wait for feeder threads to finish
        if self._input_queue is not None:
            self._input_queue.close()
            self._input_queue.join_thread()
        if self._output_queue is not None:
            self._output_queue.close()
            self._output_queue.join_thread()

        # Release references to trigger immediate deallocation of semaphores
        self._input_queue = None
        self._output_queue = None
        self._shutdown_event = None
        self._pending_count = None

    def run(self) -> None:
        while not self._shutdown_event.is_set():
            job: Optional[Job] = None
            image_bytes: int = 0
            try:
                job = self._input_queue.get(timeout=self._input_timeout)
                # Track image bytes for backpressure release
                if job.capture_image.image_array is not None:
                    image_bytes = job.capture_image.image_array.nbytes
                self._log.info(f"Running job {job.job_id}...")
                result: Any = job.run()
                # Only queue non-None results (DownsampledViewJob returns None for intermediate FOVs)
                if result is not None:
                    self._log.info(
                        f"Job {job.job_id} returned. Sending result to output queue."
                    )
                    self._output_queue.put_nowait(
                        JobResult(job_id=job.job_id, result=result, exception=None)
                    )
                    self._log.debug(f"Result for {job.job_id} is on output queue.")
                else:
                    self._log.debug(f"Job {job.job_id} returned None, not queuing.")
            except queue.Empty:
                pass
            except Exception as e:
                if job:
                    self._log.exception(
                        f"Job {job.job_id} failed! Returning exception result."
                    )
                    self._output_queue.put_nowait(
                        JobResult(job_id=job.job_id, result=None, exception=e)
                    )
            finally:
                # Decrement pending counter if we processed a job
                if job is not None:
                    with self._pending_count.get_lock():
                        self._pending_count.value -= 1
                    # Release backpressure counters and signal capacity
                    # CRITICAL: Release bytes immediately per-job (not per-well) to prevent z-stack deadlock
                    if self._bp_jobs is not None:
                        with self._bp_jobs.get_lock():
                            self._bp_jobs.value -= 1
                    if self._bp_bytes is not None and image_bytes > 0:
                        with self._bp_bytes.get_lock():
                            self._bp_bytes.value -= image_bytes
                    if self._bp_event is not None:
                        self._bp_event.set()  # Signal capacity available
        # Finalize any zarr writers that are still open
        try:
            success = SaveZarrJob.finalize_all_writers()
            if not success:
                self._log.error("ZARR FINALIZATION INCOMPLETE - Some data may not be saved correctly")
        except Exception as e:
            self._log.error(f"Error finalizing zarr writers during shutdown: {e}")

        self._log.info("Shutdown request received, exiting run.")
