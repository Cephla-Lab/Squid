"""Release-and-settle before an imaging row (`imaging.release_and_settle` in the protocol header).

Some fluidic lines hold suction after a syringe draw: the coverslip stays pulled in until the selector valve
moves to a free port, then springs back over several seconds. Imaging during that spring-back puts the first
FOVs out of focus or starts laser AF on a moving surface. So, before the acquisition starts:

1. one window of laser AF displacement readings where the stage is (z is not moved) - the baseline;
2. the selector valve to the release port (the alternate port when it is already there);
3. readings until the medians of the last two windows differ by less than the tolerance (settled), or the
   timeout. Without a laser AF reading the wait is a fixed time after the move instead.

The first flow sensor, when there is one, is watched throughout: the volume it sees after the move (in the
draw direction, net of its reading before the move) is the liquid the released line let in.
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


class ReleaseFailed(RuntimeError):
    """The valve could not be moved to the release port."""


@dataclass
class SettleOutcome:
    record: ReleaseSettleRecord
    series: List[Tuple[float, Optional[float]]] = field(default_factory=list)  # (s after the move, um | None)
    stopped: bool = False  # should_stop() ended the wait early


def _valid(readings) -> List[float]:
    return [v for _t, v in readings if v is not None and not math.isnan(v)]


def _median(values: List[float]) -> Optional[float]:
    return float(statistics.median(values)) if values else None


def _net_volume_ul(flow: List[Tuple[float, Optional[float]]], t_move: float) -> Optional[float]:
    """Trapezoid integral of (flow - its median before the move) from the move on; uL/min x s -> uL."""
    before = [f for t, f in flow if t < t_move and f is not None]
    after = [(t, f) for t, f in flow if t >= t_move and f is not None]
    if not after:
        return None
    rest = float(statistics.median(before)) if before else 0.0
    total = 0.0
    for (t0, f0), (t1, f1) in zip(after, after[1:]):
        total += 0.5 * ((f0 - rest) + (f1 - rest)) * (t1 - t0) / 60.0
    return total


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
        if not stopped:
            try:
                fluidics.open_port(port)
            except Exception as e:
                raise ReleaseFailed(str(e)) from e

        if stopped:
            message = "stopped before the valve move"
        elif mode == "fixed_wait":
            while now() - t_move < settings.fixed_wait_s:
                if should_stop():
                    stopped = True
                    break
                pace(min(now() + SAMPLE_INTERVAL_S, t_move + settings.fixed_wait_s))
        else:
            settled = False
            w = settings.window_s
            while True:
                if should_stop():
                    stopped = True
                    break
                t = now()
                readings.append((t, read()))
                elapsed = t - t_move
                if elapsed >= 2 * w:
                    tail = _valid([r for r in readings if t - w < r[0] <= t])
                    prev = _valid([r for r in readings if t - 2 * w < r[0] <= t - w])
                    if len(tail) >= MIN_READINGS_PER_WINDOW and len(prev) >= MIN_READINGS_PER_WINDOW:
                        last_change = _median(tail) - _median(prev)
                        final = _median(tail)
                        if abs(last_change) < settings.tolerance_um:
                            settled, settle_s = True, elapsed
                            break
                if elapsed >= settings.timeout_s:
                    break
                pace(t + SAMPLE_INTERVAL_S)
            if final is None:
                final = _median(_valid([r for r in readings if r[0] > now() - w and r[0] >= t_move]))
            if not stopped and not settled:
                if final is None:
                    message = f"no laser AF reading in the last {w:g} s"
                else:
                    change = "n/a" if last_change is None else f"{last_change:+.2f} um"
                    message = (
                        f"{settings.timeout_s:g} s after the valve move the last {w:g} s window still changed "
                        f"{change} (tolerance {settings.tolerance_um:g} um)"
                    )
        waited = now() - t_move
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
        baseline_um=baseline if mode == "laser_af" else None,
        final_um=final,
        rise_um=None if final is None or baseline is None or mode != "laser_af" else final - baseline,
        last_change_um=last_change,
        inflow_ul=_net_volume_ul(flow, t_move) if unsubscribe is not None else None,
        message=message,
    )
    series = [(round(t - t_move, 3), None if v is None else round(v, 3)) for t, v in readings]
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
