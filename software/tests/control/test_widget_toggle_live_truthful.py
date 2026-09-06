"""The live toggle buttons must reflect what the LiveController actually did.

stop_live() can raise after live is already stopped (a busy MCU timing out the
illumination-off wait - seen on an instrument on 2026-09-02). The old handlers
updated the button only after a successful call, so the button kept saying
"Stop" while live was off, inviting the operator to keep toggling - which
restarted the trigger stream against the busy MCU. The button state must be
derived from liveController.is_live in a finally block, not from the click.
"""

from unittest.mock import MagicMock

import pytest

from control.widgets import LaserAutofocusSettingWidget, LiveControlWidget, NapariLiveWidget


class _WidgetStub:
    """Widget-shaped object exposing only what toggle_live touches."""

    def __init__(self, cls, is_live_after_call: bool):
        self._toggle = cls.toggle_live
        self.liveController = MagicMock()
        self.liveController.is_live = is_live_after_call
        self.btn_live = MagicMock()
        self.btn_snap = MagicMock()
        self.run_spot_detection_button = MagicMock()
        self.signal_start_live = MagicMock()

    def toggle_live(self, pressed):
        return self._toggle(self, pressed)


CASES = [
    (LiveControlWidget, "Stop", "Live"),
    (LaserAutofocusSettingWidget, "Stop Live", "Start Live"),
    (NapariLiveWidget, "Stop Live", "Start Live"),
]


@pytest.mark.parametrize("cls,live_text,idle_text", CASES)
def test_button_shows_idle_when_stop_live_raises(cls, live_text, idle_text):
    # Real stop_live() sets is_live = False before the raising illumination-off
    # wait, so live is genuinely off when the exception escapes.
    stub = _WidgetStub(cls, is_live_after_call=False)
    stub.liveController.stop_live.side_effect = TimeoutError("mcu busy")

    with pytest.raises(TimeoutError):
        stub.toggle_live(False)

    stub.btn_live.setText.assert_called_with(idle_text)


@pytest.mark.parametrize("cls,live_text,idle_text", CASES)
def test_button_shows_live_when_start_live_raises(cls, live_text, idle_text):
    # Real start_live() sets is_live = True first, so a later exception leaves
    # live running.
    stub = _WidgetStub(cls, is_live_after_call=True)
    stub.liveController.start_live.side_effect = TimeoutError("mcu busy")

    with pytest.raises(TimeoutError):
        stub.toggle_live(True)

    stub.btn_live.setText.assert_called_with(live_text)


@pytest.mark.parametrize("cls,live_text,idle_text", CASES)
def test_button_shows_live_after_normal_start(cls, live_text, idle_text):
    stub = _WidgetStub(cls, is_live_after_call=True)

    stub.toggle_live(True)

    stub.liveController.start_live.assert_called_once()
    stub.btn_live.setText.assert_called_with(live_text)


@pytest.mark.parametrize("cls,live_text,idle_text", CASES)
def test_button_shows_idle_after_normal_stop(cls, live_text, idle_text):
    stub = _WidgetStub(cls, is_live_after_call=False)

    stub.toggle_live(False)

    stub.liveController.stop_live.assert_called_once()
    stub.btn_live.setText.assert_called_with(idle_text)


def test_live_control_widget_snap_button_follows_actual_live_state():
    stub = _WidgetStub(LiveControlWidget, is_live_after_call=False)
    stub.liveController.stop_live.side_effect = TimeoutError("mcu busy")

    with pytest.raises(TimeoutError):
        stub.toggle_live(False)

    # Snapping is meaningless while live; live is actually off, so re-enable it.
    stub.btn_snap.setEnabled.assert_called_with(True)


def test_laser_af_widget_spot_detection_follows_actual_live_state():
    stub = _WidgetStub(LaserAutofocusSettingWidget, is_live_after_call=False)
    stub.liveController.stop_live.side_effect = TimeoutError("mcu busy")

    with pytest.raises(TimeoutError):
        stub.toggle_live(False)

    stub.run_spot_detection_button.setEnabled.assert_called_with(True)
