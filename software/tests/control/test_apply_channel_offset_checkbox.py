"""Tests for _ApplyChannelOffsetMixin._update_apply_channel_offset_enable_state.

The 'Per-channel Z-offset' checkbox must stay in sync with the controller's
apply_channel_offset flag. Visibility follows laser AF (the offset needs an AF
reference anchor), but toggling laser AF must NOT silently change the checked state:
offset application is already double-gated on reflection AF in the worker, so a
retained opt-in is harmless while AF is off and must survive an AF off->on cycle.

Regression: force-unchecking on AF-off (and never re-checking on AF-on) meant a
laser-AF off->on cycle dropped the user's opt-in — the visible checkbox no longer
matched what actually happened during acquisition.

Regression 2: the host widgets add the checkbox to a layout only when
SUPPORT_LASER_AUTOFOCUS is true, so without laser AF it stays parentless. The
laser-AF box can still end up checked on such a machine (a cached ``laser_af: true``
restored at startup), and the visibility sync then showed the parentless checkbox as
a stray top-level "Per-channel Z-offset" window that outlived the main window and
kept the application from quitting.
"""

from unittest.mock import MagicMock

import pytest

import control.widgets
from control.widgets import _ApplyChannelOffsetMixin


@pytest.fixture
def laser_af_supported(monkeypatch):
    monkeypatch.setattr(control.widgets, "SUPPORT_LASER_AUTOFOCUS", True)


class _Stub(_ApplyChannelOffsetMixin):
    """Minimal host for the mixin with a mocked checkbox and controller."""

    def __init__(self):
        self.multipointController = MagicMock()
        self.checkbox_applyChannelOffset = MagicMock()


def test_af_off_hides_without_touching_checked_state(laser_af_supported):
    s = _Stub()
    s._update_apply_channel_offset_enable_state(False)
    s.checkbox_applyChannelOffset.setVisible.assert_called_once_with(False)
    # Must NOT force the checkbox off — that would drop the user's opt-in.
    s.checkbox_applyChannelOffset.setChecked.assert_not_called()
    s.multipointController.set_apply_channel_offset.assert_not_called()


def test_af_on_shows_without_touching_checked_state(laser_af_supported):
    s = _Stub()
    s._update_apply_channel_offset_enable_state(True)
    s.checkbox_applyChannelOffset.setVisible.assert_called_once_with(True)
    s.checkbox_applyChannelOffset.setChecked.assert_not_called()
    s.multipointController.set_apply_channel_offset.assert_not_called()


def test_af_off_then_on_never_changes_checked_state(laser_af_supported):
    """An AF off->on round trip must leave the checked state entirely to the user."""
    s = _Stub()
    s._update_apply_channel_offset_enable_state(False)
    s._update_apply_channel_offset_enable_state(True)
    s.checkbox_applyChannelOffset.setChecked.assert_not_called()
    assert [c.args[0] for c in s.checkbox_applyChannelOffset.setVisible.call_args_list] == [False, True]


def test_af_on_keeps_checkbox_hidden_when_laser_af_unsupported(monkeypatch):
    monkeypatch.setattr(control.widgets, "SUPPORT_LASER_AUTOFOCUS", False)
    s = _Stub()
    s._update_apply_channel_offset_enable_state(True)
    s.checkbox_applyChannelOffset.setVisible.assert_called_once_with(False)


class _ParentlessHost(_ApplyChannelOffsetMixin):
    """Like the real widgets without laser AF: the checkbox is built, never laid out."""

    def __init__(self):
        self.multipointController = MagicMock()
        self._create_apply_channel_offset_checkbox()


def test_without_laser_af_the_checkbox_never_becomes_a_window(qtbot, monkeypatch):
    """The mocked tests above were green while the bug shipped: a MagicMock cannot
    become a window. This one uses a real, parentless QCheckBox."""
    monkeypatch.setattr(control.widgets, "SUPPORT_LASER_AUTOFOCUS", False)
    host = _ParentlessHost()
    qtbot.addWidget(host.checkbox_applyChannelOffset)
    host._update_apply_channel_offset_enable_state(True)  # laser-AF box checked, e.g. restored from the cache
    assert not host.checkbox_applyChannelOffset.isVisible()
