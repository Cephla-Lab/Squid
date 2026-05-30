import ctypes

import numpy as np
import pytest

# The TUCam SDK shared library is loaded at import time (control/TUCam.py).
# Skip the whole module where it is not installed (e.g. CI without the SDK).
try:
    from control.TUCam import TUCAM_RAWIMG_HEADER
    from control.camera_tucsen import TucsenCamera
    from squid.abc import CameraError
except OSError as e:  # libTUCam.so.1 / TUCam.dll not present
    pytest.skip(f"TUCam SDK not available: {e}", allow_module_level=True)


def _make_header(arr: np.ndarray):
    """Build a TUCAM_RAWIMG_HEADER pointing at a real ctypes buffer holding `arr`.

    Returns (header, backing_buffer). The caller MUST keep backing_buffer alive
    for as long as header.pImgData is used — pImgData is a raw pointer into it.
    """
    raw = arr.astype(np.uint16).tobytes()
    backing = ctypes.create_string_buffer(raw, len(raw))
    header = TUCAM_RAWIMG_HEADER()
    header.usWidth = arr.shape[1]
    header.usHeight = arr.shape[0]
    header.uiImgSize = len(raw)
    header.pImgData = ctypes.cast(backing, ctypes.c_void_p)
    return header, backing


def test_convert_header_to_numpy_roundtrips_pixels():
    arr = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)
    header, _backing = _make_header(arr)

    out = TucsenCamera._convert_header_to_numpy(header)

    assert out.shape == (2, 3)
    assert out.dtype == np.uint16
    np.testing.assert_array_equal(out, arr)


def test_convert_header_to_numpy_copies_data():
    # The returned array must own its data (copied from device memory), so it
    # stays valid after the source buffer is dropped — it must not alias pImgData.
    arr = np.array([[7, 8], [9, 10]], dtype=np.uint16)
    header, backing = _make_header(arr)

    out = TucsenCamera._convert_header_to_numpy(header)

    # Drop the source buffer and mutate its bytes; `out` must be unaffected.
    ctypes.memset(backing, 0, len(backing))
    del backing
    np.testing.assert_array_equal(out, [[7, 8], [9, 10]])


def test_convert_header_to_numpy_rejects_short_buffer():
    arr = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)
    header, _backing = _make_header(arr)
    header.uiImgSize = 4  # smaller than usWidth*usHeight*2 == 12

    with pytest.raises(CameraError):
        TucsenCamera._convert_header_to_numpy(header)


def test_convert_header_to_numpy_rejects_padding():
    arr = np.arange(2 * 3, dtype=np.uint16).reshape(2, 3)
    header, _backing = _make_header(arr)
    header.usXPadding = 1  # per-row padding would corrupt a flat reshape

    with pytest.raises(CameraError):
        TucsenCamera._convert_header_to_numpy(header)
