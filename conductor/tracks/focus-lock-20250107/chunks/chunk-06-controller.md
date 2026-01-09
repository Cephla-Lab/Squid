# Chunk 6: ContinuousFocusLockController

## Goal

Implement the real continuous focus lock controller with control loop, gain scheduling, and laser lifecycle management.

## Dependencies

- Chunk 1 (Events and Configuration)
- Chunk 4 (LaserAFResult)
- Chunk 5 (Continuous Measurement API)

## Files to Create

| File | Purpose |
|------|---------|
| `software/src/squid/backend/controllers/autofocus/continuous_focus_lock.py` | Controller |
| `software/tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py` | Tests |

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/backend/controllers/autofocus/__init__.py` | Export |
| `software/src/squid/backend/controllers/__init__.py` | Re-export |
| `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py` | Add laser control methods |

## Critical Corrections

1. **Control sign**: `return -p_term * error_um` (NEGATIVE feedback loop)
2. **Error calculation**: `error = displacement - target`
3. **Laser control**: Add `turn_on_laser()`/`turn_off_laser()` methods to LaserAutofocusController instead of reaching into PeripheralService
4. **Status publishing**: Only on actual transitions, throttle metrics

## Deliverables

### Add Laser Control to LaserAutofocusController

```python
# In laser_auto_focus_controller.py
class LaserAutofocusController:
    def turn_on_laser(self, bypass_mode_gate: bool = False) -> None:
        """Turn on AF laser for continuous lock."""
        self._peripheral_service.turn_on_af_laser(bypass_mode_gate=bypass_mode_gate)

    def turn_off_laser(self, bypass_mode_gate: bool = False) -> None:
        """Turn off AF laser after continuous lock."""
        self._peripheral_service.turn_off_af_laser(bypass_mode_gate=bypass_mode_gate)
```

### Controller Class

```python
class ContinuousFocusLockController:
    """Continuous closed-loop focus lock using laser autofocus."""

    def __init__(
        self,
        laser_af: LaserAutofocusController,
        piezo_service: PiezoService,
        event_bus: EventBus,
        config: FocusLockConfig = None,
    ):
        self._laser_af = laser_af
        self._piezo_service = piezo_service
        self._event_bus = event_bus
        self._config = config or FocusLockConfig()

        self._mode: str = self._config.default_mode
        self._status: str = "disabled"
        self._laser_on: bool = False
        self._lock_buffer: list[bool] = []
        self._target_um: float = 0.0
        self._paused: bool = False

        self._running = False
        self._stop_event = threading.Event()  # For clean shutdown signaling
        self._thread: threading.Thread | None = None
        ...
```

### Gain-Scheduled Control (NEGATIVE FEEDBACK)

```python
def _control_fn(self, error_um: float) -> float:
    """
    Gain-scheduled proportional control with NEGATIVE feedback.

    - Near target (< 0.5 μm): Use base gain for stability
    - Far from target: Use max gain for fast recovery

    Returns NEGATIVE correction (negative feedback loop).
    """
    sigma = 0.5  # μm, transition width
    dx = error_um ** 2 / sigma
    scale = self._config.gain_max - self._config.gain
    p_term = self._config.gain_max - scale * math.exp(-dx)

    # CRITICAL: Negative feedback
    return -p_term * error_um
```

### Control Loop

```python
def _control_loop(self) -> None:
    """Main control loop running in background thread."""
    period = 1.0 / self._config.loop_rate_hz
    metrics_period = 1.0 / self._config.metrics_rate_hz
    last_metrics_time = 0.0
    last_status = None

    try:
        while self._running:
            start = time.monotonic()

            result = self._laser_af.measure_displacement_continuous()

            # Error = displacement - target
            error_um = result.displacement_um - self._target_um

            # Determine if good reading (controller owns thresholds)
            is_good = (
                not math.isnan(result.displacement_um)
                and result.spot_snr >= self._config.min_spot_snr
                and abs(error_um) <= self._config.offset_threshold_um
            )

            self._update_lock_buffer(is_good)

            if is_good:
                correction = self._control_fn(error_um)
                new_pos = self._piezo_service.get_position() + correction
                new_pos = self._clamp_to_range(new_pos)
                self._piezo_service.move_to_fast(new_pos)

            # Throttled metrics publishing
            now = time.monotonic()
            if now - last_metrics_time >= metrics_period:
                self._publish_metrics(result)
                last_metrics_time = now

            # Status only on change
            if self._status != last_status:
                self._publish_status()
                last_status = self._status

            self._check_warnings(result)

            # Time-compensated sleep
            elapsed = time.monotonic() - start
            time.sleep(max(0, period - elapsed))

    except Exception as e:
        self._log.exception("Control loop crashed")
    finally:
        self._cleanup()
```

### Laser Lifecycle

```python
def _turn_on_laser(self) -> None:
    """Turn on AF laser with state tracking."""
    if not self._laser_on:
        self._laser_af.turn_on_laser(bypass_mode_gate=True)
        self._laser_on = True

def _turn_off_laser(self) -> None:
    """Turn off AF laser with state tracking."""
    if self._laser_on:
        self._laser_af.turn_off_laser(bypass_mode_gate=True)
        self._laser_on = False

def _cleanup(self) -> None:
    """Ensure laser is off - called on stop/crash."""
    self._turn_off_laser()
```

### wait_for_lock() - Critical Gate for Acquisition

This method is the integration point with acquisition. It must confirm both **correct position** AND **mechanical stability** before returning True.

```python
def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
    """Wait until focus is locked (position correct AND stable).

    This is the critical gate before imaging. Returns True ONLY when:
    1. Displacement is within offset_threshold_um of target (CORRECT)
    2. N consecutive readings confirm this (STABLE - accounts for settling/vibrations)

    The lock buffer handles mechanical realities:
    - Piezo settling time after moves
    - Stage vibrations from XY movement
    - Thermal drift
    - Any transient disturbances

    Acquisition should NOT proceed if this returns False.

    Args:
        timeout_s: Maximum time to wait for lock

    Returns:
        True if lock achieved (position verified correct and stable)
        False if timeout (position not stable or not correct)
    """
    if not self._running:
        return False

    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if self._status == "locked":
            return True  # N consecutive good readings confirmed
        if self._stop_event.wait(0.05):
            return False  # Stopped while waiting

    self._log.warning(f"wait_for_lock timed out after {timeout_s}s")
    return False
```

**Why N consecutive readings matter:**
- A single "good" reading could be noise
- Mechanical settling takes time (ms to tens of ms)
- N readings at 30Hz = N/30 seconds of confirmed stability
- Default N=5 means ~167ms of stable readings required

## Testing

```bash
cd software
pytest tests/unit/squid/backend/controllers/autofocus/test_continuous_focus_lock.py -v
```

## Completion Checklist

### LaserAutofocusController Updates
- [ ] Add `turn_on_laser(bypass_mode_gate=False)` method
- [ ] Add `turn_off_laser(bypass_mode_gate=False)` method

### Core Implementation
- [ ] Create `ContinuousFocusLockController` class
- [ ] Implement constructor with all dependencies
- [ ] Implement mode property and `set_mode()`
- [ ] Implement status property

### Control Logic
- [ ] Implement `_control_fn()` with **NEGATIVE feedback** (`-p_term * error_um`)
- [ ] Error calculation: `error = displacement - target`
- [ ] `is_good_reading` computed here (controller owns thresholds)
- [ ] Implement `_update_lock_buffer()` logic
- [ ] Implement `_control_loop()` thread with time compensation
- [ ] Implement piezo clamping to range

### Lifecycle
- [ ] Add `_stop_event = threading.Event()` for clean shutdown signaling
- [ ] Add `_paused: bool` flag
- [ ] Implement `start()` with laser ON and `_stop_event.clear()`
- [ ] Implement `stop()` with laser OFF and `_stop_event.set()`
- [ ] Implement `pause()` with laser OFF
- [ ] Implement `resume()` with laser ON (check `_running` first - see Chunk 9)
- [ ] Implement `wait_for_lock(timeout_s)` using `_stop_event.wait()` for responsive cancellation

### Laser Safety
- [ ] Add `_laser_on` state tracking
- [ ] Use `laser_af.turn_on_laser(bypass_mode_gate=True)`
- [ ] Use `laser_af.turn_off_laser(bypass_mode_gate=True)`
- [ ] Implement `_cleanup()` in finally block

### Event Publishing
- [ ] Publish `FocusLockStatusChanged` **only on transitions**
- [ ] Publish `FocusLockMetricsUpdated` at **throttled rate**
- [ ] Publish `FocusLockWarning` with debounce
- [ ] Publish `FocusLockModeChanged` on mode change

### EventBus Subscriptions

**UI Commands** (handled in this chunk):
- [ ] Subscribe to `SetFocusLockModeCommand`
- [ ] Subscribe to `StartFocusLockCommand`
- [ ] Subscribe to `StopFocusLockCommand`
- [ ] Subscribe to `PauseFocusLockCommand`
- [ ] Subscribe to `ResumeFocusLockCommand`

**Backend Events** (handled in later chunks):
- `ObjectiveChanged` → Chunk 10 (invalidate reference)
- `AcquisitionStarted`/`AcquisitionFinished` → Chunk 9 (auto_lock mode)

### Export
- [ ] Export from `autofocus/__init__.py`
- [ ] Re-export from `controllers/__init__.py`

### Preview Handler (Optional - for Chunk 11)
- [ ] Add `_preview_handler: Optional[FocusLockStreamHandler] = None`
- [ ] Add `set_preview_handler()` method
- [ ] Push frames to handler in control loop (when set and enabled)
- [ ] Note: Backend only - no Qt dependencies

### Testing
- [ ] Unit test: Control function returns negative values
- [ ] Unit test: Lock buffer transitions
- [ ] Unit test: Status transitions
- [ ] Unit test: Laser state tracking
- [ ] Integration test: Start/stop in simulation

### Verification
- [ ] Controller can be instantiated
- [ ] Controller runs in simulation mode
- [ ] Laser lifecycle correct (on at start, off at stop)
- [ ] Events published correctly (throttled)
