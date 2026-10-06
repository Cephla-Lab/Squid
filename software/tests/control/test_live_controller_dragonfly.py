"""Tests for what LiveController sends to a Dragonfly when a channel is selected."""

import pytest

from control.core.live_controller import LiveController
from control.serial_peripherals import Dragonfly_Simulation
from tests.control.spinning_disk_test_utils import build_simulated_microscope, enable_spinning_disk, make_channel


class RecordingDragonfly(Dragonfly_Simulation):
    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def scope(tmp_path, monkeypatch):
    enable_spinning_disk(monkeypatch, dragonfly=True)
    microscope = build_simulated_microscope(tmp_path)
    microscope.addons.dragonfly = RecordingDragonfly()
    yield microscope
    microscope.close()


@pytest.fixture
def live(scope):
    return LiveController(microscope=scope, camera=scope.camera)


def test_filter_position_goes_to_the_port_the_camera_is_on(scope, live):
    scope.addons.dragonfly.set_port_selection_dichroic(4)  # 100% Reflect: camera on port 2
    live.currentConfiguration = make_channel(filter_position=3)

    live.update_illumination()

    assert scope.addons.dragonfly.get_emission_filter(2) == 3
    assert scope.addons.dragonfly.get_emission_filter(1) == 1


def test_channel_without_a_filter_position_leaves_the_wheel_alone(scope, live):
    scope.addons.dragonfly.set_emission_filter(1, 5)
    live.currentConfiguration = make_channel(filter_position=None)

    live.update_illumination()

    assert scope.addons.dragonfly.get_emission_filter(1) == 5


def test_closing_the_microscope_closes_the_dragonfly(scope):
    scope.close()

    assert scope.addons.dragonfly.closed
