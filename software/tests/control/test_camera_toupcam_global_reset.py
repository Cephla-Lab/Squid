"""Toupcam global reset mode: every row starts exposing at once, so triggered frames need no rolling-shutter strobe
delay.  It is opt-in through ENABLE_TOUPCAM_GLOBAL_RESET and only used in the trigger modes."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import squid.logging
import control.toupcam as toupcam
import control.camera_toupcam as camera_toupcam


def _capabilities(**overrides):
    fields = dict(
        binning_to_resolution={(1, 1): (6208, 4168), (2, 2): (3104, 2084)},
        max_bit_depth=16,
        has_fan=False,
        has_TEC=False,
        has_low_noise_mode=False,
        has_black_level=True,
    )
    fields.update(overrides)
    return camera_toupcam.ToupCamCapabilities(**fields)


def _sdk(resolution, bandwidth=100, max_framerate_tenths_fps=100):
    """The SDK surface _calculate_strobe_info reads."""
    sdk = MagicMock()
    sdk.get_Size.return_value = resolution
    sdk.get_Roi.return_value = (0, 0, *resolution)
    options = {
        toupcam.TOUPCAM_OPTION_BANDWIDTH: bandwidth,
        toupcam.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE: max_framerate_tenths_fps,
    }
    sdk.get_Option.side_effect = lambda option: options[option]
    return sdk


def test_global_reset_needs_no_strobe_delay_and_does_not_read_the_sensor_timing():
    sdk = _sdk((3104, 2084))

    strobe = camera_toupcam.ToupcamCamera._calculate_strobe_info(
        camera=sdk, pixel_size=1, exposure_time_ms=10, capabilities=_capabilities(), global_reset=True
    )

    assert (strobe.strobe_time_us, strobe.trigger_delay_us) == (0, 0)
    sdk.get_Size.assert_not_called()
    # continuous mode still needs the precise framerate reset to its maximum
    sdk.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, 100)


def test_rolling_shutter_strobe_is_still_calculated_without_global_reset():
    sdk = _sdk((3104, 2084))

    strobe = camera_toupcam.ToupcamCamera._calculate_strobe_info(
        camera=sdk, pixel_size=1, exposure_time_ms=10, capabilities=_capabilities()
    )

    assert strobe.strobe_time_us > 0
    assert strobe.trigger_delay_us > 0
    sdk.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, 100)


def test_a_resolution_without_row_timing_gets_a_zero_strobe_instead_of_a_division_by_zero():
    """The ITR10CMOS26000KMA's full resolution has no rolling shutter timing table entry."""
    sdk = _sdk((6208, 4168))

    strobe = camera_toupcam.ToupcamCamera._calculate_strobe_info(
        camera=sdk, pixel_size=2, exposure_time_ms=10, capabilities=_capabilities()
    )

    assert (strobe.strobe_time_us, strobe.trigger_delay_us) == (0, 0)


def _camera(global_reset_enabled=False, supported=True):
    """A ToupcamCamera with __init__ skipped: only the state _set_global_reset reads."""
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._camera = MagicMock()
    cam._trigger_lock = threading.RLock()
    cam._global_reset_enabled = global_reset_enabled
    cam._global_reset_supported = supported
    cam._log = squid.logging.get_logger("test_toupcam_global_reset")
    cam.get_is_streaming = lambda: False
    return cam


def test_global_reset_is_off_unless_enabled_in_def(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", False)
    cam = _camera()

    cam._set_global_reset(True)

    assert cam._global_reset_enabled is False
    cam._camera.put_Option.assert_not_called()


def test_def_flag_off_turns_off_a_camera_that_came_up_with_global_reset_on(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", False)
    cam = _camera(global_reset_enabled=True)

    cam._set_global_reset(True)

    assert cam._global_reset_enabled is False
    cam._camera.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 0)


def test_global_reset_follows_the_requested_state_when_enabled_in_def(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", True)
    cam = _camera()

    cam._set_global_reset(True)
    assert cam._global_reset_enabled is True
    cam._camera.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 1)

    cam._camera.put_Option.reset_mock()
    cam._set_global_reset(True)  # already on: nothing to send
    cam._camera.put_Option.assert_not_called()

    cam._set_global_reset(False)
    assert cam._global_reset_enabled is False
    cam._camera.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 0)


def test_global_reset_is_changed_with_the_stream_stopped(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", True)
    cam = _camera()
    calls = []
    cam.get_is_streaming = lambda: True
    cam.stop_streaming = lambda: calls.append("stop")
    cam.start_streaming = lambda: calls.append("start")
    cam._camera.put_Option.side_effect = lambda option, value: calls.append(("put", option, value))

    cam._set_global_reset(True)

    assert calls == ["stop", ("put", toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE, 1), "start"]


def test_a_camera_that_rejects_the_option_is_not_asked_again(monkeypatch):
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", True)
    cam = _camera()
    cam._camera.put_Option.side_effect = toupcam.HRESULTException(0x80004001)  # E_NOTIMPL

    cam._set_global_reset(True)
    assert cam._global_reset_enabled is False
    assert cam._global_reset_supported is False

    cam._camera.put_Option.reset_mock()
    cam._set_global_reset(True)
    cam._camera.put_Option.assert_not_called()
