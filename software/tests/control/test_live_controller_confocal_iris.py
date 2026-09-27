"""Tests for the X-Light iris values LiveController applies when a channel is selected."""

import pytest

import control.core.live_controller
import control.microscope
from control.core.config import ConfigRepository
from control.core.live_controller import LiveController
from control.models.acquisition_config import (
    AcquisitionChannel,
    CameraSettings,
    ConfocalSettings,
    IlluminationSettings,
)
from control.serial_peripherals import SerialDeviceError, XLight_Simulation

ILLUMINATION_YAML = """\
version: 1
controller_port_mapping:
  D1: 11
channels:
  - name: Fluorescence 405 nm Ex
    type: epi_illumination
    controller_port: D1
    wavelength_nm: 405
"""


class StuckIlluminationIrisXLight(XLight_Simulation):
    """An X-Light whose illumination iris does not acknowledge commands."""

    def set_illumination_iris(self, value):
        raise SerialDeviceError("Max attempts reached without receiving expected response.")


def _channel(confocal_hardware_settings):
    return AcquisitionChannel(
        name="Fluorescence 405 nm Ex",
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(illumination_channel="Fluorescence 405 nm Ex", intensity=10.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=0.0),
        confocal_hardware_settings=confocal_hardware_settings,
    )


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setattr(control.core.live_controller, "ENABLE_SPINNING_DISK_CONFOCAL", True)
    monkeypatch.setattr(control.core.live_controller, "USE_DRAGONFLY", False)
    monkeypatch.setattr(control.core.live_controller, "XLIGHT_ILLUMINATION_IRIS_DEFAULT", 80)
    monkeypatch.setattr(control.core.live_controller, "XLIGHT_EMISSION_IRIS_DEFAULT", 60)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(ILLUMINATION_YAML)
    microscope = control.microscope.Microscope.build_from_global_config(True)
    microscope.config_repo = ConfigRepository(base_path=tmp_path)
    microscope.addons.xlight = XLight_Simulation()
    yield microscope
    microscope.close()


@pytest.fixture
def live(scope):
    return LiveController(microscope=scope, camera=scope.camera)


def test_channel_iris_settings_are_applied(scope, live):
    live.currentConfiguration = _channel(ConfocalSettings(illumination_iris=30.0, emission_iris=40.0))

    live.update_illumination()

    assert scope.addons.xlight.illumination_iris == 30
    assert scope.addons.xlight.emission_iris == 40


def test_channel_without_iris_settings_gets_the_default_iris(scope, live):
    live.currentConfiguration = _channel(None)

    live.update_illumination()

    assert scope.addons.xlight.illumination_iris == 80
    assert scope.addons.xlight.emission_iris == 60


def test_iris_missing_from_channel_settings_gets_its_default(scope, live):
    live.currentConfiguration = _channel(ConfocalSettings(illumination_iris=30.0))

    live.update_illumination()

    assert scope.addons.xlight.illumination_iris == 30
    assert scope.addons.xlight.emission_iris == 60


def test_iris_is_not_commanded_on_hardware_without_one(scope, live):
    scope.addons.xlight.has_illumination_iris_diaphragm = False
    scope.addons.xlight.illumination_iris = 12
    live.currentConfiguration = _channel(None)

    live.update_illumination()

    assert scope.addons.xlight.illumination_iris == 12
    assert scope.addons.xlight.emission_iris == 60


def test_stuck_illumination_iris_does_not_stop_the_emission_iris(scope, live):
    scope.addons.xlight = StuckIlluminationIrisXLight()
    live.currentConfiguration = _channel(ConfocalSettings(illumination_iris=30.0, emission_iris=40.0))

    live.update_illumination()

    assert scope.addons.xlight.emission_iris == 40
