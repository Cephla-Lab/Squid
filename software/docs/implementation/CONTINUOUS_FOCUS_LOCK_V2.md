# Continuous Focus Lock - Implementation Plan V2

## Overview

Implement continuous focus lock for the Squid microscopy software. The feature provides real-time focus correction using the laser autofocus system, with a floating widget for monitoring and control during live imaging and tiled acquisitions.

## Architecture Decision

**Extend `LaserAutofocusController`** with focus lock capabilities (not a separate controller). This leverages existing hardware access (camera, piezo, laser) and signals without duplication.

---

## Requirements Summary

| Requirement | Implementation |
|-------------|----------------|
| Continuous visualization | Camera image + centroid marker + fit quality indicator |
| Background operation | Daemon thread with 20Hz control loop |
| Physical units display | Displacement bar, Z position bar, out-of-range warning |
| Lock quality indicator | Quality bar with color coding (green/yellow/red) |
| Lock position control | Offset field + step buttons; piezo moves then recalibrates |
| Error handling | Soft warnings only; visual indicators at range limits |
| Engage/disengage | Toggle button in widget |
| Tiled acquisition | Keep lock during XY, pause for z-stack, verify on arrival |
| Floating widget | pyqtgraph.dockarea dock, can float independently |
| Piezo init | Move to middle of range when widget opens |

**Control Algorithms**: Implement all three (user-selectable):
1. Simple proportional + clamp
2. Exponential proportional (storm-control style)
3. PID with anti-windup

---

## State Machine

```
IDLE (monitoring only) --lock()--> LOCKED (correcting)
                                        |
                          pause() ------|-----> PAUSED (z-stack)
                                        |           |
                          error --------|-----> ERROR (signal lost)
                                        |           |
                          <-- resume() --           |
                          <-- recover() ------------+
```

---

## Files to Create

### 1. `software/control/core/focus_lock_algorithms.py` (NEW)

Control algorithm implementations:

```python
class ControlAlgorithm(ABC):
    def calculate_correction(self, error_um: float, dt_s: float) -> float: ...
    def reset(self) -> None: ...

class ProportionalController(ControlAlgorithm): ...
class ExponentialController(ControlAlgorithm): ...
class PIDController(ControlAlgorithm): ...

# Parameter dataclasses
@dataclass
class ProportionalParams: gain, deadband_um, max_correction_um
@dataclass
class ExponentialParams: max_gain, min_gain, scale, deadband_um, max_correction_um
@dataclass
class PIDParams: kp, ki, kd, integral_limit, deadband_um, max_correction_um
```

### 2. `software/control/widgets/hardware/focus_lock.py` (NEW)

Widget class with:
- `pyqtgraph.ImageView` for focus camera + centroid overlay
- Custom `DisplacementBar` and `PiezoPositionBar` widgets
- Lock/unlock button, offset controls, step buttons
- Collapsible advanced settings panel (algorithm selection, gains)
- Signals: `signal_lock_requested`, `signal_unlock_requested`, `signal_offset_set`, etc.

---

## Files to Modify

### 1. `software/control/core/laser_auto_focus_controller.py`

Add focus lock state and methods:

```python
# New imports
from squid.utils.thread_safe_state import ThreadSafeFlag, ThreadSafeValue
from control.core.focus_lock_algorithms import ControlAlgorithm, ProportionalController

# New enum
class FocusLockState(Enum):
    IDLE = auto()
    LOCKED = auto()
    PAUSED = auto()
    ERROR = auto()

# New signals
signal_focus_lock_state = Signal(object)  # FocusLockState
signal_focus_lock_update = Signal(float, float, float, bool)  # displacement, error, quality, good_lock
signal_focus_lock_warning = Signal(str)

# New state variables in __init__
self._focus_lock_running = ThreadSafeFlag(initial=False)
self._focus_lock_paused = ThreadSafeFlag(initial=False)
self._focus_lock_state = ThreadSafeValue[FocusLockState](FocusLockState.IDLE)
self._focus_lock_thread: Optional[threading.Thread] = None
self._target_displacement_um = ThreadSafeValue[float](0.0)
self._control_algorithm: ControlAlgorithm = ProportionalController(...)
self._quality_buffer = np.zeros(5, dtype=float)  # Circular buffer

# New methods
def start_focus_lock(self, target_um: float = 0.0) -> bool
def stop_focus_lock(self) -> None
def pause_focus_lock(self) -> bool  # Returns True if was running
def resume_focus_lock(self) -> bool
def _focus_lock_loop(self) -> None  # Background thread entry point
def _focus_lock_iteration(self) -> None  # Single iteration
def apply_focus_lock_offset(self, offset_um: float) -> None  # Move piezo + recalibrate
def set_control_algorithm(self, algorithm: ControlAlgorithm) -> None
def verify_focus_lock(self, tolerance_um: float = 0.5) -> bool  # Check lock is within tolerance

# New properties
@property
def is_focus_locked(self) -> bool
@property
def focus_error_um(self) -> float
@property
def focus_lock_target_um(self) -> float
```

### 2. `software/control/core/multi_point_worker.py`

Integrate focus lock pause/resume around z-stacks:

```python
def acquire_at_position(self, region_id, current_path, fov):
    focus_lock_was_active = False

    if self.laser_auto_focus_controller:
        focus_lock_was_active = self.laser_auto_focus_controller.is_focus_locked

    # Verify lock is within tolerance at new FOV
    if focus_lock_was_active:
        if not self.laser_auto_focus_controller.verify_focus_lock(tolerance_um=0.5):
            self._log.warning("Focus lock outside tolerance at FOV")

    # Pause for z-stack
    if self.NZ > 1 and focus_lock_was_active:
        self.laser_auto_focus_controller.pause_focus_lock()

    # ... existing z-stack acquisition ...

    # Resume after z-stack
    if self.NZ > 1 and focus_lock_was_active:
        self.move_z_back_after_stack()
        self.laser_auto_focus_controller.resume_focus_lock()
```

### 3. `software/control/gui/widget_factory.py`

Add widget creation:

```python
def create_focus_lock_widget(gui: "HighContentScreeningGui") -> None:
    from control.widgets.hardware.focus_lock import FocusLockWidget

    piezo_range = gui.piezo.range_um if gui.piezo else 300.0
    gui.focusLockWidget = FocusLockWidget(
        focus_lock_controller=gui.laserAutofocusController,
        stream_handler_focus=gui.streamHandler_focus_camera,
        piezo_range_um=piezo_range,
    )
```

### 4. `software/control/gui/signal_connector.py`

Add signal connections:

```python
def connect_focus_lock_signals(gui: "HighContentScreeningGui") -> None:
    widget = gui.focusLockWidget
    controller = gui.laserAutofocusController

    # Widget -> Controller
    widget.signal_lock_requested.connect(controller.start_focus_lock)
    widget.signal_unlock_requested.connect(controller.stop_focus_lock)
    widget.signal_offset_set.connect(controller.apply_focus_lock_offset)
    widget.signal_parameters_changed.connect(controller.set_control_parameters)

    # Controller -> Widget
    controller.signal_focus_lock_state.connect(widget.update_state)
    controller.signal_focus_lock_update.connect(widget.update_displays)
    controller.image_to_display.connect(widget.update_focus_image)

    # Acquisition -> Widget (UI locking)
    gui.flexibleMultiPointWidget.signal_acquisition_started.connect(
        widget.on_acquisition_started
    )
```

### 5. `software/control/gui_hcs.py`

Add widget initialization and dock:

```python
# In widget creation section
create_focus_lock_widget(self)

# In layout section - create floating dock
if self.focusLockWidget:
    dock_focus_lock = dock.Dock("Focus Lock", autoOrientation=False)
    dock_focus_lock.showTitleBar()
    dock_focus_lock.addWidget(self.focusLockWidget)
    laserfocus_dockArea.addDock(dock_focus_lock, "bottom")

    # Move piezo to middle when widget is shown
    self.focusLockWidget.shown.connect(self._on_focus_lock_widget_shown)

def _on_focus_lock_widget_shown(self):
    if self.piezo:
        middle = self.piezo.range_um / 2
        self.piezo.move_to(middle)
```

### 6. `software/control/utils_config.py`

Add configuration:

```python
class FocusLockAlgorithm(str, Enum):
    PROPORTIONAL = "proportional"
    EXPONENTIAL = "exponential"
    PID = "pid"

class FocusLockConfig(BaseModel):
    algorithm: FocusLockAlgorithm = FocusLockAlgorithm.PROPORTIONAL

    # Proportional params
    proportional_gain: float = 0.5

    # Exponential params
    exponential_max_gain: float = 0.7
    exponential_min_gain: float = 0.1
    exponential_scale: float = 0.5

    # PID params
    pid_kp: float = 0.5
    pid_ki: float = 0.05
    pid_kd: float = 0.1
    pid_integral_limit: float = 5.0

    # Common params
    deadband_um: float = 0.1
    max_correction_um: float = 2.0
    loop_interval_ms: int = 50
    quality_threshold_um: float = 0.5
    range_warning_fraction: float = 0.8
```

---

## Widget Layout

```
+----------------------------------------------------------+
| Focus Lock                                          [X]  |
+----------------------------------------------------------+
| +------------------------+  +---------------------------+ |
| |                        |  | State: [LOCKED (Good)]    | |
| |   Focus Camera         |  |                           | |
| |   (centroid marker +   |  | Displacement: -0.12 um    | |
| |    quality ring)       |  | [====|====] -2...0...+2   | |
| |                        |  |                           | |
| |                        |  | Quality: 0.95             | |
| |                        |  | [##########] 0...1        | |
| |                        |  |                           | |
| +------------------------+  | Piezo Z: 152.3 um         | |
|                             | [#######|   ] 0...300     | |
+----------------------------------------------------------+
| [Lock/Unlock]  Offset: [____] um [Set] [-0.5] [+0.5]     |
+----------------------------------------------------------+
| > Advanced Settings...                                    |
|   Algorithm: [Proportional v]  Gain: [0.50]              |
|   Deadband: [0.10] um   Max Correction: [2.0] um         |
+----------------------------------------------------------+
```

---

## Implementation Order

### Phase 1: Control Algorithms (Day 1)
1. Create `focus_lock_algorithms.py` with all three controllers
2. Add unit tests for each algorithm

### Phase 2: Controller Extension (Days 2-3)
1. Add `FocusLockState` enum to `laser_auto_focus_controller.py`
2. Add state variables and signals
3. Implement `start_focus_lock()`, `stop_focus_lock()`
4. Implement `_focus_lock_loop()`, `_focus_lock_iteration()`
5. Implement `pause_focus_lock()`, `resume_focus_lock()`
6. Implement `apply_focus_lock_offset()`, `verify_focus_lock()`
7. Add property accessors

### Phase 3: Widget Implementation (Days 4-5)
1. Create `focus_lock.py` with `FocusLockWidget`
2. Implement image display with centroid overlay
3. Implement displacement and quality bars
4. Implement offset controls and lock button
5. Implement advanced settings panel

### Phase 4: GUI Integration (Day 6)
1. Add widget factory function
2. Add signal connections
3. Add dock to `gui_hcs.py`
4. Add piezo initialization on widget show

### Phase 5: Acquisition Integration (Day 7)
1. Modify `multi_point_worker.py` for pause/resume
2. Add tolerance verification at FOVs
3. Test tiled acquisition workflow

### Phase 6: Configuration & Testing (Day 8)
1. Add `FocusLockConfig` to settings
2. Add settings persistence
3. Manual testing with hardware

---

## Critical Files

| File | Purpose |
|------|---------|
| `software/control/core/laser_auto_focus_controller.py` | Core controller to extend |
| `software/control/core/multi_point_worker.py` | Acquisition integration |
| `software/squid/utils/thread_safe_state.py` | Threading utilities |
| `software/control/widgets/hardware/laser_autofocus.py` | Pattern reference |
| `software/control/gui/signal_connector.py` | Signal wiring pattern |
| `software/control/gui_hcs.py` | Main GUI integration |

---

## Threading Model

```
Main Thread (GUI)              Focus Lock Thread (Daemon)
      |                               |
      | start_focus_lock()            |
      |------------------------------>|
      |                               | while _focus_lock_running.is_set():
      |                               |   if _focus_lock_paused.is_set():
      |                               |     continue
      |                               |   _focus_lock_iteration()
      |<-- signal_focus_lock_update --|   sleep(loop_interval)
      |                               |
      | pause_focus_lock()            |
      |------------------------------>| _focus_lock_paused.set()
      |                               |
      | resume_focus_lock()           |
      |------------------------------>| _focus_lock_paused.clear()
      |                               |
      | stop_focus_lock()             |
      |------------------------------>| _focus_lock_running.clear()
                                      | thread.join()
```

**Synchronization**:
- `ThreadSafeFlag` for running/paused states
- `ThreadSafeValue` for target displacement, current state
- `threading.Lock` for control algorithm parameter updates
- Qt signals for all UI updates (thread-safe by design)

---

## Comparison with Previous Documents

This plan supersedes and consolidates:
- `software/docs/implementation/CONTINUOUS_FOCUS_LOCK.md` - Task-oriented implementation guide (8 tasks)
- `docs/FOCUS_LOCK_PLAN.md` - High-level design with storm-control patterns

**Key differences from previous documents:**
1. Implements ALL THREE control algorithms (not just proportional)
2. Adds user-selectable algorithm with parameter tuning
3. More detailed widget design with all visualization components
4. Explicit acquisition integration with pause/resume/verify workflow
5. Physical units for all displays (um throughout)
6. Soft warning approach for range limits (not auto-pause)
