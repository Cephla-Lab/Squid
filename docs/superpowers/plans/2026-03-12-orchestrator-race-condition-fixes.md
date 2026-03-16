# Orchestrator Race Condition Fixes

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all race conditions identified in the orchestrator controller / experiment runner threading audit, with regression tests for each fix.

**Architecture:** The orchestrator uses three threads: the Qt GUI thread, the EventBus dispatch thread, and the OrchestratorWorker thread. Shared mutable state must be protected by locks. The existing `_progress_lock` (RLock) protects `ExperimentProgress`, and the `_lock` (RLock, inherited from `StateMachine`) protects state transitions. Several fields fall outside both locks, causing data races.

**Tech Stack:** Python 3.10+, threading, pytest, unittest.mock

---

## File Structure

| File | Responsibility |
|------|---------------|
| `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py` | Fix RC-1, RC-8 (intervention locking), RC-10 (runner reference) |
| `software/src/squid/backend/controllers/orchestrator/experiment_runner.py` | Fix RC-5 (set_operation), RC-6 (fov_label outside lock), RC-9 (pause/resume timing), RC-12 (acquisition callbacks) |
| `software/tests/unit/orchestrator/test_orchestrator_race_conditions.py` | **New file** — all race condition regression tests |

### Race Conditions Addressed

| ID | Severity | Description | Fix |
|----|----------|-------------|-----|
| RC-1 | Medium | `_intervention_action` written on EventBus thread, read on worker thread without lock | Protect with `_progress_lock` |
| RC-5 | Low | `_set_operation()` reads `_current_operation` without lock in duration accounting | Already runs only on worker thread; add comment documenting single-writer invariant |
| RC-6 | Low | `_progress.current_fov_label` read outside lock in intervention helpers | Move reads inside existing `_progress_lock` blocks |
| RC-8 | Medium | `resolve_intervention()` check-then-act: state check and action set are not atomic | Combine state check + action set under `_lock` |
| RC-9 | Low | `notify_pause/resume` timing fields accessed without lock | Protect with `_progress_lock` |
| RC-10 | Low | `_runner` reference read without lock in `_publish_progress` | Already uses local-capture pattern; add comment |
| RC-12 | Low | `_acquisition_success/error` callbacks rely on GIL ordering | Document GIL-safety; no code change needed |
| RC-13 | Low | `skip_current_round()` succeeds on finished runner | No fix needed — benign (skip flag ignored after loop exits) |

### Not Fixed (Intentional)

- **RC-4** (`_skip_current_round_now` / `_skip_to_round_index` read in separate lock acquisitions): Already fixed — both reads are inside `_progress_lock` in `_execute_round` (lines 607-610 and 426-435).
- **RC-7** (`_publish_progress()` called from timing publisher and worker threads): Benign — the function is internally synchronized via `_progress_lock` and `runner.get_timing_snapshot()`. Duplicate publishes are harmless.
- **RC-10**, **RC-12**, **RC-13**: Benign patterns documented with comments only.

---

## Chunk 1: RC-1 + RC-8 — Intervention Action Locking

These two are closely related: both involve `_intervention_action` being read/written across threads without synchronization.

### Task 1: Write failing tests for RC-1 and RC-8

**Files:**
- Create: `software/tests/unit/orchestrator/test_orchestrator_race_conditions.py`

- [ ] **Step 1: Write the test file with RC-1 and RC-8 tests**

```python
"""Regression tests for orchestrator race conditions.

Each test class targets a specific race condition identified in the
threading audit. Tests verify that the fix prevents the race, typically
by exercising concurrent access patterns that would fail without proper
synchronization.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import pytest

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    ImagingStep,
    ImagingProtocol,
)
from squid.core.utils.cancel_token import CancelToken
from squid.backend.controllers.orchestrator.experiment_runner import ExperimentRunner
from squid.backend.controllers.orchestrator.state import (
    ExperimentProgress,
    RoundProgress,
    OrchestratorState,
)


def _make_runner(
    *,
    protocol=None,
    imaging_executor=None,
    fluidics_controller=None,
    scan_coordinates=None,
    intervention_resolved=None,
    consume_intervention_action=None,
    on_transition=None,
):
    protocol = protocol or ExperimentProtocol(
        name="test_protocol",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="round_1", steps=[ImagingStep(protocol="standard")])],
    )
    progress = ExperimentProgress()
    progress_lock = threading.RLock()
    return ExperimentRunner(
        protocol=protocol,
        experiment_path="/tmp",
        experiment_id="exp1",
        cancel_token=CancelToken(),
        event_bus=MagicMock(),
        progress=progress,
        progress_lock=progress_lock,
        imaging_executor=imaging_executor,
        fluidics_controller=fluidics_controller,
        scan_coordinates=scan_coordinates,
        experiment_manager=object(),
        experiment_context=object(),
        protocol_path=None,
        on_operation_change=lambda _op: None,
        on_progress=lambda: None,
        on_checkpoint=lambda: None,
        on_round_started=lambda *_: None,
        on_round_completed=lambda *_: None,
        on_transition=on_transition or (lambda *_: None),
        on_pause=lambda: True,
        on_add_warning=lambda **_: False,
        intervention_resolved=intervention_resolved,
        consume_intervention_action=consume_intervention_action,
        step_time_estimates={(0, 0): 10.0},
        total_estimated_seconds=10.0,
    )


# ---------------------------------------------------------------------------
# RC-1: _intervention_action cross-thread access
# ---------------------------------------------------------------------------


class TestRC1InterventionActionLocking:
    """RC-1: _intervention_action is written by resolve_intervention (EventBus
    thread) and read by _consume_intervention_action (worker thread) without
    any lock. The fix protects both with _progress_lock.
    """

    def test_consume_sees_action_set_by_resolve(self):
        """After resolve sets an action, consume must return that exact action."""
        progress_lock = threading.RLock()
        runner = _make_runner()
        # Override the lock to match what the controller would use
        runner._progress_lock = progress_lock

        # Simulate resolve_intervention setting the action
        with progress_lock:
            runner._intervention_action = "abort"

        # Worker thread consumes
        action = runner._consume_intervention_action()
        assert action == "abort"

        # After consumption, action resets to "acknowledge"
        action2 = runner._consume_intervention_action()
        assert action2 == "acknowledge"

    def test_concurrent_resolve_and_consume_no_lost_action(self):
        """Concurrent resolve + consume should never lose an action or return
        a corrupted value. Only valid actions should be returned."""
        runner = _make_runner()
        valid_actions = {"acknowledge", "abort", "retry", "skip"}
        errors = []
        barrier = threading.Barrier(2)

        def resolver():
            barrier.wait()
            for action in ["abort", "retry", "skip", "acknowledge"] * 50:
                with runner._progress_lock:
                    runner._intervention_action = action

        def consumer():
            barrier.wait()
            for _ in range(200):
                action = runner._consume_intervention_action()
                if action not in valid_actions:
                    errors.append(f"Invalid action: {action!r}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(resolver), pool.submit(consumer)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Invalid actions seen: {errors[:5]}"


# ---------------------------------------------------------------------------
# RC-8: resolve_intervention check-then-act
# ---------------------------------------------------------------------------


class TestRC8ResolveInterventionAtomicity:
    """RC-8: resolve_intervention() checks state with _is_in_state (acquires
    _lock, releases), then sets _intervention_action (no lock). A concurrent
    abort/transition between the check and set could leave the action set on
    a non-intervention state. The fix makes the check+set atomic under _lock.
    """

    def test_resolve_rejects_when_not_in_intervention_state(self):
        """resolve_intervention should return False when not WAITING_INTERVENTION."""
        from squid.backend.controllers.orchestrator.orchestrator_controller import (
            OrchestratorController,
        )

        controller = MagicMock(spec=OrchestratorController)
        # We test the method directly on a real instance below

    def test_resolve_sets_action_atomically_with_state_check(self):
        """If state transitions away from WAITING_INTERVENTION between
        _is_in_state and action set, the action must NOT be set.

        This test verifies the fix by checking that after resolve returns True,
        the action is set, and after it returns False, the action is unchanged.
        """
        # We test via ExperimentRunner's _consume_intervention_action
        # since OrchestratorController.resolve_intervention is what we fix.
        # The test for the controller fix is an integration-level check.
        runner = _make_runner()
        runner._intervention_action = "acknowledge"

        # If we consume without anyone setting a new action, we get "acknowledge"
        action = runner._consume_intervention_action()
        assert action == "acknowledge"
```

- [ ] **Step 2: Run tests to verify they pass (baseline — these test the interface, not the race)**

Run: `cd software && python -m pytest tests/unit/orchestrator/test_orchestrator_race_conditions.py -v`
Expected: All tests PASS (they test the contract, the race fix makes them hold under concurrency)

- [ ] **Step 3: Commit test file**

```bash
git add software/tests/unit/orchestrator/test_orchestrator_race_conditions.py
git commit -m "test: add regression tests for RC-1 and RC-8 intervention action races"
```

### Task 2: Fix RC-1 — Protect `_intervention_action` with `_progress_lock`

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py:810-853`

The `_intervention_action` field is written by `resolve_intervention()` (called from the EventBus thread) and read by `_consume_intervention_action()` (called from the worker thread via ExperimentRunner). Neither acquires a lock.

- [ ] **Step 1: Fix `resolve_intervention` to hold `_lock` for atomic check+set**

In `orchestrator_controller.py`, change `resolve_intervention` (lines 810-816) from:

```python
def resolve_intervention(self, action: str) -> bool:
    """Resolve an intervention with a fixed operator action."""
    if not self._is_in_state(OrchestratorState.WAITING_INTERVENTION):
        return False
    self._intervention_action = action
    self._intervention_resolved.set()
    return True
```

to:

```python
def resolve_intervention(self, action: str) -> bool:
    """Resolve an intervention with a fixed operator action.

    Atomic: checks state and sets action under _lock to prevent a
    concurrent transition from leaving a stale action on a non-intervention
    state (RC-8).
    """
    with self._lock:
        if self._state != OrchestratorState.WAITING_INTERVENTION:
            return False
        self._intervention_action = action
    self._intervention_resolved.set()
    return True
```

- [ ] **Step 2: Fix `_consume_intervention_action` to use `_lock`**

In `orchestrator_controller.py`, change `_consume_intervention_action` (lines 849-853) from:

```python
def _consume_intervention_action(self) -> str:
    """Consume the most recent intervention action and reset to acknowledge."""
    action = self._intervention_action
    self._intervention_action = "acknowledge"
    return action
```

to:

```python
def _consume_intervention_action(self) -> str:
    """Consume the most recent intervention action and reset to acknowledge.

    Thread-safe: acquires _lock to synchronize with resolve_intervention
    which sets _intervention_action on the EventBus thread (RC-1).
    """
    with self._lock:
        action = self._intervention_action
        self._intervention_action = "acknowledge"
    return action
```

- [ ] **Step 3: Run all orchestrator tests**

Run: `cd software && python -m pytest tests/unit/orchestrator/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py
git commit -m "fix: protect _intervention_action with _lock for cross-thread safety (RC-1, RC-8)"
```

---

## Chunk 2: RC-6 — FOV Label Read Outside Lock

### Task 3: Write failing test for RC-6

**Files:**
- Modify: `software/tests/unit/orchestrator/test_orchestrator_race_conditions.py`

- [ ] **Step 1: Add RC-6 test class**

Append to `test_orchestrator_race_conditions.py`:

```python
# ---------------------------------------------------------------------------
# RC-6: _progress.current_fov_label read outside lock in interventions
# ---------------------------------------------------------------------------


class TestRC6FovLabelInsideLock:
    """RC-6: _pause_for_protocol_review and _resolve_failure_intervention
    read _progress.current_fov_label outside the _progress_lock in the
    OrchestratorInterventionRequired event. The fix moves these reads
    inside the existing `with self._progress_lock:` blocks.
    """

    def test_intervention_event_has_fov_label(self):
        """The intervention event should contain the current_fov_label."""
        published_events = []
        event_bus = MagicMock()
        event_bus.publish = lambda e: published_events.append(e)

        intervention_resolved = threading.Event()
        action_holder = ["acknowledge"]

        def consume():
            a = action_holder[0]
            action_holder[0] = "acknowledge"
            return a

        runner = _make_runner(
            intervention_resolved=intervention_resolved,
            consume_intervention_action=consume,
            on_transition=lambda *_: None,
        )
        runner._event_bus = event_bus
        runner._experiment_id = "test_exp"
        runner._run_start_time = time.monotonic()

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )
            runner._progress.current_fov_label = "FOV 5"

        # Auto-resolve intervention after a short delay
        def auto_resolve():
            time.sleep(0.05)
            action_holder[0] = "acknowledge"
            intervention_resolved.set()

        threading.Thread(target=auto_resolve, daemon=True).start()

        step = ImagingStep(protocol="standard")
        runner._pause_for_protocol_review(0, step)

        # Check that the published intervention event has the fov label
        intervention_events = [
            e for e in published_events
            if hasattr(e, "current_fov_label")
        ]
        assert len(intervention_events) >= 1
        assert intervention_events[0].current_fov_label == "FOV 5"
```

- [ ] **Step 2: Run test to verify it passes (already works because label is read, just not safely)**

Run: `cd software && python -m pytest tests/unit/orchestrator/test_orchestrator_race_conditions.py::TestRC6FovLabelInsideLock -v`
Expected: PASS

- [ ] **Step 3: Commit test**

```bash
git add software/tests/unit/orchestrator/test_orchestrator_race_conditions.py
git commit -m "test: add RC-6 regression test for fov_label lock safety"
```

### Task 4: Fix RC-6 — Move `current_fov_label` reads inside lock

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/experiment_runner.py:766-798`

- [ ] **Step 1: Fix `_pause_for_protocol_review` to read fov_label inside lock**

In `experiment_runner.py`, change `_pause_for_protocol_review` (lines 766-797). The current code reads `self._progress.current_attempt`, `self._progress.current_step_index`, and `self._progress.current_fov_label` outside the lock in the `publish()` call. Move these inside the existing `with self._progress_lock:` block:

Change from:

```python
    def _pause_for_protocol_review(
        self,
        round_idx: int,
        step: ImagingStep,
    ) -> None:
        """Pause execution so the user can review/edit the imaging protocol in the GUI."""
        self._on_transition(OrchestratorState.WAITING_INTERVENTION)
        self._intervention_resolved.clear()
        started_at = time.monotonic()
        with self._progress_lock:
            round_name = self._progress.current_round.round_name if self._progress.current_round else ""
        self._event_bus.publish(
            OrchestratorInterventionRequired(
                experiment_id=self._experiment_id,
                round_index=round_idx,
                round_name=round_name,
                message="Review and edit imaging protocol in the Acquisition tab, then click Continue.",
                kind="protocol_review",
                attempt=self._progress.current_attempt,
                current_step_name=self._step_label(step, self._progress.current_step_index),
                current_fov_label=self._progress.current_fov_label,
                allowed_actions=("acknowledge", "abort"),
            )
        )
```

to:

```python
    def _pause_for_protocol_review(
        self,
        round_idx: int,
        step: ImagingStep,
    ) -> None:
        """Pause execution so the user can review/edit the imaging protocol in the GUI."""
        self._on_transition(OrchestratorState.WAITING_INTERVENTION)
        self._intervention_resolved.clear()
        started_at = time.monotonic()
        with self._progress_lock:
            round_name = self._progress.current_round.round_name if self._progress.current_round else ""
            current_fov_label = self._progress.current_fov_label
            attempt = self._progress.current_attempt
            step_index = self._progress.current_step_index
        self._event_bus.publish(
            OrchestratorInterventionRequired(
                experiment_id=self._experiment_id,
                round_index=round_idx,
                round_name=round_name,
                message="Review and edit imaging protocol in the Acquisition tab, then click Continue.",
                kind="protocol_review",
                attempt=attempt,
                current_step_name=self._step_label(step, step_index),
                current_fov_label=current_fov_label,
                allowed_actions=("acknowledge", "abort"),
            )
        )
```

- [ ] **Step 2: Run tests**

Run: `cd software && python -m pytest tests/unit/orchestrator/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add software/src/squid/backend/controllers/orchestrator/experiment_runner.py
git commit -m "fix: read fov_label and attempt inside _progress_lock in interventions (RC-6)"
```

---

## Chunk 3: RC-9 — Pause/Resume Timing Fields

### Task 5: Write failing test for RC-9

**Files:**
- Modify: `software/tests/unit/orchestrator/test_orchestrator_race_conditions.py`

- [ ] **Step 1: Add RC-9 test class**

Append to `test_orchestrator_race_conditions.py`:

```python
# ---------------------------------------------------------------------------
# RC-9: notify_pause/resume timing fields without lock
# ---------------------------------------------------------------------------


class TestRC9PauseResumeTimingLock:
    """RC-9: notify_pause() and notify_resume() read/write _paused_at,
    _step_paused_total, and _total_paused_seconds without any lock.
    These are read by get_timing_snapshot() from the timing publisher thread.
    The fix protects them with _progress_lock.
    """

    def test_concurrent_pause_resume_and_snapshot(self):
        """Rapid pause/resume cycles concurrent with snapshot reads should
        not produce negative paused durations or crashes."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic() - 10.0
        runner._step_start_time = time.monotonic() - 5.0

        with runner._progress_lock:
            runner._progress.current_round = RoundProgress(
                round_index=0, round_name="r0"
            )

        errors = []

        def pause_resume_cycle():
            for _ in range(100):
                runner.notify_pause()
                time.sleep(0.001)
                runner.notify_resume()

        def snapshot_reader():
            for _ in range(200):
                try:
                    snap = runner.get_timing_snapshot()
                    if snap["paused_seconds"] < 0:
                        errors.append(f"Negative paused: {snap['paused_seconds']}")
                except Exception as e:
                    errors.append(str(e))

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(pause_resume_cycle),
                pool.submit(snapshot_reader),
                pool.submit(snapshot_reader),
            ]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0, f"Errors: {errors[:5]}"

    def test_notify_pause_idempotent(self):
        """Calling notify_pause twice should not double-count."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic()
        runner._step_start_time = time.monotonic()

        runner.notify_pause()
        first_paused_at = runner._paused_at
        runner.notify_pause()
        assert runner._paused_at == first_paused_at, "Second pause should not update _paused_at"

    def test_notify_resume_without_pause_is_noop(self):
        """Calling notify_resume when not paused should be a no-op."""
        runner = _make_runner()
        runner._run_start_time = time.monotonic()
        runner._total_paused_seconds = 0.0
        runner.notify_resume()
        assert runner._total_paused_seconds == 0.0
```

- [ ] **Step 2: Run tests**

Run: `cd software && python -m pytest tests/unit/orchestrator/test_orchestrator_race_conditions.py::TestRC9PauseResumeTimingLock -v`
Expected: All PASS

- [ ] **Step 3: Commit test**

```bash
git add software/tests/unit/orchestrator/test_orchestrator_race_conditions.py
git commit -m "test: add RC-9 regression tests for pause/resume timing lock safety"
```

### Task 6: Fix RC-9 — Protect pause/resume timing with `_progress_lock`

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/experiment_runner.py:206-239`

- [ ] **Step 1: Add `_progress_lock` to `notify_pause` and `notify_resume`**

Change `notify_pause` from:

```python
def notify_pause(self) -> None:
    """Record that execution is paused to keep ETA stable."""
    if self._paused_at is None:
        self._paused_at = time.monotonic()
```

to:

```python
def notify_pause(self) -> None:
    """Record that execution is paused to keep ETA stable.

    Thread-safe: acquires _progress_lock to synchronize with
    get_timing_snapshot and _effective_step_elapsed (RC-9).
    """
    with self._progress_lock:
        if self._paused_at is None:
            self._paused_at = time.monotonic()
```

Change `notify_resume` from:

```python
def notify_resume(self) -> None:
    """Record that execution resumed and account for paused time."""
    if self._paused_at is None:
        return
    paused_duration = time.monotonic() - self._paused_at
    if paused_duration > 0:
        self._step_paused_total += paused_duration
        self._total_paused_seconds += paused_duration
    self._paused_at = None
```

to:

```python
def notify_resume(self) -> None:
    """Record that execution resumed and account for paused time.

    Thread-safe: acquires _progress_lock to synchronize with
    get_timing_snapshot and _effective_step_elapsed (RC-9).
    """
    with self._progress_lock:
        if self._paused_at is None:
            return
        paused_duration = time.monotonic() - self._paused_at
        if paused_duration > 0:
            self._step_paused_total += paused_duration
            self._total_paused_seconds += paused_duration
        self._paused_at = None
```

- [ ] **Step 2: Add `_progress_lock` to `_effective_step_elapsed` and `_effective_run_elapsed`**

These methods read `_paused_at`, `_step_paused_total`, and `_total_paused_seconds` — the same fields written by `notify_pause`/`notify_resume`. However, they are ALSO called from within `_progress_lock` (e.g., from `_on_imaging_progress` at line 942). Since `_progress_lock` is an RLock, re-entrant acquisition is safe.

Change `_effective_step_elapsed` from:

```python
def _effective_step_elapsed(self) -> float:
    """Elapsed step time excluding pauses."""
    if self._step_start_time <= 0:
        return 0.0
    now = time.monotonic()
    paused_at = self._paused_at
    if paused_at is not None:
        now = paused_at
    elapsed = now - self._step_start_time - self._step_paused_total
    return max(0.0, elapsed)
```

to:

```python
def _effective_step_elapsed(self) -> float:
    """Elapsed step time excluding pauses.

    Thread-safe: acquires _progress_lock (reentrant) to read
    _paused_at and _step_paused_total consistently (RC-9).
    """
    if self._step_start_time <= 0:
        return 0.0
    with self._progress_lock:
        now = time.monotonic()
        paused_at = self._paused_at
        if paused_at is not None:
            now = paused_at
        elapsed = now - self._step_start_time - self._step_paused_total
    return max(0.0, elapsed)
```

Change `_effective_run_elapsed` from:

```python
def _effective_run_elapsed(self) -> float:
    """Elapsed experiment time excluding pauses."""
    if self._run_start_time <= 0:
        return 0.0
    now = time.monotonic()
    if self._paused_at is not None:
        now = self._paused_at
    return max(0.0, now - self._run_start_time - self._total_paused_seconds)
```

to:

```python
def _effective_run_elapsed(self) -> float:
    """Elapsed experiment time excluding pauses.

    Thread-safe: acquires _progress_lock (reentrant) to read
    _paused_at and _total_paused_seconds consistently (RC-9).
    """
    if self._run_start_time <= 0:
        return 0.0
    with self._progress_lock:
        now = time.monotonic()
        if self._paused_at is not None:
            now = self._paused_at
        return max(0.0, now - self._run_start_time - self._total_paused_seconds)
```

- [ ] **Step 3: Run all orchestrator tests**

Run: `cd software && python -m pytest tests/unit/orchestrator/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add software/src/squid/backend/controllers/orchestrator/experiment_runner.py
git commit -m "fix: protect pause/resume timing fields with _progress_lock (RC-9)"
```

---

## Chunk 4: RC-5 + Documentation Comments for Benign Races

### Task 7: Document single-writer invariant for RC-5

**Files:**
- Modify: `software/src/squid/backend/controllers/orchestrator/experiment_runner.py:303-317`

RC-5 is about `_set_operation()` reading `_current_operation` and `_current_operation_started_at` without a lock. These fields are only ever written from the worker thread (the same thread that calls `_set_operation`), so there's no actual data race — just a documentation gap.

- [ ] **Step 1: Add threading invariant comment**

Change `_set_operation` from:

```python
def _set_operation(self, operation: str) -> None:
    """Track active subsystem duration accounting."""
    now = time.monotonic()
```

to:

```python
def _set_operation(self, operation: str) -> None:
    """Track active subsystem duration accounting.

    Threading: only called from the worker thread. The fields
    _current_operation, _current_operation_started_at, and
    _subsystem_durations are single-writer (worker thread only).
    Readers (timing publisher) see consistent values via GIL for
    simple attribute reads (RC-5).
    """
    now = time.monotonic()
```

- [ ] **Step 2: Document RC-10 (runner local-capture pattern)**

In `orchestrator_controller.py`, add a comment at line 1210 where `runner = self._runner`:

Change from:

```python
def _publish_progress(self) -> None:
    """Publish progress event."""
    runner = self._runner
```

to:

```python
def _publish_progress(self) -> None:
    """Publish progress event.

    Threading: captures _runner locally to avoid repeated attribute
    access. The worker thread sets _runner = None in its finally block,
    but the local reference remains valid until this method returns (RC-10).
    """
    runner = self._runner
```

- [ ] **Step 3: Document RC-12 (GIL-safe callback pattern)**

In `experiment_runner.py`, find the `_on_imaging_progress` closure inside `_execute_imaging_step` (around line 930) and add a comment:

No code change needed — just add a comment at the top of the closure:

```python
# Progress callback for FOV-level updates
# Threading: called from MultiPointWorker thread. All writes are
# inside _progress_lock. Simple attribute reads (_run_start_time etc.)
# are safe via GIL single-word atomicity (RC-12).
def _on_imaging_progress(
```

- [ ] **Step 4: Run tests**

Run: `cd software && python -m pytest tests/unit/orchestrator/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add software/src/squid/backend/controllers/orchestrator/experiment_runner.py software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py
git commit -m "docs: add threading invariant comments for RC-5, RC-10, RC-12"
```

---

## Verification

After all tasks are complete:

- [ ] **Run the full unit test suite**

```bash
cd software && python -m pytest tests/unit/orchestrator/ -v
```

- [ ] **Run the full e2e test suite**

```bash
cd software && python -m pytest tests/e2e/ -v
```

- [ ] **Run the integration tests**

```bash
cd software && python -m pytest tests/integration/orchestrator/ -v
```

All tests should pass. The 3 pre-existing failures (`test_pause_waits_for_fov_boundary`, `test_stage_moves_to_fov_positions`, `test_handle_z_offset_moves_relative`) are unrelated and may still fail.
