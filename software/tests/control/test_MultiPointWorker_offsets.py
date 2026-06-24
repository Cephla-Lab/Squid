"""Unit tests for MultiPointWorker per-channel z-offset helpers.

These tests construct a minimal MultiPointWorker-shaped stub with mocked stage
and piezo to verify the delta-tracking algorithm in isolation. See
software/docs/laser-af-channel-offset-design.md §4 for the algorithm spec.
"""

import math
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

    _Z_OFFSET_EPS_UM = MultiPointWorker._Z_OFFSET_EPS_UM
    _reset_channel_z_offset = MultiPointWorker._reset_channel_z_offset
    _move_z_for_offset = MultiPointWorker._move_z_for_offset

    def _apply_channel_z_offset(self, config, af_succeeded: bool = True):
        # Default af_succeeded=True so the common-case tests don't have to thread it;
        # the AF-failure gate test passes False explicitly.
        return MultiPointWorker._apply_channel_z_offset(self, config, af_succeeded)


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
    """Piezo overflow is clamped and the offset tracker reflects the achieved (clamped) position."""
    w = _Stub(use_piezo=True, do_reflection_af=True, apply_channel_offset=True)
    w.z_piezo_um = 380.0
    w._apply_channel_z_offset(_config(50.0))  # would land at 430, clamps to 400
    w.piezo.move_to.assert_called_once_with(400.0)
    w._log.warning.assert_called_once()
    assert w.z_piezo_um == 400.0
    # Tracker reflects the ACHIEVED offset (20 µm), not the requested 50 µm
    assert w._current_z_offset_um == pytest.approx(20.0)


def test_piezo_clamp_does_not_corrupt_subsequent_deltas():
    """After a clamp, the next channel's delta is computed from the achieved offset, not the requested one."""
    w = _Stub(use_piezo=True, do_reflection_af=True, apply_channel_offset=True)
    w.z_piezo_um = 380.0
    w._apply_channel_z_offset(_config(50.0))  # clamps to +20
    w.piezo.move_to.reset_mock()
    w._apply_channel_z_offset(_config(10.0))  # tracker=20, target=10, delta=-10
    # Should move piezo by -10, from 400 → 390
    w.piezo.move_to.assert_called_once_with(390.0)
    assert w._current_z_offset_um == pytest.approx(10.0)


def test_reset_handles_stage_failure_without_stranding_state():
    """If the stage raises during reset, the tracker retains the outstanding offset (so a
    subsequent reset can retry) and the exception is logged, not propagated."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0))
    assert w._current_z_offset_um == 2.0

    # Make the stage raise on the reset move
    w.stage.move_z.side_effect = RuntimeError("stage timeout")
    w._reset_channel_z_offset()  # should not raise

    # Tracker retains the outstanding offset so a later reset retry knows what to undo;
    # zeroing it pre-emptively would silently abandon the offset on a transient failure.
    assert w._current_z_offset_um == 2.0
    # The error was logged via _log.exception (or _log.error with exc_info)
    assert w._log.exception.called or w._log.error.called

    # A retry (stage now working) successfully resets to baseline.
    w.stage.move_z.side_effect = None
    w._reset_channel_z_offset()
    assert w._current_z_offset_um == 0.0


def test_sequence_four_channels_delta_pattern():
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    for off in [0, 2, 2, -1]:
        w._apply_channel_z_offset(_config(off))
    w._reset_channel_z_offset()
    rel_mm_args = [call.args[0] for call in w.stage.move_z.call_args_list]
    assert rel_mm_args == pytest.approx([2 / 1000, -3 / 1000, 1 / 1000])
    assert w._current_z_offset_um == 0.0


def test_handle_acquisition_abort_resets_offset(tmp_path):
    """handle_acquisition_abort resets any stranded offset defensively."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    # Simulate a stranded offset (e.g., exception bypassed the inner finally)
    w._current_z_offset_um = 1.7
    # Wire up the real method
    w.handle_acquisition_abort = MultiPointWorker.handle_acquisition_abort.__get__(w)
    # Minimal mocks for the rest of handle_acquisition_abort's behavior
    w.coordinates_pd = MagicMock()
    w.microcontroller = MagicMock()
    w._wait_for_outstanding_callback_images = MagicMock()

    w.handle_acquisition_abort(current_path=str(tmp_path))

    # The first stage move must be the reset (-1.7 µm)
    assert w.stage.move_z.call_args_list[0].args[0] == pytest.approx(-1.7 / 1000)
    assert w._current_z_offset_um == 0.0


# ---------------------------------------------------------------------------
# Gap 1: z-stack (NZ > 1) with channel offsets
# ---------------------------------------------------------------------------


def test_z_stack_offsets_reset_between_levels():
    """In a z-stack loop pattern, _reset_channel_z_offset is called between levels
    and _current_z_offset_um is 0 before the next level begins."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    nz = 3
    channels = [_config(0.0), _config(2.0), _config(-1.0)]
    moves_per_level = []
    for z_level in range(nz):
        w.stage.move_z.reset_mock()
        # Inner channel loop mirrors multi_point_worker.py
        try:
            for ch in channels:
                w._apply_channel_z_offset(ch)
        finally:
            w._reset_channel_z_offset()
        moves_per_level.append([call.args[0] for call in w.stage.move_z.call_args_list])
        assert w._current_z_offset_um == 0.0, f"Offset leaked between z_levels at z_level={z_level}"

    # Each level applies the same delta pattern: [+2, -3, +1 (reset)]
    for i, lvl in enumerate(moves_per_level):
        assert lvl == pytest.approx(
            [2 / 1000, -3 / 1000, 1 / 1000]
        ), f"z_level {i} moves were {lvl}, expected [+0.002, -0.003, +0.001]"


# ---------------------------------------------------------------------------
# Gap 2: _log_ignored_offsets is tested
# ---------------------------------------------------------------------------


def test_log_ignored_offsets_logs_will_apply_on_happy_path():
    """Gate is on AND non-zero offsets exist → log that they will be applied
    (helps diagnose 'offsets not applied' reports by confirming the worker saw them)."""
    cfg_a = _config(2.0)
    cfg_a.name = "GFP"
    cfg_b = _config(-1.0)
    cfg_b.name = "DAPI"
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w.selected_configurations = [cfg_a, cfg_b]
    w._log_ignored_offsets = MultiPointWorker._log_ignored_offsets.__get__(w)
    w._log_ignored_offsets()
    assert w._log.info.call_count == 1
    msg = w._log.info.call_args[0][0]
    assert "will be applied" in msg
    assert "GFP" in msg and "DAPI" in msg
    assert "+2.00" in msg and "-1.00" in msg


def test_log_ignored_offsets_silent_when_all_offsets_zero():
    """No log when offsets are all zero even if gating is off."""
    w = _Stub(use_piezo=False, do_reflection_af=False, apply_channel_offset=True)
    w.selected_configurations = [_config(0.0), _config(0.0)]
    w._log_ignored_offsets = MultiPointWorker._log_ignored_offsets.__get__(w)
    w._log_ignored_offsets()
    w._log.info.assert_not_called()


def test_log_ignored_offsets_fires_when_laser_af_off_with_nonzero_offsets():
    """Logs once with the right reason and channel summary."""
    cfg_a = _config(1.2)
    cfg_a.name = "mCherry"
    cfg_b = _config(-0.6)
    cfg_b.name = "Cy5"
    w = _Stub(use_piezo=False, do_reflection_af=False, apply_channel_offset=True)
    w.selected_configurations = [cfg_a, cfg_b]
    w._log_ignored_offsets = MultiPointWorker._log_ignored_offsets.__get__(w)
    w._log_ignored_offsets()
    assert w._log.info.call_count == 1
    msg = w._log.info.call_args[0][0]
    assert "laser AF off" in msg
    assert "mCherry" in msg and "Cy5" in msg
    assert "+1.20" in msg and "-0.60" in msg


def test_log_ignored_offsets_fires_when_checkbox_off():
    """Reason string changes when laser AF is on but checkbox is off."""
    cfg = _config(0.5)
    cfg.name = "GFP"
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=False)
    w.selected_configurations = [cfg]
    w._log_ignored_offsets = MultiPointWorker._log_ignored_offsets.__get__(w)
    w._log_ignored_offsets()
    assert w._log.info.call_count == 1
    assert "'Apply channel offset' unchecked" in w._log.info.call_args[0][0]


def test_log_ignored_offsets_warns_on_non_finite_and_excludes_from_will_apply():
    """A NaN offset must be warned about separately and NOT counted in the
    'will be applied' summary (since _apply_channel_z_offset treats it as 0)."""
    cfg_nan = _config(float("nan"))
    cfg_nan.name = "Bad"
    cfg_ok = _config(2.0)
    cfg_ok.name = "GFP"
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w.selected_configurations = [cfg_nan, cfg_ok]
    w._log_ignored_offsets = MultiPointWorker._log_ignored_offsets.__get__(w)
    w._log_ignored_offsets()
    warn_msg = w._log.warning.call_args[0][0]
    assert "non-finite" in warn_msg and "Bad" in warn_msg
    info_msg = w._log.info.call_args[0][0]
    assert "will be applied" in info_msg
    assert "GFP" in info_msg
    assert "Bad" not in info_msg  # NaN channel excluded from the apply summary


# ---------------------------------------------------------------------------
# Gap 3: Multi-time-point invariant — placeholder skip
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Requires integration-level harness; documented as design invariant in spec §4.2")
def test_multi_time_point_last_z_pos_records_offset_free_baseline():
    """At Nt > 1, _last_time_point_z_pos[(region_id, fov)] must be recorded with
    _current_z_offset_um == 0 (before any channel offset is applied at z_level 0).
    Spec §4.2 says this must hold; the loop integration in Task 7 preserves it
    because acquire_pos is captured before the try-block that applies offsets.
    Verified manually in code review; needs an integration test."""
    pass


# ---------------------------------------------------------------------------
# Regression tests for review fixes
# ---------------------------------------------------------------------------


def test_apply_skipped_when_laser_af_failed_for_fov():
    """When perform_autofocus failed for the FOV (af_succeeded=False), applying the
    offset would shift z relative to an absent reference. The gate must suppress it."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(2.0), af_succeeded=False)
    w.stage.move_z.assert_not_called()
    assert w._current_z_offset_um == 0.0
    w._log.warning.assert_called_once()


def test_apply_treats_nan_offset_as_zero_with_warning():
    """A non-finite z_offset_um (e.g. NaN written by an older buggy capture) must not
    be forwarded to the stage as a NaN move command."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._apply_channel_z_offset(_config(float("nan")))
    w.stage.move_z.assert_not_called()
    assert w._current_z_offset_um == 0.0
    w._log.warning.assert_called_once()


def test_apply_skips_near_zero_delta_within_epsilon():
    """Sub-µm FP drift in the delta must not trigger a stage move."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w._current_z_offset_um = 0.1 + 0.2  # 0.30000000000000004 in IEEE-754
    w._apply_channel_z_offset(_config(0.3))
    w.stage.move_z.assert_not_called()


def test_piezo_state_not_mutated_when_move_fails():
    """If piezo.move_to raises, z_piezo_um (the software cache) must remain at the
    pre-move value so subsequent z-stack steps aren't biased."""
    w = _Stub(use_piezo=True, do_reflection_af=True, apply_channel_offset=True)
    w.piezo.move_to.side_effect = RuntimeError("piezo USB error")
    with pytest.raises(RuntimeError):
        w._apply_channel_z_offset(_config(3.0))
    assert w.z_piezo_um == 100.0  # unchanged from the stub default
    assert w._current_z_offset_um == 0.0  # tracker also unchanged


# ---------------------------------------------------------------------------
# perform_autofocus AF-success bookkeeping (reflection AF branch)
# ---------------------------------------------------------------------------


def _af_stub(*, move_to_target_result=True, move_to_target_raises=False):
    """Stub with just the attributes the reflection-AF branch of perform_autofocus reads."""
    w = _Stub(use_piezo=False, do_reflection_af=True, apply_channel_offset=True)
    w.laser_auto_focus_controller = MagicMock()
    if move_to_target_raises:
        w.laser_auto_focus_controller.move_to_target.side_effect = RuntimeError("af crashed")
    else:
        w.laser_auto_focus_controller.move_to_target.return_value = move_to_target_result
    w._laser_af_successes = 0
    w._laser_af_failures = 0
    # perform_autofocus reads this per-region target map (empty -> target 0.0, the
    # pre-existing behavior); the real worker sets it in __init__.
    w.region_laser_af_offsets = {}
    w.perform_autofocus = MultiPointWorker.perform_autofocus.__get__(w)
    return w


def test_perform_autofocus_soft_failure_marks_af_failed():
    """move_to_target reports failure via its RETURN VALUE (no reference, NaN
    displacement, out-of-range, cross-correlation mismatch) — not by raising.
    perform_autofocus must treat False as a failure or the per-channel z-offset
    gate applies offsets from an unanchored z."""
    w = _af_stub(move_to_target_result=False)
    af_ok = w.perform_autofocus("region", 0)
    assert af_ok is False
    assert w._laser_af_failures == 1
    assert w._laser_af_successes == 0
    # And the gate (fed the return value) must suppress the move:
    w._apply_channel_z_offset(_config(2.0), af_succeeded=af_ok)
    w.stage.move_z.assert_not_called()


def test_perform_autofocus_success_marks_af_succeeded():
    w = _af_stub(move_to_target_result=True)
    assert w.perform_autofocus("region", 0) is True
    assert w._laser_af_successes == 1
    assert w._laser_af_failures == 0


def test_perform_autofocus_exception_marks_af_failed(tmp_path):
    """The raise path also reports failure (and dumps the focus image)."""
    import numpy as np

    w = _af_stub(move_to_target_raises=True)
    w.laser_auto_focus_controller.image = np.zeros((4, 4), dtype=np.uint8)
    w.base_path = str(tmp_path)
    w.experiment_ID = "exp"
    w.time_point = 0
    (tmp_path / "exp" / "0").mkdir(parents=True)
    assert w.perform_autofocus("region", 0) is False
    assert w._laser_af_failures == 1
    assert w._laser_af_successes == 0
