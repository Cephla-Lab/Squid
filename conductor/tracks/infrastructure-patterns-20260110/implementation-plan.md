# Infrastructure Patterns: Backend Refactoring Plan

## Goal

Systematically address antipatterns across the backend codebase to improve maintainability, testability, and code clarity. Use Pythonic approaches: pure functions, dataclasses, composition over inheritance.

---

## Priority Overview

| Priority | Target | Current State | Goal |
|----------|--------|---------------|------|
| **P0** | Event subscription boilerplate | 45 files with manual subscribe() | `@handles` decorator |
| **P0** | ScanCoordinates | 1,414 lines, 59 methods | ~400 lines + pure function modules |
| **P0** | LaserAutofocusController | 1,221 lines, 47 methods | ~700 lines + laser_spot.py |
| **P1** | Mode gate duplication | 29 occurrences across services | Single decorator |
| **P1** | Feature flag access | 39 files with scattered _def imports | Centralized registry |
| **P1** | MultiPointWorker | 1,850 lines, 53 methods | Extract more domain objects |
| **P1** | MultiPointController | 19 constructor params, state duplication | AcquisitionConfig dataclass |
| **P2** | Microscope.py | 633 lines, factory + hardware access | Split concerns |
| **P2** | Wildcard imports | 12+ files with `from _def import *` | Explicit imports |

---

## P0: Critical Refactors

### 0.1 Event Subscription Decorator

**Problem:** 45 files with manual `event_bus.subscribe()` boilerplate

**Solution:** Add to `software/src/squid/core/events.py`:

```python
def handles(*event_types: Type[Event]):
    """Mark a method as handling specific event types."""
    def decorator(method):
        method._handles_events = list(event_types)
        return method
    return decorator


def auto_subscribe(obj: Any, event_bus: EventBus) -> List[Tuple[Type[Event], Callable]]:
    """Subscribe all @handles-decorated methods on obj to event_bus."""
    subscriptions = []
    for name in dir(obj):
        method = getattr(obj, name, None)
        if callable(method) and hasattr(method, '_handles_events'):
            for event_type in method._handles_events:
                event_bus.subscribe(event_type, method)
                subscriptions.append((event_type, method))
    return subscriptions


def auto_unsubscribe(subscriptions: List[Tuple[Type[Event], Callable]], event_bus: EventBus) -> None:
    """Unsubscribe handlers previously registered via auto_subscribe."""
    for event_type, handler in subscriptions:
        event_bus.unsubscribe(event_type, handler)
```

**Migration targets (by subscription count):**
- ScanCoordinates: 15 subscriptions
- WellplateMultiPointWidget: 16 subscriptions
- LaserAutofocusController: 10 subscriptions
- FlexibleMultiPointWidget: 9 subscriptions

---

### 0.2 ScanCoordinates Decomposition

**Problem:** 1,414-line god object mixing data storage, grid generation, geometry, wellplate logic, and 15+ event handlers

**Solution:** Extract to package with pure functions:

```
software/src/squid/backend/managers/scan_coordinates/
├── __init__.py              # Re-exports ScanCoordinates
├── scan_coordinates.py      # Lean coordinator (~400 lines)
├── grid.py                  # Pure grid generation functions (~200 lines)
├── geometry.py              # Pure geometry functions (~50 lines)
└── wellplate.py             # Wellplate coordinate helpers (~100 lines)
```

**grid.py:**
```python
@dataclass
class GridConfig:
    fov_width_mm: float
    fov_height_mm: float
    overlap_percent: float = 10.0
    fov_pattern: str = "S-Pattern"

def generate_rectangular_grid(center_x, center_y, width_mm, height_mm, config: GridConfig) -> List[Tuple[float, float]]: ...
def generate_square_grid(center_x, center_y, scan_size_mm, config: GridConfig) -> List[Tuple[float, float]]: ...
def generate_circular_grid(center_x, center_y, diameter_mm, config: GridConfig) -> List[Tuple[float, float]]: ...
def generate_polygon_grid(vertices, config: GridConfig) -> List[Tuple[float, float]]: ...
def apply_s_pattern(coords, row_axis: int = 1) -> List[Tuple[float, float]]: ...
```

**geometry.py:**
```python
def point_in_polygon(x: float, y: float, vertices: np.ndarray) -> bool: ...
def point_in_circle(x, y, center_x, center_y, radius) -> bool: ...
def fov_corners_in_circle(x, y, fov_width, fov_height, center_x, center_y, radius) -> bool: ...
```

**wellplate.py:**
```python
def well_id_to_position(well_id, a1_x_mm, a1_y_mm, well_spacing_mm, offset_x_mm=0, offset_y_mm=0) -> Tuple[float, float]: ...
def row_col_to_well_id(row: int, col: int) -> str: ...
def parse_well_range(well_range: str) -> List[Tuple[int, int]]: ...
```

---

### 0.3 LaserSpotDetector Extraction

**Problem:** LaserAutofocusController (1,221 lines) mixes hardware control with image processing

**Solution:** Extract pure image processing to `software/src/squid/backend/processing/laser_spot.py`:

```python
@dataclass
class SpotDetectionResult:
    x: float
    y: float
    snr: float
    intensity: float
    background: float

def detect_spot(image: np.ndarray, params: Dict[str, Any], filter_sigma: float = 0.0) -> Optional[SpotDetectionResult]: ...
def compute_displacement(spot_x: float, reference_x: float, pixel_to_um: float) -> float: ...
def compute_correlation(current_crop: np.ndarray, reference_crop: np.ndarray) -> float: ...
def extract_spot_crop(image, spot_x, spot_y, crop_size) -> Tuple[np.ndarray, Tuple[int, int, int, int]]: ...
def remove_background(image: np.ndarray, kernel_size: int = 50) -> np.ndarray: ...
```

---

## P1: High Priority Refactors

### 1.1 Mode Gate Decorator

**Problem:** 29 occurrences of duplicated pattern across 5 service files:
```python
if self._blocked_for_ui_hardware_commands():
    self._log.info("Ignoring %s due to global mode gate", type(event).__name__)
    return
```

**Files affected:**
- `camera_service.py`: 8 occurrences
- `stage_service.py`: 7 occurrences
- `peripheral_service.py`: 8 occurrences
- `filter_wheel_service.py`: 2 occurrences
- `piezo_service.py`: 2 occurrences

**Solution:** Add decorator to `software/src/squid/backend/services/base.py`:

```python
def gated_command(method: Callable) -> Callable:
    """Decorator that skips command handler when mode gate is active."""
    @functools.wraps(method)
    def wrapper(self, event: Event) -> Any:
        if self._blocked_for_ui_hardware_commands():
            self._log.debug("Ignoring %s due to mode gate", type(event).__name__)
            return None
        return method(self, event)
    return wrapper
```

**Usage:**
```python
class CameraService(BaseService):
    @gated_command
    def _on_set_exposure(self, cmd: SetCameraExposureCommand) -> None:
        self.set_exposure_time(cmd.exposure_time_ms)
```

---

### 1.2 Feature Flags Registry

**Problem:** 47 flags scattered in `_def.py` with inconsistent access (39 files)

**Solution:** Create `software/src/squid/core/config/feature_flags.py`:

```python
@dataclass(frozen=True)
class FeatureFlag:
    name: str
    category: str
    default: bool
    description: str

class FeatureFlags:
    """Centralized feature flag access with validation."""

    # Categories
    HARDWARE = "hardware"
    UI = "ui"
    ACQUISITION = "acquisition"
    DEBUG = "debug"

    _flags: Dict[str, FeatureFlag]
    _values: Dict[str, bool]

    def __init__(self):
        self._flags = {}
        self._values = {}
        self._load_from_def()

    def _load_from_def(self) -> None:
        """Load flag values from _def.py for backwards compatibility."""
        import _def
        for flag in self._flags.values():
            self._values[flag.name] = getattr(_def, flag.name, flag.default)

    def is_enabled(self, flag_name: str) -> bool:
        """Check if flag is enabled with validation warning for unknown flags."""
        if flag_name not in self._flags:
            _log.warning(f"Unknown feature flag: {flag_name}")
        return self._values.get(flag_name, False)

    def register(self, name: str, category: str, default: bool, description: str) -> None:
        """Register a feature flag."""
        self._flags[name] = FeatureFlag(name, category, default, description)


# Global instance
_feature_flags: Optional[FeatureFlags] = None

def get_feature_flags() -> FeatureFlags:
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = FeatureFlags()
    return _feature_flags
```

**Usage:**
```python
from squid.core.config.feature_flags import get_feature_flags

flags = get_feature_flags()
if flags.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
    ...
```

---

### 1.3 MultiPointController State Consolidation

**Problem:**
- 19 constructor parameters
- State duplicated between controller and worker
- 20+ trivial setters without validation
- `build_params()` reconstructs state that already exists

**Solution:** Create immutable `AcquisitionConfig` dataclass:

```python
# software/src/squid/backend/controllers/multipoint/acquisition_config.py

@dataclass(frozen=True)
class GridConfig:
    nx: int = 1
    ny: int = 1
    dx_mm: float = 0.9
    dy_mm: float = 0.9

@dataclass(frozen=True)
class ZStackConfig:
    nz: int = 1
    delta_z_um: float = 1.5
    stacking_direction: str = "FROM CENTER"
    use_piezo: bool = False

@dataclass(frozen=True)
class TimingConfig:
    nt: int = 1
    dt_s: float = 0.0

@dataclass(frozen=True)
class FocusConfig:
    do_reflection_af: bool = False
    do_contrast_af: bool = False
    gen_focus_map: bool = False
    focus_map_dx_mm: float = 3.0
    focus_map_dy_mm: float = 3.0

@dataclass(frozen=True)
class AcquisitionConfig:
    """Immutable acquisition configuration snapshot."""
    grid: GridConfig
    zstack: ZStackConfig
    timing: TimingConfig
    focus: FocusConfig
    selected_channels: Tuple[str, ...]

    def validate(self) -> None:
        """Validate all cross-field constraints."""
        if self.grid.nx < 1 or self.grid.ny < 1:
            raise ValueError("Grid dimensions must be >= 1")
        if self.zstack.nz < 1:
            raise ValueError("Z-stack count must be >= 1")
        # ... more validation
```

**Refactor controller:**
```python
class MultiPointController:
    def __init__(self, ...):
        self._config = AcquisitionConfig(...)  # Single source of truth

    def update_config(self, **updates) -> None:
        """Update config with validation. Rejects updates during acquisition."""
        if self.acquisition_in_progress():
            raise RuntimeError("Cannot modify config during acquisition")

        # Build new config with updates
        new_config = self._config.replace(**updates)
        new_config.validate()
        self._config = new_config
```

---

### 1.4 Focus Map Generation - Move to AutofocusExecutor

**Problem:** 82 lines of focus map generation in MultiPointController (lines 804-886)

**Solution:** Move to `AutofocusExecutor`:

```python
# In focus_operations.py
class AutofocusExecutor:
    def generate_focus_map_for_acquisition(
        self,
        scan_bounds: Dict[str, Tuple[float, float]],
        dx_mm: float,
        dy_mm: float,
    ) -> bool:
        """Generate AF map for acquisition region. Returns success."""
        if not self._af_controller:
            return False

        # Calculate grid (moved from controller)
        corners = self._calculate_focus_map_corners(scan_bounds, dx_mm, dy_mm)

        # Generate map
        return self._af_controller.gen_focus_map(corners)
```

---

### 1.5 Service Dependencies Dataclass

**Problem:** MultiPointWorker takes 21+ constructor parameters (12 optional services)

**Solution:** Group services into dataclass:

```python
# software/src/squid/backend/controllers/multipoint/dependencies.py

@dataclass
class AcquisitionServices:
    """Required and optional services for acquisition."""
    # Required
    camera: "CameraService"
    stage: "StageService"
    peripheral: "PeripheralService"
    event_bus: "EventBus"

    # Optional
    illumination: Optional["IlluminationService"] = None
    filter_wheel: Optional["FilterWheelService"] = None
    piezo: Optional["PiezoService"] = None
    fluidics: Optional["FluidicsService"] = None
    nl5: Optional["NL5Service"] = None

    def validate(self) -> None:
        """Validate required services are present."""
        if not all([self.camera, self.stage, self.peripheral, self.event_bus]):
            raise ValueError("Missing required services")


@dataclass
class AcquisitionControllers:
    """Optional controllers for acquisition."""
    autofocus: Optional["AutoFocusController"] = None
    laser_autofocus: Optional["LaserAutofocusController"] = None
    focus_lock: Optional["ContinuousFocusLockController"] = None
```

**Refactored constructor:**
```python
class MultiPointWorker:
    def __init__(
        self,
        services: AcquisitionServices,
        controllers: AcquisitionControllers,
        config: AcquisitionParameters,
        objective_store: ObjectiveStore,
        channel_config_manager: ChannelConfigurationManager,
    ):
        self._services = services
        self._controllers = controllers
        # ...
```

---

## P2: Medium Priority Refactors

### 2.1 Microscope.py Decomposition

**Problem:** 633 lines mixing factory methods, hardware access, and orchestration

**Solution:** Split into:
- `microscope.py` - Core orchestration only
- `microscope_factory.py` - `build_from_global_config()` and related builders
- Keep hardware access wrappers as thin delegation

### 2.2 Wildcard Import Cleanup

**Problem:** 12+ files with `from _def import *`

**Solution:** Replace with explicit imports:
```python
# Before
from _def import *

# After
from _def import (
    CAMERA_TYPE,
    STAGE_TYPE,
    MULTIPOINT_PIEZO_DELAY_MS,
)
```

**Files to fix:**
- `multi_point_worker.py`
- `drivers/cameras/*.py`
- `drivers/gxipy/*.py`

### 2.3 Acquisition Context Manager

**Problem:** Pre/post acquisition state management scattered across controller

**Solution:**
```python
@contextmanager
def acquisition_context(
    live_controller: LiveController,
    camera_service: CameraService,
    stage_service: StageService,
) -> Generator[None, None, None]:
    """Manage pre/post acquisition state automatically."""
    # Save state
    was_live = live_controller.is_live
    callbacks_enabled = camera_service.callbacks_enabled
    start_pos = stage_service.get_position()

    # Stop live if running
    if was_live:
        live_controller.stop_live()

    try:
        yield
    finally:
        # Restore state
        stage_service.move_to(start_pos)
        if callbacks_enabled:
            camera_service.enable_callbacks(True)
        if was_live:
            live_controller.start_live()
```

---

## Implementation Order

1. **Phase 1: Core Infrastructure**
   - Add `@handles` decorator to events.py
   - Add `@gated_command` decorator to services/base.py
   - Create feature_flags.py

2. **Phase 2: ScanCoordinates**
   - Create geometry.py with pure functions
   - Create grid.py with pure functions
   - Create wellplate.py with pure functions
   - Refactor scan_coordinates.py to use modules + @handles

3. **Phase 3: LaserAutofocusController**
   - Create laser_spot.py
   - Refactor controller to use laser_spot + @handles

4. **Phase 4: Multipoint Consolidation**
   - Create acquisition_config.py dataclass
   - Create dependencies.py service grouping
   - Move focus map generation to AutofocusExecutor
   - Refactor controller to use AcquisitionConfig

5. **Phase 5: Cleanup**
   - Remove wildcard imports
   - Apply @gated_command to services
   - Migrate other classes to @handles decorator

---

## Key Files

**To create:**
- `software/src/squid/core/config/feature_flags.py`
- `software/src/squid/backend/managers/scan_coordinates/__init__.py`
- `software/src/squid/backend/managers/scan_coordinates/scan_coordinates.py`
- `software/src/squid/backend/managers/scan_coordinates/grid.py`
- `software/src/squid/backend/managers/scan_coordinates/geometry.py`
- `software/src/squid/backend/managers/scan_coordinates/wellplate.py`
- `software/src/squid/backend/processing/laser_spot.py`
- `software/src/squid/backend/controllers/multipoint/acquisition_config.py`
- `software/src/squid/backend/controllers/multipoint/dependencies.py`

**To modify:**
- `software/src/squid/core/events.py` - Add @handles, auto_subscribe
- `software/src/squid/backend/services/base.py` - Add @gated_command
- `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py`
- `software/src/squid/backend/controllers/multipoint/multi_point_controller.py`
- `software/src/squid/backend/controllers/multipoint/multi_point_worker.py`
- `software/src/squid/backend/controllers/multipoint/focus_operations.py`
- `software/src/squid/backend/services/camera_service.py`
- `software/src/squid/backend/services/stage_service.py`
- `software/src/squid/backend/services/peripheral_service.py`

**To delete:**
- `software/src/squid/backend/managers/scan_coordinates.py` (replaced by package)

---

## Testing Requirements

### Unit Tests to Create

**Core infrastructure:**
- `tests/unit/squid/core/test_handles_decorator.py`
- `tests/unit/squid/core/config/test_feature_flags.py`
- `tests/unit/squid/backend/services/test_gated_command.py`

**ScanCoordinates modules:**
- `tests/unit/squid/backend/managers/scan_coordinates/test_geometry.py`
- `tests/unit/squid/backend/managers/scan_coordinates/test_grid.py`
- `tests/unit/squid/backend/managers/scan_coordinates/test_wellplate.py`

**Laser spot detection:**
- `tests/unit/squid/backend/processing/test_laser_spot.py`

**Multipoint config:**
- `tests/unit/squid/backend/controllers/multipoint/test_acquisition_config.py`
- `tests/unit/squid/backend/controllers/multipoint/test_dependencies.py`

### Test Coverage Targets

| Module | Target Coverage |
|--------|-----------------|
| geometry.py | 95% |
| grid.py | 90% |
| wellplate.py | 90% |
| laser_spot.py | 90% |
| feature_flags.py | 85% |
| acquisition_config.py | 85% |

### Running Tests

```bash
cd software

# New module tests
pytest tests/unit/squid/core/test_handles_decorator.py -v
pytest tests/unit/squid/backend/managers/scan_coordinates/ -v
pytest tests/unit/squid/backend/processing/test_laser_spot.py -v

# Integration tests
pytest tests/integration/ -v --simulation

# Full regression
pytest tests/ -v --simulation
```

---

## Success Metrics

| Metric | Before | After |
|--------|--------|-------|
| ScanCoordinates lines | 1,414 | ~400 |
| LaserAutofocusController lines | 1,221 | ~700 |
| Mode gate duplications | 29 | 0 (decorator) |
| MultiPointWorker constructor params | 21+ | ~5 (grouped) |
| MultiPointController setter methods | 20+ | 1 (update_config) |
| Feature flag access patterns | 2 inconsistent | 1 unified |
| Wildcard imports | 12+ files | 0 |
| New module test coverage | - | 80%+ |
| Breaking changes | - | Zero |
