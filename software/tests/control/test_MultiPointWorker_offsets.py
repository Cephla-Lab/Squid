"""Unit tests for MultiPointWorker per-channel z-offset helpers.

These tests construct a minimal MultiPointWorker-shaped stub with mocked stage
and piezo to verify the delta-tracking algorithm in isolation. See
software/docs/laser-af-channel-offset-design.md §4 for the algorithm spec.
"""

from unittest.mock import MagicMock
import pytest

from control._def import TriggerMode
from control.core.multi_point_worker import MultiPointWorker


class _Stub:
    """Bare MultiPointWorker-ish object with just the attributes the helpers read."""

    def __init__(self, *, use_piezo: bool, do_reflection_af: bool, apply_channel_offset: bool):
        self.use_piezo = use_piezo
        self.do_reflection_af = do_reflection_af
        self.apply_channel_offset = apply_channel_offset
        self.stage = MagicMock()
        self.piezo = MagicMock()
        self.piezo.range_um = 400.0
        self.z_piezo_um = 100.0
        self.liveController = MagicMock()
        self.liveController.trigger_mode = TriggerMode.SOFTWARE
        self._current_z_offset_um = 0.0
        self._log = MagicMock()
        self.wait_till_operation_is_completed = MagicMock()
        self._sleep = MagicMock()

    _apply_channel_z_offset = MultiPointWorker._apply_channel_z_offset
    _reset_channel_z_offset = MultiPointWorker._reset_channel_z_offset
    _move_z_for_offset = MultiPointWorker._move_z_for_offset


def _config(z_offset_um):
    cfg = MagicMock()
    cfg.z_offset_um = z_offset_um
    return cfg


def test_apply_stage_path_single_channel():
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.assert_called_once_with(2.0 / 1000)
    w.piezo.move_to.assert_not_called()
    assert w._current_z_offset_um == 2.0


def test_apply_skipped_when_laser_af_off():
    w = _Stub(use_piezo=False, do_reflection_af=False, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.assert_not_called()
    w.piezo.move_to.assert_not_called()
    assert w._current_z_offset_um == 0.0


def test_apply_skipped_when_checkbox_off():
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=False)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.assert_not_called()
    assert w._current_z_offset_um == 0.0


def test_apply_no_move_for_zero_delta():
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w._apply_channel_z_offset(_config(2.0))
    assert w.stage.move_z.call_count == 1


def test_reset_undoes_remaining_offset():
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    w.stage.move_z.reset_mock()
    w._reset_channel_z_offset()
    w.stage.move_z.assert_called_once_with(-2.0 / 1000)
    assert w._current_z_offset_um == 0.0


def test_reset_noop_when_offset_is_zero():
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._reset_channel_z_offset()
    w.stage.move_z.assert_not_called()


def test_piezo_path_uses_piezo_move_to():
    w = _Stub(use_piezo=True, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(3.0))
    w.piezo.move_to.assert_called_once_with(103.0)
    w.stage.move_z.assert_not_called()
    assert w.z_piezo_um == 103.0


def test_piezo_clamped_when_out_of_range():
    w = _Stub(use_piezo=True, do_reflection_af=True, apply_channel_offset=True)
    w.z_piezo_um = 380.0
    w._apply_channel_z_offset(_config(50.0))
    w.piezo.move_to.assert_called_once_with(400.0)
    w._log.warning.assert_called_once()
    assert w.z_piezo_um == 400.0


def test_sequence_four_channels_delta_pattern():
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    for off in [0, 2, 2, -1]:
        w._apply_channel_z_offset(_config(off))
    w._reset_channel_z_offset()
    rel_mm_args = [call.args[0] for call in w.stage.move_z.call_args_list]
    assert rel_mm_args == pytest.approx([2 / 1000, -3 / 1000, 1 / 1000])
    assert w._current_z_offset_um == 0.0
