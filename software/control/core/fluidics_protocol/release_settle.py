"""Release-and-settle before an imaging row (`imaging.release_and_settle` in the protocol header).

Some fluidic lines hold suction after a syringe draw: the coverslip stays pulled in until the selector valve
moves to a free port, then springs back over several seconds. Imaging during that spring-back puts the first
FOVs out of focus or starts laser AF on a moving surface. So, before the acquisition starts:

1. one window of laser AF displacement readings where the stage is (z is not moved) - the baseline;
2. the selector valve to the release port (the alternate port when it is already there);
3. readings until the medians of the last two windows differ by less than the tolerance (settled), or the
   timeout. Without a laser AF reading the wait is a fixed time instead. Settle time, timeout and fixed wait
   all count from when the valve reached the port, not from when it started to turn.

A window only counts when the spot was found in most of its readings (MIN_VALID_PERCENT) and in the newest
one: a spot lost part-way (a rebound beyond the laser AF range looks like that) is not settled, it waits on.

The first flow sensor, when there is one, is watched throughout (also for one window before the move when
laser AF is not available): the volume it sees from the move on (in the draw direction, net of its reading
before the move) is the liquid the released line let in; without a reading before the move it is unknown.
Qt-free; the runner drives it on its own thread through the two ports."""

import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from control.models.fluidics_protocol import ReleaseAndSettle
from control.models.fluidics_run import ReleaseSettleRecord

SAMPLE_INTERVAL_S = 0.25  # at most 4 readings a second; a reading itself takes a few camera frames
MIN_READINGS_PER_WINDOW = 2
MIN_VALID_PERCENT = 80  # of a window's readings that must have found the spot
VALVE_POLL_S = 0.05  # how often a valve move in flight is checked for a stop request
VALVE_TIMEOUT_S = 60.0  # a valve that is not there by then is aborted and the release fails
VALVE_STOP_S = 10.0  # how long an aborted move may take to end


class ReleaseFailed(RuntimeError):
    """The valve could not be moved to the release port."""


def _move_valve(fluidics, port: int, should_stop, tick, now) -> bool:
    """Move the valve, polling for a stop request like an ordinary fluidics step does. True once it is on
    `port`; False when a stop request aborted the move (which has ended by then). Raises ReleaseFailed when
    the move is refused, fails, times out (aborted first) or does not end after the abort."""
    try:
        return _drive_valve_move(fluidics.start_valve_move(port), port, should_stop, tick, now)
    except ReleaseFailed:
        raise
    except Exception as e:  # the engine refused the move or broke while it was in flight
        raise ReleaseFailed(str(e)) from e


def _drive_valve_move(move, port: int, should_stop, tick, now) -> bool:
    deadline = now() + VALVE_TIMEOUT_S
    while not move.wait(VALVE_POLL_S):
        tick()
        stop = should_stop()
        if stop or now() >= deadline:
            move.abort()
            if not move.wait(VALVE_STOP_S):
                raise ReleaseFailed(
                    f"the selector valve move to port {port} did not stop within {VALVE_STOP_S:g} s of the abort"
                )
            if stop:
                return False
            raise ReleaseFailed(f"the selector valve did not reach port {port} within {VALVE_TIMEOUT_S:g} s")
    failure = move.failure()
    if failure is not None:
        raise ReleaseFailed(failure)
    return True


@dataclass
class SettleOutcome:
    record: ReleaseSettleRecord
    # (s after the valve reached the port - negative: the baseline before the move, um | None)
    series: List[Tuple[float, Optional[float]]] = field(default_factory=list)
    stopped: bool = False  # should_stop() ended the wait early


def _valid(readings) -> List[float]:
    return [v for _t, v in readings if v is not None and not math.isnan(v)]


def _median(values: List[float]) -> Optional[float]:
    return float(statistics.median(values)) if values else None


def _net_volume_ul(flow: List[Tuple[float, Optional[float]]], t_move: float) -> Optional[float]:
    """Trapezoid integral of (flow - its median before the move) from the move on; uL/min x s -> uL. None
    without a reading before the move: a sensor's resting offset would otherwise count as inflow."""
    before = [f for t, f in flow if t < t_move and f is not None]
    after = [(t, f) for t, f in flow if t >= t_move and f is not None]
    if not before or not after:
        return None
    rest = float(statistics.median(before))
    total = 0.0
    for (t0, f0), (t1, f1) in zip(after, after[1:]):
        total += 0.5 * ((f0 - rest) + (f1 - rest)) * (t1 - t0) / 60.0
    return total


def _unreliable(tail, prev, w: float) -> Optional[str]:
    """Why the last two windows (all readings, newest last) cannot decide settled; None when they can."""
    for name, window in (("last", tail), ("previous", prev)):
        found = len(_valid(window))
        if found < MIN_READINGS_PER_WINDOW or 100 * found < MIN_VALID_PERCENT * len(window):
            return (
                f"the laser AF spot was found in only {found} of {len(window)} readings of the {name} {w:g} s "
                f"window (needs {MIN_VALID_PERCENT} %)"
            )
    if tail[-1][1] is None:
        return "the newest laser AF reading found no spot"
    return None


def release_and_settle(
    settings: ReleaseAndSettle,
    fluidics,
    imaging,
    should_stop: Callable[[], bool] = lambda: False,
    tick: Callable[[], None] = lambda: None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> SettleOutcome:
    """Raises ReleaseFailed when the valve move fails; everything else is in the returned record."""
    flow: List[Tuple[float, Optional[float]]] = []
    unsubscribe = fluidics.subscribe_flow(lambda ul_min, _ts: flow.append((now(), ul_min)))
    readings: List[Tuple[float, Optional[float]]] = []  # (absolute time, um | None)

    def read() -> Optional[float]:
        value = imaging.measure_focus_um()
        return None if value is None or math.isnan(value) else float(value)

    def pace(until: float) -> None:
        tick()
        remaining = until - now()
        if remaining > 0:
            sleep(remaining)

    try:
        mode, message = "laser_af", None
        t0 = now()
        first = imaging.measure_focus_um()
        if first is None:
            mode, message = "fixed_wait", "laser AF not available"
            if unsubscribe is not None:  # the inflow still needs the sensor's reading before the move
                while now() - t0 < settings.window_s and not should_stop():
                    pace(min(now() + SAMPLE_INTERVAL_S, t0 + settings.window_s))
        else:
            readings.append((t0, None if math.isnan(first) else float(first)))
            pace(t0 + SAMPLE_INTERVAL_S)
            while now() - t0 < settings.window_s and not should_stop():
                t = now()
                readings.append((t, read()))
                pace(t + SAMPLE_INTERVAL_S)
            if len(_valid(readings)) < MIN_READINGS_PER_WINDOW:
                mode, message = "fixed_wait", "no laser AF spot before the valve move"
        baseline = _median(_valid(readings))

        port_before = fluidics.current_port()
        port = settings.release_port
        if port_before == settings.release_port and settings.alternate_port is not None:
            port = settings.alternate_port
        t_move = now()
        settled: Optional[bool] = None
        settle_s = last_change = None
        final = None
        stopped = should_stop()
        valve_move_s = None
        if stopped:
            message = "stopped before the valve move"
        elif not _move_valve(fluidics, port, should_stop, tick, now):
            stopped, message = True, "stopped during the valve move"
        else:
            valve_move_s = now() - t_move
        t_arrived = now()  # the settle windows, the timeout and the fixed wait count from here

        if stopped:
            pass  # nothing to wait for
        elif mode == "fixed_wait":
            while now() - t_arrived < settings.fixed_wait_s:
                if should_stop():
                    stopped = True
                    break
                pace(min(now() + SAMPLE_INTERVAL_S, t_arrived + settings.fixed_wait_s))
        else:
            settled = False
            w = settings.window_s
            unreliable = None
            while True:
                if should_stop():
                    stopped = True
                    break
                t = now()
                readings.append((t, read()))
                elapsed = t - t_arrived
                if elapsed >= 2 * w:
                    tail = [r for r in readings if t - w < r[0] <= t]
                    prev = [r for r in readings if t - 2 * w < r[0] <= t - w]
                    unreliable = _unreliable(tail, prev, w)
                    if unreliable is None:
                        final = _median(_valid(tail))
                        last_change = final - _median(_valid(prev))
                        if abs(last_change) < settings.tolerance_um:
                            settled, settle_s = True, elapsed
                            break
                    else:  # what an earlier window said no longer holds
                        final = last_change = None
                if elapsed >= settings.timeout_s:
                    break
                pace(t + SAMPLE_INTERVAL_S)
            if final is None:
                final = _median(_valid([r for r in readings if r[0] > now() - w and r[0] >= t_arrived]))
            if not stopped and not settled:
                if final is None:
                    message = f"no laser AF reading in the last {w:g} s"
                elif unreliable is not None:
                    message = f"{settings.timeout_s:g} s after the valve move {unreliable}"
                else:
                    change = "n/a" if last_change is None else f"{last_change:+.2f} um"
                    message = (
                        f"{settings.timeout_s:g} s after the valve move the last {w:g} s window still changed "
                        f"{change} (tolerance {settings.tolerance_um:g} um)"
                    )
        waited = now() - t_arrived
    finally:
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception:
                pass

    record = ReleaseSettleRecord(
        port_before=port_before,
        release_port=port,
        mode=mode,
        settled=None if mode == "fixed_wait" else settled,
        settle_s=settle_s,
        waited_s=waited,
        valve_move_s=valve_move_s,
        baseline_um=baseline if mode == "laser_af" else None,
        final_um=final,
        rise_um=None if final is None or baseline is None or mode != "laser_af" else final - baseline,
        last_change_um=last_change,
        inflow_ul=_net_volume_ul(flow, t_move) if unsubscribe is not None else None,
        message=message,
    )
    series = [(round(t - t_arrived, 3), None if v is None else round(v, 3)) for t, v in readings]
    return SettleOutcome(record=record, series=series, stopped=stopped)


def summary(record: ReleaseSettleRecord) -> str:
    """One run.log line."""
    parts = [f"valve {record.port_before if record.port_before is not None else '?'} -> {record.release_port}"]
    if record.mode == "fixed_wait":
        parts.append(f"fixed wait {record.waited_s:.1f} s ({record.message})")
    else:
        if record.rise_um is not None:
            parts.append(f"rise {record.rise_um:+.2f} um (baseline {record.baseline_um:+.2f} um)")
        if record.settled:
            parts.append(f"settled in {record.settle_s:.1f} s")
        else:
            parts.append(f"NOT settled after {record.waited_s:.1f} s")
    if record.inflow_ul is not None:
        parts.append(f"inflow {record.inflow_ul:+.2f} uL")
    return ", ".join(parts)
