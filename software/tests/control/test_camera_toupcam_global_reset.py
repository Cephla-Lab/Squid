"""Toupcam global reset mode: every row starts exposing at once, so hardware-triggered frames need no rolling-shutter
strobe delay.  It is opt-in through ENABLE_TOUPCAM_GLOBAL_RESET and only used in hardware trigger mode."""

from unittest.mock import MagicMock

import pytest

import control.toupcam as toupcam
import control.camera_toupcam as camera_toupcam
from squid.abc import CameraAcquisitionMode
from tests.control.toupcam_test_utils import capabilities, skeleton_toupcam

_CAPABILITIES = capabilities()
HARDWARE = CameraAcquisitionMode.HARDWARE_TRIGGER
SOFTWARE = CameraAcquisitionMode.SOFTWARE_TRIGGER
CONTINUOUS = CameraAcquisitionMode.CONTINUOUS


def _sdk(resolution):
    """The SDK surface _calculate_strobe_info reads: full bandwidth, max precise framerate 10 fps."""
    sdk = MagicMock()
    sdk.get_Size.return_value = resolution
    sdk.get_Roi.return_value = (0, 0, *resolution)
    options = {toupcam.TOUPCAM_OPTION_BANDWIDTH: 100, toupcam.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE: 100}
    sdk.get_Option.side_effect = lambda option: options[option]
    return sdk


def test_global_reset_needs_no_strobe_delay_and_does_not_touch_the_sdk():
    sdk = _sdk((3104, 2084))

    strobe = camera_toupcam.ToupcamCamera._calculate_strobe_info(
        camera=sdk, pixel_size=1, exposure_time_ms=10, capabilities=_CAPABILITIES, global_reset=True
    )

    assert (strobe.strobe_time_us, strobe.trigger_delay_us) == (0, 0)
    assert sdk.mock_calls == []


def test_rolling_shutter_strobe_is_still_calculated_without_global_reset():
    sdk = _sdk((3104, 2084))

    strobe = camera_toupcam.ToupcamCamera._calculate_strobe_info(
        camera=sdk, pixel_size=1, exposure_time_ms=10, capabilities=_CAPABILITIES
    )

    assert strobe.strobe_time_us > 0
    assert strobe.trigger_delay_us > 0
    sdk.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, 100)


def test_a_resolution_without_row_timing_gets_a_zero_strobe_instead_of_a_division_by_zero():
    """The ITR10CMOS26000KMA's full resolution has no rolling shutter timing table entry."""
    sdk = _sdk((6208, 4168))

    strobe = camera_toupcam.ToupcamCamera._calculate_strobe_info(
        camera=sdk, pixel_size=2, exposure_time_ms=10, capabilities=_CAPABILITIES
    )

    assert (strobe.strobe_time_us, strobe.trigger_delay_us) == (0, 0)
    # continuous mode still needs the precise framerate reset to its maximum
    sdk.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, 100)


def _camera(global_reset_enabled=None):
    """Only the state _set_global_reset reads; None is the state before _configure_camera's first write."""
    return skeleton_toupcam(
        "test_toupcam_global_reset", _global_reset_enabled=global_reset_enabled, _global_reset_supported=True
    )


def test_global_reset_is_off_unless_enabled_in_def(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", False)
    cam = _camera(global_reset_enabled=False)

    cam._set_global_reset(HARDWARE)

    assert cam._global_reset_enabled is False
    cam._camera.put_Option.assert_not_called()


def test_the_first_call_always_writes_so_a_camera_that_came_up_with_global_reset_on_is_turned_off(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", False)
    cam = _camera()  # state unknown, as at startup

    cam._set_global_reset(HARDWARE)

    assert cam._global_reset_enabled is False
    cam._camera.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 0)


def test_global_reset_is_on_in_hardware_trigger_mode_and_off_in_the_other_modes_when_enabled_in_def(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", True)
    cam = _camera()

    cam._set_global_reset(HARDWARE)
    assert cam._global_reset_enabled is True
    cam._camera.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 1)

    cam._camera.put_Option.reset_mock()
    cam._set_global_reset(SOFTWARE)
    assert cam._global_reset_enabled is False
    cam._camera.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 0)

    cam._camera.put_Option.reset_mock()
    cam._set_global_reset(CONTINUOUS)  # already off: nothing to send
    cam._camera.put_Option.assert_not_called()


def test_global_reset_is_changed_with_the_stream_stopped(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", True)
    cam = _camera()
    calls = []
    cam.get_is_streaming = lambda: True
    cam.stop_streaming = lambda: calls.append("stop")
    cam.start_streaming = lambda: calls.append("start")
    cam._camera.put_Option.side_effect = lambda option, value: calls.append(("put", option, value))

    cam._set_global_reset(HARDWARE)

    assert calls == ["stop", ("put", toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 1), "start"]


@pytest.mark.parametrize(
    "flag, error",
    [(True, toupcam.E_NOTIMPL), (False, toupcam.E_INVALIDARG)],
    ids=["not_supported", "any_error_on_the_first_write"],
)
def test_a_camera_that_rejects_the_first_write_is_not_asked_again(monkeypatch, flag, error):
    """The first write is normally the flag-off write of 0 at startup, so no error there may stop the camera from
    opening: any one means unsupported.  A later write only means that on E_NOTIMPL, see the next test."""
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", flag)
    cam = _camera()
    cam._camera.put_Option.side_effect = toupcam.HRESULTException(error)

    cam._set_global_reset(HARDWARE)
    assert cam._global_reset_enabled is False
    assert cam._global_reset_supported is False

    cam._camera.put_Option.reset_mock()
    cam._set_global_reset(HARDWARE)
    cam._camera.put_Option.assert_not_called()


def test_a_later_write_that_fails_keeps_the_last_known_state_and_raises(monkeypatch):
    """The failed write left the camera in global reset, so the driver must not record it as off."""
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", True)
    cam = _camera(global_reset_enabled=True)
    cam._camera.put_Option.side_effect = toupcam.HRESULTException(toupcam.E_BUSY)

    with pytest.raises(toupcam.HRESULTException):
        cam._set_global_reset(CONTINUOUS)

    assert cam._global_reset_enabled is True
    assert cam._global_reset_supported is True
