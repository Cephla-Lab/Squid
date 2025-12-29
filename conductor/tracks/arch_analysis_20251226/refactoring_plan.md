# Refactoring Plan: Minimal Approach - Squid (Revised)

## 1. Overview

This document outlines a **minimal refactoring strategy** that addresses core problems without introducing unnecessary abstraction layers.

**Key Principles:**
- Simple classes composed together, not deep hierarchies
- Extract utility functions, not heavyweight services
- Keep software triggering as the default
- Only add complexity when it solves a real, demonstrated problem
- **Pure functions for stateless logic** - testable, reusable, explicit

## 2. Simplifying `MultiPointController`

Rather than decomposing into 4+ new classes (ScanPlanner, AcquisitionEngine, AcquisitionCoordinator, DataArchiveService), we take a lighter approach:

### A. Extract Pure Functions (Not Classes)

**File: `squid/backend/controllers/multipoint/multi_point_utils.py`**

```python
# --- File/Path Generation ---
def generate_file_id(region_id: str, fov: int, z_level: int, padding: int = 4) -> str:
    """Generate standardized file ID like 'region_0001_0001'."""
    return f"{region_id}_{fov:0{padding}}_{z_level:0{padding}}"

def generate_timepoint_path(experiment_path: str, timepoint: int, padding: int = 4) -> str:
    """Generate path for a timepoint folder."""
    return os.path.join(experiment_path, f"{timepoint:0{padding}}")

# --- Scan Planning ---
@dataclass
class ScanPoint:
    region_id: str
    fov_index: int
    coordinate_mm: tuple[float, float, float]
    z_level: int
    config: ChannelMode

def generate_scan_sequence(
    regions: dict[str, list[tuple[float, float, float]]],
    configs: list[ChannelMode],
    nz: int,
) -> Iterator[ScanPoint]:
    """Yield all acquisition points in order."""
    for region_id, fov_coords in regions.items():
        for fov_idx, coord in enumerate(fov_coords):
            for z_level in range(nz):
                for config in configs:
                    yield ScanPoint(region_id, fov_idx, coord, z_level, config)

def calculate_total_images(regions: dict, nz: int, n_configs: int) -> int:
    """Calculate total number of images to acquire."""
    return sum(len(coords) for coords in regions.values()) * nz * n_configs

# --- Z-Stack Math ---
def calculate_z_positions(
    nz: int,
    delta_z: float,
    z_stacking_config: str,
    center_z: float,
) -> list[float]:
    """Calculate absolute Z positions for a Z-stack."""
    if z_stacking_config == "FROM CENTER":
        start_offset = -delta_z * ((nz - 1) / 2.0)
    elif z_stacking_config == "FROM TOP":
        start_offset = -delta_z * (nz - 1)
    else:  # FROM BOTTOM
        start_offset = 0
    return [center_z + start_offset + i * delta_z for i in range(nz)]

# --- Progress Calculation ---
def calculate_progress(
    current_region: int,
    total_regions: int,
    current_fov: int,
    total_fovs: int,
) -> float:
    """Calculate overall progress as percentage."""
    if total_regions == 0 or total_fovs == 0:
        return 0.0
    region_progress = (current_region - 1) / total_regions
    fov_progress = current_fov / total_fovs
    return (region_progress + fov_progress / total_regions) * 100.0

def calculate_eta(start_time: float, progress_percent: float) -> Optional[float]:
    """Calculate estimated time remaining in seconds."""
    if progress_percent <= 0:
        return None
    elapsed = time.time() - start_time
    total_estimated = elapsed * 100.0 / progress_percent
    return total_estimated - elapsed

# --- Autofocus Decision ---
def should_run_autofocus(
    af_fov_count: int,
    fovs_per_af: int,
    do_autofocus: bool,
    nz: int,
    z_stacking_config: str,
) -> bool:
    """Determine if autofocus should run at this FOV."""
    if not do_autofocus:
        return False
    if nz > 1 and z_stacking_config != "FROM CENTER":
        return False
    return af_fov_count % fovs_per_af == 0
```

### B. Refactor Long Methods
Split `run_acquisition()` (~280 lines) into focused private methods:
- `_can_start_acquisition()` - state check and validation
- `_prepare_scan_coordinates()` - coordinate setup
- `_build_acquisition_params()` - create params dataclass
- `_start_worker()` - thread creation

### C. Remove Wrapper Methods from Worker
With shared utilities (StageService `wait` param, ChannelModeService), delete ~100 lines of pass-through wrappers in `MultiPointWorker`.

### D. Simplify `acquire_at_position()` using pure functions

```python
def acquire_at_position(self, region_id: str, current_path: str, fov: int) -> None:
    # Use pure function for decision
    if should_run_autofocus(self.af_fov_count, FOVS_PER_AF, self.do_autofocus, self.NZ, self.z_stacking_config):
        self._perform_autofocus()

    # Use pure function for Z positions
    z_positions = calculate_z_positions(self.NZ, self.deltaZ, self.z_stacking_config, self._stage_get_pos().z_mm)

    for z_level, z_pos in enumerate(z_positions):
        self._stage_move_z_to(z_pos)
        for config in self.selected_configurations:
            file_id = generate_file_id(region_id, fov, z_level)
            self._acquire_single_image(config, file_id, current_path)
```

### E. Add Continuous AF Hook
Simple optional parameter for future continuous autofocus:
```python
if self._continuous_af:
    self._continuous_af.wait_for_lock()
# ... acquire ...
if self._continuous_af:
    self._continuous_af.re_engage()
```

### What We're NOT Doing
- ~~`AcquisitionCoordinator` state machine class~~
- ~~`ScanPlanner` class~~ (just functions)
- ~~`AcquisitionEngine` class~~
- ~~`DataArchiveService` class~~ (use experiment_io.py + existing job_processing.py)
- ~~Hardware-orchestrated FOV~~ (software triggering works fine)

## 3. `ApplicationContext` - No Changes Needed

Analysis showed the current structure is reasonable:
- Services built in one place with clear dependency injection
- Controllers receive explicit dependencies
- Hardware initialization is centralized

**What We're NOT Doing:**
- ~~`MicroscopeFactory` class~~
- ~~`ServiceFactory` class~~
- ~~`ControllerFactory` class~~

The original plan overestimated this as a problem.

## 4. Cleaning Up `Microscope`

### A. Remove Circular Dependency
The `Microscope` → `LiveController` back-reference must go:
- Add `FrameAcquisitionStarting` event
- Refactor `acquire_image()` to publish event instead of calling LiveController
- `IlluminationService` subscribes and handles illumination

### B. Remove Pass-Through Methods
Delete ~15 methods that just delegate to `self.stage`:
```python
# Remove these - callers use microscope.stage directly
def move_x(self, distance): self.stage.move_x(distance)
def get_x(self): return self.stage.get_pos().x_mm
# etc.
```

### C. Keep
- `build_from_global_config()` - factory method
- `home_xyz()` - complex homing sequence with real logic
- `close()` - cleanup orchestration

## 5. New Shared Utilities

### A. `ChannelModeService` (~40 lines)
Simple class that applies a `ChannelMode` config to hardware:
```python
class ChannelModeService:
    def apply(self, config: ChannelMode) -> None:
        # Set camera exposure/gain
        # Set illumination source/intensity
        # Set filter wheel position
```

**Consumers:** LiveController, MultiPointWorker, AutoFocusController

### B. `experiment_io.py` - Experiment File IO

**File:** `squid/backend/io/experiment_io.py`

Consolidate all experiment file IO into pure functions:

```python
# --- Experiment Setup ---
def generate_experiment_id(label: str) -> str:
    """Generate timestamped experiment ID."""
    return f"{label}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S.%f')}"

def create_experiment_folders(base_path, experiment_id, num_timepoints) -> str:
    """Create experiment folder structure. Returns experiment path."""

# --- Metadata Writing ---
def write_acquisition_params(experiment_path, params) -> None:
    """Write acquisition parameters JSON."""

def write_configurations_xml(experiment_path, configs) -> None:
    """Write channel configurations XML."""

def write_coordinates_csv(timepoint_path, coordinates_df) -> None:
    """Write coordinates CSV for a timepoint."""

def create_done_file(path) -> None:
    """Create 'done' marker file."""

# --- Convenience ---
def setup_experiment(base_path, label, params, configs, num_timepoints) -> tuple[str, str]:
    """Complete experiment setup in one call."""
```

**What stays in `job_processing.py`:**
- `SaveImageJob` - already well-structured for async image saving
- `JobRunner` - multiprocessing infrastructure
- OME-TIFF logic - complex but properly encapsulated

### C. StageService `wait` parameter
Add `wait: bool = True` to movement methods - eliminates wrapper methods.

## 6. Migration Strategy

Same "Strangler Fig" approach, but smaller scope:

1. **Phase 1:** Break circular dependency (Microscope → LiveController)
2. **Phase 2:** Add shared utilities (ChannelModeService, StageService wait, experiment IO)
3. **Phase 3:** Simplify MultiPointController using utilities + pure functions
4. **Phase 4:** Clean up Microscope pass-through methods

Each phase can be merged independently. No big-bang refactoring.

## 7. Future Consideration: `ScanCoordinates` Simplification

The `ScanCoordinates` class (~1400 lines) is also complex. Similar approach could be applied:

### Pure Functions to Extract to `scan_coordinate_utils.py`

```python
# --- Grid Generation ---
def generate_grid_positions(
    center_x: float,
    center_y: float,
    scan_size_mm: float,
    fov_width_mm: float,
    fov_height_mm: float,
    overlap_percent: float,
    shape: str,  # "Square", "Circle", "Rectangle"
) -> list[tuple[float, float]]:
    """Generate FOV positions for a scan region."""

def calculate_tiles_for_coverage(
    scan_size: float,
    fov_size: float,
    step_size: float,
) -> int:
    """Calculate number of tiles needed to cover scan area."""

def calculate_step_size(fov_size: float, overlap_percent: float) -> float:
    """Calculate step between tile centers."""

# --- Geometry ---
def is_point_in_polygon(x: float, y: float, vertices: np.ndarray) -> bool:
    """Ray casting algorithm for point-in-polygon."""

def is_fov_in_circle(
    x: float, y: float,
    center_x: float, center_y: float,
    radius_squared: float,
    fov_half: float,
) -> bool:
    """Check if all FOV corners are within circle."""

def is_within_stage_limits(x: float, y: float, limits: StageLimits) -> bool:
    """Check if coordinate is within stage travel limits."""

# --- Wellplate Math ---
def well_id_to_coordinates(
    row: int,
    col: int,
    a1_x: float,
    a1_y: float,
    spacing: float,
    offset_x: float = 0,
    offset_y: float = 0,
) -> tuple[float, float]:
    """Convert well grid position to stage coordinates."""

def index_to_well_letter(index: int) -> str:
    """Convert 0-based index to well letter (0='A', 25='Z', 26='AA')."""

def well_letter_to_index(letter: str) -> int:
    """Convert well letter to 0-based index."""

# --- S-Pattern Sorting ---
def apply_s_pattern(
    coordinates: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Reorder coordinates in S-pattern for efficient stage movement."""
```

**Benefits:**
- Grid generation logic becomes testable without camera/stage dependencies
- Geometry functions can be reused elsewhere
- `ScanCoordinates` class becomes thinner - just state + event handling

**Note:** This is marked as "future consideration" - lower priority than MultiPointController work.

---

## 8. LaserAutofocusController Simplification

The `LaserAutofocusController` (~1023 lines) is the largest controller with significant embedded image processing logic.

### A. Extract Image Processing to `laser_af_algorithms.py`

**File:** `squid/backend/processing/laser_af_algorithms.py`

Move all stateless image analysis out of the controller:

```python
# --- Spot Detection ---
def find_laser_spot_centroid(
    image: np.ndarray,
    threshold: float,
    background_subtract: bool = True,
) -> Optional[tuple[float, float]]:
    """Find centroid of laser reflection spot in image."""

def detect_spot_with_background_removal(
    image: np.ndarray,
    kernel_size: int = 51,
) -> np.ndarray:
    """Remove background and enhance spot for detection."""

# --- Displacement Measurement ---
def compute_displacement_from_correlation(
    ref_crop: np.ndarray,
    current_crop: np.ndarray,
    subpixel: bool = True,
) -> float:
    """Cross-correlation based displacement in pixels."""

def compute_displacement_from_centroid_shift(
    ref_centroid: tuple[float, float],
    current_centroid: tuple[float, float],
) -> float:
    """Simple centroid-based displacement."""

# --- Calibration ---
def calibrate_pixel_to_um(
    spot_positions_px: list[tuple[float, float]],
    z_positions_mm: list[float],
) -> float:
    """Compute um/pixel calibration from Z-scan data."""

def fit_focus_curve(
    z_positions: list[float],
    displacements: list[float],
) -> tuple[float, float]:
    """Linear fit to find slope and intercept."""

# --- Configuration Validation ---
def validate_laser_af_config(
    config: dict,
    camera_roi: tuple[int, int, int, int],
) -> LaserAFConfig:
    """Validate and clamp configuration parameters."""

def clamp_crop_region(
    crop_x: int,
    crop_y: int,
    crop_width: int,
    crop_height: int,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """Clamp crop region to image bounds."""
```

### B. Simplify Controller Methods

With pure functions extracted, the controller becomes:

```python
def measure_displacement(self) -> Optional[float]:
    # Acquire image
    image = self._camera_service.capture_single_frame()

    # Use pure function for centroid finding
    centroid = find_laser_spot_centroid(image, self._config.threshold)
    if centroid is None:
        return None

    # Use pure function for displacement
    displacement_px = compute_displacement_from_centroid_shift(
        self._reference_centroid, centroid
    )

    return displacement_px * self._um_per_pixel
```

### C. What Stays in Controller
- State machine (IDLE, INITIALIZING, TRACKING, etc.)
- Hardware orchestration (camera, piezo, stage coordination)
- Reference management (storing/clearing reference images)
- Event publishing

**Impact:** ~200 lines moved to pure functions, controller reduced to ~800 lines

---

## 9. LiveController Simplification

The `LiveController` (~756 lines) mixes timer logic and illumination handling with hardware orchestration.

### A. Extract Timer/FPS Logic to `live_utils.py`

**File:** `squid/backend/controllers/live_utils.py`

```python
# --- Timer Calculations ---
def calculate_timer_interval_ms(fps: float) -> float:
    """Convert FPS to timer interval in milliseconds."""
    if fps <= 0:
        return 1000.0  # Default 1 second
    return 1000.0 / fps

def should_skip_frame(
    frame_count: int,
    skip_ratio: int,
) -> bool:
    """Determine if frame should be skipped for display throttling."""
    return frame_count % skip_ratio != 0

# --- Trigger Mode Conversion ---
def trigger_mode_to_str(mode: TriggerMode) -> str:
    """Convert TriggerMode enum to string for camera."""
    mapping = {
        TriggerMode.SOFTWARE: "SOFTWARE",
        TriggerMode.HARDWARE: "HARDWARE",
        TriggerMode.CONTINUOUS: "CONTINUOUS",
    }
    return mapping.get(mode, "SOFTWARE")

def str_to_trigger_mode(s: str) -> TriggerMode:
    """Convert string to TriggerMode enum."""
    mapping = {
        "SOFTWARE": TriggerMode.SOFTWARE,
        "HARDWARE": TriggerMode.HARDWARE,
        "CONTINUOUS": TriggerMode.CONTINUOUS,
    }
    return mapping.get(s.upper(), TriggerMode.SOFTWARE)

# --- Illumination Config Extraction ---
def extract_illumination_params(
    config: ChannelMode,
) -> tuple[Optional[int], Optional[float]]:
    """Extract illumination channel and intensity from config."""
    channel = getattr(config, 'illumination_source', None)
    intensity = getattr(config, 'illumination_intensity', None)
    return channel, intensity

def validate_illumination_params(
    channel: Optional[int],
    intensity: Optional[float],
) -> bool:
    """Check if illumination parameters are valid for use."""
    if channel is None:
        return False
    if intensity is not None and intensity <= 0:
        return False
    return True
```

### B. Simplify Controller with Pure Functions

```python
def _start_triggered_acquisition(self) -> None:
    interval_ms = calculate_timer_interval_ms(self._trigger_fps)
    self._trigger_timer.start(int(interval_ms))

def _update_illumination_for_config(self, config: ChannelMode) -> None:
    channel, intensity = extract_illumination_params(config)
    if validate_illumination_params(channel, intensity):
        self._illumination_service.set_channel_power(channel, intensity)
```

**Impact:** ~50 lines moved to pure functions, cleaner timer/illumination logic

---

## 10. StreamHandler Rate Limiting

The `StreamHandler` (~200 lines) has embedded FPS/rate limiting logic.

### A. Extract to `stream_utils.py`

**File:** `squid/backend/io/stream_utils.py`

```python
from dataclasses import dataclass

@dataclass
class FPSState:
    """Mutable state for FPS tracking."""
    counter: int = 0
    timestamp_last: int = 0
    fps_measured: int = 0

def update_fps_counter(
    timestamp_now: int,
    state: FPSState,
) -> FPSState:
    """Update FPS counter, return new state."""
    if timestamp_now == state.timestamp_last:
        return FPSState(
            counter=state.counter + 1,
            timestamp_last=state.timestamp_last,
            fps_measured=state.fps_measured,
        )
    else:
        return FPSState(
            counter=0,
            timestamp_last=timestamp_now,
            fps_measured=state.counter,
        )

def should_process_frame(
    now: float,
    last_process_time: float,
    target_fps: float,
) -> bool:
    """Determine if enough time has passed to process next frame."""
    if target_fps <= 0:
        return True
    min_interval = 1.0 / target_fps
    return (now - last_process_time) >= min_interval
```

### B. Simplify StreamHandler

```python
def on_new_frame(self, frame: np.ndarray, timestamp: int) -> None:
    # Use pure function for FPS tracking
    self._fps_state = update_fps_counter(timestamp, self._fps_state)

    # Use pure function for rate limiting
    now = time.time()
    if should_process_frame(now, self._last_save_time, self._fps_save):
        self._last_save_time = now
        self._process_frame(frame)
```

**Impact:** ~20 lines moved to pure functions, rate limiting logic testable

---

## 11. Deferred Items

| Item | Reason |
|------|--------|
| StateStore / reactive state | EventBus + existing managers work fine |
| BufferManager / tiered workers | Profile first if performance becomes an issue |
| Hardware-orchestrated FOV | Software triggering is easier to debug |
| Factory class hierarchies | ApplicationContext is adequate |
