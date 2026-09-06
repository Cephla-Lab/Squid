"""The live toggle buttons must reflect what the LiveController actually did.

stop_live() can raise after live is already stopped (a busy MCU timing out the
illumination-off wait), and a button stuck on "Stop" invites more toggling that
restarts the trigger stream against the busy MCU. Button text, checked state,
and companion buttons must therefore derive from liveController.is_live in a
finally block, not from the click.
"""

from unittest.mock import MagicMock

import pytest

from control.widgets import LaserAutofocusSettingWidget, LiveControlWidget, NapariLiveWidget


def _widget_stub(is_live_after_call: bool) -> MagicMock:
    """Widget-shaped fake self exposing what the toggle_live methods touch."""
    stub = MagicMock()
    stub.liveController.is_live = is_live_after_call
    return stub


# (widget class, button text while live, button text while idle,
#  companion-button attribute that is enabled only while idle, or None)
CASES = [
    (LiveControlWidget, "Stop", "Live", "btn_snap"),
    (LaserAutofocusSettingWidget, "Stop Live", "Start Live", "run_spot_detection_button"),
    (NapariLiveWidget, "Stop Live", "Start Live", None),
]


def _assert_button_state(stub, is_live, live_text, idle_text, companion):
    stub.btn_live.setText.assert_called_with(live_text if is_live else idle_text)
    stub.btn_live.setChecked.assert_called_with(is_live)
    if companion:
        getattr(stub, companion).setEnabled.assert_called_with(not is_live)


@pytest.mark.parametrize("cls,live_text,idle_text,companion", CASES)
def test_button_shows_idle_when_stop_live_raises(cls, live_text, idle_text, companion):
    # Real stop_live() sets is_live = False before the raising illumination-off
    # wait, so live is genuinely off when the exception escapes.
    stub = _widget_stub(is_live_after_call=False)
    stub.liveController.stop_live.side_effect = TimeoutError("mcu busy")

    with pytest.raises(TimeoutError):
        cls.toggle_live(stub, False)

    _assert_button_state(stub, False, live_text, idle_text, companion)


@pytest.mark.parametrize("cls,live_text,idle_text,companion", CASES)
def test_button_shows_live_when_start_live_raises(cls, live_text, idle_text, companion):
    # Real start_live() sets is_live = True first, so a later exception leaves
    # live running.
    stub = _widget_stub(is_live_after_call=True)
    stub.liveController.start_live.side_effect = TimeoutError("mcu busy")

    with pytest.raises(TimeoutError):
        cls.toggle_live(stub, True)

    _assert_button_state(stub, True, live_text, idle_text, companion)


@pytest.mark.parametrize("cls,live_text,idle_text,companion", CASES)
def test_button_shows_live_after_normal_start(cls, live_text, idle_text, companion):
    stub = _widget_stub(is_live_after_call=True)

    cls.toggle_live(stub, True)

    stub.liveController.start_live.assert_called_once()
    _assert_button_state(stub, True, live_text, idle_text, companion)


@pytest.mark.parametrize("cls,live_text,idle_text,companion", CASES)
def test_button_shows_idle_after_normal_stop(cls, live_text, idle_text, companion):
    stub = _widget_stub(is_live_after_call=False)

    cls.toggle_live(stub, False)

    stub.liveController.stop_live.assert_called_once()
    _assert_button_state(stub, False, live_text, idle_text, companion)
