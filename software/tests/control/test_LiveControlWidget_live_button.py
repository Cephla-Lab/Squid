"""Unit tests for the LiveControlWidget live-button labels.

The button reads "Live" when idle and "Stop" while live is running ("Start"/"Stop"
was ambiguous next to Snap — start what?). Snap is disabled while live runs.
"""

from unittest.mock import MagicMock

from control.widgets import LiveControlWidget


class _ToggleStub:
    """Minimal LiveControlWidget-shaped object for testing toggle_live."""

    def __init__(self):
        self.liveController = MagicMock()
        self.btn_live = MagicMock()
        self.btn_snap = MagicMock()
        self.signal_start_live = MagicMock()

    toggle_live = LiveControlWidget.toggle_live


def test_pressed_starts_live_and_shows_stop():
    s = _ToggleStub()
    s.toggle_live(True)
    s.liveController.start_live.assert_called_once()
    s.btn_live.setText.assert_called_once_with("Stop")
    s.btn_snap.setEnabled.assert_called_once_with(False)
    s.signal_start_live.emit.assert_called_once()


def test_released_stops_live_and_shows_live():
    s = _ToggleStub()
    s.toggle_live(False)
    s.liveController.stop_live.assert_called_once()
    s.btn_live.setText.assert_called_once_with("Live")
    s.btn_snap.setEnabled.assert_called_once_with(True)
    s.signal_start_live.emit.assert_not_called()
