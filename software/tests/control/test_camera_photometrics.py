"""Tests for the Photometrics (PVCAM) driver that need no camera hardware.

pyvcam imports without a camera attached, so `control.camera_photometrics`
imports fine on this machine. Every test swaps the module's `pvc` / `PVCam`
bindings for fakes so we exercise the real driver logic against a scripted
device list (the same approach as tests/control/test_camera_toupcam.py).
"""

from types import SimpleNamespace

import pytest

import control.camera_photometrics as camera_photometrics
from squid.abc import CameraError
from squid.config import CameraConfig, CameraPixelFormat, CameraVariant

# pyvcam accepts exp_mode as a name string but reads it back as the PVCAM
# integer code; the driver's _TRIGGER_CODE_MAPPING_KINETIX depends on that.
_EXP_MODE_CODES = {
    "Internal Trigger": 1792,
    "Edge Trigger": 2304,
    "Software Trigger Edge": 3072,
}


class FakePVCamera:
    """Stand-in for a pyvcam.camera.Camera as the driver uses it."""

    def __init__(self, name: str):
        self.name = name
        self.is_open = False
        self.exp_res = None
        self.speed_table_index = None
        self.exp_out_mode = None
        self.readout_port = None
        self.exp_time = None
        self.temp_setpoint = None
        self.temp = 0.0
        self._exp_mode_code = _EXP_MODE_CODES["Internal Trigger"]
        self._roi = None

    @property
    def exp_mode(self):
        return self._exp_mode_code

    @exp_mode.setter
    def exp_mode(self, mode_name: str):
        self._exp_mode_code = _EXP_MODE_CODES[mode_name]

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def set_roi(self, offset_x, offset_y, width, height):
        self._roi = (offset_x, offset_y, width, height)

    def shape(self, roi_index):
        if self._roi is not None:
            return (self._roi[2], self._roi[3])
        return (3200, 3200)

    def abort(self):
        pass

    def start_live(self):
        pass

    def finish(self):
        pass

    def poll_frame(self, timeout_ms=0):
        raise TimeoutError("no frames in the fake")

    def sw_trigger(self):
        pass


@pytest.fixture
def fake_pvcam(monkeypatch):
    class FakePvc:
        def __init__(self):
            self.init_calls = 0
            self.uninit_calls = 0

        def init_pvcam(self):
            self.init_calls += 1

        def uninit_pvcam(self):
            self.uninit_calls += 1

    fake_pvc = FakePvc()
    detected = []

    class FakePVCamClass:
        @staticmethod
        def detect_camera():
            yield from detected

    monkeypatch.setattr(camera_photometrics, "pvc", fake_pvc)
    monkeypatch.setattr(camera_photometrics, "PVCam", FakePVCamClass)
    # The PVCAM init refcount is module state; start each test from zero.
    monkeypatch.setattr(camera_photometrics, "_pvcam_user_count", 0, raising=False)
    return SimpleNamespace(pvc=fake_pvc, detected=detected)


def _photometrics_config(device_index=None) -> CameraConfig:
    kwargs = dict(
        camera_type=CameraVariant.PHOTOMETRICS,
        default_pixel_format=CameraPixelFormat.MONO16,
        default_roi=(0, 0, 128, 128),
        default_temperature=0,
    )
    if device_index is not None:
        kwargs["device_index"] = device_index
    return CameraConfig(**kwargs)


def _open_camera(device_index=None) -> "camera_photometrics.PhotometricsCamera":
    return camera_photometrics.PhotometricsCamera(
        _photometrics_config(device_index), hw_trigger_fn=None, hw_set_strobe_delay_ms_fn=None
    )


def test_without_device_index_opens_the_first_detected_camera(fake_pvcam):
    fake_pvcam.detected.extend([FakePVCamera("cam0"), FakePVCamera("cam1")])
    _open_camera()
    assert fake_pvcam.detected[0].is_open
    assert not fake_pvcam.detected[1].is_open


def test_device_index_selects_the_matching_detected_camera(fake_pvcam):
    fake_pvcam.detected.extend([FakePVCamera("cam0"), FakePVCamera("cam1")])
    _open_camera(device_index=1)
    assert fake_pvcam.detected[1].is_open
    assert not fake_pvcam.detected[0].is_open


def test_out_of_range_device_index_raises_camera_error(fake_pvcam):
    fake_pvcam.detected.append(FakePVCamera("cam0"))
    with pytest.raises(CameraError):
        _open_camera(device_index=1)
    # A failed open must release its PVCAM hold so later opens start clean.
    assert fake_pvcam.pvc.uninit_calls == fake_pvcam.pvc.init_calls


def test_no_detected_cameras_raises_camera_error(fake_pvcam):
    with pytest.raises(CameraError):
        _open_camera()
    assert fake_pvcam.pvc.uninit_calls == fake_pvcam.pvc.init_calls


def test_pvcam_uninitializes_only_after_the_last_camera_closes(fake_pvcam):
    fake_pvcam.detected.extend([FakePVCamera("cam0"), FakePVCamera("cam1")])
    first = _open_camera(device_index=0)
    second = _open_camera(device_index=1)
    assert fake_pvcam.pvc.init_calls == 1

    first.close()
    assert fake_pvcam.pvc.uninit_calls == 0
    assert fake_pvcam.detected[0].is_open is False
    assert fake_pvcam.detected[1].is_open is True

    second.close()
    assert fake_pvcam.pvc.uninit_calls == 1
