"""Mocked-SDK tests for the opt-in LEVEL trigger + global reset camera modes.

These cover the two new opt-in settings (``HARDWARE_TRIGGER_GLOBAL_RESET`` and
``CAMERA_TRIGGER_READY_OUTPUT``) and their effect on the ToupCam and Hamamatsu
drivers. No camera and no vendor SDK is required: the Hamamatsu ``dcamapi4``
wrapper loads ``libdcamapi.so`` at import time, so the shared-library load is
stubbed out before importing the driver (the enums and property ids that come
back are the real ones from the wrapper).

The hard rule under test is that with both settings at their default ``False``,
neither driver issues a single camera write it does not issue today -- including
for a user already running ``HARDWARE_TRIGGER_MODE = LEVEL``.
"""

import ctypes
import threading
import types
from configparser import ConfigParser
from unittest import mock

import pytest

import control._def
from control._def import HardwareTriggerMode
from squid.abc import CameraAcquisitionMode, CameraError
from squid.config import CameraConfig, CameraPixelFormat, CameraVariant
import squid.logging

import control.camera_toupcam as camera_toupcam
import control.toupcam as toupcam
from tests.control.toupcam_test_utils import capabilities as toupcam_capabilities

# control/dcamapi4.py does cdll.LoadLibrary('/usr/local/lib/libdcamapi.so') at import
# time. Stub the loader (both the POSIX and the Windows entry point) so the driver -
# and the real DCAM enums/property ids - import on a machine with no DCAM SDK.
_dll_patches = [mock.patch.object(ctypes.cdll, "LoadLibrary", return_value=mock.MagicMock())]
if hasattr(ctypes, "windll"):
    _dll_patches.append(mock.patch.object(ctypes.windll, "LoadLibrary", return_value=mock.MagicMock()))
for _patch in _dll_patches:
    _patch.start()
try:
    import control.camera_hamamatsu as camera_hamamatsu
    from control.dcamapi4 import DCAM_IDPROP, DCAMPROP
finally:
    for _patch in _dll_patches:
        _patch.stop()


# --------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------


def test_global_reset_setting_defaults_off():
    assert control._def.HARDWARE_TRIGGER_GLOBAL_RESET is False


def test_trigger_ready_output_setting_defaults_off():
    assert control._def.CAMERA_TRIGGER_READY_OUTPUT is False


@pytest.mark.parametrize(
    "global_reset, trigger_mode, expected",
    [
        (False, HardwareTriggerMode.EDGE, False),
        (False, HardwareTriggerMode.LEVEL, False),
        (True, HardwareTriggerMode.EDGE, False),
        (True, HardwareTriggerMode.LEVEL, True),
    ],
)
def test_use_level_trigger_global_reset_truth_table(monkeypatch, global_reset, trigger_mode, expected):
    """Global reset is only in effect when it is enabled AND the trigger is LEVEL."""
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", global_reset)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", trigger_mode)

    assert control._def.use_level_trigger_global_reset() is expected


def test_dcam_wrapper_exposes_globalreset_value():
    """The sequencer/driver work depends on this DCAM enum value. Bench-verify on the camera."""
    assert int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.GLOBALRESET) == 5


def test_preferences_dialog_round_trips_both_settings(qtbot, tmp_path, monkeypatch):
    """Settings > Advanced > Hardware Configuration shows and writes both settings."""
    import control.widgets

    config = ConfigParser()
    config.add_section("GENERAL")
    config_path = tmp_path / "configuration_test.ini"

    # Absent from the .ini -> the dialog shows the _def default (off).
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dialog = control.widgets.PreferencesDialog(config, str(config_path))
    qtbot.addWidget(dialog)
    assert dialog.hardware_trigger_global_reset_checkbox.isChecked() is False
    assert dialog.camera_trigger_ready_output_checkbox.isChecked() is False

    dialog.hardware_trigger_global_reset_checkbox.setChecked(True)
    dialog.camera_trigger_ready_output_checkbox.setChecked(True)
    assert dialog._apply_settings() is True

    assert config.get("GENERAL", "hardware_trigger_global_reset") == "true"
    assert config.get("GENERAL", "camera_trigger_ready_output") == "true"

    # ...and a saved .ini is read back into the checkboxes on the next open.
    reopened = control.widgets.PreferencesDialog(config, str(config_path))
    qtbot.addWidget(reopened)
    assert reopened.hardware_trigger_global_reset_checkbox.isChecked() is True
    assert reopened.camera_trigger_ready_output_checkbox.isChecked() is True


# --------------------------------------------------------------------------------------
# ToupCam
# --------------------------------------------------------------------------------------

_GLOBAL_RESET_OPTION = toupcam.TOUPCAM_OPTION_GLOBAL_RESET_MODE


class FakeToupcamSdk:
    """Records every SDK call the driver makes and serves option read-backs."""

    # IoControl GET type -> the SET type whose last value it reports (same io line).
    _IO_GET_TO_SET = {
        toupcam.TOUPCAM_IOCONTROLTYPE_GET_OUTPUTMODE: toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTMODE,
        toupcam.TOUPCAM_IOCONTROLTYPE_GET_OUTPUTINVERTER: toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTINVERTER,
    }

    def __init__(
        self, put_option_error=None, option_readback=None, io_error=None, io_readback=None, io_supported_mode=None
    ):
        self.put_option_calls = []
        self.get_option_calls = []
        self.io_control_calls = []
        self._io_state = {}
        # io line -> GET_SUPPORTEDMODE answer (0x01 input, 0x02 output); every line does both unless overridden.
        self._io_supported_mode = dict(io_supported_mode or {})
        # (io line, control type) -> HRESULTException to raise from IoControl.
        self._io_error = dict(io_error or {})
        # (io line, GET control type) -> value to report, regardless of what was written.
        self._io_readback = dict(io_readback or {})
        self._options = {toupcam.TOUPCAM_OPTION_TRIGGER: 0}
        # Option id -> HRESULTException to raise from put_Option.
        self._put_option_error = dict(put_option_error or {})
        # Option id -> value get_Option should report, regardless of what was written.
        self._option_readback = dict(option_readback or {})

    def put_Option(self, option, value):
        self.put_option_calls.append((option, value))
        if option in self._put_option_error:
            raise self._put_option_error[option]
        self._options[option] = value

    def get_Option(self, option):
        self.get_option_calls.append(option)
        if option in self._option_readback:
            return self._option_readback[option]
        return self._options.get(option, 0)

    def IoControl(self, index, control_type, value):
        self.io_control_calls.append((index, control_type, value))
        if (index, control_type) in self._io_error:
            raise self._io_error[(index, control_type)]
        if control_type == toupcam.TOUPCAM_IOCONTROLTYPE_GET_SUPPORTEDMODE:
            return self._io_supported_mode.get(index, 0x01 | 0x02)
        if control_type in self._IO_GET_TO_SET:
            if (index, control_type) in self._io_readback:
                return self._io_readback[(index, control_type)]
            return self._io_state.get((index, self._IO_GET_TO_SET[control_type]), 0)
        self._io_state[(index, control_type)] = value
        return 0


def _make_toupcam(sdk, strobe_time_us=5000.0, trigger_delay_us=120.0):
    """A ToupcamCamera with only the SDK boundary faked.

    __init__ is skipped so every attribute the code under test touches is set here
    explicitly -- a miss shows up as an AttributeError rather than a silent pass.
    """
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._camera = sdk
    cam._log = squid.logging.get_logger("test_camera_global_reset.toupcam")
    cam._strobe_info = camera_toupcam.StrobeInfo(strobe_time_us=strobe_time_us, trigger_delay_us=trigger_delay_us)
    cam._exposure_time = 20.0
    # The state after production startup: _configure_camera wrote the global reset option off once (the
    # ENABLE_TOUPCAM_GLOBAL_RESET path), and the camera accepted it. The option is written with the
    # stream paused, which takes the trigger lock and asks whether the camera is streaming.
    cam._global_reset_enabled = False
    cam._global_reset_supported = True
    cam._global_reset_verified = False
    cam._trigger_lock = threading.RLock()
    cam.get_is_streaming = lambda: False
    # set_exposure_time() re-derives the strobe info from the live camera, which is far
    # outside what these tests fake. _set_acquisition_mode_imp's trailing call to it is
    # not the behavior under test, so record it instead of running it.
    cam.exposure_resets = []
    cam.set_exposure_time = lambda ms: cam.exposure_resets.append(ms)
    return cam


def _enable_global_reset(monkeypatch, enabled=True, trigger_mode=HardwareTriggerMode.LEVEL):
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", enabled)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", trigger_mode)
    # camera_toupcam does `from control._def import *`, so it holds its own binding for
    # HARDWARE_TRIGGER_MODE. Keep the two in sync, as they always are in production,
    # so these tests exercise the branch the real code takes.
    monkeypatch.setattr(camera_toupcam, "HARDWARE_TRIGGER_MODE", trigger_mode)


def _global_reset_writes(sdk):
    return [call for call in sdk.put_option_calls if call[0] == _GLOBAL_RESET_OPTION]


@pytest.mark.parametrize("trigger_mode", [HardwareTriggerMode.EDGE, HardwareTriggerMode.LEVEL])
def test_toupcam_default_off_never_touches_global_reset_option(monkeypatch, trigger_mode):
    """Default OFF: no new camera writes, including for a user already on LEVEL."""
    _enable_global_reset(monkeypatch, enabled=False, trigger_mode=trigger_mode)
    sdk = FakeToupcamSdk()
    cam = _make_toupcam(sdk)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert _global_reset_writes(sdk) == []
    assert _GLOBAL_RESET_OPTION not in sdk.get_option_calls


def test_toupcam_global_reset_on_writes_and_reads_back(monkeypatch):
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk()
    cam = _make_toupcam(sdk)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert _global_reset_writes(sdk) == [(_GLOBAL_RESET_OPTION, 1)]
    assert _GLOBAL_RESET_OPTION in sdk.get_option_calls
    # The mode must be in effect before the exposure/strobe refresh, otherwise the strobe
    # delay pushed to the microcontroller is the rolling-shutter one.
    assert cam.exposure_resets == [20.0]


def test_toupcam_global_reset_sdk_error_raises(monkeypatch):
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk(put_option_error={_GLOBAL_RESET_OPTION: toupcam.HRESULTException(0x80004001)})
    cam = _make_toupcam(sdk)

    with pytest.raises(CameraError, match="global reset"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_toupcam_global_reset_readback_mismatch_raises(monkeypatch):
    """A camera that accepts the write but does not report the mode must fail loud."""
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk(option_readback={_GLOBAL_RESET_OPTION: 0})
    cam = _make_toupcam(sdk)

    with pytest.raises(CameraError, match="global reset"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_toupcam_global_reset_not_applied_in_software_trigger(monkeypatch):
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk()
    cam = _make_toupcam(sdk)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    assert _global_reset_writes(sdk) == []


def test_toupcam_global_reset_not_applied_in_edge_mode(monkeypatch):
    _enable_global_reset(monkeypatch, trigger_mode=HardwareTriggerMode.EDGE)
    sdk = FakeToupcamSdk()
    cam = _make_toupcam(sdk)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert _global_reset_writes(sdk) == []


def test_toupcam_global_reset_is_turned_off_again_on_return_to_software_trigger(monkeypatch):
    """Two opt-ins put the sensor in global reset (this one and ENABLE_TOUPCAM_GLOBAL_RESET). Whichever
    turned it on, leaving hardware trigger mode must turn it off: in software trigger mode the illumination
    stays on through the readout, and under global reset that shades the image top to bottom."""
    _enable_global_reset(monkeypatch)
    monkeypatch.setattr(camera_toupcam, "ENABLE_TOUPCAM_GLOBAL_RESET", False)
    sdk = FakeToupcamSdk()
    cam = _make_toupcam(sdk)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert sdk.get_Option(_GLOBAL_RESET_OPTION) == 1 and cam._global_reset_enabled is True

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert sdk.get_Option(_GLOBAL_RESET_OPTION) == 0 and cam._global_reset_enabled is False
    assert _global_reset_writes(sdk) == [(_GLOBAL_RESET_OPTION, 1), (_GLOBAL_RESET_OPTION, 0)]


def test_toupcam_strobe_time_default_is_rolling_model(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    sdk = FakeToupcamSdk()
    sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] = 2  # HARDWARE_TRIGGER
    cam = _make_toupcam(sdk, strobe_time_us=5000.0, trigger_delay_us=120.0)

    assert cam.get_strobe_time() == pytest.approx((5000.0 + 120.0) / 1000.0)


def test_toupcam_strobe_time_global_reset_is_trigger_delay_only(monkeypatch):
    """Global reset removes the rows x line-interval wait -- only the trigger latency is left."""
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk()
    cam = _make_toupcam(sdk, strobe_time_us=5000.0, trigger_delay_us=120.0)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert cam.get_strobe_time() == pytest.approx(120.0 / 1000.0)


def test_toupcam_setting_and_trigger_option_alone_do_not_mean_global_reset(monkeypatch):
    """Only a confirmed switch puts the strobe on the global-reset model, not the setting plus trigger option 2."""
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk()
    sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] = 2  # HARDWARE_TRIGGER, but nothing confirmed global reset
    cam = _make_toupcam(sdk, strobe_time_us=5000.0, trigger_delay_us=120.0)

    assert cam._global_reset_active() is False
    assert cam.get_strobe_time() == pytest.approx((5000.0 + 120.0) / 1000.0)


def test_toupcam_global_reset_is_only_active_until_the_next_mode_change(monkeypatch):
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk()
    cam = _make_toupcam(sdk)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert cam._global_reset_active() is True

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert cam._global_reset_active() is False


@pytest.mark.parametrize("previous_option", [0, 1], ids=["from_continuous", "from_software_trigger"])
def test_toupcam_rejected_global_reset_puts_the_previous_trigger_mode_back(monkeypatch, previous_option):
    """No half switch: a camera left on the external trigger would take triggers with timing it never confirmed."""
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk(option_readback={_GLOBAL_RESET_OPTION: 0})  # accepts the write, reports rolling
    sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] = previous_option
    cam = _make_toupcam(sdk)

    with pytest.raises(CameraError, match="global reset"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] == previous_option
    assert cam._global_reset_active() is False
    # ...and the global reset option written on the way in is turned off again, with the camera's state tracked.
    assert _global_reset_writes(sdk) == [(_GLOBAL_RESET_OPTION, 1), (_GLOBAL_RESET_OPTION, 0)]
    assert cam._global_reset_enabled is False


def test_toupcam_rejected_ready_output_puts_the_previous_trigger_mode_back(monkeypatch):
    _enable_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk(io_readback={(_GPIO1, _GET_INVERTER): 0})
    sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] = 1
    cam = _make_toupcam(sdk)

    with pytest.raises(CameraError, match="inverter"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] == 1
    assert cam._global_reset_active() is False
    assert sdk.get_Option(_GLOBAL_RESET_OPTION) == 0 and cam._global_reset_enabled is False


def test_toupcam_global_reset_write_error_at_the_switch_puts_the_previous_trigger_mode_back(monkeypatch):
    """A busy camera refusing the global reset write raises the SDK error; the trigger option still goes back."""
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk(put_option_error={_GLOBAL_RESET_OPTION: toupcam.HRESULTException(toupcam.E_BUSY)})
    sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] = 1
    cam = _make_toupcam(sdk)

    with pytest.raises(toupcam.HRESULTException):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] == 1
    assert cam._global_reset_active() is False


def test_toupcam_strobe_time_global_reset_ignored_outside_hardware_trigger(monkeypatch):
    _enable_global_reset(monkeypatch)
    sdk = FakeToupcamSdk()
    sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] = 1  # SOFTWARE_TRIGGER
    cam = _make_toupcam(sdk, strobe_time_us=5000.0, trigger_delay_us=120.0)

    assert cam.get_strobe_time() == pytest.approx((5000.0 + 120.0) / 1000.0)


# --------------------------------------------------------------------------------------
# Hamamatsu
# --------------------------------------------------------------------------------------

_TRIGGERACTIVE = int(DCAM_IDPROP.TRIGGERACTIVE)
_GLOBALEXPOSURE = int(DCAM_IDPROP.TRIGGER_GLOBALEXPOSURE)
_OUT_KIND = int(DCAM_IDPROP.OUTPUTTRIGGER_KIND)
_OUT_POLARITY = int(DCAM_IDPROP.OUTPUTTRIGGER_POLARITY)
_NEW_HAMAMATSU_PROPS = (_TRIGGERACTIVE, _GLOBALEXPOSURE, _OUT_KIND, _OUT_POLARITY)


# --- ToupCam: trigger-ready output ("Frame Trigger Wait" on GPIO1) -----------------------------

_GPIO1 = 3  # IoControl line numbers: 0 opto in, 1 opto out, 2 GPIO0, 3 GPIO1
_SET_MODE = toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTMODE
_GET_MODE = toupcam.TOUPCAM_IOCONTROLTYPE_GET_OUTPUTMODE
_SET_INVERTER = toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTINVERTER
_GET_INVERTER = toupcam.TOUPCAM_IOCONTROLTYPE_GET_OUTPUTINVERTER
_FRAME_TRIGGER_WAIT = 0

# What master's driver sends today, per trigger mode. The opt-in must not change a single call.
_TODAYS_IO_CALLS = {
    HardwareTriggerMode.EDGE: [
        (1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 1),
        (_GPIO1, _SET_MODE, _FRAME_TRIGGER_WAIT),
        (_GPIO1, _SET_INVERTER, 0),
    ],
    HardwareTriggerMode.LEVEL: [
        (0, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 4),
        (2, toupcam.TOUPCAM_IOCONTROLTYPE_SET_GPIODIR, 0),
        (2, toupcam.TOUPCAM_IOCONTROLTYPE_SET_PWMSOURCE, 1),
    ],
}


def _last_written(sdk, line, control_type):
    values = [value for index, kind, value in sdk.io_control_calls if (index, kind) == (line, control_type)]
    return values[-1] if values else None


@pytest.mark.parametrize("trigger_mode", [HardwareTriggerMode.EDGE, HardwareTriggerMode.LEVEL])
def test_toupcam_trigger_ready_output_off_sends_exactly_todays_io_calls(monkeypatch, trigger_mode):
    """Characterization: passes before and after the change. It pins the defaults-never-change rule."""
    _enable_global_reset(monkeypatch, enabled=False, trigger_mode=trigger_mode)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    sdk = FakeToupcamSdk()

    _make_toupcam(sdk)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert sdk.io_control_calls == _TODAYS_IO_CALLS[trigger_mode]


@pytest.mark.parametrize("trigger_mode", [HardwareTriggerMode.EDGE, HardwareTriggerMode.LEVEL])
def test_toupcam_trigger_ready_output_on_is_frame_trigger_wait_active_low(monkeypatch, trigger_mode):
    """In LEVEL mode - the one the hardware sequencer needs - master never configures GPIO1 at all.
    Active low for the same reason as the Hamamatsu: the controller's ready input is pulled up."""
    _enable_global_reset(monkeypatch, enabled=False, trigger_mode=trigger_mode)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk()

    _make_toupcam(sdk)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert _last_written(sdk, _GPIO1, _SET_MODE) == _FRAME_TRIGGER_WAIT
    assert _last_written(sdk, _GPIO1, _SET_INVERTER) == 1
    # ...and the camera was asked to confirm both
    assert (_GPIO1, _GET_MODE, 0) in sdk.io_control_calls
    assert (_GPIO1, _GET_INVERTER, 0) in sdk.io_control_calls


def test_toupcam_trigger_ready_output_inverter_readback_mismatch_raises(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk(io_readback={(_GPIO1, _GET_INVERTER): 0})

    with pytest.raises(CameraError, match="inverter"):
        _make_toupcam(sdk)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_toupcam_trigger_ready_output_mode_readback_mismatch_raises(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk(io_readback={(_GPIO1, _GET_MODE): 2})  # the camera says "Strobe"

    with pytest.raises(CameraError, match="Frame Trigger Wait"):
        _make_toupcam(sdk)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_toupcam_trigger_ready_output_sdk_error_raises(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk(io_error={(_GPIO1, _SET_INVERTER): toupcam.HRESULTException(0x80004001)})
    cam = _make_toupcam(sdk)
    # In EDGE mode master's own inverter write would hit the injected error first; LEVEL isolates ours.
    with pytest.raises(CameraError, match="trigger-ready output"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_toupcam_trigger_ready_output_not_configured_in_software_trigger(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk()

    _make_toupcam(sdk)._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    assert _last_written(sdk, _GPIO1, _SET_INVERTER) is None


# --- ToupCam: an opt-in the camera cannot do stops it from opening ---------------------------


def _open_toupcam(monkeypatch, sdk, startup_mode=CameraAcquisitionMode.SOFTWARE_TRIGGER):
    """Run the real ToupcamCamera.__init__ over the fake SDK.

    Only the camera-wide setup that is not under test is stubbed. _configure_camera is reduced to the part that
    finds out whether global reset is supported (its first global reset write), so __init__'s own order is real.
    Returns the list the stubbed stream start appends to.
    """
    sdk._options[toupcam.TOUPCAM_OPTION_TRIGGER] = {
        CameraAcquisitionMode.CONTINUOUS: 0,
        CameraAcquisitionMode.SOFTWARE_TRIGGER: 1,
        CameraAcquisitionMode.HARDWARE_TRIGGER: 2,
    }[startup_mode]
    cls = camera_toupcam.ToupcamCamera
    monkeypatch.setattr(cls, "_open", staticmethod(lambda index=None, sn=None: (sdk, toupcam_capabilities())))
    monkeypatch.setattr(cls, "_get_raw_exposure_time", lambda self: 20.0)
    monkeypatch.setattr(cls, "_check_temperature", lambda self: None)
    monkeypatch.setattr(cls, "_configure_camera", lambda self: self._set_global_reset(self.get_acquisition_mode()))
    monkeypatch.setattr(cls, "_update_internal_settings", lambda self, send_exposure=True: None)
    monkeypatch.setattr(cls, "_log_startup_option_dump", lambda self: None)
    streams_started = []
    monkeypatch.setattr(cls, "_start_raw_camera_stream", lambda self: streams_started.append(True))
    cls(
        CameraConfig(camera_type=CameraVariant.TOUPCAM, default_pixel_format=CameraPixelFormat.MONO16),
        hw_trigger_fn=None,
        hw_set_strobe_delay_ms_fn=None,
    )
    return streams_started


def test_toupcam_without_global_reset_does_not_open_with_the_opt_in_on(monkeypatch):
    _enable_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    sdk = FakeToupcamSdk(put_option_error={_GLOBAL_RESET_OPTION: toupcam.HRESULTException(0x80004001)})

    with pytest.raises(CameraError, match="Turn off HARDWARE_TRIGGER_GLOBAL_RESET"):
        _open_toupcam(monkeypatch, sdk)


def test_toupcam_without_an_output_capable_gpio1_does_not_open_with_the_ready_output_on(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk(io_supported_mode={_GPIO1: 0x01})  # input only

    with pytest.raises(CameraError, match="Turn off CAMERA_TRIGGER_READY_OUTPUT"):
        _open_toupcam(monkeypatch, sdk)


def test_toupcam_gpio1_support_query_error_does_not_open_with_the_ready_output_on(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    supported_mode = toupcam.TOUPCAM_IOCONTROLTYPE_GET_SUPPORTEDMODE
    sdk = FakeToupcamSdk(io_error={(_GPIO1, supported_mode): toupcam.HRESULTException(0x80004001)})

    with pytest.raises(CameraError, match="Turn off CAMERA_TRIGGER_READY_OUTPUT"):
        _open_toupcam(monkeypatch, sdk)


def test_toupcam_that_supports_both_opt_ins_opens_and_starts_its_stream(monkeypatch):
    _enable_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    sdk = FakeToupcamSdk()

    assert _open_toupcam(monkeypatch, sdk) == [True]


def test_toupcam_opens_without_asking_about_gpio1_with_both_opt_ins_off(monkeypatch):
    _enable_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    sdk = FakeToupcamSdk(put_option_error={_GLOBAL_RESET_OPTION: toupcam.HRESULTException(0x80004001)})

    assert _open_toupcam(monkeypatch, sdk) == [True]
    assert sdk.io_control_calls == []


class FakeDcam:
    """A DCAM handle backed by a property dict, so read-back is real."""

    def __init__(self, props=None, attrs=None, set_fails=(), readback=None):
        self._props = {
            int(DCAM_IDPROP.TRIGGERSOURCE): int(DCAMPROP.TRIGGERSOURCE.INTERNAL),
            int(DCAM_IDPROP.TRIGGERDELAY): 0.0,
            int(DCAM_IDPROP.INTERNAL_LINEINTERVAL): 4.4e-6,
            int(DCAM_IDPROP.EXPOSURETIME): 0.02,
            # The camera's own trigger timing before anything changes it (EDGE, the DCAM default).
            _TRIGGERACTIVE: int(DCAMPROP.TRIGGERACTIVE.EDGE),
            _GLOBALEXPOSURE: int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.DELAYED),
        }
        self._props.update({int(k): v for k, v in (props or {}).items()})
        # Property id -> attr object (valuemin/valuemax); anything else is unsupported.
        self._attrs = {
            _TRIGGERACTIVE: types.SimpleNamespace(valuemin=1.0, valuemax=4.0, valuestep=1.0),
            _GLOBALEXPOSURE: types.SimpleNamespace(valuemin=1.0, valuemax=5.0, valuestep=1.0),
            _OUT_KIND: types.SimpleNamespace(valuemin=1.0, valuemax=6.0, valuestep=1.0),
            _OUT_POLARITY: types.SimpleNamespace(valuemin=1.0, valuemax=2.0, valuestep=1.0),
        }
        self._attrs.update({int(k): v for k, v in (attrs or {}).items()})
        self._set_fails = {int(p) for p in set_fails}
        self._readback = {int(k): v for k, v in (readback or {}).items()}
        self.set_calls = []
        self.get_calls = []
        self.getattr_calls = []

    def prop_setvalue(self, idprop, value):
        self.set_calls.append((int(idprop), value))
        if int(idprop) in self._set_fails:
            return False
        self._props[int(idprop)] = value
        return True

    def prop_getvalue(self, idprop):
        self.get_calls.append(int(idprop))
        if int(idprop) in self._readback:
            return self._readback[int(idprop)]
        if int(idprop) not in self._props:
            return False
        return self._props[int(idprop)]

    def prop_getattr(self, idprop):
        self.getattr_calls.append(int(idprop))
        return self._attrs.get(int(idprop), False)

    def lasterr(self):
        return -1

    def written(self, idprop):
        return [value for (prop, value) in self.set_calls if prop == int(idprop)]


def _make_hamamatsu(dcam):
    cam = object.__new__(camera_hamamatsu.HamamatsuCamera)
    cam._camera = dcam
    cam._log = squid.logging.get_logger("test_camera_global_reset.hamamatsu")
    cam._capabilities = camera_hamamatsu.HamamatsuCapabilities(binning_to_resolution={(1, 1): (2304, 2304)})
    cam._exposure_time_ms = 20.0
    cam._trigger_sent = threading.Event()
    cam._is_streaming = threading.Event()  # not streaming -> _pause_streaming is a no-op
    cam._trigger_lock = threading.RLock()
    cam._global_reset_verified = False
    cam._trigger_timing_before_global_reset = None
    # set_exposure_time() pushes the strobe delay to the microcontroller in hardware
    # trigger mode; record what it pushed so tests can assert the value the MCU is told.
    cam.strobe_delays_pushed = []
    cam._hw_set_strobe_delay_ms_fn = cam.strobe_delays_pushed.append
    return cam


def _make_streaming_hamamatsu(dcam):
    """A Hamamatsu that is streaming, so _pause_streaming really stops and restarts it (recorded in .events)."""
    cam = _make_hamamatsu(dcam)
    cam.events = []
    cam._is_streaming.set()

    def stop():
        cam.events.append(("stop", int(dcam._props[int(DCAM_IDPROP.TRIGGERSOURCE)])))
        cam._is_streaming.clear()

    def start():
        cam.events.append(("start", int(dcam._props[int(DCAM_IDPROP.TRIGGERSOURCE)])))
        cam._is_streaming.set()

    cam.stop_streaming = stop
    cam.start_streaming = start
    return cam


def _enable_hamamatsu_global_reset(monkeypatch, enabled=True, trigger_mode=HardwareTriggerMode.LEVEL):
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", enabled)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", trigger_mode)


def _new_prop_writes(dcam):
    return [call for call in dcam.set_calls if call[0] in _NEW_HAMAMATSU_PROPS]


@pytest.mark.parametrize("trigger_mode", [HardwareTriggerMode.EDGE, HardwareTriggerMode.LEVEL])
def test_hamamatsu_defaults_off_never_write_new_props(monkeypatch, trigger_mode):
    """Default OFF: the driver writes exactly the properties it writes today."""
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False, trigger_mode=trigger_mode)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert _new_prop_writes(dcam) == []
    assert sorted({prop for (prop, _) in dcam.set_calls}) == sorted(
        {
            int(DCAM_IDPROP.TRIGGERPOLARITY),
            int(DCAM_IDPROP.TRIGGERSOURCE),
            int(DCAM_IDPROP.EXPOSURETIME),
        }
    )


def test_hamamatsu_global_reset_on_sets_level_and_globalreset(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam(props={DCAM_IDPROP.TRIGGERDELAY: 0.0005})
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert dcam.written(_TRIGGERACTIVE) == [int(DCAMPROP.TRIGGERACTIVE.LEVEL)]
    assert dcam.written(_GLOBALEXPOSURE) == [int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.GLOBALRESET)]
    # TRIGGERACTIVE must be in effect before the global-exposure refinement is applied.
    props_in_order = [prop for (prop, _) in dcam.set_calls if prop in (_TRIGGERACTIVE, _GLOBALEXPOSURE)]
    assert props_in_order == [_TRIGGERACTIVE, _GLOBALEXPOSURE]
    # ...and the mode must be in effect before the exposure/strobe refresh, so the
    # microcontroller is told the global-reset strobe delay (trigger delay only) rather
    # than the rolling rows x line-interval one.
    assert cam.strobe_delays_pushed == [pytest.approx(0.5)]


def test_hamamatsu_global_reset_set_failure_raises(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    dcam = FakeDcam(set_fails=[_GLOBALEXPOSURE])
    cam = _make_hamamatsu(dcam)

    with pytest.raises(CameraError, match="TRIGGER_GLOBALEXPOSURE"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_hamamatsu_global_reset_readback_mismatch_raises(monkeypatch):
    """DCAM can clip a mode value silently; the read-back is what proves the mode."""
    _enable_hamamatsu_global_reset(monkeypatch)
    dcam = FakeDcam(readback={_GLOBALEXPOSURE: int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.DELAYED)})
    cam = _make_hamamatsu(dcam)

    with pytest.raises(CameraError, match="TRIGGER_GLOBALEXPOSURE"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_hamamatsu_trigger_active_readback_mismatch_raises(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    dcam = FakeDcam(readback={_TRIGGERACTIVE: int(DCAMPROP.TRIGGERACTIVE.EDGE)})
    cam = _make_hamamatsu(dcam)

    with pytest.raises(CameraError, match="TRIGGERACTIVE"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_hamamatsu_global_reset_not_applied_in_software_trigger(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    assert _new_prop_writes(dcam) == []


def test_hamamatsu_global_reset_not_applied_in_edge_mode(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, trigger_mode=HardwareTriggerMode.EDGE)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert _new_prop_writes(dcam) == []


def test_hamamatsu_strobe_time_default_is_rolling_model(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    dcam = FakeDcam(props={DCAM_IDPROP.TRIGGERSOURCE: int(DCAMPROP.TRIGGERSOURCE.EXTERNAL)})
    cam = _make_hamamatsu(dcam)

    expected_ms = (4.4e-6 * 2304 + 0.0) * 1000.0
    assert cam.get_strobe_time() == pytest.approx(expected_ms)


def test_hamamatsu_strobe_time_global_reset_is_trigger_delay_only(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam(props={DCAM_IDPROP.TRIGGERDELAY: 0.0005})
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert cam.get_strobe_time() == pytest.approx(0.5)


def test_hamamatsu_setting_and_trigger_source_alone_do_not_mean_global_reset(monkeypatch):
    """Only a confirmed switch puts the strobe on the global-reset model, not the setting plus EXTERNAL."""
    _enable_hamamatsu_global_reset(monkeypatch)
    dcam = FakeDcam(props={DCAM_IDPROP.TRIGGERSOURCE: int(DCAMPROP.TRIGGERSOURCE.EXTERNAL)})
    cam = _make_hamamatsu(dcam)

    assert cam._global_reset_active() is False
    assert cam.get_strobe_time() == pytest.approx((4.4e-6 * 2304 + 0.0) * 1000.0)


def test_hamamatsu_global_reset_is_only_active_until_the_next_mode_change(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    cam = _make_hamamatsu(FakeDcam())

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert cam._global_reset_active() is True

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert cam._global_reset_active() is False


# --- Hamamatsu: a failed hardware trigger setup leaves the camera as it was -------------------

_SOURCE = int(DCAM_IDPROP.TRIGGERSOURCE)
_EXTERNAL = int(DCAMPROP.TRIGGERSOURCE.EXTERNAL)
_SOFTWARE = int(DCAMPROP.TRIGGERSOURCE.SOFTWARE)


@pytest.mark.parametrize(
    "fake_kwargs, ready_output, error",
    [
        (
            {"readback": {_GLOBALEXPOSURE: int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.DELAYED)}},
            False,
            "TRIGGER_GLOBALEXPOSURE",
        ),
        ({"set_fails": [_GLOBALEXPOSURE]}, False, "TRIGGER_GLOBALEXPOSURE"),
        ({"readback": {_OUT_POLARITY: int(DCAMPROP.OUTPUTTRIGGER_POLARITY.POSITIVE)}}, True, "OUTPUTTRIGGER_POLARITY"),
    ],
    ids=["global_reset_not_taken", "global_reset_write_refused", "ready_output_not_taken"],
)
def test_hamamatsu_failed_setup_puts_the_previous_trigger_mode_back(monkeypatch, fake_kwargs, ready_output, error):
    """No half switch: left on the external trigger (and, for a refused global reset, in LEVEL), the camera would
    take triggers with timing it never confirmed. Streaming comes back in the mode it was in."""
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", ready_output)
    dcam = FakeDcam(props={DCAM_IDPROP.TRIGGERSOURCE: _SOFTWARE}, **fake_kwargs)
    cam = _make_streaming_hamamatsu(dcam)

    with pytest.raises(CameraError, match=error):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert cam.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER
    assert cam.events == [("stop", _SOFTWARE), ("start", _SOFTWARE)]
    assert cam._global_reset_active() is False
    assert dcam._props[_TRIGGERACTIVE] == int(DCAMPROP.TRIGGERACTIVE.EDGE)
    assert cam.strobe_delays_pushed == []  # the strobe is only pushed in hardware trigger mode


def test_hamamatsu_failed_setup_from_hardware_trigger_refreshes_the_strobe_for_the_rolling_sensor(monkeypatch):
    """Re-applying hardware trigger mode (the confocal re-arm) can fail too: the sensor is then back on its own
    timing, so the strobe pushed to the controller must be the rolling one again."""
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    dcam._readback[_GLOBALEXPOSURE] = int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.DELAYED)
    cam.strobe_delays_pushed.clear()

    with pytest.raises(CameraError, match="TRIGGER_GLOBALEXPOSURE"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert int(dcam._props[_SOURCE]) == _EXTERNAL
    assert dcam._props[_TRIGGERACTIVE] == int(DCAMPROP.TRIGGERACTIVE.EDGE)
    assert cam._global_reset_active() is False
    assert cam.strobe_delays_pushed == [pytest.approx((4.4e-6 * 2304 + 0.0) * 1000.0)]


# --- Hamamatsu: leaving hardware trigger puts the camera's own trigger timing back -------------


@pytest.mark.parametrize("next_mode", [CameraAcquisitionMode.SOFTWARE_TRIGGER, CameraAcquisitionMode.CONTINUOUS])
def test_hamamatsu_leaving_hardware_trigger_restores_trigger_active_and_global_exposure(monkeypatch, next_mode):
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    # Hardware trigger mode re-applied (as the confocal re-arm does) must not record its own values as the originals.
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    cam._set_acquisition_mode_imp(next_mode)

    assert dcam._props[_TRIGGERACTIVE] == int(DCAMPROP.TRIGGERACTIVE.EDGE)
    assert dcam._props[_GLOBALEXPOSURE] == int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.DELAYED)

    # Restored once: the next switch has nothing left to put back.
    dcam.set_calls.clear()
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert _new_prop_writes(dcam) == []


def test_hamamatsu_round_trip_with_the_opt_ins_off_neither_reads_nor_writes_trigger_timing(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    for mode in (
        CameraAcquisitionMode.HARDWARE_TRIGGER,
        CameraAcquisitionMode.SOFTWARE_TRIGGER,
        CameraAcquisitionMode.HARDWARE_TRIGGER,
        CameraAcquisitionMode.CONTINUOUS,
    ):
        cam._set_acquisition_mode_imp(mode)

    assert _new_prop_writes(dcam) == []
    assert [prop for prop in dcam.get_calls if prop in _NEW_HAMAMATSU_PROPS] == []
    assert [prop for prop in dcam.getattr_calls if prop in _NEW_HAMAMATSU_PROPS] == []


# --- Hamamatsu: an opt-in the camera cannot do stops it from opening --------------------------


def _open_hamamatsu(monkeypatch, dcam):
    """Run the real HamamatsuCamera.__init__ over the fake DCAM handle."""
    capabilities = camera_hamamatsu.HamamatsuCapabilities(binning_to_resolution={(1, 1): (2304, 2304)})
    monkeypatch.setattr(
        camera_hamamatsu.HamamatsuCamera, "_open", staticmethod(lambda index=None, sn=None: (dcam, capabilities))
    )
    return camera_hamamatsu.HamamatsuCamera(
        CameraConfig(camera_type=CameraVariant.HAMAMATSU, default_pixel_format=CameraPixelFormat.MONO16),
        hw_trigger_fn=None,
        hw_set_strobe_delay_ms_fn=None,
    )


@pytest.mark.parametrize(
    "unsupported",
    [
        {"pop": _GLOBALEXPOSURE},
        {_GLOBALEXPOSURE: types.SimpleNamespace(valuemin=1.0, valuemax=4.0, valuestep=1.0)},
        {"pop": _TRIGGERACTIVE},
        {_TRIGGERACTIVE: types.SimpleNamespace(valuemin=1.0, valuemax=1.0, valuestep=1.0)},
    ],
    ids=["no_global_exposure", "no_globalreset_value", "no_trigger_active", "no_level_trigger"],
)
def test_hamamatsu_without_global_reset_does_not_open_with_the_opt_in_on(monkeypatch, unsupported):
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    if "pop" in unsupported:
        dcam._attrs.pop(unsupported["pop"])
    else:
        dcam._attrs.update(unsupported)

    with pytest.raises(CameraError, match="Turn off HARDWARE_TRIGGER_GLOBAL_RESET"):
        _open_hamamatsu(monkeypatch, dcam)


@pytest.mark.parametrize(
    "unsupported",
    [
        {"pop": _OUT_KIND},
        {_OUT_KIND: types.SimpleNamespace(valuemin=1.0, valuemax=3.0, valuestep=1.0)},
        {"pop": _OUT_POLARITY},
    ],
    ids=["no_output_trigger", "no_trigger_ready_kind", "no_output_polarity"],
)
def test_hamamatsu_without_a_trigger_ready_output_does_not_open_with_the_opt_in_on(monkeypatch, unsupported):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    dcam = FakeDcam()
    if "pop" in unsupported:
        dcam._attrs.pop(unsupported["pop"])
    else:
        dcam._attrs.update(unsupported)

    with pytest.raises(CameraError, match="Turn off CAMERA_TRIGGER_READY_OUTPUT"):
        _open_hamamatsu(monkeypatch, dcam)


def test_hamamatsu_that_supports_both_opt_ins_opens(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)

    cam = _open_hamamatsu(monkeypatch, FakeDcam())

    assert cam._global_reset_active() is False  # nothing is confirmed until hardware trigger mode is set


def test_hamamatsu_opens_without_asking_about_the_new_properties_with_both_opt_ins_off(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    dcam._attrs.clear()  # a camera with none of them still opens

    _open_hamamatsu(monkeypatch, dcam)

    assert [prop for prop in dcam.getattr_calls if prop in _NEW_HAMAMATSU_PROPS] == []
    assert _new_prop_writes(dcam) == []


def test_hamamatsu_strobe_time_global_reset_ignored_outside_hardware_trigger(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch)
    dcam = FakeDcam(props={DCAM_IDPROP.TRIGGERSOURCE: int(DCAMPROP.TRIGGERSOURCE.SOFTWARE)})
    cam = _make_hamamatsu(dcam)

    expected_ms = (4.4e-6 * 2304 + 0.0) * 1000.0
    assert cam.get_strobe_time() == pytest.approx(expected_ms)


# --------------------------------------------------------------------------------------
# Hamamatsu trigger-ready output
# --------------------------------------------------------------------------------------


def test_hamamatsu_trigger_ready_output_off_leaves_output_untouched(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert dcam.written(_OUT_KIND) == []
    assert dcam.written(_OUT_POLARITY) == []


def test_hamamatsu_trigger_ready_output_on_is_active_low_and_reads_back(monkeypatch):
    """The controller's ready input is pulled UP (measured: ~4.7 k to 3.3 V), so an unplugged cable
    reads HIGH. HIGH must therefore mean NOT ready: the camera signals ready by driving LOW."""
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert dcam.written(_OUT_KIND) == [int(DCAMPROP.OUTPUTTRIGGER_KIND.TRIGGERREADY)]
    assert dcam.written(_OUT_POLARITY) == [int(DCAMPROP.OUTPUTTRIGGER_POLARITY.NEGATIVE)]


def test_hamamatsu_trigger_ready_output_is_independent_of_global_reset(monkeypatch):
    """The ready output is its own setting: on with global reset off, both are configured."""
    _enable_hamamatsu_global_reset(monkeypatch, enabled=True)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)

    assert dcam.written(_GLOBALEXPOSURE) == [int(DCAMPROP.TRIGGER_GLOBALEXPOSURE.GLOBALRESET)]
    assert dcam.written(_OUT_KIND) == [int(DCAMPROP.OUTPUTTRIGGER_KIND.TRIGGERREADY)]


def test_hamamatsu_trigger_ready_output_set_failure_raises(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    dcam = FakeDcam(set_fails=[_OUT_KIND])
    cam = _make_hamamatsu(dcam)

    with pytest.raises(CameraError, match="OUTPUTTRIGGER_KIND"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_hamamatsu_trigger_ready_output_readback_mismatch_raises(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    dcam = FakeDcam(readback={_OUT_POLARITY: int(DCAMPROP.OUTPUTTRIGGER_POLARITY.POSITIVE)})
    cam = _make_hamamatsu(dcam)

    with pytest.raises(CameraError, match="OUTPUTTRIGGER_POLARITY"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_hamamatsu_trigger_ready_output_not_configured_in_software_trigger(monkeypatch):
    _enable_hamamatsu_global_reset(monkeypatch, enabled=False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", True)
    dcam = FakeDcam()
    cam = _make_hamamatsu(dcam)

    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    assert dcam.written(_OUT_KIND) == []
    assert dcam.written(_OUT_POLARITY) == []
