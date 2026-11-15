"""LiveController sends a channel's emission filter position to the first configured wheel, not always index 1."""

from typing import Dict, List

import pytest

import control.core.live_controller
from control.core.live_controller import LiveController
from control.core.sequenced_acquisition import EmissionFilterUnit
from tests.control.spinning_disk_test_utils import build_simulated_microscope, make_channel


class _RecordingWheel:
    """An emission filter wheel on the given indices that records the positions it is sent."""

    def __init__(self, indices: List[int]):
        self._indices = indices
        self.positions: List[Dict[int, int]] = []

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._indices

    def set_delay_offset_ms(self, offset_ms):
        pass

    def set_filter_wheel_position(self, positions: Dict[int, int]):
        missing = [i for i in positions if i not in self._indices]
        if missing:
            raise ValueError(f"Filter wheel index {missing[0]} not found")
        self.positions.append(positions)

    def close(self):
        pass


@pytest.fixture
def scope(tmp_path):
    microscope = build_simulated_microscope(tmp_path)
    yield microscope
    microscope.close()


@pytest.mark.parametrize("indices", [[1], [2], [2, 3]])
def test_channel_filter_position_goes_to_the_first_configured_wheel(scope, indices):
    wheel = _RecordingWheel(indices)
    scope.addons.emission_filter_wheel = wheel
    live = LiveController(microscope=scope, camera=scope.camera)
    live.enable_channel_auto_filter_switching = True
    live.currentConfiguration = make_channel(filter_position=3)

    live.update_illumination()

    assert wheel.positions == [{indices[0]: 3}]


def test_no_configured_wheel_sends_nothing(scope):
    wheel = _RecordingWheel([])
    scope.addons.emission_filter_wheel = wheel
    live = LiveController(microscope=scope, camera=scope.camera)
    live.enable_channel_auto_filter_switching = True
    live.currentConfiguration = make_channel(filter_position=3)

    live.update_illumination()

    assert wheel.positions == []


# --- what a hardware-sequenced acquisition needs to know: which unit a channel switch moves ---------


def test_the_unit_a_channel_switch_moves_is_the_configured_wheel(scope, monkeypatch):
    monkeypatch.setattr(control.core.live_controller, "EMISSION_FILTER_WHEEL_TYPE", "ZABER")
    scope.addons.emission_filter_wheel = _RecordingWheel([1])
    live = LiveController(microscope=scope, camera=scope.camera)
    live.enable_channel_auto_filter_switching = True

    assert live.emission_filter_unit() == EmissionFilterUnit("ZABER emission filter wheel", applies_iris=False)


def test_with_auto_filter_switching_off_no_unit_is_moved(scope):
    scope.addons.emission_filter_wheel = _RecordingWheel([1])
    live = LiveController(microscope=scope, camera=scope.camera)
    live.enable_channel_auto_filter_switching = False

    assert live.emission_filter_unit() is None


def test_without_a_wheel_no_unit_is_moved(scope):
    scope.addons.emission_filter_wheel = None
    live = LiveController(microscope=scope, camera=scope.camera)
    live.enable_channel_auto_filter_switching = True

    assert live.emission_filter_unit() is None


def test_apply_emission_filter_sends_a_channel_position_on_its_own(scope):
    """The sequenced path sets the one position its channels share without selecting a channel
    (that would also rewrite the camera exposure the burst was set up with)."""
    wheel = _RecordingWheel([1])
    scope.addons.emission_filter_wheel = wheel
    live = LiveController(microscope=scope, camera=scope.camera)
    live.enable_channel_auto_filter_switching = True
    exposure_before = scope.camera.get_exposure_time()

    live.apply_emission_filter(make_channel(filter_position=4))

    assert wheel.positions == [{1: 4}]
    assert scope.camera.get_exposure_time() == exposure_before
