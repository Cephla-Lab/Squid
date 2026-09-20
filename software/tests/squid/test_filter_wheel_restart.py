"""Where the Squid filter wheel is, across a `--skip-init` restart.

The controller does not report the wheel's position, so the slot and turn count are the host's own bookkeeping.
A restart does not reset the controller and must not move the wheel - the system stays on the same channel - so
the new process is told where the old one left it through a small record in cache/. Nothing is assumed when that
record is missing or unusable: the position is then unknown and the wheel is homed before it is used.

TEMPORARY: to be replaced by a position readback (firmware >= 1.6 can report W; see cephla.py).
"""

import json
import types
from unittest.mock import MagicMock

import pytest

import squid.filter_wheel_controller.cephla as cephla
from control.microcontroller import CommandAborted
from squid.config import SquidFilterWheelConfig
from squid.filter_wheel_controller.cephla import SquidFilterWheel


@pytest.fixture(autouse=True)
def cache_path(tmp_path, monkeypatch):
    path = tmp_path / "cache" / "filter_wheel_position.json"
    monkeypatch.setattr(cephla, "_WHEEL_CACHE_PATH", str(path))
    return path


def _config(motor_slot=3, slots=8):
    return SquidFilterWheelConfig(
        max_index=slots, min_index=1, offset=0.008, motor_slot_index=motor_slot, transitions_per_revolution=4000
    )


def _mc(fw=(1, 6)):
    mc = MagicMock()
    mc.firmware_version = fw
    mc.last_command_aborted_error = None
    return mc


def _record(path):
    return json.loads(path.read_text())["wheels"]


TURN = SquidFilterWheel._usteps_per_turn()


# ---------------------------------------------------------------- a normal start: unknown until homed
def test_a_fresh_wheel_is_unknown_until_it_is_homed(cache_path):
    w = SquidFilterWheel(_mc(), _config(), skip_init=False)
    assert w.position_is_known() is False
    assert not cache_path.exists() or _record(cache_path) == {}
    w.home()
    assert w.position_is_known() is True
    assert _record(cache_path) == {"1": {"position": 1, "turns": 0}}


def test_every_successful_move_is_recorded(cache_path):
    w = SquidFilterWheel(_mc(), _config(), skip_init=False)
    w.home()
    w.wrap = True
    w.set_filter_wheel_position({1: 8})  # one slot back across the flag
    assert _record(cache_path) == {"1": {"position": 8, "turns": -1}}
    w.set_filter_wheel_position({1: 3})
    assert _record(cache_path) == {"1": {"position": 3, "turns": 0}}


# ---------------------------------------------------------------- the restart: stay on the same channel
def test_a_restart_restores_slot_and_turns_without_touching_the_wheel(cache_path):
    cephla.cache_wheel_state({1: (5, 2)})
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    assert w.get_filter_wheel_position() == {1: 5}
    assert w._turns[1] == 2 and w.position_is_known() is True
    mc.home_w.assert_not_called()
    mc.move_w_to_usteps.assert_not_called()
    mc.init_filter_wheel.assert_not_called()


def test_the_restored_turn_count_keeps_the_driver_coordinate_continuous(cache_path):
    cephla.cache_wheel_state({1: (8, 2)})
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    w.wrap = True
    w.set_filter_wheel_position({1: 1})  # one slot forward across the flag, onto turn 3
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(_config(), 1) + 3 * TURN)
    assert _record(cache_path) == {"1": {"position": 1, "turns": 3}}


def test_the_first_move_after_a_restore_is_always_sent(cache_path):
    """The record may be one move old (a process that died mid-move). The move is absolute, so sending it to a
    wheel that is already there is harmless, and skipping it could leave the wrong filter in the path."""
    cephla.cache_wheel_state({1: (5, 0)})
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    w.set_filter_wheel_position({1: 5})
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(_config(), 5))
    w.set_filter_wheel_position({1: 5})  # now established by this process: a repeat is skipped as before
    assert mc.move_w_to_usteps.call_count == 1


# ---------------------------------------------------------------- nothing is assumed
@pytest.mark.parametrize(
    "contents",
    [
        None,  # no file
        "",
        "not json",
        '{"version": 1}',
        '{"version": 1, "wheels": {"1": {"position": 9, "turns": 0}}}',  # slot outside 1..8
        '{"version": 1, "wheels": {"1": {"position": 0, "turns": 0}}}',
        '{"version": 1, "wheels": {"1": {"position": "5", "turns": 0}}}',
        '{"version": 1, "wheels": {"1": {"position": true, "turns": 0}}}',
        '{"version": 1, "wheels": {"1": {"position": 5.0, "turns": 0}}}',
        '{"version": 1, "wheels": {"2": {"position": 5, "turns": 0}}}',  # a record, but not for this wheel
        '{"version": 1, "wheels": [1, 2]}',
        "x" * 5000,
    ],
)
def test_without_a_usable_record_the_position_is_unknown_and_the_wheel_is_homed_before_use(cache_path, contents):
    if contents is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(contents)
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    assert w.position_is_known() is False
    mc.home_w.assert_not_called()  # constructing moves nothing
    w.set_filter_wheel_position({1: 1})  # even "slot 1" is not concluded from an unknown position
    mc.home_w.assert_called_once()
    assert w.position_is_known() is True
    assert _record(cache_path) == {"1": {"position": 1, "turns": 0}}


def test_one_wheel_can_be_restored_while_the_other_is_unknown(cache_path):
    cephla.cache_wheel_state({1: (4, 0)})
    configs = {1: _config(3), 2: _config(4)}
    w = SquidFilterWheel(_mc(), configs, skip_init=True)
    assert w.position_is_known(1) is True and w.position_is_known(2) is False
    assert w.position_is_known() is False


# ---------------------------------------------------------------- failures withdraw the record
def test_a_home_withdraws_the_record_until_it_has_succeeded(cache_path):
    cephla.cache_wheel_state({1: (5, 1)})
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    mc.wait_till_operation_is_completed.side_effect = TimeoutError("no ack")
    with pytest.raises(TimeoutError):
        w.home()
    assert w.position_is_known() is False
    assert _record(cache_path) == {}  # a restart after this must not believe slot 5


def test_a_move_that_fails_even_after_the_re_home_leaves_the_position_unknown(cache_path):
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=False)
    w.home()
    assert _record(cache_path) == {"1": {"position": 1, "turns": 0}}
    # every slot move is refused; homes and their offset move succeed
    offset = SquidFilterWheel._delta_to_usteps(_config().offset)
    state = {"refuse": False}

    def move(usteps):
        state["refuse"] = usteps != offset

    def wait(*a, **k):
        if state["refuse"]:
            state["refuse"] = False
            raise CommandAborted(command_id=1, reason="refused")

    mc.move_w_to_usteps.side_effect = move
    mc.wait_till_operation_is_completed.side_effect = wait
    with pytest.raises(CommandAborted):
        w.set_filter_wheel_position({1: 4})
    assert w.position_is_known() is False
    assert _record(cache_path) == {}


def test_a_failed_write_of_the_record_does_not_fail_the_filter_change(cache_path, monkeypatch):
    blocker = cache_path.parent
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("a file where the cache folder should be")
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=False)
    w.home()
    w.set_filter_wheel_position({1: 3})  # no exception
    assert w.get_filter_wheel_position() == {1: 3}


# ---------------------------------------------------------------- the microscope's restart path
def _addons(wheel):
    import control.microscope as microscope

    addons = types.SimpleNamespace(emission_filter_wheel=wheel, piezo_stage=None, squid_laser_engine=None)
    return microscope, addons


@pytest.mark.parametrize(
    "skip_init, known, homed",
    [
        (False, True, True),  # a normal start always homes
        (False, False, True),
        (True, True, False),  # a restart stays on the same channel when the wheel knows where it is
        (True, False, True),  # and homes a wheel that does not
    ],
)
def test_prepare_for_use_homes_on_restart_only_a_wheel_that_does_not_know_its_position(
    monkeypatch, skip_init, known, homed
):
    import squid.config

    wheel = MagicMock()
    wheel.position_is_known.return_value = known
    microscope, addons = _addons(wheel)
    monkeypatch.setattr(squid.config, "get_filter_wheel_config", lambda: types.SimpleNamespace(indices=[1]))
    microscope.MicroscopeAddons.prepare_for_use(addons, skip_init=skip_init)
    assert wheel.home.called is homed


def test_controllers_with_a_position_readback_keep_the_default():
    from squid.abc import AbstractFilterWheelController

    assert AbstractFilterWheelController.position_is_known(MagicMock()) is True


# ---------------------------------------------------------------- the completion window (firmware >= 1.6)
@pytest.mark.parametrize("skip_init", [False, True])
def test_the_completion_window_is_sent_on_a_normal_start_and_on_a_restart(monkeypatch, skip_init):
    """A restart does not reset the controller, so it still holds the previous session's window; a change made in
    Preferences takes effect through that restart only if the restart sends the configured value."""
    import control._def
    from control._def import AXIS

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_COMPLETION_WINDOW_DEG", 5.0)
    mc = _mc((1, 6))
    SquidFilterWheel(mc, {1: _config(3), 2: _config(4)}, skip_init=skip_init)
    assert [c.args for c in mc.set_completion_window.call_args_list] == [(AXIS.W, 5.0 / 360.0), (AXIS.W2, 5.0 / 360.0)]


def test_zero_is_sent_too_because_the_controller_keeps_the_window_across_a_restart(monkeypatch):
    import control._def
    from control._def import AXIS

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_COMPLETION_WINDOW_DEG", 0.0)
    mc = _mc((1, 6))
    SquidFilterWheel(mc, _config(), skip_init=True)
    mc.set_completion_window.assert_called_once_with(AXIS.W, 0.0)


@pytest.mark.parametrize("window, warns", [(5.0, True), (0.0, False)])
def test_older_firmware_never_receives_the_command(monkeypatch, caplog, window, warns):
    import control._def

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_COMPLETION_WINDOW_DEG", window)
    for fw in [(1, 4), (1, 5)]:
        mc = _mc(fw)
        with caplog.at_level("WARNING"):
            caplog.clear()
            SquidFilterWheel(mc, _config(), skip_init=False)
        mc.set_completion_window.assert_not_called()
        assert any("needs firmware >= 1.6" in r.message for r in caplog.records) is warns


def test_a_negative_window_is_a_configuration_error(monkeypatch):
    import control._def

    monkeypatch.setattr(control._def, "SQUID_FILTERWHEEL_COMPLETION_WINDOW_DEG", -1.0)
    with pytest.raises(ValueError, match="completion_window"):
        SquidFilterWheel(_mc((1, 6)), _config(), skip_init=False)
