"""Tests for _ApplyChannelOffsetMixin._update_apply_channel_offset_enable_state.

The 'Per-channel Z-offset' checkbox must stay in sync with the controller's
apply_channel_offset flag. Visibility follows laser AF (the offset needs an AF
reference anchor), but toggling laser AF must NOT silently change the checked state:
offset application is already double-gated on reflection AF in the worker, so a
retained opt-in is harmless while AF is off and must survive an AF off->on cycle.

Regression: force-unchecking on AF-off (and never re-checking on AF-on) meant a
laser-AF off->on cycle dropped the user's opt-in — the visible checkbox no longer
matched what actually happened during acquisition.
"""

from unittest.mock import MagicMock

from control.widgets import _ApplyChannelOffsetMixin


class _Stub(_ApplyChannelOffsetMixin):
    """Minimal host for the mixin with a mocked checkbox and controller."""

    def __init__(self):
        self.multipointController = MagicMock()
        self.checkbox_applyChannelOffset = MagicMock()


def test_af_off_hides_without_touching_checked_state():
    s = _Stub()
    s._update_apply_channel_offset_enable_state(False)
    s.checkbox_applyChannelOffset.setVisible.assert_called_once_with(False)
    # Must NOT force the checkbox off — that would drop the user's opt-in.
    s.checkbox_applyChannelOffset.setChecked.assert_not_called()
    s.multipointController.set_apply_channel_offset.assert_not_called()


def test_af_on_shows_without_touching_checked_state():
    s = _Stub()
    s._update_apply_channel_offset_enable_state(True)
    s.checkbox_applyChannelOffset.setVisible.assert_called_once_with(True)
    s.checkbox_applyChannelOffset.setChecked.assert_not_called()
    s.multipointController.set_apply_channel_offset.assert_not_called()


def test_af_off_then_on_never_changes_checked_state():
    """An AF off->on round trip must leave the checked state entirely to the user."""
    s = _Stub()
    s._update_apply_channel_offset_enable_state(False)
    s._update_apply_channel_offset_enable_state(True)
    s.checkbox_applyChannelOffset.setChecked.assert_not_called()
    assert [c.args[0] for c in s.checkbox_applyChannelOffset.setVisible.call_args_list] == [False, True]
