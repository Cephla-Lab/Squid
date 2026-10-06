import threading
import time
from configparser import ConfigParser
from pathlib import Path

import pytest

import control._def
from control.laser_engine_rev1 import EngineOptions, LaserEngineRev1, LaserEngineRev1Error, save_source_power_mw
from control.laser_engine_rev1_link import EngineLink
from control.laser_engine_rev1_sim import FakeEngine, FakeSource
from control.laser_engine_rev1_status import LineState

NO_CALIBRATIONS = Path(__file__).parent / "no_such_calibration_dir"


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


def _with_source(source=None, options=None):
    source = source or FakeSource()  # 200-1000 mW, like the DF unit's limits
    fake = FakeEngine(tok_delay_polls=0)
    engine = LaserEngineRev1(
        link_factory=lambda: EngineLink(fake),
        source_factory=lambda: source,
        query_interval_s=0.01,
        options=options,
        calibration_dir=NO_CALIBRATIONS,
    )
    engine.open()
    return engine, fake, source


def _to_ready(engine, source):
    engine.wake_up("L3")  # arm, LINE3:EN 1, queue the source enable
    for _ in range(5):
        engine.source_step()
    status = engine.poll_once()
    assert status.channels["L3"].state == LineState.READY, status.channels["L3"]


def test_starts_at_the_source_minimum_then_the_operator_power():
    engine, fake, source = _with_source()
    assert engine.set_source_power_mw(500.0) == 500.0  # set in the tab before the source is on
    assert source.calls == []  # stored only: the source is off
    _to_ready(engine, source)
    assert "LINE3:SET 0.000" in fake.sent  # the AOM dark: no intensity asked yet
    assert source.calls[:2] == ["power 200.0", "enable"]  # the source's own minimum first
    assert "power 500.0" in source.calls and source.calls.count("enable") == 1


def test_key_not_cycled_raises_without_touching_line3_or_source():
    source = FakeSource()
    source.needs_key = True
    engine, fake, _ = _with_source(source)
    with pytest.raises(LaserEngineRev1Error, match="560 key OFF then ON"):
        engine.wait_until_ready(["L3"], timeout_s=30.0)
    assert "LINE3:EN 1" not in fake.sent and "enable" not in source.calls


def test_source_switched_off_elsewhere_is_rewoken():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    source.enabled = False  # e.g. switched off on its own front panel
    engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.SOURCE_OFF
    engine.wake_up("L3")
    engine.source_step()
    assert source.calls.count("enable") == 2


def test_disarm_disables_source():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.disarm()
    engine.source_step()
    assert source.calls[-1] == "disable" and not source.enabled


def test_engine_side_disarm_disables_source():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    fake.armed = False  # the firmware disarmed on its own (HOST_LOST / key off)
    engine.poll_once()
    engine.source_step()
    assert source.calls[-1] == "disable"


def test_connection_lost_disables_source():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    fake.unplug()
    engine.poll_once()
    engine.source_step()
    assert source.calls[-1] == "disable"


def test_l3_error_disables_source():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    fake.faults = ["OVERTEMP"]  # a system fault latched: every line, L3 included, reads FAULT
    assert engine.poll_once().channels["L3"].state == LineState.FAULT
    engine.source_step()
    assert source.calls[-1] == "disable" and not source.enabled


def test_put_to_sleep_l3_disables_source():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.put_to_sleep("L3")
    engine.source_step()
    assert "LINE3:EN 0" in fake.sent and source.calls[-1] == "disable" and not source.enabled


def test_idle_off():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.source_idle_off_s = 0.0
    time.sleep(0.01)
    engine.poll_once()
    engine.source_step()
    assert source.calls[-1] == "disable"


def test_idle_off_minutes_zero_means_24_h():
    engine, _, _ = _with_source(options=EngineOptions(source_idle_off_min=0))
    assert engine.source_idle_off_s == 24 * 3600
    engine.set_source_idle_off_min(5)
    assert engine.source_idle_off_s == 300
    with pytest.raises(ValueError):
        engine.set_source_idle_off_min(-1)


def _refuse_once(fake, command):
    """The engine refuses `command` once (e.g. a transient expander error), then accepts it again."""
    real_reply, left = fake._reply, [1]

    def reply(cmd):
        if cmd == command and left[0]:
            left[0] -= 1
            return "ERR simulated refusal"
        return real_reply(cmd)

    fake._reply = reply


def test_refused_aom_close_is_sent_again():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.set_line_intensity(3, 50.0)
    _refuse_once(fake, "LINE3:SET 0.000")
    with pytest.raises(LaserEngineRev1Error, match="simulated refusal"):
        engine.set_line_intensity(3, 0.0)
    n_sent = len(fake.sent)
    engine.set_line_intensity(3, 0.0)  # the AOM never closed: the retry must send the close again
    assert "LINE3:SET 0.000" in fake.sent[n_sent:] and fake.lines[2]["target"] == 0.0


def test_refused_aom_open_is_sent_again():
    engine, fake, source = _with_source()
    _to_ready(engine, source)  # the AOM at 0 V (nothing asked)
    _refuse_once(fake, "LINE3:SET 2.500")
    with pytest.raises(LaserEngineRev1Error, match="simulated refusal"):
        engine.set_line_intensity(3, 50.0)
    n_sent = len(fake.sent)
    engine.set_line_intensity(3, 50.0)  # the AOM never opened: the retry must send it again
    assert "LINE3:SET 2.500" in fake.sent[n_sent:] and fake.lines[2]["target"] == pytest.approx(2.5)


def test_slow_source_does_not_delay_heartbeat():
    source = FakeSource()
    source.poll_delay_s = 0.5
    engine, fake, _ = _with_source(source)
    engine.start()
    time.sleep(0.4)
    engine.close()
    assert fake.sent.count("STAT?") >= 8  # 0.01 s polls keep running while every source poll takes 0.5 s


def test_ready_only_once_power_has_settled():
    engine, fake, source = _with_source()
    engine.set_source_power_mw(500.0)
    engine.wake_up("L3")
    for _ in range(3):  # enable at 200 mW + starting; starting; ready at 200 mW with 500 mW just applied
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.STARTING
    engine.source_step()  # now reading 500 mW
    assert engine.poll_once().channels["L3"].state == LineState.READY


def test_acquisition_use_keeps_the_source_on():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.source_idle_off_s = 0.5
    engine._source_last_use -= 1.0  # rewind instead of sleeping: deterministic, not scheduling-dependent
    engine.note_use(["L1", "L3"])  # Squid's acquisition, every FOV
    engine.poll_once()
    engine.source_step()
    engine._source_last_use -= 1.0
    engine.channel_keys_for_wavelengths([561])  # live view / acquisition start on the L3 port
    engine.poll_once()
    engine.source_step()
    assert "disable" not in source.calls
    engine._source_last_use -= 1.0
    engine.note_use(["L1"])  # an acquisition without L3 does not count
    engine.poll_once()
    engine.source_step()
    assert source.calls[-1] == "disable"


def test_source_found_on_at_connect_is_switched_off():
    source = FakeSource()
    source.enabled = True  # left on by a session that crashed
    engine, fake, _ = _with_source(source)
    assert source.calls == ["disable"] and not source.enabled
    assert any("was on at connect" in n for n in engine.notices)
    assert engine.poll_once().channels["L3"].state == LineState.NOT_ARMED


def test_request_during_the_start_still_starts_at_the_minimum():
    engine, fake, source = _with_source()
    engine.wake_up("L3")  # enable queued at the 200 mW minimum, nothing set yet
    engine.set_source_power_mw(500.0)  # before the source thread has run the enable
    engine.source_step()
    assert source.calls == ["power 200.0", "enable"]  # not 500 mW during the start-up
    for _ in range(3):
        engine.source_step()
    assert source.calls[-1] == "power 500.0"


def test_refusing_source_becomes_a_fault_until_reset():
    source = FakeSource()
    source.fail_enable = True
    engine, fake, _ = _with_source(source)
    for _ in range(engine.SOURCE_ENABLE_ATTEMPTS):
        engine.wake_up("L3")
        engine.source_step()
    with pytest.raises(LaserEngineRev1Error, match="enable failed"):
        engine.wait_until_ready(["L3"], timeout_s=5.0)
    engine.fault_reset()  # the operator's reset also clears the source error
    engine.source_step()
    assert not engine._source_status.fault


def test_no_source_is_not_an_error_until_requested():
    fake = FakeEngine(tok_delay_polls=0)
    engine = LaserEngineRev1(link_factory=lambda: EngineLink(fake), query_interval_s=0.01)
    engine.open()
    status = engine.poll_once()
    assert status.channels["L3"].state == LineState.NOT_CONFIGURED and not status.any_error()
    with pytest.raises(LaserEngineRev1Error, match="not configured"):
        engine.wait_until_ready(["L3"], timeout_s=1.0)


def test_startup_bring_up_includes_the_560():
    engine, fake, source = _with_source()
    engine.on_startup()
    for _ in range(6):
        engine.poll_once()
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY
    assert source.calls[:2] == ["power 200.0", "enable"] and engine.bringup_state == "done"


def test_bring_up_waits_for_the_560_key_cycle():
    source = FakeSource()
    source.needs_key = True  # key-locked after a power-up
    engine, fake, _ = _with_source(source)
    engine.on_startup()
    for _ in range(3):
        engine.poll_once()
        engine.source_step()
    status = engine.poll_once()
    assert status.channels["L3"].state == LineState.NEEDS_KEY and status.channels["L1"].state == LineState.READY
    assert engine.bringup_state == "running" and "LINE3:EN 1" not in fake.sent and "enable" not in source.calls
    assert any("L3 waits for the operator" in n for n in engine.notices)
    source.needs_key = False  # the operator turned the 560 key OFF then ON
    for _ in range(6):
        engine.source_step()
        engine.poll_once()
    assert engine.poll_once().channels["L3"].state == LineState.READY and engine.bringup_state == "done"


# ---- a failed disable is retried; an enable is never queued twice -----------------------------------------------------


def test_failed_disable_is_retried():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    source.fail_disable = 1  # the source's own disable() raises once (e.g. a serial hiccup)
    engine.disarm()
    engine.source_step()  # this disable fails; the source is still on
    assert source.enabled
    engine.poll_once()  # not wanted and still reads ready: the step above queued another disable
    engine.source_step()
    assert not source.enabled


def test_enable_is_not_repeated_while_the_first_start_is_polled():
    class CallerDuringPoll(FakeSource):
        """A caller (wake_up) runs during the source thread's own poll, right after the first enable."""

        def __init__(self):
            super().__init__()
            self.engine = None
            self.fired = False

        def poll(self):
            if self.engine is not None and not self.fired and self.calls.count("enable") == 1:
                self.fired = True
                self.engine.wake_up("L3")  # what a live-view / set-intensity caller does on another thread
            return super().poll()

    source = CallerDuringPoll()
    engine, fake, _ = _with_source(source)
    source.engine = engine
    engine.wake_up("L3")
    for _ in range(6):
        engine.source_step()
    assert source.calls.count("enable") == 1


# ---- concurrent wake / disable; the reconcile survives link loss ------------------------------------------------------


def test_concurrent_wake_during_line3_set_enables_once():
    """A second wake_up / wait_until_ready caller lands inside the LINE3:SET round-trip. The lock around the
    enable_pending check-then-set makes the second call a no-op."""
    engine, fake, source = _with_source()
    real_cmd = engine._cmd
    fired = [False]

    def cmd_with_nested_wake(line):
        if line.startswith("LINE3:SET") and not fired[0]:
            fired[0] = True
            engine._wake_source()  # a second caller's wake, landing in this window
        return real_cmd(line)

    engine._cmd = cmd_with_nested_wake
    engine.wake_up("L3")
    for _ in range(4):
        engine.source_step()
    assert source.calls.count("enable") == 1


def test_aom_set_racing_the_wake_line3_set_stays_consistent():
    """Another thread's AOM set-point (0 V) lands inside the wake's LINE3:SET round-trip (the requested 50 %, 2.5 V).
    _aom_lock orders the two sends, so the engine ends at the cached value, not at the wake's."""
    engine, fake, source = _with_source()
    engine.set_line_intensity(3, 50.0)  # the 560 intensity asked before the source is on: the wake sends 2.5 V
    real_cmd = engine._cmd
    helpers = []

    def cmd_with_racing_close(line):
        if line.startswith("LINE3:SET") and not helpers:
            t = threading.Thread(target=engine._set_aom_volts, args=(0.0,))
            helpers.append(t)
            t.start()
            t.join(0.2)  # blocks on _aom_lock until the wake's send is done: no deadlock, just a wait
        return real_cmd(line)

    engine._cmd = cmd_with_racing_close
    engine.wake_up("L3")
    for t in helpers:
        t.join()  # the close has run: the outcome does not depend on scheduling
    assert helpers
    assert [c for c in fake.sent if c.startswith("LINE3:SET")][-1] == "LINE3:SET 0.000" and engine._aom_volts == 0.0


def test_disable_racing_the_enable_put_stays_consistent():
    """A disable from another thread lands between _wake_source setting _source_want_on = True and its own
    queue.put(("enable", ...)). The lock serialises the two, so the source's enabled state and _source_want_on
    agree once both threads are done."""
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    source.enabled = False  # e.g. switched off on its own front panel: L3 reads SOURCE_OFF, _source_want_on still True
    engine.source_step()
    engine.poll_once()
    real_put = engine._source_queue.put
    helpers = []

    def put_with_interleaved_disable(item, *a, **kw):
        if item[0] == "enable":
            t = threading.Thread(target=engine._disable_source)
            helpers.append(t)
            t.start()
            t.join(0.2)  # blocks on _source_lock until this put's caller releases it: no deadlock, just a wait
        return real_put(item, *a, **kw)

    engine._source_queue.put = put_with_interleaved_disable
    engine.wake_up("L3")
    for t in helpers:
        t.join()  # the disable has run before the step: the outcome does not depend on scheduling
    assert helpers
    engine.source_step()
    assert source.enabled == engine._source_want_on


def test_failed_disable_after_link_loss_is_retried():
    """Once the engine link is lost, poll_once() returns None and the poll thread stops calling _after_poll, so a
    failed disable is retried by source_step itself, with no further poll_once() at all."""
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    source.fail_disable = 1
    fake.unplug()
    engine.poll_once()  # link lost -> _on_lost() -> _disable_source() queues a disable
    for _ in range(3):
        engine.source_step()  # no poll_once() in between: the reconcile must live in source_step, not _after_poll
    assert not source.enabled


# ---- the operator's 560 laser power (ruling 2026-10-06) ------------------------------------------------------------------


def test_source_power_defaults_to_the_minimum():
    engine, fake, source = _with_source()
    assert engine.source_power_setpoint_mw == 200.0
    _to_ready(engine, source)
    assert source.power_mw == pytest.approx(200.0) and "power 200.0" in source.calls


def test_saved_source_power_is_used_at_the_start():
    engine, fake, source = _with_source(options=EngineOptions(source_power_mw=600))
    assert engine.source_power_setpoint_mw == 600.0
    _to_ready(engine, source)
    assert source.calls[:2] == ["power 200.0", "enable"] and source.calls[-1] == "power 600.0"
    assert source.power_mw == pytest.approx(600.0)


def test_saved_source_power_outside_the_limits_is_clamped_and_said():
    engine, fake, source = _with_source(options=EngineOptions(source_power_mw=5000))
    assert engine.source_power_setpoint_mw == 1000.0
    assert any("saved 560 nm laser power 5000 mW is outside" in n and "using 1000 mW" in n for n in engine.notices)


def test_set_source_power_clamps_to_the_limits():
    engine, fake, source = _with_source()  # 200-1000 mW
    assert engine.set_source_power_mw(5000.0) == 1000.0 and engine.source_power_setpoint_mw == 1000.0
    assert engine.set_source_power_mw(50.0) == 200.0
    assert engine.notices[-1].startswith("560 nm laser set to 200 mW") and "50 mW is outside" in engine.notices[-1]
    with pytest.raises(ValueError):
        engine.set_source_power_mw(float("nan"))


def test_set_source_power_is_applied_when_on():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    assert engine.set_source_power_mw(600.0) == 600.0
    assert engine.notices[-1] == "560 nm laser set to 600 mW"
    engine.source_step()
    assert source.calls[-1] == "power 600.0" and source.power_mw == pytest.approx(600.0)
    assert engine.poll_once().channels["L3"].state == LineState.READY  # settled at the new power
    mark = len(fake.sent)
    engine.set_line_intensity(3, 30.0)  # Squid's intensity: the AOM only
    engine.source_step()
    assert source.calls[-1] == "power 600.0" and "LINE3:SET 1.500" in fake.sent[mark:]


def test_set_source_power_while_starting_is_applied_once_ready():
    engine, fake, source = _with_source()
    engine.wake_up("L3")
    engine.source_step()  # enabled at the minimum, starting
    engine.set_source_power_mw(700.0)
    engine.source_step()  # still starting
    assert source.calls == ["power 200.0", "enable"]  # not during the start-up
    assert engine.poll_once().channels["L3"].state == LineState.STARTING
    engine.source_step()  # ready: now the operator's power
    assert source.calls[-1] == "power 700.0" and source.power_mw == pytest.approx(700.0)
    engine.source_step()  # its next reading is at 700 mW: settled
    assert engine.poll_once().channels["L3"].state == LineState.READY


def test_set_source_power_while_off_applies_at_the_next_start():
    engine, fake, source = _with_source()
    engine.set_source_power_mw(450.0)
    assert "applies when the 560 is next switched on" in engine.notices[-1] and source.calls == []
    _to_ready(engine, source)
    assert source.power_mw == pytest.approx(450.0)


def test_set_source_power_without_a_source_raises():
    fake = FakeEngine(tok_delay_polls=0)
    engine = LaserEngineRev1(link_factory=lambda: EngineLink(fake), query_interval_s=0.01)
    engine.open()
    with pytest.raises(LaserEngineRev1Error, match="560 nm source not configured") as e:
        engine.set_source_power_mw(500.0)
    assert e.value.channel_key == "L3" and engine.source_power_setpoint_mw is None


def test_save_source_power_writes_the_ini_key_and_keeps_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "LASER_ENGINE_REV1_SOURCE_POWER_MW", None)
    ini = tmp_path / "configuration_test.ini"
    ini.write_text(
        "[GENERAL]\nuse_laser_engine_rev1 = True\nlaser_engine_rev1_sn = 123\n\n[VIEWS]\nenable_ndviewer = false\n"
    )
    assert save_source_power_mw(600.0, path=str(ini)) is True
    config = ConfigParser()
    config.read(ini)
    assert config.get("GENERAL", "laser_engine_rev1_source_power_mw") == "600"
    assert control._def.conf_attribute_reader(config.get("GENERAL", "laser_engine_rev1_source_power_mw")) == 600
    assert (
        config.get("GENERAL", "use_laser_engine_rev1") == "True"
        and config.get("GENERAL", "laser_engine_rev1_sn") == "123"
    )
    assert config.get("VIEWS", "enable_ndviewer") == "false"
    assert control._def.LASER_ENGINE_REV1_SOURCE_POWER_MW == 600.0  # this session's later connects use it too
    assert save_source_power_mw(612.5, path=str(ini))  # overwritten, not duplicated
    config = ConfigParser()
    config.read(ini)
    assert config.get("GENERAL", "laser_engine_rev1_source_power_mw") == "612.5"


def test_save_source_power_defaults_to_the_cached_ini_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "LASER_ENGINE_REV1_SOURCE_POWER_MW", None)
    ini = tmp_path / "configuration_test.ini"
    ini.write_text("[GENERAL]\nuse_laser_engine_rev1 = True\n")
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", str(ini))
    assert save_source_power_mw(300) is True and "laser_engine_rev1_source_power_mw = 300" in ini.read_text()
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", str(tmp_path / "missing.ini"))
    assert save_source_power_mw(400) is False  # logged, not raised
    assert not (tmp_path / "missing.ini").exists() and control._def.LASER_ENGINE_REV1_SOURCE_POWER_MW == 300.0
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", None)
    assert save_source_power_mw(400) is False


# ---- the shutter is safety only (ruling 2026-10-06) ----------------------------------------------------------------------


def test_shutter_is_mcu_controlled_from_connect():
    engine, fake, source = _with_source()
    assert "SHUT:SRC MCU" in fake.sent and fake.shut_src == "MCU"
    assert not any(c.startswith("SHUT:SRC TTL") for c in fake.sent)


def test_shutter_opens_when_l3_is_ready_and_is_never_sent_per_exposure():
    engine, fake, source = _with_source()
    engine.wake_up("L3")
    engine.source_step()
    engine.poll_once()  # L3 still starting
    assert not any(c.startswith("SHUT:OPEN") for c in fake.sent)
    for _ in range(4):
        engine.source_step()
    engine.poll_once()  # L3 READY: the shutter opens
    assert fake.sent.count("SHUT:OPEN 1") == 1 and fake.shut_open
    for pct in (30.0, 60.0, 0.0, 100.0):  # live view / acquisition: intensity per channel switch
        engine.light_source.set_intensity(560, pct)
        engine.note_use(["L3"])
        engine.poll_once()
        engine.source_step()
    engine.set_source_power_mw(800.0)
    for _ in range(3):
        engine.source_step()
        engine.poll_once()
    assert fake.sent.count("SHUT:OPEN 1") == 1 and "SHUT:OPEN 0" not in fake.sent and fake.shut_open


def test_shutter_closes_on_idle_off():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    assert fake.shut_open
    engine.source_idle_off_s = 0.0
    time.sleep(0.01)
    engine.poll_once()  # idle: the source goes off and the shutter closes
    assert fake.sent[-1] == "SHUT:OPEN 0" and not fake.shut_open
    engine.source_step()
    assert source.calls[-1] == "disable"


def test_shutter_closes_on_sleep_and_disarm():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    assert fake.shut_open
    engine.put_to_sleep("L3")
    assert not fake.shut_open  # LINE3:EN 0 closes it in the firmware (PERMIT3 needs line 3 on)
    engine.poll_once()
    assert fake.sent.count("SHUT:OPEN 1") == 1  # not re-opened
    _to_ready(engine, source)
    engine.poll_once()
    assert fake.shut_open and fake.sent.count("SHUT:OPEN 1") == 2
    engine.disarm()
    engine.poll_once()
    assert not fake.shut_open and fake.sent.count("SHUT:OPEN 1") == 2


def test_shutter_left_open_with_the_source_off_is_closed_by_the_driver():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    engine.put_to_sleep("L3")
    fake.shut_open = True  # a firmware that did not close it with line 3
    engine.poll_once()
    assert fake.sent[-1] == "SHUT:OPEN 0" and not fake.shut_open


def test_shutter_closes_on_an_l3_error():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    fake.faults = ["OVERTEMP"]  # every line, L3 included, reads FAULT
    engine.poll_once()
    assert fake.sent[-1] == "SHUT:OPEN 0" and not fake.shut_open
    engine.source_step()
    assert source.calls[-1] == "disable"


def test_shutter_closes_when_the_key_is_turned_off_and_reopens_only_at_ready():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    assert fake.shut_open
    source.needs_key = True  # the operator turns the 560 key off
    engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.NEEDS_KEY
    assert fake.sent[-1] == "SHUT:OPEN 0" and not fake.shut_open
    engine.source_step()
    engine.poll_once()
    assert not fake.shut_open and "disable" not in source.calls  # still wanted: the key cycle brings it back
    source.needs_key = False  # key OFF then ON
    engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY
    assert fake.shut_open and fake.sent.count("SHUT:OPEN 1") == 2


def test_shutter_closes_when_the_source_switches_itself_off_and_the_restart_is_behind_it():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    assert fake.shut_open
    source.enabled = False  # off on its own (its front panel or its own protection)
    engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.SOURCE_OFF
    assert fake.sent[-1] == "SHUT:OPEN 0" and not fake.shut_open
    engine.wake_up("L3")  # the next use restarts it
    engine.source_step()
    assert source.calls.count("enable") == 2
    assert engine.poll_once().channels["L3"].state == LineState.STARTING and not fake.shut_open
    for _ in range(4):
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY and fake.shut_open


def _lose_reply(fake, command, nth=1):
    """The engine acts on the nth `command` but its reply is lost: the link reports no reply (connection lost)."""
    real_write, seen = fake.write, [0]

    def write(data):
        if data.decode().strip() == command:
            seen[0] += 1
            if seen[0] == nth:
                fake.sent.append(command)
                fake.shut_open = command == "SHUT:OPEN 1"
                return
        real_write(data)

    fake.write = write


@pytest.mark.parametrize("nth", [1, 2])  # 1: the poll's close (same wake_up), 2: _wake_source's own close
def test_connection_lost_on_a_restart_close_does_not_start_the_source(nth):
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    source.enabled = False  # off on its own
    engine.source_step()
    _lose_reply(fake, "SHUT:OPEN 0", nth)
    engine.wake_up("L3")  # sees SOURCE_OFF, closes the shutter, restarts the source
    assert engine.is_connection_lost() and not engine._source_want_on
    for _ in range(4):
        engine.source_step()
    assert source.calls.count("enable") == 1 and not source.enabled  # no second start after the link was lost


def test_source_restarting_by_itself_between_polls_closes_the_shutter():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    assert fake.shut_open
    source.enable()  # off and on again between two polls (front panel): it reads starting, never off
    engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.STARTING
    assert fake.sent[-1] == "SHUT:OPEN 0" and not fake.shut_open
    for _ in range(3):
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY and fake.shut_open
