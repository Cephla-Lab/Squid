"""Live-view trigger-fps control is inert in CONTINUOUS mode.

In CONTINUOUS mode the camera free-runs at its own internal rate and the host
trigger timer is unused, so the fps setting does nothing. This is pinned at two
layers: LiveController (the single authority, via trigger_fps_is_active /
set_trigger_fps) and the live widget disabling its fps control accordingly.

Uses the method-stealing stub pattern (see test_LiveControlWidget_offset.py) so
the real methods run against minimal state — no QApplication / GUI needed.
"""

from unittest.mock import MagicMock

from control._def import TriggerMode
from control.core.live_controller import LiveController
from control.widgets import LiveControlWidget


class _ControllerStub:
    trigger_fps_is_active = LiveController.trigger_fps_is_active
    set_trigger_fps = LiveController.set_trigger_fps
    _set_trigger_fps = LiveController._set_trigger_fps

    def __init__(self, trigger_mode, use_internal_timer=True):
        self.trigger_mode = trigger_mode
        self.use_internal_timer_for_hardware_trigger = use_internal_timer
        self.is_live = False
        self.fps_trigger = 10
        self.timer_trigger_interval = 100.0
        self._log = MagicMock()


def test_software_trigger_is_active_and_applies_fps():
    c = _ControllerStub(TriggerMode.SOFTWARE)
    assert c.trigger_fps_is_active() is True
    c.set_trigger_fps(25)
    assert c.fps_trigger == 25  # applied


def test_continuous_is_inert_and_set_trigger_fps_is_noop():
    c = _ControllerStub(TriggerMode.CONTINUOUS)
    assert c.trigger_fps_is_active() is False
    c.set_trigger_fps(25)
    assert c.fps_trigger == 10  # unchanged: camera free-runs, fps timer unused


def test_hardware_active_only_with_internal_timer():
    assert _ControllerStub(TriggerMode.HARDWARE, use_internal_timer=True).trigger_fps_is_active() is True
    assert _ControllerStub(TriggerMode.HARDWARE, use_internal_timer=False).trigger_fps_is_active() is False


class _WidgetStub:
    _sync_trigger_fps_enabled = LiveControlWidget._sync_trigger_fps_enabled

    def __init__(self, fps_active):
        self.liveController = MagicMock()
        self.liveController.trigger_fps_is_active.return_value = fps_active
        self.entry_triggerFPS = MagicMock()


def test_widget_enables_fps_control_when_active():
    w = _WidgetStub(fps_active=True)
    w._sync_trigger_fps_enabled()
    w.entry_triggerFPS.setEnabled.assert_called_once_with(True)
    (tooltip,), _ = w.entry_triggerFPS.setToolTip.call_args
    assert tooltip == ""  # no warning needed when the control works


def test_widget_disables_fps_control_and_explains_when_inert():
    w = _WidgetStub(fps_active=False)
    w._sync_trigger_fps_enabled()
    w.entry_triggerFPS.setEnabled.assert_called_once_with(False)
    (tooltip,), _ = w.entry_triggerFPS.setToolTip.call_args
    assert "Continuous" in tooltip  # explains why it's disabled
