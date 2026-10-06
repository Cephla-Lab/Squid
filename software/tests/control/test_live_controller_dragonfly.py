"""Tests for what LiveController sends to a Dragonfly when a channel is selected."""

import pytest

import control._def
import control.core.live_controller
import control.microscope
from control.core.config import ConfigRepository
from control.core.live_controller import LiveController
from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings
from control.serial_peripherals import Dragonfly_Simulation

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


class RecordingDragonfly(Dragonfly_Simulation):
    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


def _channel(filter_position):
    return AcquisitionChannel(
        name="Fluorescence 405 nm Ex",
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(illumination_channel="Fluorescence 405 nm Ex", intensity=10.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=0.0),
        filter_position=filter_position,
    )


@pytest.fixture
def scope(tmp_path, monkeypatch):
    for module in (control._def, control.core.live_controller):
        monkeypatch.setattr(module, "ENABLE_SPINNING_DISK_CONFOCAL", True)
        monkeypatch.setattr(module, "USE_DRAGONFLY", True)
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "machine_configs" / "illumination_channel_config.yaml").write_text(ILLUMINATION_YAML)
    microscope = control.microscope.Microscope.build_from_global_config(True)
    microscope.config_repo = ConfigRepository(base_path=tmp_path)
    microscope.addons.dragonfly = RecordingDragonfly()
    yield microscope
    microscope.close()


@pytest.fixture
def live(scope):
    return LiveController(microscope=scope, camera=scope.camera)


def test_filter_position_goes_to_the_port_the_camera_is_on(scope, live):
    scope.addons.dragonfly.set_port_selection_dichroic(4)  # 100% Reflect: camera on port 2
    live.currentConfiguration = _channel(3)

    live.update_illumination()

    assert scope.addons.dragonfly.get_emission_filter(2) == 3
    assert scope.addons.dragonfly.get_emission_filter(1) == 1


def test_channel_without_a_filter_position_leaves_the_wheel_alone(scope, live):
    scope.addons.dragonfly.set_emission_filter(1, 5)
    live.currentConfiguration = _channel(None)

    live.update_illumination()

    assert scope.addons.dragonfly.get_emission_filter(1) == 5


def test_closing_the_microscope_closes_the_dragonfly(scope):
    scope.close()

    assert scope.addons.dragonfly.closed
