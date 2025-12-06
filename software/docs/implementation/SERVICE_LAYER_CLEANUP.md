# Service Layer Cleanup: Remove Fallbacks for Clean GUI/Hardware Separation

---

## ✅ COMPLETED TASKS

### TASK 1: Fix StageService.home() and zero() Bug - ✅ DONE
**Commit:** `fix(StageService): Add missing theta parameter to home() and zero()`

Added `theta` parameter to `home()` and `zero()` methods in `stage_service.py`.

### TASK 2: Add Missing CameraService Methods - ✅ DONE
**Commit:** `feat(CameraService): Add get_gain_range, get_acquisition_mode, get_pixel_size_binned_um`

Added three methods to `camera_service.py`:
- `get_gain_range()`
- `get_acquisition_mode()`
- `get_pixel_size_binned_um()`

### TASK 3A: Add Missing StageService Methods - ✅ DONE
**Commit:** `feat(StageService): Add positioning and conversion methods for widget abstraction`

Added methods to `stage_service.py`:
- `wait_for_idle()`, `set_limits()`
- `get_x_mm_per_ustep()`, `get_y_mm_per_ustep()`, `get_z_mm_per_ustep()`
- `move_to_safety_position()`
- `move_to_loading_position()`, `move_to_scanning_position()`

### TASK 3B: Update Widget Constructors to Require Services - ✅ DONE
**Commit:** `refactor(widgets): Require services in constructors, remove hardware fallbacks`

Updated widget constructors:
- `CameraSettingsWidget`: Require `camera_service`, remove `camera` param
- `StageUtils`: Require `stage_service`, update to use service methods
- `NavigationWidget`: Require `stage_service`, update `set_deltaX/Y/Z`
- `DACControWidget`: Require `peripheral_service`

### TASK 4: Remove Conditional Fallbacks in Movement Methods - ✅ DONE
**Commit:** `refactor(widgets): Remove stage movement fallbacks, use service exclusively`

Removed fallback logic from:
- `wellplate.py`: `_move_stage_relative()`
- `acquisition.py`: `_move_stage_to()`, `_move_z_to()`
- `display.py`: `_move_stage_to()`

### TASK 5: Route All Direct Hardware Calls Through Services - ✅ DONE

**5.1 Stage get_pos() calls:**
- `acquisition.py`: 22 occurrences → `self._stage_service.get_position()`
- `wellplate.py`: 1 occurrence → `self._stage_service.get_position()`
- `display.py`: 3 occurrences → `self._stage_service.get_position()`
- `custom_multipoint.py`: 1 occurrence → `self._stage_service.get_position()`

**5.2 Camera calls in camera.py:**
- All `self.camera.get_*()` calls in `CameraSettingsWidget` → `self._service.get_*()`

**5.3 Camera calls in display.py:**
- `NapariLiveWidget`: Added `camera_service` param, use for `get_exposure_limits()`
- `NapariMultiChannelWidget`: Replace `camera` with `camera_service`
- `NapariMosaicDisplayWidget`: Replace `camera` with `camera_service`

### TASK 6: Update Callers of Modified Widgets - ✅ DONE
**Commit:** `refactor(display): Route camera calls through service, update widget callers`

Updated `gui_hcs.py`:
- Pass `self._services.get('camera')` to `NapariLiveWidget`
- Pass `self._services.get('camera')` to `NapariMultiChannelWidget`
- Pass `self._services.get('camera')` to `NapariMosaicDisplayWidget`

---

## Background (Read This First)

### What is the Service Layer?
The service layer (`software/squid/services/`) sits between GUI widgets and hardware:
```
GUI Widgets → Services → Hardware Abstraction (AbstractStage, AbstractCamera) → Physical Hardware
```

Services handle:
- Command/state events via EventBus
- Value clamping and validation
- Publishing state change notifications

### What are Fallbacks?
When services were added, widgets got dual-mode code:
```python
# BAD: Fallback pattern we're removing
if self._stage_service is not None:
    self._stage_service.move_x(dx)  # Service path
else:
    self.stage.move_x(dx)  # Direct hardware fallback
```

### Goal
Remove all fallbacks. Widgets MUST use services. No direct hardware access from GUI code.

---

## Repository Structure (Key Files)

```
software/
├── squid/
│   ├── abc.py                          # AbstractStage, AbstractCamera interfaces
│   ├── services/
│   │   ├── __init__.py                 # ServiceRegistry
│   │   ├── base.py                     # BaseService (event subscription)
│   │   ├── camera_service.py           # CameraService
│   │   ├── stage_service.py            # StageService
│   │   └── peripheral_service.py       # PeripheralService
│   └── events.py                       # EventBus, Command/State events
├── control/
│   └── widgets/
│       ├── camera.py                   # CameraSettingsWidget
│       ├── stage.py                    # StageUtils, NavigationWidget
│       ├── hardware.py                 # DACControWidget
│       ├── acquisition.py              # FlexibleMultiPointWidget + others
│       ├── wellplate.py                # WellSelectionWidget
│       ├── display.py                  # Various display widgets
│       └── custom_multipoint.py        # Custom multipoint widget
└── tests/
    └── unit/squid/services/
        ├── test_camera_service.py
        ├── test_stage_service.py
        └── test_peripheral_service.py
```

---

## How to Run Tests

```bash
cd software
pytest tests/unit/squid/services/ -v           # Service tests only
pytest tests/ -v                               # All tests
pytest tests/ -v -k "stage"                    # Tests matching "stage"
```

---

# TASK 3A: Add Missing StageService Methods (PREREQUISITE)

## Problem
StageUtils and NavigationWidget call utility functions/methods that require raw stage access:
- `move_to_loading_position(stage, ...)` - used in StageUtils
- `move_to_scanning_position(stage, ...)` - used in StageUtils
- `move_z_axis_to_safety_position(stage)` - used in StageUtils
- `stage.x_mm_to_usteps()`, `stage.y_mm_to_usteps()`, `stage.z_mm_to_usteps()` - used in NavigationWidget

## Solution
Absorb utility function logic into StageService so widgets never need raw stage access.

## Files to Modify
| File | Change |
|------|--------|
| `software/squid/services/stage_service.py` | Add ~10 new methods |
| `software/tests/unit/squid/services/test_stage_service.py` | Add tests for new methods |

## Steps

### Step 3A.1: Add imports and instance variable

Edit `software/squid/services/stage_service.py`:

Add imports:
```python
from typing import Optional, Callable, TYPE_CHECKING
from threading import Thread
import control._def as _def
import control.utils
```

Add to `__init__`:
```python
self._scanning_position_z_mm = None  # Track Z position for loading/scanning
```

### Step 3A.2: Add synchronization and limit methods

```python
def wait_for_idle(self, timeout: float = 10.0):
    """Wait for stage to finish movement."""
    self._stage.wait_for_idle(timeout)

def set_limits(self, x_pos_mm: float = None, x_neg_mm: float = None,
               y_pos_mm: float = None, y_neg_mm: float = None):
    """Set movement limits."""
    self._stage.set_limits(x_pos_mm=x_pos_mm, x_neg_mm=x_neg_mm,
                          y_pos_mm=y_pos_mm, y_neg_mm=y_neg_mm)
```

### Step 3A.3: Add conversion methods for NavigationWidget

```python
def get_x_mm_per_ustep(self) -> float:
    """Get mm per microstep for X axis."""
    return 1.0 / self._stage.x_mm_to_usteps(1.0)

def get_y_mm_per_ustep(self) -> float:
    """Get mm per microstep for Y axis."""
    return 1.0 / self._stage.y_mm_to_usteps(1.0)

def get_z_mm_per_ustep(self) -> float:
    """Get mm per microstep for Z axis."""
    return 1.0 / self._stage.z_mm_to_usteps(1.0)
```

### Step 3A.4: Add positioning methods (absorb from stage_utils.py)

```python
def move_to_safety_position(self):
    """Move Z to safety position."""
    self._stage.move_z_to(int(_def.Z_HOME_SAFETY_POINT) / 1000.0)
    self._publish_position()

def _move_to_loading_position_impl(self, is_wellplate: bool):
    """Internal: move to loading position."""
    if is_wellplate:
        a_large_limit_mm = 125
        self._stage.set_limits(
            x_pos_mm=a_large_limit_mm, x_neg_mm=-a_large_limit_mm,
            y_pos_mm=a_large_limit_mm, y_neg_mm=-a_large_limit_mm,
        )
        self._scanning_position_z_mm = self._stage.get_pos().z_mm
        self._stage.move_z_to(_def.OBJECTIVE_RETRACTED_POS_MM)
        self._stage.wait_for_idle(_def.SLIDE_POTISION_SWITCHING_TIMEOUT_LIMIT_S)
        self._stage.move_y_to(15)
        self._stage.move_x_to(35)
        self._stage.move_y_to(_def.SLIDE_POSITION.LOADING_Y_MM)
        self._stage.move_x_to(_def.SLIDE_POSITION.LOADING_X_MM)
        config = self._stage.get_config()
        self._stage.set_limits(
            x_pos_mm=config.X_AXIS.MAX_POSITION, x_neg_mm=config.X_AXIS.MIN_POSITION,
            y_pos_mm=config.Y_AXIS.MAX_POSITION, y_neg_mm=config.Y_AXIS.MIN_POSITION,
        )
    else:
        self._stage.move_y_to(_def.SLIDE_POSITION.LOADING_Y_MM)
        self._stage.move_x_to(_def.SLIDE_POSITION.LOADING_X_MM)
    self._publish_position()

def _move_to_scanning_position_impl(self, is_wellplate: bool):
    """Internal: move to scanning position."""
    if is_wellplate:
        self._stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)
        self._stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
        if self._scanning_position_z_mm is not None:
            self._stage.move_z_to(self._scanning_position_z_mm)
        self._scanning_position_z_mm = None
    else:
        self._stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
        self._stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)
    self._publish_position()

def move_to_loading_position(self, blocking: bool = True,
                             callback: Callable = None,
                             is_wellplate: bool = True) -> Optional[Thread]:
    """Move stage to loading position."""
    if blocking and callback:
        raise ValueError("Callback not supported when blocking is True")
    if blocking:
        self._move_to_loading_position_impl(is_wellplate)
        return None
    return control.utils.threaded_operation_helper(
        self._move_to_loading_position_impl, callback, is_wellplate=is_wellplate
    )

def move_to_scanning_position(self, blocking: bool = True,
                              callback: Callable = None,
                              is_wellplate: bool = True) -> Optional[Thread]:
    """Move stage to scanning position."""
    if blocking and callback:
        raise ValueError("Callback not supported when blocking is True")
    if blocking:
        self._move_to_scanning_position_impl(is_wellplate)
        return None
    return control.utils.threaded_operation_helper(
        self._move_to_scanning_position_impl, callback, is_wellplate=is_wellplate
    )
```

### Step 3A.5: Add tests

Add tests to `software/tests/unit/squid/services/test_stage_service.py` for:
- `wait_for_idle`
- `set_limits`
- `get_x_mm_per_ustep`, `get_y_mm_per_ustep`, `get_z_mm_per_ustep`
- `move_to_safety_position`
- `move_to_loading_position`
- `move_to_scanning_position`

### Step 3A.6: Commit
```bash
pytest tests/unit/squid/services/test_stage_service.py -v
git add software/squid/services/stage_service.py software/tests/unit/squid/services/test_stage_service.py
git commit -m "feat(StageService): Add positioning and conversion methods for widget abstraction"
```

---

# TASK 3B: Update Widget Constructors to Require Services

## Overview
Remove the ability to pass raw hardware. Widgets MUST receive services.

## Files to Modify
| File | Widget | Change |
|------|--------|--------|
| `software/control/widgets/camera.py` | `CameraSettingsWidget` | Require `camera_service`, remove `camera` param |
| `software/control/widgets/stage.py` | `StageUtils` | Require `stage_service`, remove `stage` param |
| `software/control/widgets/stage.py` | `NavigationWidget` | Require `stage_service`, remove `stage` param |
| `software/control/widgets/hardware.py` | `DACControWidget` | Require `peripheral_service`, remove `microcontroller` param |

## Steps

### Step 3B.1: CameraSettingsWidget

**File:** `software/control/widgets/camera.py`

Find constructor (line ~43):
```python
def __init__(
    self,
    camera: AbstractCamera = None,           # REMOVE this
    camera_service: Optional["CameraService"] = None,
    ...
```

Change to:
```python
def __init__(
    self,
    camera_service: "CameraService",         # Now required
    ...
```

Find the fallback logic (lines ~62-71):
```python
# Use service if provided, otherwise create from legacy param
if camera_service is not None:
    self._service = camera_service
    self.camera = camera  # Keep for direct access where needed
elif camera is not None:
    # Legacy mode - create service wrapper
    from squid.services import CameraService
    self._service = CameraService(camera, event_bus)
    self.camera = camera
else:
    raise ValueError("Either camera_service or camera required")
```

Replace with:
```python
self._service = camera_service
```

**Remove** all `self.camera` references - they all need to use `self._service` instead (covered in Task 5).

### Step 3B.2: StageUtils

**File:** `software/control/widgets/stage.py`

Find constructor (line ~44):
```python
def __init__(
    self,
    stage: AbstractStage = None,             # REMOVE this
    ...
    stage_service: Optional["StageService"] = None,
```

Change to:
```python
def __init__(
    self,
    stage_service: "StageService",           # Now required (move to first param)
    live_controller: LiveController = None,
    is_wellplate: bool = False,
    parent=None
):
```

Find fallback logic (lines ~60-70) and replace with:
```python
self._service = stage_service
self.is_wellplate = is_wellplate  # Keep for positioning methods
```

**Update method calls in StageUtils:**

In `home_z()` (line ~160):
```python
# OLD:
move_z_axis_to_safety_position(self.stage)

# NEW:
self._service.move_to_safety_position()
```

In `switch_position()` (lines ~194-206):
```python
# OLD:
move_to_loading_position(self.stage, blocking=False, callback=..., is_wellplate=self.is_wellplate)
move_to_scanning_position(self.stage, blocking=False, callback=..., is_wellplate=self.is_wellplate)

# NEW:
self._service.move_to_loading_position(blocking=False, callback=..., is_wellplate=self.is_wellplate)
self._service.move_to_scanning_position(blocking=False, callback=..., is_wellplate=self.is_wellplate)
```

### Step 3B.3: NavigationWidget

**File:** `software/control/widgets/stage.py`

Find constructor (line ~342):
```python
def __init__(
    self,
    stage: AbstractStage = None,             # REMOVE this
    stage_service: Optional["StageService"] = None,
    ...
```

Change to:
```python
def __init__(
    self,
    stage_service: "StageService",           # Now required
    main=None,
    widget_configuration="full",
    *args,
    **kwargs,
):
```

Find fallback logic and replace with:
```python
self._service = stage_service
```

**Update conversion methods:**
```python
def set_deltaX(self, value):
    mm_per_ustep = self._service.get_x_mm_per_ustep()
    deltaX = round(value / mm_per_ustep) * mm_per_ustep
    self.entry_dX.setValue(deltaX)

def set_deltaY(self, value):
    mm_per_ustep = self._service.get_y_mm_per_ustep()
    deltaY = round(value / mm_per_ustep) * mm_per_ustep
    self.entry_dY.setValue(deltaY)

def set_deltaZ(self, value):
    mm_per_ustep = self._service.get_z_mm_per_ustep()
    deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
    self.entry_dZ.setValue(deltaZ)
```

### Step 3B.4: DACControWidget

**File:** `software/control/widgets/hardware.py`

Find constructor (line ~825):
```python
def __init__(
    self,
    microcontroller=None,                    # REMOVE this
    peripheral_service: Optional["PeripheralService"] = None,
```

Replace with:
```python
def __init__(
    self,
    peripheral_service: "PeripheralService", # Now required
```

Replace fallback logic (lines ~840-847) with:
```python
self._service = peripheral_service
```

### Step 3B.5: Run tests and commit
```bash
pytest tests/ -v
git add software/control/widgets/camera.py software/control/widgets/stage.py software/control/widgets/hardware.py
git commit -m "refactor(widgets): Require services in constructors, remove hardware fallbacks"
```

---

# TASK 4: Remove Conditional Fallbacks in Movement Methods

## Files and Locations

| File | Method | Line |
|------|--------|------|
| `software/control/widgets/wellplate.py` | `_move_stage_relative()` | ~614-621 |
| `software/control/widgets/acquisition.py` | `_move_stage_to()` | ~1069-1076 |
| `software/control/widgets/acquisition.py` | `_move_z_to()` | ~2817-2822 |
| `software/control/widgets/display.py` | `_move_stage_to()` | ~373-380 |

## Steps

### Step 4.1: wellplate.py

Find `_move_stage_relative()` (line ~614):
```python
def _move_stage_relative(self, dx: float, dy: float):
    """Move stage by relative distance using service if available."""
    if self._stage_service is not None:
        self._stage_service.move_x(dx)
        self._stage_service.move_y(dy)
    else:
        self.stage.move_x(dx)
        self.stage.move_y(dy)
```

Replace with:
```python
def _move_stage_relative(self, dx: float, dy: float):
    """Move stage by relative distance."""
    self._stage_service.move_x(dx)
    self._stage_service.move_y(dy)
```

### Step 4.2: acquisition.py - _move_stage_to()

Find method (line ~1069):
```python
def _move_stage_to(self, x: float, y: float, z: float):
    """Move stage to position using service if available, else direct call."""
    if self._stage_service is not None:
        self._stage_service.move_to(x_mm=x, y_mm=y, z_mm=z)
    else:
        self.stage.move_x_to(x)
        self.stage.move_y_to(y)
        self.stage.move_z_to(z)
```

Replace with:
```python
def _move_stage_to(self, x: float, y: float, z: float):
    """Move stage to position."""
    self._stage_service.move_to(x_mm=x, y_mm=y, z_mm=z)
```

### Step 4.3: acquisition.py - _move_z_to()

Find method (line ~2817):
```python
def _move_z_to(self, z_mm: float):
    """Move Z axis using service if available, else direct call."""
    if self._stage_service is not None:
        self._stage_service.move_to(z_mm=z_mm)
    else:
        self.stage.move_z_to(z_mm)
```

Replace with:
```python
def _move_z_to(self, z_mm: float):
    """Move Z axis."""
    self._stage_service.move_to(z_mm=z_mm)
```

### Step 4.4: display.py - _move_stage_to()

Find method (line ~373):
```python
def _move_stage_to(self, x: float, y: float, z: float):
    """Move stage to position using service if available, else direct call."""
    if self._stage_service is not None:
        self._stage_service.move_to(x_mm=x, y_mm=y, z_mm=z)
    else:
        self.stage.move_x_to(x)
        self.stage.move_y_to(y)
        self.stage.move_z_to(z)
```

Replace with:
```python
def _move_stage_to(self, x: float, y: float, z: float):
    """Move stage to position."""
    self._stage_service.move_to(x_mm=x, y_mm=y, z_mm=z)
```

### Step 4.5: Commit
```bash
pytest tests/ -v
git add software/control/widgets/wellplate.py software/control/widgets/acquisition.py software/control/widgets/display.py
git commit -m "refactor(widgets): Remove stage movement fallbacks, use service exclusively"
```

---

# TASK 5: Route All Direct Hardware Calls Through Services

This is the largest task. Replace ~50 direct hardware calls with service calls.

## 5.1: Stage get_pos() Calls

### Pattern
Replace:
```python
self.stage.get_pos()
```
With:
```python
self._stage_service.get_position()
```

**Note:** Both return a `Pos` object with `.x_mm`, `.y_mm`, `.z_mm`.

### Locations in acquisition.py (~25 locations)
Lines: 284, 295, 567, 583, 600, 616, 621, 641, 818, 902, 1273, 1465, 1481, 2332, 2475, 2652, 2801, 2806, 2846, 2878, 2961, 3096

Use search and replace:
```
Find: self.stage.get_pos()
Replace: self._stage_service.get_position()
```

### Locations in wellplate.py (1 location)
Line 625:
```python
# Before
pos = self.stage.get_pos()
# After
pos = self._stage_service.get_position()
```

### Locations in display.py (4 locations)
Lines: 287, 313, 385, and another

### Locations in custom_multipoint.py (1 location)
Line 129

### Commit after each file
```bash
git add software/control/widgets/acquisition.py
git commit -m "refactor(acquisition): Route stage.get_pos() through service"

git add software/control/widgets/wellplate.py
git commit -m "refactor(wellplate): Route stage.get_pos() through service"
# etc.
```

## 5.2: Camera get_*() Calls in camera.py

### Locations and replacements

| Line | Old | New |
|------|-----|-----|
| 90 | `self.camera.get_exposure_limits()[0]` | `self._service.get_exposure_limits()[0]` |
| 91 | `self.camera.get_exposure_limits()[1]` | `self._service.get_exposure_limits()[1]` |
| 99 | `self.camera.get_gain_range()` | `self._service.get_gain_range()` |
| 112 | `self.camera.get_available_pixel_formats()` | `self._service.get_available_pixel_formats()` |
| 117-118 | `self.camera.get_pixel_format()` | `self._service.get_pixel_format()` |
| 127 | `self.camera.get_region_of_interest()` | `self._service.get_region_of_interest()` |
| 128 | `self.camera.get_resolution()` | `self._service.get_resolution()` |
| 192 | `self.camera.get_binning()` | `self._service.get_binning()` |
| 194 | `self.camera.get_binning_options()` | `self._service.get_binning_options()` |
| 251 | `self.camera.get_pixel_format()` | `self._service.get_pixel_format()` |
| 444 | `self.camera.get_acquisition_mode()` | `self._service.get_acquisition_mode()` |
| 474-475 | `self.camera.get_exposure_limits()` | `self._service.get_exposure_limits()` |
| 482 | `self.camera.get_gain_range()` | `self._service.get_gain_range()` |
| 661 | `self.camera.get_exposure_time()` | `self._service.get_exposure_time()` |
| 666 | `self.camera.get_analog_gain()` | `self._service.get_analog_gain()` |

### Commit
```bash
git add software/control/widgets/camera.py
git commit -m "refactor(camera): Route all camera calls through service"
```

## 5.3: Camera calls in display.py

| Line | Old | New |
|------|-----|-----|
| 712 | `self.camera.get_exposure_limits()` | `self._camera_service.get_exposure_limits()` |
| 1156 | `self.camera.get_pixel_size_binned_um()` | `self._camera_service.get_pixel_size_binned_um()` |
| 1437 | `self.camera.get_pixel_size_binned_um()` | `self._camera_service.get_pixel_size_binned_um()` |

**Note:** These widgets will need a `_camera_service` attribute added to their constructors.

### Commit
```bash
git add software/control/widgets/display.py
git commit -m "refactor(display): Route camera calls through service"
```

---

# TASK 6: Update Callers of Modified Widgets

After changing widget constructors, callers need updating.

## Find Callers
```bash
grep -r "CameraSettingsWidget(" software/control/ --include="*.py"
grep -r "StageUtils(" software/control/ --include="*.py"
grep -r "NavigationWidget(" software/control/ --include="*.py"
grep -r "DACControWidget(" software/control/ --include="*.py"
```

## Key File: gui_hcs.py
`software/control/gui_hcs.py` creates most widgets.

Look for patterns like:
```python
camera_service=self._services.get('camera') if self._services else None,
```

Change to:
```python
camera_service=self._services.get('camera'),
```

The `if self._services else None` fallback is no longer needed.

---

# Verification Checklist

After all tasks, verify:

```bash
# All tests pass
pytest tests/ -v

# No remaining direct hardware access in widgets
grep -r "self\.stage\." software/control/widgets/ --include="*.py" | grep -v "_stage_service"
grep -r "self\.camera\." software/control/widgets/ --include="*.py" | grep -v "_camera_service"

# Should return no results (or only legitimate uses like self.stage_service)
```

---

# Summary of Commits

1. ✅ `fix(StageService): Add missing theta parameter to home() and zero()` - DONE
2. ✅ `feat(CameraService): Add get_gain_range, get_acquisition_mode, get_pixel_size_binned_um` - DONE
3. `feat(StageService): Add positioning and conversion methods for widget abstraction` - NEW
4. `refactor(widgets): Require services in constructors, remove hardware fallbacks`
5. `refactor(widgets): Remove stage movement fallbacks, use service exclusively`
6. `refactor(acquisition): Route stage.get_pos() through service`
7. `refactor(wellplate): Route stage.get_pos() through service`
8. `refactor(display): Route stage/camera calls through service`
9. `refactor(camera): Route all camera calls through service`
10. `refactor(custom_multipoint): Route stage calls through service`
11. `refactor(gui_hcs): Update widget instantiation to require services`
