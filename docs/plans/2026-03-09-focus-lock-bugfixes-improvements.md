# Focus Lock Bugfixes & Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix identified bugs and reduce divergence risk between `ContinuousFocusLockController` and `FocusLockSimulator`.

**Architecture:** Both controllers share the same state machine and control logic but are implemented independently. This plan fixes bugs in-place first, then extracts a shared status-publish dedup pattern, adds search-failure piezo restoration, and aligns `wait_for_lock` semantics. We do NOT extract a full shared state machine base class — that's a larger refactor for another day.

**Tech Stack:** Python 3.10+, Pydantic, threading, pytest

**Key files:**
- `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py` — real hardware controller
- `software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py` — simulation controller
- `software/src/squid/core/config/focus_lock.py` — Pydantic config model
- `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py` — real controller tests
- `software/tests/unit/squid/backend/controllers/autofocus/test_focus_lock_simulator.py` — simulator tests

**Run all focus lock tests with:**
```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/ -v
```

---

### Task 1: Remove dead code and stale `pass` statements

Cleanup pass. No behavior changes, no new tests needed.

**Files:**
- Modify: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`

**Step 1: Remove dead `pass` in `_on_auto_search_command`**

In `_on_auto_search_command` (around line 339), remove the trailing `pass` after the `with self._lock` block.

**Step 2: Remove dead `pass` statements in `_update_lock_state`**

Remove the lone `pass` statements at the end of `else` branches around lines 677, 681, 692. These are inside `if/elif/else` blocks where the `else` already has a real statement above the `pass`.

**Step 3: Fix `_set_lock_reference` dead branch**

In `_set_lock_reference` (around line 410-424), replace:
```python
if math.isnan(target_um):
    target_um = self._latest_valid_displacement_um
    if not math.isnan(target_um):
        pass
```
with:
```python
if math.isnan(target_um):
    target_um = self._latest_valid_displacement_um
    if not math.isnan(target_um):
        self._log.info(
            "Using last valid displacement (%.3f um) as lock reference", target_um
        )
```

**Step 4: Run tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/ -v
```
Expected: All 79 tests PASS (no behavior change).

**Step 5: Commit**

```bash
git add software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py
git commit -m "cleanup: Remove dead pass statements and add fallback log in ContinuousFocusLockController"
```

---

### Task 2: Add status-change deduplication to `ContinuousFocusLockController._set_status`

**Problem:** The real controller publishes `FocusLockStatusChanged` on every `_set_status` call even when the status hasn't changed. The simulator already deduplicates. This floods the event bus with redundant events (e.g., `apply_settings` calls `_set_status(self._status)` to refresh the UI).

**Files:**
- Modify: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`
- Test: `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py`

**Step 1: Write the failing test**

Add to `test_continuous_focus_lock.py`:

```python
def test_set_status_deduplicates_events(started_controller, event_bus):
    """_set_status should not publish when status hasn't changed."""
    # Controller starts in "ready" — one StatusChanged already published.
    status_events = [
        e for e in event_bus.published
        if isinstance(e, FocusLockStatusChanged)
    ]
    count_before = len(status_events)

    # Call _set_status with the same status again.
    started_controller._set_status("ready")

    status_events_after = [
        e for e in event_bus.published
        if isinstance(e, FocusLockStatusChanged)
    ]
    assert len(status_events_after) == count_before, (
        "Redundant StatusChanged event published for unchanged status"
    )
```

**Step 2: Run test to verify it fails**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py::test_set_status_deduplicates_events -v
```
Expected: FAIL (currently publishes every time).

**Step 3: Add dedup guard to `_set_status`**

In `continuous_focus_lock.py`, add a `_last_published_status` field to `__init__`:
```python
self._last_published_status: Optional[str] = None
```

Modify `_set_status`:
```python
def _set_status(self, status: str) -> None:
    with self._lock:
        self._status = status
        if status == self._last_published_status:
            return
        self._last_published_status = status
        buffer_fill = self._lock_buffer_fill
        buffer_length = self._config.buffer_length
    self._event_bus.publish(
        FocusLockStatusChanged(
            is_locked=status == "locked",
            status=status,
            lock_buffer_fill=buffer_fill,
            lock_buffer_length=buffer_length,
        )
    )
```

Also add `self._last_published_status = None` to `_reset_lock_state`.

**Note:** `_on_set_params` currently calls `self._set_status(self._status)` to force a UI refresh of lock buffer length. After adding dedup, this will be suppressed. Fix by adding `self._last_published_status = None` inside the `if updates:` block before calling `_set_status`, so the next publish is forced. Same for `apply_settings`.

**Step 4: Run tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py -v
```
Expected: All PASS including the new test.

**Step 5: Commit**

```bash
git add software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py \
      software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py
git commit -m "fix: Deduplicate FocusLockStatusChanged events in ContinuousFocusLockController"
```

---

### Task 3: Fix `_check_warnings` NaN-safe SNR comparison

**Problem:** `result.spot_snr < self._config.min_spot_snr` silently returns `False` when `spot_snr` is `NaN`, so the low-SNR warning is never published for invalid readings.

**Files:**
- Modify: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`
- Test: `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py`

**Step 1: Write the failing test**

```python
def test_check_warnings_nan_snr_publishes_snr_low(started_controller, event_bus, make_result):
    """NaN spot_snr should still trigger snr_low warning."""
    result = make_result(spot_snr=float("nan"))
    started_controller._check_warnings(result, error_um=0.0)
    warnings = [e for e in event_bus.published if isinstance(e, FocusLockWarning) and e.warning_type == "snr_low"]
    assert len(warnings) >= 1
```

(Adapt `make_result` fixture as needed to produce a `LaserAFResult` with controllable fields.)

**Step 2: Run test to verify it fails**

Expected: FAIL — NaN comparison returns False, no warning published.

**Step 3: Fix the comparison**

In `_check_warnings` (around line 1018), change:
```python
if result.spot_snr < self._config.min_spot_snr:
```
to:
```python
if math.isnan(result.spot_snr) or result.spot_snr < self._config.min_spot_snr:
```

**Step 4: Run tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py -v
```
Expected: All PASS.

**Step 5: Commit**

```bash
git add software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py \
      software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py
git commit -m "fix: Publish snr_low warning when spot_snr is NaN"
```

---

### Task 4: Fix simulator `wait_for_lock` to return `False` when not running

**Problem:** `FocusLockSimulator.wait_for_lock` returns `True` when not running, while the real controller returns `False`. This could mask issues in simulation-mode tests and `AutofocusExecutor.verify_focus_lock_before_capture`.

**Files:**
- Modify: `software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py`
- Test: `software/tests/unit/squid/backend/controllers/autofocus/test_focus_lock_simulator.py`

**Step 1: Write the failing test**

```python
def test_wait_for_lock_returns_false_when_not_running(event_bus):
    """wait_for_lock should return False when simulator isn't running."""
    from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator
    sim = FocusLockSimulator(event_bus)
    assert not sim.is_running
    assert sim.wait_for_lock(timeout_s=0.1) is False
```

**Step 2: Run test to verify it fails**

Expected: FAIL — currently returns `True`.

**Step 3: Fix the return value**

In `focus_lock_simulator.py`, `wait_for_lock` (around line 278), change:
```python
if not self.is_running:
    return True
```
to:
```python
if not self.is_running:
    return False
```

**Step 4: Run all focus lock tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/ -v
```
Expected: All PASS. If any existing test relied on the old behavior, fix those tests — they were testing a bug.

**Step 5: Commit**

```bash
git add software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py \
      software/tests/unit/squid/backend/controllers/autofocus/test_focus_lock_simulator.py
git commit -m "fix: FocusLockSimulator.wait_for_lock returns False when not running"
```

---

### Task 5: Return piezo to last locked position when search fails

**Problem:** When `_search_step` exhausts the sweep without finding focus, the piezo is left at the last search position. This could be far from the original locked position, making manual recovery harder.

**Files:**
- Modify: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`
- Modify: `software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py`
- Test: `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py`

**Step 1: Write the failing test (real controller)**

```python
def test_search_failure_restores_piezo_to_locked_position(started_controller, piezo_service):
    """When search sweep fails, piezo should return to last locked position."""
    # Setup: get into searching state
    started_controller._lock_reference_active = True
    started_controller._locked_piezo_um = 150.0
    started_controller._start_search()
    started_controller._set_status("searching")

    # Exhaust search by setting index past end
    started_controller._search_position_index = len(started_controller._search_positions)

    # This should detect exhausted sweep and go to "lost"
    started_controller._search_step()

    assert started_controller.status == "lost"
    # Piezo should be restored to locked position
    assert piezo_service.get_position() == pytest.approx(150.0, abs=0.1)
```

**Step 2: Run test to verify it fails**

Expected: FAIL — piezo is not moved back.

**Step 3: Add piezo restore on search failure**

In `ContinuousFocusLockController._search_step`, in the search-exhausted branch (around line 841), add a `move_to` before setting status:
```python
else:
    # Search failed — restore piezo to last known good position
    self._piezo_service.move_to(self._locked_piezo_um)
    self._lock_buffer_fill = 0
    self._set_status("lost")
```

Apply the same fix in `FocusLockSimulator._search_step` (around line 524-528):
```python
if self._search_position > search_max:
    # Search failed — restore piezo to last known good position
    if self._piezo_service is not None:
        self._piezo_service.move_to(self._locked_piezo_um)
    self._status = "lost"
    self._lock_buffer_fill = 0
    self._log.warning("Focus lock lost - search sweep completed without finding lock")
    self._publish_status_if_needed()
```

**Step 4: Run tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/ -v
```
Expected: All PASS.

**Step 5: Commit**

```bash
git add software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py \
      software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py \
      software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py
git commit -m "fix: Restore piezo to locked position when focus search sweep fails"
```

---

### Task 6: Add `search_timeout_s` config parameter and enforce it

**Problem:** A misconfigured search range can make the sweep block the control loop for an unbounded duration.

**Files:**
- Modify: `software/src/squid/core/config/focus_lock.py`
- Modify: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`
- Modify: `software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py`
- Test: `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py`

**Step 1: Add `search_timeout_s` to config**

In `focus_lock.py`, add to `FocusLockConfig`:
```python
search_timeout_s: float = 30.0
```

Add to `_def.py` (or wherever constants live):
```python
FOCUS_LOCK_SEARCH_TIMEOUT_S = 30.0
```

Add a validator in `_validate_cross_fields`:
```python
if self.search_timeout_s <= 0:
    raise ValueError(f"search_timeout_s must be positive, got {self.search_timeout_s}")
```

**Step 2: Write the failing test**

```python
def test_search_times_out(started_controller, piezo_service):
    """Search should abort and go to 'lost' after search_timeout_s."""
    # Use a very short timeout
    started_controller._config = started_controller._config.model_copy(
        update={"search_timeout_s": 0.0}  # immediate timeout
    )
    started_controller._lock_reference_active = True
    started_controller._locked_piezo_um = 150.0
    started_controller._start_search()
    started_controller._set_status("searching")

    # Simulate one search step (should detect timeout)
    started_controller._search_step()

    assert started_controller.status == "lost"
```

**Step 3: Implement timeout check**

In `ContinuousFocusLockController._start_search`, record start time:
```python
self._search_start_time = time.monotonic()
```

Add `self._search_start_time: Optional[float] = None` to `__init__` and `_reset_lock_state`.

In `_search_step`, add at the top (after the status guard):
```python
if self._search_start_time is not None:
    elapsed = time.monotonic() - self._search_start_time
    if elapsed >= self._config.search_timeout_s:
        self._log.warning("Focus search timed out after %.1fs", elapsed)
        self._piezo_service.move_to(self._locked_piezo_um)
        self._lock_buffer_fill = 0
        self._set_status("lost")
        return
```

Apply the same pattern to `FocusLockSimulator._search_step`.

**Step 4: Run tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/ -v
```
Expected: All PASS.

**Step 5: Commit**

```bash
git add software/src/squid/core/config/focus_lock.py \
      software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py \
      software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py \
      software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py
git commit -m "feat: Add search_timeout_s to prevent unbounded focus search sweeps"
```

Also add the `_def.py` constant file if needed.

---

### Task 7: Remove dead `_recovery_start_time is None` guard

**Problem:** In both controllers, when entering the "recovering" state, `_recovery_start_time` is always set immediately. The subsequent `if self._recovery_start_time is None` check in the bad-reading-during-recovery branch is dead code that's misleading.

**Files:**
- Modify: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`
- Modify: `software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py`

**Step 1: Remove the dead guard in both files**

In `ContinuousFocusLockController._update_lock_state`, the recovering-bad-reading branch (around line 662):
```python
# Before:
self._recovery_good_count = 0
if self._recovery_start_time is None:
    self._recovery_start_time = time.monotonic()
elapsed = time.monotonic() - self._recovery_start_time

# After:
self._recovery_good_count = 0
elapsed = time.monotonic() - self._recovery_start_time
```

Same change in `FocusLockSimulator._update_from_laser_af_result` (around line 763).

**Step 2: Run tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/ -v
```
Expected: All 79+ tests PASS.

**Step 3: Commit**

```bash
git add software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py \
      software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py
git commit -m "cleanup: Remove dead _recovery_start_time is None guard in both focus lock controllers"
```

---

### Task 8: Make integral save/restore in NaN holdover explicit

**Problem:** The holdover path saves and restores `_integral_accumulator` around `_control_fn` because `_control_fn` mutates it as a side effect. This is fragile.

**Files:**
- Modify: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`
- Test: `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py`

**Step 1: Add `update_integral` parameter to `_control_fn`**

```python
def _control_fn(self, error_um: float, dt: float, *, update_integral: bool = True) -> float:
```

When `update_integral=False`, skip the integral accumulation but still compute the I term from the current accumulator:

```python
if self._config.ki > 0:
    piezo_pos = self._piezo_service.get_position()
    min_um, max_um = self._piezo_service.get_range()
    near_limit = (piezo_pos <= min_um + 5.0 or piezo_pos >= max_um - 5.0)
    if not near_limit and update_integral:
        self._integral_accumulator += error_um * dt
        limit = self._config.integral_limit_um
        self._integral_accumulator = max(-limit, min(limit, self._integral_accumulator))
    i_correction = -self._config.ki * self._integral_accumulator
```

**Step 2: Update the holdover call site**

Replace the save/restore pattern (around line 569-572):
```python
# Before:
saved_integral = self._integral_accumulator
correction = self._control_fn(self._last_good_error_um, period) * decay
self._integral_accumulator = saved_integral

# After:
correction = self._control_fn(
    self._last_good_error_um, period, update_integral=False
) * decay
```

**Step 3: Run tests**

```bash
cd software && pytest tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py -v
```
Expected: All PASS (including `test_nan_holdover_does_not_update_integral`).

**Step 4: Commit**

```bash
git add software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py
git commit -m "refactor: Add update_integral flag to _control_fn, remove save/restore pattern in NaN holdover"
```

---

## Summary

| Task | Type | Risk | Description |
|------|------|------|-------------|
| 1 | Cleanup | None | Remove dead `pass` and fix silent fallback |
| 2 | Bug fix | Low | Deduplicate status events in real controller |
| 3 | Bug fix | Low | NaN-safe SNR warning comparison |
| 4 | Bug fix | Low | Simulator `wait_for_lock` semantics |
| 5 | Bug fix | Low | Restore piezo on search failure |
| 6 | Feature | Low | Search timeout to prevent unbounded sweeps |
| 7 | Cleanup | None | Remove dead recovery guard |
| 8 | Refactor | Low | Explicit integral control in holdover path |

All tasks are independent and can be committed individually. Tasks 1 and 7 are pure cleanup. Tasks 2-5 fix actual bugs. Task 6 adds a small safety feature. Task 8 is a targeted refactor.
