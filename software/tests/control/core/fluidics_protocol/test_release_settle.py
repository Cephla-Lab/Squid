"""release_and_settle on a fake clock: a held line springs back after the valve move, a free one does not."""

import math

import pytest

from control.core.fluidics_protocol.release_settle import ReleaseFailed, release_and_settle, summary
from control.models.fluidics_protocol import ReleaseAndSettle


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class Valve:
    """FluidicsPort's release-and-settle half; `flow(s since the move)` is pushed on every focus reading."""

    def __init__(self, clock, port=25, flow=None, error=None):
        self.clock = clock
        self.port = port
        self.flow = flow
        self.error = error
        self.opened = []
        self.moved_at = None
        self.callback = None

    def current_port(self):
        return self.port

    def open_port(self, port, timeout=60.0):
        if self.error:
            raise RuntimeError(self.error)
        self.opened.append(port)
        self.port = port
        self.moved_at = self.clock.now()

    def subscribe_flow(self, callback):
        if self.flow is None:
            return None
        self.callback = callback
        return lambda: setattr(self, "callback", None)

    def push(self):
        if self.callback is not None:
            since = None if self.moved_at is None else self.clock.now() - self.moved_at
            self.callback(self.flow(since), 0.0)


class Focus:
    """ImagingPort.measure_focus_um: `um(s since the move or None before it)`; one reading costs 0.1 s."""

    def __init__(self, clock, valve, um):
        self.clock = clock
        self.valve = valve
        self.um = um

    def measure_focus_um(self):
        if self.um is None:
            return None
        self.valve.push()
        since = None if self.valve.moved_at is None else self.clock.now() - self.valve.moved_at
        value = self.um(since)
        self.clock.sleep(0.1)
        return value


def held_line(since):
    """Pulled in 20 um until the valve moves, then springs back with a 2 s time constant."""
    return -20.0 if since is None else -20.0 * math.exp(-since / 2.0)


def inflow_pulse(since):
    """10 uL/min resting offset; 3 uL comes in over the 2 s after the move (90 uL/min above rest)."""
    return 10.0 + (90.0 if since is not None and since < 2.0 else 0.0)


def _run(um, flow=None, port=25, error=None, settings=None, should_stop=lambda: False):
    clock = Clock()
    valve = Valve(clock, port=port, flow=flow, error=error)
    focus = Focus(clock, valve, um)
    settings = settings or ReleaseAndSettle(release_port=30, alternate_port=15)
    outcome = release_and_settle(settings, valve, focus, should_stop=should_stop, now=clock.now, sleep=clock.sleep)
    return outcome, valve


def test_a_held_line_springs_back_then_settles_and_its_rise_and_inflow_are_recorded():
    outcome, valve = _run(held_line, flow=inflow_pulse)
    r = outcome.record
    assert valve.opened == [30] and r.port_before == 25 and r.release_port == 30
    assert r.mode == "laser_af" and r.settled is True
    assert r.baseline_um == pytest.approx(-20.0)
    assert r.rise_um == pytest.approx(20.0, abs=0.3)
    assert 10.0 <= r.settle_s < 20.0  # needs two quiet windows after the spring-back
    assert abs(r.last_change_um) < 0.2
    assert r.inflow_ul == pytest.approx(3.0, abs=0.3)  # sampled every 0.35 s here
    assert outcome.series[0][0] < 0 < outcome.series[-1][0]  # baseline before the move, the rest after
    line = summary(r)
    assert line.startswith("valve 25 -> 30, rise +19.9") and "settled in" in line and "inflow +2.8" in line


def test_a_free_line_settles_after_two_windows_with_no_rise():
    outcome, _ = _run(lambda since: 1.5, flow=lambda since: 10.0)
    r = outcome.record
    assert r.settled is True and r.settle_s == pytest.approx(10.0, abs=0.3)
    assert r.rise_um == pytest.approx(0.0) and r.inflow_ul == pytest.approx(0.0)


def test_a_coverslip_that_keeps_moving_is_not_settled_at_the_timeout():
    outcome, _ = _run(lambda since: 0.0 if since is None else 0.5 * since)
    r = outcome.record
    assert r.settled is False and r.settle_s is None
    assert r.waited_s == pytest.approx(90.0, abs=0.3)
    assert (
        "90 s after the valve move the last 5 s window still changed +2.50 um" in r.message
        and "tolerance 0.2 um" in r.message
    )
    assert r.inflow_ul is None  # no flow sensor
    assert "NOT settled" in summary(r)


def test_on_the_release_port_already_the_valve_goes_to_the_alternate_port():
    outcome, valve = _run(lambda since: 0.0, port=30)
    assert valve.opened == [15] and outcome.record.release_port == 15
    outcome, valve = _run(lambda since: 0.0, port=30, settings=ReleaseAndSettle(release_port=30))
    assert valve.opened == [30]


def test_without_laser_af_it_waits_a_fixed_time_after_the_move():
    outcome, valve = _run(None, settings=ReleaseAndSettle(release_port=30, fixed_wait_s=12))
    r = outcome.record
    assert valve.opened == [30] and r.mode == "fixed_wait" and r.settled is None
    assert r.waited_s == pytest.approx(12.0, abs=0.01) and r.message == "laser AF not available"
    assert r.rise_um is None and outcome.series == []


def test_no_spot_before_the_move_falls_back_to_the_fixed_wait():
    outcome, valve = _run(lambda since: float("nan"))
    r = outcome.record
    assert valve.opened == [30] and r.mode == "fixed_wait" and r.message == "no laser AF spot before the valve move"
    assert r.waited_s == pytest.approx(15.0, abs=0.01)


def test_a_spot_lost_after_the_move_is_not_settled():
    outcome, _ = _run(lambda since: 0.0 if since is None else float("nan"))
    assert outcome.record.settled is False and outcome.record.message == "no laser AF reading in the last 5 s"


def test_a_failed_valve_move_raises():
    with pytest.raises(ReleaseFailed, match="valve jammed"):
        _run(held_line, error="valve jammed")


def test_a_stop_request_ends_the_wait():
    calls = []

    def stop():
        calls.append(1)
        return len(calls) > 30  # after the baseline and a few readings after the move

    outcome, valve = _run(held_line, should_stop=stop)
    assert outcome.stopped and valve.opened == [30] and outcome.record.waited_s < 10
