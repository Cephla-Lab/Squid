# Continuous Focus Lock Feature - Implementation Plan

## Overview

Implement a continuous focus lock feature that monitors and corrects focus drift in real-time using the existing laser-based autofocus system. Design is informed by the storm-control focusLock implementation.

## User Requirements

1. Floating widget showing focus camera output **all the time**
2. Display current displacement value
3. Lock button to lock focus to current z-plane
4. Continuous closed-loop focus correction in background when locked
5. During z-stack acquisition:
   - Ensure focus is correct at start
   - Release the lock (pause)
   - Execute z-stack
   - Re-lock after z-stack completes

---

## Reference: Storm-Control FocusLock

**Key patterns from `/Users/wea/src/allenlab/storm-control/storm_control/hal4000/focusLock/`:**

1. **MVC Architecture**: `FocusLockView` (UI) + `LockControl` (business logic) + `LockModes` (state machine)
2. **Continuous QPD polling**: Event-driven chain where each reading triggers the next
3. **Exponential proportional control**: Non-linear gain for smooth corrections
4. **Lock quality buffer**: Circular buffer (5 readings) to determine "good lock" status
5. **Lock modes with behaviors**: Modes contain behaviors (idle, locked, scan, find_sum)
6. **Diagnostic logging**: Offset files + TIFF stacks during acquisition

---

## Current Squid System

**Laser Autofocus Controller** (`software/control/core/laser_auto_focus_controller.py`):
- Uses dedicated focus camera to track reflected laser spot
- Key methods: `measure_displacement()`, `move_to_target()`, `set_reference()`
- Emits signals: `image_to_display`, `signal_displacement_um`, `signal_cross_correlation`
- Currently only runs on-demand (not continuously)

**Z-Stack Acquisition** (`software/control/core/multi_point_worker.py`):
- `perform_autofocus()` runs before z-stack (lines 504-535)
- `acquire_at_position()` loops through z-levels (lines 424-497)
- `move_z_back_after_stack()` returns to start position (lines 884-898)

---

## Implementation Plan

### 1. Create FocusLockController

**File**: `software/control/core/focus_lock_controller.py` (NEW)

**State Machine** (inspired by storm-control LockModes):
```
IDLE (monitoring only)
  ↓ lock()
LOCKED (correcting)
  ↓ pause()        ↓ error
PAUSED ←──────→ ERROR
  ↓ resume()       ↓ recover()
LOCKED ←──────────┘
```

**Key Components**:
```python
class FocusLockState(Enum):
    IDLE = auto()      # Monitoring but not correcting
    LOCKED = auto()    # Actively correcting
    PAUSED = auto()    # Temporarily paused (z-stack)
    ERROR = auto()     # Lost focus

class FocusLockController(QObject):
    # Signals
    signal_state_changed = Signal(FocusLockState)
    signal_displacement_update = Signal(float)      # Current displacement
    signal_image_update = Signal(np.ndarray)        # Focus camera image
    signal_good_lock = Signal(bool)                 # Lock quality indicator
    signal_correction_applied = Signal(float)       # Correction amount
    signal_error = Signal(str)

    # Methods
    def start(self)           # Start continuous monitoring
    def stop(self)            # Stop monitoring
    def lock(self) -> bool    # Lock to current z-plane
    def unlock(self)          # Release lock (return to IDLE)
    def pause(self)           # Pause for z-stack
    def resume(self) -> bool  # Resume after z-stack
```

**Exponential Proportional Control** (from storm-control):
```python
def control_fn(self, offset: float) -> float:
    """Non-linear gain: gentle near target, aggressive far away"""
    dx = offset * offset / 0.5  # Normalize
    p_term = self.max_gain - self.scale * math.exp(-dx)
    return -1.0 * p_term * offset
```

**Lock Quality Buffer** (from storm-control):
```python
# Circular buffer of recent lock quality (1=good, 0=bad)
self._quality_buffer = np.zeros(5, dtype=int)
self._quality_index = 0

def _update_quality(self, is_good: bool):
    self._quality_buffer[self._quality_index] = 1 if is_good else 0
    self._quality_index = (self._quality_index + 1) % len(self._quality_buffer)
    good_lock = np.sum(self._quality_buffer) == len(self._quality_buffer)
    self.signal_good_lock.emit(good_lock)
```

**Configuration Parameters**:
- `offset_threshold_um`: Max deviation to be "good" (default: 0.5 um)
- `min_sum_threshold`: Minimum QPD sum signal required
- `update_interval_ms`: Monitoring frequency (default: 50ms for responsive control)
- `max_correction_um`: Maximum single correction (default: 10 um)
- `max_gain`: Maximum control gain (default: 0.7)
- `quality_buffer_length`: Frames for "good lock" (default: 5)

---

### 2. Create FocusLockWidget

**File**: `software/control/widgets_focus_lock.py` (NEW)

**UI Layout** (inspired by storm-control's LockDisplay):
```
┌──────────────────────────────────────────┐
│ Focus Lock                             X │
├──────────────────────────────────────────┤
│ ┌────────────────────┐ ┌───┐ ┌───┐ ┌───┐ │
│ │                    │ │   │ │   │ │   │ │
│ │  Focus Camera      │ │ O │ │ S │ │ Z │ │
│ │  Image             │ │ f │ │ u │ │   │ │
│ │  (with spot        │ │ f │ │ m │ │ P │ │
│ │   overlay)         │ │ s │ │   │ │ o │ │
│ │                    │ │ e │ │   │ │ s │ │
│ │                    │ │ t │ │   │ │   │ │
│ └────────────────────┘ └───┘ └───┘ └───┘ │
├──────────────────────────────────────────┤
│ State: [■ LOCKED - GOOD]                 │
│ Displacement: 0.123 um  Error: 0.023 um  │
├──────────────────────────────────────────┤
│ ┌──────────────────────────────────────┐ │
│ │           Lock Focus                 │ │
│ └──────────────────────────────────────┘ │
├──────────────────────────────────────────┤
│ Threshold: [0.50] um  Gain: [0.70]       │
└──────────────────────────────────────────┘
```

**Display Components** (from storm-control):
1. **Focus camera image**: Shows laser spot with optional overlay
2. **Offset bar**: Vertical bar showing displacement from target
3. **Sum bar**: Signal strength indicator
4. **Z position bar**: Current z position (clickable to adjust)

**Features**:
- Floating window (`Qt.WindowStaysOnTopHint`)
- Real-time image display via `pyqtgraph.ImageView`
- Color-coded state: Gray (IDLE), Green (LOCKED-GOOD), Yellow (LOCKED-BAD), Orange (PAUSED), Red (ERROR)
- Toggle lock button with visual feedback
- Vertical bar displays for offset/sum/z-position

---

### 3. Integrate with MultiPointWorker

**File**: `software/control/core/multi_point_worker.py` (MODIFY)

**Changes to `acquire_at_position()` (around line 424)**:

```python
def acquire_at_position(self, region_id, current_path, fov):
    # NEW: Pause focus lock if active
    was_focus_locked = False
    if self.focus_lock_controller:
        if self.focus_lock_controller.state == FocusLockState.LOCKED:
            was_focus_locked = True
            self.focus_lock_controller.pause()

    try:
        # Existing autofocus call
        self.perform_autofocus(region_id, fov)

        # Existing z-stack acquisition
        if self.NZ > 1:
            self.prepare_z_stack()

        for z_level in range(self.NZ):
            # ... existing acquisition code ...

        if self.NZ > 1:
            self.move_z_back_after_stack()

    finally:
        # NEW: Resume focus lock if it was active
        if was_focus_locked and self.focus_lock_controller:
            self.focus_lock_controller.resume()
```

**Pass controller through chain**:
- `MultiPointController.__init__()` accepts `focus_lock_controller`
- `MultiPointWorker.__init__()` accepts `focus_lock_controller`

---

### 4. Integrate with GUI

**File**: `software/control/gui_hcs.py` (MODIFY)

**In `__init__()` (after laser AF controller creation)**:
```python
# Create focus lock controller and widget
if SUPPORT_LASER_AUTOFOCUS and self.laserAutofocusController:
    self.focusLockController = FocusLockController(
        laser_af_controller=self.laserAutofocusController,
        stage=self.stage,
        piezo=self.piezo,
    )
    self.focusLockWidget = FocusLockWidget(
        focus_lock_controller=self.focusLockController,
    )
```

**Add button to show widget** (in laser AF section):
```python
self.btn_show_focus_lock = QPushButton("Focus Lock")
self.btn_show_focus_lock.clicked.connect(self.focusLockWidget.show)
```

---

## Files to Create

| File | Purpose |
|------|---------|
| `software/control/core/focus_lock_controller.py` | Core controller with state machine, monitoring loop |
| `software/control/widgets_focus_lock.py` | Floating widget with image display, controls |

## Files to Modify

| File | Changes |
|------|---------|
| `software/control/core/multi_point_worker.py` | Add pause/resume around z-stack acquisition |
| `software/control/core/multi_point_controller.py` | Pass focus_lock_controller to worker |
| `software/control/gui_hcs.py` | Initialize controller/widget, add show button |

---

## Threading Strategy

1. **Monitoring Loop**: `threading.Timer` (daemon thread, 50ms interval)
2. **GUI Updates**: Qt signals ensure thread-safe UI updates
3. **State Protection**: `threading.Lock` for state transitions
4. **Camera Access**: Piggyback on existing `LaserAutofocusController` methods which handle camera coordination

---

## Implementation Order

1. **FocusLockController** - Core logic first, can be tested independently
2. **FocusLockWidget** - UI once controller works
3. **GUI integration** - Wire up button and initialization
4. **MultiPointWorker integration** - Pause/resume during z-stacks
5. **Testing** - Manual testing with hardware/simulation

---

## Design Decisions

1. **Widget Type**: Floating window (stays on top, can be positioned anywhere)
2. **Z Actuator**: Piezo preferred when available, fall back to stage motor
3. **Live Behavior**: Always active - keep correcting focus even during live view
4. **Control Algorithm**: Exponential proportional control (from storm-control)
5. **Lock Quality**: Circular buffer to determine "good lock" status

---

## Reference Files

**Storm-Control FocusLock** (for reference during implementation):
- `/Users/wea/src/allenlab/storm-control/storm_control/hal4000/focusLock/focusLock.py` - Main module
- `/Users/wea/src/allenlab/storm-control/storm_control/hal4000/focusLock/lockControl.py` - Business logic
- `/Users/wea/src/allenlab/storm-control/storm_control/hal4000/focusLock/lockModes.py` - State machine
- `/Users/wea/src/allenlab/storm-control/storm_control/hal4000/focusLock/lockDisplay.py` - UI widgets
