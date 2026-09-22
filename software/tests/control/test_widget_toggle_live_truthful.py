"""The live toggle buttons derive their state from liveController.is_live in a finally
block, so a start_live()/stop_live() that raises cannot leave a button lying
(see LiveController.trigger_acquisition for the MCU wedge this protects against).
"""

from unittest.mock import MagicMock

import pytest

from control.widgets import LaserAutofocusSettingWidget, LiveControlWidget


def _widget_stub(is_live_after_call: bool) -> MagicMock:
    """Widget-shaped fake self exposing what the toggle_live methods touch."""
    stub = MagicMock()
    stub.liveController.is_live = is_live_after_call
    return stub


# (widget class, button text while live, button text while idle, companion button enabled only while idle)
CASES = [
    (LiveControlWidget, "Stop", "Live", "btn_snap"),
    (LaserAutofocusSettingWidget, "Stop Live", "Start Live", "run_spot_detection_button"),
]


def _assert_button_state(stub, is_live, live_text, idle_text, companion):
    stub.btn_live.setText.assert_called_with(live_text if is_live else idle_text)
    stub.btn_live.setChecked.assert_called_with(is_live)
    getattr(stub, companion).setEnabled.assert_called_with(not is_live)


@pytest.mark.parametrize("cls,live_text,idle_text,companion", CASES)
@pytest.mark.parametrize("pressed", [True, False])
def test_button_follows_is_live_when_the_controller_raises(cls, live_text, idle_text, companion, pressed):
    # Real start_live()/stop_live() flip is_live before anything that can raise.
    stub = _widget_stub(is_live_after_call=pressed)
    getattr(stub.liveController, "start_live" if pressed else "stop_live").side_effect = TimeoutError("mcu busy")

    with pytest.raises(TimeoutError):
        cls.toggle_live(stub, pressed)

    _assert_button_state(stub, pressed, live_text, idle_text, companion)


@pytest.mark.parametrize("cls,live_text,idle_text,companion", CASES)
@pytest.mark.parametrize("pressed", [True, False])
def test_button_follows_is_live_after_a_normal_toggle(cls, live_text, idle_text, companion, pressed):
    stub = _widget_stub(is_live_after_call=pressed)

    cls.toggle_live(stub, pressed)

    getattr(stub.liveController, "start_live" if pressed else "stop_live").assert_called_once()
    _assert_button_state(stub, pressed, live_text, idle_text, companion)
