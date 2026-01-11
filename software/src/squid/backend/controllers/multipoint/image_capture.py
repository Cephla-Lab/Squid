"""
Image capture context for multipoint acquisitions.

This module provides:
- CaptureContext: Dataclass holding capture parameters
- build_capture_info: Factory function for building CaptureInfo

These provide a clean parameter passing pattern for image capture.
"""

from dataclasses import dataclass
from typing import Optional, Union, TYPE_CHECKING
import time

import squid.core.abc

if TYPE_CHECKING:
    from squid.core.utils.config_utils import ChannelMode
    from squid.backend.controllers.multipoint.job_processing import CaptureInfo


@dataclass
class CaptureContext:
    """
    Context for a single image capture.

    Holds all parameters needed for one capture operation, making it easy
    to pass capture context between methods.

    Note: region_id accepts both str and int for compatibility with existing
    code patterns. The str form (e.g. "region_0") is used in scan_region_fov_coords_mm
    while the int form is used in CaptureInfo.
    """

    config: "ChannelMode"
    file_id: str
    save_directory: str
    z_index: int
    region_id: Union[str, int]
    fov: int
    config_idx: int
    time_point: int
    z_piezo_um: Optional[float] = None
    pixel_size_um: Optional[float] = None


def build_capture_info(
    context: CaptureContext,
    position: squid.core.abc.Pos,
    capture_time: Optional[float] = None,
) -> "CaptureInfo":
    """
    Build a CaptureInfo from a CaptureContext and position.

    This factory function consolidates CaptureInfo construction that was
    previously duplicated across acquire_camera_image and acquire_rgb_image.

    Args:
        context: Capture context with configuration and identifiers
        position: Current stage position
        capture_time: Capture timestamp (defaults to current time)

    Returns:
        CaptureInfo ready for job processing
    """
    from squid.backend.controllers.multipoint.job_processing import CaptureInfo

    # Note: CaptureInfo.region_id is typed as int but actually accepts strings
    # in the existing codebase. Using type: ignore for compatibility.
    return CaptureInfo(
        position=position,
        z_index=context.z_index,
        capture_time=capture_time if capture_time is not None else time.time(),
        z_piezo_um=context.z_piezo_um,
        configuration=context.config,
        save_directory=context.save_directory,
        file_id=context.file_id,
        region_id=context.region_id,  # type: ignore[arg-type]
        fov=context.fov,
        configuration_idx=context.config_idx,
        time_point=context.time_point,
        pixel_size_um=context.pixel_size_um,
    )
