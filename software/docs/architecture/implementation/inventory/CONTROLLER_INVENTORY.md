# Controller Layer Inventory

This document catalogs the current controller layer implementation in `control/core/`. It identifies what each controller does, which need refactoring, and how they should interact with services.

---

## Overview

| Controller | File | Status | Action |
|------------|------|--------|--------|
| **LiveController** | `control/core/display/live_controller.py` | Direct hardware access | Refactor to use services, absorb LiveService |
| **StreamHandler** | `control/core/display/stream_handler.py` | Good | Keep as-is (data plane) |
| **MultiPointController** | `control/core/acquisition/multi_point_controller.py` | Orchestration only | Keep as-is |
| **MultiPointWorker** | `control/core/acquisition/multi_point_worker.py` | Direct hardware access | Refactor to use services |
| **AutoFocusController** | `control/core/autofocus/auto_focus_controller.py` | Good | Keep (ensure uses services) |
| **LaserAutofocusController** | `control/core/autofocus/laser_auto_focus_controller.py` | Good | Keep as-is |
| **TrackingController** | `control/core/tracking/tracking.py` | Good | Keep as-is |
| **MicroscopeModeController** | N/A | Missing | Create from MicroscopeModeService |
| **PeripheralsController** | N/A | Missing | Create new |

---

## LiveController (Refactor - High Priority)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/display/live_controller.py`

**Purpose:** Controls live camera preview, triggering, and illumination.

### Current Implementation

**Key Properties:**
```python
self.camera  # Direct hardware reference!
self.microscope  # Direct hardware reference!
self.trigger_mode = TriggerMode.SOFTWARE
self.fps_trigger = 10.0
self.illumination_on = False
self.currentConfiguration = None
self.timer_trigger = None  # threading.Timer for software trigger
self.is_live = False
```

**Key Methods:**
| Method | What It Does | Issue |
|--------|--------------|-------|
| `start_live()` | Starts camera streaming + trigger | Directly calls `camera.start_streaming()` |
| `stop_live()` | Stops streaming + illumination | Directly calls `camera.stop_streaming()` |
| `trigger_acquisition()` | Sends software trigger | Directly calls `camera.send_trigger()` |
| `set_trigger_mode(mode)` | Sets trigger mode | Directly calls `camera.set_acquisition_mode()` |
| `set_trigger_fps(fps)` | Sets trigger FPS | OK - internal state |
| `set_microscope_mode(config)` | Applies channel config | Directly calls camera/illumination hardware |
| `turn_on_illumination()` | Turns on lights | Complex routing to various hardware |
| `turn_off_illumination()` | Turns off lights | Complex routing to various hardware |
| `update_illumination()` | Sets intensity/filters | Direct hardware access |

### Problems

1. **Direct Hardware Access:**
   ```python
   # Lines 150-170: Direct camera access
   self.camera.start_streaming()
   self.camera.send_trigger(exposure_time)
   self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
   ```

2. **Complex Illumination Routing:**
   ```python
   # Lines 250-350: Hard-coded routing to different illumination sources
   if self.microscope.illumination_controller:
       self.microscope.illumination_controller.turn_on_illumination(wavelength)
   elif self.microscope.addons.sci_microscopy_led_array:
       self.microscope.addons.sci_microscopy_led_array.turn_on_illumination()
   elif self.microscope.addons.xlight:
       # ...
   elif self.microscope.addons.dragonfly:
       # ...
   ```

3. **No Event Publishing:**
   - `start_live()` doesn't publish `LiveStateChanged`
   - `set_trigger_mode()` doesn't publish `TriggerModeChanged`
   - State changes are invisible to other components

4. **No Event Subscriptions:**
   - Doesn't subscribe to `StartLiveCommand`, `StopLiveCommand`
   - These are handled by `LiveService` which just delegates back

### Target State

After refactoring, `LiveController` should:
1. Receive `EventBus` in constructor
2. Subscribe to `StartLiveCommand`, `StopLiveCommand`, `SetTriggerModeCommand`, `SetTriggerFPSCommand`
3. Use `CameraService` for camera operations
4. Use `IlluminationService` for illumination operations
5. Publish `LiveStateChanged`, `TriggerModeChanged`, `TriggerFPSChanged`

### Refactoring Tasks (Phase 3)

- [ ] Add `EventBus` parameter to constructor
- [ ] Subscribe to `StartLiveCommand` and `StopLiveCommand`
- [ ] Subscribe to `SetTriggerModeCommand` and `SetTriggerFPSCommand`
- [ ] Replace `self.camera.*` calls with `self._camera_service.*` calls
- [ ] Replace illumination calls with `self._illumination_service.*` calls
- [ ] Publish state change events after operations
- [ ] Add `LiveState` dataclass to track state

---

## StreamHandler (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/display/stream_handler.py`

**Purpose:** Data plane for camera frames. Routes frames from camera to displays.

### Current Implementation

```python
class StreamHandler:
    def __init__(self):
        self._callbacks: list[Callable[[NDArray, dict], None]] = []
        self._display_fps = 30.0
        self._last_display_time = 0.0

    def on_new_frame(self, frame: CameraFrame) -> None:
        """Called from camera thread. Must be fast."""
        # Throttle for display
        now = time.time()
        if now - self._last_display_time < 1.0 / self._display_fps:
            return
        self._last_display_time = now

        # Distribute to callbacks
        for callback in self._callbacks:
            callback(frame.frame, {"frame_id": frame.frame_id})

    def add_callback(self, callback: Callable) -> None
    def remove_callback(self, callback: Callable) -> None
```

**Status:** This is the **data plane** - it handles high-frequency frame data. It correctly:
- Throttles for display performance
- Distributes to registered callbacks
- Stays out of the event bus (frames don't go through EventBus)

**Action:** Keep as-is. This is working correctly.

---

## QtStreamHandler (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/display/stream_handler.py`

**Purpose:** Qt signal bridge for thread-safe GUI updates.

```python
class QtStreamHandler(QObject):
    signal_new_frame = Signal(object, object)  # frame, metadata

    def __init__(self, stream_handler: StreamHandler):
        super().__init__()
        self._handler = stream_handler
        self._handler.add_callback(self._on_frame)

    def _on_frame(self, frame: NDArray, metadata: dict) -> None:
        # Emit Qt signal - received on GUI thread
        self.signal_new_frame.emit(frame, metadata)
```

**Status:** Correct implementation of thread-safe frame delivery to Qt widgets.

**Action:** Keep as-is.

---

## MultiPointController (Keep - Minor Updates)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_controller.py`

**Purpose:** Orchestrates multi-point acquisitions.

### Current Implementation

**Key Methods:**
| Method | What It Does |
|--------|--------------|
| `set_NX/NY/NZ(N)` | Set grid dimensions |
| `set_deltaX/Y/Z(delta)` | Set grid spacing |
| `set_Nt(N)` / `set_deltat(delta)` | Set timelapse params |
| `set_af_flag(bool)` | Enable autofocus |
| `run_acquisition()` | Start acquisition |
| `request_abort_aquisition()` | Stop acquisition |
| `get_acquisition_image_count()` | Estimate total frames |

**Current Flow:**
1. Validate acquisition settings
2. Create `MultiPointWorker` with `AcquisitionParameters`
3. Start worker in background thread
4. Worker sends callbacks for progress/completion

**Issues:**
1. Creates worker with direct hardware references
2. Should use events for start/stop

### Target State

- [ ] Subscribe to `StartAcquisitionCommand`, `StopAcquisitionCommand`, `PauseAcquisitionCommand`, `ResumeAcquisitionCommand`
- [ ] Pass services (not hardware) to `MultiPointWorker`
- [ ] Publish `AcquisitionStarted`, `AcquisitionProgress`, `AcquisitionFinished`

---

## MultiPointWorker (Refactor - High Priority)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

**Purpose:** Executes acquisition loop in worker thread.

### Current Implementation (~1100 lines)

**Direct Hardware Access (PROBLEM):**
```python
# Camera operations (lines 202-281)
self.camera.start_streaming()
self.camera.add_frame_callback(self._image_callback)
self.camera.send_trigger(illumination_time)
self.camera.read_frame()
self.camera.get_ready_for_trigger()
self.camera.remove_frame_callback()

# Stage operations (lines 417-668)
self.stage.move_x_to(x_mm)
self.stage.move_y_to(y_mm)
self.stage.move_z_to(z_mm)
self.stage.get_pos()
self.stage.move_z(relative_mm)
self.stage.wait_for_idle()

# Microcontroller (lines 331-367)
self.microcontroller.enable_joystick(bool)
self.microcontroller.wait_till_operation_is_completed()

# Piezo (lines 523, 1079, 1091)
self.piezo.move_to(z_um)
self.piezo.position

# LiveController (lines 614-645)
self.liveController.set_microscope_mode(config)
```

### Key Methods

| Method | What It Does | Hardware Access |
|--------|--------------|-----------------|
| `run()` | Main acquisition loop | Camera streaming |
| `run_single_time_point()` | Acquire all FOVs | Joystick disable |
| `run_coordinate_acquisition()` | Iterate through positions | Stage movement |
| `move_to_coordinate()` | Move to XYZ position | Stage movement |
| `acquire_at_position()` | Capture all channels at FOV | Camera, illumination |
| `_select_config()` | Apply channel settings | LiveController |
| `_image_callback()` | Process captured frame | Job submission |

### Target State

Replace all direct hardware calls with service calls:

| Current | Replace With |
|---------|--------------|
| `self.camera.start_streaming()` | `self._camera_service.start_streaming(callback)` |
| `self.camera.send_trigger()` | `self._camera_service.send_trigger()` |
| `self.stage.move_x_to(x)` | `self._stage_service.move_to_blocking(x=x)` |
| `self.stage.get_pos()` | `self._stage_service.get_position()` |
| `self.microcontroller.enable_joystick(b)` | `self._peripheral_service.enable_joystick(b)` |
| `self.liveController.set_microscope_mode(c)` | `self._bus.publish(SetMicroscopeModeCommand(...))` |

---

## AutoFocusController (Keep - Minor Review)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/autofocus/auto_focus_controller.py`

**Purpose:** Software autofocus using image-based focus metrics.

### Current Implementation

```python
class AutoFocusController:
    def __init__(self, camera, stage, stream_handler, ...):
        self.camera = camera  # Direct hardware
        self.stage = stage    # Direct hardware
        # ...

    def autofocus(self):
        """Run autofocus algorithm."""
        # Move Z through range
        # Capture images
        # Calculate focus metric
        # Find best Z
```

**Status:** Generally good design with worker thread pattern.

**Review Items:**
- [ ] Verify uses services or direct hardware
- [ ] Add event publishing for progress/completion if missing
- [ ] Consider subscribing to `StartAutofocusCommand`

---

## LaserAutofocusController (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/autofocus/laser_auto_focus_controller.py`

**Purpose:** Hardware laser autofocus control.

**Status:** Uses reflection-based laser displacement sensor. Different hardware path than software AF.

**Action:** Keep as-is. Review after other refactoring complete.

---

## TrackingController (Keep As-Is)

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/tracking/tracking.py`

**Purpose:** Real-time object tracking with stage following.

**Status:** Specialized workflow that coordinates camera and stage.

**Action:** Keep as-is. Review after other refactoring complete.

---

## MicroscopeModeController (Create New)

**File:** Does not exist. Create at `/Users/wea/src/allenlab/Squid/software/squid/controllers/microscope_mode_controller.py`

**Purpose:** Manage microscope channel/mode switching.

### Expected Implementation

```python
# squid/controllers/microscope_mode_controller.py

from dataclasses import dataclass, replace
import threading
from typing import TYPE_CHECKING

from squid.events import (
    SetMicroscopeModeCommand,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    MicroscopeModeChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services import CameraService, IlluminationService, FilterWheelService


@dataclass
class MicroscopeModeState:
    current_mode: str | None = None
    available_modes: list[str] = None


class MicroscopeModeController:
    """Manages microscope channel/mode switching.

    Coordinates camera settings, illumination, and filters when switching modes.
    """

    def __init__(
        self,
        camera_service: CameraService,
        illumination_service: IlluminationService,
        filter_wheel_service: FilterWheelService,
        channel_configs: dict,
        event_bus: EventBus,
    ):
        self._camera = camera_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._channel_configs = channel_configs
        self._bus = event_bus

        self._state = MicroscopeModeState(
            current_mode=None,
            available_modes=list(channel_configs.keys())
        )

        self._bus.subscribe(SetMicroscopeModeCommand, self._on_set_mode)

    @property
    def state(self) -> MicroscopeModeState:
        return self._state

    def _on_set_mode(self, cmd: SetMicroscopeModeCommand) -> None:
        mode = cmd.configuration_name

        if mode not in self._channel_configs:
            return

        config = self._channel_configs[mode]

        # Set camera parameters via events
        self._bus.publish(SetExposureTimeCommand(exposure_time_ms=config.exposure_ms))
        self._bus.publish(SetAnalogGainCommand(gain=config.analog_gain))

        # Set illumination via service
        self._illumination.set_channel_intensity(config.illumination_source, config.intensity)

        # Set filter wheel if specified
        if config.filter_wheel_position is not None and self._filter_wheel.is_available():
            self._filter_wheel.set_position(config.filter_wheel_position)

        self._state = replace(self._state, current_mode=mode)
        self._bus.publish(MicroscopeModeChanged(configuration_name=mode))

    def apply_mode_for_acquisition(self, mode: str) -> None:
        """Apply mode settings for acquisition (direct calls for speed)."""
        if mode not in self._channel_configs:
            return

        config = self._channel_configs[mode]

        # Direct service calls for efficiency during acquisition
        self._camera.set_exposure_time(config.exposure_ms)
        self._camera.set_analog_gain(config.analog_gain)
        self._illumination.set_channel_intensity(config.illumination_source, config.intensity)

        if config.filter_wheel_position is not None and self._filter_wheel.is_available():
            self._filter_wheel.set_position(config.filter_wheel_position)
```

---

## PeripheralsController (Create New)

**File:** Does not exist. Create at `/Users/wea/src/allenlab/Squid/software/squid/controllers/peripherals_controller.py`

**Purpose:** Handle objective changer, spinning disk, and piezo control.

### Expected Implementation

See `REVISED_ARCHITECTURE_V3.md` lines 1053-1246 for full implementation.

**Key Features:**
- Handles `SetObjectiveCommand`, `SetSpinningDiskPositionCommand`, `SetPiezoPositionCommand`
- Publishes `ObjectiveChanged`, `SpinningDiskStateChanged`, `PiezoPositionChanged`
- Coordinates with ObjectiveStore for pixel size updates

---

## Summary: Controller Layer Changes

| Controller | Current | Target | Action |
|------------|---------|--------|--------|
| LiveController | Direct hardware | Use services | Major refactor |
| StreamHandler | Good | Keep | None |
| MultiPointController | Orchestration | Add events | Minor updates |
| MultiPointWorker | Direct hardware | Use services | Major refactor |
| AutoFocusController | Mixed | Review | Check service usage |
| LaserAutofocusController | Good | Keep | None |
| TrackingController | Good | Keep | None |
| MicroscopeModeController | Missing | Create | New from MicroscopeModeService |
| PeripheralsController | Missing | Create | New |

---

## Controller-Service Dependencies

After refactoring, controllers will depend on services (not hardware):

```
LiveController
├── CameraService
├── IlluminationService
├── PeripheralService (for trigger)
└── StreamHandler

MicroscopeModeController
├── CameraService
├── IlluminationService
└── FilterWheelService

PeripheralsController
├── ObjectiveChanger (hardware - OK, no service needed)
├── SpinningDiskController (hardware - OK)
├── PiezoStage (hardware - OK)
└── ObjectiveStore

MultiPointController / MultiPointWorker
├── CameraService
├── StageService
├── IlluminationService
├── FilterWheelService
├── MicroscopeModeController
└── AutoFocusController

AutoFocusController
├── CameraService
├── StageService
└── StreamHandler
```
