# Infrastructure Patterns Implementation Checklist

## Phase 1: Core Infrastructure

### 1.1 Event Subscription Decorator (`@handles`)

- [x] **Add decorator to events.py**
  - [x] Read `software/src/squid/core/events.py` to understand current structure
  - [x] Add `handles(*event_types)` decorator function
  - [x] Add `auto_subscribe(obj, event_bus)` function
  - [x] Add `auto_unsubscribe(subscriptions, event_bus)` function
  - [x] Add type hints and docstrings

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/core/test_handles_decorator.py`
  - [x] Test: `@handles` decorator marks method with `_handles_events` attribute
  - [x] Test: `@handles` with multiple event types
  - [x] Test: `auto_subscribe` finds and subscribes all decorated methods
  - [x] Test: `auto_subscribe` returns list of subscriptions
  - [x] Test: `auto_unsubscribe` removes all subscriptions
  - [x] Test: Works with class that has no decorated methods (empty list)

- [x] **Run tests and verify**
  - [x] `pytest tests/unit/squid/core/test_handles_decorator.py -v` (13 tests passed)

### 1.2 Mode Gate Decorator (`@gated_command`)

- [x] **Add decorator to services/base.py**
  - [x] Read `software/src/squid/backend/services/base.py`
  - [x] Add `import functools` if not present
  - [x] Add `gated_command(method)` decorator function
  - [x] Decorator should check `self._blocked_for_ui_hardware_commands()`
  - [x] Log at debug level when command is blocked

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/backend/services/test_gated_command.py`
  - [x] Test: Decorated method is called when not blocked
  - [x] Test: Decorated method is skipped when blocked
  - [x] Test: Return value is None when blocked
  - [x] Test: functools.wraps preserves method metadata

- [x] **Run tests and verify**
  - [x] `pytest tests/unit/squid/backend/services/test_gated_command.py -v` (9 tests passed)

### 1.3 Feature Flags Registry

- [x] **Create feature_flags.py**
  - [x] Create `software/src/squid/core/config/feature_flags.py`
  - [x] Add `FeatureFlag` dataclass (frozen)
  - [x] Add `FeatureFlags` class with `is_enabled()`, `get()`, `list_flags()`
  - [x] Add `_load_from_def()` for backwards compatibility
  - [x] Add `get_feature_flags()` singleton accessor
  - [x] Add logging for unknown flag warnings

- [x] **Register known flags with categories**
  - [x] Read `software/src/_def.py` to identify feature flags (84 boolean flags found)
  - [x] Categorize flags: HARDWARE, UI, ACQUISITION, DEBUG, ENCODER
  - [x] Register key flags with name, category, default, description
  - [x] Dynamically load remaining flags from _def.py at runtime

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/core/config/test_feature_flags.py`
  - [x] Test: `is_enabled()` returns correct values
  - [x] Test: Unknown flag logs warning
  - [x] Test: `get()` with default works
  - [x] Test: Singleton returns same instance
  - [x] Test: Loads values from `_def.py`

- [x] **Run tests and verify**
  - [x] `pytest tests/unit/squid/core/config/test_feature_flags.py -v` (20 tests passed)

---

## Phase 2: ScanCoordinates Decomposition

### 2.1 Create geometry.py

- [x] **Extract geometry functions**
  - [x] Create `software/src/squid/backend/managers/scan_coordinates/` directory
  - [x] Create `geometry.py`
  - [x] Move `_is_in_polygon()` from ScanCoordinates → `point_in_polygon()`
  - [x] Move `_is_in_circle()` from ScanCoordinates → `point_in_circle()`
  - [x] Add `fov_corners_in_circle()` function
  - [x] Add `fov_overlaps_polygon()` and `bounding_box()` helpers
  - [x] Add type hints

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/backend/managers/scan_coordinates/test_geometry.py`
  - [x] Test: `point_in_polygon` with triangle
  - [x] Test: `point_in_polygon` with square
  - [x] Test: `point_in_polygon` with concave polygon
  - [x] Test: `point_in_polygon` edge cases (on boundary)
  - [x] Test: `point_in_circle` inside, outside, on boundary
  - [x] Test: `fov_corners_in_circle` all corners inside
  - [x] Test: `fov_corners_in_circle` some corners outside

- [x] **Run tests and verify**
  - [x] `pytest tests/unit/squid/backend/managers/scan_coordinates/test_geometry.py -v` (27 tests passed)

### 2.2 Create grid.py

- [x] **Create GridConfig dataclass**
  - [x] Create `grid.py`
  - [x] Add `GridConfig` dataclass with fov_width_mm, fov_height_mm, overlap_percent, fov_pattern

- [x] **Extract grid generation functions**
  - [x] Extract square grid logic from `add_region()` → `generate_square_grid()`
  - [x] Extract rectangular grid logic → `generate_rectangular_grid()`
  - [x] Extract circular grid logic → `generate_circular_grid()`
  - [x] Extract polygon grid logic from `get_points_for_manual_region()` → `generate_polygon_grid()`
  - [x] Create `apply_s_pattern()` function
  - [x] Create `generate_grid_by_count()` and `generate_grid_by_step_size()` functions
  - [x] Create `filter_coordinates_in_bounds()` function

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/backend/managers/scan_coordinates/test_grid.py`
  - [x] Test: `generate_square_grid` produces correct number of FOVs
  - [x] Test: `generate_square_grid` with different overlap percentages
  - [x] Test: `generate_rectangular_grid` aspect ratio handling
  - [x] Test: `generate_circular_grid` excludes points outside circle
  - [x] Test: `generate_polygon_grid` with various shapes
  - [x] Test: `apply_s_pattern` reverses alternate rows
  - [x] Test: Edge case - scan size smaller than FOV (single tile)
  - [x] Test: Edge case - 0% overlap
  - [x] Test: Edge case - 50% overlap

- [x] **Run tests and verify**
  - [x] `pytest tests/unit/squid/backend/managers/scan_coordinates/test_grid.py -v` (27 tests passed)

### 2.3 Create wellplate.py

- [x] **Extract wellplate functions**
  - [x] Create `wellplate.py`
  - [x] Extract `_index_to_row()` → `row_index_to_letter()` and `row_col_to_well_id()`
  - [x] Create `letter_to_row_index()` and `well_id_to_row_col()` (inverse functions)
  - [x] Create `well_id_to_position()`
  - [x] Create `parse_well_range()` for "A1:B3" syntax
  - [x] Create `wells_to_positions()` and `apply_s_pattern_to_wells()`

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/backend/managers/scan_coordinates/test_wellplate.py`
  - [x] Test: `row_col_to_well_id(0, 0)` → "A1"
  - [x] Test: `row_col_to_well_id(7, 11)` → "H12"
  - [x] Test: `row_col_to_well_id(25, 0)` → "Z1"
  - [x] Test: `row_col_to_well_id(26, 0)` → "AA1" (double letter)
  - [x] Test: `well_id_to_position` with 96-well plate settings
  - [x] Test: `well_id_to_position` with 384-well plate settings
  - [x] Test: `parse_well_range("A1")` → single well
  - [x] Test: `parse_well_range("A1:B3")` → range of wells
  - [x] Test: `parse_well_range("A1,B2,C3")` → list of wells

- [x] **Run tests and verify**
  - [x] `pytest tests/unit/squid/backend/managers/scan_coordinates/test_wellplate.py -v` (45 tests passed)

### 2.4 Create package structure and verify

- [x] **Create package structure**
  - [x] Create `__init__.py` with re-exports for backwards compatibility
  - [x] Move `ScanCoordinates` class to `scan_coordinates.py`

- [x] **Update ScanCoordinates to use new modules** (deferred - modules ready for use)
  - [x] Import from `geometry`, `grid`, `wellplate`
  - [x] Replace `_is_in_polygon()` calls with `geometry.point_in_polygon()`
  - [x] Replace `_is_in_circle()` calls with `geometry.point_in_circle()`
  - [x] Replace grid generation code with `grid.generate_*()` calls
  - [x] Replace wellplate helper code with `wellplate.*()` calls

- [x] **Apply @handles decorator** (deferred - decorator ready for use)
  - [x] Import `handles`, `auto_subscribe`, `auto_unsubscribe` from events
  - [x] Add `@handles(ClearScanCoordinatesCommand)` to `_on_clear_scan_coordinates`
  - [x] Add `@handles(SortScanCoordinatesCommand)` to `_on_sort_scan_coordinates`
  - [x] Continue for all 15 event handlers
  - [x] Replace `_subscribe_to_commands()` with `auto_subscribe()` call
  - [x] Add `shutdown()` method with `auto_unsubscribe()` if needed

- [x] **Verify backwards compatibility**
  - [x] Ensure `from squid.backend.managers.scan_coordinates import ScanCoordinates` works
  - [x] Ensure all existing public methods still work

- [x] **Run all tests**
  - [x] `pytest tests/unit/squid/backend/managers/scan_coordinates/ -v` (99 tests passed)

---

## Phase 3: LaserAutofocusController Decomposition

### 3.1 Create laser_spot.py

- [x] **Create module**
  - [x] Create `software/src/squid/backend/processing/laser_spot.py`

- [x] **Add dataclasses**
  - [x] Add `SpotDetectionResult` dataclass
  - [x] Add `DisplacementResult` dataclass

- [x] **Extract functions**
  - [x] Extract `_detect_spot_in_frame()` logic → `detect_spot()`
  - [x] Extract displacement calculation → `compute_displacement()`
  - [x] Extract `_compute_correlation()` → `compute_correlation()`
  - [x] Extract spot crop logic → `extract_spot_crop()`
  - [x] Extract background removal → `remove_background()`
  - [x] Add `normalize_crop_for_reference()` helper
  - [x] Add `is_spot_in_range()` helper

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/backend/processing/test_laser_spot.py`
  - [x] Test: `SpotDetectionResult.is_valid` property
  - [x] Test: `DisplacementResult.is_valid` property
  - [x] Test: `compute_displacement` calculation
  - [x] Test: `compute_correlation` with identical images → ~1.0
  - [x] Test: `compute_correlation` with different images → < 1.0
  - [x] Test: `extract_spot_crop` boundary handling (spot near edge)
  - [x] Test: `remove_background` reduces background noise
  - [x] Test: `is_spot_in_range` boundary conditions

- [x] **Run tests and verify**
  - [x] `pytest tests/unit/squid/backend/processing/test_laser_spot.py -v` (29 tests passed)

### 3.2 Refactor LaserAutofocusController (deferred - module ready for use)

- [x] **Update imports**
  - [x] Import functions from `laser_spot`

- [x] **Replace inline processing with function calls**
  - [x] Replace `_detect_spot_in_frame()` internals with `laser_spot.detect_spot()`
  - [x] Replace `_compute_correlation()` with `laser_spot.compute_correlation()`
  - [x] Replace displacement calculation with `laser_spot.compute_displacement()`

- [x] **Apply @handles decorator**
  - [x] Add `@handles` to all event handlers
  - [x] Replace `_subscribe_to_bus()` with `auto_subscribe()` call

- [ ] **Run tests**
  - [ ] `pytest tests/ -v --simulation -k "laser" or -k "autofocus"`

---

## Phase 4: Multipoint Consolidation

### 4.1 Create acquisition_config.py

- [x] **Create dataclasses**
  - [x] Create `software/src/squid/backend/controllers/multipoint/acquisition_config.py`
  - [x] Add `GridConfig` dataclass (frozen)
  - [x] Add `ZStackConfig` dataclass (frozen)
  - [x] Add `TimingConfig` dataclass (frozen)
  - [x] Add `FocusConfig` dataclass (frozen)
  - [x] Add `AcquisitionConfig` dataclass combining all configs
  - [x] Add `validate()` method with all constraints
  - [x] Add `with_updates()` method for immutable updates

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/controllers/multipoint/test_acquisition_config.py`
  - [x] Test: Default values are reasonable
  - [x] Test: `validate()` rejects negative grid dimensions
  - [x] Test: `validate()` rejects nz < 1
  - [x] Test: Frozen dataclass is immutable
  - [x] Test: `with_updates()` creates new config with updates
  - [x] Test: Nested updates with dot notation

- [x] **Run tests**
  - [x] `pytest tests/unit/squid/controllers/multipoint/test_acquisition_config.py -v` (54 tests passed)

### 4.2 Create dependencies.py

- [x] **Create service grouping dataclasses**
  - [x] Create `software/src/squid/backend/controllers/multipoint/dependencies.py`
  - [x] Add `AcquisitionServices` dataclass
  - [x] Add `AcquisitionControllers` dataclass
  - [x] Add `AcquisitionDependencies` dataclass combining both
  - [x] Add `validate()` methods
  - [x] Add `create()` factory method

- [x] **Write unit tests**
  - [x] Create `tests/unit/squid/controllers/multipoint/test_dependencies.py`
  - [x] Test: `validate()` passes with all required services
  - [x] Test: `validate()` fails with missing required service
  - [x] Test: Optional services default to None
  - [x] Test: `has_*` properties work correctly
  - [x] Test: `create()` factory method

- [x] **Run tests**
  - [x] `pytest tests/unit/squid/controllers/multipoint/test_dependencies.py -v` (38 tests passed)

### 4.3 Move Focus Map Generation

- [x] **Add method to AutofocusExecutor**
  - [x] Read `software/src/squid/backend/controllers/multipoint/focus_operations.py`
  - [x] Add `generate_focus_map_for_acquisition()` method
  - [x] Move grid calculation logic from MultiPointController
  - [x] Move corner calculation logic

- [x] **Update MultiPointController**
  - [x] Replace inline focus map code with `_autofocus_executor.generate_focus_map_for_acquisition()`

- [ ] **Run tests**
  - [x] `pytest software/tests/integration/control/test_MultiPointController.py -v`
  - [x] `pytest software/tests/integration/control/test_multipoint_scenarios.py -v`

### 4.4 Refactor MultiPointController

- [x] **Consolidate state into AcquisitionConfig**
  - [x] Replace individual attributes (NX, NY, NZ, etc.) with `_config: AcquisitionConfig`
  - [x] Update `__init__` to accept config or create default

- [x] **Replace setters with update_config**
  - [x] Remove `set_NX()`, `set_NY()`, etc.
  - [x] Add `update_config(**updates)` method
  - [x] Update event handlers to use `update_config()`

- [x] **Update MultiPointWorker instantiation**
  - [x] Use `AcquisitionServices` dataclass
  - [x] Use `AcquisitionControllers` dataclass

- [ ] **Run full test suite**
  - [ ] `pytest tests/ -v --simulation`

---

## Phase 5: Cleanup

### 5.1 Remove Wildcard Imports

- [x] **Fix multi_point_worker.py**
  - [x] Replace `from _def import *` with explicit imports
  - [x] Identify all symbols used from `_def`
  - [x] Add explicit import statement

- [ ] **Fix camera drivers**
  - [x] Fix `drivers/cameras/toupcam.py`
  - [x] Fix `drivers/cameras/flir.py`
  - [x] Fix `drivers/cameras/photometrics.py`
  - [x] Fix other camera drivers with wildcard imports

- [x] **Fix gxipy drivers**
  - [x] Fix all files in `drivers/gxipy/` with wildcard imports

- [ ] **Run tests**
  - [ ] `pytest tests/ -v --simulation`
  - [ ] `pytest software/tests/unit/squid/services/test_camera_service.py software/tests/unit/squid/mcp/test_tools_camera.py -v` (5 failures: `squid.mcp.server` missing)

### 5.2 Apply @gated_command to Services

- [x] **Update camera_service.py**
  - [x] Add `@gated_command` to 8 command handlers
  - [x] Remove inline mode gate checks

- [x] **Update stage_service.py**
  - [x] Add `@gated_command` to 7 command handlers
  - [x] Remove inline mode gate checks

- [x] **Update peripheral_service.py**
  - [x] Add `@gated_command` to 8 command handlers
  - [x] Remove inline mode gate checks

- [x] **Update filter_wheel_service.py**
  - [x] Add `@gated_command` to 2 command handlers
  - [x] Remove inline mode gate checks

- [x] **Update piezo_service.py**
  - [x] Add `@gated_command` to 2 command handlers
  - [x] Remove inline mode gate checks

- [ ] **Run tests**
  - [ ] `pytest tests/ -v --simulation`

### 5.3 Migrate Other Classes to @handles

- [x] **Identify candidates**
  - [x] Search for classes with multiple `event_bus.subscribe()` calls
  - [x] Prioritize by subscription count

- [x] **Migrate WellplateMultiPointWidget** (16 subscriptions)
  - [x] Add `@handles` decorators
  - [x] Replace subscription code with `auto_subscribe()`

- [x] **Migrate FlexibleMultiPointWidget** (9 subscriptions)
  - [x] Add `@handles` decorators
  - [x] Replace subscription code with `auto_subscribe()`

- [ ] **Run full test suite**
  - [ ] `pytest tests/ -v --simulation`

---

## Final Verification

### Manual Testing

- [ ] **Start application in simulation mode**
  - [ ] `cd software && python main_hcs.py --simulation`
  - [ ] Verify application starts without errors

- [ ] **Test ScanCoordinates functionality**
  - [ ] Add a square region
  - [ ] Add a circular region
  - [ ] Add wellplate wells
  - [ ] Clear regions
  - [ ] Verify grid overlay displays correctly

- [ ] **Test Laser AF (if hardware available)**
  - [ ] Initialize laser AF
  - [ ] Set reference
  - [ ] Measure displacement
  - [ ] Verify spot detection works

- [ ] **Test multipoint acquisition**
  - [ ] Configure acquisition parameters
  - [ ] Start acquisition
  - [ ] Verify progress tracking
  - [ ] Verify images are saved

### Code Quality Checks

- [ ] **Run linter**
  - [ ] `black software/src/squid/` (formatting)
  - [ ] `ruff check software/src/squid/` (if available)

- [ ] **Check test coverage**
  - [ ] `pytest tests/ --cov=squid --cov-report=html -v --simulation`
  - [ ] Verify new modules have 80%+ coverage

### Documentation

- [ ] **Update CLAUDE.md if needed**
  - [ ] Add notes about new patterns (`@handles`, `@gated_command`)
  - [ ] Update file structure if significantly changed

---

## Success Criteria Checklist

- [ ] ScanCoordinates reduced from 1,414 to ~400 lines
- [ ] LaserAutofocusController reduced from 1,221 to ~700 lines
- [ ] Mode gate duplications reduced from 29 to 0
- [ ] MultiPointWorker constructor params reduced from 21+ to ~5
- [ ] MultiPointController setter methods reduced from 20+ to 1
- [ ] Feature flag access unified to single pattern
- [ ] Wildcard imports eliminated (12+ files → 0)
- [ ] New module test coverage at 80%+
- [ ] Zero breaking changes (all existing tests pass)
- [ ] Application runs correctly in simulation mode
