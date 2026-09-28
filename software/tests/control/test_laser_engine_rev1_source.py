import threading
import time
from pathlib import Path

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
    engine.set_line_intensity(3, 10.0)  # 100 mW, below the 200 mW minimum
    engine.set_line_intensity(3, 5.0)  # 50 mW: still above 0 %, so clamped too (0 % is dark)
    engine.source_step()
    assert source.power_mw == pytest.approx(200.0)
    assert sum("below the 560 nm minimum" in n for n in engine.notices) == 1
    assert engine.poll_once().channels["L3"].state == LineState.READY  # READY at the clamped power


def test_zero_percent_is_dark():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    n_sent = len(fake.sent)
    engine.light_source.set_intensity(560, 0.0)  # 0 % = dark: the source goes off, not down to its minimum
    assert not any(c.startswith(("ARM", "LINE3:EN", "LINE3:SET")) for c in fake.sent[n_sent:])  # set only, no wake
    engine.source_step()
    assert source.calls[-1] == "disable" and not source.enabled
    assert engine.poll_once().channels["L3"].state == LineState.READY  # dark by request, like any line at 0 %
    engine.set_line_intensity(3, 0.0)
    assert sum("set to 0 %" in n for n in engine.notices) == 1
    n_sent, n_calls = len(fake.sent), len(source.calls)
    assert engine.wait_until_ready(["L3"], timeout_s=1.0) is True
    engine.source_step()
    assert "enable" not in source.calls[n_calls:] and not any(c.startswith("LINE3:SET") for c in fake.sent[n_sent:])
    engine.light_source.set_intensity(560, 10.0)  # 100 mW: the source starts again, at its 200 mW minimum
    for _ in range(4):
        engine.source_step()
    assert source.calls[n_calls:] == ["power 200.0", "enable"] and source.enabled
    assert sum("below the 560 nm minimum" in n for n in engine.notices) == 1
    assert engine.poll_once().channels["L3"].state == LineState.READY


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
    engine.source_idle_off_s = 0.05
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


# ---- review fix round 1: a failed disable is never retried; duplicate enables (t6_probe.py / t6_probe3.py ideas) --------


def test_failed_disable_is_retried():
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    source.fail_disable = 1  # the source's own disable() raises once (e.g. a serial hiccup)
    engine.disarm()
    engine.source_step()  # this disable fails; the source is still on
    assert source.enabled
    engine.poll_once()  # _after_poll: not wanted, still reads ready -> queues another disable
    engine.source_step()
    assert not source.enabled


def test_enable_is_not_repeated_while_the_first_start_is_polled():
    class CallerDuringPoll(FakeSource):
        """t6_probe3's idea: a caller (wake_up) runs on the source thread's own poll, right after the first enable."""

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


# ---- review fix round 2: the reconcile must survive link loss; two more discriminating concurrency tests ----------------


def test_concurrent_wake_during_line3_set_enables_once():
    """Round 2 finding 3's scenario, probe_rr's B: a second wake_up / wait_until_ready thread lands inside the
    LINE3:SET round-trip. The lock around the enable_pending check-then-set must make the second call a no-op."""
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


def test_disable_racing_the_enable_put_stays_consistent():
    """Round 2 finding 3's scenario, probe_rr's C: a disable from another thread lands between _wake_source setting
    _source_want_on = True and its own queue.put(("enable", ...)). The lock serialises the two, so the source's
    enabled state and _source_want_on must agree once both threads are done (before round 1 this could disagree)."""
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    source.enabled = False  # e.g. switched off on its own front panel: L3 reads SOURCE_OFF, _source_want_on still True
    engine.source_step()
    engine.poll_once()
    real_put = engine._source_queue.put

    def put_with_interleaved_disable(item, *a, **kw):
        if item[0] == "enable":
            t = threading.Thread(target=engine._disable_source)
            t.start()
            t.join(0.2)  # blocks on _source_lock until this put's caller releases it: no deadlock, just a wait
        return real_put(item, *a, **kw)

    engine._source_queue.put = put_with_interleaved_disable
    engine.wake_up("L3")
    engine.source_step()
    assert source.enabled == engine._source_want_on


def test_failed_disable_after_link_loss_is_retried():
    """Round 2 finding 1: once the engine link is lost, poll_once() returns None and the poll thread stops calling
    _after_poll - so a failed disable must be retried by source_step itself, with no further poll_once() at all."""
    engine, fake, source = _with_source()
    _to_ready(engine, source)
    source.fail_disable = 1
    fake.unplug()
    engine.poll_once()  # link lost -> _on_lost() -> _disable_source() queues a disable
    for _ in range(3):
        engine.source_step()  # no poll_once() in between: the reconcile must live in source_step, not _after_poll
    assert not source.enabled
