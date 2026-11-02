"""Tests for the X-Light iris values LiveController applies when a channel is selected."""

import pytest

import control.core.live_controller
from control.core.live_controller import LiveController
from control.models.acquisition_config import ConfocalSettings
from control.serial_peripherals import SerialDeviceError, XLight_Simulation
from tests.control.spinning_disk_test_utils import build_simulated_microscope, enable_spinning_disk, make_channel


class StuckIlluminationIrisXLight(XLight_Simulation):
    """An X-Light whose illumination iris does not acknowledge commands."""

    def set_illumination_iris(self, value):
        raise SerialDeviceError("Max attempts reached without receiving expected response.")


def _channel(confocal_hardware_settings):
    return make_channel(confocal_hardware_settings=confocal_hardware_settings)


@pytest.fixture
def scope(tmp_path, monkeypatch):
    enable_spinning_disk(monkeypatch, dragonfly=False)
    monkeypatch.setattr(control.core.live_controller, "XLIGHT_ILLUMINATION_IRIS_DEFAULT", 80)
    monkeypatch.setattr(control.core.live_controller, "XLIGHT_EMISSION_IRIS_DEFAULT", 60)
    microscope = build_simulated_microscope(tmp_path)
    microscope.addons.xlight = XLight_Simulation()
    yield microscope
    microscope.close()


@pytest.fixture
def live(scope):
    return LiveController(microscope=scope, camera=scope.camera)


def test_channel_without_iris_settings_gets_the_default_iris(scope, live):
    live.currentConfiguration = _channel(None)

    live.update_illumination()

    assert scope.addons.xlight.illumination_iris == 80
    assert scope.addons.xlight.emission_iris == 60


def test_only_the_iris_missing_from_channel_settings_gets_its_default(scope, live):
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
