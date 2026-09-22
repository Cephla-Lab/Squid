"""Shortest-path (wrap-around) slot changes on the Squid filter wheel.

The wheel is rotary: 8 -> 1 is one slot forward across the index flag, not seven slots back.
The controller keeps the driver coordinate continuous with a per-wheel turn counter, so the
absolute MOVETO target for slot k on turn n is target(k) + n * usteps_per_turn.
"""

from unittest.mock import MagicMock

import pytest

from squid.config import SquidFilterWheelConfig
from squid.filter_wheel_controller.cephla import SquidFilterWheel


@pytest.fixture(autouse=True)
def _wheel_cache_in_tmp(tmp_path, monkeypatch):
    """The wheel records its position for a restart; keep that record out of the real cache/ folder.

    The record starts out saying the driver is already configured the way this host would configure it, so that the
    skip_init=True construction below touches no hardware; a record that says otherwise makes a restart re-configure
    and re-home, which is test_filter_wheel_restart.py's subject.
    """
    import squid.filter_wheel_controller.cephla as cephla

    monkeypatch.setattr(cephla, "_WHEEL_CACHE_PATH", str(tmp_path / "filter_wheel_position.json"))
    cephla.cache_wheel_state({1: cephla.WheelRecord(1, 0, cephla.host_motion_config())})


def _as_homed(wheel):
    """These tests build the controller with skip_init=True only to avoid hardware init, and then exercise a
    wheel whose position is known. Mark it so, as a successful home by this process would."""
    for wheel_id in wheel._configs:
        wheel._position_known[wheel_id] = True
        wheel._restored_unverified[wheel_id] = False  # established here, not restored: "already there" may be skipped
    return wheel


def _config(motor_slot: int = 3, slots: int = 8) -> SquidFilterWheelConfig:
    return SquidFilterWheelConfig(
        max_index=slots, min_index=1, offset=0.008, motor_slot_index=motor_slot, transitions_per_revolution=4000
    )


def _wheel(wrap=True, slots=8):
    mc = MagicMock()
    mc.firmware_version = (1, 4)
    cfg = _config(slots=slots)
    w = _as_homed(SquidFilterWheel(mc, cfg, skip_init=True))
    w.wrap = wrap
    return w, mc, cfg


TURN = SquidFilterWheel._usteps_per_turn()


@pytest.mark.parametrize(
    "delta,expected", [(1, 1), (3, 3), (4, 4), (5, -3), (7, -1), (-1, -1), (-4, 4), (-7, 1), (0, 0)]
)
def test_shortest_slot_delta_prefers_the_short_way_and_forward_on_ties(delta, expected):
    assert SquidFilterWheel._shortest_slot_delta(delta, 8) == expected


@pytest.mark.parametrize("delta, expected", [(1, 1), (3, 3), (4, -3), (-3, -3), (-4, 3), (6, -1), (7, 0)])
def test_shortest_slot_delta_on_an_odd_wheel(delta, expected):
    assert SquidFilterWheel._shortest_slot_delta(delta, 7) == expected


def test_wrap_is_off_on_firmware_before_1_4():
    w, mc, _ = _wheel()
    mc.firmware_version = (1, 3)
    assert w._wrap_enabled() is False
    mc.firmware_version = (1, 4)
    assert w._wrap_enabled() is True


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
    seq = [3, 6, 1, 4, 7, 2, 5, 8, 3]  # keeps going forward around the wheel
    for k in seq:
        w.set_filter_wheel_position({1: k})
    targets = [c.args[0] for c in mc.move_w_to_usteps.call_args_list]
    assert all(b > a for a, b in zip(targets, targets[1:]))  # monotonic: never unwinds
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


def test_re_homes_once_the_net_turn_count_reaches_the_bound(caplog):
    """Shortest-path cycles that net to a full turn grow the absolute target without bound.

    1 -> 4 -> 7 -> 1 on an 8-slot wheel is three forward moves that come back to the same slot
    one turn on, so a long acquisition adds a turn per cycle. The absolute MOVETO payload is
    four bytes, and Microcontroller._move_axis_to_usteps raises ValueError once the target no
    longer fits - not one of the recoverable move errors, so it would take the acquisition down.
    Re-homing re-anchors the coordinate at zero.
    """
    w, mc, cfg = _wheel()
    w._positions[1] = 1
    w._turns[1] = SquidFilterWheel.REHOME_AFTER_TURNS

    w.set_filter_wheel_position({1: 4})

    mc.home_w.assert_called_once()
    assert w._turns[1] == 0
    target = mc.move_w_to_usteps.call_args_list[-1].args[0]
    assert abs(target) < abs(TURN), f"target {target} is not within one turn ({TURN}) of zero"
    assert target == SquidFilterWheel._target_pos_to_usteps(cfg, 4)
    assert w._positions[1] == 4


def test_no_re_home_just_below_the_bound():
    """The bound is a ceiling, not a periodic re-home: one turn short of it nothing changes."""
    w, mc, cfg = _wheel()
    w._positions[1] = 1
    w._turns[1] = SquidFilterWheel.REHOME_AFTER_TURNS - 1

    w.set_filter_wheel_position({1: 4})

    mc.home_w.assert_not_called()
    assert w._turns[1] == SquidFilterWheel.REHOME_AFTER_TURNS - 1
    expected = SquidFilterWheel._target_pos_to_usteps(cfg, 4) + (SquidFilterWheel.REHOME_AFTER_TURNS - 1) * TURN
    mc.move_w_to_usteps.assert_called_once_with(expected)


def test_wrap_ini_key_off_keeps_every_move_on_the_flag_free_arc(monkeypatch):
    """SQUID_FILTERWHEEL_WRAP is read at construction so an ini override is honoured.

    With it False a 1 -> 8 move takes the long way round (+7 slots) rather than one slot
    backward across the index flag, which is what a wheel whose flag does stop it needs.
    """
    import control._def

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_WRAP", False)

    mc = MagicMock()
    mc.firmware_version = (1, 4)
    cfg = _config()
    w = _as_homed(SquidFilterWheel(mc, cfg, skip_init=True))

    assert w.wrap is False
    assert w._wrap_enabled() is False

    w.set_filter_wheel_position({1: 8})

    expected = SquidFilterWheel._target_pos_to_usteps(cfg, 8)
    mc.move_w_to_usteps.assert_called_once_with(expected)
    assert w._turns[1] == 0
    # The long way round never goes past the home reference, so no negative target.
    assert expected * TURN > 0, f"target {expected} must have the same sign as a forward turn ({TURN})"


@pytest.mark.parametrize("fw, expect_wrap", [((1, 4), False), ((1, 5), False), ((1, 6), True), ((2, 0), True)])
def test_the_default_is_auto_on_from_firmware_1_6_and_off_below(fw, expect_wrap):
    """Crossing the flag was verified on firmware 1.6; below it the wheel keeps the flag-free arc unless the
    machine's ini says True."""
    import control._def

    assert control._def.SQUID_FILTERWHEEL_WRAP == "auto"
    mc = MagicMock()
    mc.firmware_version = fw
    w = _as_homed(SquidFilterWheel(mc, _config(), skip_init=True))
    assert w.wrap == "auto" and w._wrap_enabled() is expect_wrap
    w.set_filter_wheel_position({1: 8})
    long_way = SquidFilterWheel._target_pos_to_usteps(_config(), 8)
    expected = long_way - TURN if expect_wrap else long_way  # one slot back across the flag, or seven forward
    mc.move_w_to_usteps.assert_called_once_with(expected)
    assert w._turns[1] == (-1 if expect_wrap else 0)


@pytest.mark.parametrize(
    "setting, fw, enabled",
    [
        (True, (1, 3), False),  # a backward wrap lands on a negative target, which firmware before 1.4 rejects
        (True, (1, 4), True),  # an explicit True is the operator's word that this machine was checked
        (True, (1, 6), True),
        (False, (1, 6), False),
        ("auto", (1, 5), False),
        ("Auto", (1, 6), True),  # the ini reader hands strings through as typed
    ],
)
def test_wrap_setting_against_firmware(monkeypatch, setting, fw, enabled):
    import control._def

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_WRAP", setting)
    mc = MagicMock()
    mc.firmware_version = fw
    assert SquidFilterWheel(mc, _config(), skip_init=True)._wrap_enabled() is enabled


@pytest.mark.parametrize("bad", ["off", "yes", 2, 1.0, None])  # 1 and 0 are what an ini yields: accepted
def test_a_mistyped_wrap_setting_is_an_error_not_a_silent_on(monkeypatch, bad):
    import control._def

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_WRAP", bad)
    mc = MagicMock()
    mc.firmware_version = (1, 6)
    with pytest.raises(ValueError, match="squid_filterwheel_wrap"):
        SquidFilterWheel(mc, _config(), skip_init=True)


@pytest.mark.parametrize(
    "setting, fw, wraps",
    [("auto", (1, 6), True), ("auto", (1, 4), False), (True, (1, 4), True), (False, (1, 6), False)],
)
def test_next_at_the_last_slot_follows_the_setting_and_the_firmware(monkeypatch, setting, fw, wraps):
    """What the GUI's Next button gets at the last slot: one slot across the flag, or nothing."""
    import control._def

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_WRAP", setting)
    mc = MagicMock()
    mc.firmware_version = fw
    w = _as_homed(SquidFilterWheel(mc, _config(), skip_init=True))
    w._positions[1] = 8
    mc.move_w_to_usteps.reset_mock()
    w.next_position(1)
    assert mc.move_w_to_usteps.called is wraps
    assert w.get_filter_wheel_position()[1] == (1 if wraps else 8)


def test_wrap_is_parsed_on_assignment_and_a_typo_is_refused_there():
    w, mc, cfg = _wheel(wrap="auto")
    assert w.wrap == "auto"
    w.wrap = True
    assert w.wrap is True
    with pytest.raises(ValueError, match="squid_filterwheel_wrap"):
        w.wrap = "off"
    assert w.wrap is True  # unchanged by the refused assignment


def test_an_explicit_true_below_firmware_1_4_is_warned_about(caplog):
    import control._def
    import logging

    with pytest.MonkeyPatch.context() as m:
        m.setattr(control._def, "SQUID_FILTERWHEEL_WRAP", True)
        mc = MagicMock()
        mc.firmware_version = (1, 3)
        with caplog.at_level(logging.WARNING):
            w = SquidFilterWheel(mc, _config(), skip_init=True)
    assert w._wrap_enabled() is False
    assert any("squid_filterwheel_wrap = True needs firmware" in r.getMessage() for r in caplog.records)


def test_controllers_without_a_rotary_wrap_keep_their_ends():
    from squid.abc import AbstractFilterWheelController

    assert AbstractFilterWheelController.position_is_known(MagicMock(), 1) is True
    assert not hasattr(AbstractFilterWheelController, "wraps_around")  # the buttons ask next/previous instead
