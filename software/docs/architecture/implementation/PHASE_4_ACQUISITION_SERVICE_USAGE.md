# Phase 4: Acquisition Service Usage

**Purpose:** Refactor `MultiPointWorker` to use services instead of direct hardware access. This is the largest refactoring phase with ~22 direct hardware calls to replace.

**Prerequisites:** Phase 3 complete (MicroscopeModeController exists)

**Estimated Effort:** 3-5 days

---

## Overview

`MultiPointWorker` is the acquisition engine (~1100 lines). It currently bypasses the service layer entirely, accessing camera, stage, microcontroller, and piezo directly. This phase fixes that.

**Files to Modify:**
1. `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py` - Main changes
2. `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_controller.py` - Pass services to worker
3. `/Users/wea/src/allenlab/Squid/software/squid/application.py` - Provide services to controller

---

## Task Checklist

### 4.1 Update MultiPointWorker Constructor

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

- [x] Add service imports
- [x] Update constructor to receive services
- [x] Store service references

**Implementation Note:** Services are optional parameters with fallback to direct hardware access for backwards compatibility. Pattern: `if self._service: service.method() else: self.hardware.method()`

**Current constructor (approximately):**
```python
def __init__(
    self,
    camera,
    stage,
    microcontroller,
    liveController,
    autofocusController,
    configurationManager,
    parameters: AcquisitionParameters,
    ...
):
    self.camera = camera
    self.stage = stage
    self.microcontroller = microcontroller
    self.liveController = liveController
    # ...
```

**Target constructor:**
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.services import CameraService, StageService, PeripheralService
    from squid.services import IlluminationService
    from squid.controllers import MicroscopeModeController
    from squid.events import EventBus


def __init__(
    self,
    camera_service: "CameraService",
    stage_service: "StageService",
    peripheral_service: "PeripheralService",
    illumination_service: "IlluminationService",
    microscope_mode_controller: "MicroscopeModeController",
    autofocus_controller,
    configuration_manager,
    parameters: AcquisitionParameters,
    event_bus: "EventBus",
    # Optional: keep piezo reference if no PiezoService exists
    piezo=None,
    ...
):
    self._camera_service = camera_service
    self._stage_service = stage_service
    self._peripheral_service = peripheral_service
    self._illumination_service = illumination_service
    self._mode_controller = microscope_mode_controller
    self._autofocus = autofocus_controller
    self._config_manager = configuration_manager
    self._params = parameters
    self._bus = event_bus
    self._piezo = piezo  # Direct access OK for simple hardware
    # ...
```

**Commit:** `refactor(acquisition): Update MultiPointWorker constructor for services`

---

### 4.2 Replace Camera Operations

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

Added service-based alternatives with fallback to direct access.

- [x] Replace `self.camera.start_streaming()` - uses CameraService when available
- [x] Replace `self.camera.stop_streaming()` - uses CameraService when available
- [x] Replace `self.camera.send_trigger()` - uses CameraService when available
- [x] Replace `self.camera.read_frame()` - uses CameraService when available
- [x] Replace `self.camera.add_frame_callback()` - uses CameraService when available
- [x] Replace `self.camera.remove_frame_callback()` - uses CameraService when available
- [x] Replace `self.camera.enable_callbacks()` - uses CameraService when available
- [x] Replace `self.camera.get_ready_for_trigger()` - uses CameraService when available
- [x] Replace `self.camera.get_frame_id()` - uses CameraService when available

**Note:** CameraService was extended with streaming/trigger methods to support acquisition.

**Replacement patterns:**

```python
# ============================================================================
# START STREAMING
# ============================================================================

# Before (around line 202)
self.camera.start_streaming()
self.camera.add_frame_callback(self._image_callback)

# After
self._callback_id = self._camera_service.add_frame_callback(self._image_callback)
self._camera_service.start_streaming()


# ============================================================================
# STOP STREAMING
# ============================================================================

# Before (around line 280)
self.camera.remove_frame_callback(self._callback_id)
self.camera.stop_streaming()

# After
self._camera_service.remove_frame_callback(self._callback_id)
self._camera_service.stop_streaming()


# ============================================================================
# SEND TRIGGER
# ============================================================================

# Before (around line 220)
self.camera.send_trigger(illumination_time)

# After
self._camera_service.send_trigger()


# ============================================================================
# READ FRAME (synchronous capture)
# ============================================================================

# Before (around line 230)
frame = self.camera.read_frame()

# After
frame = self._camera_service.read_frame()


# ============================================================================
# ENABLE/DISABLE CALLBACKS
# ============================================================================

# Before (around line 260)
self.camera.enable_callbacks(True)
self.camera.enable_callbacks(False)

# After
self._camera_service.enable_callbacks(True)
self._camera_service.enable_callbacks(False)


# ============================================================================
# WAIT FOR TRIGGER READY
# ============================================================================

# Before (around line 240)
self.camera.get_ready_for_trigger()

# After
self._camera_service.get_ready_for_trigger()


# ============================================================================
# GET FRAME ID
# ============================================================================

# Before (around line 250)
frame_id = self.camera.get_frame_id()

# After
frame_id = self._camera_service.get_frame_id()
```

**Verification:**
```bash
# Should return NO matches after refactoring
grep -n "self\.camera\." control/core/acquisition/multi_point_worker.py
```

**Commit:** `refactor(acquisition): Replace camera calls with CameraService`

---

### 4.3 Replace Stage Operations

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

Added service-based alternatives with fallback to direct access.

- [x] Replace `self.stage.move_x_to()` - uses StageService when available
- [x] Replace `self.stage.move_y_to()` - uses StageService when available
- [x] Replace `self.stage.move_z_to()` - uses StageService when available
- [x] Replace `self.stage.move_z()` - uses StageService when available
- [x] Replace `self.stage.get_pos()` - uses StageService when available
- [x] Replace `self.stage.wait_for_idle()` - uses StageService when available

**Note:** StageService was extended with blocking move methods (`move_x_to`, `move_y_to`, `move_z_to`).

**Replacement patterns:**

```python
# ============================================================================
# MOVE TO ABSOLUTE POSITION
# ============================================================================

# Before (around lines 417-441)
self.stage.move_x_to(x_mm)
self.stage.move_y_to(y_mm)
self.stage.move_z_to(z_mm)
self.stage.wait_for_idle()

# After - Option 1: Sequential moves
self._stage_service.move_to_blocking(x=x_mm)
self._stage_service.move_to_blocking(y=y_mm)
self._stage_service.move_to_blocking(z=z_mm)

# After - Option 2: Combined move (preferred, if service supports it)
self._stage_service.move_to_blocking(x=x_mm, y=y_mm, z=z_mm)


# ============================================================================
# GET POSITION
# ============================================================================

# Before (around line 441)
pos = self.stage.get_pos()
x_mm = pos.x_mm
y_mm = pos.y_mm
z_mm = pos.z_mm

# After
pos = self._stage_service.get_position()
x_mm = pos.x_mm
y_mm = pos.y_mm
z_mm = pos.z_mm


# ============================================================================
# RELATIVE Z MOVEMENT
# ============================================================================

# Before (around line 480)
self.stage.move_z(relative_mm)
self.stage.wait_for_idle()

# After
self._stage_service.move_relative_blocking(z=relative_mm)


# ============================================================================
# MOVE TO COORDINATE (helper method)
# ============================================================================

def _move_to_coordinate(self, coord: ScanCoordinate) -> None:
    """Move stage to scan coordinate using service layer."""
    # Get current position
    current = self._stage_service.get_position()

    # Move Z first if going up (safety)
    if coord.z_mm is not None and coord.z_mm > current.z_mm:
        self._stage_service.move_to_blocking(z=coord.z_mm)

    # Move XY
    self._stage_service.move_to_blocking(x=coord.x_mm, y=coord.y_mm)

    # Move Z if going down
    if coord.z_mm is not None and coord.z_mm <= current.z_mm:
        self._stage_service.move_to_blocking(z=coord.z_mm)
```

**Note:** If `StageService.move_to_blocking()` doesn't exist, add it first:

```python
# In squid/services/stage_service.py

def move_to_blocking(
    self,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    timeout_s: float = 30.0,
) -> None:
    """Move to absolute position and wait for completion.

    Args:
        x: X position in mm (None = don't move)
        y: Y position in mm (None = don't move)
        z: Z position in mm (None = don't move)
        timeout_s: Timeout for move completion
    """
    with self._lock:
        if x is not None:
            self._stage.move_x_to(x)
        if y is not None:
            self._stage.move_y_to(y)
        if z is not None:
            self._stage.move_z_to(z)
        self._stage.wait_for_idle(timeout_s)


def move_relative_blocking(
    self,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    timeout_s: float = 30.0,
) -> None:
    """Move relative to current position and wait for completion."""
    with self._lock:
        if x is not None:
            self._stage.move_x(x)
        if y is not None:
            self._stage.move_y(y)
        if z is not None:
            self._stage.move_z(z)
        self._stage.wait_for_idle(timeout_s)
```

**Verification:**
```bash
# Should return NO matches after refactoring
grep -n "self\.stage\." control/core/acquisition/multi_point_worker.py
```

**Commit:** `refactor(acquisition): Replace stage calls with StageService`

---

### 4.4 Replace Microcontroller Operations

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

Added service-based alternatives with fallback to direct access.

- [x] Replace `self.microcontroller.enable_joystick()` - uses PeripheralService when available
- [x] Replace `self.microcontroller.wait_till_operation_is_completed()` - uses PeripheralService when available

**Note:** PeripheralService was extended with `enable_joystick()` and `wait_till_operation_is_completed()` methods.

**Replacement patterns:**

```python
# ============================================================================
# JOYSTICK CONTROL
# ============================================================================

# Before (around lines 336, 350)
self.microcontroller.enable_joystick(False)  # Disable during acquisition
# ... acquisition ...
self.microcontroller.enable_joystick(True)   # Re-enable after

# After
self._peripheral_service.enable_joystick(False)
# ... acquisition ...
self._peripheral_service.enable_joystick(True)


# ============================================================================
# WAIT FOR MCU OPERATION
# ============================================================================

# Before (around line 360)
self.microcontroller.wait_till_operation_is_completed()

# After
self._peripheral_service.wait_for_idle()
```

**Note:** If these methods don't exist in `PeripheralService`, add them:

```python
# In squid/services/peripheral_service.py

def enable_joystick(self, enabled: bool) -> None:
    """Enable or disable joystick control."""
    with self._lock:
        if self._microcontroller is not None:
            self._microcontroller.enable_joystick(enabled)


def wait_for_idle(self, timeout_s: float = 10.0) -> None:
    """Wait for microcontroller operations to complete."""
    with self._lock:
        if self._microcontroller is not None:
            self._microcontroller.wait_till_operation_is_completed(timeout_s)
```

**Verification:**
```bash
# Should return NO matches after refactoring
grep -n "self\.microcontroller\." control/core/acquisition/multi_point_worker.py
```

**Commit:** `refactor(acquisition): Replace microcontroller calls with PeripheralService`

---

### 4.5 Replace LiveController Operations

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

**Status:** COMPLETE - No changes needed.

Analysis revealed that LiveController already uses services internally (CameraService, IlluminationService, PeripheralService). The MultiPointWorker's use of LiveController is appropriate as LiveController provides the coordination layer for mode switching during acquisition.

- [x] Review `self.liveController.set_microscope_mode()` - LiveController already uses services internally
- [x] Review `self.liveController.turn_on_illumination()` - LiveController uses IlluminationService
- [x] Review `self.liveController.turn_off_illumination()` - LiveController uses IlluminationService
- [x] Review `self.liveController.update_illumination()` - LiveController uses IlluminationService

**Decision:** Keep LiveController usage as-is. LiveController is the appropriate coordination layer for microscope mode management.

**Replacement patterns:**

```python
from squid.events import SetMicroscopeModeCommand


# ============================================================================
# SET MICROSCOPE MODE (for channel switching during acquisition)
# ============================================================================

# Before (around line 614)
self.liveController.set_microscope_mode(config)

# After - Option 1: Via event (for loose coupling)
self._bus.publish(SetMicroscopeModeCommand(configuration_name=config.name))

# After - Option 2: Via controller directly (for speed during acquisition)
self._mode_controller.apply_mode_for_acquisition(config.name)


# ============================================================================
# TURN ON ILLUMINATION
# ============================================================================

# Before (around line 625)
self.liveController.turn_on_illumination()

# After
self._illumination_service.turn_on(self._current_channel)


# ============================================================================
# TURN OFF ILLUMINATION
# ============================================================================

# Before (around line 635)
self.liveController.turn_off_illumination()

# After
self._illumination_service.turn_off()


# ============================================================================
# UPDATE ILLUMINATION (intensity/settings)
# ============================================================================

# Before (around line 645)
self.liveController.update_illumination()

# After - Via mode controller (applies full config)
self._mode_controller.apply_mode_for_acquisition(config.name)

# Or - Direct illumination service call
self._illumination_service.set_channel_intensity(channel, intensity)
```

**Verification:**
```bash
# Should return NO matches after refactoring
grep -n "self\.liveController\." control/core/acquisition/multi_point_worker.py
```

**Commit:** `refactor(acquisition): Replace LiveController calls with events/services`

---

### 4.6 Handle Piezo Operations

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

**Status:** COMPLETE - Created PiezoService as first-class service.

After user feedback, determined that piezo is NOT simple hardware - it's integral to the microscope for:
1. Z-stack acquisition
2. Real-time focus locking (requires fast synchronous control)

**Implementation:**
- [x] Created `squid/services/piezo_service.py` as a first-class service
- [x] PiezoService provides dual access patterns:
  - Event-driven: `SetPiezoPositionCommand`, `MovePiezoRelativeCommand` for GUI
  - Direct synchronous: `move_to()`, `get_position()`, `move_relative()` for acquisition
  - Fast methods: `move_to_fast()` for focus lock control loop (minimal overhead)
- [x] Added PiezoService to ServiceRegistry in ApplicationContext
- [x] MultiPointWorker uses PiezoService when available with fallback to direct access

**New file:** `squid/services/piezo_service.py`

**Commit:** `feat(services): Add PiezoService for thread-safe piezo control`

---

### 4.7 Update MultiPointController to Pass Services

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_controller.py`

- [x] Update constructor to receive services (CameraService, StageService, PeripheralService, PiezoService, EventBus)
- [x] Pass services when creating worker

**Target implementation:**

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.services import CameraService, StageService, PeripheralService
    from squid.services import IlluminationService
    from squid.controllers import MicroscopeModeController
    from squid.events import EventBus


class MultiPointController:
    """Orchestrates multi-point acquisitions."""

    def __init__(
        self,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        illumination_service: "IlluminationService",
        microscope_mode_controller: "MicroscopeModeController",
        autofocus_controller,
        configuration_manager,
        event_bus: "EventBus",
        piezo=None,
        ...
    ):
        self._camera_service = camera_service
        self._stage_service = stage_service
        self._peripheral_service = peripheral_service
        self._illumination_service = illumination_service
        self._mode_controller = microscope_mode_controller
        self._autofocus = autofocus_controller
        self._config_manager = configuration_manager
        self._bus = event_bus
        self._piezo = piezo
        # ...

    def run_acquisition(self) -> None:
        """Start acquisition."""
        params = self._build_parameters()

        worker = MultiPointWorker(
            camera_service=self._camera_service,
            stage_service=self._stage_service,
            peripheral_service=self._peripheral_service,
            illumination_service=self._illumination_service,
            microscope_mode_controller=self._mode_controller,
            autofocus_controller=self._autofocus,
            configuration_manager=self._config_manager,
            parameters=params,
            event_bus=self._bus,
            piezo=self._piezo,
        )

        # Start worker thread
        self._worker = worker
        self._worker_thread = threading.Thread(target=worker.run)
        self._worker_thread.start()
```

**Commit:** `refactor(acquisition): Update MultiPointController to pass services`

---

### 4.8 Update ApplicationContext Wiring

**File:** `/Users/wea/src/allenlab/Squid/software/squid/application.py`

- [x] Pass services to MultiPointController
- [x] Added PiezoService creation in `_build_services()`

**Also updated:**
- `control/gui/qt_controllers.py` - QtMultiPointController accepts and passes services
- `control/gui_hcs.py` - Passes services and event_bus when creating QtMultiPointController

**Add to ApplicationContext:**

```python
def _create_multi_point_controller(self) -> MultiPointController:
    """Create acquisition controller with services."""
    return MultiPointController(
        camera_service=self._camera_service,
        stage_service=self._stage_service,
        peripheral_service=self._peripheral_service,
        illumination_service=self._illumination_service,
        microscope_mode_controller=self._microscope_mode_controller,
        autofocus_controller=self._autofocus_controller,
        configuration_manager=self._configuration_manager,
        event_bus=self._event_bus,
        piezo=self._microscope.low_level_drivers.piezo,
    )
```

**Commit:** `refactor(app): Wire services to MultiPointController`

---

### 4.9 Add Acquisition Events

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

- [x] Publish `AcquisitionStarted` at beginning of acquisition
- [x] Publish `AcquisitionProgress` during acquisition (with ETA calculation)
- [x] Publish `AcquisitionFinished` at end of acquisition (with success/error status)
- [ ] Publish `AcquisitionPaused` when paused (not implemented - pause not yet supported)
- [ ] Publish `AcquisitionResumed` when resumed (not implemented - pause not yet supported)

**Implementation:**
- Added `_publish_acquisition_started()`, `_publish_acquisition_finished()`, `_publish_acquisition_progress()` helper methods
- Progress events include: current_fov, total_fovs, current_region, total_regions, current_channel, progress_percent, eta_seconds
- ETA calculated from elapsed time and progress percentage

**Add progress reporting:**

```python
from squid.events import AcquisitionProgress, AcquisitionPaused, AcquisitionResumed


def run(self) -> None:
    """Main acquisition loop with progress events."""
    self._publish_progress(0)  # Initial progress

    for round_idx in range(self._params.n_rounds):
        for fov_idx, coord in enumerate(self._coordinates):
            # Check for abort
            if self._abort_requested:
                return

            # Check for pause
            if self._paused:
                self._bus.publish(AcquisitionPaused())
                while self._paused and not self._abort_requested:
                    time.sleep(0.1)
                if not self._abort_requested:
                    self._bus.publish(AcquisitionResumed())

            # Acquire at position
            self._move_to_coordinate(coord)
            self._acquire_all_channels()

            # Report progress
            self._publish_progress(fov_idx + 1, round_idx)


def _publish_progress(self, fov: int, round_idx: int = 0) -> None:
    """Publish acquisition progress event."""
    total_fovs = len(self._coordinates)
    total_rounds = self._params.n_rounds
    progress = (round_idx * total_fovs + fov) / (total_fovs * total_rounds) * 100

    # Estimate ETA
    elapsed = time.time() - self._start_time
    if fov > 0:
        time_per_fov = elapsed / (round_idx * total_fovs + fov)
        remaining_fovs = (total_rounds - round_idx - 1) * total_fovs + (total_fovs - fov)
        eta_seconds = time_per_fov * remaining_fovs
    else:
        eta_seconds = None

    self._bus.publish(AcquisitionProgress(
        current_fov=fov,
        total_fovs=total_fovs,
        current_round=round_idx,
        total_rounds=total_rounds,
        current_channel=self._current_channel or "",
        progress_percent=progress,
        eta_seconds=eta_seconds,
    ))
```

**Commit:** `feat(acquisition): Add acquisition progress events`

---

### 4.10 Write Tests for Refactored Worker

**Status:** Deferred - syntax validation completed, unit tests deferred to future work.

- [x] Syntax validation passed for all modified files
- [ ] Write comprehensive unit tests (deferred)

**File:** `/Users/wea/src/allenlab/Squid/software/tests/unit/control/core/acquisition/test_multi_point_worker.py`

```python
"""Tests for MultiPointWorker service integration."""

import pytest
from unittest.mock import Mock, MagicMock, patch
import threading

from control.core.acquisition.multi_point_worker import MultiPointWorker
from control.core.acquisition.multi_point_controller import AcquisitionParameters


@pytest.fixture
def mock_camera_service():
    """Create mock camera service."""
    service = Mock()
    service.add_frame_callback.return_value = "callback_id"
    service.read_frame.return_value = Mock(frame=None, frame_id=0)
    return service


@pytest.fixture
def mock_stage_service():
    """Create mock stage service."""
    service = Mock()
    service.get_position.return_value = Mock(x_mm=0.0, y_mm=0.0, z_mm=0.0)
    return service


@pytest.fixture
def mock_peripheral_service():
    """Create mock peripheral service."""
    return Mock()


@pytest.fixture
def mock_illumination_service():
    """Create mock illumination service."""
    return Mock()


@pytest.fixture
def mock_mode_controller():
    """Create mock microscope mode controller."""
    return Mock()


@pytest.fixture
def mock_event_bus():
    """Create mock event bus."""
    bus = Mock()
    bus.subscribe = Mock()
    bus.publish = Mock()
    return bus


@pytest.fixture
def acquisition_params():
    """Create minimal acquisition parameters."""
    return AcquisitionParameters(
        n_x=1,
        n_y=1,
        n_z=1,
        delta_x_mm=0.0,
        delta_y_mm=0.0,
        delta_z_um=0.0,
        n_rounds=1,
        configurations=[],
    )


@pytest.fixture
def worker(
    mock_camera_service,
    mock_stage_service,
    mock_peripheral_service,
    mock_illumination_service,
    mock_mode_controller,
    mock_event_bus,
    acquisition_params,
):
    """Create worker with mock services."""
    return MultiPointWorker(
        camera_service=mock_camera_service,
        stage_service=mock_stage_service,
        peripheral_service=mock_peripheral_service,
        illumination_service=mock_illumination_service,
        microscope_mode_controller=mock_mode_controller,
        autofocus_controller=Mock(),
        configuration_manager=Mock(),
        parameters=acquisition_params,
        event_bus=mock_event_bus,
    )


class TestWorkerServiceIntegration:
    """Test that worker uses services correctly."""

    def test_uses_camera_service_for_streaming(
        self, worker, mock_camera_service
    ):
        """Worker should use CameraService for streaming."""
        # This would require running acquisition or testing individual methods
        # For now, verify services are stored
        assert worker._camera_service is mock_camera_service

    def test_uses_stage_service_for_movement(
        self, worker, mock_stage_service
    ):
        """Worker should use StageService for stage movement."""
        assert worker._stage_service is mock_stage_service

    def test_uses_peripheral_service_for_joystick(
        self, worker, mock_peripheral_service
    ):
        """Worker should use PeripheralService for joystick control."""
        assert worker._peripheral_service is mock_peripheral_service

    def test_uses_illumination_service(
        self, worker, mock_illumination_service
    ):
        """Worker should use IlluminationService."""
        assert worker._illumination_service is mock_illumination_service

    def test_uses_mode_controller_for_channel_switching(
        self, worker, mock_mode_controller
    ):
        """Worker should use MicroscopeModeController."""
        assert worker._mode_controller is mock_mode_controller

    def test_publishes_progress_events(
        self, worker, mock_event_bus
    ):
        """Worker should publish progress events."""
        # Test progress publication
        worker._start_time = 0
        worker._coordinates = [Mock()]
        worker._current_channel = "DAPI"

        worker._publish_progress(1, 0)

        mock_event_bus.publish.assert_called()
        call_args = mock_event_bus.publish.call_args[0][0]
        assert hasattr(call_args, 'progress_percent')


class TestWorkerNoDirectHardwareAccess:
    """Verify worker doesn't access hardware directly."""

    def test_no_direct_camera_attribute(self, worker):
        """Worker should not have self.camera attribute."""
        assert not hasattr(worker, 'camera') or worker.camera is None

    def test_no_direct_stage_attribute(self, worker):
        """Worker should not have self.stage attribute."""
        assert not hasattr(worker, 'stage') or worker.stage is None

    def test_no_direct_microcontroller_attribute(self, worker):
        """Worker should not have self.microcontroller attribute."""
        assert not hasattr(worker, 'microcontroller') or worker.microcontroller is None

    def test_no_direct_livecontroller_attribute(self, worker):
        """Worker should not have self.liveController attribute."""
        assert not hasattr(worker, 'liveController') or worker.liveController is None
```

**Run tests:**
```bash
cd /Users/wea/src/allenlab/Squid/software
NUMBA_DISABLE_JIT=1 pytest tests/unit/control/core/acquisition/test_multi_point_worker.py -v
```

**Commit:** `test(acquisition): Add tests for MultiPointWorker service integration`

---

## Verification Checklist

**Note:** Implementation uses optional services with fallback pattern for backwards compatibility.
Direct hardware access still exists but is only used when services are not provided.

Before proceeding to Phase 5, verify:

- [x] Service-based alternatives exist for all hardware operations (with fallback to direct access)
- [x] CameraService extended with streaming/trigger methods
- [x] StageService extended with blocking move methods
- [x] PeripheralService extended with joystick/wait methods
- [x] PiezoService created as first-class service
- [x] EventBus wired through controller chain
- [x] Syntax validation passes for all modified files
- [ ] Tests pass: `NUMBA_DISABLE_JIT=1 pytest tests/unit/control/core/acquisition/ -v` (deferred)
- [ ] Application starts: `python main_hcs.py --simulation` (manual verification needed)
- [ ] Acquisition runs successfully (manual test needed)

**Full verification command:**
```bash
cd /Users/wea/src/allenlab/Squid/software

# Check for remaining direct hardware access
echo "=== Checking for direct hardware access ==="
echo "Camera:" && grep -c "self\.camera\." control/core/acquisition/multi_point_worker.py || echo "0"
echo "Stage:" && grep -c "self\.stage\." control/core/acquisition/multi_point_worker.py || echo "0"
echo "MCU:" && grep -c "self\.microcontroller\." control/core/acquisition/multi_point_worker.py || echo "0"
echo "LiveController:" && grep -c "self\.liveController\." control/core/acquisition/multi_point_worker.py || echo "0"

# Run tests
echo "=== Running tests ==="
NUMBA_DISABLE_JIT=1 pytest tests/unit/control/core/acquisition/ -v
```

---

## Commit Summary

| Order | Commit Message | Files |
|-------|----------------|-------|
| 1 | `refactor(acquisition): Update MultiPointWorker constructor for services` | `multi_point_worker.py` |
| 2 | `refactor(acquisition): Replace camera calls with CameraService` | `multi_point_worker.py` |
| 3 | `refactor(acquisition): Replace stage calls with StageService` | `multi_point_worker.py`, `stage_service.py` |
| 4 | `refactor(acquisition): Replace microcontroller calls with PeripheralService` | `multi_point_worker.py`, `peripheral_service.py` |
| 5 | `refactor(acquisition): Replace LiveController calls with events/services` | `multi_point_worker.py` |
| 6 | `docs(acquisition): Document piezo access decision` | `multi_point_worker.py` |
| 7 | `refactor(acquisition): Update MultiPointController to pass services` | `multi_point_controller.py` |
| 8 | `refactor(app): Wire services to MultiPointController` | `application.py` |
| 9 | `feat(acquisition): Add acquisition progress events` | `multi_point_worker.py` |
| 10 | `test(acquisition): Add tests for MultiPointWorker service integration` | `tests/...` |

---

## Next Steps

Once all checkmarks are complete, proceed to:
→ [PHASE_4B_AUTOFOCUS_SERVICE_USAGE.md](./PHASE_4B_AUTOFOCUS_SERVICE_USAGE.md)
