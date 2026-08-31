"""Scripted engines for runner tests: deterministic, instant unless told to hold."""

import os
import threading
import time
from typing import List, Optional

from control.core.fluidics_protocol.ports import FluidicsOutcome, ImagingRequest, ImagingResult, ImagingStartError
from control.models.fluidics_run import TecState


class FakePlanEntry:
    def __init__(self, row: int):
        self.row = row
        self.duration_seconds = 1.0

    def __repr__(self):
        return f"FakePlanEntry({self.row})"


class FakeTicket:
    """Completes on the first wait() with the scripted outcome, unless `hold` is set: then it blocks until
    resume()/abort(), reporting at_rest() while paused (like a run parked at a gate)."""

    def __init__(
        self,
        outcome: FluidicsOutcome,
        hold: bool = False,
        run_id: str = "run-fake",
        position: Optional[int] = None,
        pause_ok: bool = True,
    ):
        self.run_id = run_id
        self.position = position  # a held ticket reports the sequence it is parked in
        self._outcome = outcome
        self._hold = hold
        self._pause_ok = pause_ok
        self._paused = False
        self._aborted = False
        self._released = threading.Event()
        if not hold:
            self._released.set()

    def wait(self, timeout: float) -> Optional[FluidicsOutcome]:
        if not self._released.wait(timeout):
            return None
        if self._aborted:
            return FluidicsOutcome("stopped", None, 0.1, self.position or 0, self.run_id, {})
        return self._outcome

    def release(self) -> None:
        self._released.set()

    def pause(self) -> bool:
        if not self._pause_ok:
            return False
        self._paused = True
        return True

    def resume(self) -> bool:
        self._paused = False
        self._released.set()
        return True

    def abort(self) -> bool:
        self._aborted = True
        self._released.set()
        return True

    def at_rest(self) -> bool:
        return self._paused


class FakeFluidicsPort:
    """`script` is consumed one entry per start(): ("finished",) | ("stopped", position) | ("failed", position, message)
    | ("hold",) - a ticket parked in its 2nd sequence that blocks until resumed/aborted/released, then finishes
    | ("nopause",) - like hold but refuses pause() | ("raise",) - start() raises (the library refused the run)."""

    def __init__(self, script: Optional[List[tuple]] = None, tec: Optional[TecState] = None):
        self.script = list(script or [])
        self.starts: List[dict] = []
        self.tickets: List[Optional["FakeTicket"]] = []
        self.validated: List[List[dict]] = []
        self.make_safe_calls = 0
        self.restored: List[TecState] = []
        self._tec = tec

    def validate(self, rows):
        self.validated.append(rows)

    def plan(self, rows):
        return tuple(FakePlanEntry(i) for i in range(len(rows)))

    def start(self, rows, plan=None):
        entry = self.script.pop(0) if self.script else ("finished",)
        plan = plan if plan is not None else self.plan(rows)
        self.starts.append({"rows": rows, "plan": plan, "outcome": entry})
        run_id = f"run-{len(self.starts)}"
        if entry[0] == "raise":
            raise RuntimeError("the rig is busy: a run is in progress")
        self.tickets.append(None)
        if entry[0] == "finished":
            return FakeTicket(FluidicsOutcome("finished", None, 1.0, None, run_id, {1: 500.0}), run_id=run_id)
        if entry[0] == "stopped":
            return FakeTicket(FluidicsOutcome("stopped", None, 0.5, entry[1], run_id, {1: 100.0}), run_id=run_id)
        if entry[0] == "failed":
            return FakeTicket(FluidicsOutcome("failed", entry[2], 0.5, entry[1], run_id, {}), run_id=run_id)
        if entry[0] in ("hold", "nopause"):
            ticket = FakeTicket(
                FluidicsOutcome("finished", None, 1.0, None, run_id, {}),
                hold=True,
                run_id=run_id,
                position=min(1, len(plan) - 1),
                pause_ok=entry[0] == "hold",
            )
            self.tickets[-1] = ticket
            return ticket
        raise AssertionError(f"unknown script entry {entry}")

    def make_safe(self):
        self.make_safe_calls += 1
        return []

    def tec_state(self):
        return self._tec

    def restore_tec(self, state):
        self.restored.append(state)


class FakeHandle:
    def __init__(self, result: ImagingResult, hold: bool = False):
        self._result = result
        self._released = threading.Event()
        self._aborted = False
        if not hold:
            self._released.set()

    def wait(self, timeout: float) -> Optional[ImagingResult]:
        if not self._released.wait(timeout):
            return None
        if self._aborted:
            return ImagingResult("user_abort", 3, self._result.folder)
        return self._result

    def abort(self) -> None:
        self._aborted = True
        self._released.set()

    def release(self) -> None:
        self._released.set()


class FakeImagingPort:
    """Creates the session folder like start_new_experiment(add_timestamp=False) would (FileExistsError ->
    ImagingStartError). `script`: one entry per start(): "completed" | "completed_with_errors" | "user_abort" |
    "error" | "raise" (start fails) | "hold" (blocks until release()/abort())."""

    def __init__(self, script: Optional[List[str]] = None):
        self.script = list(script or [])
        self.requests: List[ImagingRequest] = []
        self.handles: List[FakeHandle] = []

    def start(self, request: ImagingRequest) -> FakeHandle:
        entry = self.script.pop(0) if self.script else "completed"
        self.requests.append(request)
        if entry == "raise":
            raise ImagingStartError("Laser AF reference not set")
        folder = os.path.join(request.run_dir, request.folder)
        if os.path.exists(folder):
            raise ImagingStartError(f"folder exists: {folder}")
        os.makedirs(folder)
        hold = entry == "hold"
        handle = FakeHandle(ImagingResult("completed" if hold else entry, 7, request.folder), hold=hold)
        self.handles.append(handle)
        return handle


def wait_until(predicate, timeout=5.0, step=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(step)
    return True
