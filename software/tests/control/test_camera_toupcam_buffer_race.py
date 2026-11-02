"""Regression test for the ToupcamCamera internal-read-buffer realloc race.

``_update_internal_settings`` runs on the acquisition thread on every
``set_exposure_time`` (i.e. every channel switch) and can replace
``self._internal_read_buffer`` with a fresh zero-filled buffer. ``_on_frame_callback``
used to read that attribute twice — ``PullImageV2`` fills it, then ``np.frombuffer``
re-reads it — so a swap landing between the two reads produced an entirely zero frame.
This test forces that interleaving deterministically.
"""

import numpy as np
import pytest

from squid.abc import CameraPixelFormat
from tests.control.toupcam_test_utils import make_callback_camera


_W, _H = 8, 4
_ITEMSIZE = 2  # uint16
_SIZE = _W * _H * _ITEMSIZE
_FILL_BYTE = 0xAB  # a filled frame is never all-zero
_FILL_PIXEL = int.from_bytes(bytes([_FILL_BYTE]) * _ITEMSIZE, "little")


def _realloc_during_pull(cam):
    """Models the acquisition thread's realloc landing mid-pull."""
    cam._internal_read_buffer = bytes(_SIZE)


@pytest.mark.parametrize("reassign_buffer_during_pull", [False, True], ids=["no_realloc", "realloc_during_pull"])
def test_callback_frame_not_zeroed_by_concurrent_buffer_realloc(reassign_buffer_during_pull):
    """The delivered frame must be the buffer PullImageV2 filled, realloc or not."""
    cam, captured = make_callback_camera(
        CameraPixelFormat.MONO16,
        bytes([_FILL_BYTE]) * _SIZE,  # a "filled" frame
        _W,
        _H,
        on_pull=_realloc_during_pull if reassign_buffer_during_pull else None,
        log_name="test_toupcam_buffer_race",
    )

    cam._on_frame_callback()

    frame = captured["frame"]
    assert frame.shape == (_H, _W)
    # An all-zero frame here is the production symptom: the callback viewed the freshly
    # zero-filled buffer the concurrent realloc swapped in, not the one that was filled.
    assert np.all(frame == _FILL_PIXEL), "delivered frame is all zeros: buffer-realloc race corrupted it"
