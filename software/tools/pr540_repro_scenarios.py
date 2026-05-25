"""PR #540 filter-wheel silent-fail reproduction scenarios.

Pure logic. No Qt imports. See worktrees/docs/2026-05-24-pr-540-repro-gui-design.md.
"""

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional


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
        import time

        self.calls.append(("wait",))
        if self._wait_durations:
            time.sleep(self._wait_durations.pop(0))
        if self._wait_responses:
            exc = self._wait_responses.pop(0)
            if exc is not None:
                raise exc
