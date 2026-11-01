"""_last_dcam_error_string_direct(): a DCAM error is named, and an unlisted code is reported, never raised.

The membership test used to be inverted: a listed error came out as UNKNOWN_ERROR, and an unlisted
code raised KeyError from inside the error path, hiding the failure being reported.
"""

import contextlib
import ctypes
from unittest import mock

# control/dcamapi4.py loads the vendor library (libdcamapi.so / dcamapi.dll) at import time. Only
# that load is patched, and only while importing, so the driver module is the real one.
with contextlib.ExitStack() as _stack:
    _stack.enter_context(mock.patch.object(ctypes.cdll, "LoadLibrary", return_value=mock.MagicMock()))
    if hasattr(ctypes, "windll"):
        _stack.enter_context(mock.patch.object(ctypes.windll, "LoadLibrary", return_value=mock.MagicMock()))
    import control.camera_hamamatsu as camera_hamamatsu
    from control.dcamapi4 import DCAMERR

describe = camera_hamamatsu.HamamatsuCamera._last_dcam_error_string_direct


def test_a_listed_error_is_named():
    assert describe(DCAMERR.INVALIDHANDLE) == f"{int(DCAMERR.INVALIDHANDLE)}:INVALIDHANDLE"


def test_the_raw_integer_code_is_named_too():
    assert describe(int(DCAMERR.BUSY)) == f"{int(DCAMERR.BUSY)}:BUSY"


def test_an_unlisted_code_is_reported_not_raised():
    assert describe(-12345) == "-12345:UNKNOWN_ERROR"
