"""HamamatsuCamera.reopen(): a camera that stopped delivering frames is closed and opened again with its settings.

The settings come from what was last applied, not from the camera (which may not answer). A read thread stuck in a
DCAM call on the old handle is left behind holding its lock, and blocks neither the reopen nor the new read thread;
if its call ever returns it stops instead of reading beside the new one. Only the SDK boundary is faked here.
"""

import contextlib
import ctypes
import threading
import time
import types
from unittest import mock

import numpy as np

from squid.abc import CameraAcquisitionMode
from squid.config import CameraConfig, CameraPixelFormat, CameraVariant

# control/dcamapi4.py loads the vendor library (libdcamapi.so / dcamapi.dll) at import time. Only
# that load is patched, and only while importing, so the driver module is the real one.
with contextlib.ExitStack() as _stack:
    _stack.enter_context(mock.patch.object(ctypes.cdll, "LoadLibrary", return_value=mock.MagicMock()))
    if hasattr(ctypes, "windll"):
        _stack.enter_context(mock.patch.object(ctypes.windll, "LoadLibrary", return_value=mock.MagicMock()))
    import control.camera_hamamatsu as camera_hamamatsu
    from control.dcamapi4 import DCAM_IDPROP, DCAM_PIXELTYPE, DCAMPROP


class FakeDcam:
    """One DCAM handle: properties in a dict, frames on demand. wait_event can be made to park (a stuck driver)."""

    def __init__(self, name):
        self.name = name
        self.props = {
            DCAM_IDPROP.TRIGGERSOURCE: DCAMPROP.TRIGGERSOURCE.INTERNAL,
            DCAM_IDPROP.IMAGE_PIXELTYPE: DCAM_PIXELTYPE.MONO16,
        }
        self.props_set = []
        self.calls = []
        self.frame_ready = threading.Event()
        self.stuck = threading.Event()  # set: wait_event parks until unstuck
        self.unstuck = threading.Event()
        self.in_wait = threading.Event()
        self.stamp = 0

    def prop_setvalue(self, prop, value):
        self.props[prop] = value
        self.props_set.append(prop)
        return True

    def prop_getvalue(self, prop):
        return self.props.get(prop, 0)

    def prop_getattr(self, prop):
        return types.SimpleNamespace(valuemin=1, valuemax=3, valuestep=1)

    def wait_event(self, event, timeout_ms):
        if self.stuck.is_set():
            self.in_wait.set()
            self.unstuck.wait(10)
            return False
        return self.frame_ready.wait(timeout_ms / 1000.0)

    def buf_getframe(self, index):
        self.frame_ready.clear()
        self.stamp += 1
        return types.SimpleNamespace(framestamp=self.stamp), np.zeros((4, 4), dtype=np.uint16)

    def _call(self, name):
        self.calls.append(name)
        return True

    def buf_alloc(self, count):
        return self._call("buf_alloc")

    def cap_start(self):
        return self._call("cap_start")

    def cap_stop(self):
        return self._call("cap_stop")

    def buf_release(self):
        return self._call("buf_release")

    def dev_close(self):
        return self._call("dev_close")

    def cap_firetrigger(self):
        self.frame_ready.set()
        return True

    def lasterr(self):
        return 0


def open_camera():
    handles = [FakeDcam("first"), FakeDcam("second")]
    capabilities = camera_hamamatsu.HamamatsuCapabilities(binning_to_resolution={(1, 1): (2304, 2304)})
    opener = mock.patch.object(
        camera_hamamatsu.HamamatsuCamera, "_open", side_effect=[(h, capabilities) for h in handles]
    )
    with opener:
        cam = camera_hamamatsu.HamamatsuCamera(
            CameraConfig(camera_type=CameraVariant.HAMAMATSU, default_pixel_format=CameraPixelFormat.MONO16),
            hw_trigger_fn=None,
            hw_set_strobe_delay_ms_fn=None,
        )
    return cam, handles, opener


def reopen(cam, handles):
    with (
        mock.patch.object(
            camera_hamamatsu.HamamatsuCamera,
            "_open",
            return_value=(handles[1], cam._capabilities),
        ),
        mock.patch.object(camera_hamamatsu.Dcamapi, "uninit") as uninit,
    ):
        cam.reopen()
    return uninit


def frames_after_trigger(cam, count=1, timeout_s=5):
    got = []
    cam.add_frame_callback(got.append)
    for _ in range(count):
        cam._trigger_sent.clear()
        cam.send_trigger()
        deadline = time.time() + timeout_s
        while len(got) < 1 and time.time() < deadline:
            time.sleep(0.01)
    return got


def test_reopen_applies_the_settings_last_applied_and_streams_again():
    cam, handles, _ = open_camera()
    cam.set_pixel_format(CameraPixelFormat.MONO8)
    cam.set_region_of_interest(100, 200, 1024, 512)
    cam.set_sensor_mode("Ultra Quiet")
    cam.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    cam.set_exposure_time(37.0)
    cam.start_streaming()

    uninit = reopen(cam, handles)

    first, second = handles
    assert first.calls[-3:] == ["cap_stop", "buf_release", "dev_close"]
    uninit.assert_called_once()
    assert cam._camera is second
    assert second.props[DCAM_IDPROP.IMAGE_PIXELTYPE] == first.props[DCAM_IDPROP.IMAGE_PIXELTYPE]
    for prop in (DCAM_IDPROP.SUBARRAYHPOS, DCAM_IDPROP.SUBARRAYVPOS, DCAM_IDPROP.SUBARRAYHSIZE):
        assert second.props[prop] == first.props[prop]
    assert second.props[DCAM_IDPROP.READOUTSPEED] == 1
    assert second.props[DCAM_IDPROP.TRIGGERSOURCE] == DCAMPROP.TRIGGERSOURCE.SOFTWARE
    assert second.props[DCAM_IDPROP.EXPOSURETIME] == 0.037
    assert cam.get_is_streaming() and "cap_start" in second.calls
    assert frames_after_trigger(cam), "no frame from the reopened camera"
    cam.close()


def test_reopen_leaves_a_read_thread_stuck_in_dcam_behind_and_it_stops_if_it_ever_returns():
    cam, handles, _ = open_camera()
    cam.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    cam.start_streaming()
    first, second = handles
    first.stuck.set()
    assert first.in_wait.wait(5)  # the read thread is now parked inside DCAM
    old_thread = cam._read_thread
    old_lock = cam._capture_lock
    old_lock.acquire()  # as if it were stuck inside buf_getframe, holding the capture lock

    reopen(cam, handles)  # returns: neither the stuck thread nor its lock blocks it

    assert cam._read_thread is not old_thread and cam._read_thread.is_alive()
    assert frames_after_trigger(cam), "no frame from the reopened camera"

    first.unstuck.set()  # the old call returns at last: that thread stops, it does not read beside the new one
    old_lock.release()
    old_thread.join(5)
    assert not old_thread.is_alive()
    assert cam._read_thread.is_alive()
    cam.close()


def test_a_camera_reopened_without_settings_applied_gets_the_open_defaults():
    cam, handles, _ = open_camera()

    reopen(cam, handles)

    second = handles[1]
    assert DCAM_IDPROP.IMAGE_PIXELTYPE not in second.props_set  # never set: the camera's own default, as at startup
    assert DCAM_IDPROP.SUBARRAYMODE not in second.props_set
    assert second.props[DCAM_IDPROP.EXPOSURETIME] == 0.02  # the 20 ms the driver starts with
    assert not cam.get_is_streaming()
