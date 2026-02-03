"""Simulated disk I/O for development/testing.

Encodes images to memory buffers to exercise RAM/CPU pressure,
then throttles and discards to simulate disk throughput without
actually writing files.

Usage:
    When SIMULATED_DISK_IO_ENABLED is True in _def, the job classes
    (SaveImageJob, SaveOMETiffJob) will call these functions instead of
    performing real disk I/O.
"""

import threading
import time
from io import BytesIO
from typing import Any, Dict

import numpy as np
import tifffile

import _def
import squid.core.logging

_log = squid.core.logging.get_logger(__name__)

# Track initialized OME-TIFF stacks (keyed by stack identifier)
# Note: This runs in JobRunner subprocess, so state is process-local and
# automatically reset when a new acquisition starts a fresh subprocess.
_simulated_ome_stacks: Dict[str, Dict[str, Any]] = {}
_simulated_ome_lock = threading.Lock()


def is_simulation_enabled() -> bool:
    """Check if simulated disk I/O is enabled.

    Returns False if SIMULATION_FORCE_SAVE_IMAGES is True, allowing
    real file saves even when SIMULATED_DISK_IO_ENABLED is True.
    """
    if getattr(_def, "SIMULATION_FORCE_SAVE_IMAGES", False):
        return False
    return getattr(_def, "SIMULATED_DISK_IO_ENABLED", False)


def get_simulated_speed_mb_s() -> float:
    """Get configured simulated write speed in MB/s."""
    return getattr(_def, "SIMULATED_DISK_IO_SPEED_MB_S", 200.0)


def get_simulated_compression() -> bool:
    """Get whether compression is enabled for simulation."""
    return getattr(_def, "SIMULATED_DISK_IO_COMPRESSION", True)


def throttle_for_speed(bytes_count: int, speed_mb_s: float) -> float:
    """Sleep to simulate disk write at target speed.

    Args:
        bytes_count: Number of bytes "written"
        speed_mb_s: Target speed in megabytes per second

    Returns:
        Actual delay in seconds
    """
    if speed_mb_s <= 0:
        return 0.0
    if bytes_count < 0:
        _log.warning(f"throttle_for_speed called with negative bytes_count={bytes_count}, treating as 0")
        return 0.0
    delay_s = (bytes_count / (1024 * 1024)) / speed_mb_s
    time.sleep(delay_s)
    return delay_s


def simulated_tiff_write(image: np.ndarray) -> int:
    """Simulate single TIFF write (for SaveImageJob).

    Encodes image to BytesIO buffer (exercises encoding RAM/CPU),
    throttles to configured speed, then discards buffer.

    Args:
        image: Image array to "write"

    Returns:
        Number of bytes that would have been written
    """
    buffer = BytesIO()
    compression = "lzw" if get_simulated_compression() else None

    try:
        tifffile.imwrite(buffer, image, compression=compression)
    except Exception as e:
        _log.error(
            f"Simulated TIFF write failed: image shape={image.shape}, "
            f"dtype={image.dtype}, compression={compression}. Error: {e}"
        )
        raise

    bytes_written = buffer.tell()
    throttle_for_speed(bytes_written, get_simulated_speed_mb_s())
    _log.debug(f"Simulated TIFF write: {bytes_written} bytes, shape={image.shape}")
    return bytes_written


def simulated_ome_tiff_write(
    image: np.ndarray,
    stack_key: str,
    shape: tuple,
    time_point: int,
    z_index: int,
    channel_index: int,
) -> int:
    """Simulate OME-TIFF write with init + plane write timing.

    Tracks stack state to simulate:
    - Initialization overhead on first plane
    - Per-plane encoding
    - Finalization overhead when complete

    Args:
        image: Image array for this plane
        stack_key: Unique identifier for this stack (e.g., output_path)
        shape: Full 5D stack shape (T, Z, C, Y, X)
        time_point: Time point index
        z_index: Z slice index
        channel_index: Channel index

    Returns:
        Bytes "written" for this operation
    """
    speed_mb_s = get_simulated_speed_mb_s()

    # First plane for this stack - simulate initialization
    if stack_key not in _simulated_ome_stacks:
        expected_count = shape[0] * shape[1] * shape[2]  # T * Z * C
        _simulated_ome_stacks[stack_key] = {
            "shape": shape,
            "written_planes": set(),
            "expected_count": expected_count,
        }
        # Simulate metadata/header write overhead (~4KB for OME-XML header)
        throttle_for_speed(4096, speed_mb_s)
        _log.debug(f"Initialized simulated OME stack: {stack_key}, expected planes: {expected_count}")

    # Simulate plane write with encoding
    buffer = BytesIO()
    compression = "lzw" if get_simulated_compression() else None

    try:
        tifffile.imwrite(buffer, image, compression=compression)
    except Exception as e:
        _log.error(
            f"Simulated OME-TIFF plane write failed: stack={stack_key}, "
            f"plane=t{time_point}-z{z_index}-c{channel_index}, "
            f"image shape={image.shape}, dtype={image.dtype}. Error: {e}"
        )
        raise

    bytes_written = buffer.tell()
    throttle_for_speed(bytes_written, speed_mb_s)

    # Track this plane
    plane_key = f"{time_point}-{z_index}-{channel_index}"
    stack_info = _simulated_ome_stacks[stack_key]
    stack_info["written_planes"].add(plane_key)

    _log.debug(
        f"Simulated OME plane write: {bytes_written} bytes, "
        f"plane={plane_key}, progress={len(stack_info['written_planes'])}/{stack_info['expected_count']}"
    )

    # Check completion and cleanup
    if len(stack_info["written_planes"]) >= stack_info["expected_count"]:
        # Stack complete - simulate finalization overhead (~8KB for OME-XML update)
        throttle_for_speed(8192, speed_mb_s)
        del _simulated_ome_stacks[stack_key]
        _log.debug(f"Completed simulated OME stack: {stack_key}")

    return bytes_written


def reset_simulated_stacks() -> None:
    """Reset simulated stack tracking state.

    Call this when starting a new acquisition to ensure clean state.
    """
    global _simulated_ome_stacks
    _simulated_ome_stacks.clear()
    _simulated_zarr_stacks.clear()
    _log.debug("Reset simulated stack tracking state")


# Track simulated zarr datasets (keyed by output path)
_simulated_zarr_stacks: Dict[str, Dict[str, Any]] = {}
_simulated_zarr_lock = threading.Lock()


def simulated_zarr_write(
    image: np.ndarray,
    stack_key: str,
    shape: tuple,
    time_point: int,
    z_index: int,
    channel_index: int,
) -> int:
    """Simulate Zarr v3 write with compression simulation.

    Tracks dataset state to simulate:
    - Initialization overhead on first frame
    - Per-frame blosc compression
    - Finalization overhead when complete

    Args:
        image: Image array for this frame
        stack_key: Unique identifier for this dataset (e.g., output_path)
        shape: Dataset shape - 5D (T, C, Z, Y, X) or 6D (FOV, T, C, Z, Y, X)
        time_point: Time point index
        z_index: Z slice index
        channel_index: Channel index

    Returns:
        Bytes "written" for this operation
    """
    speed_mb_s = get_simulated_speed_mb_s()

    # First frame for this dataset - simulate initialization (with lock for shared state)
    is_first_frame = False
    with _simulated_zarr_lock:
        if stack_key not in _simulated_zarr_stacks:
            # Calculate expected frame count based on T * C * Z
            if len(shape) == 5:
                expected_count = shape[0] * shape[1] * shape[2]  # T * C * Z
            elif len(shape) == 6:
                expected_count = shape[1] * shape[2] * shape[3]  # T * C * Z (skip FOV dim)
            else:
                raise ValueError(f"Unexpected shape dimensionality: {len(shape)}")
            _simulated_zarr_stacks[stack_key] = {
                "shape": shape,
                "written_frames": set(),
                "expected_count": expected_count,
            }
            is_first_frame = True
            _log.debug(f"Initialized simulated zarr dataset: {stack_key}, expected frames: {expected_count}")

    # Simulate metadata/zarr.json write overhead outside lock (~2KB)
    if is_first_frame:
        throttle_for_speed(2048, speed_mb_s)

    # Simulate frame write with blosc compression
    # Blosc typically achieves 2-4x compression on microscopy data
    raw_bytes = image.nbytes
    if get_simulated_compression():
        # Simulate blosc compression overhead
        compression_ratio = 3.0  # Typical for blosc-lz4 on uint16 microscopy
        compressed_bytes = int(raw_bytes / compression_ratio)
        bytes_written = compressed_bytes
    else:
        bytes_written = raw_bytes

    throttle_for_speed(bytes_written, speed_mb_s)

    # Track this frame and check completion (with lock for shared state access)
    frame_key = f"{time_point}-{channel_index}-{z_index}"
    is_complete = False
    with _simulated_zarr_lock:
        stack_info = _simulated_zarr_stacks[stack_key]
        stack_info["written_frames"].add(frame_key)
        written_count = len(stack_info["written_frames"])
        expected_count = stack_info["expected_count"]
        is_complete = written_count >= expected_count

        _log.debug(
            f"Simulated zarr frame write: {bytes_written} bytes (raw: {raw_bytes}), "
            f"frame={frame_key}, progress={written_count}/{expected_count}"
        )

        # Check completion and cleanup
        if is_complete:
            del _simulated_zarr_stacks[stack_key]

    # Simulate finalization overhead outside lock (~4KB for zarr.json attributes update)
    if is_complete:
        throttle_for_speed(4096, speed_mb_s)
        _log.debug(f"Completed simulated zarr dataset: {stack_key}")

    return bytes_written


def reset_simulated_zarr_stacks() -> None:
    """Reset simulated zarr stack tracking state."""
    global _simulated_zarr_stacks
    _simulated_zarr_stacks.clear()
    _log.debug("Reset simulated zarr stack tracking state")
