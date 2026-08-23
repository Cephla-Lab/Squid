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

from unittest.mock import MagicMock

import pytest

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control.microscope
from control._def import TriggerMode
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


def test_hardware_trigger_channel_switch_does_not_toggle_illumination(live):
    live.trigger_mode = TriggerMode.HARDWARE
    _switch(live)
    live.turn_off_illumination.assert_not_called()
    live.turn_on_illumination.assert_not_called()
    live._start_new_timer.assert_called_once()


def test_software_trigger_channel_switch_still_toggles_illumination(live):
    live.trigger_mode = TriggerMode.SOFTWARE
    _switch(live)
    live.turn_off_illumination.assert_called_once()
    live.turn_on_illumination.assert_called_once()


def test_hardware_trigger_keeps_toggling_when_host_holds_illumination_on(live):
    """Manual continuous illumination (user toggle) must survive a channel switch:
    the old source must be turned off and the new one on."""
    live.trigger_mode = TriggerMode.HARDWARE
    live.illumination_on = True
    _switch(live)
    live.turn_off_illumination.assert_called_once()
    live.turn_on_illumination.assert_called_once()


def test_hardware_trigger_keeps_toggling_with_software_shutter(live, scope):
    """With a software shutter (e.g. LDI PC mode) the strobe cannot gate the light,
    so this call is the only thing that turns the lamp on."""
    live.trigger_mode = TriggerMode.HARDWARE
    scope.illumination_controller.shutter_control_mode = ShutterControlMode.Software
    _switch(live)
    live.turn_off_illumination.assert_called_once()
    live.turn_on_illumination.assert_called_once()


def test_not_live_never_toggles(live):
    live.is_live = False
    live.trigger_mode = TriggerMode.SOFTWARE
    _switch(live)
    live.turn_off_illumination.assert_not_called()
    live.turn_on_illumination.assert_not_called()
