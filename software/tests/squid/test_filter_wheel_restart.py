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
    """{wheel_id: (slot, turns)} from the record on disk. The motion configuration the record also carries has its
    own tests below, and _recorded_config() reads it."""
    return {k: (v["position"], v["turns"]) for k, v in json.loads(path.read_text())["wheels"].items()}


def _recorded_config(path, wheel_id=1):
    return json.loads(path.read_text())["wheels"][str(wheel_id)]["config"]


def _in_force(slot, turns, wheel_id=1, **overrides):
    """Write a record that says the wheel is on `slot` and its driver is configured the way this host would
    configure it now - the case where a --skip-init restart may leave the hardware completely alone."""
    config = dict(cephla.host_motion_config(), **overrides)
    cephla.cache_wheel_state({wheel_id: cephla.WheelRecord(slot, turns, config)})


TURN = SquidFilterWheel._usteps_per_turn()


# ---------------------------------------------------------------- a normal start: unknown until homed
def test_a_fresh_wheel_is_unknown_until_it_is_homed(cache_path):
    w = SquidFilterWheel(_mc(), _config(), skip_init=False)
    assert w.position_is_known() is False
    assert not cache_path.exists() or _record(cache_path) == {}
    w.home()
    assert w.position_is_known() is True
    assert _record(cache_path) == {"1": (1, 0)}


def test_every_successful_move_is_recorded(cache_path):
    w = SquidFilterWheel(_mc(), _config(), skip_init=False)
    w.home()
    w.wrap = True
    w.set_filter_wheel_position({1: 8})  # one slot back across the flag
    assert _record(cache_path) == {"1": (8, -1)}
    w.set_filter_wheel_position({1: 3})
    assert _record(cache_path) == {"1": (3, 0)}


# ---------------------------------------------------------------- the restart: stay on the same channel
def test_a_restart_restores_slot_and_turns_without_touching_the_wheel(cache_path):
    _in_force(5, 2)
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    assert w.get_filter_wheel_position() == {1: 5}
    assert w._turns[1] == 2 and w.position_is_known() is True
    mc.home_w.assert_not_called()
    mc.move_w_to_usteps.assert_not_called()
    mc.init_filter_wheel.assert_not_called()


def test_the_restored_turn_count_keeps_the_driver_coordinate_continuous(cache_path):
    _in_force(8, 2)
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    w.wrap = True
    w.set_filter_wheel_position({1: 1})  # one slot forward across the flag, onto turn 3
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(_config(), 1) + 3 * TURN)
    assert _record(cache_path) == {"1": (1, 3)}


def test_the_first_move_after_a_restore_is_always_sent(cache_path):
    """The record may be one move old (a process that died mid-move). The move is absolute, so sending it to a
    wheel that is already there is harmless, and skipping it could leave the wrong filter in the path."""
    _in_force(5, 0)
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
    assert _record(cache_path) == {"1": (1, 0)}


def test_one_wheel_can_be_restored_while_the_other_is_unknown(cache_path):
    _in_force(4, 0)
    configs = {1: _config(3), 2: _config(4)}
    w = SquidFilterWheel(_mc(), configs, skip_init=True)
    assert w.position_is_known(1) is True and w.position_is_known(2) is False
    assert w.position_is_known() is False


# ---------------------------------------------------------------- failures withdraw the record
def test_a_home_withdraws_the_record_until_it_has_succeeded(cache_path):
    _in_force(5, 1)
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
    assert _record(cache_path) == {"1": (1, 0)}
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


# ------------------------------------------- the motion configuration the driver was left with (the ini can change)
# A --skip-init restart does not reset the controller: the driver keeps the microstepping, current and velocity /
# acceleration the PREVIOUS process configured, while this process computes slot addresses from the ini it has just
# read. A tuned profile (64 -> 8 microsteps) between the two would otherwise send every move to the wrong slot with
# nothing said. The record therefore carries the configuration that was in force, and a mismatch costs one re-configure
# and one home - and the recorded slot is driven to afterwards, so the system still comes up on the same channel.
@pytest.fixture
def at_8_microsteps(monkeypatch):
    """This host configures 8 microsteps per full step; the record below says the driver was left at 64."""
    import control._def

    monkeypatch.setattr(control._def, "MICROSTEPPING_DEFAULT_W", 8)
    return 8


def test_a_changed_microstepping_re_configures_homes_and_returns_to_the_recorded_slot(cache_path, at_8_microsteps):
    _in_force(5, 2, microstepping=64)  # the previous session ran the wheel at 64 usteps/FS
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)

    mc.init_filter_wheel.assert_called_once()  # the driver is put back in step with the host...
    mc.configure_squidfilter.assert_called_once()
    mc.home_w.assert_called_once()  # ...re-anchored by a home (a microstep is not the same length any more)...
    # ...and driven back to the slot the previous process was on, at the NEW microstepping
    assert mc.move_w_to_usteps.call_args_list[-1].args == (SquidFilterWheel._target_pos_to_usteps(_config(), 5),)
    assert w.get_filter_wheel_position() == {1: 5} and w.position_is_known() is True
    assert w._turns[1] == 0  # the home re-anchored the coordinate: the turn count restarts, the slot is what matters
    assert _record(cache_path) == {"1": (5, 0)}
    assert _recorded_config(cache_path)["microstepping"] == 8


def test_a_record_from_before_the_configuration_was_tracked_costs_one_home(cache_path):
    """The older format (version 1, no configuration): nothing can be concluded about the driver, so it is
    re-configured and re-homed, and the recorded slot is returned to."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text('{"version": 1, "wheels": {"1": {"position": 5, "turns": 3}}}')
    mc = _mc()
    w = SquidFilterWheel(mc, _config(), skip_init=True)
    mc.configure_squidfilter.assert_called_once()
    mc.home_w.assert_called_once()
    assert w.get_filter_wheel_position() == {1: 5} and w.position_is_known() is True
    assert _recorded_config(cache_path) == cephla.host_motion_config()


@pytest.mark.parametrize(
    "overrides",
    [
        {"microstepping": 16},  # anything but the ini's own value
        {"current_ma": 900.0},
        {"i_hold": 0.9},
        {"max_velocity": 1.5},
        {"max_acceleration": 42.0},
    ],
)
def test_any_changed_motion_setting_is_noticed_not_only_the_microstepping(cache_path, overrides):
    _in_force(5, 0, **overrides)
    mc = _mc()
    SquidFilterWheel(mc, _config(), skip_init=True)
    mc.configure_squidfilter.assert_called_once()
    mc.home_w.assert_called_once()


def test_a_wheel_whose_configuration_still_matches_is_left_alone_while_the_other_is_re_homed(cache_path):
    """W was left by this profile, W2 by another (its record was written before the configuration was tracked).
    Only W2 may be touched: re-homing W would throw away the channel the record exists to keep."""
    from control._def import AXIS

    cephla.cache_wheel_state(
        {
            1: cephla.WheelRecord(5, 2, cephla.host_motion_config()),
            2: cephla.WheelRecord(3, 0, None),
        }
    )
    mc = _mc()
    w = SquidFilterWheel(mc, {1: _config(3), 2: _config(4)}, skip_init=True)
    assert [c.args for c in mc.init_filter_wheel.call_args_list] == [(AXIS.W2,)]
    mc.home_w.assert_not_called()
    mc.home_w2.assert_called_once()
    assert w.get_filter_wheel_position() == {1: 5, 2: 3}
    assert w._turns == {1: 2, 2: 0}
    assert _record(cache_path) == {"1": (5, 2), "2": (3, 0)}


def test_a_home_that_fails_on_that_path_leaves_the_position_unknown_instead_of_claiming_a_slot(cache_path):
    _in_force(5, 2, microstepping=16)  # the previous session left the driver at another microstepping
    mc = _mc()
    mc.home_w.side_effect = TimeoutError("W never acked")
    w = SquidFilterWheel(mc, _config(), skip_init=True)  # comes up anyway: the GUI must still start
    assert w.position_is_known() is False
    assert _record(cache_path) == {}  # nothing claims slot 5
    mc.home_w.assert_called_once()


def test_a_normal_start_records_the_configuration_it_sent(cache_path):
    w = SquidFilterWheel(_mc(), _config(), skip_init=False)
    w.home()
    assert _recorded_config(cache_path) == cephla.host_motion_config()


def test_a_configuration_is_a_match_only_when_it_states_every_key_and_agrees(monkeypatch):
    wanted = cephla.host_motion_config()
    assert cephla.motion_configs_match(dict(wanted), wanted) is True
    assert cephla.motion_configs_match(dict(wanted, microstepping=int(wanted["microstepping"])), wanted) is True
    assert cephla.motion_configs_match(None, wanted) is False  # an older record, or none at all
    assert cephla.motion_configs_match({}, wanted) is False
    assert cephla.motion_configs_match({k: v for k, v in wanted.items() if k != "i_hold"}, wanted) is False
    assert cephla.motion_configs_match(dict(wanted, max_velocity=wanted["max_velocity"] + 0.01), wanted) is False
    assert "microstepping" in cephla.describe_motion_config_difference(dict(wanted, microstepping=16), wanted)
    assert "record" in cephla.describe_motion_config_difference(None, wanted)


@pytest.mark.parametrize(
    "contents",
    [
        '{"version": 2, "wheels": {"1": {"position": 5, "turns": 0, "config": "nonsense"}}}',
        '{"version": 2, "wheels": {"1": {"position": 5, "turns": 0, "config": {"microstepping": "64"}}}}',
        '{"version": 3, "wheels": {"1": {"position": 5, "turns": 0}}}',  # written by a newer version: unreadable
    ],
)
def test_a_record_this_software_cannot_make_sense_of_is_no_record_at_all(cache_path, contents):
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(contents)
    assert cephla.load_cached_wheel_state() == {}


def test_a_plain_slot_and_turn_pair_can_still_be_written(cache_path):
    """cache_wheel_state() keeps taking (slot, turns); such a record states no configuration, so a restart that
    reads it re-configures and re-homes rather than trusting the driver."""
    cephla.cache_wheel_state({1: (5, 2)})
    assert cephla.load_cached_wheel_state() == {1: cephla.WheelRecord(5, 2, None)}


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


def test_a_restart_homes_only_the_wheel_that_does_not_know_its_position(cache_path, monkeypatch):
    """Review finding on #657: W1 restored on slot 5, no record for W2. The restart homed BOTH, so W1 lost the
    channel the record exists to keep. Only W2 may be homed."""
    import squid.config

    _in_force(5, 2)
    mc = _mc()
    w = SquidFilterWheel(mc, {1: _config(3), 2: _config(4)}, skip_init=True)
    assert w.position_is_known(1) is True and w.position_is_known(2) is False
    microscope, addons = _addons(w)
    monkeypatch.setattr(squid.config, "get_filter_wheel_config", lambda: types.SimpleNamespace(indices=[1, 2]))
    microscope.MicroscopeAddons.prepare_for_use(addons, skip_init=True)
    mc.home_w.assert_not_called()
    mc.home_w2.assert_called_once()
    assert w.get_filter_wheel_position() == {1: 5, 2: 1}
    assert w._turns[1] == 2 and w.position_is_known() is True
    assert _record(cache_path) == {"1": (5, 2), "2": (1, 0)}


def test_one_wheel_failing_to_home_on_restart_does_not_stop_the_next(cache_path, monkeypatch):
    import squid.config

    wheel = MagicMock()
    wheel.position_is_known.return_value = False
    wheel.home.side_effect = [TimeoutError("W never acked"), None]
    microscope, addons = _addons(wheel)
    monkeypatch.setattr(squid.config, "get_filter_wheel_config", lambda: types.SimpleNamespace(indices=[1, 2]))
    microscope.MicroscopeAddons.prepare_for_use(addons, skip_init=True)  # no raise
    assert [c.args for c in wheel.home.call_args_list] == [(1,), (2,)]


def test_a_normal_start_still_homes_every_wheel_in_one_call(monkeypatch):
    import squid.config

    wheel = MagicMock()
    microscope, addons = _addons(wheel)
    monkeypatch.setattr(squid.config, "get_filter_wheel_config", lambda: types.SimpleNamespace(indices=[1, 2]))
    microscope.MicroscopeAddons.prepare_for_use(addons, skip_init=False)
    wheel.home.assert_called_once_with()


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
