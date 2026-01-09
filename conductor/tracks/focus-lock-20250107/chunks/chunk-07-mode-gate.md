# Chunk 7: Mode Gate Bypass

## Goal

Allow focus lock controller to control laser without mode gate blocking during acquisitions.

## Dependencies

- None (can be done early)

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/backend/services/peripheral_service.py` | Add bypass parameter |

## Background

The `PeripheralService` checks `GlobalModeGate` before executing hardware commands:

```python
def turn_on_af_laser(self):
    if self._blocked_for_ui_hardware_commands():
        return
    self._perform_action(...)
```

During acquisition, the mode gate blocks UI-initiated commands. But focus lock is a backend controller that needs to control the laser during acquisition.

## Deliverables

### Add Bypass Parameter

```python
def turn_on_af_laser(self, bypass_mode_gate: bool = False) -> None:
    """
    Turn on the autofocus laser.

    Args:
        bypass_mode_gate: If True, skip mode gate check. Use ONLY from backend
                         controllers that manage their own lifecycle (e.g.,
                         ContinuousFocusLockController). UI code must NEVER
                         pass bypass_mode_gate=True.
    """
    if not bypass_mode_gate and self._blocked_for_ui_hardware_commands():
        return
    self._perform_action(lambda: self._microcontroller.turn_on_af_laser())

def turn_off_af_laser(self, bypass_mode_gate: bool = False) -> None:
    """
    Turn off the autofocus laser.

    Args:
        bypass_mode_gate: If True, skip mode gate check. Use ONLY from backend
                         controllers that manage their own lifecycle.
                         UI code must NEVER pass bypass_mode_gate=True.
    """
    if not bypass_mode_gate and self._blocked_for_ui_hardware_commands():
        return
    self._perform_action(lambda: self._microcontroller.turn_off_af_laser())
```

## Testing

```bash
cd software
pytest tests/unit/squid/backend/services/ -v
```

## Completion Checklist

### Implementation
- [ ] Add `bypass_mode_gate: bool = False` to `turn_on_af_laser()`
- [ ] Add `bypass_mode_gate: bool = False` to `turn_off_af_laser()`
- [ ] Skip mode gate check when bypass is True
- [ ] Add clear docstring: "Use ONLY from backend controllers"
- [ ] Add warning: "UI code must NEVER pass bypass_mode_gate=True"

### Safety
- [ ] Add test to verify UI code never uses bypass
- [ ] Grep codebase to ensure no UI widget passes bypass_mode_gate=True
- [ ] Document intended usage pattern

### Testing
- [ ] Unit test: Default behavior unchanged (mode gate checked)
- [ ] Unit test: Bypass parameter skips mode gate
- [ ] Integration test: Focus lock can control laser during acquisition

### Verification
- [ ] Existing code still works (default behavior)
- [ ] Focus lock controller can use bypass
- [ ] No UI code uses bypass
