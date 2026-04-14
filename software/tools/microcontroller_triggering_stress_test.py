"""Stress test the microcontroller triggering commands.

Mimics the MCU-level commands issued during a multi-channel multi-point
acquisition loop (as observed in main_hcs.log):

    set_illumination(source, intensity)
    send_hardware_trigger(control_illumination=True, illumination_on_time_us=100_000)

...cycling through the D1-D5 laser channels (405 / 488 / 561 / 638 / 730 nm),
with wait_till_operation_is_completed() after each command (matching how the
GUI drives it).

Tracks:
    - commands sent / acked
    - per-command latency (min / avg / p50 / p95 / p99 / max)
    - resends triggered by "command timed out without ack" (from MCU log sniff)
    - retry-exhaustion aborts (CommandAborted)
    - wait_till_operation_is_completed() timeouts

USAGE
-----
Requirements:
    - The Squid GUI must be closed (only one process can hold the MCU serial port).
    - The microcontroller must be powered and connected via USB.
    - Run from the `software/` directory.

Basic (60 second run):
    python3 -m tools.microcontroller_triggering_stress_test --runtime 60

Long soak test (30 minutes):
    python3 -m tools.microcontroller_triggering_stress_test --runtime 1800

Enable debug-level MCU logging:
    python3 -m tools.microcontroller_triggering_stress_test --runtime 60 --verbose

Change illumination on-time (default matches log: 100 ms = 100000 µs):
    python3 -m tools.microcontroller_triggering_stress_test --illum-on-time-us 50000

Slow the loop down:
    python3 -m tools.microcontroller_triggering_stress_test --inter-command-sleep 0.05

Stop early: press Ctrl+C — a partial report is printed before exit.

INTERPRETING RESULTS
--------------------
All counters should be 0 for a healthy MCU:

    resends > 0              MCU missed some acks but recovered via retry.
                             Investigate if the rate is high.
    wait_timeouts > 0        Driver gave up waiting (default 5 s). Bigger problem.
    aborts > 0               Retry limit exhausted; command never completed.

Check latency p99 and max — if they are much larger than p50, there is jitter
that can trigger the 0.5 s ack timeout seen in production logs.

Exit code: 0 if no failures were observed, 1 otherwise.
"""

import argparse
import logging
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, List, Tuple

import control.microcontroller as mc
import squid.logging
from control._def import ILLUMINATION_CODE


log = squid.logging.get_logger("mcu_timeout_stress")


# D1-D5 laser channels — all five source codes cycled each pass.
# (name, illumination_source_code)
DEFAULT_CHANNELS: List[Tuple[str, int]] = [
    ("D1_405nm", ILLUMINATION_CODE.ILLUMINATION_D1),
    ("D2_488nm", ILLUMINATION_CODE.ILLUMINATION_D2),
    ("D3_561nm", ILLUMINATION_CODE.ILLUMINATION_D3),
    ("D4_638nm", ILLUMINATION_CODE.ILLUMINATION_D4),
    ("D5_730nm", ILLUMINATION_CODE.ILLUMINATION_D5),
]


# The MCU logs this at debug level every time it resends a command because
# an ack did not arrive in time. We sniff the logger since there's no public
# counter for it.
_RESEND_RE = re.compile(r"command timed out without ack")
_ABORT_RE = re.compile(r"Command\s+\d+\s+\(.*?\)\s+ABORTED")


class _LogCounter(logging.Handler):
    """Logging handler that counts matches of named regexes."""

    def __init__(self, patterns: dict):
        super().__init__(level=logging.DEBUG)
        self.patterns = patterns
        self.counts = {name: 0 for name in patterns}
        self.samples = {name: [] for name in patterns}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        for name, pattern in self.patterns.items():
            if pattern.search(msg):
                self.counts[name] += 1
                if len(self.samples[name]) < 5:
                    self.samples[name].append(msg)


@dataclass
class Stats:
    sent: int = 0
    completed: int = 0
    wait_timeouts: int = 0
    aborts: int = 0
    other_errors: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    per_command_counts: dict = field(default_factory=dict)

    def record_latency(self, name: str, ms: float) -> None:
        self.latencies_ms.append(ms)
        self.per_command_counts[name] = self.per_command_counts.get(name, 0) + 1


def _send(micro: mc.Microcontroller, stats: Stats, counter: _LogCounter,
          name: str, fn: Callable[[], None], wait_timeout_s: float) -> None:
    """Send one command, wait for completion, update stats. Never raises."""
    t0 = time.time()
    try:
        fn()
        stats.sent += 1
        micro.wait_till_operation_is_completed(timeout_limit_s=wait_timeout_s)
        stats.completed += 1
    except TimeoutError as e:
        stats.wait_timeouts += 1
        log.warning("wait timed out on %s: %s", name, e)
        return
    except mc.CommandAborted as e:
        stats.aborts += 1
        log.error("command %s aborted: %s", name, e)
        try:
            micro.acknowledge_aborted_command()
        except Exception:
            pass
        return
    except Exception as e:
        stats.other_errors += 1
        log.exception("unexpected error sending %s: %s", name, e)
        return
    stats.record_latency(name, (time.time() - t0) * 1000.0)


def run_stress(micro: mc.Microcontroller, args, counter: _LogCounter) -> Stats:
    stats = Stats()
    end_time = time.time() + args.runtime
    last_report_at = time.time()
    iteration = 0

    log.info(
        "Starting stress loop: channels=%s intensity=%.1f%% illum_on_time=%dus",
        [name for name, _ in DEFAULT_CHANNELS], args.intensity, args.illum_on_time_us,
    )

    while time.time() < end_time:
        for ch_name, source_code in DEFAULT_CHANNELS:
            if time.time() >= end_time:
                break
            iteration += 1

            # 1. Set illumination source + intensity for this channel.
            _send(
                micro, stats, counter,
                name=f"set_illumination[{ch_name}]",
                fn=lambda sc=source_code: micro.set_illumination(sc, args.intensity),
                wait_timeout_s=args.wait_timeout,
            )

            # 2. Send hardware trigger holding illumination on for illum_on_time_us.
            _send(
                micro, stats, counter,
                name="send_hardware_trigger",
                fn=lambda: micro.send_hardware_trigger(
                    control_illumination=True,
                    illumination_on_time_us=args.illum_on_time_us,
                ),
                wait_timeout_s=args.wait_timeout,
            )

            if args.inter_command_sleep > 0:
                time.sleep(args.inter_command_sleep)

            now = time.time()
            if now - last_report_at >= args.report_interval_s:
                log.info(
                    "progress: iter=%d sent=%d completed=%d wait_timeouts=%d aborts=%d "
                    "resends=%d mcu_aborts=%d (%.1fs remaining)",
                    iteration, stats.sent, stats.completed, stats.wait_timeouts,
                    stats.aborts, counter.counts["resend"], counter.counts["abort"],
                    max(0.0, end_time - now),
                )
                last_report_at = now

    return stats


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return float("nan")
    k = max(0, min(len(sorted_values) - 1, int(round(pct / 100.0 * (len(sorted_values) - 1)))))
    return sorted_values[k]


def print_report(stats: Stats, counter: _LogCounter, elapsed_s: float) -> None:
    print()
    print("=" * 72)
    print("MICROCONTROLLER TIMEOUT STRESS TEST REPORT")
    print("=" * 72)
    print(f"runtime:                {elapsed_s:.1f} s")
    print(f"commands sent:          {stats.sent}")
    print(f"commands completed:     {stats.completed}")
    print(f"wait-timeouts:          {stats.wait_timeouts}")
    print(f"aborts (caller):        {stats.aborts}")
    print(f"other errors:           {stats.other_errors}")
    print(f"resends (from MCU log): {counter.counts['resend']}")
    print(f"aborts  (from MCU log): {counter.counts['abort']}")
    if stats.sent:
        print(f"effective rate:         {stats.sent / elapsed_s:.1f} cmd/s")

    if stats.latencies_ms:
        lat = sorted(stats.latencies_ms)
        print()
        print("latency (ms):")
        print(f"  min:  {lat[0]:.2f}")
        print(f"  avg:  {statistics.fmean(lat):.2f}")
        print(f"  p50:  {_percentile(lat, 50):.2f}")
        print(f"  p95:  {_percentile(lat, 95):.2f}")
        print(f"  p99:  {_percentile(lat, 99):.2f}")
        print(f"  max:  {lat[-1]:.2f}")

    if stats.per_command_counts:
        print()
        print("per-command counts:")
        for name, n in sorted(stats.per_command_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {name:<32} {n}")

    for name, samples in counter.samples.items():
        if not samples:
            continue
        print()
        print(f"sample {name} log messages:")
        for s in samples:
            print(f"  - {s}")


def install_log_counter() -> _LogCounter:
    counter = _LogCounter({"resend": _RESEND_RE, "abort": _ABORT_RE})
    # Attach to the squid root logger so we catch the MCU logger regardless of name.
    logging.getLogger("squid").addHandler(counter)
    return counter


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stress test the MCU by replaying the acquisition command pattern on D1-D5."
    )
    ap.add_argument("--runtime", type=float, default=60.0, help="Duration in seconds (default: 60)")
    ap.add_argument("--intensity", type=float, default=10.0,
                    help="Illumination intensity percent passed to set_illumination (default: 10)")
    ap.add_argument("--illum-on-time-us", type=int, default=100_000,
                    help="Illumination on-time passed to send_hardware_trigger in microseconds "
                         "(default: 100000, matches the log)")
    ap.add_argument("--wait-timeout", type=float, default=5.0,
                    help="Per-command wait_till_operation_is_completed timeout (default: 5s)")
    ap.add_argument("--inter-command-sleep", type=float, default=0.0,
                    help="Optional sleep between channel iterations in seconds (default: 0)")
    ap.add_argument("--report-interval-s", type=float, default=5.0,
                    help="Progress report cadence in seconds (default: 5)")
    ap.add_argument("--verbose", action="store_true", help="Enable debug-level stdout logging")

    args = ap.parse_args()

    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    counter = install_log_counter()

    log.info("Creating microcontroller...")
    serial_device = mc.get_microcontroller_serial_device(simulated=False)
    micro = mc.Microcontroller(serial_device=serial_device)

    started_at = time.time()
    stats = Stats()
    try:
        stats = run_stress(micro, args, counter)
    except KeyboardInterrupt:
        log.warning("Interrupted — reporting partial results")
    elapsed = time.time() - started_at

    print_report(stats, counter, elapsed)

    failures = (
        stats.wait_timeouts + stats.aborts + stats.other_errors
        + counter.counts["resend"] + counter.counts["abort"]
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
