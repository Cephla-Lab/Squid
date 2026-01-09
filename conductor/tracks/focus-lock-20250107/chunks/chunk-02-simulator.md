# Chunk 2: Focus Lock Simulator

## Goal

Create a simulator that generates synthetic focus lock behavior for UI development and testing. This allows iterating on UI design without real hardware.

## Dependencies

- Chunk 1 (Events and Configuration)

## Files to Create

| File | Purpose |
|------|---------|
| `software/src/squid/backend/controllers/autofocus/focus_lock_simulator.py` | Simulator class |
| `software/tests/unit/squid/backend/controllers/autofocus/test_focus_lock_simulator.py` | Unit tests |

## Deliverables

### FocusLockSimulator Class

```python
class FocusLockSimulator:
    """Simulates focus lock behavior for UI development and testing.

    NOTE: Does NOT publish frame events - preview is handled separately
    via QtStreamHandler if needed.
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: FocusLockConfig = None,
        noise_level_um: float = 0.1,
        drift_rate_um_per_s: float = 0.05,
        lock_acquisition_time_s: float = 2.0,
        snr_range: tuple[float, float] = (8.0, 15.0),
    ):
        self._config = config or FocusLockConfig()
        self._buffer_length = self._config.buffer_length  # Use config, not hardcoded 5
        ...

    def start(self) -> None:
        """Start the simulator loop."""

    def stop(self) -> None:
        """Stop the simulator loop."""

    @property
    def mode(self) -> str:
        """Current mode: off | always_on | auto_lock"""

    @property
    def is_running(self) -> bool:
        """Whether the lock loop is active."""

    @property
    def status(self) -> str:
        """Current status: disabled | searching | locked | lost | paused"""

    def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
        """Wait for lock to be achieved.

        Returns True when status is "locked".
        Returns False when not running (matches real controller).
        """
```

### Time-Compensated Loop

**Note**: Simulator uses `loop_rate_hz` (not just `metrics_rate_hz`) to simulate control loop timing if testing timing-sensitive behavior. For simple UI development, metrics_rate_hz is sufficient.

```python
def _simulation_loop(self) -> None:
    """Main simulation loop with time compensation."""
    # Use loop_rate_hz for simulation timing (same as real controller)
    period = 1.0 / self._config.loop_rate_hz
    metrics_period = 1.0 / self._config.metrics_rate_hz
    last_metrics_time = 0.0

    while self._running:
        start = time.monotonic()

        # Simulate control loop step
        self._simulate_step()

        # Throttled metrics publishing (not every loop iteration)
        now = time.monotonic()
        if now - last_metrics_time >= metrics_period:
            self._publish_metrics()
            last_metrics_time = now

        # Time-compensated sleep to maintain rate
        elapsed = time.monotonic() - start
        sleep_time = max(0, period - elapsed)
        time.sleep(sleep_time)
```

### Simulated Behaviors

1. **Lock Acquisition**
   - On start: status = "searching"
   - Gradual buffer fill using `config.buffer_length`
   - After lock achieved: status = "locked"

2. **Continuous Metrics** (~10 Hz via config.metrics_rate_hz)
   - `z_position_um`: Simulated piezo position (100-200 μm range)
   - `z_error_um`: Noise around 0 ± `noise_level_um`
   - `spot_snr`: Random in `snr_range`
   - `z_error_rms_um`: Computed from recent history
   - `drift_rate_um_per_s`: Slow drift simulation

3. **Lock Loss Simulation**
   - Occasionally simulate lock loss (status = "lost")
   - Auto-recover after brief period

4. **Piezo Warnings**
   - When position approaches limits, publish `FocusLockWarning`

5. **Command Response**
   - Subscribe to `SetFocusLockModeCommand`
- Subscribe to `StartFocusLockCommand`, `StopFocusLockCommand`
- Subscribe to `PauseFocusLockCommand`, `ResumeFocusLockCommand`
- Subscribe to `AdjustFocusLockTargetCommand` (nudges lock target by delta)

### Event Publishing

```python
# Status changes (on transitions only)
FocusLockStatusChanged(
    is_locked=True,
    status="locked",
    lock_buffer_fill=5,
    lock_buffer_length=self._config.buffer_length
)

# Metrics (at config.metrics_rate_hz)
FocusLockMetricsUpdated(
    z_error_um=0.05,
    z_position_um=150.0,
    spot_snr=12.3,
    spot_intensity=45000.0,
    z_error_rms_um=0.08,
    drift_rate_um_per_s=0.03,
    is_good_reading=True,    # Validity flag for UI
    correlation=0.95,        # Simulated correlation (or NaN if N/A)
)

# Warnings (debounced)
FocusLockWarning(warning_type="piezo_high", message="Piezo approaching upper limit")
```

## Interface Compatibility

The simulator must implement the same public interface as `ContinuousFocusLockController` so they can be used interchangeably. Both should implement:

```python
# Optional: Define as Protocol for type checking (not enforced at runtime)
# This Protocol is useful for documentation and IDE support but not required.
# If you don't use it anywhere, skip it to reduce surface area.
class FocusLockInterface(Protocol):
    """Common interface for focus lock implementations."""

    @property
    def mode(self) -> str: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def status(self) -> str: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def shutdown(self) -> None: ...
    def set_mode(self, mode: str) -> None: ...

    # Fine adjust support
    def adjust_target(self, delta_um: float) -> None: ...

    # Acquisition integration
    def wait_for_lock(self, timeout_s: float = 5.0) -> bool: ...

    # Optional preview support (Chunk 11)
    def set_preview_handler(self, handler) -> None: ...
```

**Note**: The simulator DOES need `wait_for_lock()` because multipoint acquisition can run in simulation mode.

**Behavior parity**: The simulator returns `False` when not running, matching the real controller. This ensures consistent behavior in tests. The caller (MultiPointWorker) should check `is_running` before calling `wait_for_lock()` if it wants to proceed without lock.

## Activation

The simulator is used automatically in `--simulation` mode when no real laser AF hardware is present:

```python
# In application.py (Chunk 8)
if laser_autofocus is not None and piezo_service is not None:
    # Real hardware available
    continuous_focus_lock = ContinuousFocusLockController(...)
elif simulation_mode:
    # No hardware, but simulation mode - use simulator for UI development
    continuous_focus_lock = FocusLockSimulator(event_bus, FocusLockConfig())
else:
    # No hardware, not simulation - no focus lock
    continuous_focus_lock = None
```

## Testing

```bash
cd software
pytest tests/unit/squid/backend/controllers/autofocus/test_focus_lock_simulator.py -v

# Manual test
python -c "
from squid.core.events import EventBus
from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator
import time

bus = EventBus()
sim = FocusLockSimulator(bus)
sim.start()
time.sleep(5)
sim.stop()
print('Simulator test passed')
"
```

## Completion Checklist

### Core Implementation
- [ ] Create `FocusLockSimulator` class
- [ ] Implement `start()` / `stop()` lifecycle
- [ ] Implement mode property and switching
- [ ] Implement background thread for event publishing
- [ ] Use `config.buffer_length` (NOT hardcoded 5)
- [ ] Time-compensated loop (`sleep = period - elapsed`)

### Simulated Behaviors
- [ ] Lock acquisition simulation (searching → locked)
- [ ] Lock buffer fill progression (0/N → N/N using config)
- [ ] Continuous metrics publishing at `config.metrics_rate_hz`
- [ ] Z position simulation with noise
- [ ] Z error simulation with noise
- [ ] Z error RMS calculation
- [ ] SNR simulation in configured range
- [ ] Drift rate simulation
- [ ] Occasional lock loss simulation
- [ ] Piezo range warning simulation

### Command Handling
- [ ] Subscribe to `SetFocusLockModeCommand`
- [ ] Subscribe to `StartFocusLockCommand`
- [ ] Subscribe to `StopFocusLockCommand`
- [ ] Subscribe to `PauseFocusLockCommand`
- [ ] Subscribe to `ResumeFocusLockCommand`
- [ ] Subscribe to `AdjustFocusLockTargetCommand`
- [ ] Mode changes publish `FocusLockModeChanged`

### Interface Compatibility
- [ ] Implement same public interface as `ContinuousFocusLockController`
- [ ] Include `shutdown()` method
- [ ] Include `wait_for_lock(timeout_s)` - return True when status is "locked", or True immediately if not running
- [ ] Include `set_preview_handler()` (can be no-op for simulator)
- [ ] Include `adjust_target(delta_um)` for fine adjustments
- [ ] Use `loop_rate_hz` for loop timing, not just `metrics_rate_hz`

### NO Frame Events
- [ ] Do NOT publish `FocusLockFrameUpdated` (would overwhelm EventBus)

### Testing
- [ ] Unit test: Simulator starts and publishes events
- [ ] Unit test: Simulator responds to mode commands
- [ ] Unit test: Lock acquisition timing
- [ ] Unit test: Metrics publishing rate
- [ ] All existing tests still pass

### Verification
- [ ] Simulator can run for 30+ seconds without errors
- [ ] Events visible in event bus (can log/print)
- [ ] Mode switching works correctly
- [ ] Clean shutdown (no orphan threads)
