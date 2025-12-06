# Squid Modernization Roadmap

This document outlines the modernization work **after** the service layer is complete. It assumes `SERVICE_LAYER_COMPLETION_PLAN.md` has been implemented and tested.

## Prerequisites

Before starting this work, verify:
```bash
# All tests pass
pytest tests/ -v

# No direct hardware calls in widgets (except read-only getters)
grep -r "self\.camera\." control/widgets/*.py | grep -v "get_" | grep -v "_service" | grep -v "#"
grep -r "self\.stage\." control/widgets/*.py | grep -v "get_" | grep -v "_service" | grep -v "#"
# Should return minimal results

# Simulation mode works end-to-end
python main_hcs.py --simulation
```

---

## Current State (Post-Service Layer)

### What's Working
- ✅ Services exist: `CameraService`, `StageService`, `PeripheralService`
- ✅ All widgets use services for hardware operations
- ✅ State events propagate changes (ExposureTimeChanged, etc.)
- ✅ Simulated hardware for offline testing

### What's Still Problematic
- ❌ Global `_def.py` with 1001 lines of module-level state
- ❌ Config loaded at import time, no runtime changes
- ❌ GUI still receives and unpacks full `Microscope` object
- ❌ Mixed Qt Signals and EventBus patterns
- ❌ 49+ files import `control._def` directly

---

## Phase 1: GUI Cleanup (2-3 hours)

**Goal:** Remove unused hardware references from GUI now that widgets use services.

### Why This is Easy Now
With service layer complete, GUI's direct hardware references are dead code:
```python
# gui_hcs.py lines 103-150 - these are now unused
self.stage = microscope.stage           # widgets use StageService
self.camera = microscope.camera         # widgets use CameraService
self.microcontroller = ...              # widgets use PeripheralService
```

### Task 1.1: Audit GUI Hardware References
**File:** `control/gui_hcs.py`

Find all `self.{hardware}` references and verify they're unused:
```bash
grep -n "self\.stage\|self\.camera\|self\.microcontroller" control/gui_hcs.py
```

### Task 1.2: Remove Unused Unpacking
**File:** `control/gui_hcs.py`

**Before (lines 103-150):**
```python
def __init__(self, microscope, services, ...):
    self.stage = microscope.stage
    self.camera = microscope.camera
    self.microcontroller = microscope.low_level_drivers.microcontroller
    self.xlight = microscope.addons.xlight
    # ... 17 more attributes
```

**After:**
```python
def __init__(self, services: ServiceRegistry, ...):
    self._services = services
    # Widgets receive services, not hardware
```

### Task 1.3: Update Widget Instantiation
Widgets should only receive services:
```python
# Before:
self.cameraSettingsWidget = CameraSettingsWidget(
    camera=self.camera,
    camera_service=self._services.get('camera'),
)

# After:
self.cameraSettingsWidget = CameraSettingsWidget(
    camera_service=self._services.get('camera'),
)
```

### Task 1.4: Update main_hcs.py Entry Point
```python
# Before:
context = ApplicationContext(simulation=args.simulation)
win = HighContentScreeningGui(
    microscope=context.microscope,
    services=context.services,
)

# After:
context = ApplicationContext(simulation=args.simulation)
win = HighContentScreeningGui(
    services=context.services,
)
```

**Commit:** `Remove unused hardware references from GUI`

---

## Phase 2: Config Service (1-2 weeks)

**Goal:** Replace global `_def.py` with injectable configuration.

### Why This Matters
Currently:
- Config is loaded at import time via `control._def`
- 49+ files import `_def` directly
- No runtime config changes possible
- Can't run multiple instances with different configs
- Testing requires mocking global state

### Architecture

```
                    ┌─────────────────┐
                    │  ConfigService  │
                    │  (injectable)   │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ CameraConfig  │  │  StageConfig    │  │  SystemConfig   │
└───────────────┘  └─────────────────┘  └─────────────────┘
```

### Task 2.1: Create ConfigService Class
**File:** `squid/services/config_service.py` (NEW)

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import configparser

from squid.config import CameraConfig, StageConfig, FilterWheelConfig
from squid.services.base import BaseService
from squid.events import EventBus, ConfigChanged


@dataclass
class SystemConfig:
    """Complete system configuration."""
    camera: CameraConfig
    stage: StageConfig
    filter_wheel: Optional[FilterWheelConfig]
    # Add other configs as needed

    # Hardware flags (from _def.py)
    use_prior_stage: bool = False
    enable_laser_autofocus: bool = False
    enable_tracking: bool = False
    # ... etc


class ConfigService(BaseService):
    """
    Service for configuration management.

    Replaces global _def.py with injectable configuration.
    Supports runtime config changes with event notification.
    """

    def __init__(self, event_bus: EventBus, config_path: Optional[Path] = None):
        super().__init__(event_bus)
        self._config: Optional[SystemConfig] = None
        self._config_path = config_path

        if config_path:
            self.load_from_file(config_path)

    def load_from_file(self, path: Path) -> None:
        """Load configuration from INI file."""
        parser = configparser.ConfigParser()
        parser.read(path)

        self._config = self._parse_config(parser)
        self._config_path = path

        self.publish(ConfigChanged(source=str(path)))

    def _parse_config(self, parser: configparser.ConfigParser) -> SystemConfig:
        """Parse INI into typed config objects."""
        # Implementation mirrors current _def.py parsing
        # but returns typed objects instead of setting globals
        ...

    @property
    def camera(self) -> CameraConfig:
        return self._config.camera

    @property
    def stage(self) -> StageConfig:
        return self._config.stage

    @property
    def system(self) -> SystemConfig:
        return self._config

    def update_camera_config(self, **kwargs) -> None:
        """Update camera config at runtime."""
        for key, value in kwargs.items():
            setattr(self._config.camera, key, value)
        self.publish(ConfigChanged(source="runtime"))
```

**Test first** (`tests/unit/squid/services/test_config_service.py`):
```python
def test_load_from_file():
    bus = EventBus()
    service = ConfigService(bus, config_path=Path("test_config.ini"))

    assert service.camera is not None
    assert service.stage is not None

def test_publishes_config_changed():
    bus = EventBus()
    service = ConfigService(bus)

    received = []
    bus.subscribe(ConfigChanged, lambda e: received.append(e))

    service.load_from_file(Path("test_config.ini"))

    assert len(received) == 1
```

**Commit:** `Add ConfigService for injectable configuration`

---

### Task 2.2: Add ConfigChanged Event
**File:** `squid/events.py`

```python
@dataclass
class ConfigChanged(Event):
    """Notification that configuration changed."""
    source: str  # "file", "runtime", etc.
```

**Commit:** `Add ConfigChanged event`

---

### Task 2.3: Update ApplicationContext to Use ConfigService
**File:** `squid/application.py`

```python
class ApplicationContext:
    def __init__(
        self,
        simulation: bool = False,
        config_path: Optional[Path] = None,
    ):
        self._build_config_service(config_path)
        self._build_microscope()
        self._build_controllers()
        self._build_services()

    def _build_config_service(self, config_path: Optional[Path]):
        from squid.services.config_service import ConfigService

        if config_path is None:
            config_path = self._find_default_config()

        self._config_service = ConfigService(event_bus, config_path)

    def _build_microscope(self):
        # Pass config to Microscope instead of relying on _def.py
        self._microscope = Microscope.build_from_config(
            config=self._config_service.system,
            simulated=self._simulation,
        )
```

**Commit:** `Integrate ConfigService into ApplicationContext`

---

### Task 2.4: Add Microscope.build_from_config()
**File:** `control/microscope.py`

```python
@classmethod
def build_from_config(
    cls,
    config: SystemConfig,
    simulated: bool = False,
) -> "Microscope":
    """
    Build microscope from explicit config (no globals).

    This is the new preferred method. build_from_global_config()
    is deprecated but kept for backwards compatibility.
    """
    # Use config instead of _def.py
    stage = get_stage(
        stage_config=config.stage,
        simulated=simulated,
    )

    camera = get_camera(
        config=config.camera,
        simulated=simulated,
    )

    # ... etc
```

**Commit:** `Add Microscope.build_from_config() method`

---

### Task 2.5: Migrate _def.py Constants to Config Classes
**File:** `control/_def.py` → `squid/config/system.py`

This is the largest task. Strategy:

1. **Identify categories** of constants in `_def.py`:
   - Hardware flags (USE_PRIOR_STAGE, ENABLE_TRACKING, etc.)
   - Microcontroller constants (CMD_SET, AXIS, MCU_PINS)
   - Acquisition defaults
   - UI defaults

2. **Create typed config classes** for each category:
   ```python
   @dataclass
   class HardwareFlags:
       use_prior_stage: bool = False
       enable_laser_autofocus: bool = False
       enable_tracking: bool = False

   @dataclass
   class MicrocontrollerConfig:
       serial_number: Optional[str] = None
       cmd_set: Dict[str, int] = field(default_factory=dict)
   ```

3. **Keep _def.py as compatibility shim** (deprecated):
   ```python
   # control/_def.py (deprecated - use ConfigService)
   import warnings
   from squid.config import get_system_config

   warnings.warn(
       "_def.py is deprecated. Use ConfigService instead.",
       DeprecationWarning
   )

   # Load from default config for backwards compatibility
   _config = get_system_config()
   USE_PRIOR_STAGE = _config.hardware.use_prior_stage
   # ... etc
   ```

**Commits:**
- `Add HardwareFlags config class`
- `Add MicrocontrollerConfig class`
- `Add AcquisitionConfig class`
- `Deprecate _def.py, add compatibility shim`

---

### Task 2.6: Update Imports Incrementally
**Files:** 49+ files that import `control._def`

Strategy: Update files in groups by feature area:

| Group | Files | Approach |
|-------|-------|----------|
| Services | `squid/services/*.py` | Inject config via constructor |
| Microscope | `control/microscope.py` | Use `build_from_config()` |
| Peripherals | `control/peripherals/**` | Pass config to factories |
| Widgets | `control/widgets/*.py` | Get config from services |
| Controllers | `control/core/*.py` | Inject via ApplicationContext |

Example migration:
```python
# Before:
from control._def import USE_PRIOR_STAGE, STAGE_PID_CONFIG

# After:
# Config passed via constructor or accessed via service
def __init__(self, config: StageConfig):
    self._use_prior = config.use_prior_stage
```

**Commits:** One per file group

---

## Phase 3: EventBus Migration (1 week)

**Goal:** Replace Qt Signals with EventBus for consistent messaging.

### Why This Matters
Currently mixed patterns:
- Services use EventBus (good)
- Some widgets use EventBus (good)
- Many widgets use Qt Signals (inconsistent)
- Controllers use Qt Signals (hard to test)

### Task 3.1: Audit Qt Signal Usage
```bash
grep -r "Signal\|\.emit\|\.connect" control/ --include="*.py" | grep -v __pycache__
```

Categorize results:
- **Keep:** UI-only signals (button clicks, value changes)
- **Migrate:** Cross-component communication
- **Remove:** Unused signals

### Task 3.2: Define Missing Events
**File:** `squid/events.py`

Add events to replace Qt signals:
```python
# Acquisition events
@dataclass
class AcquisitionProgress(Event):
    current: int
    total: int
    message: str

@dataclass
class LiveViewStarted(Event):
    configuration: Optional[str] = None

@dataclass
class LiveViewStopped(Event):
    pass

# Autofocus events
@dataclass
class AutofocusStarted(Event):
    pass

@dataclass
class AutofocusCompleted(Event):
    success: bool
    z_mm: float

# Tracking events
@dataclass
class TrackingStarted(Event):
    pass

@dataclass
class TrackingUpdate(Event):
    x_mm: float
    y_mm: float
```

**Commit:** `Add events for controller state changes`

---

### Task 3.3: Migrate Controllers to EventBus
**Files:** `control/core/live_controller.py`, `control/core/multi_point_controller.py`

**Before:**
```python
class LiveController(QObject):
    signal_live_started = Signal()
    signal_live_stopped = Signal()

    def start_live(self):
        ...
        self.signal_live_started.emit()
```

**After:**
```python
class LiveController:  # No longer QObject
    def __init__(self, event_bus: EventBus, ...):
        self._event_bus = event_bus

    def start_live(self):
        ...
        self._event_bus.publish(LiveViewStarted())
```

**Commit:** `Migrate LiveController to EventBus`

---

### Task 3.4: Update GUI Subscriptions
**File:** `control/gui_hcs.py`

**Before:**
```python
self.liveController.signal_live_started.connect(self.on_live_started)
```

**After:**
```python
event_bus.subscribe(LiveViewStarted, self.on_live_started)
```

**Commit:** `Update GUI to subscribe to EventBus`

---

## Phase 4: Controller Interfaces (Optional)

**Goal:** Add abstract base classes for controllers.

### Why This is Now Optional
With service layer complete:
- Widgets don't call controllers directly
- Controllers are internal to ApplicationContext
- Mocking happens at service level

### If Needed Later
```python
# squid/abc.py

class AbstractLiveController(ABC):
    @abstractmethod
    def start_live(self, configuration: Optional[str] = None) -> None: ...

    @abstractmethod
    def stop_live(self) -> None: ...

    @abstractmethod
    def is_live(self) -> bool: ...


class AbstractMultiPointController(ABC):
    @abstractmethod
    def start_acquisition(self, config: AcquisitionConfig) -> None: ...

    @abstractmethod
    def stop_acquisition(self) -> None: ...

    @abstractmethod
    def is_acquiring(self) -> bool: ...
```

---

## Testing Strategy

### Unit Tests for ConfigService
```python
# Test config loading
def test_load_valid_config(tmp_path):
    config_file = tmp_path / "test.ini"
    config_file.write_text("[camera]\ntype = TOUPCAM\n")

    service = ConfigService(EventBus(), config_file)

    assert service.camera.camera_type == CameraType.TOUPCAM

# Test runtime updates
def test_runtime_config_change():
    service = ConfigService(EventBus())

    service.update_camera_config(default_exposure_ms=100.0)

    assert service.camera.default_exposure_ms == 100.0

# Test event publishing
def test_config_change_publishes_event():
    bus = EventBus()
    received = []
    bus.subscribe(ConfigChanged, lambda e: received.append(e))

    service = ConfigService(bus)
    service.load_from_file(Path("config.ini"))

    assert len(received) == 1
```

### Integration Tests
```python
def test_application_with_custom_config(tmp_path):
    config_file = tmp_path / "custom.ini"
    # Write custom config...

    context = ApplicationContext(
        simulation=True,
        config_path=config_file,
    )

    assert context.config.camera.camera_type == CameraType.SIMULATED
```

---

## Verification Checklist

### After Phase 1 (GUI Cleanup)
```bash
# GUI doesn't store hardware references
grep -n "self\.stage\|self\.camera\|self\.microcontroller" control/gui_hcs.py
# Should return 0 results (or only in comments)
```

### After Phase 2 (Config Service)
```bash
# _def.py imports are deprecated
grep -r "from control._def import\|from control import _def" --include="*.py" | wc -l
# Should decrease over time, eventually 0

# Config is injectable
python -c "
from squid.application import ApplicationContext
ctx = ApplicationContext(simulation=True, config_path='custom.ini')
print('Config loaded:', ctx.config.camera.camera_type)
"
```

### After Phase 3 (EventBus Migration)
```bash
# No Qt signals for cross-component communication
grep -r "Signal()" control/core/*.py | wc -l
# Should be 0 or minimal (UI-only signals)
```

---

## Summary

| Phase | Effort | Dependency | Value |
|-------|--------|------------|-------|
| 1. GUI Cleanup | 2-3 hours | Service Layer | Clean separation |
| 2. Config Service | 1-2 weeks | Phase 1 | Runtime config, testability |
| 3. EventBus Migration | 1 week | Phase 2 | Consistent messaging |
| 4. Controller ABCs | Optional | - | Interface contracts |

**Total effort:** ~3-4 weeks after service layer is complete.

---

## Files Summary

### New Files
- `squid/services/config_service.py`
- `squid/config/system.py`
- `tests/unit/squid/services/test_config_service.py`

### Modified Files
- `squid/application.py` - Use ConfigService
- `squid/events.py` - Add ConfigChanged and controller events
- `control/microscope.py` - Add build_from_config()
- `control/gui_hcs.py` - Remove hardware unpacking
- `control/_def.py` - Deprecate, add compatibility shim
- `control/core/live_controller.py` - Use EventBus
- `control/core/multi_point_controller.py` - Use EventBus
- 49+ files - Update _def imports incrementally
