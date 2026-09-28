import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from control.laser_engine_rev1 import EngineOptions, LaserEngineRev1, LaserEngineRev1Error
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


def test_starts_at_the_source_minimum_then_the_requested_power():
    engine, fake, source = _with_source()
    engine.set_line_intensity(3, 50.0)  # 500 mW requested before the source is on
    _to_ready(engine, source)
    assert "LINE3:SET 5.000" in fake.sent  # AOM analog full scale; TTL3 gates it (and the shutter)
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


def test_below_minimum_clamps_and_warns_once():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.set_line_intensity(3, 5.0)  # 50 mW, below the 200 mW minimum
    engine.set_line_intensity(3, 0.0)  # no AOM: 0 % runs at the minimum too
    engine.source_step()
    assert source.power_mw == pytest.approx(200.0) and source.enabled
    assert sum("below the 560 nm minimum" in n for n in engine.notices) == 1
    assert any("(20 % of 1000 mW)" in n for n in engine.notices)  # the minimum as a % of maximum power
    assert engine.poll_once().channels["L3"].state == LineState.READY  # READY at the clamped power


def test_intensity_floor_percent():
    """Ruling 2026-09-28: the 560 source's own minimum, as a % of maximum power, is the GUI floor - but only when
    the engine cannot go below it in hardware (no AOM in the path)."""
    engine, fake, source = _with_source()  # FakeSource 200-1000 mW
    assert engine.intensity_floor_percent(560) == pytest.approx(20.0)
    assert engine.intensity_floor_percent(488) == 0.0  # not the 560 source line

    aom_engine, _, _ = _with_source(options=EngineOptions(aom_in_path=True))
    assert aom_engine.intensity_floor_percent(560) == 0.0  # the AOM closes for 0 %: no floor needed

    no_source_fake = FakeEngine(tok_delay_polls=0)
    no_source_engine = LaserEngineRev1(link_factory=lambda: EngineLink(no_source_fake), query_interval_s=0.01)
    no_source_engine.open()
    assert no_source_engine.intensity_floor_percent(560) == 0.0

    source.max_power_mw = 0.0  # a source that reported no limits: no floor, not a ZeroDivisionError on a channel switch
    assert engine.intensity_floor_percent(560) == 0.0


def test_below_minimum_warns_on_every_api_request():
    """The tab notice fires once per session, but an API caller must be told on every below-minimum request."""
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine._log = MagicMock()
    engine.set_line_intensity(3, 5.0)  # 50 mW, below the 200 mW minimum
    engine.set_line_intensity(3, 0.0)  # a second below-minimum request
    warnings = [c.args[0] for c in engine._log.warning.call_args_list if "below the 560 nm minimum" in c.args[0]]
    assert len(warnings) == 2
    assert sum("below the 560 nm minimum" in n for n in engine.notices) == 1


def test_zero_percent_without_the_aom_clamps_and_warns_once():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.set_line_intensity(3, 50.0)
    engine.source_step()
    engine.set_line_intensity(3, 0.0)  # the first below-minimum request is 0 %: no AOM, so the minimum + one warning
    engine.set_line_intensity(3, 0.0)
    engine.source_step()
    assert source.power_mw == pytest.approx(200.0) and source.enabled
    assert [n for n in engine.notices if "below the 560 nm minimum" in n] == [
        "560 nm: 0 mW requested is below the 560 nm minimum of 200 mW (20 % of 1000 mW) - running at the minimum"
    ]
    assert not any(c.startswith("LINE3:SET 0.") for c in fake.sent)  # no AOM: line 3 is never closed


def test_zero_percent_with_the_aom_closes_it():
    engine, fake, source = _with_source(options=EngineOptions(aom_in_path=True))  # no AOM calibration file
    _to_ready(engine, source)
    engine.light_source.set_intensity(560, 50.0)
    engine.source_step()
    assert source.power_mw == pytest.approx(500.0)
    n_sent = len(fake.sent)
    engine.light_source.set_intensity(560, 0.0)  # the AOM closed: dark at the sample, the source at its minimum
    engine.source_step()
    assert "LINE3:SET 0.000" in fake.sent[n_sent:] and source.power_mw == pytest.approx(200.0) and source.enabled
    assert engine.poll_once().channels["L3"].state == LineState.READY
    assert not any("below the 560 nm minimum" in n for n in engine.notices)
    n_sent = len(fake.sent)
    engine.light_source.set_intensity(560, 50.0)  # 500 mW: the AOM back to full transmission
    engine.source_step()
    assert [c for c in fake.sent[n_sent:] if c.startswith("LINE3:SET")] == ["LINE3:SET 5.000"]
    assert source.power_mw == pytest.approx(500.0)


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
    engine, fake, source = _with_source(options=EngineOptions(aom_in_path=True))
    _to_ready(engine, source)
    _refuse_once(fake, "LINE3:SET 0.000")
    with pytest.raises(LaserEngineRev1Error, match="simulated refusal"):
        engine.set_line_intensity(3, 0.0)
    n_sent = len(fake.sent)
    engine.set_line_intensity(3, 0.0)  # the AOM never closed: the retry must send the close again
    assert "LINE3:SET 0.000" in fake.sent[n_sent:] and fake.lines[2]["target"] == 0.0


def test_refused_aom_open_is_sent_again():
    engine, fake, source = _with_source(options=EngineOptions(aom_in_path=True))
    _to_ready(engine, source)
    engine.set_line_intensity(3, 0.0)  # the AOM closed
    _refuse_once(fake, "LINE3:SET 5.000")
    with pytest.raises(LaserEngineRev1Error, match="simulated refusal"):
        engine.set_line_intensity(3, 50.0)
    n_sent = len(fake.sent)
    engine.set_line_intensity(3, 50.0)  # the AOM never opened: the retry must send full transmission again
    assert "LINE3:SET 5.000" in fake.sent[n_sent:] and fake.lines[2]["target"] > 0.0


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
    engine.set_line_intensity(3, 50.0)  # 500 mW
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
    engine.wake_up("L3")  # enable queued at the 200 mW minimum, nothing requested yet
    engine.set_line_intensity(3, 50.0)  # 500 mW, before the source thread has run the enable
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


def test_shutter_gates_without_the_aom():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    engine.poll_once()
    assert "SHUT:SRC TTL" in fake.sent and not any(c.startswith("SHUT:OPEN") for c in fake.sent)


def test_shutter_held_open_with_the_aom():
    engine, fake, source = _with_source(options=EngineOptions(aom_in_path=True, shutter_with_aom="open"))
    assert "SHUT:SRC MCU" in fake.sent
    _to_ready(engine, source)  # its last poll sees line 3 READY and opens the shutter
    engine.poll_once()
    assert fake.sent.count("SHUT:OPEN 1") == 1 and fake.shut_open  # opened once; the AOM gates each exposure
    engine.source_idle_off_s = 0.0
    time.sleep(0.01)
    engine.poll_once()  # idle: the source goes off and the shutter closes
    assert fake.sent[-1] == "SHUT:OPEN 0" and not fake.shut_open


def test_shutter_mode_can_be_switched_in_the_tab():
    engine, fake, _ = _with_source(options=EngineOptions(aom_in_path=True))  # default mode: the shutter gates too
    engine.set_shutter_with_aom("open")
    assert fake.sent[-1] == "SHUT:SRC MCU"
    engine.set_shutter_with_aom("gate")
    assert fake.sent[-2:] == ["SHUT:OPEN 0", "SHUT:SRC TTL"]
    engine2, _, _ = _with_source()
    with pytest.raises(ValueError, match="no AOM"):
        engine2.set_shutter_with_aom("open")


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
    """Another thread's AOM set-point (0 % closes the AOM) lands inside the wake's LINE3:SET round-trip. _aom_lock
    orders the two sends, so the engine ends at the cached value, not at the wake's full transmission."""
    engine, fake, source = _with_source(options=EngineOptions(aom_in_path=True))
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
