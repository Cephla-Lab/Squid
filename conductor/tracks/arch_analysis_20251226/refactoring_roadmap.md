# Refactoring Roadmap - Squid (Revised)

## Design Principles

- **Keep it minimal** - Simple classes, composition over abstraction layers
- **No unnecessary abstractions** - Add complexity only when it solves a real problem
- **Reusable utilities** - Extract common patterns as shared functions/services
- **Software triggering by default** - Hardware orchestration is optional optimization
- **Pure functions where possible** - Stateless logic is testable and reusable

---

## Phase 1: Break Circular Dependency (Low Risk)

*Goal: Remove HAL → Controller back-reference*

1. **Add `FrameAcquisitionStarting` event** to `squid/core/events.py`
2. **Remove `LiveController` reference from `Microscope`** - refactor `acquire_image()` to publish event
3. **`IlluminationService` subscribes** and handles software-triggered illumination

**Files:** `events.py`, `microscope.py`, `live_controller.py`
**Impact:** ~50 lines changed

---

## Phase 2: Shared Utilities (Low-Medium Risk)

*Goal: Create reusable utilities for all controllers*

### 2a. StageService wait parameter
Add `wait: bool = True` to movement methods. Eliminates wrapper methods everywhere.

### 2b. ChannelModeService
Simple class (~40 lines) that applies a `ChannelMode` config:
- Sets camera exposure/gain
- Sets illumination source/intensity
- Sets filter wheel position

**Consumers:** LiveController, MultiPointWorker, AutoFocusController

### 2c. Experiment IO utilities

**File:** `squid/backend/io/experiment_io.py`

Consolidate all experiment file IO into pure functions:

```python
# --- Experiment Setup ---
def generate_experiment_id(label: str) -> str
def create_experiment_folders(base_path, experiment_id, num_timepoints) -> str

# --- Metadata Writing ---
def write_acquisition_params(experiment_path, params) -> None
def write_configurations_xml(experiment_path, configs) -> None
def write_coordinates_csv(timepoint_path, coordinates_df) -> None
def create_done_file(path) -> None

# --- Convenience ---
def setup_experiment(base_path, label, params, configs, num_timepoints) -> tuple[str, str]
```

**What stays in `job_processing.py`:**
- `SaveImageJob` - already well-structured for async image saving
- `JobRunner` - multiprocessing infrastructure
- OME-TIFF logic - complex but properly encapsulated

---

## Phase 3: MultiPointController Simplification (Medium Risk)

*Goal: Clarify control flow, extract pure functions, enable continuous AF*

### 3a. Split `run_acquisition()` into focused methods
- `_can_start_acquisition()` - validation
- `_prepare_scan_coordinates()` - coordinate setup
- `_build_acquisition_params()` - params dataclass
- `_start_worker()` - thread start

### 3b. Remove wrapper methods from Worker
With StageService `wait=True` and ChannelModeService, delete ~100 lines of wrappers.

### 3c. Extract pure functions to `multi_point_utils.py`

Move stateless logic out of MultiPointWorker:

```python
# --- File/Path Generation ---
def generate_file_id(region_id, fov, z_level, padding=4) -> str
def generate_timepoint_path(experiment_path, timepoint, padding=4) -> str

# --- Scan Planning ---
@dataclass
class ScanPoint:
    region_id: str
    fov_index: int
    coordinate_mm: tuple[float, float, float]
    z_level: int
    config: ChannelMode

def generate_scan_sequence(regions, configs, nz) -> Iterator[ScanPoint]
def calculate_total_images(regions, nz, n_configs) -> int

# --- Z-Stack Math ---
def calculate_z_positions(nz, delta_z, z_stacking_config, center_z) -> list[float]

# --- Progress Calculation ---
def calculate_progress(current_region, total_regions, current_fov, total_fovs) -> float
def calculate_eta(start_time, progress_percent) -> Optional[float]

# --- Autofocus Decision ---
def should_run_autofocus(af_fov_count, fovs_per_af, do_autofocus, nz, z_stacking_config) -> bool
```

### 3d. Simplify `acquire_at_position()` using pure functions

```python
def acquire_at_position(self, region_id, current_path, fov):
    if should_run_autofocus(...):
        self._perform_autofocus()

    z_positions = calculate_z_positions(self.NZ, self.deltaZ, self.z_stacking_config, current_z)

    for z_level, z_pos in enumerate(z_positions):
        self._stage_move_z_to(z_pos)
        for config in self.selected_configurations:
            file_id = generate_file_id(region_id, fov, z_level)
            self._acquire_single_image(config, file_id, current_path)
```

### 3e. Add continuous AF support
Add optional `continuous_af_controller` parameter:
```python
if self._continuous_af:
    self._continuous_af.wait_for_lock()
# ... acquire ...
if self._continuous_af:
    self._continuous_af.re_engage()
```

---

## Phase 4: Microscope Class Cleanup (Low Risk)

*Goal: Remove unnecessary pass-through methods*

Remove ~15 methods that just delegate to `self.stage`:
- `move_x()`, `move_y()`, `move_x_to()`, `move_y_to()`, `get_x()`, `get_y()`, etc.

Callers should use `microscope.stage` directly.

**Impact:** ~80 lines removed

---

## Phase 5: LaserAutofocusController Simplification (Medium Risk)

*Goal: Extract image processing algorithms into testable pure functions*

The largest controller (~1023 lines) with embedded image analysis.

### 5a. Create `laser_af_algorithms.py`

**File:** `squid/backend/processing/laser_af_algorithms.py`

```python
# --- Spot Detection ---
def find_laser_spot_centroid(image, threshold, background_subtract=True) -> Optional[tuple[float, float]]
def detect_spot_with_background_removal(image, kernel_size=51) -> np.ndarray

# --- Displacement Measurement ---
def compute_displacement_from_correlation(ref_crop, current_crop, subpixel=True) -> float
def compute_displacement_from_centroid_shift(ref_centroid, current_centroid) -> float

# --- Calibration ---
def calibrate_pixel_to_um(spot_positions_px, z_positions_mm) -> float
def fit_focus_curve(z_positions, displacements) -> tuple[float, float]

# --- Configuration ---
def validate_laser_af_config(config, camera_roi) -> LaserAFConfig
def clamp_crop_region(crop_x, crop_y, crop_width, crop_height, image_width, image_height) -> tuple
```

### 5b. Simplify controller to use pure functions
- `measure_displacement()` calls `find_laser_spot_centroid()` + `compute_displacement_from_centroid_shift()`
- `calibrate()` calls `calibrate_pixel_to_um()` + `fit_focus_curve()`
- Controller keeps: state machine, hardware orchestration, event publishing

**Impact:** ~200 lines moved to pure functions

---

## Phase 6: LiveController Simplification (Low Risk)

*Goal: Extract timer/FPS and illumination logic*

### 6a. Create `live_utils.py`

**File:** `squid/backend/controllers/live_utils.py`

```python
# --- Timer Calculations ---
def calculate_timer_interval_ms(fps: float) -> float
def should_skip_frame(frame_count: int, skip_ratio: int) -> bool

# --- Trigger Mode Conversion ---
def trigger_mode_to_str(mode: TriggerMode) -> str
def str_to_trigger_mode(s: str) -> TriggerMode

# --- Illumination ---
def extract_illumination_params(config: ChannelMode) -> tuple[Optional[int], Optional[float]]
def validate_illumination_params(channel, intensity) -> bool
```

### 6b. Simplify controller timer/illumination handling
- Timer start uses `calculate_timer_interval_ms()`
- Illumination updates use `extract_illumination_params()` + `validate_illumination_params()`

**Impact:** ~50 lines moved to pure functions

---

## Phase 7: StreamHandler Rate Limiting (Low Risk)

*Goal: Extract FPS/rate limiting logic for testability*

### 7a. Create `stream_utils.py`

**File:** `squid/backend/io/stream_utils.py`

```python
@dataclass
class FPSState:
    counter: int = 0
    timestamp_last: int = 0
    fps_measured: int = 0

def update_fps_counter(timestamp_now: int, state: FPSState) -> FPSState
def should_process_frame(now: float, last_process_time: float, target_fps: float) -> bool
```

### 7b. Simplify StreamHandler
- Frame callback uses `update_fps_counter()` + `should_process_frame()`

**Impact:** ~20 lines moved to pure functions

---

## Deferred (Not Needed Now)

These items from the original plan are **explicitly deferred**:

| Item | Reason |
|------|--------|
| Centralized StateStore | EventBus + existing managers already work |
| BufferManager / tiered workers | Profile first if perf becomes an issue |
| Hardware-orchestrated FOV | Keep software triggering as default |
| Factory class hierarchies | ApplicationContext is adequate |
| ApplicationContext refactoring | Analysis showed current structure is fine |

---

## Execution Order

1. **Phase 1** - Break circular dep (lowest risk, unblocks future work)
2. **Phase 2a** - StageService wait parameter (low risk, immediate benefit)
3. **Phase 2b** - ChannelModeService (medium risk, enables Phase 3)
4. **Phase 2c** - Experiment IO utilities (low risk)
5. **Phase 3a-b** - Refactor controller methods, remove wrapper methods
6. **Phase 3c-d** - Extract pure functions, simplify `acquire_at_position()`
7. **Phase 3e** - Add continuous AF hook
8. **Phase 4** - Microscope pass-through cleanup (can be done anytime)
9. **Phase 5** - LaserAutofocusController pure functions (can be done anytime after Phase 2)
10. **Phase 6** - LiveController pure functions (can be done anytime)
11. **Phase 7** - StreamHandler rate limiting (can be done anytime)

Each phase can be merged independently. Phases 5-7 have no dependencies on each other.

---

## Summary

| File | Change | Lines |
|------|--------|-------|
| `squid/core/events.py` | Add FrameAcquisitionStarting event | +10 |
| `squid/backend/microscope.py` | Remove LiveController ref, remove pass-throughs | -100 |
| `squid/backend/services/stage_service.py` | Add wait parameter | +15 |
| `squid/backend/services/channel_mode_service.py` | New simple service | +60 |
| `squid/backend/io/experiment_io.py` | Experiment folder/metadata IO functions | +80 |
| `squid/backend/controllers/multipoint/multi_point_controller.py` | Refactor methods | -50 |
| `squid/backend/controllers/multipoint/multi_point_utils.py` | Pure functions (scan, Z-stack, progress, AF) | +80 |
| `squid/backend/controllers/multipoint/multi_point_worker.py` | Use pure functions, remove wrappers, continuous AF | -150 |
| `squid/backend/controllers/live_controller.py` | Use ChannelModeService, use live_utils | -80 |
| `squid/backend/controllers/live_utils.py` | Timer/FPS, trigger mode, illumination utils | +50 |
| `squid/backend/processing/laser_af_algorithms.py` | Spot detection, displacement, calibration | +150 |
| `squid/backend/controllers/autofocus/laser_auto_focus_controller.py` | Use pure functions | -200 |
| `squid/backend/io/stream_utils.py` | FPS state, rate limiting | +30 |
| `squid/backend/io/stream_handler.py` | Use stream_utils | -20 |

**Total:** ~125 net lines removed, clearer structure, reusable utilities, testable pure functions
