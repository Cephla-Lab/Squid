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

    def __init__(self, clock, port=25, flow=None, error=None, move_s=0.0):
        self.clock = clock
        self.port = port
        self.flow = flow
        self.error = error
        self.move_s = move_s  # valve travel time
        self.opened = []
        self.started_at = None
        self.moved_at = None  # when the valve reached the port
        self.callback = None

    def current_port(self):
        return self.port

    def open_port(self, port, timeout=60.0):
        if self.error:
            raise RuntimeError(self.error)
        self.opened.append(port)
        self.started_at = self.clock.now()
        self.clock.sleep(self.move_s)
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


class SensorClock(Clock):
    """A flow sensor that reports on its own (every sleep), not only alongside a focus reading."""

    valve = None

    def sleep(self, dt):
        super().sleep(dt)
        if self.valve is not None:
            self.valve.push()


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


def _run(um, flow=None, port=25, error=None, settings=None, should_stop=lambda: False, move_s=0.0, clock=None):
    clock = clock or Clock()
    valve = Valve(clock, port=port, flow=flow, error=error, move_s=move_s)
    clock.valve = valve
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


# ---- a lost or patchy laser AF spot must not count as settled ----


def _steady_until(cut_s, um=1.5):
    """Steady at `um`; the spot is lost `cut_s` after the valve reaches the port and does not come back."""
    return lambda since: um if since is None or since <= cut_s else float("nan")


@pytest.mark.parametrize("cut_s", [5.8, 7.0, 9.0])
def test_a_spot_lost_part_way_through_the_windows_is_not_settled(cut_s):
    outcome, _ = _run(_steady_until(cut_s))
    r = outcome.record
    assert r.settled is False and r.settle_s is None
    assert r.waited_s == pytest.approx(90.0, abs=0.3)  # waited for the timeout, then on_timeout decides
    assert r.message == "no laser AF reading in the last 5 s"


def test_a_drifting_surface_whose_spot_is_lost_is_not_settled_on_stale_readings():
    outcome, _ = _run(lambda since: 10.0 if since is None else (10.0 + 0.06 * since if since <= 14.0 else float("nan")))
    assert outcome.record.settled is False


def test_a_spot_found_in_only_half_the_readings_is_not_settled():
    count = {"n": 0}

    def patchy(since):
        count["n"] += 1
        return float("nan") if since is not None and count["n"] % 2 else 1.5

    outcome, _ = _run(patchy)
    r = outcome.record
    assert r.settled is False and r.final_um == pytest.approx(1.5)
    assert "90 s after the valve move the laser AF spot was found in only" in r.message
    assert "of the last 5 s window" in r.message


def test_a_newest_reading_without_a_spot_is_not_settled():
    # the spot is lost just before the first check: both windows are still > 80 % covered, the newest is not
    outcome, _ = _run(_steady_until(9.9), settings=ReleaseAndSettle(release_port=30, timeout_s=10.6))
    r = outcome.record
    assert r.settled is False and r.final_um == pytest.approx(1.5)
    assert r.message == "10.6 s after the valve move the newest laser AF reading found no spot"


def test_an_occasional_missed_reading_still_settles():
    count = {"n": 0}

    def mostly(since):
        count["n"] += 1
        return float("nan") if since is not None and count["n"] % 10 == 0 else 1.5

    outcome, _ = _run(mostly)
    assert outcome.record.settled is True and outcome.record.settle_s < 11.0


# ---- times count from when the valve reached the port ----


def test_the_fixed_wait_starts_when_the_valve_reaches_the_port():
    clock = Clock()
    outcome, valve = _run(None, settings=ReleaseAndSettle(release_port=30, fixed_wait_s=15), move_s=12.0, clock=clock)
    r = outcome.record
    assert clock.now() - valve.moved_at == pytest.approx(15.0, abs=0.01)
    assert r.waited_s == pytest.approx(15.0, abs=0.01) and r.valve_move_s == pytest.approx(12.0)


def test_the_settle_windows_start_when_the_valve_reaches_the_port():
    outcome, valve = _run(lambda since: 1.5, move_s=4.0)
    r = outcome.record
    assert r.settled is True and r.settle_s == pytest.approx(10.0, abs=0.3) and r.valve_move_s == pytest.approx(4.0)
    after = [t for t, _ in outcome.series if t >= 0]
    assert after[0] == pytest.approx(0.0, abs=0.01)  # the series counts from the arrival


# ---- the inflow needs a flow reading before the move ----


def test_without_laser_af_the_flow_baseline_is_still_taken_before_the_move():
    clock = SensorClock()
    outcome, valve = _run(None, flow=lambda since: 10.0, clock=clock)  # a constant 10 uL/min offset
    r = outcome.record
    assert valve.started_at - 1000.0 == pytest.approx(5.0, abs=0.3)  # one window of flow before the move
    assert r.mode == "fixed_wait" and r.inflow_ul == pytest.approx(0.0, abs=1e-6)


def test_without_laser_af_or_flow_sensor_the_valve_moves_at_once():
    outcome, valve = _run(None)
    assert valve.started_at == pytest.approx(1000.0) and outcome.record.inflow_ul is None


def test_no_flow_reading_before_the_move_leaves_the_inflow_unknown():
    outcome, _ = _run(lambda since: 1.5, flow=lambda since: None if since is None else 10.0)
    assert outcome.record.inflow_ul is None
