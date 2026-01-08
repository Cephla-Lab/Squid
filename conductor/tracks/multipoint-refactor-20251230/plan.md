# Unified Controller Refactoring Plan

## Problem Statement
| File | Lines | Methods | Largest Method |
|------|-------|---------|----------------|
| `multi_point_controller.py` | 1,264 | 40+ | `run_acquisition()` - 295 lines |
| `multi_point_worker.py` | 1,690 | 35+ | `acquire_camera_image()` - 118 lines |
| `live_controller.py` | 655 | 25+ | `set_microscope_mode()` - 50 lines |

**Core Issues:**
- God objects with 7-8 distinct responsibilities each
- Duplicated illumination/channel logic between live and multipoint
- 20+ thin service wrapper methods adding no value
- Z-movement + stabilization pattern repeated 4x
- Mixed abstraction levels (hardware waits mixed with business logic)

---

## Target Architecture

```
software/src/squid/backend/
├── services/
│   └── acquisition_service.py      # NEW - hardware orchestration primitives
│
└── controllers/
    ├── live_controller.py          # 655 → ~450 lines
    │
    └── multipoint/
        ├── multi_point_controller.py   # 1,264 → ~700 lines
        ├── multi_point_worker.py       # 1,690 → ~900 lines
        │
        │  # Existing (unchanged)
        ├── multi_point_utils.py
        ├── job_processing.py
        ├── downsampled_views.py
        │
        │  # NEW modules
        ├── experiment_manager.py       # ~150 lines - folder/metadata
        ├── acquisition_planner.py      # ~100 lines - estimation logic
        ├── position_zstack.py          # ~200 lines - stage/z-stack
        ├── focus_operations.py         # ~250 lines - focus map/AF
        ├── progress_tracking.py        # ~150 lines - events/coordinates
        └── image_capture.py            # ~200 lines - capture sequences
```

**Expected Reduction:** ~1,350 lines moved to focused modules

---

## New Components

### 1. `AcquisitionService` (services layer)

**Location:** `software/src/squid/backend/services/acquisition_service.py`

**Purpose:** Single source of truth for hardware orchestration primitives. Used by both `LiveController` and `MultiPointWorker`.

**Interface:**
```python
class AcquisitionService:
    def __init__(
        self,
        camera_service: CameraService,
        illumination_service: Optional[IlluminationService],
        filter_wheel_service: Optional[FilterWheelService],
        peripheral_service: PeripheralService,
    ): ...

    def apply_configuration(
        self,
        config: ChannelMode,
        trigger_mode: TriggerMode,
        enable_filter_switching: bool = True,
    ) -> None:
        """Apply exposure, gain, illumination power, filter position."""

    def trigger_acquisition(
        self,
        config: ChannelMode,
        trigger_mode: TriggerMode,
        illumination_time: Optional[float] = None,
    ) -> None:
        """Execute trigger sequence with proper illumination control."""

    def wait_for_ready(self, timeout_s: float = 5.0) -> bool:
        """Check if camera is ready for next trigger."""

    @contextmanager
    def illumination_context(self, config: ChannelMode, trigger_mode: TriggerMode):
        """Context manager for illumination during software trigger."""
```

**Methods consolidated from:**
| From | Method |
|------|--------|
| Worker | `_apply_channel_mode()`, `_select_config()` |
| Worker | `_turn_on_illumination()`, `_turn_off_illumination()` |
| Live | `turn_on_illumination()`, `turn_off_illumination()` |
| Live | `set_microscope_mode()` (config application part) |
| Live | `update_illumination()` (filter wheel part) |

---

### 2. `ExperimentManager` (controller layer)

**Location:** `software/src/squid/backend/controllers/multipoint/experiment_manager.py`

**Purpose:** Encapsulate experiment setup - folder creation, metadata, logging.

**Interface:**
```python
class ExperimentManager:
    def start_experiment(
        self,
        base_path: str,
        experiment_id: str,
        configurations: List[ChannelMode],
        acquisition_params: AcquisitionParameters,
    ) -> ExperimentContext:
        """Create folder structure, write metadata files, setup logging."""

    def finalize_experiment(self, context: ExperimentContext) -> None:
        """Close logs, write final metadata."""

@dataclass
class ExperimentContext:
    experiment_path: str
    experiment_id: str
    log_handler: Optional[logging.Handler]
```

**Methods moving from Controller:**
- `start_new_experiment()` (lines 378-440)
- `_start_per_acquisition_log()` / `_stop_per_acquisition_log()`
- Metadata writing logic

---

### 3. `AcquisitionPlanner` (controller layer)

**Location:** `software/src/squid/backend/controllers/multipoint/acquisition_planner.py`

**Purpose:** Pure calculation logic for estimates - easy to unit test.

**Interface:**
```python
class AcquisitionPlanner:
    def estimate_disk_storage_bytes(
        self,
        params: AcquisitionParameters,
        pixel_size_bytes: int,
        image_dimensions: Tuple[int, int],
    ) -> int: ...

    def estimate_mosaic_ram_bytes(
        self,
        params: AcquisitionParameters,
        fov_dimensions: Tuple[int, int],
        downsample_factor: int,
    ) -> int: ...

    def calculate_image_count(self, params: AcquisitionParameters) -> int: ...

    def validate_settings(
        self,
        params: AcquisitionParameters,
        laser_af_ready: bool,
    ) -> List[ValidationError]: ...
```

**Methods moving from Controller:**
- `get_estimated_acquisition_disk_storage()` (lines 558-580)
- `get_estimated_mosaic_ram_bytes()` (lines 582-654)
- `get_acquisition_image_count()` (lines 529-556)
- `validate_acquisition_settings()` (lines 488-527)

---

### 4. `PositionController` + `ZStackExecutor` (controller layer)

**Location:** `software/src/squid/backend/controllers/multipoint/position_zstack.py`

**Purpose:** Stage movement with stabilization, z-stack sequences.

**Interface:**
```python
class PositionController:
    STABILIZATION_MS = {'x': 90, 'y': 90, 'z': 20}

    def move_to_coordinate(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: Optional[float] = None,
    ) -> None:
        """Move with axis-specific stabilization delays."""

    def move_z_with_stabilization(self, z_mm: float) -> None: ...

class ZStackExecutor:
    def initialize(self, params: ZStackParameters) -> float:
        """Initialize z-stack, return starting z."""

    def prepare_for_stack(self, params: ZStackParameters) -> None:
        """Move to z-stack starting position."""

    def advance_z_level(self, params: ZStackParameters) -> None:
        """Move to next z level."""

    def return_after_stack(self, params: ZStackParameters) -> None:
        """Return to starting position."""

    def apply_z_offset(self, config: ChannelMode, apply: bool) -> None:
        """Apply or remove channel-specific z offset."""
```

**Methods moving from Worker:**
| Method | Lines |
|--------|-------|
| `move_to_coordinate()` | 765-802 |
| `move_to_z_level()` | 804-807 |
| `initialize_z_stack()` | 723-733 |
| `prepare_z_stack()` | 1143-1149 |
| `move_z_for_stack()` | 1666-1676 |
| `move_z_back_after_stack()` | 1678-1695 |
| `handle_z_offset()` | 1151-1161 |

**Consolidates duplicated pattern (4 instances):**
```python
self._stage_move_*(value)
self._sleep(SCAN_STABILIZATION_TIME_MS_* / 1000)
```

---

### 5. `FocusMapGenerator` + `AutofocusExecutor` (controller layer)

**Location:** `software/src/squid/backend/controllers/multipoint/focus_operations.py`

**Purpose:** Focus map generation, autofocus execution.

**Interface:**
```python
class FocusMapGenerator:
    def generate_from_bounds(
        self,
        bounds: Dict[str, Tuple[float, float]],
        dx: float,
        dy: float,
    ) -> bool:
        """Generate focus map from scan bounds."""

    def apply_to_coordinates(
        self,
        focus_map: FocusSurface,
        scan_coords: Dict[str, List[Tuple[float, ...]]],
    ) -> Dict[str, List[Tuple[float, float, float]]]:
        """Apply focus map to update Z coordinates."""

    @contextmanager
    def preserve_existing(self):
        """Context manager to save/restore existing focus map."""

class AutofocusExecutor:
    def should_autofocus(
        self,
        fov_count: int,
        af_interval: int,
        z_stacking_config: str,
        NZ: int,
    ) -> bool: ...

    def perform_autofocus(
        self,
        use_reflection_af: bool,
        region_id: str,
        fov: int,
    ) -> FocusResult: ...
```

**Methods moving:**
| From | Method | Lines |
|------|--------|-------|
| Controller | Focus map generation | 785-880 |
| Controller | Focus map save/restore | 851-856, 1010-1014 |
| Worker | `perform_autofocus()` | 1106-1141 |

---

### 6. `ProgressTracker` + `CoordinateTracker` (controller layer)

**Location:** `software/src/squid/backend/controllers/multipoint/progress_tracking.py`

**Purpose:** Event publishing, coordinate DataFrame management.

**Interface:**
```python
class ProgressTracker:
    def start_acquisition(self) -> None: ...
    def update_progress(
        self,
        region: int,
        fov: int,
        total_fovs: int,
        channel: str,
    ) -> None: ...
    def finish_acquisition(self, success: bool, error: Optional[Exception]) -> None: ...

class CoordinateTracker:
    def initialize(self, use_piezo: bool) -> None: ...
    def record_position(
        self,
        region_id: str,
        fov: int,
        z_level: int,
        position: Pos,
        z_piezo_um: Optional[float] = None,
    ) -> None: ...
    def save_to_csv(self, path: str) -> None: ...
    def get_last_z_position(self, region_id: str, fov: int) -> Optional[float]: ...
```

**Methods moving from Worker:**
- `_publish_acquisition_started/finished/progress()` (lines 354-423)
- `initialize_coordinates_dataframe()` (lines 734-739)
- `update_coordinates_dataframe()` (lines 741-763)

---

### 7. `ImageCaptureExecutor` (controller layer)

**Location:** `software/src/squid/backend/controllers/multipoint/image_capture.py`

**Purpose:** Multipoint-specific capture logic (CaptureInfo, callbacks, NL5).

**Interface:**
```python
@dataclass
class CaptureContext:
    position: Pos
    z_index: int
    config: ChannelMode
    save_directory: str
    file_id: str
    region_id: str
    fov: int
    # ... metadata fields

class ImageCaptureExecutor:
    def __init__(
        self,
        acquisition_service: AcquisitionService,
        nl5_service: Optional[NL5Service],
    ): ...

    def capture_single_image(
        self,
        context: CaptureContext,
        trigger_mode: TriggerMode,
        ready_flag: ThreadSafeFlag,
        capture_info_holder: ThreadSafeValue[CaptureInfo],
    ) -> CaptureResult: ...

def build_capture_info(context: CaptureContext, metadata: AcquisitionMetadata) -> CaptureInfo:
    """Factory function for CaptureInfo - consolidates 2 duplicates."""
```

**Methods moving from Worker:**
- `acquire_camera_image()` (lines 1329-1440) - uses `AcquisitionService`
- CaptureInfo construction (2 places)
- NL5 handling logic

---

## Service Wrapper Removal

Remove 21 thin wrapper methods from Worker (lines 254-338):

```python
# DELETE these - call services directly
_camera_get_pixel_size_binned_um()  → self._camera_service.get_pixel_size_binned_um()
_camera_add_frame_callback()        → self._camera_service.add_frame_callback()
_camera_remove_frame_callback()     → self._camera_service.remove_frame_callback()
_camera_start_streaming()           → self._camera_service.start_streaming()
_camera_stop_streaming()            → self._camera_service.stop_streaming()
_camera_send_trigger()              → self._camera_service.send_trigger()
_camera_get_ready_for_trigger()     → self._camera_service.get_ready_for_trigger()
_camera_get_total_frame_time()      → self._camera_service.get_total_frame_time()
_camera_get_strobe_time()           → self._camera_service.get_strobe_time()
_camera_read_frame()                → self._camera_service.read_frame()
_camera_get_exposure_time()         → self._camera_service.get_exposure_time()
_camera_set_exposure()              → self._camera_service.set_exposure_time()
_stage_get_pos()                    → self._stage_service.get_position()
_stage_move_x_to()                  → self._stage_service.move_x_to()
_stage_move_y_to()                  → self._stage_service.move_y_to()
_stage_move_z_to()                  → self._stage_service.move_z_to()
_stage_move_z()                     → self._stage_service.move_z()
_peripheral_enable_joystick()       → self._peripheral_service.enable_joystick()
_peripheral_wait_till_operation_is_completed() → self._peripheral_service.wait_till_operation_is_completed()
_piezo_get_position()               → self._piezo_service.get_position()
_piezo_move_to()                    → self._piezo_service.move_to()
```

---

## Implementation Phases

### Phase 1: Foundation - AcquisitionService
**Risk: Low | Impact: High**

1. Create `acquisition_service.py` with core methods
2. Add comprehensive unit tests
3. Migrate `LiveController` to use `AcquisitionService`
4. Verify live view still works

**Files:**
- CREATE: `software/src/squid/backend/services/acquisition_service.py`
- CREATE: `software/tests/unit/squid/services/test_acquisition_service.py`
- MODIFY: `software/src/squid/backend/controllers/live_controller.py`

### Phase 2: Controller Extraction
**Risk: Low | Impact: Medium**

5. Create `experiment_manager.py`, migrate from controller
6. Create `acquisition_planner.py`, migrate estimation logic
7. Update controller to delegate

**Files:**
- CREATE: `experiment_manager.py`, `acquisition_planner.py`
- CREATE: Unit tests for both
- MODIFY: `multi_point_controller.py`

### Phase 3: Worker Domain Modules
**Risk: Medium | Impact: High**

8. Create `progress_tracking.py` (lowest risk, no hardware)
9. Create `position_zstack.py` (consolidates z-movement)
10. Create `focus_operations.py` (extracts focus map logic)
11. Create `image_capture.py` (highest complexity)

**Files:**
- CREATE: 4 new modules + tests
- MODIFY: `multi_point_worker.py`

### Phase 4: Integration
**Risk: Medium | Impact: High**

12. Update `MultiPointWorker.__init__()` to create domain objects
13. Replace worker methods with domain module calls
14. Wire `AcquisitionService` into `ImageCaptureExecutor`

### Phase 5: Cleanup
**Risk: Low | Impact: Low**

15. Remove 21 service wrapper methods
16. Update tests to mock services directly
17. Remove any dead code

### Phase 6: Stretch - Loop Decomposition
**Risk: High | Impact: Medium**

18. Create `AcquisitionTask` interface
19. Implement `TimepointSequence`, `RegionSequence`
20. Refactor `run()` to execute task sequences

---

## Risk Assessment

| Component | Risk | Mitigation |
|-----------|------|------------|
| `AcquisitionService` | Low | New code, doesn't break existing |
| `ExperimentManager` | Low | Pure extraction, no logic change |
| `AcquisitionPlanner` | Low | Pure functions, easy to test |
| `PositionController` | Medium | Timing-sensitive stabilization |
| `FocusMapGenerator` | Medium | Complex coordinate math |
| `ImageCaptureExecutor` | High | Critical path, callback timing |
| Loop decomposition | High | Major structural change |

---

## Files Summary

### Create (12 files)
```
services/acquisition_service.py
controllers/multipoint/experiment_manager.py
controllers/multipoint/acquisition_planner.py
controllers/multipoint/position_zstack.py
controllers/multipoint/focus_operations.py
controllers/multipoint/progress_tracking.py
controllers/multipoint/image_capture.py
tests/unit/squid/services/test_acquisition_service.py
tests/unit/squid/controllers/multipoint/test_experiment_manager.py
tests/unit/squid/controllers/multipoint/test_acquisition_planner.py
tests/unit/squid/controllers/multipoint/test_position_zstack.py
tests/unit/squid/controllers/multipoint/test_focus_operations.py
```

### Modify (4 files)
```
controllers/live_controller.py
controllers/multipoint/multi_point_controller.py
controllers/multipoint/multi_point_worker.py
controllers/multipoint/__init__.py
```

---

## Success Metrics

| Metric | Before | After |
|--------|--------|-------|
| `multi_point_controller.py` | 1,264 lines | ~700 lines |
| `multi_point_worker.py` | 1,690 lines | ~900 lines |
| `live_controller.py` | 655 lines | ~450 lines |
| Service wrappers | 21 methods | 0 methods |
| Duplicated illumination logic | 2 places | 1 place |
| Duplicated z-movement pattern | 4 places | 1 place |
| Unit test coverage | Low | High (focused modules) |
