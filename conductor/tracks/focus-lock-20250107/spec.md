# Continuous Focus Lock System

## Overview

Implement a continuous closed-loop focus lock system that maintains sample focus during long acquisitions. The system uses the existing laser autofocus (reflection-based AF) hardware with a piezo Z stage for fast corrections.

### Key Requirements

1. **Backwards Compatibility**: Users can switch between current single-shot AF mode and new continuous mode
2. **Simultaneous Viewing**: Focus lock status visible alongside live imaging (not separate tab)
3. **Piezo-Only**: System uses piezo stage only (no motorized stage Z fallback)
4. **Real-Time Feedback**: Visual indicators for lock status, error, and signal quality

## Architecture

### Mode-Based Design

The system supports three operational modes, selectable via UI:

| Mode | Behavior | Use Case |
|------|----------|----------|
| **Off** | Single-shot AF only (current behavior) | Manual focus, setup |
| **Always On** | Continuous lock, user toggleable | Live viewing with drift compensation |
| **Auto Lock** | Lock active only during acquisition | Long acquisitions with minimal intervention |

### System Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ContinuousFocusLockController               │
│  - Mode management (off/always_on/auto_lock)                        │
│  - Control loop thread                                              │
│  - Gain-scheduled correction                                        │
│  - Lock quality tracking                                            │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ LaserAutofocus  │ │  PiezoService   │ │    EventBus     │
│   Controller    │ │                 │ │                 │
│ - Displacement  │ │ - move_to_fast()│ │ - Status events │
│   measurement   │ │ - get_position()│ │ - Metrics events│
│ - Spot detection│ │ - get_range()   │ │ - Warning events│
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Control Algorithm

### Gain-Scheduled Proportional Control

Inspired by storm-control's `controlFn`, uses adaptive gain based on error magnitude:

```python
def control_fn(error_um: float) -> float:
    """
    Gain scheduling: aggressive when far from target, gentle when close.

    - Near target (< 0.5 μm): Use base gain for stability
    - Far from target: Use max gain for fast recovery
    """
    sigma = 0.5  # μm, transition width
    dx = error_um ** 2 / sigma
    scale = lock_gain_max - lock_gain
    p_term = lock_gain_max - scale * math.exp(-dx)
    return p_term * error_um
```

**Parameters:**
- `lock_gain`: Base gain near target (default: 0.5)
- `lock_gain_max`: Maximum gain far from target (default: 0.7)

### Lock Quality Assessment

Uses buffer-based quality tracking (from storm-control):

```python
# Lock is "good" only after N consecutive good readings
lock_buffer = [0, 1, 1, 1, 1]  # Last 5 readings
is_locked = sum(lock_buffer) == len(lock_buffer)  # All must be good
```

**Parameters:**
- `lock_buffer_length`: Readings required for lock (default: 5)
- `offset_threshold_um`: Max error to count as "good" (default: 0.5 μm)
- `minimum_spot_snr`: Min SNR to trust reading (default: 5.0)

## Quality Metrics

### Core Metrics (per reading)

| Metric | Type | Description |
|--------|------|-------------|
| `z_error_um` | float | Displacement from target |
| `z_position_um` | float | Current piezo position |
| `correlation` | float | Cross-correlation with reference (0-1) |
| `is_good_reading` | bool | This reading is valid |

### Signal Quality Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `spot_intensity` | float | Peak intensity of AF spot |
| `spot_snr` | float | Signal-to-noise ratio |
| `spot_width_px` | float | Gaussian sigma (focus indicator) |
| `background_level` | float | Background intensity |

### Lock Quality Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `lock_buffer_fill` | int | Good readings in buffer (e.g., 4/5) |
| `z_error_rms_um` | float | RMS error over recent history |
| `z_drift_rate_um_per_s` | float | Estimated drift rate |

### Warning Thresholds

| Warning | Threshold | Action |
|---------|-----------|--------|
| Low spot SNR | < 5.0 | Skip correction, mark reading invalid |
| Defocused spot | width > 15 px | Warning indicator |
| Piezo near low limit | < 20 μm | Yellow warning |
| Piezo near high limit | > 280 μm | Yellow warning |
| Lock lost | 0/5 good readings | Red status indicator |

## User Interface

### Dockable Focus Lock Status Widget

A compact, always-visible panel that docks alongside any image display tab:

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Live View] [Mosaic] [Multichannel]                                │
├─────────────────────────────────────────────────────────────┬───────┤
│                                                             │ Focus │
│                                                             │ Lock  │
│                   Main Image Display                        │┌─────┐│
│                                                             ││ AF  ││
│                                                             ││Cam  ││
│                                                             │└─────┘│
│                                                             │● LOCK │
│                                                             │Z:150μm│
│                                                             │Err:0.0│
│                                                             │▃▅▇▇▇ │
│                                                             │[Mode] │
└─────────────────────────────────────────────────────────────┴───────┘
```

### Widget Components

1. **AF Camera Preview** (small, ~150x100 px)
   - Live feed from focus camera
   - Spot position overlay (crosshair or circle)

2. **Lock Status Indicator**
   - Green LED: Locked (N/N good readings)
   - Yellow LED: Searching (partial buffer fill)
   - Red LED: Lost (0/N good or signal invalid)
   - Gray LED: Off/Disabled

3. **Numeric Displays**
   - Z Position: Current piezo position in μm
   - Z Error: Current displacement from target
   - Signal: Spot SNR or intensity

4. **Visual Bars**
   - Error bar: Vertical bar showing offset from center
   - Quality bar: Signal quality (green/yellow/red zones)
   - Piezo range bar: Position within available range

5. **Mode Selector**
   - Dropdown or segmented control: Off | Always On | Auto Lock
   - Lock/Unlock toggle button (for Always On mode)

6. **Fine Adjust Controls**
   - Step size input (μm)
   - Up/Down buttons to shift the lock target by the step

7. **Collapse/Expand**
   - Collapsed: Just status LED + Z position
   - Expanded: Full panel with all metrics

### Behavior

- **Persists across tabs**: Widget visible on Live View, Mosaic, etc.
- **Collapsible**: User can minimize to status-only
- **Floatable**: Can be popped out as independent window
- **Updates at ~10 Hz**: Throttled to avoid UI overhead

## Acquisition Integration

### MultiPoint Acquisition

```python
# In multi_point_worker.py
def perform_autofocus(self, region_id: str, fov: int) -> bool:
    if self.do_reflection_af:
        if self.focus_lock_controller.mode == "auto_lock":
            # Continuous mode: wait for lock to stabilize
            return self.focus_lock_controller.wait_for_lock(timeout_s=5.0)
        else:
            # Single-shot mode: existing behavior
            return self.laser_auto_focus_controller.move_to_target(0)
```

### Z-Stack Behavior

During Z-stacks, focus lock should pause:

```python
def acquire_z_stack(self, ...):
    # Pause focus lock during Z-stack
    was_locked = self.focus_lock_controller.is_running
    if was_locked:
        self.focus_lock_controller.pause()

    try:
        # Perform Z-stack acquisition
        for z_offset in z_offsets:
            self.piezo_service.move_to(base_z + z_offset)
            self.capture_frame()
    finally:
        # Resume focus lock
        if was_locked:
            self.focus_lock_controller.resume()
```

## Events

### New Events

```python
@dataclass(frozen=True)
class FocusLockModeChanged(Event):
    """User changed focus lock mode."""
    mode: str  # "off" | "always_on" | "auto_lock"

@dataclass(frozen=True)
class FocusLockStatusChanged(Event):
    """Lock status changed."""
    is_locked: bool
    status: str  # "locked" | "searching" | "lost" | "disabled" | "paused"
    lock_buffer_fill: int
    lock_buffer_length: int

@dataclass(frozen=True)
class FocusLockMetricsUpdated(Event):
    """Real-time metrics update (~10 Hz)."""
    z_error_um: float
    z_position_um: float
    spot_snr: float
    spot_intensity: float
    is_good_reading: bool
    z_error_rms_um: float
    drift_rate_um_per_s: float

@dataclass(frozen=True)
class FocusLockWarning(Event):
    """Warning condition detected."""
    warning_type: str  # "piezo_low" | "piezo_high" | "signal_lost" | "snr_low"
    message: str

@dataclass(frozen=True)
class FocusLockFrameUpdated(Event):
    """Cropped AF spot region for widget preview (~10 Hz)."""
    frame: np.ndarray  # Small cropped region around spot
    spot_x_px: float  # Centroid x in cropped frame
    spot_y_px: float  # Centroid y in cropped frame
    correlation: float  # Correlation vs reference (0-1)

# Commands
@dataclass(frozen=True)
class SetFocusLockModeCommand(Event):
    """Command to change focus lock mode."""
    mode: str

@dataclass(frozen=True)
class StartFocusLockCommand(Event):
    """Command to start focus lock (for Always On mode)."""
    target_um: float = 0.0

@dataclass(frozen=True)
class StopFocusLockCommand(Event):
    """Command to stop focus lock."""
    pass

@dataclass(frozen=True)
class PauseFocusLockCommand(Event):
    """Command to pause focus lock (e.g., during Z-stack)."""
    pass

@dataclass(frozen=True)
class ResumeFocusLockCommand(Event):
    """Command to resume focus lock after pause."""
    pass

@dataclass(frozen=True)
class AdjustFocusLockTargetCommand(Event):
    """Command to adjust focus lock target by a relative offset."""
    delta_um: float
```

## Piezo-Only Considerations

Since the system uses piezo only (no stage Z):

### Range Management

- **Typical range**: 0-300 μm
- **Safe operating range**: 20-280 μm (with margin)
- **Warning zones**: 0-20 μm and 280-300 μm

### Recovery Strategies

When piezo approaches limits:

1. **Warning**: Yellow indicator when within 20 μm of limit
2. **Soft limit**: Reduce correction gain near limits
3. **User notification**: "Piezo near limit - sample may have drifted"

No automatic recovery scan (would require stage Z). User must manually recenter piezo and re-establish reference if focus drifts too far.

### Re-Home Procedure

Manual procedure when piezo is at limit:
1. Pause focus lock
2. Move piezo to center (150 μm)
3. Manually adjust sample position
4. Set new reference
5. Resume focus lock

## Configuration

### Default Parameters

```ini
[laser_autofocus]
# Existing parameters...

# Focus lock parameters
focus_lock_gain = 0.5
focus_lock_gain_max = 0.7
focus_lock_buffer_length = 5
focus_lock_offset_threshold_um = 0.5
focus_lock_min_spot_snr = 5.0
focus_lock_loop_rate_hz = 30
focus_lock_metrics_rate_hz = 10

# Piezo warnings
focus_lock_piezo_warning_margin_um = 20.0
```

## Testing Strategy

### Unit Tests

- Control function gain scheduling
- Lock buffer state machine
- Metrics calculation (SNR, RMS, drift rate)
- Piezo range checking

### Integration Tests (Simulation)

- Mode switching (off → always_on → auto_lock)
- Lock acquisition and loss detection
- Acquisition integration (pause during Z-stack)
- Event publishing

### Manual Testing

- Lock stability over time (>30 min)
- Response to sample perturbation
- UI responsiveness
- Piezo range warning behavior

## Dependencies

- Existing `LaserAutofocusController` for displacement measurement
- Existing `PiezoService` for Z control
- Existing `EventBus` for communication
- PyQt5 for UI widgets
- pyqtgraph for real-time plots (optional)

## References

- storm-control focusLock implementation (Zhuang Lab, Harvard)
- Previous Squid tracks: `focus_lock_20251226`, `continuous_focus_20251230`
