"""Regression test: switching the ToupTek camera to software trigger while its stream is stopped.

Measured on an ITR3CMOS26000KMA: the SDK moves the trigger source (IOCONTROLTYPE TRIGGERSOURCE: 1 = GPIO0,
5 = software) along with OPTION_TRIGGER only while the stream runs. Switched from hardware to software
trigger while stopped, the source stays on GPIO0 and every software trigger is ignored, which aborted
Record + Z-Stack z-stacks started from hardware-trigger live view. The fake SDK below models that.
"""

from squid.abc import CameraAcquisitionMode
import control.toupcam as toupcam
from tests.control.toupcam_test_utils import skeleton_toupcam

_GPIO0, _SOFTWARE = 1, 5


class _FakeSDK:
    def __init__(self):
        self.streaming = False
        self.option_trigger = 1
        self.source = _SOFTWARE

    def put_Option(self, option, value):
        if option == toupcam.TOUPCAM_OPTION_TRIGGER:
            self.option_trigger = value
            if self.streaming and value == 1:
                self.source = _SOFTWARE  # the SDK follows the option only while streaming

    def IoControl(self, line, kind, value):
        if kind == toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE:
            self.source = value
        return 0


def _make_camera():
    # Global reset mode is out of scope here: reported unsupported, so the mode switch under test neither pauses
    # the stream nor writes that option.
    cam = skeleton_toupcam("test_toupcam_trigger_source", _global_reset_enabled=False, _global_reset_supported=False)
    cam._camera = _FakeSDK()
    cam.get_exposure_time = lambda: 20.0
    cam.set_exposure_time = lambda exposure_ms: None
    return cam


def test_software_trigger_set_while_stopped_moves_the_source_off_the_hardware_input():
    cam = _make_camera()
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert cam._camera.source == _GPIO0

    cam._camera.streaming = False
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    assert cam._camera.option_trigger == 1
    assert cam._camera.source == _SOFTWARE


def test_hardware_trigger_still_selects_the_hardware_input():
    cam = _make_camera()
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert cam._camera.option_trigger == 2
    assert cam._camera.source == _GPIO0
