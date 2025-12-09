# Clean Architecture Design

This document describes the target architecture for the Squid microscopy control software. The goal is a clean, testable, maintainable system with clear separation of concerns—**while reusing ~90% of existing code**.

## Design Philosophy

**Simple, not clever.** Use straightforward Python patterns:
- Classes for state + behavior (flat, no deep inheritance)
- Dataclasses for pure data
- Protocols for hardware interfaces
- Composition over inheritance
- Explicit over implicit

**Reuse what works.** The existing codebase has many well-designed components. This architecture preserves them while clarifying responsibilities and resolving specific tangles.

**Incremental migration.** Rather than rewriting, we reclassify and refactor targeted areas.

---

## What Already Fits the Target Architecture

These components are well-designed and should be kept as-is:

| Component | Location | Status |
|-----------|----------|--------|
| **Composition root** | `squid/application.py` | ✓ Keep - `ApplicationContext` and `Controllers` already centralize DI |
| **EventBus** | `squid/events.py` | ✓ Keep - typed events, command/state taxonomy, debug mode |
| **Configuration** | `squid/config/*` | ✓ Keep - Pydantic models for hardware and acquisition config |
| **Hardware abstractions** | `squid/abc.py` | ✓ Keep - `AbstractCamera`, `AbstractStage`, `LightSource`, etc. |
| **Registry & simulation** | `squid/registry.py`, simulated drivers | ✓ Keep - enables testing without hardware |
| **Job processing** | `control/core/acquisition/job_processing.py` | ✓ Keep - `JobRunner`, `SaveImageJob` for async saving |
| **Stream handling** | `control/core/display/stream_handler.py` | ✓ Keep - frame routing, throttling, Qt signals |
| **Autofocus** | `control/core/autofocus/*` | ✓ Keep - well-structured controller + worker pattern |
| **Acquisition** | `control/core/acquisition/*` | ✓ Keep - `MultiPointController`, `MultiPointWorker` |
| **Navigation** | `control/core/navigation/*` | ✓ Keep - `ScanCoordinates`, `FocusMap`, `ObjectiveStore` |
| **Utilities** | `squid/utils/*` | ✓ Keep - `ThreadSafeValue`, `WorkerManager`, `safe_callback` |

---

## Problems to Resolve

Three main architectural tangles need to be addressed:

### 1. Services vs Controllers Overlap

**Current state:**
- `squid/services/*` (CameraService, StageService, LiveService, TriggerService, MicroscopeModeService) subscribe to events and implement both hardware operations AND behavior
- `control/core/*` controllers (LiveController, MultiPointController, AutoFocusController) ALSO orchestrate behavior and talk to hardware

**Problem:** Unclear who owns domain logic. Duplicated responsibilities.

**Solution:** 
- **Hardware services** (CameraService, StageService, PeripheralService) → Keep as event-subscribed hardware adapters
- **"Smart" services** (LiveService, TriggerService, MicroscopeModeService) → Reclassify as controllers or merge into existing controllers

### 2. Live Path Split Three Ways

**Current state:**
- `LiveService` listens to `StartLiveCommand`/`StopLiveCommand`
- `LiveController` has `start_live()`, `stop_live()`, `set_microscope_mode()`
- `StreamHandler`/`QtStreamHandler`/`ImageDisplay` handle frames

**Problem:** Three components share responsibility for "live view."

**Solution:** 
- **Control plane:** `LiveController` (merge `LiveService` into it)
- **Data plane:** `StreamHandler` → `QtStreamHandler` → widgets
- **Hardware access:** Via `CameraService`, `IlluminationService`

### 3. Acquisition Tightly Coupled to Hardware

**Current state:**
- `MultiPointWorker` directly accesses drivers in places
- Bypasses services for some operations

**Problem:** Hard to test, hard to swap hardware.

**Solution:** Audit `MultiPointWorker` to use services exclusively. Keep `MultiPointController` as the canonical acquisition orchestrator.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         GUI Widgets                             │
│              (render state, emit commands, no logic)            │
│                       control/widgets/                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
          EventBus                   StreamHandler
     (commands ↓ state ↑)          (frames → display)
       squid/events.py         control/core/display/
              │                           │
              │                           │
┌─────────────▼───────────────────────────┴───────────────────────┐
│                         Controllers                             │
│           (orchestration, state machines, workflows)            │
│                                                                 │
│   LiveController          MultiPointController (Acquisition)   │
│   AutoFocusController     MicroscopeModeController             │
│   TrackingController      PeripheralsController                │
│   LaserAFController                                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                      direct method calls
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      Hardware Services                          │
│            (thread-safe device access, validation)              │
│                                                                 │
│   CameraService       StageService       IlluminationService   │
│   PeripheralService   FilterWheelService                       │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                      direct method calls
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                   Hardware Abstractions                         │
│        (AbstractCamera, AbstractStage, LightSource, etc.)       │
│                         squid/abc.py                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      Hardware Drivers                           │
│              (vendor-specific implementations)                  │
│                    control/peripherals/                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer Responsibilities

| Layer | Responsibility | Communicates Via |
|-------|----------------|------------------|
| **Widgets** | Render state, capture user intent as commands | EventBus only |
| **Controllers** | Own state, orchestrate workflows, coordinate services | EventBus (subscribe + publish) + direct service calls |
| **Services** | Thread-safe hardware access, validation, simple transforms | EventBus (subscribe + publish) + direct hardware calls |
| **Hardware ABCs** | Define contracts for hardware | Direct method calls |
| **Drivers** | Implement hardware protocols | Vendor SDKs |

### Control Plane vs Data Plane

| Plane | Purpose | Mechanism | Examples |
|-------|---------|-----------|----------|
| **Control** | Commands, state changes, coordination | EventBus | `StartLiveCommand`, `ExposureTimeChanged` |
| **Data** | High-frequency real-time data | StreamHandler + Qt signals | Camera frames at 60fps |

**Key rule:** Frames never go through EventBus. Commands never go through StreamHandler.

---

## Threading Model

```
┌─────────────────────────────────────────────────────────────────┐
│                        GUI Thread                               │
│   Qt event loop, widget updates, user input                     │
│   RULE: Never block. Use signals for cross-thread updates.     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     EventBus Thread                             │
│   Processes queued events, calls handlers                       │
│   RULE: Handlers must not block. Spawn workers for long ops.   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     Camera Thread                               │
│   Frame callbacks from camera SDK                               │
│   RULE: Callbacks must be fast. StreamHandler throttles.       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     Worker Threads                              │
│   Long operations: acquisition loops, autofocus, stage moves   │
│   RULE: Use services (which have internal locks).              │
└─────────────────────────────────────────────────────────────────┘
```

**Service Thread Safety:**
- All services use `threading.RLock()` internally
- Service methods are blocking but thread-safe
- Multiple threads can call the same service safely

---

## 1. Hardware Abstractions (squid/abc.py)

**Status:** Keep existing abstractions. Add new protocols for peripheral hardware.

### Existing Protocols (Keep As-Is)

```python
# squid/abc.py - these are well-designed, keep them

class AbstractCamera(Protocol):
    """Full camera interface - ~40 methods."""
    # Exposure, gain, binning, ROI, streaming, triggering, etc.
    ...

class AbstractStage(Protocol):
    """Stage interface for XYZ (+ optional theta) movement."""
    # move_x/y/z(), move_x/y/z_to(), get_pos(), home(), zero()
    ...

class AbstractFilterWheelController(Protocol):
    """Filter wheel controller - may manage multiple wheels."""
    ...

class LightSource(Protocol):
    """Illumination source interface."""
    ...

# Data classes
@dataclass(frozen=True)
class Pos:
    x_mm: float
    y_mm: float
    z_mm: float
    theta_rad: float | None = None

@dataclass(frozen=True)
class CameraFrame:
    frame_id: int
    timestamp: float
    frame: NDArray
    frame_format: CameraFrameFormat
    frame_pixel_format: CameraPixelFormat
```

### New Protocols to Add

```python
# squid/abc.py - add these for peripheral hardware

@dataclass(frozen=True)
class ObjectiveInfo:
    """Metadata about an objective lens."""
    name: str
    magnification: float
    na: float
    pixel_size_um: float
    parfocal_offset_um: float = 0.0


class ObjectiveChanger(Protocol):
    """Motorized objective turret."""
    
    @property
    def current_position(self) -> int: ...
    
    @property
    def num_positions(self) -> int: ...
    
    def set_position(self, position: int) -> None: ...
    
    def get_objective_info(self, position: int) -> ObjectiveInfo | None: ...


class SpinningDiskController(Protocol):
    """Spinning disk confocal unit (xLight, Dragonfly, etc.)."""
    
    @property
    def is_disk_in(self) -> bool: ...
    
    @property
    def is_spinning(self) -> bool: ...
    
    @property
    def disk_motor_speed(self) -> int: ...
    
    @property
    def current_dichroic(self) -> int: ...
    
    @property
    def current_emission_filter(self) -> int: ...
    
    def set_disk_position(self, in_beam: bool) -> None: ...
    def set_spinning(self, spinning: bool) -> None: ...
    def set_disk_motor_speed(self, speed: int) -> None: ...
    def set_dichroic(self, position: int) -> None: ...
    def set_emission_filter(self, position: int) -> None: ...


class PiezoStage(Protocol):
    """Fast Z piezo for fine focus."""
    
    @property
    def position_um(self) -> float: ...
    
    @property
    def range_um(self) -> tuple[float, float]: ...
    
    def move_to(self, position_um: float) -> None: ...
    def move_relative(self, delta_um: float) -> None: ...
```

---

## 2. Hardware Services

**Location:** `squid/services/`

**Philosophy:** Services are thread-safe adapters for hardware. They:
- Subscribe to command events
- Call hardware methods (with locking)
- Publish state change events
- Do NOT contain complex orchestration logic

### Service Classification

| Service | Type | Keep/Change |
|---------|------|-------------|
| `CameraService` | Hardware | ✓ Keep as-is |
| `StageService` | Hardware | ✓ Keep as-is |
| `PeripheralService` | Hardware | ✓ Keep as-is |
| `LiveService` | Smart (orchestration) | ✗ Merge into `LiveController` |
| `TriggerService` | Mixed | ◐ Keep low-level parts, move mode logic to `LiveController` |
| `MicroscopeModeService` | Smart (orchestration) | ✗ Rename to `MicroscopeModeController`, move to controllers |

### CameraService (Keep As-Is)

```python
# squid/services/camera_service.py
# This is already well-designed. Keep it.

class CameraService(BaseService):
    """Thread-safe camera operations. Subscribes to camera commands."""
    
    def __init__(self, camera: AbstractCamera, event_bus: EventBus):
        super().__init__(event_bus)
        self._camera = camera
        self._lock = threading.RLock()
        
        # Subscribe to commands
        self.subscribe(SetExposureTimeCommand, self._on_set_exposure)
        self.subscribe(SetAnalogGainCommand, self._on_set_gain)
        self.subscribe(SetBinningCommand, self._on_set_binning)
        self.subscribe(SetROICommand, self._on_set_roi)
        self.subscribe(SetPixelFormatCommand, self._on_set_pixel_format)
    
    def _on_set_exposure(self, cmd: SetExposureTimeCommand) -> None:
        with self._lock:
            min_exp, max_exp = self._camera.get_exposure_limits()
            clamped = max(min_exp, min(max_exp, cmd.exposure_time_ms))
            self._camera.set_exposure_time(clamped)
        self.publish(ExposureTimeChanged(exposure_time_ms=clamped))
    
    def _on_set_gain(self, cmd: SetAnalogGainCommand) -> None:
        with self._lock:
            gain_range = self._camera.get_gain_range()
            if gain_range is None:
                logger.warning("Camera does not support analog gain")
                return
            clamped = max(gain_range.min_gain, min(gain_range.max_gain, cmd.gain))
            self._camera.set_analog_gain(clamped)
        self.publish(AnalogGainChanged(gain=clamped))
    
    # ... other handlers ...
    
    # Direct access methods for controllers (not event-driven)
    def start_streaming(self, callback: Callable[[CameraFrame], None]) -> None:
        with self._lock:
            self._camera.add_frame_callback(callback)
            self._camera.start_streaming()
    
    def stop_streaming(self) -> None:
        with self._lock:
            self._camera.stop_streaming()
    
    def capture_single(self) -> CameraFrame:
        with self._lock:
            return self._camera.read_camera_frame()
    
    def send_trigger(self) -> None:
        with self._lock:
            self._camera.send_trigger()
    
    def get_exposure(self) -> float:
        with self._lock:
            return self._camera.get_exposure_time()
    
    def get_gain(self) -> float:
        with self._lock:
            return self._camera.get_analog_gain()
    
    def set_acquisition_mode(self, mode: CameraAcquisitionMode) -> None:
        with self._lock:
            self._camera.set_acquisition_mode(mode)
```

### StageService (Keep As-Is)

```python
# squid/services/stage_service.py
# This is already well-designed. Keep it.

class StageService(BaseService):
    """Thread-safe stage operations. Subscribes to movement commands."""
    
    def __init__(self, stage: AbstractStage, event_bus: EventBus):
        super().__init__(event_bus)
        self._stage = stage
        self._lock = threading.RLock()
        
        self.subscribe(MoveStageCommand, self._on_move_relative)
        self.subscribe(MoveStageToCommand, self._on_move_to)
        self.subscribe(HomeStageCommand, self._on_home)
        self.subscribe(ZeroStageCommand, self._on_zero)
        self.subscribe(MoveStageToLoadingPositionCommand, self._on_loading)
        self.subscribe(MoveStageToScanningPositionCommand, self._on_scanning)
    
    def _on_move_relative(self, cmd: MoveStageCommand) -> None:
        # Spawn thread to avoid blocking EventBus
        threading.Thread(
            target=self._do_move_relative,
            args=(cmd.axis, cmd.distance_mm),
            daemon=True
        ).start()
    
    def _do_move_relative(self, axis: str, distance: float) -> None:
        with self._lock:
            if axis == 'x':
                self._stage.move_x(distance)
            elif axis == 'y':
                self._stage.move_y(distance)
            elif axis == 'z':
                self._stage.move_z(distance)
            self._stage.wait_for_idle()
            pos = self._stage.get_pos()
        self._publish_position(pos)
    
    def _on_move_to(self, cmd: MoveStageToCommand) -> None:
        threading.Thread(
            target=self._do_move_to,
            args=(cmd.x_mm, cmd.y_mm, cmd.z_mm),
            daemon=True
        ).start()
    
    def _do_move_to(self, x: float | None, y: float | None, z: float | None) -> None:
        with self._lock:
            if x is not None:
                self._stage.move_x_to(x)
            if y is not None:
                self._stage.move_y_to(y)
            if z is not None:
                self._stage.move_z_to(z)
            self._stage.wait_for_idle()
            pos = self._stage.get_pos()
        self._publish_position(pos)
    
    def _publish_position(self, pos: Pos) -> None:
        self.publish(StagePositionChanged(
            x_mm=pos.x_mm,
            y_mm=pos.y_mm,
            z_mm=pos.z_mm,
            theta_rad=pos.theta_rad
        ))
    
    # Direct access for controllers
    def move_to_blocking(self, x: float | None = None, y: float | None = None, z: float | None = None) -> Pos:
        """Blocking move for use by acquisition/autofocus."""
        with self._lock:
            if x is not None:
                self._stage.move_x_to(x)
            if y is not None:
                self._stage.move_y_to(y)
            if z is not None:
                self._stage.move_z_to(z)
            self._stage.wait_for_idle()
            pos = self._stage.get_pos()
        self._publish_position(pos)
        return pos
    
    def get_position(self) -> Pos:
        with self._lock:
            return self._stage.get_pos()
```

### PeripheralService (Keep As-Is)

```python
# squid/services/peripheral_service.py
# Already handles DAC, joystick, basic MCU operations. Keep it.

class PeripheralService(BaseService):
    """Thread-safe microcontroller peripheral access."""
    
    def __init__(self, microcontroller: Microcontroller, event_bus: EventBus):
        super().__init__(event_bus)
        self._mcu = microcontroller
        self._lock = threading.RLock()
        
        self.subscribe(SetDACCommand, self._on_set_dac)
        self.subscribe(TurnOnAFLaserCommand, self._on_af_laser_on)
        self.subscribe(TurnOffAFLaserCommand, self._on_af_laser_off)
    
    def _on_set_dac(self, cmd: SetDACCommand) -> None:
        with self._lock:
            clamped = max(0.0, min(100.0, cmd.value))
            self._mcu.set_dac(cmd.channel, clamped)
        self.publish(DACValueChanged(channel=cmd.channel, value=clamped))
    
    def _on_af_laser_on(self, cmd: TurnOnAFLaserCommand) -> None:
        with self._lock:
            self._mcu.turn_on_af_laser()
    
    def _on_af_laser_off(self, cmd: TurnOffAFLaserCommand) -> None:
        with self._lock:
            self._mcu.turn_off_af_laser()
    
    def add_joystick_button_listener(self, callback: Callable[[int], None]) -> None:
        with self._lock:
            self._mcu.add_joystick_button_listener(callback)
    
    def send_trigger(self) -> None:
        with self._lock:
            self._mcu.send_trigger()
```

### IlluminationService (New or Refactor)

```python
# squid/services/illumination_service.py
# Consolidate illumination control across all light sources

class IlluminationService(BaseService):
    """Thread-safe illumination control across multiple light sources."""
    
    def __init__(
        self,
        light_sources: dict[str, LightSource],  # channel name -> source
        microcontroller: Microcontroller | None,
        event_bus: EventBus
    ):
        super().__init__(event_bus)
        self._sources = light_sources
        self._mcu = microcontroller
        self._lock = threading.RLock()
        self._current_channel: str | None = None
    
    def set_channel_intensity(self, channel: str, intensity: float) -> None:
        """Set intensity for a channel (0-100%). Does not turn on."""
        with self._lock:
            if channel not in self._sources:
                logger.warning(f"Unknown illumination channel: {channel}")
                return
            clamped = max(0.0, min(100.0, intensity))
            self._sources[channel].set_intensity(clamped)
            self._current_channel = channel
    
    def turn_on(self, channel: str | None = None) -> None:
        """Turn on illumination. Uses current channel if not specified."""
        with self._lock:
            ch = channel or self._current_channel
            if ch is None or ch not in self._sources:
                return
            self._sources[ch].set_shutter_state(True)
            if self._mcu:
                self._mcu.turn_on_illumination()
    
    def turn_off(self) -> None:
        """Turn off all illumination."""
        with self._lock:
            for source in self._sources.values():
                source.set_shutter_state(False)
            if self._mcu:
                self._mcu.turn_off_illumination()
    
    def get_intensity(self, channel: str) -> float:
        with self._lock:
            if channel not in self._sources:
                return 0.0
            return self._sources[channel].get_intensity()
```

### FilterWheelService (New)

```python
# squid/services/filter_wheel_service.py

class FilterWheelService(BaseService):
    """Thread-safe filter wheel access."""
    
    def __init__(
        self,
        filter_wheel: AbstractFilterWheelController | None,
        event_bus: EventBus
    ):
        super().__init__(event_bus)
        self._wheel = filter_wheel
        self._lock = threading.RLock()
        
        if filter_wheel:
            self.subscribe(SetFilterPositionCommand, self._on_set_position)
    
    def _on_set_position(self, cmd: SetFilterPositionCommand) -> None:
        if not self._wheel:
            return
        with self._lock:
            self._wheel.set_filter_wheel_position(cmd.position, cmd.wheel_index)
            actual = self._wheel.get_filter_wheel_position(cmd.wheel_index)
        self.publish(FilterPositionChanged(position=actual, wheel_index=cmd.wheel_index))
    
    def set_position(self, position: int, wheel_index: int = 0) -> int:
        """Direct access for controllers."""
        if not self._wheel:
            raise HardwareNotAvailable("filter_wheel")
        with self._lock:
            self._wheel.set_filter_wheel_position(position, wheel_index)
            return self._wheel.get_filter_wheel_position(wheel_index)
    
    def get_position(self, wheel_index: int = 0) -> int:
        if not self._wheel:
            raise HardwareNotAvailable("filter_wheel")
        with self._lock:
            return self._wheel.get_filter_wheel_position(wheel_index)
    
    def is_available(self) -> bool:
        return self._wheel is not None
```

---

## 3. Data Plane: StreamHandler

**Location:** `control/core/display/stream_handler.py`

**Status:** Keep as-is. This is already the data plane for camera frames.

### Current Design (Already Good)

```python
# control/core/display/stream_handler.py

class StreamHandler:
    """
    Core frame router. Receives frames from camera, distributes to consumers.
    
    Responsibilities:
    - FPS throttling (don't overload display)
    - Resolution scaling for preview
    - Frame format conversion
    - Distribution to registered callbacks
    """
    
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
            try:
                callback(frame.frame, {"frame_id": frame.frame_id})
            except Exception as e:
                logger.exception(f"StreamHandler callback error: {e}")
    
    def add_callback(self, callback: Callable) -> None:
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)


class QtStreamHandler(QObject):
    """
    Qt signal bridge for StreamHandler.
    
    Marshals frame callbacks to Qt signal for thread-safe GUI updates.
    """
    
    signal_new_frame = Signal(object, object)  # frame, metadata
    
    def __init__(self, stream_handler: StreamHandler):
        super().__init__()
        self._handler = stream_handler
        self._handler.add_callback(self._on_frame)
    
    def _on_frame(self, frame: NDArray, metadata: dict) -> None:
        # Emit signal - will be received on GUI thread
        self.signal_new_frame.emit(frame, metadata)
```

### Optional: Add Typed Stream Interface

For cleaner typing, you can add a thin wrapper:

```python
# squid/data_streams.py (optional addition)

@dataclass(frozen=True)
class FrameData:
    """Typed frame data for stream subscribers."""
    frame: NDArray
    frame_id: int
    timestamp: float
    channel: str | None = None


class FrameStream:
    """Typed interface to StreamHandler's frame distribution."""
    
    def __init__(self, stream_handler: StreamHandler):
        self._handler = stream_handler
    
    def subscribe(self, callback: Callable[[FrameData], None]) -> None:
        def wrapper(frame: NDArray, metadata: dict):
            callback(FrameData(
                frame=frame,
                frame_id=metadata.get("frame_id", 0),
                timestamp=time.time(),
                channel=metadata.get("channel")
            ))
        self._handler.add_callback(wrapper)
    
    def unsubscribe(self, callback: Callable[[FrameData], None]) -> None:
        # Note: need to track wrapper mapping
        pass
```

---

## 4. Controllers

**Location:** `control/core/` (refactor), `squid/controllers/` (new)

Controllers own state, orchestrate workflows, and coordinate services. They subscribe to command events and publish state events.

### Controller Inventory

| Controller | Location | Status |
|------------|----------|--------|
| `LiveController` | `control/core/display/` | ✓ Keep + absorb `LiveService` |
| `MultiPointController` | `control/core/acquisition/` | ✓ Keep as acquisition controller |
| `AutoFocusController` | `control/core/autofocus/` | ✓ Keep |
| `LaserAutofocusController` | `control/core/autofocus/` | ✓ Keep |
| `TrackingController` | `control/core/tracking/` | ✓ Keep |
| `MicroscopeModeController` | `squid/controllers/` | ✗ NEW (from `MicroscopeModeService`) |
| `PeripheralsController` | `squid/controllers/` | ✗ NEW (for objective, spinning disk, piezo) |

### LiveController (Merge LiveService Into It)

```python
# control/core/display/live_controller.py

@dataclass
class LiveState:
    is_live: bool
    current_channel: str | None
    trigger_mode: str  # "Software", "Hardware", "Continuous"
    trigger_fps: float
    illumination_on: bool


class LiveController:
    """
    Controls live camera preview.
    
    Orchestrates: camera streaming, triggering, illumination, mode switching.
    Uses: CameraService, IlluminationService, StreamHandler.
    """
    
    def __init__(
        self,
        camera_service: CameraService,
        illumination_service: IlluminationService,
        peripheral_service: PeripheralService,
        stream_handler: StreamHandler,
        channel_configs: dict[str, ChannelConfig],
        event_bus: EventBus
    ):
        self._camera = camera_service
        self._illumination = illumination_service
        self._peripheral = peripheral_service
        self._stream_handler = stream_handler
        self._channel_configs = channel_configs
        self._bus = event_bus
        
        self._state = LiveState(
            is_live=False,
            current_channel=None,
            trigger_mode="Continuous",
            trigger_fps=10.0,
            illumination_on=False
        )
        
        self._trigger_timer: threading.Timer | None = None
        
        # Subscribe to commands (absorbing LiveService's responsibilities)
        self._bus.subscribe(StartLiveCommand, self._on_start_live)
        self._bus.subscribe(StopLiveCommand, self._on_stop_live)
        self._bus.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode)
        self._bus.subscribe(SetTriggerFPSCommand, self._on_set_trigger_fps)
    
    @property
    def state(self) -> LiveState:
        return self._state
    
    def _on_start_live(self, cmd: StartLiveCommand) -> None:
        if self._state.is_live:
            return
        
        # Apply channel configuration if specified
        channel = cmd.configuration
        if channel and channel in self._channel_configs:
            self._apply_channel_config(channel)
        
        # Start camera streaming
        self._camera.start_streaming(self._stream_handler.on_new_frame)
        
        # Start triggering
        self._start_triggering()
        
        # Turn on illumination
        if channel:
            self._illumination.turn_on(channel)
            self._state = dataclasses.replace(self._state, illumination_on=True)
        
        self._state = dataclasses.replace(
            self._state,
            is_live=True,
            current_channel=channel
        )
        self._bus.publish(LiveStateChanged(is_live=True, configuration=channel))
    
    def _on_stop_live(self, cmd: StopLiveCommand) -> None:
        if not self._state.is_live:
            return
        
        # Stop triggering
        self._stop_triggering()
        
        # Turn off illumination
        self._illumination.turn_off()
        
        # Stop camera streaming
        self._camera.stop_streaming()
        
        self._state = dataclasses.replace(
            self._state,
            is_live=False,
            illumination_on=False
        )
        self._bus.publish(LiveStateChanged(is_live=False, configuration=None))
    
    def _on_set_trigger_mode(self, cmd: SetTriggerModeCommand) -> None:
        old_mode = self._state.trigger_mode
        self._state = dataclasses.replace(self._state, trigger_mode=cmd.mode)
        
        # Update camera acquisition mode
        if cmd.mode == "Software":
            self._camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
        elif cmd.mode == "Hardware":
            self._camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
        else:  # Continuous
            self._camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
        
        # Restart triggering if live
        if self._state.is_live:
            self._stop_triggering()
            self._start_triggering()
        
        self._bus.publish(TriggerModeChanged(mode=cmd.mode))
    
    def _on_set_trigger_fps(self, cmd: SetTriggerFPSCommand) -> None:
        self._state = dataclasses.replace(self._state, trigger_fps=cmd.fps)
        
        # Restart software triggering if active
        if self._state.is_live and self._state.trigger_mode == "Software":
            self._stop_triggering()
            self._start_triggering()
        
        self._bus.publish(TriggerFPSChanged(fps=cmd.fps))
    
    def _apply_channel_config(self, channel: str) -> None:
        config = self._channel_configs[channel]
        
        # Set camera parameters (direct call, bypasses events for internal use)
        with self._camera._lock:
            self._camera._camera.set_exposure_time(config.exposure_ms)
            self._camera._camera.set_analog_gain(config.analog_gain)
        
        # Set illumination
        self._illumination.set_channel_intensity(config.illumination_source, config.intensity)
    
    def _start_triggering(self) -> None:
        if self._state.trigger_mode == "Software":
            self._schedule_software_trigger()
        elif self._state.trigger_mode == "Hardware":
            # Configure microcontroller for hardware triggering
            # (implementation depends on your hardware)
            pass
        # Continuous mode needs no triggering
    
    def _stop_triggering(self) -> None:
        if self._trigger_timer:
            self._trigger_timer.cancel()
            self._trigger_timer = None
    
    def _schedule_software_trigger(self) -> None:
        if not self._state.is_live:
            return
        
        interval = 1.0 / self._state.trigger_fps
        self._trigger_timer = threading.Timer(interval, self._do_software_trigger)
        self._trigger_timer.daemon = True
        self._trigger_timer.start()
    
    def _do_software_trigger(self) -> None:
        if self._state.is_live and self._state.trigger_mode == "Software":
            self._camera.send_trigger()
            self._schedule_software_trigger()
    
    # Methods for other controllers to coordinate
    def stop_for_acquisition(self) -> None:
        """Stop live view to allow acquisition to proceed."""
        if self._state.is_live:
            self._bus.publish(StopLiveCommand())
    
    def is_live(self) -> bool:
        return self._state.is_live
```

### MicroscopeModeController (Refactor from MicroscopeModeService)

```python
# squid/controllers/microscope_mode_controller.py

@dataclass
class MicroscopeModeState:
    current_mode: str | None
    available_modes: list[str]


class MicroscopeModeController:
    """
    Manages microscope channel/mode switching.
    
    When switching modes, coordinates:
    - Camera exposure/gain
    - Illumination source and intensity
    - Filter wheel position
    - Emission filter (if spinning disk)
    """
    
    def __init__(
        self,
        camera_service: CameraService,
        illumination_service: IlluminationService,
        filter_wheel_service: FilterWheelService,
        peripherals_controller: 'PeripheralsController',
        channel_configs: dict[str, ChannelConfig],
        event_bus: EventBus
    ):
        self._camera = camera_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._peripherals = peripherals_controller
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
            logger.warning(f"Unknown microscope mode: {mode}")
            return
        
        config = self._channel_configs[mode]
        
        # Set camera parameters
        self._bus.publish(SetExposureTimeCommand(exposure_time_ms=config.exposure_ms))
        self._bus.publish(SetAnalogGainCommand(gain=config.analog_gain))
        
        # Set illumination
        self._illumination.set_channel_intensity(config.illumination_source, config.intensity)
        
        # Set filter wheel if specified
        if config.filter_wheel_position is not None and self._filter_wheel.is_available():
            self._filter_wheel.set_position(config.filter_wheel_position)
        
        # Set emission filter (spinning disk) if specified
        if config.emission_filter_position is not None:
            self._bus.publish(SetDiskEmissionFilterCommand(position=config.emission_filter_position))
        
        self._state = dataclasses.replace(self._state, current_mode=mode)
        self._bus.publish(MicroscopeModeChanged(configuration_name=mode))
    
    def apply_mode_for_acquisition(self, mode: str) -> None:
        """Apply mode settings without publishing event. For acquisition use."""
        if mode not in self._channel_configs:
            return
        
        config = self._channel_configs[mode]
        
        # Direct calls for efficiency during acquisition
        with self._camera._lock:
            self._camera._camera.set_exposure_time(config.exposure_ms)
            self._camera._camera.set_analog_gain(config.analog_gain)
        
        self._illumination.set_channel_intensity(config.illumination_source, config.intensity)
        
        if config.filter_wheel_position is not None and self._filter_wheel.is_available():
            self._filter_wheel.set_position(config.filter_wheel_position)
```

### PeripheralsController (New)

```python
# squid/controllers/peripherals_controller.py

@dataclass(frozen=True)
class SpinningDiskState:
    is_available: bool
    is_disk_in: bool
    is_spinning: bool
    motor_speed: int
    dichroic: int
    emission_filter: int


@dataclass
class PeripheralsState:
    objective_position: int | None
    objective_info: ObjectiveInfo | None
    spinning_disk: SpinningDiskState | None
    piezo_position_um: float | None


class PeripheralsController:
    """
    Handles simple peripheral hardware that doesn't need complex orchestration.
    
    Manages: objective changer, spinning disk, piezo Z stage.
    (Filter wheel is handled by FilterWheelService; DAC by PeripheralService)
    """
    
    def __init__(
        self,
        objective_changer: ObjectiveChanger | None,
        spinning_disk: SpinningDiskController | None,
        piezo: PiezoStage | None,
        objective_store: ObjectiveStore,
        event_bus: EventBus
    ):
        self._objective_changer = objective_changer
        self._spinning_disk = spinning_disk
        self._piezo = piezo
        self._objective_store = objective_store
        self._bus = event_bus
        self._lock = threading.RLock()
        
        self._state = self._read_initial_state()
        
        # Objective commands
        if objective_changer:
            self._bus.subscribe(SetObjectiveCommand, self._on_set_objective)
        
        # Spinning disk commands
        if spinning_disk:
            self._bus.subscribe(SetSpinningDiskPositionCommand, self._on_set_disk_position)
            self._bus.subscribe(SetSpinningDiskSpinningCommand, self._on_set_spinning)
            self._bus.subscribe(SetDiskDichroicCommand, self._on_set_dichroic)
            self._bus.subscribe(SetDiskEmissionFilterCommand, self._on_set_emission)
        
        # Piezo commands
        if piezo:
            self._bus.subscribe(SetPiezoPositionCommand, self._on_set_piezo)
            self._bus.subscribe(MovePiezoRelativeCommand, self._on_move_piezo_relative)
    
    @property
    def state(self) -> PeripheralsState:
        return self._state
    
    def _read_initial_state(self) -> PeripheralsState:
        obj_pos = None
        obj_info = None
        if self._objective_changer:
            with self._lock:
                obj_pos = self._objective_changer.current_position
                obj_info = self._objective_changer.get_objective_info(obj_pos)
        
        disk_state = None
        if self._spinning_disk:
            with self._lock:
                disk_state = SpinningDiskState(
                    is_available=True,
                    is_disk_in=self._spinning_disk.is_disk_in,
                    is_spinning=self._spinning_disk.is_spinning,
                    motor_speed=self._spinning_disk.disk_motor_speed,
                    dichroic=self._spinning_disk.current_dichroic,
                    emission_filter=self._spinning_disk.current_emission_filter,
                )
        
        piezo_pos = None
        if self._piezo:
            with self._lock:
                piezo_pos = self._piezo.position_um
        
        return PeripheralsState(
            objective_position=obj_pos,
            objective_info=obj_info,
            spinning_disk=disk_state,
            piezo_position_um=piezo_pos,
        )
    
    # --- Objective ---
    def _on_set_objective(self, cmd: SetObjectiveCommand) -> None:
        if not self._objective_changer:
            return
        
        with self._lock:
            self._objective_changer.set_position(cmd.position)
            actual = self._objective_changer.current_position
            info = self._objective_changer.get_objective_info(actual)
        
        self._state = dataclasses.replace(
            self._state,
            objective_position=actual,
            objective_info=info
        )
        
        # Update objective store
        self._objective_store.set_current_objective(actual)
        
        self._bus.publish(ObjectiveChanged(position=actual, objective_info=info))
        
        # Publish pixel size change if info available
        if info:
            self._bus.publish(PixelSizeChanged(pixel_size_um=info.pixel_size_um))
    
    # --- Spinning Disk ---
    def _on_set_disk_position(self, cmd: SetSpinningDiskPositionCommand) -> None:
        if not self._spinning_disk:
            return
        
        with self._lock:
            self._spinning_disk.set_disk_position(cmd.in_beam)
        self._update_disk_state()
    
    def _on_set_spinning(self, cmd: SetSpinningDiskSpinningCommand) -> None:
        if not self._spinning_disk:
            return
        
        with self._lock:
            self._spinning_disk.set_spinning(cmd.spinning)
        self._update_disk_state()
    
    def _on_set_dichroic(self, cmd: SetDiskDichroicCommand) -> None:
        if not self._spinning_disk:
            return
        
        with self._lock:
            self._spinning_disk.set_dichroic(cmd.position)
        self._update_disk_state()
    
    def _on_set_emission(self, cmd: SetDiskEmissionFilterCommand) -> None:
        if not self._spinning_disk:
            return
        
        with self._lock:
            self._spinning_disk.set_emission_filter(cmd.position)
        self._update_disk_state()
    
    def _update_disk_state(self) -> None:
        if not self._spinning_disk:
            return
        
        with self._lock:
            disk_state = SpinningDiskState(
                is_available=True,
                is_disk_in=self._spinning_disk.is_disk_in,
                is_spinning=self._spinning_disk.is_spinning,
                motor_speed=self._spinning_disk.disk_motor_speed,
                dichroic=self._spinning_disk.current_dichroic,
                emission_filter=self._spinning_disk.current_emission_filter,
            )
        
        self._state = dataclasses.replace(self._state, spinning_disk=disk_state)
        self._bus.publish(SpinningDiskStateChanged(
            is_disk_in=disk_state.is_disk_in,
            is_spinning=disk_state.is_spinning,
            motor_speed=disk_state.motor_speed,
            dichroic=disk_state.dichroic,
            emission_filter=disk_state.emission_filter,
        ))
    
    # --- Piezo ---
    def _on_set_piezo(self, cmd: SetPiezoPositionCommand) -> None:
        if not self._piezo:
            return
        
        with self._lock:
            min_pos, max_pos = self._piezo.range_um
            clamped = max(min_pos, min(max_pos, cmd.position_um))
            self._piezo.move_to(clamped)
            actual = self._piezo.position_um
        
        self._state = dataclasses.replace(self._state, piezo_position_um=actual)
        self._bus.publish(PiezoPositionChanged(position_um=actual))
    
    def _on_move_piezo_relative(self, cmd: MovePiezoRelativeCommand) -> None:
        if not self._piezo:
            return
        
        with self._lock:
            self._piezo.move_relative(cmd.delta_um)
            actual = self._piezo.position_um
        
        self._state = dataclasses.replace(self._state, piezo_position_um=actual)
        self._bus.publish(PiezoPositionChanged(position_um=actual))
    
    # --- Convenience methods ---
    def has_objective_changer(self) -> bool:
        return self._objective_changer is not None
    
    def has_spinning_disk(self) -> bool:
        return self._spinning_disk is not None
    
    def has_piezo(self) -> bool:
        return self._piezo is not None
```

### MultiPointController / AcquisitionController (Keep + Refactor)

```python
# control/core/acquisition/multi_point_controller.py
# This is already the canonical acquisition controller. Keep it, but ensure it uses services.

class MultiPointController:
    """
    Orchestrates multi-point acquisitions.
    
    This is the existing controller - keep its structure, but ensure:
    1. Uses services (CameraService, StageService, etc.) not drivers directly
    2. Subscribes to acquisition commands via EventBus
    3. Publishes progress and completion events
    """
    
    def __init__(
        self,
        camera_service: CameraService,
        stage_service: StageService,
        illumination_service: IlluminationService,
        filter_wheel_service: FilterWheelService,
        microscope_mode_controller: MicroscopeModeController,
        autofocus_controller: AutoFocusController,
        live_controller: LiveController,
        job_runner: JobRunner,
        scan_coordinates: ScanCoordinates,
        focus_map: FocusMap,
        event_bus: EventBus
    ):
        self._camera = camera_service
        self._stage = stage_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._mode_controller = microscope_mode_controller
        self._autofocus = autofocus_controller
        self._live = live_controller
        self._job_runner = job_runner
        self._scan_coords = scan_coordinates
        self._focus_map = focus_map
        self._bus = event_bus
        
        # Acquisition state
        self._is_running = False
        self._is_paused = False
        self._stop_requested = threading.Event()
        self._pause_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        
        # Subscribe to commands
        self._bus.subscribe(StartAcquisitionCommand, self._on_start)
        self._bus.subscribe(StopAcquisitionCommand, self._on_stop)
        self._bus.subscribe(PauseAcquisitionCommand, self._on_pause)
        self._bus.subscribe(ResumeAcquisitionCommand, self._on_resume)
    
    def _on_start(self, cmd: StartAcquisitionCommand) -> None:
        if self._is_running:
            logger.warning("Acquisition already running")
            return
        
        # Stop live view if running
        self._live.stop_for_acquisition()
        
        self._is_running = True
        self._is_paused = False
        self._stop_requested.clear()
        self._pause_event.clear()
        
        config = cmd.config
        
        self._bus.publish(AcquisitionStarted(
            experiment_id=getattr(config, 'experiment_id', str(uuid.uuid4())),
            timestamp=time.time()
        ))
        
        # Start worker thread
        self._worker_thread = threading.Thread(
            target=self._run_acquisition,
            args=(config,),
            daemon=True
        )
        self._worker_thread.start()
    
    def _on_stop(self, cmd: StopAcquisitionCommand) -> None:
        self._stop_requested.set()
        self._pause_event.set()  # Unblock if paused
    
    def _on_pause(self, cmd: PauseAcquisitionCommand) -> None:
        self._is_paused = True
        self._pause_event.set()
        self._bus.publish(AcquisitionPaused())
    
    def _on_resume(self, cmd: ResumeAcquisitionCommand) -> None:
        self._is_paused = False
        self._pause_event.clear()
        self._bus.publish(AcquisitionResumed())
    
    def _run_acquisition(self, config: AcquisitionConfig) -> None:
        """
        Main acquisition loop. Runs in worker thread.
        
        KEY REFACTOR: This should use services, not driver methods directly.
        """
        try:
            positions = self._generate_positions(config)
            total_fovs = len(positions)
            channels = config.channels
            n_rounds = getattr(config, 'n_rounds', 1)
            
            start_time = time.time()
            images_captured = 0
            total_images = total_fovs * len(channels) * n_rounds
            
            for round_idx in range(n_rounds):
                for fov_idx, position in enumerate(positions):
                    if self._stop_requested.is_set():
                        break
                    
                    # Handle pause
                    while self._is_paused and not self._stop_requested.is_set():
                        time.sleep(0.1)
                    
                    # Move stage (using service)
                    self._stage.move_to_blocking(position.x_mm, position.y_mm, position.z_mm)
                    
                    # Run autofocus if enabled
                    if self._should_autofocus(config, fov_idx):
                        self._run_autofocus_at_position(position)
                    
                    # Acquire each channel
                    for channel_config in channels:
                        if self._stop_requested.is_set():
                            break
                        
                        self._acquire_channel(config, position, channel_config, fov_idx, round_idx)
                        images_captured += 1
                    
                    # Publish progress
                    self._publish_progress(fov_idx, total_fovs, round_idx, n_rounds, 
                                          images_captured, total_images, start_time)
            
            # Success
            self._is_running = False
            self._bus.publish(AcquisitionFinished(success=True))
            
        except Exception as e:
            logger.exception(f"Acquisition failed: {e}")
            self._is_running = False
            self._bus.publish(AcquisitionFinished(success=False, error=str(e)))
    
    def _acquire_channel(self, config, position, channel_config, fov_idx, round_idx) -> None:
        """Acquire single channel at current position."""
        
        # Apply microscope mode (camera settings, illumination, filters)
        self._mode_controller.apply_mode_for_acquisition(channel_config.name)
        
        # Turn on illumination
        self._illumination.turn_on(channel_config.illumination_source)
        
        # Capture frame (using service)
        frame = self._camera.capture_single()
        
        # Turn off illumination
        self._illumination.turn_off()
        
        # Queue save job (async)
        save_job = SaveImageJob(
            frame=frame.frame,
            metadata=self._build_metadata(config, position, channel_config, fov_idx, round_idx),
            output_path=config.save_path
        )
        self._job_runner.enqueue(save_job)
    
    def _generate_positions(self, config: AcquisitionConfig) -> list[Pos]:
        """Generate FOV positions from config."""
        return self._scan_coords.get_fov_coordinates(config)
    
    def _should_autofocus(self, config: AcquisitionConfig, fov_idx: int) -> bool:
        if not config.autofocus or not config.autofocus.enabled:
            return False
        return fov_idx % config.autofocus.every_n_fovs == 0
    
    def _run_autofocus_at_position(self, position: Pos) -> None:
        # Use focus map if available
        if self._focus_map.has_data():
            z_interpolated = self._focus_map.interpolate(position.x_mm, position.y_mm)
            self._stage.move_to_blocking(z=z_interpolated)
        else:
            # Run software autofocus
            self._autofocus.run_blocking()
    
    def _publish_progress(self, fov_idx, total_fovs, round_idx, n_rounds,
                         images_captured, total_images, start_time) -> None:
        elapsed = time.time() - start_time
        progress = images_captured / total_images if total_images > 0 else 0
        eta = (elapsed / progress - elapsed) if progress > 0.01 else None
        
        self._bus.publish(AcquisitionProgress(
            current_fov=fov_idx + 1,
            total_fovs=total_fovs,
            current_round=round_idx + 1,
            total_rounds=n_rounds,
            current_channel="",  # Could track this
            progress_percent=progress * 100,
            eta_seconds=eta
        ))
```

### AutoFocusController (Keep As-Is)

```python
# control/core/autofocus/auto_focus_controller.py
# This is already well-structured. Keep it.

class AutoFocusController:
    """
    Software autofocus using image-based focus metrics.
    
    Uses: StageService, CameraService
    Runs: AutofocusWorker via WorkerManager
    """
    
    def __init__(
        self,
        camera_service: CameraService,
        stage_service: StageService,
        stream_handler: StreamHandler,
        event_bus: EventBus
    ):
        self._camera = camera_service
        self._stage = stage_service
        self._stream_handler = stream_handler
        self._bus = event_bus
        
        self._n_planes = 10
        self._delta_z_um = 2.0
        self._focus_metric = "brenner"  # or "laplacian", etc.
        
        self._is_running = False
        self._worker_thread: threading.Thread | None = None
        
        self._bus.subscribe(StartAutofocusCommand, self._on_start)
        self._bus.subscribe(StopAutofocusCommand, self._on_stop)
        self._bus.subscribe(SetAutofocusParamsCommand, self._on_set_params)
    
    def _on_start(self, cmd: StartAutofocusCommand) -> None:
        if self._is_running:
            return
        
        self._is_running = True
        self._worker_thread = threading.Thread(target=self._run_autofocus, daemon=True)
        self._worker_thread.start()
    
    def _on_stop(self, cmd: StopAutofocusCommand) -> None:
        self._is_running = False
    
    def _run_autofocus(self) -> None:
        """Autofocus algorithm. Runs in worker thread."""
        try:
            current_z = self._stage.get_position().z_mm
            z_start = current_z - (self._n_planes // 2) * (self._delta_z_um / 1000)
            
            best_z = current_z
            best_score = -1
            
            for i in range(self._n_planes):
                if not self._is_running:
                    break
                
                z = z_start + i * (self._delta_z_um / 1000)
                self._stage.move_to_blocking(z=z)
                
                frame = self._camera.capture_single()
                score = self._calculate_focus_score(frame.frame)
                
                if score > best_score:
                    best_score = score
                    best_z = z
                
                self._bus.publish(AutofocusProgress(
                    current_step=i + 1,
                    total_steps=self._n_planes,
                    current_z=z,
                    best_z=best_z,
                    best_score=best_score
                ))
            
            # Move to best Z
            self._stage.move_to_blocking(z=best_z)
            
            self._is_running = False
            self._bus.publish(AutofocusCompleted(
                success=True,
                z_position=best_z,
                score=best_score
            ))
            self._bus.publish(FocusChanged(z_mm=best_z, source="autofocus"))
            
        except Exception as e:
            logger.exception(f"Autofocus failed: {e}")
            self._is_running = False
            self._bus.publish(AutofocusCompleted(success=False, z_position=None, score=None, error=str(e)))
    
    def _calculate_focus_score(self, image: NDArray) -> float:
        """Calculate focus metric. Pure function."""
        if self._focus_metric == "brenner":
            return brenner_score(image)
        elif self._focus_metric == "laplacian":
            return laplacian_score(image)
        else:
            return brenner_score(image)
    
    def run_blocking(self) -> float | None:
        """Run autofocus synchronously. For use by AcquisitionController."""
        # Simplified version for blocking calls
        current_z = self._stage.get_position().z_mm
        z_start = current_z - (self._n_planes // 2) * (self._delta_z_um / 1000)
        
        best_z = current_z
        best_score = -1
        
        for i in range(self._n_planes):
            z = z_start + i * (self._delta_z_um / 1000)
            self._stage.move_to_blocking(z=z)
            frame = self._camera.capture_single()
            score = self._calculate_focus_score(frame.frame)
            
            if score > best_score:
                best_score = score
                best_z = z
        
        self._stage.move_to_blocking(z=best_z)
        return best_z


# Focus metric functions (pure, no side effects)
def brenner_score(image: NDArray) -> float:
    """Brenner gradient focus metric."""
    if image.ndim == 3:
        image = image.mean(axis=2)
    diff = image[2:, :] - image[:-2, :]
    return np.sum(diff ** 2)


def laplacian_score(image: NDArray) -> float:
    """Laplacian variance focus metric."""
    if image.ndim == 3:
        image = image.mean(axis=2)
    laplacian = cv2.Laplacian(image.astype(np.float64), cv2.CV_64F)
    return laplacian.var()
```

---

## 5. Events

**Location:** `squid/events.py`

**Status:** Keep existing events. Add new ones for peripherals and progress.

### Existing Events (Keep As-Is)

```python
# squid/events.py - these already exist and are well-designed

# Commands (GUI → Controller/Service)
SetExposureTimeCommand      # exposure_time_ms
SetAnalogGainCommand        # gain
SetDACCommand               # channel, value
MoveStageCommand            # axis, distance_mm
MoveStageToCommand          # x_mm, y_mm, z_mm (all optional)
HomeStageCommand            # home_x, home_y, home_z
ZeroStageCommand            # zero_x, zero_y, zero_z
MoveStageToLoadingPositionCommand
MoveStageToScanningPositionCommand
StartLiveCommand            # configuration (optional)
StopLiveCommand
SetTriggerModeCommand       # mode
SetTriggerFPSCommand        # fps
SetMicroscopeModeCommand    # configuration_name
TurnOnAFLaserCommand
TurnOffAFLaserCommand

# State Events (Controller/Service → GUI)
ExposureTimeChanged         # exposure_time_ms
AnalogGainChanged           # gain
StagePositionChanged        # x_mm, y_mm, z_mm, theta_rad
DACValueChanged             # channel, value
LiveStateChanged            # is_live, configuration
ROIChanged                  # offset_x, offset_y, width, height
BinningChanged              # binning_x, binning_y
PixelFormatChanged          # pixel_format
TriggerModeChanged          # mode
TriggerFPSChanged           # fps
MicroscopeModeChanged       # configuration_name

# Acquisition Events
AcquisitionStarted          # experiment_id, timestamp
AcquisitionFinished         # success, error
```

### New Events to Add

```python
# squid/events.py - add these

# --- Peripheral Commands ---
@dataclass(frozen=True)
class SetFilterPositionCommand(Event):
    """Set filter wheel position."""
    position: int
    wheel_index: int = 0


@dataclass(frozen=True)
class SetObjectiveCommand(Event):
    """Change objective lens."""
    position: int


@dataclass(frozen=True)
class SetSpinningDiskPositionCommand(Event):
    """Move disk in/out of beam path."""
    in_beam: bool


@dataclass(frozen=True)
class SetSpinningDiskSpinningCommand(Event):
    """Start/stop disk spinning."""
    spinning: bool


@dataclass(frozen=True)
class SetDiskDichroicCommand(Event):
    """Set spinning disk dichroic position."""
    position: int


@dataclass(frozen=True)
class SetDiskEmissionFilterCommand(Event):
    """Set spinning disk emission filter position."""
    position: int


@dataclass(frozen=True)
class SetPiezoPositionCommand(Event):
    """Set piezo Z position (absolute)."""
    position_um: float


@dataclass(frozen=True)
class MovePiezoRelativeCommand(Event):
    """Move piezo Z relative to current position."""
    delta_um: float


# --- Peripheral State Events ---
@dataclass(frozen=True)
class FilterPositionChanged(Event):
    """Filter wheel position changed."""
    position: int
    wheel_index: int = 0


@dataclass(frozen=True)
class ObjectiveChanged(Event):
    """Objective lens changed."""
    position: int
    objective_info: ObjectiveInfo | None = None


@dataclass(frozen=True)
class PixelSizeChanged(Event):
    """Pixel size changed (due to objective or binning change)."""
    pixel_size_um: float


@dataclass(frozen=True)
class SpinningDiskStateChanged(Event):
    """Spinning disk state changed."""
    is_disk_in: bool
    is_spinning: bool
    motor_speed: int
    dichroic: int
    emission_filter: int


@dataclass(frozen=True)
class PiezoPositionChanged(Event):
    """Piezo Z position changed."""
    position_um: float


# --- Acquisition Progress Events ---
@dataclass(frozen=True)
class AcquisitionProgress(Event):
    """Progress update during acquisition."""
    current_fov: int
    total_fovs: int
    current_round: int
    total_rounds: int
    current_channel: str
    progress_percent: float
    eta_seconds: float | None = None


@dataclass(frozen=True)
class AcquisitionPaused(Event):
    """Acquisition was paused."""
    pass


@dataclass(frozen=True)
class AcquisitionResumed(Event):
    """Acquisition was resumed."""
    pass


# --- Autofocus Events ---
@dataclass(frozen=True)
class StartAutofocusCommand(Event):
    """Start autofocus."""
    pass


@dataclass(frozen=True)
class StopAutofocusCommand(Event):
    """Stop autofocus."""
    pass


@dataclass(frozen=True)
class SetAutofocusParamsCommand(Event):
    """Configure autofocus parameters."""
    n_planes: int | None = None
    delta_z_um: float | None = None
    focus_metric: str | None = None


@dataclass(frozen=True)
class AutofocusProgress(Event):
    """Autofocus progress update."""
    current_step: int
    total_steps: int
    current_z: float
    best_z: float | None
    best_score: float | None


@dataclass(frozen=True)
class AutofocusCompleted(Event):
    """Autofocus completed."""
    success: bool
    z_position: float | None
    score: float | None
    error: str | None = None


@dataclass(frozen=True)
class FocusChanged(Event):
    """Focus position changed."""
    z_mm: float
    source: str  # "autofocus", "manual", "focus_map", "laser_af"


# --- Start/Stop Acquisition Commands (may already exist) ---
@dataclass(frozen=True)
class StartAcquisitionCommand(Event):
    """Start multi-point acquisition."""
    config: AcquisitionConfig


@dataclass(frozen=True)
class StopAcquisitionCommand(Event):
    """Stop acquisition."""
    pass


@dataclass(frozen=True)
class PauseAcquisitionCommand(Event):
    """Pause acquisition."""
    pass


@dataclass(frozen=True)
class ResumeAcquisitionCommand(Event):
    """Resume paused acquisition."""
    pass
```

---

## 6. Widget Pattern

**Location:** `control/widgets/`

**Rule:** Widgets render state and emit commands. No business logic. No direct service/driver calls.

### Example: CameraSettingsWidget (Already Good)

```python
# control/widgets/camera/settings.py

class CameraSettingsWidget(QWidget):
    """Camera settings. Publishes commands, subscribes to state."""
    
    def __init__(self, event_bus: EventBus, parent: QWidget | None = None):
        super().__init__(parent)
        self._bus = event_bus
        
        self._setup_ui()
        
        # Subscribe to state events
        self._bus.subscribe(ExposureTimeChanged, self._on_exposure_changed)
        self._bus.subscribe(AnalogGainChanged, self._on_gain_changed)
        self._bus.subscribe(BinningChanged, self._on_binning_changed)
    
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        
        # Exposure
        self._exposure_spin = QDoubleSpinBox()
        self._exposure_spin.setRange(0.01, 10000)
        self._exposure_spin.setSuffix(" ms")
        self._exposure_spin.valueChanged.connect(self._on_exposure_input)
        layout.addWidget(self._exposure_spin)
        
        # Gain
        self._gain_spin = QDoubleSpinBox()
        self._gain_spin.setRange(0, 48)
        self._gain_spin.setSuffix(" dB")
        self._gain_spin.valueChanged.connect(self._on_gain_input)
        layout.addWidget(self._gain_spin)
    
    # User input → publish command
    def _on_exposure_input(self, value: float) -> None:
        self._bus.publish(SetExposureTimeCommand(exposure_time_ms=value))
    
    def _on_gain_input(self, value: float) -> None:
        self._bus.publish(SetAnalogGainCommand(gain=value))
    
    # State change → update display
    def _on_exposure_changed(self, event: ExposureTimeChanged) -> None:
        self._exposure_spin.blockSignals(True)
        self._exposure_spin.setValue(event.exposure_time_ms)
        self._exposure_spin.blockSignals(False)
    
    def _on_gain_changed(self, event: AnalogGainChanged) -> None:
        self._gain_spin.blockSignals(True)
        self._gain_spin.setValue(event.gain)
        self._gain_spin.blockSignals(False)
```

### Example: SpinningDiskWidget (New)

```python
# control/widgets/hardware/confocal.py

class SpinningDiskWidget(QWidget):
    """Controls for spinning disk confocal."""
    
    def __init__(self, event_bus: EventBus, parent: QWidget | None = None):
        super().__init__(parent)
        self._bus = event_bus
        
        self._setup_ui()
        
        self._bus.subscribe(SpinningDiskStateChanged, self._on_state_changed)
    
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        
        # Disk in/out toggle
        self._disk_in_checkbox = QCheckBox("Disk In Beam")
        self._disk_in_checkbox.toggled.connect(self._on_disk_in_toggled)
        layout.addWidget(self._disk_in_checkbox)
        
        # Spinning toggle
        self._spinning_checkbox = QCheckBox("Disk Spinning")
        self._spinning_checkbox.toggled.connect(self._on_spinning_toggled)
        layout.addWidget(self._spinning_checkbox)
        
        # Dichroic selector
        self._dichroic_combo = QComboBox()
        self._dichroic_combo.addItems(["Quad", "Dual 488/561", "Single 488", "Single 561"])
        self._dichroic_combo.currentIndexChanged.connect(self._on_dichroic_changed)
        layout.addWidget(self._dichroic_combo)
        
        # Emission filter selector
        self._emission_combo = QComboBox()
        self._emission_combo.addItems(["Open", "525/50", "600/50", "700/75"])
        self._emission_combo.currentIndexChanged.connect(self._on_emission_changed)
        layout.addWidget(self._emission_combo)
    
    # User input → publish commands
    def _on_disk_in_toggled(self, checked: bool) -> None:
        self._bus.publish(SetSpinningDiskPositionCommand(in_beam=checked))
    
    def _on_spinning_toggled(self, checked: bool) -> None:
        self._bus.publish(SetSpinningDiskSpinningCommand(spinning=checked))
    
    def _on_dichroic_changed(self, index: int) -> None:
        self._bus.publish(SetDiskDichroicCommand(position=index))
    
    def _on_emission_changed(self, index: int) -> None:
        self._bus.publish(SetDiskEmissionFilterCommand(position=index))
    
    # State change → update display
    def _on_state_changed(self, event: SpinningDiskStateChanged) -> None:
        self._disk_in_checkbox.blockSignals(True)
        self._disk_in_checkbox.setChecked(event.is_disk_in)
        self._disk_in_checkbox.blockSignals(False)
        
        self._spinning_checkbox.blockSignals(True)
        self._spinning_checkbox.setChecked(event.is_spinning)
        self._spinning_checkbox.blockSignals(False)
        
        self._dichroic_combo.blockSignals(True)
        self._dichroic_combo.setCurrentIndex(event.dichroic)
        self._dichroic_combo.blockSignals(False)
        
        self._emission_combo.blockSignals(True)
        self._emission_combo.setCurrentIndex(event.emission_filter)
        self._emission_combo.blockSignals(False)
```

### Example: NapariLiveWidget with StreamHandler

```python
# control/widgets/display/napari_live.py

class NapariLiveWidget(QWidget):
    """Live camera display using napari. Subscribes to StreamHandler."""
    
    def __init__(self, qt_stream_handler: QtStreamHandler, parent: QWidget | None = None):
        super().__init__(parent)
        self._qt_handler = qt_stream_handler
        
        self._setup_ui()
        
        # Subscribe to frame signal (thread-safe via Qt signal)
        self._qt_handler.signal_new_frame.connect(self._on_frame)
    
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        
        self._viewer = napari.Viewer(show=False)
        self._image_layer = self._viewer.add_image(np.zeros((512, 512), dtype=np.uint16))
        
        layout.addWidget(self._viewer.window._qt_window)
    
    def _on_frame(self, frame: NDArray, metadata: dict) -> None:
        """Called on GUI thread (via Qt signal)."""
        self._image_layer.data = frame
```

---

## 7. Application Context

**Location:** `squid/application.py`

**Status:** Keep existing structure. Update to create new controllers.

```python
# squid/application.py

@dataclass
class Services:
    """Container for all services."""
    camera: CameraService
    stage: StageService
    peripheral: PeripheralService
    illumination: IlluminationService
    filter_wheel: FilterWheelService


@dataclass
class Controllers:
    """Container for all controllers."""
    live: LiveController
    microscope_mode: MicroscopeModeController
    peripherals: PeripheralsController
    autofocus: AutoFocusController
    laser_autofocus: LaserAutofocusController | None
    acquisition: MultiPointController
    tracking: TrackingController | None


class ApplicationContext:
    """
    Dependency injection container.
    
    Creates and owns all components in the correct order.
    """
    
    def __init__(self, config: AppConfig, simulated: bool = False):
        self._config = config
        self._simulated = simulated
        
        # Build in order
        self._event_bus = EventBus()
        self._hardware = self._build_hardware()
        self._services = self._build_services()
        self._stream_handler = StreamHandler()
        self._qt_stream_handler = QtStreamHandler(self._stream_handler)
        self._controllers = self._build_controllers()
    
    @property
    def event_bus(self) -> EventBus:
        return self._event_bus
    
    @property
    def services(self) -> Services:
        return self._services
    
    @property
    def controllers(self) -> Controllers:
        return self._controllers
    
    @property
    def stream_handler(self) -> StreamHandler:
        return self._stream_handler
    
    @property
    def qt_stream_handler(self) -> QtStreamHandler:
        return self._qt_stream_handler
    
    def _build_hardware(self):
        """Create hardware instances (real or simulated)."""
        if self._simulated:
            camera = SimulatedCamera(self._config.camera)
            stage = SimulatedStage(self._config.stage)
            microcontroller = SimulatedMicrocontroller()
            filter_wheel = SimulatedFilterWheelController()
            objective_changer = None
            spinning_disk = None
            piezo = None
        else:
            camera = get_camera(self._config)
            stage = CephlaStage(self._config.stage, ...)
            microcontroller = Microcontroller(...)
            filter_wheel = get_filter_wheel(self._config) if self._config.has_filter_wheel else None
            objective_changer = ObjectiveChanger2PosController(...) if self._config.has_objective_changer else None
            spinning_disk = XLight(...) if self._config.has_spinning_disk else None
            piezo = PiezoStage(...) if self._config.has_piezo else None
        
        return SimpleNamespace(
            camera=camera,
            stage=stage,
            microcontroller=microcontroller,
            filter_wheel=filter_wheel,
            objective_changer=objective_changer,
            spinning_disk=spinning_disk,
            piezo=piezo,
            light_sources=self._build_light_sources(),
        )
    
    def _build_services(self) -> Services:
        """Create services wrapping hardware."""
        return Services(
            camera=CameraService(self._hardware.camera, self._event_bus),
            stage=StageService(self._hardware.stage, self._event_bus),
            peripheral=PeripheralService(self._hardware.microcontroller, self._event_bus),
            illumination=IlluminationService(
                self._hardware.light_sources,
                self._hardware.microcontroller,
                self._event_bus
            ),
            filter_wheel=FilterWheelService(self._hardware.filter_wheel, self._event_bus),
        )
    
    def _build_controllers(self) -> Controllers:
        """Create controllers."""
        # Load channel configs
        channel_configs = self._load_channel_configs()
        
        # Objective store
        objective_store = ObjectiveStore(self._config.objectives)
        
        # Peripherals controller (new)
        peripherals = PeripheralsController(
            objective_changer=self._hardware.objective_changer,
            spinning_disk=self._hardware.spinning_disk,
            piezo=self._hardware.piezo,
            objective_store=objective_store,
            event_bus=self._event_bus,
        )
        
        # Microscope mode controller (refactored from MicroscopeModeService)
        microscope_mode = MicroscopeModeController(
            camera_service=self._services.camera,
            illumination_service=self._services.illumination,
            filter_wheel_service=self._services.filter_wheel,
            peripherals_controller=peripherals,
            channel_configs=channel_configs,
            event_bus=self._event_bus,
        )
        
        # Live controller (absorbs LiveService)
        live = LiveController(
            camera_service=self._services.camera,
            illumination_service=self._services.illumination,
            peripheral_service=self._services.peripheral,
            stream_handler=self._stream_handler,
            channel_configs=channel_configs,
            event_bus=self._event_bus,
        )
        
        # Autofocus controller
        autofocus = AutoFocusController(
            camera_service=self._services.camera,
            stage_service=self._services.stage,
            stream_handler=self._stream_handler,
            event_bus=self._event_bus,
        )
        
        # Laser autofocus (if available)
        laser_autofocus = None
        if self._config.has_laser_autofocus:
            laser_autofocus = LaserAutofocusController(...)
        
        # Scan coordinates and focus map
        scan_coordinates = ScanCoordinates()
        focus_map = FocusMap()
        job_runner = JobRunner()
        
        # Acquisition controller (uses MultiPointController)
        acquisition = MultiPointController(
            camera_service=self._services.camera,
            stage_service=self._services.stage,
            illumination_service=self._services.illumination,
            filter_wheel_service=self._services.filter_wheel,
            microscope_mode_controller=microscope_mode,
            autofocus_controller=autofocus,
            live_controller=live,
            job_runner=job_runner,
            scan_coordinates=scan_coordinates,
            focus_map=focus_map,
            event_bus=self._event_bus,
        )
        
        # Tracking controller (if needed)
        tracking = None
        if self._config.enable_tracking:
            tracking = TrackingController(...)
        
        return Controllers(
            live=live,
            microscope_mode=microscope_mode,
            peripherals=peripherals,
            autofocus=autofocus,
            laser_autofocus=laser_autofocus,
            acquisition=acquisition,
            tracking=tracking,
        )
    
    def create_gui(self) -> 'HighContentScreeningGui':
        """Create main GUI window."""
        from control.gui_hcs import HighContentScreeningGui
        return HighContentScreeningGui(
            event_bus=self._event_bus,
            controllers=self._controllers,
            qt_stream_handler=self._qt_stream_handler,
            config=self._config,
        )
    
    def shutdown(self) -> None:
        """Clean shutdown."""
        self._event_bus.shutdown()
        # Close hardware...
```

---

## 8. Directory Structure (After Migration)

```
squid/
├── abc.py                           # Hardware protocols (keep + extend)
├── events.py                        # EventBus + all events (keep + extend)
├── application.py                   # DI container (keep + update)
├── registry.py                      # Plugin registry (keep)
├── exceptions.py                    # Exceptions (keep)
├── logging.py                       # Logging setup (keep)
├── config/
│   ├── __init__.py                  # Hardware config models (keep)
│   └── acquisition.py               # Acquisition config (keep)
├── services/
│   ├── __init__.py
│   ├── base.py                      # BaseService (keep)
│   ├── camera_service.py            # Keep as-is
│   ├── stage_service.py             # Keep as-is
│   ├── peripheral_service.py        # Keep as-is
│   ├── illumination_service.py      # New or refactor
│   └── filter_wheel_service.py      # New
├── controllers/
│   ├── __init__.py
│   ├── microscope_mode_controller.py   # Refactor from MicroscopeModeService
│   └── peripherals_controller.py        # New
├── data_streams.py                  # Optional: typed stream interface
└── utils/
    ├── safe_callback.py             # Keep
    ├── thread_safe_state.py         # Keep
    └── worker_manager.py            # Keep

control/
├── gui_hcs.py                       # Main window (update)
├── widgets/                         # All widgets (keep structure)
│   ├── camera/
│   │   ├── settings.py              # Keep
│   │   ├── live_control.py          # Keep
│   │   └── recording.py             # Keep
│   ├── display/
│   │   ├── napari_live.py           # Keep
│   │   └── stats.py                 # Keep
│   ├── stage/
│   │   ├── navigation.py            # Keep
│   │   ├── autofocus.py             # Keep
│   │   └── piezo.py                 # Keep
│   ├── hardware/
│   │   ├── confocal.py              # Refactor to use events
│   │   ├── objectives.py            # Refactor to use events
│   │   ├── filter_controller.py     # Keep
│   │   └── dac.py                   # Keep
│   ├── acquisition/
│   │   ├── flexible_multipoint.py   # Keep
│   │   └── wellplate_multipoint.py  # Keep
│   └── wellplate/                   # Keep all
├── core/
│   ├── display/
│   │   ├── live_controller.py       # Refactor: absorb LiveService
│   │   ├── stream_handler.py        # Keep as-is
│   │   └── image_display.py         # Keep
│   ├── acquisition/
│   │   ├── multi_point_controller.py   # Keep + ensure uses services
│   │   ├── multi_point_worker.py        # Keep + ensure uses services
│   │   ├── multi_point_utils.py         # Keep
│   │   └── job_processing.py            # Keep
│   ├── autofocus/
│   │   ├── auto_focus_controller.py     # Keep
│   │   ├── auto_focus_worker.py         # Keep
│   │   ├── laser_auto_focus_controller.py  # Keep
│   │   └── pdaf.py                      # Keep
│   ├── navigation/
│   │   ├── scan_coordinates.py          # Keep
│   │   ├── focus_map.py                 # Keep
│   │   └── objective_store.py           # Keep
│   ├── configuration/
│   │   ├── configuration_manager.py     # Keep
│   │   ├── channel_configuration_manager.py  # Keep
│   │   └── contrast_manager.py          # Keep
│   └── tracking/
│       └── tracking.py                  # Keep
├── peripherals/                     # Hardware drivers (keep all)
│   ├── cameras/
│   ├── stage/
│   ├── lighting/
│   └── filter_wheel/
└── _def.py                          # Constants (keep, migrate to config over time)
```

---

## 9. Migration Steps

### Phase 1: Establish Boundaries (No Code Changes)

1. Document which existing class does what
2. Identify services that should become controllers
3. Add `--debug-bus` logging to understand current event flow

### Phase 2: Create New Infrastructure

1. Add new events to `squid/events.py` (peripherals, progress, autofocus)
2. Add new protocols to `squid/abc.py` (ObjectiveChanger, SpinningDiskController, PiezoStage)
3. Create `squid/controllers/` directory

### Phase 3: Refactor "Smart" Services into Controllers

1. **LiveService → LiveController**
   - Move `StartLiveCommand`/`StopLiveCommand` handling to `LiveController`
   - Remove `LiveService`
   - Update `ApplicationContext`

2. **MicroscopeModeService → MicroscopeModeController**
   - Move to `squid/controllers/microscope_mode_controller.py`
   - Update imports

3. **Create PeripheralsController**
   - Handle objective, spinning disk, piezo commands
   - Wire to new events

### Phase 4: Audit Acquisition for Service Usage

1. Review `MultiPointWorker` for direct driver calls
2. Replace with service method calls
3. Ensure all hardware access goes through services

### Phase 5: Update Widgets

1. Audit widgets for direct service/driver calls
2. Convert to EventBus-only communication
3. Add new peripheral widgets (if needed)

### Phase 6: Cleanup

1. Remove deprecated `LiveService`, `TriggerService`, `MicroscopeModeService`
2. Update documentation
3. Run full test suite

---

## 10. Summary

| Component | Before | After |
|-----------|--------|-------|
| `CameraService` | Subscribes to events | Keep as-is |
| `StageService` | Subscribes to events | Keep as-is |
| `PeripheralService` | Subscribes to events | Keep as-is |
| `LiveService` | Smart service | Merge into `LiveController` |
| `TriggerService` | Mixed | Low-level parts to service, mode logic to `LiveController` |
| `MicroscopeModeService` | Smart service | Rename to `MicroscopeModeController` |
| `MultiPointController` | Acquisition orchestrator | Keep + ensure uses services |
| `AutoFocusController` | AF orchestrator | Keep |
| `StreamHandler` | Frame distribution | Keep (data plane) |
| Spinning disk, objectives, piezo | Ad-hoc | Consolidate in `PeripheralsController` |

**Result:** ~90% code reuse with clear responsibilities:
- **Services** = thread-safe hardware access
- **Controllers** = orchestration and state
- **EventBus** = control plane (commands, state)
- **StreamHandler** = data plane (frames)
- **Widgets** = render state, emit commands