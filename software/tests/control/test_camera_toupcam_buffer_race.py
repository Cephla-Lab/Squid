"""Regression test for the ToupcamCamera internal-read-buffer realloc race.

Root cause: ``_update_internal_settings`` (called on every ``set_exposure_time``,
i.e. on every channel switch during an acquisition) reassigns
``self._internal_read_buffer = bytes(buffer_size)`` — a fresh zero-filled buffer —
on the acquisition thread, with no synchronization against the camera callback
thread. ``_on_frame_callback`` reads ``self._internal_read_buffer`` twice
non-atomically: ``PullImageV2`` fills it, then ``np.frombuffer`` re-reads it. If the
reallocation lands between those two reads, ``PullImageV2`` filled the old buffer but
``np.frombuffer`` views the new all-zero one, so the delivered/saved frame is entirely
zeros. This test forces that interleaving deterministically.
"""

import threading
import types

import numpy as np

import squid.logging
from squid.abc import CameraFrameFormat, CameraPixelFormat
import control.camera_toupcam as camera_toupcam


_W, _H = 8, 4
_ITEMSIZE = 2  # uint16
_SIZE = _W * _H * _ITEMSIZE
_SENTINEL_BYTE = 0xAB  # a filled frame is never all-zero
_SENTINEL_U16 = 0xABAB  # little/big-endian identical for repeated bytes


def _make_callback_camera(reassign_buffer_during_pull: bool):
    """Build a minimal ToupcamCamera exercising the real _on_frame_callback path.

    Only the hardware boundary (the SDK object and ROI getters) is faked. The real
    _on_frame_callback and _process_raw_frame run. The fake PullImageV2 models the
    acquisition thread reallocating _internal_read_buffer to a fresh zero buffer while
    the callback is in flight.
    """
    cam = object.__new__(camera_toupcam.ToupcamCamera)

    cam._raw_frame_callback_lock = threading.Lock()
    cam._internal_read_buffer = bytes([_SENTINEL_BYTE]) * _SIZE  # a "filled" frame
    cam._current_frame = None
    cam._trigger_sent = True
    cam._raw_camera_stream_started = False
    cam._config = types.SimpleNamespace(rotate_image_angle=None, flip=None, crop_width=None, crop_height=None)
    cam._diag_frame_log_every = 30  # keep high so the per-frame diagnostic branch never runs
    cam._log = squid.logging.get_logger("test_toupcam_buffer_race")

    class _FakeSDK:
        def PullImageV2(self, buffer, bits, info):
            # PullImageV2 fills `buffer` (our sentinel buffer). Model the acquisition
            # thread's set_exposure_time -> _update_internal_settings reallocation
            # landing during the pull: swap in a fresh zero-filled buffer.
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


def test_callback_frame_not_zeroed_by_concurrent_buffer_realloc():
    """A buffer reallocation racing the in-flight callback must not zero the frame."""
    cam, captured = _make_callback_camera(reassign_buffer_during_pull=True)

    cam._on_frame_callback()

    frame = captured["frame"]
    assert frame.shape == (_H, _W)
    # The frame must reflect the buffer PullImageV2 actually filled (the sentinel), not the
    # freshly zero-filled buffer the concurrent realloc swapped in. An all-zero frame is the
    # production symptom this guards against.
    assert np.all(frame == _SENTINEL_U16), "delivered frame is all zeros: buffer-realloc race corrupted it"


def test_callback_frame_correct_without_realloc():
    """Sanity: with no concurrent realloc the callback delivers the filled frame."""
    cam, captured = _make_callback_camera(reassign_buffer_during_pull=False)

    cam._on_frame_callback()

    frame = captured["frame"]
    assert frame.shape == (_H, _W)
    assert np.all(frame == _SENTINEL_U16)
