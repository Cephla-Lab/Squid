"""Setting the Hamamatsu exposure time never overlaps a software trigger.

DCAM deadlocks when prop_setvalue (from the GUI thread, on an exposure change) runs at the same
time as cap_firetrigger (from the live trigger thread). Rapidly changing exposure during live froze
the GUI with the main thread stuck in prop_setvalue and the trigger thread stuck in cap_firetrigger.
set_exposure_time now holds the camera's trigger lock, so a trigger that arrives mid-set is dropped
instead of reaching DCAM. Only the SDK boundary is faked here.
"""

import contextlib
import ctypes
import threading
from unittest import mock

import squid.logging
from squid.abc import CameraAcquisitionMode

# control/dcamapi4.py loads the vendor library (libdcamapi.so / dcamapi.dll) at import time. Only
# that load is patched, and only while importing, so the driver module is the real one.
with contextlib.ExitStack() as _stack:
    _stack.enter_context(mock.patch.object(ctypes.cdll, "LoadLibrary", return_value=mock.MagicMock()))
    if hasattr(ctypes, "windll"):
        _stack.enter_context(mock.patch.object(ctypes.windll, "LoadLibrary", return_value=mock.MagicMock()))
    import control.camera_hamamatsu as camera_hamamatsu


class BlockingPropDcam:
    """prop_setvalue parks until released, so the test can act while an exposure set is in flight."""

    def __init__(self):
        self.in_prop_setvalue = threading.Event()
        self.release = threading.Event()

    def prop_setvalue(self, prop, value):
        self.in_prop_setvalue.set()
        assert self.release.wait(timeout=5)
        return True

    def lasterr(self):
        return "no error"


def make_camera(dcam):
    cam = object.__new__(camera_hamamatsu.HamamatsuCamera)
    cam._camera = dcam
    cam._log = squid.logging.get_logger("test_camera_hamamatsu_exposure_trigger_lock")
    cam._trigger_lock = threading.RLock()
    cam._trigger_sent = threading.Event()
    cam._hw_set_strobe_delay_ms_fn = None
    cam._exposure_time_ms = 10.0
    cam.get_acquisition_mode = lambda: CameraAcquisitionMode.SOFTWARE_TRIGGER
    cam.triggers_sent = []
    cam._send_trigger_imp = lambda illumination_time=None: cam.triggers_sent.append(illumination_time)
    return cam


def test_trigger_during_exposure_set_is_dropped():
    dcam = BlockingPropDcam()
    cam = make_camera(dcam)

    setter = threading.Thread(target=cam.set_exposure_time, args=(20.0,))
    setter.start()
    try:
        assert dcam.in_prop_setvalue.wait(timeout=5)
        cam.send_trigger()  # what the live trigger thread does
        assert cam.triggers_sent == []
    finally:
        dcam.release.set()
        setter.join(timeout=5)

    assert not setter.is_alive()
    assert cam.get_exposure_time() == 20.0

    cam.send_trigger()  # once the set is done, triggers go through again
    assert cam.triggers_sent == [None]


def test_exposure_set_is_reentrant_under_trigger_lock():
    # set_sensor_mode / set_region_of_interest / _pause_streaming already hold the trigger lock
    # when they recompute exposure; that must not deadlock.
    dcam = BlockingPropDcam()
    dcam.release.set()
    cam = make_camera(dcam)

    done = threading.Event()

    def nested():
        with cam._trigger_lock:
            cam.set_exposure_time(30.0)
        done.set()

    t = threading.Thread(target=nested, daemon=True)
    t.start()
    assert done.wait(timeout=5)
    assert cam.get_exposure_time() == 30.0
