# Chunk 10: Safety and Polish

## Goal

Add safety features: reference invalidation, crash recovery, warning debounce, concurrent access prevention.

## Dependencies

- Chunk 6 (Controller)
- Chunk 9 (Acquisition Integration)

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py` | Safety features |
| `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py` | Reference clearing |

## Critical Corrections

1. **Don't rely on `__del__`**: Python's `__del__` is unreliable for hardware cleanup. Use explicit `stop()` on shutdown.

2. **Return `float("nan")` not `None`**: When blocking single-shot measurement, return nan because existing callers use `math.isnan()`.

3. **Clear all reference data**: On `ObjectiveChanged`, clear both `laser_af_properties.reference_image` AND any cached reference_crop. Use a single clear method.

## Deliverables

### Reference Invalidation

```python
def __init__(self, ...):
    ...
    # Subscribe to events that invalidate focus reference
    self._event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

def _on_objective_changed(self, event: ObjectiveChanged) -> None:
    """Objective change invalidates focus reference."""
    if self._status != "disabled":
        self._log.warning("Objective changed - stopping focus lock")
        self.stop()
        # Clear ALL reference data via single method
        self._laser_af.clear_all_references()
        self._event_bus.publish(FocusLockWarning(
            warning_type="reference_invalid",
            message="Focus reference invalidated by objective change",
        ))
```

### Single Reference Clear Method

In `laser_auto_focus_controller.py`:

```python
def clear_all_references(self) -> None:
    """Clear all stored reference data.

    Call this when reference becomes invalid (objective change, etc.)
    Uses LaserAFConfig.set_reference_image(None) which clears:
    - reference_image
    - reference_image_shape
    - reference_image_dtype
    - has_reference (sets to False)
    """
    # Use the proper API method to clear reference
    self.laser_af_properties.set_reference_image(None)
    # Clear the cached crop separately
    self.reference_crop = None
```

**Note**: `reference_crop` is a separate cached attribute on the controller that must also be cleared.

### Explicit Shutdown (NOT `__del__`)

```python
class ContinuousFocusLockController:
    def shutdown(self) -> None:
        """Explicit shutdown - call on application exit.

        Do NOT rely on __del__ for hardware cleanup.
        """
        self.stop()

# In ApplicationContext.shutdown() - integrate with existing shutdown:
# (See Chunk 8 for full integration details)
def shutdown(self) -> None:
    # ... existing cleanup ...
    if getattr(self._controllers, "continuous_focus_lock", None):
        self._controllers.continuous_focus_lock.shutdown()
```

**Important**: Use `shutdown()` everywhere, not `cleanup()`. Chunk 8 shows how to integrate with the existing `ApplicationContext.shutdown()` method.

### Crash Recovery in Control Loop

```python
def _control_loop(self) -> None:
    """Main control loop with crash recovery."""
    try:
        while self._running:
            # ... control loop logic ...
            pass
    except Exception as e:
        self._log.exception("Control loop crashed")
    finally:
        # Always clean up laser - this is the reliable path
        self._cleanup()
```

### Warning Debounce

```python
def __init__(self, ...):
    ...
    self._last_warning_time: dict[str, float] = {}
    self._warning_debounce_s: float = 5.0

def _publish_warning(self, warning_type: str, message: str) -> None:
    """Publish warning with debounce."""
    now = time.monotonic()
    last = self._last_warning_time.get(warning_type, 0)
    if now - last >= self._warning_debounce_s:
        self._last_warning_time[warning_type] = now
        self._event_bus.publish(FocusLockWarning(
            warning_type=warning_type,
            message=message,
        ))
```

### Piezo State Sync

```python
def _control_loop(self) -> None:
    last_sync = time.monotonic()
    sync_interval = 1.0  # ~1 Hz, not every iteration

    while self._running:
        # ... control logic ...

        # Periodic piezo state sync
        now = time.monotonic()
        if now - last_sync >= sync_interval:
            self._piezo_service.sync_state()
            last_sync = now
```

### Concurrent Access Prevention

Return `nan` not `None` to match existing callers:

```python
# In laser_auto_focus_controller.py
def measure_displacement(self) -> float:
    """Single-shot - blocked if continuous lock is running.

    Returns nan (not None) if blocked, because existing callers
    use math.isnan() to check for failures.
    """
    if not self._measurement_lock.acquire(timeout=0.1):
        self._log.warning("Measurement blocked - continuous lock is running")
        return float("nan")  # NOT None
    try:
        # ... existing implementation ...
        pass
    finally:
        self._measurement_lock.release()
```

### Conflict with Manual Piezo Movement

When user manually moves piezo via UI while focus lock is running:

```python
# In ContinuousFocusLockController
def __init__(self, ...):
    ...
    # Subscribe to piezo movement commands to pause lock
    self._event_bus.subscribe(SetPiezoPositionCommand, self._on_manual_piezo_move)
    self._event_bus.subscribe(MovePiezoRelativeCommand, self._on_manual_piezo_move)

def _on_manual_piezo_move(self, event) -> None:
    """Pause lock when user manually moves piezo."""
    if self._running and not self._paused:
        self._log.info("Pausing focus lock due to manual piezo movement")
        self.pause()
        # Note: User must manually resume via UI after manual adjustment
```

**Alternative: Auto-resume after delay** (optional, may be confusing to users):
```python
def _on_manual_piezo_move(self, event) -> None:
    if self._running and not self._paused:
        self.pause()
        # Schedule auto-resume after 2 seconds of no movement
        self._schedule_auto_resume()
```

### Conflict with Single-Shot Reflection AF from UI

When user triggers single-shot laser AF from UI while lock is running:

```python
# In ContinuousFocusLockController
def __init__(self, ...):
    ...
    self._event_bus.subscribe(MoveToLaserAFTargetCommand, self._on_single_shot_af)

def _on_single_shot_af(self, event) -> None:
    """Handle single-shot AF request during continuous lock."""
    if not self._running:
        return  # Let it through - lock not running

    # Option 1: Block and warn
    self._log.warning("Single-shot AF blocked - focus lock is running")
    # Publish warning event for UI
    self._event_bus.publish(FocusLockWarning(
        warning_type="action_blocked",
        message="Cannot run single-shot AF while focus lock is active. Stop focus lock first.",
    ))

    # Option 2: Temporarily pause, run single-shot, resume (more complex)
```

## Testing

```bash
cd software
pytest tests/unit/squid/backend/controllers/autofocus/ -v
```

## Completion Checklist

### Reference Invalidation
- [ ] Subscribe to `ObjectiveChanged`
- [ ] Stop lock on objective change
- [ ] Call `clear_all_references()` (single method)
- [ ] Publish warning event

### Reference Clear Method
- [ ] Add `clear_all_references()` to LaserAutofocusController
- [ ] Clear `reference_image`
- [ ] Clear `reference_crop` (if cached)
- [ ] Clear any other reference state

### Explicit Shutdown
- [ ] Add `shutdown()` method
- [ ] Call from application cleanup
- [ ] **Do NOT rely on `__del__`**

### Crash Recovery
- [ ] finally block in control loop
- [ ] Call `_cleanup()` on exception
- [ ] Test: kill thread, verify laser off

### Warning Debounce
- [ ] Add `_last_warning_time` dict
- [ ] Add `_warning_debounce_s` config
- [ ] Implement `_publish_warning()` with debounce
- [ ] Test: rapid warnings are debounced

### Piezo State Sync
- [ ] Add periodic `sync_state()` call (~1 Hz)
- [ ] Don't sync on every loop iteration

### Concurrent Access Prevention
- [ ] Return `float("nan")` when blocked (NOT `None`)
- [ ] Log warning when blocked
- [ ] Test: existing callers handle nan correctly

### Conflict Handling
- [ ] Subscribe to `SetPiezoPositionCommand` and `MovePiezoRelativeCommand`
- [ ] Pause lock when manual piezo movement detected
- [ ] Subscribe to `MoveToLaserAFTargetCommand` for single-shot AF
- [ ] Block or warn when single-shot AF attempted during lock
- [ ] Publish `FocusLockWarning(warning_type="action_blocked")` to inform UI

### Testing
- [ ] Unit test: Reference invalidation on objective change
- [ ] Unit test: Warning debounce
- [ ] Unit test: Crash recovery cleanup
- [ ] Integration test: Change objective while locked
- [ ] Integration test: Verify laser off after crash

### Verification
- [ ] No laser left on after errors
- [ ] Warnings not flooding event bus
- [ ] Objective change handled gracefully
- [ ] UI piezo display stays accurate
