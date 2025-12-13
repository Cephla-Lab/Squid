# Squid Refactor: Detailed Architecture Plan

## Current State Summary

### MultiPointWorker (1,342 lines) - 10+ concerns mixed:
| Concern | Lines | Methods |
|---------|-------|---------|
| Position iteration | 693-728 | `run_coordinate_acquisition`, `move_to_coordinate` |
| Z-stack choreography | 586-595, 880-898, 1313-1342 | `initialize_z_stack`, `prepare_z_stack`, `move_z_for_stack`, `move_z_back_after_stack` |
| Frame capture/trigger | 993-1104 | `acquire_camera_image`, `acquire_rgb_image` |
| Callback handling | 900-978 | `_image_callback`, `_process_camera_frame` |
| Job dispatching | 922-951, 665-691 | Job creation in callback, `_summarize_runner_outputs` |
| Progress tracking | 328-396 | `_publish_acquisition_progress`, `_publish_worker_progress` |
| Metadata recording | 597-626 | `initialize_coordinates_dataframe`, `update_coordinates_dataframe` |
| Timing control | 411-461 | Timepoint scheduling, skip/wait logic |
| Autofocus routing | 843-878 | `perform_autofocus` |
| Abort handling | 289-291, 1303-1311 | `request_abort`, `handle_acquisition_abort` |

### LiveController (35 methods) - 7 concerns mixed:
| Concern | Methods |
|---------|---------|
| State machine | 4-state lifecycle, `_publish_state_changed` |
| Camera streaming | `_start_live`, `_stop_live` |
| Trigger timing | 7 timer methods (`_start_new_timer`, etc.) |
| Illumination | `turn_on_illumination`, `turn_off_illumination`, `update_illumination` |
| Filter wheel | Nested in `update_illumination` |
| Mode/config | `set_microscope_mode`, `currentConfiguration` |
| Display scaling | `set_display_resolution_scaling` |

### AutoFocus - scattered across 5 files:
- `auto_focus_controller.py` - orchestrator + focus map
- `auto_focus_worker.py` - contrast sweep algorithm
- `laser_auto_focus_controller.py` - 30+ methods, 20 are 1-line wrappers
- `laser_af_settings_manager.py` - JSON persistence
- `ops/navigation/focus_map.py` - Z-height interpolation

---

## Target Architecture

### Principle: Modules by Single Responsibility

Each module does ONE thing. No module exceeds 300 lines. Dependencies flow downward only.

```
┌─────────────────────────────────────────────────────────────────┐
│                         APPLICATION                              │
│  main_window.py, ApplicationContext                             │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   WORKFLOWS     │  │   CONTROLLERS   │  │      UI         │
│  (ops/)         │  │   (mcs/)        │  │   (ui/)         │
│                 │  │                 │  │                 │
│ AcquisitionLoop │  │ LiveController  │  │ Widgets only    │
│ TimelapseMgr    │  │ AFController    │  │ No logic        │
└────────┬────────┘  └────────┬────────┘  └─────────────────┘
         │                    │
         └────────┬───────────┘
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                        OPERATIONS                                │
│  Reusable multi-service coordination functions                   │
│  capture_z_stack(), move_and_focus(), configure_channel()       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         SERVICES                                 │
│  CameraService, StageService, IlluminationService, etc.         │
│  Thread-safe hardware wrappers with events                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         DRIVERS                                  │
│  Hardware ABCs + vendor implementations                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Concrete Module Design

### 1. MultiPointWorker → Split into 6 modules

```
ops/acquisition/
├── multi_point_controller.py    # Entry point, state machine, abort (~150 lines)
├── acquisition_loop.py          # Main acquisition orchestration (~200 lines)
├── position_iterator.py         # Region/FOV iteration, pure generator (~100 lines)
├── z_stack.py                   # Z-stack movement logic (~120 lines)
├── frame_dispatcher.py          # Callback handling, job dispatch (~150 lines)
├── progress_reporter.py         # Progress calculation, event publishing (~100 lines)
├── metadata_recorder.py         # CaptureInfo assembly, DataFrame (~100 lines)
└── timing_scheduler.py          # Timepoint scheduling, wait logic (~80 lines)
```

#### 1.1 `position_iterator.py` - Pure iteration logic
```python
"""Generate position sequences for acquisition. No side effects."""

from dataclasses import dataclass
from typing import Iterator, List, Tuple

@dataclass(frozen=True)
class FOVPosition:
    region_id: str
    region_index: int
    fov_index: int
    x_mm: float
    y_mm: float
    z_mm: Optional[float]  # None if using autofocus

def iterate_positions(
    scan_regions: Dict[str, List[Tuple[float, float]]],
    region_names: List[str],
) -> Iterator[FOVPosition]:
    """Yield FOV positions in acquisition order."""
    for region_index, region_id in enumerate(region_names):
        coordinates = scan_regions[region_id]
        for fov_index, (x, y) in enumerate(coordinates):
            yield FOVPosition(
                region_id=region_id,
                region_index=region_index,
                fov_index=fov_index,
                x_mm=x,
                y_mm=y,
                z_mm=None,
            )

def count_total_fovs(scan_regions: Dict[str, List]) -> int:
    """Count total FOVs across all regions."""
    return sum(len(coords) for coords in scan_regions.values())
```

#### 1.2 `z_stack.py` - Z movement coordination
```python
"""Z-stack movement operations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

class ZStackDirection(Enum):
    FROM_TOP = "from_top"
    FROM_BOTTOM = "from_bottom"
    FROM_CENTER = "from_center"

@dataclass
class ZStackConfig:
    num_z: int
    delta_z_mm: float
    direction: ZStackDirection
    use_piezo: bool
    z_range_mm: Optional[float] = None

@dataclass
class ZStackState:
    """Mutable state for z-stack traversal."""
    start_z_mm: float
    current_z_index: int = 0

def calculate_z_positions(config: ZStackConfig, center_z_mm: float) -> List[float]:
    """Calculate all Z positions for a stack."""
    if config.num_z == 1:
        return [center_z_mm]

    if config.direction == ZStackDirection.FROM_CENTER:
        half_range = (config.num_z - 1) * config.delta_z_mm / 2
        start = center_z_mm - half_range
    elif config.direction == ZStackDirection.FROM_BOTTOM:
        start = center_z_mm
    else:  # FROM_TOP
        start = center_z_mm + (config.num_z - 1) * config.delta_z_mm

    positions = []
    for i in range(config.num_z):
        if config.direction == ZStackDirection.FROM_TOP:
            positions.append(start - i * config.delta_z_mm)
        else:
            positions.append(start + i * config.delta_z_mm)
    return positions

def move_to_z(
    z_mm: float,
    stage_service: StageService,
    piezo_service: Optional[PiezoService],
    use_piezo: bool,
    settle_time_ms: float = 0,
) -> float:
    """Move to Z position using stage or piezo. Returns actual position."""
    if use_piezo and piezo_service:
        piezo_service.move_to(z_mm * 1000)  # mm to um
        return piezo_service.get_position() / 1000
    else:
        stage_service.move_z_to(z_mm)
        stage_service.wait_for_idle()
        if settle_time_ms > 0:
            time.sleep(settle_time_ms / 1000)
        return stage_service.get_z()
```

#### 1.3 `frame_dispatcher.py` - Callback and job management
```python
"""Handle camera frames and dispatch to storage/display."""

from dataclasses import dataclass
from typing import Callable, List, Optional
import threading

@dataclass
class FrameDestination:
    """Where to send captured frames."""
    job_runners: List[Tuple[type, JobRunner]]
    stream_handler: Optional[StreamHandler]
    acquisition_stream: Optional[AcquisitionStream]
    event_bus: EventBus

class FrameDispatcher:
    """Receives frames from camera callback, dispatches to destinations."""

    def __init__(self, destinations: FrameDestination):
        self._destinations = destinations
        self._pending_capture: ThreadSafeValue[Optional[CaptureInfo]] = ThreadSafeValue(None)
        self._ready_for_next = threading.Event()
        self._ready_for_next.set()
        self._callback_idle = threading.Event()
        self._callback_idle.set()

    def prepare_capture(self, capture_info: CaptureInfo) -> None:
        """Set up metadata for next frame."""
        self._pending_capture.set(capture_info)
        self._ready_for_next.clear()

    def on_frame(self, frame: CameraFrame) -> None:
        """Camera callback - dispatch frame to all destinations."""
        if self._ready_for_next.is_set():
            return  # Spurious frame, ignore

        self._callback_idle.clear()
        try:
            capture_info = self._pending_capture.get_and_clear()
            if capture_info is None:
                return

            self._ready_for_next.set()

            # Dispatch to job runners (storage)
            for job_class, runner in self._destinations.job_runners:
                job = job_class(frame.image, capture_info)
                runner.submit(job)

            # Dispatch to display
            if self._destinations.stream_handler:
                self._destinations.stream_handler.on_new_image(frame, capture_info)

            # Dispatch to acquisition stream
            if self._destinations.acquisition_stream:
                self._destinations.acquisition_stream.on_capture(frame, capture_info)

        finally:
            self._callback_idle.set()

    def wait_for_frame(self, timeout_s: float) -> bool:
        """Wait for pending frame to be processed."""
        return self._ready_for_next.wait(timeout_s) and self._callback_idle.wait(timeout_s)
```

#### 1.4 `progress_reporter.py` - Progress calculation and events
```python
"""Track and report acquisition progress."""

from dataclasses import dataclass
from typing import Optional
import time

@dataclass
class AcquisitionProgress:
    current_fov: int
    total_fovs: int
    current_region: int
    total_regions: int
    current_z: int
    total_z: int
    current_channel: int
    total_channels: int
    current_timepoint: int
    total_timepoints: int
    elapsed_s: float
    eta_s: Optional[float]
    percent_complete: float

class ProgressReporter:
    """Calculate and publish acquisition progress."""

    def __init__(self, event_bus: EventBus, experiment_id: str):
        self._event_bus = event_bus
        self._experiment_id = experiment_id
        self._start_time: Optional[float] = None
        self._total_fovs = 0
        self._completed_fovs = 0

    def start(self, total_fovs: int, total_timepoints: int) -> None:
        self._start_time = time.time()
        self._total_fovs = total_fovs * total_timepoints
        self._completed_fovs = 0

    def report_fov_complete(
        self,
        region_index: int,
        total_regions: int,
        fov_index: int,
        fovs_in_region: int,
        timepoint: int,
        total_timepoints: int,
    ) -> None:
        self._completed_fovs += 1
        elapsed = time.time() - self._start_time

        if self._completed_fovs > 0:
            avg_time_per_fov = elapsed / self._completed_fovs
            remaining_fovs = self._total_fovs - self._completed_fovs
            eta = avg_time_per_fov * remaining_fovs
        else:
            eta = None

        progress = AcquisitionProgress(
            current_fov=fov_index,
            total_fovs=fovs_in_region,
            current_region=region_index,
            total_regions=total_regions,
            current_z=0,  # Set by caller
            total_z=0,
            current_channel=0,
            total_channels=0,
            current_timepoint=timepoint,
            total_timepoints=total_timepoints,
            elapsed_s=elapsed,
            eta_s=eta,
            percent_complete=(self._completed_fovs / self._total_fovs) * 100,
        )

        self._event_bus.publish(AcquisitionProgressEvent(
            experiment_id=self._experiment_id,
            progress=progress,
        ))
```

#### 1.5 `acquisition_loop.py` - Main orchestration (~200 lines)
```python
"""Main acquisition loop - coordinates all pieces."""

class AcquisitionLoop:
    """Orchestrates multi-position, multi-timepoint acquisition."""

    def __init__(
        self,
        # Services
        camera_service: CameraService,
        stage_service: StageService,
        piezo_service: Optional[PiezoService],
        illumination_service: Optional[IlluminationService],
        # Controllers
        live_controller: LiveController,
        af_controller: Optional[AutoFocusController],
        # Support modules
        frame_dispatcher: FrameDispatcher,
        progress_reporter: ProgressReporter,
        # Config
        config: AcquisitionConfig,
    ):
        self._camera = camera_service
        self._stage = stage_service
        self._piezo = piezo_service
        self._illumination = illumination_service
        self._live = live_controller
        self._af = af_controller
        self._dispatcher = frame_dispatcher
        self._progress = progress_reporter
        self._config = config
        self._abort_requested = threading.Event()

    def run(self) -> AcquisitionResult:
        """Execute the acquisition."""
        self._camera.add_frame_callback(self._dispatcher.on_frame)
        self._camera.start_streaming()

        try:
            for timepoint in range(self._config.num_timepoints):
                if self._abort_requested.is_set():
                    break
                self._run_timepoint(timepoint)
                self._wait_for_next_timepoint(timepoint)
        finally:
            self._camera.stop_streaming()
            self._camera.remove_frame_callback(self._dispatcher.on_frame)

        return AcquisitionResult(...)

    def _run_timepoint(self, timepoint: int) -> None:
        """Acquire all positions for one timepoint."""
        positions = list(iterate_positions(
            self._config.scan_regions,
            self._config.region_names,
        ))

        for pos in positions:
            if self._abort_requested.is_set():
                break
            self._acquire_at_position(pos, timepoint)

    def _acquire_at_position(self, pos: FOVPosition, timepoint: int) -> None:
        """Acquire all channels and z-planes at one position."""
        # Move to position
        self._stage.move_x_to(pos.x_mm)
        self._stage.move_y_to(pos.y_mm)
        self._stage.wait_for_idle()

        # Autofocus if needed
        if self._af and self._should_autofocus(pos):
            self._af.autofocus()
            self._af.wait_till_autofocus_has_completed()

        # Calculate z positions
        current_z = self._stage.get_z()
        z_positions = calculate_z_positions(self._config.z_stack, current_z)

        # Acquire z-stack
        for z_index, z_mm in enumerate(z_positions):
            move_to_z(z_mm, self._stage, self._piezo, self._config.z_stack.use_piezo)

            for channel in self._config.channels:
                self._acquire_frame(pos, z_index, channel, timepoint)

        # Return to center z
        if len(z_positions) > 1:
            move_to_z(current_z, self._stage, self._piezo, self._config.z_stack.use_piezo)

    def _acquire_frame(
        self,
        pos: FOVPosition,
        z_index: int,
        channel: ChannelConfig,
        timepoint: int,
    ) -> None:
        """Capture single frame."""
        # Configure channel
        self._live.set_microscope_mode(channel)

        # Build capture info
        capture_info = CaptureInfo(
            x_mm=pos.x_mm,
            y_mm=pos.y_mm,
            z_mm=self._stage.get_z(),
            channel=channel.name,
            z_index=z_index,
            timepoint=timepoint,
            region_id=pos.region_id,
            fov_index=pos.fov_index,
        )

        # Prepare dispatcher and trigger
        self._dispatcher.prepare_capture(capture_info)

        if self._live.trigger_mode == TriggerMode.SOFTWARE:
            self._illumination.turn_on(channel.illumination)
            self._camera.send_trigger()
            self._dispatcher.wait_for_frame(timeout_s=10)
            self._illumination.turn_off(channel.illumination)
        else:
            self._camera.send_trigger()
            self._dispatcher.wait_for_frame(timeout_s=10)

    def request_abort(self) -> None:
        self._abort_requested.set()
```

---

### 2. LiveController → Split into 3 modules

```
mcs/controllers/
├── live_controller.py           # State machine, start/stop (~150 lines)
├── trigger_manager.py           # Trigger timing logic (~100 lines)
└── channel_coordinator.py       # Illumination + filter wheel (~100 lines)
```

#### 2.1 `trigger_manager.py`
```python
"""Manage camera trigger timing."""

from enum import Enum
import threading
from typing import Callable, Optional

class TriggerMode(Enum):
    SOFTWARE = "software"
    HARDWARE = "hardware"
    CONTINUOUS = "continuous"

class TriggerManager:
    """Handle trigger timing for live and acquisition modes."""

    def __init__(
        self,
        camera_service: CameraService,
        on_trigger: Callable[[], None],
    ):
        self._camera = camera_service
        self._on_trigger = on_trigger
        self._mode = TriggerMode.SOFTWARE
        self._fps = 1.0
        self._timer: Optional[threading.Timer] = None
        self._running = False

    @property
    def mode(self) -> TriggerMode:
        return self._mode

    @property
    def fps(self) -> float:
        return self._fps

    def set_mode(self, mode: TriggerMode) -> None:
        was_running = self._running
        if was_running:
            self.stop()
        self._mode = mode
        if was_running:
            self.start()

    def set_fps(self, fps: float) -> None:
        self._fps = max(0.1, min(fps, 1000))
        if self._running and self._mode == TriggerMode.SOFTWARE:
            self._restart_timer()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._mode == TriggerMode.SOFTWARE:
            self._start_timer()
        elif self._mode == TriggerMode.CONTINUOUS:
            self._camera.start_continuous_acquisition()

    def stop(self) -> None:
        self._running = False
        self._stop_timer()
        if self._mode == TriggerMode.CONTINUOUS:
            self._camera.stop_continuous_acquisition()

    def _start_timer(self) -> None:
        interval = 1.0 / self._fps
        self._timer = threading.Timer(interval, self._timer_callback)
        self._timer.start()

    def _stop_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _restart_timer(self) -> None:
        self._stop_timer()
        if self._running:
            self._start_timer()

    def _timer_callback(self) -> None:
        if not self._running:
            return
        self._on_trigger()
        self._start_timer()  # Schedule next
```

#### 2.2 `channel_coordinator.py`
```python
"""Coordinate illumination and filter wheel for channel switching."""

from typing import Optional
import re

class ChannelCoordinator:
    """Manage illumination and filter wheel when switching channels."""

    def __init__(
        self,
        illumination_service: Optional[IlluminationService],
        filter_wheel_service: Optional[FilterWheelService],
        nl5_service: Optional[NL5Service],
    ):
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._nl5 = nl5_service
        self._current_channel: Optional[ChannelConfig] = None
        self._illumination_on = False
        self._auto_filter_switch = True

    def configure_channel(self, channel: ChannelConfig) -> None:
        """Set up illumination and filter for a channel."""
        self._current_channel = channel

        # Set filter wheel
        if self._auto_filter_switch and self._filter_wheel and channel.emission_filter:
            self._filter_wheel.set_position(channel.emission_filter)

        # Configure illumination intensity (don't turn on yet)
        if self._illumination:
            wavelength = self._extract_wavelength(channel.name)
            if wavelength:
                self._illumination.set_intensity(wavelength, channel.illumination_intensity)

        # Configure NL5 if fluorescence
        if self._nl5 and channel.is_fluorescence:
            self._nl5.set_power(channel.laser_power)

    def turn_on(self) -> None:
        """Turn on illumination for current channel."""
        if not self._current_channel or self._illumination_on:
            return
        if self._illumination:
            wavelength = self._extract_wavelength(self._current_channel.name)
            if wavelength:
                self._illumination.turn_on(wavelength)
        self._illumination_on = True

    def turn_off(self) -> None:
        """Turn off illumination."""
        if not self._illumination_on:
            return
        if self._illumination and self._current_channel:
            wavelength = self._extract_wavelength(self._current_channel.name)
            if wavelength:
                self._illumination.turn_off(wavelength)
        self._illumination_on = False

    @staticmethod
    def _extract_wavelength(channel_name: str) -> Optional[int]:
        """Extract wavelength from channel name like 'Fluorescence 488 nm'."""
        match = re.search(r'(\d{3})\s*nm', channel_name)
        return int(match.group(1)) if match else None
```

---

### 3. AutoFocus → Consolidate into 3 modules

```
mcs/controllers/autofocus/
├── __init__.py
├── af_controller.py             # Unified controller interface (~150 lines)
├── contrast_af.py               # Contrast-based algorithm (~150 lines)
├── laser_af.py                  # Laser reflection algorithm (~200 lines)
└── focus_map.py                 # 3-point interpolation (~100 lines)
```

Move `laser_af_settings_manager.py` functionality into `laser_af.py`.

---

### 4. Directory Clarification

**Final structure:**
```
squid/
├── core/
│   ├── abc.py                   # Hardware ABCs (unchanged)
│   ├── events.py                # EventBus + events (unchanged)
│   └── config.py                # Pydantic config models
│
├── mcs/                         # Hardware control layer
│   ├── services/                # Thread-safe hardware wrappers (unchanged)
│   ├── controllers/
│   │   ├── live_controller.py   # Refactored (smaller)
│   │   ├── trigger_manager.py   # NEW: extracted from LiveController
│   │   ├── channel_coordinator.py  # NEW: extracted from LiveController
│   │   ├── mode_controller.py   # Microscope mode switching
│   │   ├── peripherals_controller.py
│   │   └── autofocus/           # Consolidated AF
│   │       ├── af_controller.py
│   │       ├── contrast_af.py
│   │       ├── laser_af.py
│   │       └── focus_map.py
│   └── drivers/                 # Vendor implementations (unchanged)
│
├── ops/                         # Workflow layer
│   ├── acquisition/
│   │   ├── multi_point_controller.py  # State machine only
│   │   ├── acquisition_loop.py        # NEW: main orchestration
│   │   ├── position_iterator.py       # NEW: position generation
│   │   ├── z_stack.py                 # NEW: z-stack logic
│   │   ├── frame_dispatcher.py        # NEW: callback handling
│   │   ├── progress_reporter.py       # NEW: progress events
│   │   ├── metadata_recorder.py       # NEW: CaptureInfo/DataFrame
│   │   ├── timing_scheduler.py        # NEW: timepoint timing
│   │   └── job_processing.py          # Job runners (unchanged)
│   ├── navigation/              # Unchanged
│   └── configuration/           # Unchanged
│
└── ui/                          # Widgets (unchanged)
```

---

## Migration Plan

### Phase 1: Extract Pure Functions (No behavior change)

**Step 1.1: Extract `position_iterator.py`**
- Copy iteration logic from `run_coordinate_acquisition()`
- Create pure generator function
- Update `MultiPointWorker` to use it
- Run tests

**Step 1.2: Extract `z_stack.py`**
- Copy z-stack methods: `initialize_z_stack`, `prepare_z_stack`, `move_z_for_stack`, `move_z_back_after_stack`
- Create `calculate_z_positions()` and `move_to_z()` functions
- Update `MultiPointWorker` to use them
- Run tests

**Step 1.3: Extract `progress_reporter.py`**
- Copy progress calculation from `_publish_acquisition_progress`
- Create `ProgressReporter` class
- Update `MultiPointWorker` to use it
- Run tests

### Phase 2: Extract Callback Handling

**Step 2.1: Extract `frame_dispatcher.py`**
- Copy `_image_callback`, `_process_camera_frame`
- Create `FrameDispatcher` class with same thread-safety
- Update `MultiPointWorker` to use it
- Run tests (critical - callback timing)

**Step 2.2: Extract `metadata_recorder.py`**
- Copy DataFrame logic
- Update `MultiPointWorker` to use it
- Run tests

### Phase 3: Extract Timing

**Step 3.1: Extract `timing_scheduler.py`**
- Copy timepoint scheduling logic
- Update `MultiPointWorker` to use it
- Run tests

### Phase 4: Create Acquisition Loop

**Step 4.1: Create `acquisition_loop.py`**
- Compose all extracted modules
- `MultiPointWorker` becomes thin wrapper around `AcquisitionLoop`
- Run full integration tests

### Phase 5: Refactor LiveController

**Step 5.1: Extract `trigger_manager.py`**
- Copy timer logic
- Update `LiveController` to use it
- Run tests

**Step 5.2: Extract `channel_coordinator.py`**
- Copy illumination/filter logic
- Update `LiveController` to use it
- Run tests

### Phase 6: Consolidate AutoFocus

**Step 6.1: Clean up `laser_af.py`**
- Inline the 20 one-line wrapper methods
- Merge settings manager
- Run tests

**Step 6.2: Unify AF interface**
- Create common interface in `af_controller.py`
- Both contrast and laser AF implement it
- Run tests

---

## File Size Targets

| File | Current | Target |
|------|---------|--------|
| multi_point_worker.py | 1,342 | 150 (wrapper only) |
| acquisition_loop.py | N/A | 200 |
| position_iterator.py | N/A | 100 |
| z_stack.py | N/A | 120 |
| frame_dispatcher.py | N/A | 150 |
| progress_reporter.py | N/A | 100 |
| live_controller.py | ~400 | 150 |
| trigger_manager.py | N/A | 100 |
| channel_coordinator.py | N/A | 100 |
| laser_af.py | ~600 | 200 |

**Total acquisition code:** 1,342 → ~820 (split into testable units)
**Total live controller:** ~400 → ~350 (split into testable units)

---

## Dependency Rules

1. **ops/** may import from **mcs/** (workflows use hardware control)
2. **mcs/** may NOT import from **ops/** (hardware layer is independent)
3. **ui/** imports from both (but only for events and types)
4. All layers import from **core/** (abc, events, config)

---

## Testing Strategy

Each extracted module gets its own test file:
```
tests/unit/ops/acquisition/
├── test_position_iterator.py
├── test_z_stack.py
├── test_frame_dispatcher.py
├── test_progress_reporter.py
└── test_acquisition_loop.py
```

Pure functions (`position_iterator`, `z_stack`) can be tested without mocks.
Classes with dependencies use mock services.
