"""PR #540 filter-wheel silent-fail reproduction scenarios.

Pure logic. No Qt imports. See worktrees/docs/2026-05-24-pr-540-repro-gui-design.md.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from control.microcontroller import CommandAborted


Verdict = Literal["PASS", "OBSERVED-BUG", "FAIL", "ERROR", "GATE-NOT-PRESENT"]
LogCallback = Callable[[str], None]


@dataclass
class ScenarioResult:
    name: str
    verdict: Verdict
    summary: str
    iterations: int = 0
    fast_fail_count: int = 0
    suspect_fast_ack_count: int = 0
    normal_count: int = 0
    elapsed_seconds: float = 0.0
    details: list = field(default_factory=list)


class FakeMicrocontroller:
    """Minimal fake for scenario unit tests.

    Records every call and lets tests pre-program responses to wait_till_operation_is_completed.
    """

    def __init__(self, firmware_version=(1, 2)):
        self.firmware_version = firmware_version
        self.last_command_aborted_error: Optional[Exception] = None
        self.calls: list = []
        # Pre-programmed responses: each call to wait_till_operation_is_completed pops one.
        # An item that is an Exception is raised; anything else is returned.
        self._wait_responses: list = []
        # Pre-programmed elapsed times to simulate; the fake's wait sleeps for this many seconds.
        self._wait_durations: list = []

    def queue_wait(self, *, raises: Optional[Exception] = None, duration: float = 0.0):
        self._wait_responses.append(raises)
        self._wait_durations.append(duration)

    def move_w_usteps(self, n):
        self.calls.append(("move_w_usteps", n))

    def move_w2_usteps(self, n):
        self.calls.append(("move_w2_usteps", n))

    def init_filter_wheel(self, axis):
        self.calls.append(("init_filter_wheel", axis))

    def wait_till_operation_is_completed(self, timeout_limit_s=5):
        self.calls.append(("wait",))
        if self._wait_durations:
            time.sleep(self._wait_durations.pop(0))
        if self._wait_responses:
            exc = self._wait_responses.pop(0)
            if exc is not None:
                raise exc


N_SAFE_USTEPS = 100  # well below one full slot transition; chosen so no motion would occur even if INIT had run
SCENARIO_A_TIME_LIMIT_S = 0.1


def _classify_move(elapsed_s, exc):
    if isinstance(exc, CommandAborted) and "CMD_EXECUTION_ERROR" in str(exc):
        return "fast_fail"
    if exc is not None:
        return "error"
    if elapsed_s < SCENARIO_A_TIME_LIMIT_S:
        return "suspect_fast_ack"
    return "normal"


def scenario_a_pre_init_move(micro, log_cb: LogCallback) -> ScenarioResult:
    """Send MOVE_W and MOVE_W2 without prior INITFILTERWHEEL; classify outcomes."""
    log_cb("[Scenario A] pre-INIT MOVE_W / MOVE_W2 — expecting CommandAborted on post-fix firmware")

    fast_fail = 0
    suspect = 0
    errors = []
    details = []
    t_total_0 = time.monotonic()

    for label, send in (("MOVE_W", micro.move_w_usteps), ("MOVE_W2", micro.move_w2_usteps)):
        micro.last_command_aborted_error = None
        t0 = time.monotonic()
        captured: Optional[Exception] = None
        try:
            send(N_SAFE_USTEPS)
            micro.wait_till_operation_is_completed()
        except Exception as e:  # CommandAborted lives here, plus anything else
            captured = e
        elapsed = time.monotonic() - t0
        klass = _classify_move(elapsed, captured)
        details.append(f"{label}: elapsed={elapsed*1000:.1f}ms class={klass} exc={captured!r}")
        log_cb(details[-1])
        if klass == "fast_fail":
            fast_fail += 1
        elif klass == "suspect_fast_ack":
            suspect += 1
        elif klass == "error":
            errors.append((label, captured))

    elapsed_total = time.monotonic() - t_total_0
    if errors:
        verdict, summary = "FAIL", f"Unexpected exceptions: {errors}"
    elif fast_fail >= 1 and suspect == 0:
        verdict = "PASS"
        summary = f"{fast_fail}/2 commands fast-failed with CMD_EXECUTION_ERROR (post-fix behavior)"
    elif fast_fail == 0 and suspect >= 1:
        verdict = "OBSERVED-BUG"
        summary = f"{suspect}/2 commands silently completed within {SCENARIO_A_TIME_LIMIT_S*1000:.0f}ms (pre-fix bug reproduced)"
    else:
        verdict = "FAIL"
        summary = f"Mixed/unexpected: fast_fail={fast_fail}, suspect={suspect}"

    log_cb(f"[Scenario A] verdict={verdict} — {summary}")
    return ScenarioResult(
        name="A",
        verdict=verdict,
        summary=summary,
        iterations=2,
        fast_fail_count=fast_fail,
        suspect_fast_ack_count=suspect,
        normal_count=0,
        elapsed_seconds=elapsed_total,
        details=details,
    )
