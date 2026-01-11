"""Simulated disk I/O for development/testing.

Encodes images to memory buffers to exercise RAM/CPU pressure,
then throttles and discards to simulate disk throughput without
actually writing files.

Usage:
    When SIMULATED_DISK_IO_ENABLED is True in control._def, the job classes
    (SaveImageJob, SaveOMETiffJob) will call these functions instead of
    performing real disk I/O.
"""

from io import BytesIO
from typing import Dict, Optional
import time

import numpy as np
import tifffile

import control._def


# Track initialized OME-TIFF stacks (keyed by stack identifier)
# Note: This runs in JobRunner subprocess, so state is process-local
_simulated_ome_stacks: Dict[str, Dict] = {}


def is_simulation_enabled() -> bool:
    """Check if simulated disk I/O is enabled."""
    return control._def.SIMULATED_DISK_IO_ENABLED


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
    delay_s = (bytes_count / (1024 * 1024)) / speed_mb_s
    time.sleep(delay_s)
    return delay_s


def simulated_tiff_write(
    image: np.ndarray,
    compression: Optional[str] = None,
    **kwargs,
) -> int:
    """Simulate single TIFF write (for SaveImageJob).

    Encodes image to BytesIO buffer (exercises encoding RAM/CPU),
    throttles to configured speed, then discards buffer.

    Args:
        image: Image array to "write"
        compression: Compression codec (e.g., "lzw", "zstd", None)
        **kwargs: Additional tifffile.imwrite arguments (ignored)

    Returns:
        Number of bytes that would have been written
    """
    buffer = BytesIO()
    if control._def.SIMULATED_DISK_IO_COMPRESSION and compression is None:
        compression = "lzw"
    tifffile.imwrite(buffer, image, compression=compression)
    bytes_written = buffer.tell()
    throttle_for_speed(bytes_written, control._def.SIMULATED_DISK_IO_SPEED_MB_S)
    return bytes_written


def simulated_ome_tiff_write(
    image: np.ndarray,
    stack_key: str,
    shape: tuple,
    dtype: np.dtype,
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
        dtype: Target dtype
        time_point: Time point index
        z_index: Z slice index
        channel_index: Channel index

    Returns:
        Bytes "written" for this operation
    """
    global _simulated_ome_stacks

    # First plane for this stack - simulate initialization
    if stack_key not in _simulated_ome_stacks:
        _simulated_ome_stacks[stack_key] = {
            "shape": shape,
            "written_planes": set(),
            "expected_count": shape[0] * shape[1] * shape[2],  # T * Z * C
        }
        # Simulate metadata/header write overhead (~4KB for OME-XML header)
        throttle_for_speed(4096, control._def.SIMULATED_DISK_IO_SPEED_MB_S)

    # Simulate plane write with encoding
    buffer = BytesIO()
    compression = "lzw" if control._def.SIMULATED_DISK_IO_COMPRESSION else None
    tifffile.imwrite(buffer, image, compression=compression)
    bytes_written = buffer.tell()
    throttle_for_speed(bytes_written, control._def.SIMULATED_DISK_IO_SPEED_MB_S)

    # Track this plane
    plane_key = f"{time_point}-{z_index}-{channel_index}"
    stack_info = _simulated_ome_stacks[stack_key]
    stack_info["written_planes"].add(plane_key)

    # Check completion and cleanup
    if len(stack_info["written_planes"]) >= stack_info["expected_count"]:
        # Stack complete - simulate finalization overhead (~8KB for OME-XML update)
        throttle_for_speed(8192, control._def.SIMULATED_DISK_IO_SPEED_MB_S)
        del _simulated_ome_stacks[stack_key]

    return bytes_written


def clear_simulated_stacks():
    """Clear all simulated stack state.

    Call this between acquisitions to reset state.
    """
    global _simulated_ome_stacks
    _simulated_ome_stacks.clear()
