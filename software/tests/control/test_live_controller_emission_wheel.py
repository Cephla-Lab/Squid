"""LiveController sends a channel's emission filter position to the first configured wheel, not always index 1."""

from typing import Dict, List

import pytest

from control.core.live_controller import LiveController
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
