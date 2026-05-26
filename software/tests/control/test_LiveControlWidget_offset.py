"""Unit tests for LiveControlWidget._maybe_apply_live_channel_offset (absolute positioning)."""

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
        displacement_um: float = 0.0,
        raises: bool = False,
    ):
        self.checkbox_applyOnChannelSwitch = MagicMock()
        self.checkbox_applyOnChannelSwitch.isChecked.return_value = checked

        self.liveController = MagicMock()
        self.liveController.microscope.stage.get_pos.return_value = MagicMock(z_mm=10.0)

        laser_af = MagicMock()
        laser_af.laser_af_properties.has_reference = has_reference
        if raises:
            laser_af.measure_displacement.side_effect = RuntimeError("spot lost")
        else:
            laser_af.measure_displacement.return_value = displacement_um
        self.liveController.microscope.laser_autofocus_controller = laser_af

        self._log = MagicMock()

    _LIVE_OFFSET_MAX_JUMP_UM = LiveControlWidget._LIVE_OFFSET_MAX_JUMP_UM
    _maybe_apply_live_channel_offset = LiveControlWidget._maybe_apply_live_channel_offset


def _cfg(z_offset_um):
    cfg = MagicMock()
    cfg.z_offset_um = z_offset_um
    return cfg


def test_no_move_when_checkbox_unchecked():
    w = _LiveStub(checked=False, has_reference=True)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z_to.assert_not_called()


def test_no_move_when_no_reference():
    w = _LiveStub(checked=True, has_reference=False)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z_to.assert_not_called()


def test_no_move_when_new_config_is_none():
    w = _LiveStub(checked=True, has_reference=True, displacement_um=0.0)
    w._maybe_apply_live_channel_offset(None)
    w.liveController.microscope.stage.move_z_to.assert_not_called()


def test_no_move_when_measure_raises():
    w = _LiveStub(checked=True, has_reference=True, raises=True)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z_to.assert_not_called()
    w._log.warning.assert_called_once()


def test_absolute_move_uses_reference_plus_offset():
    """With current_z=10.0mm and displacement=+5µm, reference is 9.995mm.
    Target for offset=+2µm should be 9.997mm."""
    w = _LiveStub(checked=True, has_reference=True, displacement_um=5.0)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z_to.assert_called_once()
    target = w.liveController.microscope.stage.move_z_to.call_args.args[0]
    assert target == pytest.approx(9.997)


def test_absolute_move_robust_to_manual_jog():
    """Whatever current_z is, the helper derives reference from it minus displacement.
    Verify the absolute target only depends on the channel's offset, not on prior history."""
    # Same channel offset, two different current_z values; the reference should
    # adjust by the difference because measure_displacement reflects current state.
    w1 = _LiveStub(checked=True, has_reference=True, displacement_um=0.0)
    w1.liveController.microscope.stage.get_pos.return_value = MagicMock(z_mm=10.000)
    w1._maybe_apply_live_channel_offset(_cfg(2.0))

    w2 = _LiveStub(checked=True, has_reference=True, displacement_um=0.0)
    w2.liveController.microscope.stage.get_pos.return_value = MagicMock(z_mm=10.005)
    w2._maybe_apply_live_channel_offset(_cfg(2.0))

    target1 = w1.liveController.microscope.stage.move_z_to.call_args.args[0]
    target2 = w2.liveController.microscope.stage.move_z_to.call_args.args[0]
    # Both reference planes are at current_z (because displacement_um=0), so target
    # differs by (10.005 - 10.000) = 0.005 mm.
    assert target1 == pytest.approx(10.002)
    assert target2 == pytest.approx(10.007)


# ---------------------------------------------------------------------------
# Regression tests for review fixes
# ---------------------------------------------------------------------------


def test_no_move_when_measure_returns_nan():
    """measure_displacement returns NaN on soft failure (no spot, laser-on timeout)
    instead of raising. The helper must NOT issue stage.move_z_to(NaN)."""
    w = _LiveStub(checked=True, has_reference=True, displacement_um=float("nan"))
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z_to.assert_not_called()
    w._log.warning.assert_called_once()


def test_no_move_when_stored_offset_is_nan():
    """A persisted NaN offset (e.g. from an older buggy capture) must not turn into a
    NaN absolute move on channel switch."""
    w = _LiveStub(checked=True, has_reference=True, displacement_um=0.0)
    w._maybe_apply_live_channel_offset(_cfg(float("nan")))
    w.liveController.microscope.stage.move_z_to.assert_not_called()
    w._log.warning.assert_called_once()


def test_no_move_when_jump_exceeds_safety_cap():
    """A wildly wrong AF reading (drift, secondary peak) should be suppressed rather
    than commanding a millimetre-scale absolute move."""
    # displacement_um=2000 → reference_z_mm = 10.0 - 2.0 = 8.0 mm → target with offset=2
    # = 8.002 mm → |8.002 - 10.0|*1000 = 1998 µm jump, far above the 500 µm cap.
    w = _LiveStub(checked=True, has_reference=True, displacement_um=2000.0)
    w._maybe_apply_live_channel_offset(_cfg(2.0))
    w.liveController.microscope.stage.move_z_to.assert_not_called()
    w._log.warning.assert_called_once()


def test_legitimate_small_move_passes_safety_cap():
    """A typical offset (a few µm) plus typical AF displacement (a few µm) must not
    be blocked by the safety cap."""
    w = _LiveStub(checked=True, has_reference=True, displacement_um=3.0)
    w._maybe_apply_live_channel_offset(_cfg(5.0))
    w.liveController.microscope.stage.move_z_to.assert_called_once()
