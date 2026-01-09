# Chunk 8: Application Wiring

## Goal

Wire the focus lock controller into the application context so it's available throughout the app.

## Dependencies

- Chunk 6 (Controller)
- Chunk 7 (Mode Gate Bypass)

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/application.py` | Add to Controllers, construct |

## Critical Corrections

1. **Use global `event_bus`**: ApplicationContext uses the global event_bus, not `self._event_bus` (which doesn't exist).

2. **Extend existing Controllers**: Don't redefine the dataclass, just add the new field.

## Deliverables

### Update Controllers Dataclass

```python
@dataclass
class Controllers:
    """All application controllers."""
    live: LiveController
    microscope_mode: MicroscopeModeController | None
    laser_autofocus: LaserAutofocusController | None
    continuous_focus_lock: ContinuousFocusLockController | None  # NEW
    multi_point: MultiPointController | None
    # ... other existing fields ...
```

### Construct Controller

```python
def _build_controllers(self, ...) -> Controllers:
    ...

    # Build continuous focus lock if laser AF and piezo are available
    continuous_focus_lock = None
    if laser_autofocus is not None and piezo_service is not None:
        from squid.core.config.focus_lock import FocusLockConfig
        from squid.backend.controllers.autofocus import ContinuousFocusLockController

        focus_lock_config = FocusLockConfig()  # Use defaults
        continuous_focus_lock = ContinuousFocusLockController(
            laser_af=laser_autofocus,
            piezo_service=piezo_service,
            event_bus=event_bus,  # Use global event_bus, NOT self._event_bus
            config=focus_lock_config,
        )
    elif simulation_mode:
        # No hardware but simulation mode - use simulator for UI development
        from squid.core.config.focus_lock import FocusLockConfig
        from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator

        continuous_focus_lock = FocusLockSimulator(event_bus, FocusLockConfig())

    return Controllers(
        ...
        continuous_focus_lock=continuous_focus_lock,
        ...
    )
```

### Integrate with Existing Shutdown

`ApplicationContext.shutdown()` already exists. Add focus lock cleanup there (don't create a new `cleanup()` method):

```python
# In ApplicationContext.shutdown() - ADD to existing method
def shutdown(self) -> None:
    """Clean shutdown of all components."""
    self._log.info("Shutting down application...")

    # ... existing GUI cleanup ...

    # Shutdown controllers
    if self._controllers:
        # ... existing controller cleanup ...

        # ADD: Focus lock cleanup
        if getattr(self._controllers, "continuous_focus_lock", None):
            try:
                self._controllers.continuous_focus_lock.shutdown()
            except Exception:
                self._log.exception("Failed to shutdown focus lock controller")

        # ... rest of existing cleanup ...
```

### Preview Stream Handler Wiring (for Chunk 11)

The preview stream handler connects the backend controller to the UI widget. This wiring happens after both are created:

```python
# In main_window.py or wherever UI wiring happens
def _wire_focus_lock_preview(self) -> None:
    """Wire preview stream from controller to widget.

    Called after both controller and widget exist.
    """
    controller = self._app_context.controllers.continuous_focus_lock
    widget = self._focus_lock_widget

    if controller is None or widget is None:
        return  # Graceful degradation

    # Widget creates Qt handler, gives backend handler to controller
    qt_handler = widget.setup_preview()
    if qt_handler is not None:
        controller.set_preview_handler(qt_handler.handler)
        qt_handler.set_enabled(True)
```

**Key points:**
- Widget owns the `QtFocusLockStreamHandler` (frontend)
- Controller receives only the `FocusLockStreamHandler` (backend, no Qt)
- Wiring happens in UI layer (main_window.py), not application.py
- Null checks for graceful degradation

## Testing

```bash
cd software
pytest tests/integration/ -v
python main_hcs.py --simulation  # Verify app starts
```

## Completion Checklist

### Controllers Dataclass
- [ ] Add `continuous_focus_lock: ContinuousFocusLockController | None` field
- [ ] Extend existing dataclass (don't redefine)

### Construction
- [ ] Import `ContinuousFocusLockController`
- [ ] Import `FocusLockConfig`
- [ ] Check if laser_autofocus and piezo_service exist
- [ ] Construct controller with all dependencies
- [ ] Use global `event_bus` (NOT `self._event_bus`)
- [ ] Add to Controllers instance
- [ ] Fallback to `FocusLockSimulator` in simulation mode when no hardware

### Preview Wiring (for Chunk 11)
- [ ] Add `_wire_focus_lock_preview()` in main_window.py
- [ ] Call after both controller and widget exist
- [ ] Widget creates QtFocusLockStreamHandler
- [ ] Controller receives FocusLockStreamHandler (backend only)
- [ ] Handle None cases for graceful degradation

### Shutdown (integrate with existing)
- [ ] Add focus lock cleanup to existing `ApplicationContext.shutdown()`
- [ ] Use `getattr()` for safe attribute access
- [ ] Wrap in try/except for graceful error handling
- [ ] Do NOT create new `cleanup()` method - use existing `shutdown()`

### Testing
- [ ] Integration test: App starts with focus lock controller
- [ ] Integration test: Controller accessible via `app.controllers.continuous_focus_lock`
- [ ] Simulation mode works

### Verification
- [ ] `cd software && python main_hcs.py --simulation` starts without errors
- [ ] Controller is not None when hardware present
- [ ] Controller is None when hardware absent (graceful degradation)
