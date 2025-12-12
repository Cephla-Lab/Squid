# Phase 4B: Autofocus Service Usage

**Purpose:** Refactor autofocus controllers to use services instead of direct hardware access. This ensures thread-safe hardware access during autofocus operations that run concurrently with acquisition.

**Prerequisites:** Phase 4 complete (services exist for Camera, Stage, Peripheral, Piezo)

**Estimated Effort:** 2-3 days

---

## Overview

The autofocus system has three main components with direct hardware access:

| File | Lines | Direct Hardware Calls |
|------|-------|----------------------|
| `auto_focus_controller.py` | ~242 | ~12 (camera, stage) |
| `auto_focus_worker.py` | ~134 | ~15 (camera, stage, microcontroller) |
| `laser_auto_focus_controller.py` | ~751 | ~40 (camera, stage, microcontroller) |

**Files to Modify:**
1. `/Users/wea/src/allenlab/Squid/software/control/core/autofocus/auto_focus_controller.py`
2. `/Users/wea/src/allenlab/Squid/software/control/core/autofocus/auto_focus_worker.py`
3. `/Users/wea/src/allenlab/Squid/software/control/core/autofocus/laser_auto_focus_controller.py`
4. `/Users/wea/src/allenlab/Squid/software/control/gui/qt_controllers.py` - QtAutoFocusController

---

## Task Checklist

### 4B.1 Update AutoFocusController Constructor ✅

**File:** `control/core/autofocus/auto_focus_controller.py`

- [x] Add service imports (TYPE_CHECKING)
- [x] Add service parameters to constructor (optional for backwards compatibility)
- [x] Store service references

**Current constructor:**
```python
def __init__(
    self,
    camera: AbstractCamera,
    stage: AbstractStage,
    liveController: LiveController,
    microcontroller: Microcontroller,
    finished_fn: Callable[[], None],
    image_to_display_fn: Callable[[np.ndarray], None],
    nl5: Optional[NL5],
):
    self.camera: AbstractCamera = camera
    self.stage: AbstractStage = stage
    self.microcontroller: Microcontroller = microcontroller
    # ...
```

**Target constructor:**
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.services import CameraService, StageService, PeripheralService
    from squid.events import EventBus

def __init__(
    self,
    camera: AbstractCamera,
    stage: AbstractStage,
    liveController: LiveController,
    microcontroller: Microcontroller,
    finished_fn: Callable[[], None],
    image_to_display_fn: Callable[[np.ndarray], None],
    nl5: Optional[NL5],
    # Service-based parameters (optional for backwards compatibility)
    camera_service: Optional["CameraService"] = None,
    stage_service: Optional["StageService"] = None,
    peripheral_service: Optional["PeripheralService"] = None,
    event_bus: Optional["EventBus"] = None,
):
    # Direct references (for fallback)
    self.camera: AbstractCamera = camera
    self.stage: AbstractStage = stage
    self.microcontroller: Microcontroller = microcontroller

    # Service references
    self._camera_service = camera_service
    self._stage_service = stage_service
    self._peripheral_service = peripheral_service
    self._event_bus = event_bus
    # ...
```

**Commit:** `refactor(autofocus): Update AutoFocusController constructor for services`

---

### 4B.2 Replace AutoFocusController Hardware Calls ✅

**File:** `control/core/autofocus/auto_focus_controller.py`

Replace direct hardware calls with service calls:

- [x] Replace `self.stage.wait_for_idle()`
- [x] Replace `self.stage.get_pos()`
- [x] Replace `self.stage.move_z_to()`
- [x] Replace `self.stage.move_x_to()` / `self.stage.move_y_to()`
- [x] Replace `self.camera.get_callbacks_enabled()`
- [x] Replace `self.camera.enable_callbacks()`

**Replacement pattern:**
```python
# Before
self.stage.wait_for_idle(1.0)
pos = self.stage.get_pos()

# After
if self._stage_service:
    self._stage_service.wait_for_idle(1.0)
    pos = self._stage_service.get_position()
else:
    self.stage.wait_for_idle(1.0)
    pos = self.stage.get_pos()
```

**Commit:** `refactor(autofocus): Replace AutoFocusController hardware calls with services`

---

### 4B.3 Update AutofocusWorker for Services ✅

**File:** `control/core/autofocus/auto_focus_worker.py`

The worker gets hardware references from the controller. Update to also get services:

- [x] Get service references from controller
- [x] Replace `self.camera.send_trigger()` / `self.camera.read_frame()`
- [x] Replace `self.stage.move_z()`
- [x] Replace `self.microcontroller.is_busy()` / `self.microcontroller.send_hardware_trigger()`

**Current pattern:**
```python
def __init__(self, autofocusController: "AutoFocusController", ...):
    self.camera: AbstractCamera = self.autofocusController.camera
    self.microcontroller: Microcontroller = self.autofocusController.microcontroller
    self.stage: AbstractStage = self.autofocusController.stage
```

**Target pattern:**
```python
def __init__(self, autofocusController: "AutoFocusController", ...):
    # Direct references (fallback)
    self.camera: AbstractCamera = self.autofocusController.camera
    self.microcontroller: Microcontroller = self.autofocusController.microcontroller
    self.stage: AbstractStage = self.autofocusController.stage

    # Service references
    self._camera_service = self.autofocusController._camera_service
    self._stage_service = self.autofocusController._stage_service
    self._peripheral_service = self.autofocusController._peripheral_service
```

**Commit:** `refactor(autofocus): Update AutofocusWorker to use services`

---

### 4B.4 Update LaserAutofocusController Constructor ✅

**File:** `control/core/autofocus/laser_auto_focus_controller.py`

This is the largest file (~751 lines) with the most hardware calls (~40).

- [x] Add service imports
- [x] Add service parameters to constructor
- [x] Store service references

**Commit:** `refactor(autofocus): Update LaserAutofocusController constructor for services`

---

### 4B.5 Replace LaserAutofocusController Camera Calls ✅

**File:** `control/core/autofocus/laser_auto_focus_controller.py`

- [x] Replace `self.camera.set_region_of_interest()`
- [x] Replace `self.camera.set_exposure_time()`
- [x] Replace `self.camera.set_analog_gain()`
- [x] Replace `self.camera.send_trigger()`
- [x] Replace `self.camera.read_frame()`
- [x] Replace `self.camera.enable_callbacks()`
- [x] Replace `self.camera.get_exposure_time()`

**Note:** Some camera operations (ROI, exposure, gain) may need new CameraService methods.

**Commit:** `refactor(autofocus): Replace LaserAutofocusController camera calls with CameraService`

---

### 4B.6 Replace LaserAutofocusController Microcontroller Calls ✅

**File:** `control/core/autofocus/laser_auto_focus_controller.py`

The laser autofocus has many AF laser control calls:

- [x] Replace `self.microcontroller.turn_on_AF_laser()`
- [x] Replace `self.microcontroller.turn_off_AF_laser()`
- [x] Replace `self.microcontroller.wait_till_operation_is_completed()`

**Decision:** AF laser control added to PeripheralService (Option A chosen).

**Option A: Add to PeripheralService** ✅
```python
# In squid/services/peripheral_service.py
def turn_on_af_laser(self) -> None:
    with self._lock:
        self._microcontroller.turn_on_AF_laser()
        self._microcontroller.wait_till_operation_is_completed()

def turn_off_af_laser(self) -> None:
    with self._lock:
        self._microcontroller.turn_off_AF_laser()
        self._microcontroller.wait_till_operation_is_completed()
```

**Option B: Create dedicated LaserAFService** (if laser AF has complex state)

**Commit:** `refactor(autofocus): Replace LaserAutofocusController microcontroller calls`

---

### 4B.7 Replace LaserAutofocusController Stage Calls ✅

**File:** `control/core/autofocus/laser_auto_focus_controller.py`

- [x] Replace `self.stage.move_z()`

**Commit:** `refactor(autofocus): Replace LaserAutofocusController stage calls with StageService`

---

### 4B.8 Update QtAutoFocusController ✅

**File:** `control/gui/qt_controllers.py`

- [x] Update constructor to accept services
- [x] Pass services to parent AutoFocusController

**Commit:** `refactor(autofocus): Update QtAutoFocusController for services`

---

### 4B.9 Update Wiring in GUI/ApplicationContext ✅

**Files:**
- `control/gui_hcs.py`
- `squid/application.py` (if autofocus controller is created there)

- [x] Pass services when creating autofocus controllers

**Commit:** `refactor(app): Wire services to autofocus controllers`

---

### 4B.10 Add Autofocus Events (Optional Enhancement)

**File:** `squid/events.py` and autofocus controllers

Add events for autofocus progress and completion:

- [ ] `AutofocusStarted` - when autofocus begins
- [ ] `AutofocusProgress` - progress updates (current step, best focus so far)
- [ ] `AutofocusCompleted` - when autofocus finishes (success/failure, final z position)

These events already exist in `squid/events.py`:
```python
@dataclass
class AutofocusProgress(Event):
    current_step: int
    total_steps: int
    current_z: float
    best_z: Optional[float]
    best_score: Optional[float]

@dataclass
class AutofocusCompleted(Event):
    success: bool
    z_position: Optional[float]
    score: Optional[float]
    error: Optional[str] = None
```

- [ ] Publish `AutofocusProgress` during autofocus loop
- [ ] Publish `AutofocusCompleted` when finished

**Commit:** `feat(autofocus): Add autofocus progress events`

---

## Verification Checklist

Before proceeding to Phase 5, verify:

- [x] Service-based alternatives exist for all autofocus hardware operations
- [x] AutoFocusController uses services when available
- [x] AutofocusWorker uses services when available
- [x] LaserAutofocusController uses services when available
- [x] Syntax validation passes for all modified files
- [ ] Application starts: `python main_hcs.py --simulation`
- [ ] Contrast autofocus works (manual test)
- [ ] Laser autofocus works (manual test, if hardware available)

**Verification commands:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Check for service usage
echo "=== Checking for service usage in autofocus ==="
grep -c "self\._camera_service" control/core/autofocus/*.py
grep -c "self\._stage_service" control/core/autofocus/*.py
grep -c "self\._peripheral_service" control/core/autofocus/*.py

# Syntax check
python3 -c "
import ast
for f in [
    'control/core/autofocus/auto_focus_controller.py',
    'control/core/autofocus/auto_focus_worker.py',
    'control/core/autofocus/laser_auto_focus_controller.py',
]:
    ast.parse(open(f).read())
    print(f'{f}: OK')
"
```

---

## Commit Summary

| Order | Commit Message | Files |
|-------|----------------|-------|
| 1 | `refactor(autofocus): Update AutoFocusController constructor for services` | `auto_focus_controller.py` |
| 2 | `refactor(autofocus): Replace AutoFocusController hardware calls with services` | `auto_focus_controller.py` |
| 3 | `refactor(autofocus): Update AutofocusWorker to use services` | `auto_focus_worker.py` |
| 4 | `refactor(autofocus): Update LaserAutofocusController constructor for services` | `laser_auto_focus_controller.py` |
| 5 | `refactor(autofocus): Replace LaserAutofocusController camera calls with CameraService` | `laser_auto_focus_controller.py` |
| 6 | `refactor(autofocus): Replace LaserAutofocusController microcontroller calls` | `laser_auto_focus_controller.py`, `peripheral_service.py` |
| 7 | `refactor(autofocus): Replace LaserAutofocusController stage calls with StageService` | `laser_auto_focus_controller.py` |
| 8 | `refactor(autofocus): Update QtAutoFocusController for services` | `qt_controllers.py` |
| 9 | `refactor(app): Wire services to autofocus controllers` | `gui_hcs.py`, `application.py` |
| 10 | `feat(autofocus): Add autofocus progress events` | `auto_focus_*.py`, `events.py` |

---

## Dependencies on Other Services

The autofocus system may need these CameraService methods that might not exist yet:

```python
# Methods that may need to be added to CameraService:
def set_region_of_interest(self, x: int, y: int, width: int, height: int) -> None
def get_region_of_interest(self) -> Tuple[int, int, int, int]
def set_exposure_time(self, exposure_ms: float) -> None
def set_analog_gain(self, gain: float) -> None
```

Check CameraService before starting and add missing methods if needed.

---

## Next Steps

Once all checkmarks are complete, proceed to:
→ [PHASE_5_WIDGET_UPDATES.md](./PHASE_5_WIDGET_UPDATES.md)
