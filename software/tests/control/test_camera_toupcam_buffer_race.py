"""Regression test for the ToupcamCamera internal-read-buffer realloc race.

``_update_internal_settings`` runs on the acquisition thread on every
``set_exposure_time`` (i.e. every channel switch) and can replace
``self._internal_read_buffer`` with a fresh zero-filled buffer. ``_on_frame_callback``
used to read that attribute twice — ``PullImageV2`` fills it, then ``np.frombuffer``
re-reads it — so a swap landing between the two reads produced an entirely zero frame.
This test forces that interleaving deterministically.
"""

import threading

import numpy as np
import pytest

import squid.logging
from squid.abc import CameraFrameFormat, CameraPixelFormat
from squid.config import CameraConfig, CameraVariant
import control.camera_toupcam as camera_toupcam


_W, _H = 8, 4
_ITEMSIZE = 2  # uint16
_SIZE = _W * _H * _ITEMSIZE
_FILL_BYTE = 0xAB  # a filled frame is never all-zero
_FILL_PIXEL = int.from_bytes(bytes([_FILL_BYTE]) * _ITEMSIZE, "little")


def _make_callback_camera(reassign_buffer_during_pull: bool):
    """Build a minimal ToupcamCamera exercising the real _on_frame_callback path.

    Only the hardware boundary (the SDK object and ROI getters) is faked; the real
    _on_frame_callback and _process_raw_frame run. Because __init__ is skipped, every
    attribute the callback touches is set here explicitly, so a miss surfaces as a
    failed assertion about the frame rather than an AttributeError.
    """
    cam = object.__new__(camera_toupcam.ToupcamCamera)

    cam._raw_frame_callback_lock = threading.Lock()
    cam._internal_read_buffer = bytes([_FILL_BYTE]) * _SIZE  # a "filled" frame
    cam._current_frame = None
    cam._trigger_sent = True
    cam._raw_camera_stream_started = False
    cam._config = CameraConfig(camera_type=CameraVariant.TOUPCAM, default_pixel_format=CameraPixelFormat.MONO16)
    cam._software_crop_width_ratio = 1.0
    cam._software_crop_height_ratio = 1.0
    cam._diag_frame_log_every = 30  # keep high so the per-frame diagnostic branch never runs
    cam._diag_last_callback_start_ns = None
    cam._log = squid.logging.get_logger("test_toupcam_buffer_race")

    class _FakeSDK:
        def PullImageV2(self, buffer, bits, info):
            # The real PullImageV2 fills `buffer`; ours leaves the pre-filled sentinel in
            # place and instead models the acquisition thread's realloc landing mid-pull.
            if reassign_buffer_during_pull:
                cam._internal_read_buffer = bytes(_SIZE)

    cam._camera = _FakeSDK()
    cam._get_pixel_size_in_bytes = lambda: _ITEMSIZE
    cam.get_frame_format = lambda: CameraFrameFormat.RAW
    cam.get_pixel_format = lambda: CameraPixelFormat.MONO16
    cam.get_region_of_interest = lambda: (0, 0, _W, _H)
    cam.get_binning = lambda: (1, 1)

    captured = {}
    cam._propogate_frame = lambda frame: captured.__setitem__("frame", np.array(frame.frame))
    return cam, captured


@pytest.mark.parametrize("reassign_buffer_during_pull", [False, True], ids=["no_realloc", "realloc_during_pull"])
def test_callback_frame_not_zeroed_by_concurrent_buffer_realloc(reassign_buffer_during_pull):
    """The delivered frame must be the buffer PullImageV2 filled, realloc or not."""
    cam, captured = _make_callback_camera(reassign_buffer_during_pull)

    cam._on_frame_callback()

    frame = captured["frame"]
    assert frame.shape == (_H, _W)
    # An all-zero frame here is the production symptom: the callback viewed the freshly
    # zero-filled buffer the concurrent realloc swapped in, not the one that was filled.
    assert np.all(frame == _FILL_PIXEL), "delivered frame is all zeros: buffer-realloc race corrupted it"
