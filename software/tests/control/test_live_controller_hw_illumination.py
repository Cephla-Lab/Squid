"""set_microscope_mode() must not hold illumination continuously on in hardware-trigger mode.

In HARDWARE trigger mode the MCU strobe gates the light (TTL / LED matrix), so the
live-restart path's turn_off/turn_on pair is a leftover from software-triggered live
view. It kept firmware `illumination_is_on` true across channel switches, which is
what let SET_ILLUMINATION re-light a port mid-strobe (see firmware v1.5).

The pair must still run when:
- the trigger mode is not HARDWARE (software/continuous live),
- the host explicitly holds illumination on (manual toggle),
- the light is not MCU-gated (software shutter, e.g. LDI PC mode), where this
  call is the only thing that turns the lamp on.
"""

import time
from unittest.mock import MagicMock

import pytest

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.microscope
from control._def import STROBE_GUARD_MARGIN_MS, TriggerMode
from control.core.config import ConfigRepository
from control.core.live_controller import LiveController
from control.lighting import ShutterControlMode
from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

ILLUMINATION_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
  D2: 12
  USB1: 0
channels:
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
  - name: Fluorescence 488 nm Ex
    type: epi_illumination
    controller_port: D2
    wavelength_nm: 488
"""


def _channel(name):
    return AcquisitionChannel(
        name=name,
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(illumination_channel=name, intensity=50.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=0.0),
    )


@pytest.fixture
def scope(tmp_path):
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(ILLUMINATION_YAML)
    microscope = control.microscope.Microscope.build_from_global_config(True)
    microscope.config_repo = ConfigRepository(base_path=tmp_path)
    yield microscope
    microscope.close()


@pytest.fixture
def live(scope):
    controller = LiveController(microscope=scope, camera=scope.camera)
    controller.is_live = True
    controller.currentConfiguration = _channel("Fluorescence 405 nm Ex")
    controller._stop_existing_timer = MagicMock()
    controller._start_new_timer = MagicMock()
    controller.turn_on_illumination = MagicMock()
    controller.turn_off_illumination = MagicMock()
    return controller


def _switch(live):
    live.set_microscope_mode(_channel("Fluorescence 488 nm Ex"))


def _assert_toggled(live, expected: bool):
    assert live.turn_off_illumination.call_count == (1 if expected else 0)
    assert live.turn_on_illumination.call_count == (1 if expected else 0)


def test_hardware_trigger_channel_switch_does_not_toggle_illumination(live):
    live.trigger_mode = TriggerMode.HARDWARE
    _switch(live)
    _assert_toggled(live, False)
    live._start_new_timer.assert_called_once()


def test_software_trigger_channel_switch_still_toggles_illumination(live):
    live.trigger_mode = TriggerMode.SOFTWARE
    _switch(live)
    _assert_toggled(live, True)


def test_hardware_trigger_keeps_toggling_when_host_holds_illumination_on(live):
    live.trigger_mode = TriggerMode.HARDWARE
    live.illumination_on = True
    _switch(live)
    _assert_toggled(live, True)


def test_hardware_trigger_keeps_toggling_with_software_shutter(live, scope):
    live.trigger_mode = TriggerMode.HARDWARE
    scope.illumination_controller.shutter_control_mode = ShutterControlMode.Software
    _switch(live)
    _assert_toggled(live, True)


def test_pre_1_3_firmware_keeps_legacy_toggle(live, scope):
    # < 1.3 the ISR doesn't latch its source; the legacy TURN_OFF(old) narrows that
    # firmware's stuck-port race, so the toggle must survive there.
    live.trigger_mode = TriggerMode.HARDWARE
    scope.low_level_drivers.microcontroller.firmware_version = (1, 2)
    _switch(live)
    _assert_toggled(live, True)


def test_not_live_never_toggles(live):
    live.is_live = False
    live.trigger_mode = TriggerMode.SOFTWARE
    _switch(live)
    _assert_toggled(live, False)


# --- strobe-window guard (firmware < 1.5) ---


def test_note_hardware_trigger_records_strobe_window(live):
    live.trigger_mode = TriggerMode.HARDWARE
    before = time.monotonic()
    live.note_hardware_trigger_sent()
    expected = before + (live.camera.get_total_frame_time() + STROBE_GUARD_MARGIN_MS) / 1e3
    assert abs(live._strobe_clear_at - expected) < 0.05


def _elapsed_switch(live):
    start = time.monotonic()
    _switch(live)
    return time.monotonic() - start


def test_set_microscope_mode_waits_out_strobe_window_on_old_firmware(live, scope):
    live.trigger_mode = TriggerMode.HARDWARE
    scope.low_level_drivers.microcontroller.firmware_version = (1, 4)
    live._strobe_clear_at = time.monotonic() + 0.15
    assert _elapsed_switch(live) >= 0.15


def test_set_microscope_mode_does_not_wait_on_v1_5_firmware(live, scope):
    live.trigger_mode = TriggerMode.HARDWARE
    scope.low_level_drivers.microcontroller.firmware_version = (1, 5)
    live._strobe_clear_at = time.monotonic() + 5.0
    assert _elapsed_switch(live) < 1.0


def test_set_microscope_mode_waits_even_after_leaving_hardware_mode(live, scope):
    # A strobe from a just-sent HW trigger can still be in flight after switching
    # trigger modes; the wait is gated on firmware, not the current mode.
    live.trigger_mode = TriggerMode.SOFTWARE
    scope.low_level_drivers.microcontroller.firmware_version = (1, 4)
    live._strobe_clear_at = time.monotonic() + 0.15
    assert _elapsed_switch(live) >= 0.15


def test_set_microscope_mode_does_not_wait_with_no_pending_strobe(live, scope):
    live.trigger_mode = TriggerMode.SOFTWARE
    scope.low_level_drivers.microcontroller.firmware_version = (1, 4)
    assert _elapsed_switch(live) < 1.0


def test_note_hardware_trigger_noop_on_safe_firmware(live, scope):
    live.trigger_mode = TriggerMode.HARDWARE
    scope.low_level_drivers.microcontroller.firmware_version = (1, 5)
    live.note_hardware_trigger_sent()
    assert live._strobe_clear_at == 0.0


def test_send_camera_trigger_records_window_only_in_hardware_mode(live):
    live.camera = MagicMock()
    live.camera.get_exposure_time.return_value = 10.0
    live.camera.get_total_frame_time.return_value = 20.0

    live.trigger_mode = TriggerMode.HARDWARE
    live.send_camera_trigger(10.0)
    live.camera.send_trigger.assert_called_once_with(10.0)
    assert live._strobe_clear_at > time.monotonic()

    live._strobe_clear_at = 0.0
    live.trigger_mode = TriggerMode.SOFTWARE
    live.send_camera_trigger(10.0)
    assert live._strobe_clear_at == 0.0
