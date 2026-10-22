"""Shared builders for ToupcamCamera unit tests, which run real driver methods over a faked SDK."""

import threading
from typing import Callable, Dict, Optional
from unittest.mock import MagicMock

import numpy as np

import squid.logging
from squid.abc import CameraFrameFormat, CameraPixelFormat
from squid.config import CameraConfig, CameraVariant
import control.camera_toupcam as camera_toupcam

RESOLUTIONS = {(1, 1): (6208, 4168), (2, 2): (3104, 2084), (3, 3): (2069, 1389)}


def capabilities(**overrides) -> camera_toupcam.ToupCamCapabilities:
    fields = dict(
        binning_to_resolution=RESOLUTIONS,
        max_bit_depth=16,
        has_fan=False,
        has_TEC=False,
        has_low_noise_mode=False,
        has_black_level=True,
    )
    fields.update(overrides)
    return camera_toupcam.ToupCamCapabilities(**fields)


def skeleton_toupcam(log_name: str, **attrs) -> camera_toupcam.ToupcamCamera:
    """A ToupcamCamera with __init__ skipped: a MagicMock SDK, the lock _pause_streaming takes, not streaming, plus
    whatever ``attrs`` the method under test reads."""
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._camera = MagicMock()
    cam._trigger_lock = threading.RLock()
    cam._log = squid.logging.get_logger(log_name)
    cam.get_is_streaming = lambda: False
    for name, value in attrs.items():
        setattr(cam, name, value)
    return cam


def make_callback_camera(
    pixel_format: CameraPixelFormat,
    buffer: bytes,
    width: int,
    height: int,
    on_pull: Optional[Callable[[camera_toupcam.ToupcamCamera], None]] = None,
    log_name: str = "test_toupcam_callback",
):
    """A ToupcamCamera exercising the real _on_frame_callback path over ``buffer``, the read buffer PullImageV2 has
    'filled'.  Returns the camera and a dict that receives the propagated frame under "frame".

    Only the hardware boundary (the SDK object and ROI getters) is faked; the real _on_frame_callback and
    _process_raw_frame run.  Because __init__ is skipped, every attribute the callback touches is set here
    explicitly, so a miss surfaces as a failed assertion about the frame rather than an AttributeError.
    ``on_pull`` runs inside the fake PullImageV2, to model the acquisition thread interleaving with it.
    """
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._raw_frame_callback_lock = threading.Lock()
    cam._internal_read_buffer = buffer
    cam._current_frame = None
    cam._trigger_sent = True
    cam._raw_camera_stream_started = False
    cam._config = CameraConfig(camera_type=CameraVariant.TOUPCAM, default_pixel_format=pixel_format)
    cam._software_crop_width_ratio = 1.0
    cam._software_crop_height_ratio = 1.0
    cam._diag_frame_log_every = 30  # keep high so the per-frame diagnostic branch never runs
    cam._diag_last_callback_start_ns = None
    cam._log = squid.logging.get_logger(log_name)

    class _FakeSDK:
        def PullImageV2(self, buffer, bits, info):
            # The real PullImageV2 fills `buffer`; ours leaves the pre-filled one in place.
            if on_pull is not None:
                on_pull(cam)

    cam._camera = _FakeSDK()
    cam._get_pixel_size_in_bytes = lambda: 1 if pixel_format.bit_depth == 8 else 2
    cam.get_frame_format = lambda: CameraFrameFormat.RAW
    cam.get_pixel_format = lambda: pixel_format
    cam.get_region_of_interest = lambda: (0, 0, width, height)
    cam.get_binning = lambda: (1, 1)

    captured: Dict[str, np.ndarray] = {}
    cam._propogate_frame = lambda frame: captured.__setitem__("frame", np.array(frame.frame))
    return cam, captured
