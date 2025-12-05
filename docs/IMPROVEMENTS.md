# Squid Codebase Quality Analysis & Improvement Roadmap

This document provides a comprehensive analysis of the Squid microscopy software codebase, identifying strengths, weaknesses, and a prioritized roadmap for improvements.

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [What's Well Implemented](#whats-well-implemented)
3. [What's Poorly Implemented](#whats-poorly-implemented)
4. [Improvement Roadmap](#improvement-roadmap)
5. [Architectural Refactoring Guide](#architectural-refactoring-guide)

---

## Executive Summary

The Squid microscopy software is a **moderately well-architected system** with strong hardware abstraction but significant technical debt in GUI coupling, configuration management, and code organization.

### Scorecard

| Category | Rating | Notes |
|----------|--------|-------|
| Hardware Abstraction | Good | Clean ABCs in `squid/abc.py` |
| Configuration Management | Poor | 990-line `_def.py` with global state |
| GUI Architecture | Poor | Tightly coupled to business logic |
| Code Organization | Poor | 10,671-line `widgets.py` |
| Error Handling | Poor | 30+ bare `except:` clauses |
| Type Safety | Fair | ~12% type hint coverage |
| Test Coverage | Fair | Minimal GUI/integration tests |
| Threading Safety | Fair | Multiple models, potential races |

---

## What's Well Implemented

### 1. Hardware Abstraction Layer

**Location**: `software/squid/abc.py` (872 lines)

The abstract base classes provide clean contracts for hardware components:

```python
# Well-designed abstractions
AbstractCamera          # Camera interface with frame callbacks
AbstractStage           # XYZ movement with blocking/non-blocking modes
AbstractFilterWheelController  # Filter wheel positioning
LightSource             # Illumination control
```

**Strengths**:
- Enables swapping hardware without GUI changes
- Proper use of Python ABCs with `@abstractmethod`
- Consistent interface across 8+ camera vendors
- Good separation of concerns

### 2. Type-Safe Configuration Models

**Location**: `software/squid/config.py` (568 lines)

Uses Pydantic for validation:
- `CameraConfig`, `StageConfig`, `FilterWheelConfig`
- `CameraAcquisitionMode`, `CameraPixelFormat` enums
- `AxisConfig` for per-axis stage configuration
- `Pos` dataclass for position representation

### 3. Logging Infrastructure

**Location**: `software/squid/logging.py`

- Threaded logging with thread IDs
- Color-coded log levels
- Proper exception hook registration
- File logging with rotation support

### 4. Factory Pattern for Hardware

**Locations**:
- `software/squid/camera/utils.py` - `get_camera()`
- `software/squid/filter_wheel_controller/utils.py` - `get_filter_wheel_controller()`
- `software/control/microscope.py` - `Microscope.build_from_global_config()`

Dynamic instantiation based on configuration enables runtime hardware selection.

### 5. Callback Architecture

**Location**: `software/control/core/multi_point_utils.py`

```python
@dataclass
class MultiPointControllerFunctions:
    signal_acquisition_start: Callable
    signal_new_image: Callable
    signal_coordinates: Callable
    # ... pluggable callbacks
```

**Strengths**:
- Functional callbacks instead of tight coupling
- Enables testing with NoOp callbacks
- GUI can subscribe to events without worker knowing about GUI

### 6. Camera Frame Pipeline

**Locations**:
- `software/squid/abc.py` - `CameraFrame` dataclass
- `software/control/core/stream_handler.py` - `StreamHandler`

- Frame callbacks registered via `add_frame_callback()`
- FPS throttling for display performance
- Resolution scaling for preview mode

---

## What's Poorly Implemented

### 1. CRITICAL: Monolithic Configuration

**Location**: `software/control/_def.py` (990 lines)

**Problems**:
- 258 module-level constants
- Imported with `from control._def import *` (pollutes namespace)
- Tests mutate global state: `control._def.MERGE_CHANNELS = False`
- No validation, no schema, changes require restart

**Example of problematic pattern**:
```python
# software/control/core/core.py:14
from control._def import *  # Imports 258 constants!

# software/tests/control/test_MultiPointController.py:15
control._def.MERGE_CHANNELS = False  # Direct global mutation
```

**Configuration scattered across**:
1. `_def.py` - 258 Python constants
2. `configurations/*.ini` - 10+ INI files
3. `cache/objective_and_sample_format.txt` - JSON cache
4. `channel_configurations.xml` - Per-objective channel configs
5. Hardcoded defaults in various classes

### 2. CRITICAL: GUI Over-Coupling

**Location**: `software/control/gui_hcs.py` (1,186 lines)

**Problems**:
- GUI creates and manages controllers directly (lines 330-379)
- 25+ widget instance variables stored on main window
- GUI orchestrates hardware instead of being thin view layer

**Example** (lines 263-296):
```python
class HighContentScreeningGui(QMainWindow):
    def __init__(self, microscope: Microscope, ...):
        # GUI stores direct hardware references
        self.microscope = microscope
        self.stage = microscope.stage
        self.camera = microscope.camera

        # GUI instantiates controllers
        self.multipointController = QtMultiPointController(...)
        self.streamHandler = core.QtStreamHandler(...)
        self.autofocusController = AutoFocusController(...)
```

**Issues**:
- Can't test business logic without GUI
- Can't replace GUI without rewriting controller instantiation
- Mixing UI concerns with orchestration logic

### 3. CRITICAL: Massive Files

| File | Lines | Classes | Problem |
|------|-------|---------|---------|
| `software/control/widgets.py` | 10,671 | 18+ | Unmaintainable monolith |
| `software/control/core/core.py` | 2,045 | 5+ | Mixed responsibilities |
| `software/control/toupcam.py` | 2,694 | 1 | Camera SDK wrapper |
| `software/control/stitcher.py` | 1,946 | 3 | Image stitching |
| `software/control/core/multi_point_worker.py` | 898 | 1 | Acquisition worker |

**`widgets.py` contains**:
- `ConfigEditor`
- `LaserAutofocusSettingWidget`
- `SpinningDiskConfocalWidget`
- `DragonflyConfocalWidget`
- `ObjectivesWidget`
- `FluidicsWidget`
- `NapariLiveWidget`
- `NapariMultiChannelWidget`
- `NapariMosaicDisplayWidget`
- `WellplateFormatWidget`
- `WellplateCalibration`
- `CalibrationLiveViewer`
- `Joystick`
- `Well1536SelectionWidget`
- `LedMatrixSettingsDialog`
- `SurfacePlotWidget`
- And more...

### 4. HIGH: Error Handling

**30+ bare `except:` clauses across**:
- `software/control/core/core.py`: 13 instances
- `software/control/widgets.py`: 4 instances
- `software/control/camera_*.py`: Multiple instances

**Examples**:

```python
# software/control/camera.py:16-19
try:
    import control.gxipy as gx
except:
    print("gxipy import error")  # BAD: bare except + print

# software/control/core/multi_point_worker.py:86-95
try:
    pixel_factor = self.objectiveStore.get_pixel_size_factor()
    # ...
except Exception:  # BAD: silent failure, no logging
    self._pixel_size_um = None

# software/control/core/utils_ome_tiff_writer.py:181
except Exception:
    pass  # BAD: completely silent
```

### 5. HIGH: Code Duplication

**Thread management duplicated across all cameras**:

```python
# Identical in camera_hamamatsu.py:80-88, camera_tucsen.py:142-148, camera_andor.py:80-86
self._read_thread_lock = threading.Lock()
self._read_thread: Optional[threading.Thread] = None
self._read_thread_keep_running = threading.Event()
self._read_thread_keep_running.clear()
self._read_thread_wait_period_s = 1.0
self._read_thread_running = threading.Event()
self._read_thread_running.clear()
```

**Should be extracted to**: `CameraThreadMixin` or base class method

### 6. HIGH: Incomplete Legacy Migration

**Modern `squid/` module still depends on legacy `control/`**:

```python
# software/squid/config.py:7
import control._def as _def  # Can't escape legacy!

# 78 import points from squid → control
# software/squid/camera/utils.py imports 9 legacy camera implementations
# software/squid/stage/cephla.py imports control.microcontroller, control._def
```

### 7. MEDIUM: Naming Inconsistencies

**Mixed naming conventions in same class** (`multi_point_worker.py:50-98`):
```python
self.liveController = live_controller        # camelCase
self.laser_auto_focus_controller = ...       # snake_case
self.objectiveStore = objective_store        # camelCase
self.NZ = acquisition_parameters.NZ          # UPPER_CASE
self.deltaZ = acquisition_parameters.deltaZ  # camelCase
```

**Typo in filenames**:
- `configuration_mananger.py` (should be `manager`)
- `channel_configuration_mananger.py` (should be `manager`)
- Parameter: `channel_configuration_mananger` at line 63 of `multi_point_worker.py`

### 8. MEDIUM: Type Hint Coverage ~12%

- Functions with return annotations: ~293
- Functions without: ~2,262
- **Coverage: ~12%**

**Well-typed**: `squid/abc.py`, `squid/config.py`
**Poorly typed**: All camera implementations, `widgets.py`, `core.py`

### 9. MEDIUM: Threading Safety Risks

**Multiple threading models used**:
- `threading.Event` for frame synchronization
- `threading.Timer` for LiveController
- Qt signals/slots for GUI updates
- Direct thread spawning in AutoFocusController

**Known race condition** (`multi_point_worker.py:125`):
```python
# NOTE: Once we do overlapping triggering, we'll want to keep a queue of images
```

**Shared state without locks**:
- `_current_capture_info` accessed from multiple threads
- `LiveController.is_live` is simple boolean, not atomic

### 10. MEDIUM: Test Gaps

**What exists** (18 test files):
- Unit tests for microscope, microcontroller, stage, camera, config
- Integration tests for MultiPointController, MultiPointWorker

**What's missing**:
- GUI widget interaction tests (only basic creation tested)
- Threading behavior tests (marked `@pytest.mark.skip`)
- Error condition/failure tests
- Real hardware simulation tests
- Load/performance tests

### 11. LOW: 94 TODO/FIXME Comments

| Area | Count | Priority |
|------|-------|----------|
| Camera Tucsen | 10 | Medium - incomplete model support |
| Multi-point worker | 8 | High - core functionality |
| Laser autofocus | 6 | High - safety-critical |
| Core/core.py | 5 | Medium |
| Widgets | 4 | Low |
| Other | 61 | Various |

**Critical TODOs**:
```python
# software/control/core/laser_auto_focus_controller.py:543
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (50, 50))  # TODO: tmp hard coded value

# software/control/core/multi_point_worker.py:374
# TODO(imo): Should we abort if there is a failure?
```

### 12. LOW: Hardcoded Values

**Hardcoded paths**:
```python
# software/control/gxipy/gxwrapper.py:11
CDLL('/usr/lib/libgxiapi.so')  # Linux-specific

# software/control/dcamapi4.py:19
cdll.LoadLibrary('/usr/local/lib/libdcamapi.so')  # Absolute path
```

**Magic numbers without explanation**:
```python
# software/control/Xeryon.py:66-68
DEFAULT_POLI_VALUE = 200
AMPLITUDE_MULTIPLIER = 1456.0
PHASE_MULTIPLIER = 182
# No documentation for these values
```

---

## Improvement Roadmap

### Priority 1: Quick Wins (Low Risk)

#### 1.1 Fix Bare Exception Clauses
**Scope**: ~50 edits across 15 files
**Files**:
- `software/control/core/core.py` (13 instances)
- `software/control/widgets.py` (4 instances)
- `software/control/camera_*.py` (various)

**Pattern**:
```python
# Before
except:
    print("error")

# After
except ImportError as e:
    logger.warning(f"Optional module not available: {e}")
except Exception as e:
    logger.exception(f"Unexpected error: {e}")
```

#### 1.2 Fix Naming Typo
**Files to rename**:
- `configuration_mananger.py` → `configuration_manager.py`
- `channel_configuration_mananger.py` → `channel_configuration_manager.py`

**References to update**:
- `multi_point_worker.py:63`
- All imports of these modules

#### 1.3 Extract Camera Thread Mixin
**Create**: `software/control/camera_thread_mixin.py`
```python
class CameraThreadMixin:
    def _init_thread_management(self):
        self._read_thread_lock = threading.Lock()
        self._read_thread: Optional[threading.Thread] = None
        self._read_thread_keep_running = threading.Event()
        # ...
```
**Apply to**: All 9 camera implementations

### Priority 2: Code Organization (Medium Risk)

#### 2.1 Split widgets.py

**Proposed structure**:
```
software/control/widgets/
├── __init__.py              # Re-exports for backward compatibility
├── camera.py                # CameraSettingsWidget, etc.
├── acquisition.py           # MultiPointWidget, RecordingWidget
├── navigation.py            # NavigationWidget, Joystick
├── autofocus.py             # AutoFocusWidget, LaserAutofocusSettingWidget
├── config_editors.py        # ConfigEditor, ObjectivesWidget
├── display.py               # NapariLiveWidget, NapariMultiChannelWidget
├── wellplate.py             # WellplateFormatWidget, WellplateCalibration
└── specialized.py           # SpinningDiskConfocalWidget, DragonflyConfocalWidget
```

**Backward compatibility**:
```python
# software/control/widgets/__init__.py
from .camera import CameraSettingsWidget
from .acquisition import MultiPointWidget
# ... re-export all classes
```

#### 2.2 Add Type Hints to Core Modules

**Target**: 12% → 50% coverage

**Priority files**:
1. `multi_point_worker.py` - Core acquisition logic
2. `live_controller.py` - Live view management
3. `auto_focus_controller.py` - Autofocus logic
4. `stream_handler.py` - Frame processing

### Priority 3: Architectural Changes (Higher Risk)

#### 3.1 Consolidate Configuration

**Phase 1**: Create unified config schema
```python
# software/squid/config/system_config.py
from pydantic import BaseModel

class SystemConfig(BaseModel):
    camera: CameraConfig
    stage: StageConfig
    illumination: IlluminationConfig
    acquisition: AcquisitionConfig
    paths: PathConfig

    @classmethod
    def from_ini_file(cls, path: Path) -> "SystemConfig":
        # Load and validate from INI
        pass
```

**Phase 2**: Migrate constants from `_def.py`
**Phase 3**: Remove global state mutations in tests

#### 3.2 Decouple GUI from Business Logic

**Current architecture**:
```
GUI (HighContentScreeningGui)
  └── Creates → Controllers
        └── Creates → Workers
              └── Uses → Hardware
```

**Target architecture**:
```
Application
  ├── Creates → Controllers (with DI)
  ├── Creates → GUI (receives controllers)
  └── Creates → Hardware (via factories)
```

**Implementation**:
1. Create `ApplicationContext` that builds all components
2. GUI receives pre-built controllers via constructor
3. Controllers receive hardware via constructor (already partially done)
4. Tests can build controllers without GUI

#### 3.3 Complete squid Module Independence

**Current**: 78 imports from `squid/` → `control/`

**Target**: Zero imports from modern to legacy

**Steps**:
1. Move camera implementations to `squid/camera/implementations/`
2. Move `microcontroller.py` to `squid/hardware/`
3. Define clean interface boundary
4. Legacy `control/` imports from `squid/`

---

## Architectural Refactoring Guide

### GUI Decoupling Pattern

**Before** (`gui_hcs.py`):
```python
class HighContentScreeningGui(QMainWindow):
    def __init__(self, microscope: Microscope):
        self.multipointController = QtMultiPointController(
            microscope=microscope,
            live_controller=LiveController(microscope=microscope, ...),
            # GUI builds entire dependency tree
        )
```

**After**:
```python
# application.py
class Application:
    def __init__(self, config: SystemConfig):
        self.hardware = HardwareFactory.build(config)
        self.controllers = ControllerFactory.build(self.hardware, config)
        self.gui = HighContentScreeningGui(self.controllers)

# gui_hcs.py
class HighContentScreeningGui(QMainWindow):
    def __init__(self, controllers: Controllers):
        # GUI only knows about controller interfaces
        self.multipointController = controllers.multipoint
        self.liveController = controllers.live
```

### Configuration Migration Pattern

**Before** (`_def.py`):
```python
# Global constants
CAMERA_TYPE = "Toupcam"
DEFAULT_EXPOSURE_TIME = 100
SUPPORT_LASER_AUTOFOCUS = True
```

**After** (`squid/config/`):
```python
class CameraConfig(BaseModel):
    type: CameraType
    default_exposure_ms: float = 100

class SystemConfig(BaseModel):
    camera: CameraConfig
    autofocus: AutofocusConfig

    class Config:
        extra = "forbid"  # Catch typos
```

### Thread Safety Pattern

**Before**:
```python
# Shared state without protection
self._current_capture_info = capture_info  # Race condition!
```

**After**:
```python
from threading import Lock
from dataclasses import dataclass
from typing import Optional

@dataclass
class ThreadSafeState:
    _lock: Lock = field(default_factory=Lock)
    _capture_info: Optional[CaptureInfo] = None

    def set_capture_info(self, info: CaptureInfo):
        with self._lock:
            self._capture_info = info

    def get_capture_info(self) -> Optional[CaptureInfo]:
        with self._lock:
            return self._capture_info
```

---

## Appendix: File Reference

### Critical Files for Refactoring

| File | Lines | Priority | Notes |
|------|-------|----------|-------|
| `control/_def.py` | 990 | Critical | Configuration monolith |
| `control/widgets.py` | 10,671 | Critical | Split into modules |
| `control/gui_hcs.py` | 1,186 | Critical | Decouple from controllers |
| `control/core/core.py` | 2,045 | High | Mixed responsibilities |
| `control/core/multi_point_worker.py` | 898 | High | Threading safety |
| `squid/config.py` | 568 | High | Remove legacy imports |

### Test Files

| File | Coverage | Notes |
|------|----------|-------|
| `tests/control/test_MultiPointController.py` | Good | Core acquisition logic |
| `tests/control/test_HighContentScreeningGui.py` | Minimal | Only basic creation |
| `tests/squid/test_config.py` | Good | Configuration validation |

### Camera Implementations (Duplication Targets)

All need thread mixin extraction:
- `control/camera_toupcam.py`
- `control/camera_flir.py`
- `control/camera_hamamatsu.py`
- `control/camera_ids.py`
- `control/camera_tucsen.py`
- `control/camera_photometrics.py`
- `control/camera_andor.py`
- `control/camera_TIS.py`
