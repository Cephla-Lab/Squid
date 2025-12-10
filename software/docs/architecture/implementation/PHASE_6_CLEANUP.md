# Phase 6: Cleanup

**Purpose:** Remove deprecated code, consolidate duplicates, and finalize the architecture. This is the final cleanup phase.

**Prerequisites:** Phases 1-5 complete

**Estimated Effort:** 1-2 days

---

## Overview

After the refactoring, some code will be dead or duplicated:
- `LiveService` - functionality moved to `LiveController`
- `TriggerService` - functionality moved to `LiveController`
- `MicroscopeModeService` - replaced by `MicroscopeModeController`
- Unused direct hardware references in controllers
- Duplicate event handling

This phase removes that code and ensures the architecture is clean.

---

## Task Checklist

### 6.1 Remove LiveService

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/live_service.py`

- [ ] Verify `LiveController` handles all `StartLiveCommand` and `StopLiveCommand` events
- [ ] Remove LiveService from `squid/services/__init__.py`
- [ ] Remove LiveService file
- [ ] Remove LiveService from `ApplicationContext`
- [ ] Search for any remaining references

**Verification before removal:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Verify LiveController subscribes to live events
grep -n "StartLiveCommand\|StopLiveCommand" control/core/display/live_controller.py

# Find all LiveService references
grep -rn "LiveService" --include="*.py" .
grep -rn "live_service" --include="*.py" .
```

**Removal commands:**
```bash
# Remove the file
rm squid/services/live_service.py

# Remove from __init__.py exports
# Edit squid/services/__init__.py to remove LiveService
```

**Update `squid/services/__init__.py`:**
```python
# Remove this line:
# from .live_service import LiveService

# Remove from __all__:
# "LiveService",
```

**Update `squid/application.py`:**
```python
# Remove LiveService creation:
# self._live_service = LiveService(...)

# Remove from wiring:
# live_service=self._live_service,
```

**Commit:** `refactor(services): Remove deprecated LiveService`

---

### 6.2 Remove TriggerService

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/trigger_service.py`

- [ ] Verify `LiveController` handles all trigger-related events
- [ ] Remove TriggerService from `squid/services/__init__.py`
- [ ] Remove TriggerService file
- [ ] Remove TriggerService from `ApplicationContext`
- [ ] Search for any remaining references

**Verification before removal:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Verify LiveController handles trigger events
grep -n "SetTriggerModeCommand\|SetTriggerFPSCommand" control/core/display/live_controller.py

# Find all TriggerService references
grep -rn "TriggerService" --include="*.py" .
grep -rn "trigger_service" --include="*.py" .
```

**Commit:** `refactor(services): Remove deprecated TriggerService`

---

### 6.3 Remove MicroscopeModeService

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/microscope_mode_service.py`

- [ ] Verify `MicroscopeModeController` handles all mode events
- [ ] Remove MicroscopeModeService from `squid/services/__init__.py`
- [ ] Remove MicroscopeModeService file
- [ ] Remove MicroscopeModeService from `ApplicationContext`
- [ ] Search for any remaining references

**Verification before removal:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Verify MicroscopeModeController handles events
grep -n "SetMicroscopeModeCommand" squid/controllers/microscope_mode_controller.py

# Find all MicroscopeModeService references
grep -rn "MicroscopeModeService" --include="*.py" .
grep -rn "microscope_mode_service" --include="*.py" .
```

**Commit:** `refactor(services): Remove deprecated MicroscopeModeService`

---

### 6.4 Remove Direct Hardware References from LiveController

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/display/live_controller.py`

After Phase 3, LiveController should use services. Remove any remaining direct hardware attributes.

- [ ] Remove `self.camera` attribute
- [ ] Remove `self.microscope` attribute
- [ ] Remove `self.microcontroller` attribute
- [ ] Update constructor to not accept hardware references

**Before (check for these):**
```python
def __init__(self, camera, microscope, ...):
    self.camera = camera  # REMOVE
    self.microscope = microscope  # REMOVE
```

**After:**
```python
def __init__(
    self,
    camera_service: CameraService,
    illumination_service: IlluminationService,
    stream_handler: StreamHandler,
    event_bus: EventBus,
    ...
):
    self._camera_service = camera_service
    self._illumination_service = illumination_service
    # No direct hardware references
```

**Verification:**
```bash
# Should return NO matches
grep -n "self\.camera\s*=" control/core/display/live_controller.py
grep -n "self\.microscope\s*=" control/core/display/live_controller.py
```

**Commit:** `refactor(live_controller): Remove direct hardware attributes`

---

### 6.5 Remove Direct Hardware References from MultiPointWorker

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

After Phase 4, MultiPointWorker should use services. Remove any remaining direct hardware attributes.

- [ ] Remove `self.camera` attribute
- [ ] Remove `self.stage` attribute
- [ ] Remove `self.microcontroller` attribute
- [ ] Remove `self.liveController` attribute

**Verification:**
```bash
# Should return NO matches (except self._piezo which is allowed)
grep -n "self\.camera\s*=" control/core/acquisition/multi_point_worker.py
grep -n "self\.stage\s*=" control/core/acquisition/multi_point_worker.py
grep -n "self\.microcontroller\s*=" control/core/acquisition/multi_point_worker.py
grep -n "self\.liveController\s*=" control/core/acquisition/multi_point_worker.py
```

**Commit:** `refactor(acquisition): Remove direct hardware attributes from worker`

---

### 6.6 Clean Up Unused Imports

Run import cleanup on all modified files.

- [ ] Clean up `squid/services/__init__.py`
- [ ] Clean up `squid/controllers/__init__.py`
- [ ] Clean up `control/core/display/live_controller.py`
- [ ] Clean up `control/core/acquisition/multi_point_worker.py`
- [ ] Clean up `squid/application.py`

**Use ruff to find unused imports:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Check for unused imports
ruff check --select F401 squid/
ruff check --select F401 control/

# Auto-fix unused imports
ruff check --select F401 --fix squid/
ruff check --select F401 --fix control/
```

**Commit:** `style: Remove unused imports`

---

### 6.7 Update Package Exports

Update `__init__.py` files to export new components and remove deprecated ones.

**File:** `/Users/wea/src/allenlab/Squid/software/squid/services/__init__.py`

```python
"""Service layer for Squid microscopy software."""

from .base import Service
from .camera_service import CameraService
from .stage_service import StageService
from .peripheral_service import PeripheralService
from .illumination_service import IlluminationService
from .fluidics_service import FluidicsService
# LiveService, TriggerService, MicroscopeModeService removed

__all__ = [
    "Service",
    "CameraService",
    "StageService",
    "PeripheralService",
    "IlluminationService",
    "FluidicsService",
]
```

**File:** `/Users/wea/src/allenlab/Squid/software/squid/controllers/__init__.py`

```python
"""Controller layer for Squid microscopy software."""

from .microscope_mode_controller import MicroscopeModeController
from .peripherals_controller import PeripheralsController

__all__ = [
    "MicroscopeModeController",
    "PeripheralsController",
]
```

**Commit:** `chore: Update package exports for new architecture`

---

### 6.8 Update ApplicationContext Wiring

**File:** `/Users/wea/src/allenlab/Squid/software/squid/application.py`

Ensure final wiring is clean and complete.

- [ ] All services are created
- [ ] All controllers are created
- [ ] Deprecated services are removed
- [ ] All components receive EventBus

**Target ApplicationContext structure:**
```python
class ApplicationContext:
    """Central application context - creates and wires all components."""

    def __init__(self, microscope: Microscope, config: dict):
        # Event bus
        self._event_bus = EventBus()

        # Hardware references (from Microscope)
        self._microscope = microscope

        # Services (thin wrappers around hardware)
        self._camera_service = CameraService(
            microscope.camera,
            self._event_bus,
        )
        self._stage_service = StageService(
            microscope.stage,
            self._event_bus,
        )
        self._peripheral_service = PeripheralService(
            microscope.low_level_drivers.microcontroller,
            self._event_bus,
        )
        self._illumination_service = IlluminationService(
            microscope.illumination_controller,
            microscope.addons,
            self._event_bus,
        )

        # Controllers (orchestration and state management)
        self._microscope_mode_controller = MicroscopeModeController(
            self._camera_service,
            self._illumination_service,
            config.get("channel_configurations", {}),
            self._event_bus,
        )
        self._peripherals_controller = PeripheralsController(
            microscope.addons.objective_changer,
            microscope.addons.spinning_disk,
            microscope.low_level_drivers.piezo,
            config.get("objective_store"),
            self._event_bus,
        )

        # Live view controller
        self._stream_handler = StreamHandler()
        self._live_controller = LiveController(
            self._camera_service,
            self._illumination_service,
            self._stream_handler,
            self._event_bus,
        )

        # Acquisition controller
        self._multi_point_controller = MultiPointController(
            self._camera_service,
            self._stage_service,
            self._peripheral_service,
            self._illumination_service,
            self._microscope_mode_controller,
            self._event_bus,
        )

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def camera_service(self) -> CameraService:
        return self._camera_service

    @property
    def stage_service(self) -> StageService:
        return self._stage_service

    @property
    def live_controller(self) -> LiveController:
        return self._live_controller

    # ... other properties
```

**Commit:** `refactor(app): Finalize ApplicationContext wiring`

---

### 6.9 Run Full Test Suite

- [ ] Run all unit tests
- [ ] Run all integration tests
- [ ] Fix any failures

**Commands:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Run all tests
NUMBA_DISABLE_JIT=1 pytest tests/ -v

# Run with coverage
NUMBA_DISABLE_JIT=1 pytest tests/ --cov=squid --cov=control --cov-report=term-missing

# Run only unit tests
NUMBA_DISABLE_JIT=1 pytest tests/unit/ -v

# Run only integration tests
NUMBA_DISABLE_JIT=1 pytest tests/integration/ -v
```

**Commit:** `test: Fix any test failures after cleanup`

---

### 6.10 Manual Smoke Test

- [ ] Start application in simulation mode
- [ ] Test live view start/stop
- [ ] Test stage navigation
- [ ] Test camera settings changes
- [ ] Test channel switching
- [ ] Test acquisition (if possible in simulation)

**Commands:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Start in simulation mode
python main_hcs.py --simulation
```

**Manual test checklist:**
1. [ ] Application starts without errors
2. [ ] Live view button toggles live preview
3. [ ] Stage jog buttons move stage (check position display updates)
4. [ ] Exposure time changes apply
5. [ ] Channel switching works (dropdown or channel buttons)
6. [ ] No Python exceptions in console during normal operation

**Commit:** `docs: Record smoke test results` (if any fixes needed)

---

### 6.11 Update Documentation

- [ ] Update README if needed
- [ ] Update architecture diagrams if needed
- [ ] Mark implementation phases as complete in `00_MASTER_OVERVIEW.md`

**Update `00_MASTER_OVERVIEW.md` completion status:**
```markdown
## Progress Tracking

| Phase | Status | Date Completed |
|-------|--------|----------------|
| Phase 1: Establish Boundaries | ✅ Complete | YYYY-MM-DD |
| Phase 2: Create Infrastructure | ✅ Complete | YYYY-MM-DD |
| Phase 3: Service-Controller Merge | ✅ Complete | YYYY-MM-DD |
| Phase 4: Acquisition Service Usage | ✅ Complete | YYYY-MM-DD |
| Phase 5: Widget Updates | ✅ Complete | YYYY-MM-DD |
| Phase 6: Cleanup | ✅ Complete | YYYY-MM-DD |
```

**Commit:** `docs: Mark architecture refactoring complete`

---

## Final Verification Checklist

Before declaring the refactoring complete:

### Architecture Verification
- [ ] No deprecated services remain (LiveService, TriggerService, MicroscopeModeService)
- [ ] All widgets use EventBus only
- [ ] All controllers use services (not direct hardware)
- [ ] Event flow follows: Widget → Command → Controller/Service → State → Widget

### Code Quality
- [ ] No unused imports: `ruff check --select F401`
- [ ] No linting errors: `ruff check`
- [ ] Code formatted: `ruff format --check`

### Testing
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Manual smoke test passes
- [ ] Coverage meets targets (90%+ services, 80%+ controllers, 70%+ widgets)

### Final grep verification (all should return 0 matches):
```bash
cd /Users/wea/src/allenlab/Squid/software

echo "=== Final Architecture Verification ==="

echo "Widgets with direct stage access:"
grep -rn "self\.stage\." control/widgets/ | wc -l

echo "Widgets with direct camera access:"
grep -rn "self\.camera\." control/widgets/ | wc -l

echo "Widgets with direct liveController access:"
grep -rn "\.liveController\." control/widgets/ | wc -l

echo "Controllers with direct camera access (should be 0 except StreamHandler):"
grep -rn "self\.camera\." control/core/ | grep -v stream_handler | wc -l

echo "MultiPointWorker with direct hardware (should be 0, piezo exception allowed):"
grep -n "self\.camera\.\|self\.stage\.\|self\.microcontroller\.\|self\.liveController\." control/core/acquisition/multi_point_worker.py | wc -l

echo "LiveService references (should be 0):"
grep -rn "LiveService\|live_service" --include="*.py" . | grep -v test | grep -v ".pyc" | wc -l

echo "TriggerService references (should be 0):"
grep -rn "TriggerService\|trigger_service" --include="*.py" . | grep -v test | grep -v ".pyc" | wc -l

echo "MicroscopeModeService references (should be 0):"
grep -rn "MicroscopeModeService\|microscope_mode_service" --include="*.py" . | grep -v test | grep -v ".pyc" | wc -l
```

---

## Commit Summary

| Order | Commit Message | Files |
|-------|----------------|-------|
| 1 | `refactor(services): Remove deprecated LiveService` | `squid/services/` |
| 2 | `refactor(services): Remove deprecated TriggerService` | `squid/services/` |
| 3 | `refactor(services): Remove deprecated MicroscopeModeService` | `squid/services/` |
| 4 | `refactor(live_controller): Remove direct hardware attributes` | `live_controller.py` |
| 5 | `refactor(acquisition): Remove direct hardware attributes from worker` | `multi_point_worker.py` |
| 6 | `style: Remove unused imports` | Various |
| 7 | `chore: Update package exports for new architecture` | `__init__.py` files |
| 8 | `refactor(app): Finalize ApplicationContext wiring` | `application.py` |
| 9 | `test: Fix any test failures after cleanup` | Tests |
| 10 | `docs: Mark architecture refactoring complete` | Docs |

---

## Architecture Summary (After Refactoring)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           GUI Layer                                      │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│   │ LiveControl │  │ Navigation  │  │  Camera     │  │  Autofocus  │   │
│   │   Widget    │  │   Widget    │  │  Settings   │  │   Widget    │   │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│          │                │                │                │          │
│          └────────────────┴────────────────┴────────────────┘          │
│                                    │                                    │
│                            publish/subscribe                            │
│                                    ▼                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                          EventBus                                        │
│   Commands: StartLive, StopLive, MoveStage, SetExposure, ...           │
│   States: LiveStateChanged, PositionChanged, SettingsChanged, ...       │
├─────────────────────────────────────────────────────────────────────────┤
│                                    │                                    │
│                            subscribe/publish                            │
│                                    ▼                                    │
│                       Controller Layer                                   │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐        │
│   │ LiveController  │  │ MicroscopeMode  │  │  Peripherals    │        │
│   │ (live view)     │  │   Controller    │  │   Controller    │        │
│   └────────┬────────┘  └────────┬────────┘  └────────┬────────┘        │
│            │                    │                    │                  │
│            └────────────────────┴────────────────────┘                  │
│                                 │                                       │
│                           uses services                                 │
│                                 ▼                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                         Service Layer                                    │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                 │
│   │ CameraService│  │ StageService │  │ Peripheral   │                 │
│   │              │  │              │  │   Service    │                 │
│   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                 │
│          │                 │                 │                          │
│          └─────────────────┴─────────────────┘                          │
│                            │                                            │
│                      wraps hardware                                     │
│                            ▼                                            │
├─────────────────────────────────────────────────────────────────────────┤
│                        Hardware Layer                                    │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│   │  Camera  │  │  Stage   │  │   MCU    │  │  Piezo   │               │
│   └──────────┘  └──────────┘  └──────────┘  └──────────┘               │
└─────────────────────────────────────────────────────────────────────────┘

Data Plane (not shown): StreamHandler routes frames directly
Camera → StreamHandler → QtStreamHandler → Display Widgets
```

---

## Congratulations! 🎉

If you've completed all phases and verification checks pass, the architecture refactoring is complete!

**Key achievements:**
1. ✅ Clean separation between GUI, Controller, Service, and Hardware layers
2. ✅ Event-driven communication (loose coupling)
3. ✅ Thread-safe service layer
4. ✅ Testable components with dependency injection
5. ✅ No direct hardware access in widgets or controllers
6. ✅ ~90% code reuse (existing logic preserved)

**Next steps (optional):**
- Add more comprehensive integration tests
- Implement additional controllers (e.g., AutofocusController)
- Add telemetry/logging to EventBus for debugging
- Create developer documentation for extending the system
