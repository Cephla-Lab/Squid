"""Shortest-path (wrap-around) slot changes on the Squid filter wheel.

The wheel is rotary: 8 -> 1 is one slot forward across the index flag, not seven slots back.
The controller keeps the driver coordinate continuous with a per-wheel turn counter, so the
absolute MOVETO target for slot k on turn n is target(k) + n * usteps_per_turn.
"""
from unittest.mock import MagicMock

import pytest

from squid.config import SquidFilterWheelConfig
from squid.filter_wheel_controller.cephla import SquidFilterWheel


def _config(motor_slot: int = 3, slots: int = 8) -> SquidFilterWheelConfig:
    return SquidFilterWheelConfig(max_index=slots, min_index=1, offset=0.008, motor_slot_index=motor_slot,
                                  transitions_per_revolution=4000)


def _wheel(wrap=True, slots=8):
    mc = MagicMock()
    mc.firmware_version = (1, 6)
    cfg = _config(slots=slots)
    w = SquidFilterWheel(mc, cfg, skip_init=True)
    w.wrap = wrap
    return w, mc, cfg


TURN = SquidFilterWheel._usteps_per_turn()


@pytest.mark.parametrize("delta,expected", [(1, 1), (3, 3), (4, 4), (5, -3), (7, -1), (-1, -1), (-4, 4), (-7, 1), (0, 0)])
def test_shortest_slot_delta_prefers_the_short_way_and_forward_on_ties(delta, expected):
    assert SquidFilterWheel._shortest_slot_delta(delta, 8) == expected


def test_eight_to_one_is_one_slot_forward_across_the_flag():
    w, mc, cfg = _wheel()
    w._positions[1] = 8
    w.set_filter_wheel_position({1: 1})
    expected = SquidFilterWheel._target_pos_to_usteps(cfg, 1) + TURN
    mc.move_w_to_usteps.assert_called_once_with(expected)
    assert w._positions[1] == 1
    assert w._turns[1] == 1


def test_one_to_eight_is_one_slot_backward_across_the_flag():
    w, mc, cfg = _wheel()
    w.set_filter_wheel_position({1: 8})
    expected = SquidFilterWheel._target_pos_to_usteps(cfg, 8) - TURN
    mc.move_w_to_usteps.assert_called_once_with(expected)
    assert w._turns[1] == -1


def test_half_turn_tie_goes_forward_and_matches_the_legacy_target():
    w, mc, cfg = _wheel()
    w.set_filter_wheel_position({1: 5})
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(cfg, 5))
    assert w._turns[1] == 0


def test_turns_accumulate_and_the_coordinate_stays_continuous():
    w, mc, cfg = _wheel()
    seq = [3, 6, 1, 4, 7, 2, 5, 8, 3]          # keeps going forward around the wheel
    for k in seq:
        w.set_filter_wheel_position({1: k})
    targets = [c.args[0] for c in mc.move_w_to_usteps.call_args_list]
    assert all(b > a for a, b in zip(targets, targets[1:]))   # monotonic: never unwinds
    assert w._turns[1] == 3 and w._positions[1] == 3
    assert targets[-1] == SquidFilterWheel._target_pos_to_usteps(cfg, 3) + 3 * TURN


def test_wrap_off_keeps_the_legacy_long_way_round():
    w, mc, cfg = _wheel(wrap=False)
    w._positions[1] = 8
    w.set_filter_wheel_position({1: 1})
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(cfg, 1))
    assert w._turns[1] == 0


def test_next_position_wraps_from_last_to_first_slot():
    w, mc, cfg = _wheel()
    w._positions[1] = 8
    w.next_position(1)
    assert w._positions[1] == 1
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(cfg, 1) + TURN)


def test_previous_position_wraps_from_first_to_last_slot():
    w, mc, cfg = _wheel()
    w.previous_position(1)
    assert w._positions[1] == 8
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(cfg, 8) - TURN)


def test_homing_resets_the_turn_counter():
    w, mc, cfg = _wheel()
    w._positions[1] = 8
    w.set_filter_wheel_position({1: 1})
    assert w._turns[1] == 1
    w.home(1)
    assert w._turns[1] == 0 and w._positions[1] == 1
