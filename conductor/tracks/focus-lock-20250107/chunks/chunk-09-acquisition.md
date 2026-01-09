# Chunk 9: Acquisition Integration

## Goal

Integrate focus lock with multipoint acquisition and ensure it works as a background service during live view.

**Key principle**: Focus lock is a background service that runs independently. It only pauses when piezo is needed for Z-stacks.

## Operating Modes

| Mode | Behavior |
|------|----------|
| `off` | Focus lock disabled |
| `always_on` | Runs continuously in background (live view, acquisition, etc.) |
| `auto_lock` | Starts when acquisition begins, stops when acquisition ends |

**"Always on" means**:
- User can enable focus lock during live view
- Lock stays active while moving XY stage, changing channels, etc.
- Lock only pauses during Z-stacks (when `use_piezo=True`)
- Lock resumes automatically after Z-stack completes

## Dependencies

- Chunk 6 (Controller)
- Chunk 8 (App Wiring)

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/backend/controllers/multipoint/multi_point_controller.py` | Pass controller |
| `software/src/squid/backend/controllers/multipoint/multi_point_worker.py` | Z-stack pause/resume |
| `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py` | Auto-lock lifecycle |

## Critical Corrections

1. **Z-stack loop location**: The Z-stack loop is in `acquire_at_position()` method:
   ```python
   for z_level in range(self.NZ):
       # ... acquire images
   ```
   With `move_z_for_stack()` and `move_z_back_after_stack()` helper methods.

2. **Use correct acquisition events**: `AcquisitionStateChanged` does NOT have `state == PREPARING/IDLE`. It has `in_progress` and `is_aborting`. Use:
   - `AcquisitionStarted` / `AcquisitionFinished`, OR
   - `AcquisitionStateChanged(in_progress=True/False)`

3. **Only pause when `use_piezo=True`**: Check `self.use_piezo` flag before pausing.

4. **Return piezo to base Z**: `move_z_back_after_stack()` already does this - just resume lock after it completes.

## Deliverables

### Pass Controller to Worker

In `multi_point_controller.py`:
```python
def _create_worker(self, ...):
    return MultiPointWorker(
        ...
        focus_lock_controller=self._focus_lock_controller,
        ...
    )
```

### Use Continuous Lock in Autofocus

In `multi_point_worker.py`:
```python
def perform_autofocus(self, ...) -> bool:
    if self.do_reflection_af:
        if self._focus_lock_controller and self._focus_lock_controller.mode != "off":
            # Continuous mode: wait for lock
            if self._focus_lock_controller.is_running:
                return self._focus_lock_controller.wait_for_lock(timeout_s=5.0)
            # If not running, fall through to single-shot
        # Single-shot mode: existing behavior
        return self._laser_af.move_to_target(0)
    ...
```

### Z-Stack Pause/Resume

Modify `acquire_at_position()` in `multi_point_worker.py` to pause/resume around the Z-stack loop:

```python
def acquire_at_position(self, region_id: str, current_path: str, fov: int) -> None:
    if not self.perform_autofocus(region_id, fov):
        # ... error handling

    if self.NZ > 1:
        self.prepare_z_stack()

    if self.use_piezo:
        self.z_piezo_um: float = self._piezo_get_position()

    # Pause focus lock during Z-stack if using piezo
    was_locked = (
        self._focus_lock_controller is not None
        and self._focus_lock_controller.is_running
        and self.use_piezo
        and self.NZ > 1
    )
    if was_locked:
        self._focus_lock_controller.pause()

    try:
        for z_level in range(self.NZ):
            # ... existing acquisition logic ...
            # move_z_for_stack() called between levels
    finally:
        # Resume focus lock after Z-stack
        if was_locked:
            # move_z_back_after_stack() already called, piezo at base position
            self._focus_lock_controller.resume()
```

**Key points**:
- Only pause if `use_piezo=True` AND `NZ > 1` (actual Z-stack)
- Pause before Z-stack loop starts
- Resume after loop completes (in `finally` block for safety)
- Piezo returns to base Z via existing `move_z_back_after_stack()`

### Always-On During Live View

No special integration required for live view. When mode is `always_on`:

- User starts focus lock via UI (StartFocusLockCommand)
- Focus lock controller runs in background thread
- Lock stays active during:
  - XY stage movements
  - Channel switching
  - Exposure/gain changes
  - Any other live view operations
- Lock only pauses for Z-stacks (handled by MultiPointWorker)

The focus lock controller is independent of LiveController - they don't need to coordinate.

### Auto-Lock Lifecycle

Use correct events in `continuous_focus_lock.py`:

```python
def __init__(self, ...):
    ...
    # Subscribe to acquisition events for auto_lock mode
    # Use AcquisitionStarted/Finished (NOT AcquisitionStateChanged with state==)
    self._event_bus.subscribe(AcquisitionStarted, self._on_acquisition_started)
    self._event_bus.subscribe(AcquisitionFinished, self._on_acquisition_finished)

def _on_acquisition_started(self, event: AcquisitionStarted) -> None:
    """Start lock when acquisition begins (auto_lock mode)."""
    if self._mode != "auto_lock":
        return

    # Guard: Don't start if laser AF not ready
    if not self._laser_af.is_initialized:
        self._log.warning("Auto-lock: Laser AF not initialized, skipping")
        return
    if self._laser_af.laser_af_properties.reference_image is None:
        self._log.warning("Auto-lock: No reference set, skipping")
        return

    self.start()

def _on_acquisition_finished(self, event: AcquisitionFinished) -> None:
    """Stop lock when acquisition ends (auto_lock mode).

    Note: If a Z-stack is in progress, the worker's finally block may call
    resume() after this stop(). The resume() method must check if _running
    is False and not restart a stopped lock.
    """
    if self._mode == "auto_lock":
        self.stop()
```

### Race Condition: AcquisitionFinished vs Z-Stack Resume

If `AcquisitionFinished` fires while a Z-stack's `finally` block is running, the sequence could be:
1. `finally` block calls `resume()`
2. `AcquisitionFinished` handler calls `stop()`

Or vice versa. To handle this safely:

```python
def resume(self) -> None:
    """Resume lock after Z-stack.

    IMPORTANT: Only resume if still running. If stop() was called
    (e.g., acquisition ended), don't restart.
    """
    if not self._running:
        # Lock was stopped (acquisition ended), don't resume
        self._log.debug("resume() called but lock is not running, ignoring")
        return

    if not self._paused:
        return  # Not paused, nothing to do

    self._paused = False
    self._turn_on_laser()
    self._status = "searching"
    self._publish_status()
```

**Alternative using AcquisitionStateChanged**:

```python
def _on_acquisition_state(self, event: AcquisitionStateChanged) -> None:
    if self._mode != "auto_lock":
        return

    # Use in_progress flag, NOT state == PREPARING/IDLE
    if event.in_progress and not self._running:
        self.start()
    elif not event.in_progress and self._running:
        self.stop()
```

## Testing

```bash
cd software
pytest tests/integration/ -k "multipoint" -v
pytest tests/integration/test_focus_lock_integration.py -v
```

## Completion Checklist

### Controller Passing
- [ ] Add focus_lock_controller parameter to MultiPointWorker
- [ ] Pass controller from MultiPointController to worker

### Continuous Lock Usage
- [ ] Check if focus lock controller exists and mode != "off"
- [ ] Use `wait_for_lock()` instead of single-shot when lock is running
- [ ] Fall back to single-shot if lock not running

### Z-Stack Integration
- [ ] Modify `acquire_at_position()` in multi_point_worker.py
- [ ] Check `use_piezo=True` AND `NZ > 1` before pausing
- [ ] Pause focus lock before Z-stack loop
- [ ] Resume focus lock after Z-stack (in finally block)
- [ ] Note: `move_z_back_after_stack()` already returns piezo to base Z
- [ ] **Publish status on pause/resume**: `pause()` and `resume()` must publish `FocusLockStatusChanged` to keep UI accurate

### Auto-Lock Lifecycle
- [ ] Use `AcquisitionStarted`/`AcquisitionFinished` events
- [ ] OR use `AcquisitionStateChanged(in_progress=...)` correctly
- [ ] **NOT** `AcquisitionStateChanged(state=PREPARING/IDLE)` (doesn't exist)
- [ ] Start lock on acquisition start (auto_lock mode)
- [ ] Stop lock on acquisition end (auto_lock mode)
- [ ] **Guard before starting**: Check `laser_af.is_initialized` and `reference_image is not None`
- [ ] Log warning if guards fail (don't silently skip)

### Race Condition Handling
- [ ] `resume()` must check `_running` before resuming (don't restart a stopped lock)
- [ ] Handle AcquisitionFinished firing during Z-stack finally block
- [ ] Log when resume() is called on a stopped lock (for debugging)

### Testing
- [ ] Integration test: Multipoint with focus lock enabled
- [ ] Integration test: wait_for_lock returns True when locked
- [ ] Integration test: Z-stack pauses and resumes lock
- [ ] Integration test: Auto-lock starts on acquisition start
- [ ] Integration test: Auto-lock stops on acquisition end

### Verification
- [ ] Run multipoint acquisition in simulation with focus lock
- [ ] Verify single-shot AF still works (mode = off)
- [ ] Verify continuous lock works (mode = always_on or auto_lock)
- [ ] Verify Z-stack pauses lock correctly
