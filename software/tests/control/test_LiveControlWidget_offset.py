"""Unit tests for LiveControlWidget._maybe_apply_live_channel_offset (delta tracking).

The live channel-switch path does NOT call laser AF on each switch. It maintains a
running tracker of "currently applied offset" (reset to 0 when the user enables
'Apply in Live') and moves the stage by the delta between the new channel's stored
offset and the tracker on each switch.
"""

import math
from unittest.mock import MagicMock

import pytest

from control.widgets import LiveControlWidget


class _LiveStub:
    """Minimal LiveControlWidget-shaped object for testing _maybe_apply_live_channel_offset."""

    def __init__(
        self,
        *,
        checked: bool,
        has_reference: bool,
        starting_offset_um: float = 0.0,
    ):
        self.checkbox_applyOnChannelSwitch = MagicMock()
        self.checkbox_applyOnChannelSwitch.isChecked.return_value = checked

        self.liveController = MagicMock()

        laser_af = MagicMock()
        laser_af.laser_af_properties.has_reference = has_reference
        self.liveController.microscope.laser_autofocus_controller = laser_af

        # Tracker starts wherever the test wants — the production widget resets it
        # to 0 when the user enables 'Apply in Live'.
        self._live_current_z_offset_um = starting_offset_um
        self._log = MagicMock()

    _LIVE_OFFSET_MAX_JUMP_UM = LiveControlWidget._LIVE_OFFSET_MAX_JUMP_UM
    _maybe_apply_live_channel_offset = LiveControlWidget._maybe_apply_live_channel_offset
    _on_apply_in_live_toggled = LiveControlWidget._on_apply_in_live_toggled


def _cfg(z_offset_um, name="ch"):
    cfg = MagicMock()
    cfg.z_offset_um = z_offset_um
    cfg.name = name
    return cfg


# ---------------------------------------------------------------------------
# Gates — no move when the feature is off or unanchored
# ---------------------------------------------------------------------------


def test_no_move_when_checkbox_unchecked():
    w = _LiveStub(checked=False, has_reference=True)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z.assert_not_called()


def test_no_move_when_no_reference():
    """Reference gate kept: offsets come from AF capture; without a reference all
    persisted offsets are 0 and the feature has nothing to do."""
    w = _LiveStub(checked=True, has_reference=False)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z.assert_not_called()


def test_no_move_when_new_config_is_none():
    w = _LiveStub(checked=True, has_reference=True)
    w._maybe_apply_live_channel_offset(None)
    w.liveController.microscope.stage.move_z.assert_not_called()


def test_no_move_when_stored_offset_is_nan():
    w = _LiveStub(checked=True, has_reference=True)
    w._maybe_apply_live_channel_offset(_cfg(float("nan")))
    w.liveController.microscope.stage.move_z.assert_not_called()
    w._log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Delta tracking
# ---------------------------------------------------------------------------


def test_first_switch_moves_by_full_stored_offset():
    """Tracker starts at 0 (fresh enable); switching to offset=+5µm moves +5µm."""
    w = _LiveStub(checked=True, has_reference=True)
    w._maybe_apply_live_channel_offset(_cfg(5.0))
    w.liveController.microscope.stage.move_z.assert_called_once_with(5.0 / 1000)
    assert w._live_current_z_offset_um == pytest.approx(5.0)


def test_subsequent_switch_moves_by_delta_only():
    """Tracker holds prior +5µm; switching to offset=+2µm moves -3µm (delta)."""
    w = _LiveStub(checked=True, has_reference=True, starting_offset_um=5.0)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z.assert_called_once_with(-3.0 / 1000)
    assert w._live_current_z_offset_um == pytest.approx(2.0)


def test_switch_back_to_offset_zero_moves_back_to_baseline():
    """Switching to a channel with offset=0 from a non-zero tracker undoes the prior move."""
    w = _LiveStub(checked=True, has_reference=True, starting_offset_um=5.0)
    w._maybe_apply_live_channel_offset(_cfg(0.0))
    w.liveController.microscope.stage.move_z.assert_called_once_with(-5.0 / 1000)
    assert w._live_current_z_offset_um == pytest.approx(0.0)


def test_no_move_when_delta_is_zero():
    """Same offset twice in a row → no move, no log spam."""
    w = _LiveStub(checked=True, has_reference=True, starting_offset_um=3.0)
    w._maybe_apply_live_channel_offset(_cfg(3.0))
    w.liveController.microscope.stage.move_z.assert_not_called()


def test_no_call_to_measure_displacement():
    """Channel switch must NOT consult laser AF — semantics per user request:
    AF should not run on channel switch, only the stored offset is applied."""
    w = _LiveStub(checked=True, has_reference=True)
    w._maybe_apply_live_channel_offset(_cfg(5.0))
    laser_af = w.liveController.microscope.laser_autofocus_controller
    laser_af.measure_displacement.assert_not_called()


def test_sequence_of_channels_accumulates_correctly():
    """Walk through A(0) → B(+2) → C(-1) → A(0) → verify per-step deltas and final tracker."""
    w = _LiveStub(checked=True, has_reference=True)
    sequence = [0.0, 2.0, -1.0, 0.0]
    expected_deltas = [None, 2.0, -3.0, 1.0]  # first call: 0 - 0 = 0 → no move
    for off in sequence:
        w._maybe_apply_live_channel_offset(_cfg(off))
    move_args = [c.args[0] for c in w.liveController.microscope.stage.move_z.call_args_list]
    # First two switches with offset 0 from tracker 0 → no move; then 2 → +2, -1 → -3, 0 → +1
    assert move_args == pytest.approx([2.0 / 1000, -3.0 / 1000, 1.0 / 1000])
    assert w._live_current_z_offset_um == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Safety cap on delta
# ---------------------------------------------------------------------------


def test_no_move_when_delta_exceeds_safety_cap():
    """A wildly wrong stored offset (e.g. NaN-poisoned config or hand-edited µm-wrong)
    should be suppressed rather than commanding a > 500 µm move on a single switch."""
    w = _LiveStub(checked=True, has_reference=True)
    w._maybe_apply_live_channel_offset(_cfg(2000.0))
    w.liveController.microscope.stage.move_z.assert_not_called()
    w._log.warning.assert_called_once()


def test_legitimate_small_move_passes_safety_cap():
    w = _LiveStub(checked=True, has_reference=True)
    w._maybe_apply_live_channel_offset(_cfg(5.0))
    w.liveController.microscope.stage.move_z.assert_called_once()


# ---------------------------------------------------------------------------
# Tracker reset on enable
# ---------------------------------------------------------------------------


def test_toggled_on_resets_tracker_to_zero():
    """Enabling 'Apply in Live' treats the current stage position as offset=0."""
    w = _LiveStub(checked=True, has_reference=True, starting_offset_um=12.5)
    w._on_apply_in_live_toggled(True)
    assert w._live_current_z_offset_um == 0.0


def test_toggled_off_leaves_tracker_alone():
    """Unchecking the box doesn't move the stage — preserve tracker so the user can
    re-enable later (though typical flow is to re-enable with reset semantics)."""
    w = _LiveStub(checked=False, has_reference=True, starting_offset_um=7.0)
    w._on_apply_in_live_toggled(False)
    assert w._live_current_z_offset_um == pytest.approx(7.0)


def test_failed_stage_move_does_not_advance_tracker():
    """If the stage move raises, the tracker stays at its prior value so the next
    switch retries the full delta."""
    w = _LiveStub(checked=True, has_reference=True, starting_offset_um=0.0)
    w.liveController.microscope.stage.move_z.side_effect = RuntimeError("stage timeout")
    w._maybe_apply_live_channel_offset(_cfg(5.0))
    w._log.warning.assert_called_once()
    assert w._live_current_z_offset_um == 0.0
