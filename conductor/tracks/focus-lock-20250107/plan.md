# Focus Lock Implementation Plan

## Summary

Implement continuous closed-loop focus lock with:
- Mode-based operation (Off / Always On / Auto Lock)
- Gain-scheduled control algorithm
- Dockable status widget visible alongside live imaging
- Fine adjust controls for lock target position
- Comprehensive quality metrics (real SNR/intensity/correlation)
- Piezo-only Z control with range warnings
- Continuous laser usage during lock (no per-iteration toggling)

---

## Phase 1: Events, Config, and Defaults

### 1.1 Add New Events

**File: `software/src/squid/core/events.py`**

Add focus lock events and commands (same as previous plan):
- `FocusLockModeChanged`
- `FocusLockStatusChanged`
- `FocusLockMetricsUpdated`
- `FocusLockWarning`
- `SetFocusLockModeCommand`
- `StartFocusLockCommand`
- `StopFocusLockCommand`
- `PauseFocusLockCommand`
- `ResumeFocusLockCommand`
- `AdjustFocusLockTargetCommand`

### 1.2 Add Configuration Model (Pydantic + _def defaults)

**File: `software/src/squid/core/config/focus_lock.py`** (new file)

Implement `FocusLockConfig` as a Pydantic model (frozen) with defaults from `software/src/_def.py`:
- `FOCUS_LOCK_GAIN`
- `FOCUS_LOCK_GAIN_MAX`
- `FOCUS_LOCK_BUFFER_LENGTH`
- `FOCUS_LOCK_OFFSET_THRESHOLD_UM`
- `FOCUS_LOCK_MIN_SPOT_SNR`
- `FOCUS_LOCK_LOOP_RATE_HZ`
- `FOCUS_LOCK_METRICS_RATE_HZ`
- `FOCUS_LOCK_PIEZO_WARNING_MARGIN_UM`
- `FOCUS_LOCK_DEFAULT_MODE`

Also add new constants to `software/src/_def.py`.

---

## Phase 2: Laser AF Measurement API (Continuous + Metrics)

### 2.1 Add Result Payload

**File: `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py`**

Add `LaserAFResult` dataclass, including:
- `displacement_um`
- `spot_intensity`
- `spot_snr`
- `correlation` (optional, vs reference spot)
- `is_good_reading`
- `spot_x_px`, `spot_y_px` (optional)
- `timestamp` (for drift rate calculation)

### 2.2 Add Continuous Measurement Method

**File: `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py`**

Add `measure_displacement_continuous()`:
- Assumes laser is already ON.
- Triggers camera only when in software trigger mode; check `_focus_camera.get_trigger_mode()`.
- Reads a frame and computes `LaserAFResult`.
- Does NOT toggle laser or publish `LaserAFDisplacementMeasured` each call.
- Correlation computed vs stored reference spot (captured at lock set point).

### 2.3 Add Measurement Lock

**File: `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py`**

Add `_measurement_lock: threading.Lock` to prevent concurrent access:
- Both `measure_displacement()` (single-shot) and `measure_displacement_continuous()` acquire lock.
- Prevents race conditions if UI triggers single-shot AF while continuous lock is running.

### 2.4 Surface Spot Metrics

**File: `software/src/squid/core/utils/hardware_utils.py`**

Extend spot detection to return intensity and background metrics, or add a helper:
- Return peak intensity, background estimate, and SNR (or enough to compute them).
- Preserve the existing centroid return path for current callers.

---

## Phase 3: Continuous Focus Lock Controller

### 3.1 Create ContinuousFocusLockController

**File: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`** (new file)

Key requirements:
- Control loop uses `measure_displacement_continuous()` (not the toggle version).
- Laser lifecycle management: laser ON at `start()`/`resume()`, OFF at `stop()`/`pause()`.
- Safety cleanup: ensure laser OFF even if loop crashes or stop is called while thread fails.
- Handle focus camera trigger mode when requesting frames.
- Use `LaserAFResult` metrics to publish real `FocusLockMetricsUpdated`.
- Good-reading logic uses SNR/correlation thresholds, not just error magnitude.
- Explicit status transitions (`searching`, `locked`, `lost`, `paused`, `disabled`).
- Throttle status updates and warnings to avoid UI spam.
- Thread safety: guard laser AF measurement calls to avoid concurrent usage.

**Laser State Tracking:**
- Add `_laser_on: bool` field to track expected laser state.
- On `start()`/`resume()`: set `_laser_on = True`, then call laser ON.
- On `stop()`/`pause()`: call laser OFF, then set `_laser_on = False`.
- In cleanup/`__del__`: if `_laser_on`, ensure laser OFF.
- This prevents double-off calls and provides audit trail.

**Mode Gate Interaction:**
- Focus lock is a backend controller, not UI-driven hardware command.
- Laser control via `PeripheralService.turn_on_af_laser()` / `turn_off_af_laser()` uses `_perform_action()`.
- These methods check `_blocked_for_ui_hardware_commands()` which queries `GlobalModeGate`.
- **Solution**: Use direct laser control that bypasses mode gate, OR ensure mode gate is compatible:
  - Option A: Add `bypass_mode_gate=True` parameter to service methods for backend-initiated calls.
  - Option B: Ensure `ContinuousFocusLockController` is registered with mode gate as acquisition participant.
  - Recommended: Option A - explicit bypass for backend controllers.

**Correlation Reference:**
- Store reference spot image when `set_reference()` is called.
- Compute correlation of current spot vs reference for `LaserAFResult.correlation`.
- Reference invalidated on: objective change, significant sample movement, user reset.

### 3.2 Export Controller

**File: `software/src/squid/backend/controllers/autofocus/__init__.py`**

Add export:
```python
from squid.backend.controllers.autofocus.continuous_focus_lock import ContinuousFocusLockController
```

Also re-export in `software/src/squid/backend/controllers/__init__.py`.

---

## Phase 4: UI Widget (QtPy)

### 4.1 Create FocusLockStatusWidget

**File: `software/src/squid/ui/widgets/hardware/focus_lock_status.py`** (new file)

Implementation notes:
- Use QtPy (`qtpy.QtWidgets`, `qtpy.QtCore`, `qtpy.QtGui`) for consistency.
- Subscribe via `UIEventBus` to focus lock events.
- Use LED/status indicator, buffer bar, error bar, and numeric metrics.
- Keep widget dockable/collapsible and lightweight (~10 Hz updates).
- Include fine adjust controls (step + up/down) for relative target moves.

**AF Camera Preview:**
- Existing `streamHandler_focus_camera` may not have a live preview mechanism.
- Two options:
  - Option A: Add `FocusLockFrameUpdated` event with cropped spot region (low overhead).
  - Option B: Have widget directly subscribe to focus camera's `StreamHandler.frame_ready` signal.
- Recommended: Option A - controller publishes cropped spot region in metrics update for minimal coupling.
- Preview should show spot position overlay (crosshair at detected centroid).

### 4.2 Register Widget

- `software/src/squid/ui/widgets/hardware/__init__.py`
- `software/src/squid/ui/widgets/__init__.py` (lazy import mapping)

---

## Phase 5: App Wiring + Layout

### 5.1 Build Controller in ApplicationContext

**File: `software/src/squid/application.py`**

- Add `continuous_focus_lock` to `Controllers` dataclass.
- Construct the controller using `LaserAutofocusController`, `PiezoService`, `event_bus`, and `FocusLockConfig`.

### 5.2 Widget Creation via Factory

**File: `software/src/squid/ui/gui/widget_factory.py`**

- Instantiate `FocusLockStatusWidget` in `create_laser_autofocus_widgets`.
- Connect focus camera stream (`streamHandler_focus_camera`) to widget preview.

### 5.3 Layout Integration

**File: `software/src/squid/ui/gui/layout_builder.py`** (and `software/src/squid/ui/main_window.py` as needed)

- Dock/split `imageDisplayTabs` with `FocusLockStatusWidget` so it persists across tabs.
- Keep the focus lock panel visible on Live View, Mosaic, etc.

---

## Phase 6: Acquisition Integration

### 6.1 MultiPoint Worker Hooks

**File: `software/src/squid/backend/controllers/multipoint/multi_point_worker.py`**

- Use continuous lock when enabled:
  - In `perform_autofocus`, if lock active, wait for lock rather than single-shot move.
  - Add `wait_for_lock(timeout_s: float) -> bool` method to controller.

**Z-Stack Pause Location:**
- In `_acquire_at_position()`, the Z-stack loop is in `_perform_z_stack()` or similar.
- Pause lock BEFORE entering Z-stack loop:
  ```python
  was_locked = self._focus_lock_controller and self._focus_lock_controller.is_running
  if was_locked:
      self._focus_lock_controller.pause()
  ```
- Resume AFTER Z-stack completes (in finally block):
  ```python
  finally:
      if was_locked:
          self._focus_lock_controller.resume()
  ```
- Ensure piezo returns to pre-Z-stack position before resuming lock.

### 6.2 Auto-Lock Lifecycle

**File: `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py`**

- Subscribe to acquisition events and auto-start/stop in `auto_lock` mode:
  - `AcquisitionStarted` / `AcquisitionFinished` or `AcquisitionStateChanged`.

**Auto-Lock Timing:**
- In `auto_lock` mode, start lock on `AcquisitionStateChanged(state=PREPARING)`.
- This gives time for lock to stabilize before first image.
- Worker should call `wait_for_lock()` before acquiring first frame.
- Stop lock on `AcquisitionStateChanged(state=IDLE)` or `AcquisitionFinished`.

**State Transitions:**
```
auto_lock mode:
  IDLE → PREPARING: start lock, status = "searching"
  lock achieves N/N: status = "locked"
  PREPARING → RUNNING: (already locked, continue)
  RUNNING → IDLE: stop lock, status = "disabled"
```

---

## Phase 7: Safety, Performance, and State Consistency

### 7.1 Piezo State Sync
- Add periodic `PiezoService.sync_state()` to keep UI position accurate if using `move_to_fast()`.
- Sync at ~1 Hz (not every control loop iteration) to minimize serial traffic.

### 7.2 Event Throttling
- Add warning debounce and status throttling to avoid event bus flooding.
- `FocusLockMetricsUpdated`: Publish at `FOCUS_LOCK_METRICS_RATE_HZ` (default 10 Hz).
- `FocusLockStatusChanged`: Publish only on actual status transitions.
- `FocusLockWarning`: Debounce by warning type (e.g., max once per 5 seconds per type).

### 7.3 Mode Gate Compatibility
- Ensure mode gate compatibility (ignore or pause lock when global mode blocks UI hardware commands).
- Focus lock backend calls should bypass mode gate (see Phase 3.1).
- If user tries to change mode via UI during acquisition, mode gate may block - handle gracefully.

### 7.4 Reference Invalidation
Subscribe to events that invalidate the focus reference:
- `ObjectiveChanged`: Stop lock, clear reference, warn user.
- Significant stage XY movement (> threshold): Optional warning, reference may still be valid.
- User-initiated reset: Clear reference, require new `set_reference()` call.

### 7.5 Crash Recovery
- In controller `__del__` or `atexit` handler, ensure laser OFF.
- If control loop thread crashes, catch exception and call cleanup:
  ```python
  try:
      self._control_loop()
  except Exception as e:
      self._log.exception("Control loop crashed")
      self._cleanup_laser()
      raise
  ```

### 7.6 Concurrent Access Prevention
- Single-shot AF (`measure_displacement()`) should be blocked while continuous lock is running.
- Either: acquire `_measurement_lock` and wait, OR raise/return error.
- Recommended: Return early with warning if lock is active.

---

## Phase 8: Testing

### 8.1 Unit Tests

**File: `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py`**

Add tests for:
- Gain scheduling
- Lock buffer transitions
- LaserAFResult metric handling
- Status state transitions and throttle behavior

### 8.2 Integration Tests

**File: `software/tests/integration/test_focus_lock_integration.py`**

Test in simulation:
- Mode switching
- Lock acquisition/loss
- Auto-lock start/stop on acquisition events
- Pause/resume during Z-stack

---

## Implementation Order

1. Events + config + `_def` defaults
2. Laser AF result + continuous measurement API
3. Continuous controller logic + safety/metrics
4. UI widget + registration
5. Application wiring + layout
6. Acquisition integration
7. Testing

---

## Files to Create

| File | Purpose |
|------|---------|
| `squid/core/config/focus_lock.py` | Focus lock config model |
| `squid/backend/controllers/autofocus/continuous_focus_lock.py` | Continuous controller |
| `squid/ui/widgets/hardware/focus_lock_status.py` | Status widget |
| `tests/unit/.../test_continuous_focus_lock.py` | Unit tests |
| `tests/integration/test_focus_lock_integration.py` | Integration tests |

## Files to Modify

| File | Changes |
|------|---------|
| `squid/core/events.py` | Add focus lock events/commands |
| `software/src/_def.py` | Add focus lock defaults |
| `squid/backend/controllers/autofocus/laser_auto_focus_controller.py` | LaserAFResult + continuous measurement |
| `squid/core/utils/hardware_utils.py` | Surface intensity/SNR metrics |
| `squid/backend/controllers/autofocus/__init__.py` | Export controller |
| `squid/backend/controllers/__init__.py` | Export controller |
| `squid/application.py` | DI wiring, Controllers dataclass |
| `squid/backend/controllers/multipoint/multi_point_controller.py` | Pass focus lock controller |
| `squid/backend/controllers/multipoint/multi_point_worker.py` | Autofocus + Z-stack integration |
| `squid/ui/gui/widget_factory.py` | Widget construction |
| `squid/ui/gui/layout_builder.py` | Dock/split integration |
| `squid/ui/widgets/hardware/__init__.py` | Widget export |
| `squid/ui/widgets/__init__.py` | Lazy import registration |

## Dependencies

- Existing `LaserAutofocusController`
- Existing `PiezoService`
- Existing `EventBus`
- QtPy (for UI)

## Risk Mitigation

1. **Performance**: Continuous laser ON + no per-iteration toggles; metrics throttled.
2. **Thread Safety**: Laser AF measurement guarded to avoid concurrent access.
3. **Backwards Compatibility**: Mode="off" preserves existing single-shot behavior.
4. **Piezo Limits**: Warnings + clamp guard movement.
5. **Safety**: Ensure laser is OFF on stop/pause/crash.

---

## Implementation Checklist

### Phase 1: Events, Config, Defaults
- [ ] Add focus lock events/commands in `software/src/squid/core/events.py`.
- [ ] Add `AdjustFocusLockTargetCommand` for fine target nudges.
- [ ] Add `FocusLockFrameUpdated` event for widget camera preview.
- [ ] Add focus lock defaults in `software/src/_def.py`.
- [ ] Add `FocusLockConfig` (Pydantic) in `software/src/squid/core/config/focus_lock.py`.

### Phase 2: Laser AF Measurement API
- [ ] Add `LaserAFResult` dataclass with timestamp field.
- [ ] Add `measure_displacement_continuous()` with trigger mode check.
- [ ] Add `_measurement_lock: threading.Lock` to prevent concurrent access.
- [ ] Store reference spot for correlation calculation.
- [ ] Surface intensity/SNR metrics from `software/src/squid/core/utils/hardware_utils.py`.

### Phase 3: Continuous Focus Lock Controller
- [ ] Implement `ContinuousFocusLockController` with laser lifecycle and safety cleanup.
- [ ] Add `_laser_on: bool` field for state tracking.
- [ ] Add `set_reference()` method to capture reference spot.
- [ ] Add correlation calculation vs stored reference.
- [ ] Add mode gate bypass for backend-initiated laser control.
- [ ] Export controller in `software/src/squid/backend/controllers/autofocus/__init__.py`.
- [ ] Export controller in `software/src/squid/backend/controllers/__init__.py`.

### Phase 4: UI Widget
- [ ] Build `FocusLockStatusWidget` (QtPy) with status LED, metrics, bars.
- [ ] Add AF camera preview with spot overlay.
- [ ] Add fine adjust controls for lock target.
- [ ] Register in `software/src/squid/ui/widgets/hardware/__init__.py`.
- [ ] Register in `software/src/squid/ui/widgets/__init__.py`.

### Phase 5: App Wiring + Layout
- [ ] Add `continuous_focus_lock` to `Controllers` dataclass in `software/src/squid/application.py`.
- [ ] Wire controller with dependencies (LaserAF, PiezoService, EventBus, config).
- [ ] Instantiate widget in `software/src/squid/ui/gui/widget_factory.py`.
- [ ] Integrate widget into layout via `software/src/squid/ui/gui/layout_builder.py`.

### Phase 6: Acquisition Integration
- [ ] Add `wait_for_lock(timeout_s: float) -> bool` method to controller.
- [ ] Integrate focus lock into `perform_autofocus` in multi_point_worker.py.
- [ ] Add Z-stack pause/resume hooks in `_perform_z_stack()` or equivalent.
- [ ] Pass focus lock controller through multipoint controller to worker.
- [ ] Add auto-lock start on `AcquisitionStateChanged(state=PREPARING)`.
- [ ] Add auto-lock stop on `AcquisitionStateChanged(state=IDLE)`.

### Phase 7: Safety, Performance, State
- [ ] Add periodic `PiezoService.sync_state()` at ~1 Hz.
- [ ] Add warning debounce (max once per 5s per type).
- [ ] Add status transition throttling.
- [ ] Subscribe to `ObjectiveChanged` for reference invalidation.
- [ ] Add crash recovery cleanup in `__del__` or thread exception handler.
- [ ] Block single-shot AF when continuous lock is active.

### Phase 8: Testing
- [ ] Unit tests for gain scheduling.
- [ ] Unit tests for lock buffer transitions.
- [ ] Unit tests for LaserAFResult metric handling.
- [ ] Unit tests for status state transitions.
- [ ] Integration tests for mode switching.
- [ ] Integration tests for auto-lock lifecycle.
- [ ] Integration tests for Z-stack pause/resume.
