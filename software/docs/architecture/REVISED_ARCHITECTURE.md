# Clean Architecture Design

This document describes the target architecture for the Squid microscopy control software. The goal is a clean, testable, maintainable system with clear separation of concerns.

## Design Philosophy

**Simple, not clever.** Use straightforward Python patterns:
- Classes for state + behavior (but flat, no deep inheritance)
- Dataclasses for pure data
- Protocols for interfaces (structural typing)
- Composition over inheritance
- Explicit over implicit

**Reuse what works.** The existing codebase has many well-designed components. This architecture preserves them while clarifying responsibilities and reducing coupling.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         GUI Widgets                             │
│              (render state, emit commands, no logic)            │
│                    control/widgets/                             │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
          EventBus                   DataStreams
     (commands ↓ state ↑)         (frames, positions)
       squid/events.py            squid/data_streams.py
              │                           │
┌─────────────▼───────────────────────────┴───────────────────────┐
│                          Controllers                            │
│            (own state, handle commands, orchestrate)            │
│                    squid/controllers/                           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                      direct method calls
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                           Services                              │
│         (thread-safe hardware access, stateless adapters)       │
│                      squid/services/                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                      direct method calls
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                     Hardware (via Protocols)                    │
│        (Camera, Stage, LightSource, FilterWheel, etc.)          │
│              squid/abc.py + control/peripherals/                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer Responsibilities

| Layer | Location | Responsibility | Communicates Via |
|-------|----------|----------------|------------------|
| **Widgets** | `control/widgets/` | Render state, capture user intent | EventBus only |
| **Controllers** | `squid/controllers/` | Own state, handle commands, orchestrate | EventBus + direct calls to Services |
| **Services** | `squid/services/` | Thread-safe hardware access, validation | Direct method calls only |
| **Hardware** | `control/peripherals/` | Implement hardware protocols | Direct method calls |

**Key Rules:**
1. Widgets never call Services or Hardware directly
2. Controllers own all application state
3. Services are stateless (except for hardware reference + lock)
4. Services never subscribe to EventBus
5. Hardware implements Protocols defined in `squid/abc.py`

---

## 1. Protocols (Hardware Interfaces)
    
**Location:** `squid/abc.py` (existing, extend as needed)

Use `typing.Protocol` for hardware interfaces. Any class with matching methods satisfies the protocol—no inheritance required.

### Existing Protocols to Keep

```python
# squid/abc.py - these already exist and are well-designed

class AbstractCamera(Protocol):
    """Full camera interface - ~40 methods covering all camera operations."""
    # Exposure, gain, binning, ROI, streaming, triggering, etc.
    # Keep as-is - comprehensive and well-tested
    ...

class AbstractStage(Protocol):
    """Stage interface for XYZ (+ optional theta) movement."""
    # move_x/y/z(), move_x/y/z_to(), get_pos(), home(), zero(), etc.
    # Keep as-is
    ...

class AbstractFilterWheelController(Protocol):
    """Filter wheel controller - may manage multiple wheels."""
    # initialize(), set_filter_wheel_position(), get_filter_wheel_position()
    # Keep as-is
    ...

class LightSource(Protocol):
    """Illumination source interface."""
    # set_intensity(), get_intensity(), set_shutter_state(), etc.
    # Keep as-is
    ...
```

### New Protocols to Add

```python
# squid/abc.py - add these for peripheral hardware

class ObjectiveChanger(Protocol):
    """Motorized objective turret."""
    
    @property
    def current_position(self) -> int: ...
    
    @property
    def num_positions(self) -> int: ...
    
    @property
    def objective_info(self) -> ObjectiveInfo | None: ...
    
    def set_position(self, position: int) -> None: ...


class SpinningDiskController(Protocol):
    """Spinning disk confocal unit (xLight, Dragonfly, etc.)."""
    
    @property
    def is_spinning(self) -> bool: ...
    
    @property
    def disk_position(self) -> str: ...  # "confocal", "widefield"
    
    @property
    def current_dichroic(self) -> int: ...
    
    @property
    def current_emission_filter(self) -> int: ...
    
    def set_disk_position(self, position: str) -> None: ...
    def start_disk(self) -> None: ...
    def stop_disk(self) -> None: ...
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


class Microcontroller(Protocol):
    """Low-level microcontroller interface (Teensy/Arduino)."""
    
    def set_dac(self, channel: int, value: float) -> None: ...
    def send_trigger(self) -> None: ...
    def set_illumination(self, channel: int, intensity: float) -> None: ...
    def turn_on_illumination(self) -> None: ...
    def turn_off_illumination(self) -> None: ...
    def home_stage_axis(self, axis: str) -> None: ...
    def zero_stage_axis(self, axis: str) -> None: ...
```

### Data Classes (Already Exist)

```python
# squid/abc.py - keep these

@dataclass(frozen=True)
class Pos:
    x_mm: float
    y_mm: float
    z_mm: float
    theta_rad: float | None = None

@dataclass(frozen=True)
class StageState:
    busy: bool

@dataclass(frozen=True)
class CameraFrame:
    frame_id: int
    timestamp: float
    frame: NDArray
    frame_format: CameraFrameFormat
    frame_pixel_format: CameraPixelFormat
```

---

## 2. Services (Thread-Safe Hardware Access)

**Location:** `squid/services/`

Services wrap hardware with locks for thread-safe access. They are **stateless adapters**—they don't own application state, only hardware references and locks.

### Key Change from Current Design

**Current:** Services subscribe to EventBus and publish state changes.
**New:** Services are pure—no EventBus knowledge. Controllers call services and handle events.

This makes services:
- Easier to test (no pub/sub to mock)
- Reusable in different contexts (CLI tools, scripts)
- Simpler to reason about

### CameraService

```python
# squid/services/camera_service.py
import threading
from squid.abc import AbstractCamera, CameraFrame

class CameraService:
    """Thread-safe camera operations. Stateless except for hardware + lock."""
    
    def __init__(self, camera: AbstractCamera):
        self._camera = camera
        self._lock = threading.RLock()
    
    def set_exposure(self, ms: float) -> float:
        """Set exposure, clamping to valid range. Returns actual value."""
        with self._lock:
            min_exp, max_exp = self._camera.get_exposure_limits()
            clamped = max(min_exp, min(max_exp, ms))
            self._camera.set_exposure_time(clamped)
            return clamped
    
    def get_exposure(self) -> float:
        with self._lock:
            return self._camera.get_exposure_time()
    
    def set_analog_gain(self, gain: float) -> float:
        """Set gain, clamping to valid range. Returns actual value."""
        with self._lock:
            gain_range = self._camera.get_gain_range()
            if gain_range is None:
                return 0.0  # Camera doesn't support gain
            clamped = max(gain_range.min_gain, min(gain_range.max_gain, gain))
            self._camera.set_analog_gain(clamped)
            return clamped
    
    def get_analog_gain(self) -> float:
        with self._lock:
            return self._camera.get_analog_gain()
    
    def set_binning(self, x: int, y: int) -> tuple[int, int]:
        with self._lock:
            self._camera.set_binning(x, y)
            return self._camera.get_binning()
    
    def get_binning(self) -> tuple[int, int]:
        with self._lock:
            return self._camera.get_binning()
    
    def start_streaming(self, callback: Callable[[CameraFrame], None]) -> None:
        with self._lock:
            self._camera.add_frame_callback(callback)
            self._camera.start_streaming()
    
    def stop_streaming(self) -> None:
        with self._lock:
            self._camera.stop_streaming()
    
    def capture_single(self) -> CameraFrame:
        """Capture a single frame. Blocks until complete."""
        with self._lock:
            return self._camera.read_camera_frame()
    
    def send_trigger(self) -> None:
        with self._lock:
            self._camera.send_trigger()
    
    def set_acquisition_mode(self, mode: CameraAcquisitionMode) -> None:
        with self._lock:
            self._camera.set_acquisition_mode(mode)
    
    def get_resolution(self) -> tuple[int, int]:
        with self._lock:
            return self._camera.get_resolution()
    
    def get_pixel_size_um(self) -> float:
        with self._lock:
            return self._camera.get_pixel_size_binned_um()
```

### StageService

```python
# squid/services/stage_service.py
class StageService:
    """Thread-safe stage operations."""
    
    def __init__(self, stage: AbstractStage):
        self._stage = stage
        self._lock = threading.RLock()
    
    def move_to(self, x: float | None = None, y: float | None = None, z: float | None = None) -> Pos:
        """Absolute move. Blocks until complete. Returns final position."""
        with self._lock:
            if x is not None:
                self._stage.move_x_to(x)
            if y is not None:
                self._stage.move_y_to(y)
            if z is not None:
                self._stage.move_z_to(z)
            self._stage.wait_for_idle()
            return self._stage.get_pos()
    
    def move_relative(self, dx: float = 0, dy: float = 0, dz: float = 0) -> Pos:
        """Relative move. Blocks until complete. Returns final position."""
        with self._lock:
            if dx != 0:
                self._stage.move_x(dx)
            if dy != 0:
                self._stage.move_y(dy)
            if dz != 0:
                self._stage.move_z(dz)
            self._stage.wait_for_idle()
            return self._stage.get_pos()
    
    def get_position(self) -> Pos:
        with self._lock:
            return self._stage.get_pos()
    
    def stop(self) -> None:
        with self._lock:
            # Implementation depends on stage
            pass
    
    def home(self, x: bool = False, y: bool = False, z: bool = False) -> Pos:
        with self._lock:
            self._stage.home(x=x, y=y, z=z)
            self._stage.wait_for_idle()
            return self._stage.get_pos()
    
    def zero(self, x: bool = False, y: bool = False, z: bool = False) -> None:
        with self._lock:
            self._stage.zero(x=x, y=y, z=z)
    
    def is_busy(self) -> bool:
        with self._lock:
            return self._stage.get_state().busy
```

### IlluminationService

```python
# squid/services/illumination_service.py
class IlluminationService:
    """Thread-safe illumination control across multiple light sources."""
    
    def __init__(
        self,
        light_sources: dict[str, LightSource],  # wavelength/name -> source
        microcontroller: Microcontroller | None = None
    ):
        self._sources = light_sources
        self._mcu = microcontroller
        self._lock = threading.RLock()
        self._current_channel: str | None = None
    
    def set_channel(self, channel: str, intensity: float) -> None:
        """Configure a channel. Does not turn it on."""
        with self._lock:
            if channel not in self._sources:
                raise ValueError(f"Unknown channel: {channel}")
            self._sources[channel].set_intensity(intensity)
            self._current_channel = channel
    
    def turn_on(self, channel: str | None = None) -> None:
        """Turn on illumination for channel (or current channel)."""
        with self._lock:
            ch = channel or self._current_channel
            if ch is None:
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
    
    def turn_off_all(self) -> None:
        """Alias for turn_off."""
        self.turn_off()
    
    def get_intensity(self, channel: str) -> float:
        with self._lock:
            return self._sources[channel].get_intensity()
    
    def set_intensity(self, channel: str, intensity: float) -> None:
        with self._lock:
            self._sources[channel].set_intensity(intensity)
```

### PeripheralsService

```python
# squid/services/peripherals_service.py
@dataclass(frozen=True)
class SpinningDiskState:
    is_spinning: bool
    disk_position: str  # "confocal", "widefield"
    dichroic: int
    emission_filter: int


class PeripheralsService:
    """Thread-safe access to miscellaneous peripherals."""
    
    def __init__(
        self,
        microcontroller: Microcontroller | None = None,
        filter_wheel: AbstractFilterWheelController | None = None,
        objective_changer: ObjectiveChanger | None = None,
        spinning_disk: SpinningDiskController | None = None,
        piezo: PiezoStage | None = None,
    ):
        self._mcu = microcontroller
        self._filter_wheel = filter_wheel
        self._objective_changer = objective_changer
        self._spinning_disk = spinning_disk
        self._piezo = piezo
        self._lock = threading.RLock()
    
    # --- DAC Control ---
    def set_dac(self, channel: int, value_percent: float) -> float:
        """Set DAC output (0-100%). Returns actual value."""
        if not self._mcu:
            raise HardwareNotAvailable("microcontroller")
        with self._lock:
            clamped = max(0.0, min(100.0, value_percent))
            self._mcu.set_dac(channel, clamped)
            return clamped
    
    # --- Filter Wheel ---
    def set_filter_position(self, position: int, wheel_index: int = 0) -> int:
        """Set filter wheel position. Returns actual position."""
        if not self._filter_wheel:
            raise HardwareNotAvailable("filter_wheel")
        with self._lock:
            self._filter_wheel.set_filter_wheel_position(position, wheel_index)
            return self._filter_wheel.get_filter_wheel_position(wheel_index)
    
    def get_filter_position(self, wheel_index: int = 0) -> int:
        if not self._filter_wheel:
            raise HardwareNotAvailable("filter_wheel")
        with self._lock:
            return self._filter_wheel.get_filter_wheel_position(wheel_index)
    
    # --- Objective Changer ---
    def set_objective(self, position: int) -> int:
        """Change objective. Returns actual position."""
        if not self._objective_changer:
            raise HardwareNotAvailable("objective_changer")
        with self._lock:
            self._objective_changer.set_position(position)
            return self._objective_changer.current_position
    
    def get_objective(self) -> int:
        if not self._objective_changer:
            raise HardwareNotAvailable("objective_changer")
        with self._lock:
            return self._objective_changer.current_position
    
    def get_objective_info(self) -> ObjectiveInfo | None:
        if not self._objective_changer:
            return None
        with self._lock:
            return self._objective_changer.objective_info
    
    # --- Spinning Disk ---
    def set_disk_position(self, position: str) -> None:
        """Set disk position ('confocal' or 'widefield')."""
        if not self._spinning_disk:
            raise HardwareNotAvailable("spinning_disk")
        with self._lock:
            self._spinning_disk.set_disk_position(position)
    
    def start_disk(self) -> None:
        if not self._spinning_disk:
            raise HardwareNotAvailable("spinning_disk")
        with self._lock:
            self._spinning_disk.start_disk()
    
    def stop_disk(self) -> None:
        if not self._spinning_disk:
            raise HardwareNotAvailable("spinning_disk")
        with self._lock:
            self._spinning_disk.stop_disk()
    
    def set_disk_dichroic(self, position: int) -> None:
        if not self._spinning_disk:
            raise HardwareNotAvailable("spinning_disk")
        with self._lock:
            self._spinning_disk.set_dichroic(position)
    
    def set_disk_emission_filter(self, position: int) -> None:
        if not self._spinning_disk:
            raise HardwareNotAvailable("spinning_disk")
        with self._lock:
            self._spinning_disk.set_emission_filter(position)
    
    def get_spinning_disk_state(self) -> SpinningDiskState | None:
        if not self._spinning_disk:
            return None
        with self._lock:
            return SpinningDiskState(
                is_spinning=self._spinning_disk.is_spinning,
                disk_position=self._spinning_disk.disk_position,
                dichroic=self._spinning_disk.current_dichroic,
                emission_filter=self._spinning_disk.current_emission_filter,
            )
    
    # --- Piezo ---
    def set_piezo_position(self, position_um: float) -> float:
        if not self._piezo:
            raise HardwareNotAvailable("piezo")
        with self._lock:
            min_pos, max_pos = self._piezo.range_um
            clamped = max(min_pos, min(max_pos, position_um))
            self._piezo.move_to(clamped)
            return self._piezo.position_um
    
    def get_piezo_position(self) -> float:
        if not self._piezo:
            raise HardwareNotAvailable("piezo")
        with self._lock:
            return self._piezo.position_um
    
    def move_piezo_relative(self, delta_um: float) -> float:
        if not self._piezo:
            raise HardwareNotAvailable("piezo")
        with self._lock:
            self._piezo.move_relative(delta_um)
            return self._piezo.position_um
    
    # --- Hardware Availability ---
    def has_filter_wheel(self) -> bool:
        return self._filter_wheel is not None
    
    def has_objective_changer(self) -> bool:
        return self._objective_changer is not None
    
    def has_spinning_disk(self) -> bool:
        return self._spinning_disk is not None
    
    def has_piezo(self) -> bool:
        return self._piezo is not None


class HardwareNotAvailable(Exception):
    """Raised when requested hardware is not configured."""
    def __init__(self, hardware_name: str):
        super().__init__(f"Hardware not available: {hardware_name}")
        self.hardware_name = hardware_name
```

### Service Inventory

| Service | Wraps | Thread-Safe | Notes |
|---------|-------|-------------|-------|
| `CameraService` | `AbstractCamera` | Yes | Exposure clamping, streaming |
| `StageService` | `AbstractStage` | Yes | Blocking moves, position queries |
| `IlluminationService` | `LightSource[]` + MCU | Yes | Multi-source, channel management |
| `PeripheralsService` | Filter, objective, disk, piezo | Yes | Graceful "not available" handling |
| `AutofocusCameraService` | Focus camera | Yes | For laser AF sensing |

---

## 3. Communication: EventBus and DataStreams

Two communication systems optimized for different use cases.

### EventBus (Commands and State)

**Location:** `squid/events.py` (existing)

For commands (GUI → Controller) and state changes (Controller → GUI). Queued, processed on dedicated thread.

```python
# squid/events.py - keep existing implementation, it's good

class EventBus:
    """
    Thread-safe pub/sub for commands and state.
    
    Threading model:
    - publish() can be called from any thread
    - Handlers called on EventBus's dedicated thread
    - Handlers should not block (offload to worker threads)
    """
    
    def __init__(self):
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()
        self._queue: Queue = Queue()
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
    
    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        with self._lock:
            self._subscribers[event_type].append(handler)
    
    def unsubscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        with self._lock:
            if handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)
    
    def publish(self, event: object) -> None:
        self._queue.put(event)
    
    def _process_loop(self) -> None:
        while self._running:
            event = self._queue.get()
            if event is None:
                break
            
            with self._lock:
                handlers = list(self._subscribers[type(event)])
            
            for handler in handlers:
                try:
                    handler(event)
                except Exception as e:
                    logger.exception(f"Error handling {type(event).__name__}: {e}")
    
    def shutdown(self) -> None:
        self._running = False
        self._queue.put(None)
        self._thread.join(timeout=5.0)
```

### DataStreams (Real-Time Data) - NEW

**Location:** `squid/data_streams.py` (new file)

For high-frequency data (camera frames, positions). Direct callbacks, no queuing.

```python
# squid/data_streams.py
from typing import TypeVar, Generic, Callable
from dataclasses import dataclass
import threading
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class DataStream(Generic[T]):
    """
    Pub/sub for real-time data.
    
    Unlike EventBus:
    - No queuing (only latest data matters)
    - Callbacks run synchronously on publisher's thread
    - Callbacks must be fast
    """
    
    def __init__(self, name: str):
        self.name = name
        self._subscribers: list[Callable[[T], None]] = []
        self._lock = threading.Lock()
    
    def subscribe(self, callback: Callable[[T], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)
    
    def unsubscribe(self, callback: Callable[[T], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
    
    def publish(self, data: T) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        
        for callback in subscribers:
            try:
                callback(data)
            except Exception as e:
                logger.exception(f"Error in {self.name} subscriber: {e}")


# Data types for streams
@dataclass(frozen=True)
class FrameData:
    """Camera frame with metadata."""
    frame: NDArray
    frame_id: int
    timestamp: float
    channel: str | None = None


@dataclass(frozen=True)
class PositionData:
    """Stage position update."""
    position: Pos
    timestamp: float


@dataclass(frozen=True)
class FocusData:
    """Focus measurement (from laser AF)."""
    displacement_um: float
    in_range: bool
    timestamp: float


class DataStreams:
    """Container for all real-time data streams."""
    
    def __init__(self):
        self.frames = DataStream[FrameData]("frames")
        self.display_frames = DataStream[FrameData]("display_frames")  # Decimated
        self.positions = DataStream[PositionData]("positions")
        self.focus = DataStream[FocusData]("focus")
```

### Why Two Systems?

| Aspect | EventBus | DataStreams |
|--------|----------|-------------|
| **Use case** | Commands, state changes | Frames, positions, sensors |
| **Frequency** | Low (user actions) | High (60+ fps) |
| **Delivery** | Queued, ordered | Immediate, may drop |
| **Threading** | Handlers on EventBus thread | Handlers on publisher's thread |

---

## 4. Events

**Location:** `squid/events.py` (existing - keep and extend)

All events are frozen dataclasses.

### Existing Events to Keep

The existing events in `squid/events.py` are well-designed. Keep them:

```python
# Commands (already exist)
SetExposureTimeCommand
SetAnalogGainCommand
SetDACCommand
MoveStageCommand
MoveStageToCommand
HomeStageCommand
ZeroStageCommand
StartLiveCommand
StopLiveCommand
SetTriggerModeCommand
SetTriggerFPSCommand
SetMicroscopeModeCommand
TurnOnAFLaserCommand
TurnOffAFLaserCommand

# State events (already exist)
ExposureTimeChanged
AnalogGainChanged
StagePositionChanged
DACValueChanged
LiveStateChanged
ROIChanged
BinningChanged
PixelFormatChanged
TriggerModeChanged
TriggerFPSChanged
MicroscopeModeChanged
```

### New Events to Add

```python
# squid/events.py - add these

# --- Peripheral Commands ---
@dataclass(frozen=True)
class SetFilterPositionCommand(Event):
    position: int
    wheel_index: int = 0

@dataclass(frozen=True)
class SetObjectiveCommand(Event):
    position: int

@dataclass(frozen=True)
class SetSpinningDiskModeCommand(Event):
    mode: str  # "confocal", "widefield"

@dataclass(frozen=True)
class StartSpinningDiskCommand(Event):
    pass

@dataclass(frozen=True)
class StopSpinningDiskCommand(Event):
    pass

@dataclass(frozen=True)
class SetDiskDichroicCommand(Event):
    position: int

@dataclass(frozen=True)
class SetDiskEmissionFilterCommand(Event):
    position: int

@dataclass(frozen=True)
class SetPiezoPositionCommand(Event):
    position_um: float

@dataclass(frozen=True)
class MovePiezoRelativeCommand(Event):
    delta_um: float


# --- Peripheral State Events ---
@dataclass(frozen=True)
class FilterPositionChanged(Event):
    position: int
    wheel_index: int = 0

@dataclass(frozen=True)
class ObjectiveChanged(Event):
    position: int
    objective_info: ObjectiveInfo | None = None

@dataclass(frozen=True)
class SpinningDiskStateChanged(Event):
    is_spinning: bool
    mode: str
    dichroic: int
    emission_filter: int

@dataclass(frozen=True)
class PiezoPositionChanged(Event):
    position_um: float


# --- Acquisition Events (extend existing) ---
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
    pass

@dataclass(frozen=True)
class AcquisitionResumed(Event):
    pass


# --- Autofocus Events ---
@dataclass(frozen=True)
class StartAutofocusCommand(Event):
    pass

@dataclass(frozen=True)
class StopAutofocusCommand(Event):
    pass

@dataclass(frozen=True)
class AutofocusProgress(Event):
    current_step: int
    total_steps: int
    current_z: float
    best_z: float | None
    best_score: float | None

@dataclass(frozen=True)
class AutofocusCompleted(Event):
    success: bool
    z_position: float | None
    score: float | None
    error: str | None = None
```

---

## 5. Controllers

**Location:** `squid/controllers/` (reorganize from `control/core/`)

Controllers own state, subscribe to commands, call services, and publish state changes.

### Controller Categories

| Category | Controllers | Complexity |
|----------|-------------|------------|
| **Hardware** | Camera, Stage, Peripherals | Low - wrap service calls |
| **Orchestration** | Live, Acquisition, Autofocus | High - coordinate multiple services |
| **Tracking** | Tracking, LaserAF | Medium - real-time feedback loops |

### CameraController

```python
# squid/controllers/camera_controller.py
from dataclasses import dataclass, replace

@dataclass
class CameraState:
    exposure_ms: float
    gain: float
    binning: tuple[int, int]
    pixel_format: str
    is_streaming: bool
    resolution: tuple[int, int]


class CameraController:
    """Owns camera state, handles camera commands."""
    
    def __init__(
        self,
        camera_service: CameraService,
        event_bus: EventBus,
        streams: DataStreams
    ):
        self._camera = camera_service
        self._bus = event_bus
        self._streams = streams
        self._state = self._read_initial_state()
        
        # Subscribe to commands
        self._bus.subscribe(SetExposureTimeCommand, self._on_set_exposure)
        self._bus.subscribe(SetAnalogGainCommand, self._on_set_gain)
        self._bus.subscribe(SetBinningCommand, self._on_set_binning)
    
    @property
    def state(self) -> CameraState:
        return self._state
    
    def _read_initial_state(self) -> CameraState:
        return CameraState(
            exposure_ms=self._camera.get_exposure(),
            gain=self._camera.get_analog_gain(),
            binning=self._camera.get_binning(),
            pixel_format="MONO8",  # TODO: get from camera
            is_streaming=False,
            resolution=self._camera.get_resolution()
        )
    
    def _on_set_exposure(self, cmd: SetExposureTimeCommand) -> None:
        actual = self._camera.set_exposure(cmd.exposure_time_ms)
        self._state = replace(self._state, exposure_ms=actual)
        self._bus.publish(ExposureTimeChanged(exposure_time_ms=actual))
    
    def _on_set_gain(self, cmd: SetAnalogGainCommand) -> None:
        actual = self._camera.set_analog_gain(cmd.gain)
        self._state = replace(self._state, gain=actual)
        self._bus.publish(AnalogGainChanged(gain=actual))
    
    def _on_set_binning(self, cmd: SetBinningCommand) -> None:
        actual = self._camera.set_binning(cmd.binning_x, cmd.binning_y)
        self._state = replace(self._state, binning=actual)
        self._state = replace(self._state, resolution=self._camera.get_resolution())
        self._bus.publish(BinningChanged(binning_x=actual[0], binning_y=actual[1]))
    
    # --- Methods for other controllers ---
    def start_streaming(self, callback: Callable[[CameraFrame], None]) -> None:
        self._camera.start_streaming(callback)
        self._state = replace(self._state, is_streaming=True)
    
    def stop_streaming(self) -> None:
        self._camera.stop_streaming()
        self._state = replace(self._state, is_streaming=False)
    
    def capture_single(self) -> CameraFrame:
        return self._camera.capture_single()
    
    def send_trigger(self) -> None:
        self._camera.send_trigger()
```

### StageController

```python
# squid/controllers/stage_controller.py
@dataclass
class StageState:
    position: Pos
    is_moving: bool


class StageController:
    """Owns stage state, handles movement commands."""
    
    def __init__(
        self,
        stage_service: StageService,
        event_bus: EventBus,
        streams: DataStreams
    ):
        self._stage = stage_service
        self._bus = event_bus
        self._streams = streams
        self._state = StageState(
            position=self._stage.get_position(),
            is_moving=False
        )
        
        self._bus.subscribe(MoveStageCommand, self._on_move_relative)
        self._bus.subscribe(MoveStageToCommand, self._on_move_to)
        self._bus.subscribe(HomeStageCommand, self._on_home)
        self._bus.subscribe(ZeroStageCommand, self._on_zero)
        self._bus.subscribe(StopStageCommand, self._on_stop)
    
    @property
    def state(self) -> StageState:
        return self._state
    
    def _on_move_relative(self, cmd: MoveStageCommand) -> None:
        # Run in thread to not block EventBus
        threading.Thread(
            target=self._do_move_relative,
            args=(cmd.axis, cmd.distance_mm),
            daemon=True
        ).start()
    
    def _do_move_relative(self, axis: str, distance: float) -> None:
        self._state = replace(self._state, is_moving=True)
        
        try:
            kwargs = {f"d{axis}": distance}
            position = self._stage.move_relative(**kwargs)
            self._state = StageState(position=position, is_moving=False)
        except Exception as e:
            logger.exception(f"Move failed: {e}")
            self._state = replace(self._state, is_moving=False)
        finally:
            self._publish_position()
    
    def _on_move_to(self, cmd: MoveStageToCommand) -> None:
        threading.Thread(
            target=self._do_move_to,
            args=(cmd.x_mm, cmd.y_mm, cmd.z_mm),
            daemon=True
        ).start()
    
    def _do_move_to(self, x: float | None, y: float | None, z: float | None) -> None:
        self._state = replace(self._state, is_moving=True)
        
        try:
            position = self._stage.move_to(x, y, z)
            self._state = StageState(position=position, is_moving=False)
        except Exception as e:
            logger.exception(f"Move failed: {e}")
            self._state = replace(self._state, is_moving=False)
        finally:
            self._publish_position()
    
    def _on_home(self, cmd: HomeStageCommand) -> None:
        threading.Thread(
            target=self._do_home,
            args=(cmd.home_x, cmd.home_y, cmd.home_z),
            daemon=True
        ).start()
    
    def _do_home(self, x: bool, y: bool, z: bool) -> None:
        self._state = replace(self._state, is_moving=True)
        try:
            position = self._stage.home(x, y, z)
            self._state = StageState(position=position, is_moving=False)
        finally:
            self._publish_position()
    
    def _on_zero(self, cmd: ZeroStageCommand) -> None:
        self._stage.zero(cmd.zero_x, cmd.zero_y, cmd.zero_z)
        self._state = replace(self._state, position=self._stage.get_position())
        self._publish_position()
    
    def _on_stop(self, cmd: StopStageCommand) -> None:
        self._stage.stop()
        self._state = replace(self._state, is_moving=False)
    
    def _publish_position(self) -> None:
        pos = self._state.position
        self._bus.publish(StagePositionChanged(
            x_mm=pos.x_mm,
            y_mm=pos.y_mm,
            z_mm=pos.z_mm,
            theta_rad=pos.theta_rad
        ))
        self._streams.positions.publish(PositionData(
            position=pos,
            timestamp=time.time()
        ))
    
    # --- Methods for other controllers ---
    def move_to_blocking(self, x: float | None = None, y: float | None = None, z: float | None = None) -> Pos:
        """Blocking move for use by AcquisitionController etc."""
        position = self._stage.move_to(x, y, z)
        self._state = StageState(position=position, is_moving=False)
        self._publish_position()
        return position
    
    def get_position(self) -> Pos:
        return self._stage.get_position()
```

### PeripheralsController

```python
# squid/controllers/peripherals_controller.py
@dataclass
class PeripheralsState:
    filter_position: int | None
    objective_position: int | None
    objective_info: ObjectiveInfo | None
    spinning_disk: SpinningDiskState | None
    piezo_position_um: float | None
    dac_values: dict[int, float]  # channel -> value


class PeripheralsController:
    """Handles peripheral hardware that doesn't need complex orchestration."""
    
    def __init__(
        self,
        peripherals_service: PeripheralsService,
        event_bus: EventBus
    ):
        self._peripherals = peripherals_service
        self._bus = event_bus
        self._state = self._read_initial_state()
        
        # Filter wheel
        self._bus.subscribe(SetFilterPositionCommand, self._on_set_filter)
        
        # Objective
        self._bus.subscribe(SetObjectiveCommand, self._on_set_objective)
        
        # Spinning disk
        self._bus.subscribe(SetSpinningDiskModeCommand, self._on_set_disk_mode)
        self._bus.subscribe(StartSpinningDiskCommand, self._on_start_disk)
        self._bus.subscribe(StopSpinningDiskCommand, self._on_stop_disk)
        self._bus.subscribe(SetDiskDichroicCommand, self._on_set_dichroic)
        self._bus.subscribe(SetDiskEmissionFilterCommand, self._on_set_emission)
        
        # Piezo
        self._bus.subscribe(SetPiezoPositionCommand, self._on_set_piezo)
        self._bus.subscribe(MovePiezoRelativeCommand, self._on_move_piezo)
        
        # DAC
        self._bus.subscribe(SetDACCommand, self._on_set_dac)
    
    def _read_initial_state(self) -> PeripheralsState:
        filter_pos = None
        if self._peripherals.has_filter_wheel():
            try:
                filter_pos = self._peripherals.get_filter_position()
            except Exception:
                pass
        
        obj_pos = None
        obj_info = None
        if self._peripherals.has_objective_changer():
            try:
                obj_pos = self._peripherals.get_objective()
                obj_info = self._peripherals.get_objective_info()
            except Exception:
                pass
        
        disk_state = None
        if self._peripherals.has_spinning_disk():
            disk_state = self._peripherals.get_spinning_disk_state()
        
        piezo_pos = None
        if self._peripherals.has_piezo():
            try:
                piezo_pos = self._peripherals.get_piezo_position()
            except Exception:
                pass
        
        return PeripheralsState(
            filter_position=filter_pos,
            objective_position=obj_pos,
            objective_info=obj_info,
            spinning_disk=disk_state,
            piezo_position_um=piezo_pos,
            dac_values={0: 0.0, 1: 0.0}
        )
    
    # --- Filter Wheel ---
    def _on_set_filter(self, cmd: SetFilterPositionCommand) -> None:
        try:
            actual = self._peripherals.set_filter_position(cmd.position, cmd.wheel_index)
            self._state = replace(self._state, filter_position=actual)
            self._bus.publish(FilterPositionChanged(position=actual, wheel_index=cmd.wheel_index))
        except HardwareNotAvailable:
            logger.warning("Filter wheel not available")
    
    # --- Objective ---
    def _on_set_objective(self, cmd: SetObjectiveCommand) -> None:
        try:
            actual = self._peripherals.set_objective(cmd.position)
            info = self._peripherals.get_objective_info()
            self._state = replace(self._state, objective_position=actual, objective_info=info)
            self._bus.publish(ObjectiveChanged(position=actual, objective_info=info))
        except HardwareNotAvailable:
            logger.warning("Objective changer not available")
    
    # --- Spinning Disk ---
    def _on_set_disk_mode(self, cmd: SetSpinningDiskModeCommand) -> None:
        try:
            self._peripherals.set_disk_position(cmd.mode)
            self._update_disk_state()
        except HardwareNotAvailable:
            logger.warning("Spinning disk not available")
    
    def _on_start_disk(self, cmd: StartSpinningDiskCommand) -> None:
        try:
            self._peripherals.start_disk()
            self._update_disk_state()
        except HardwareNotAvailable:
            pass
    
    def _on_stop_disk(self, cmd: StopSpinningDiskCommand) -> None:
        try:
            self._peripherals.stop_disk()
            self._update_disk_state()
        except HardwareNotAvailable:
            pass
    
    def _on_set_dichroic(self, cmd: SetDiskDichroicCommand) -> None:
        try:
            self._peripherals.set_disk_dichroic(cmd.position)
            self._update_disk_state()
        except HardwareNotAvailable:
            pass
    
    def _on_set_emission(self, cmd: SetDiskEmissionFilterCommand) -> None:
        try:
            self._peripherals.set_disk_emission_filter(cmd.position)
            self._update_disk_state()
        except HardwareNotAvailable:
            pass
    
    def _update_disk_state(self) -> None:
        disk_state = self._peripherals.get_spinning_disk_state()
        if disk_state:
            self._state = replace(self._state, spinning_disk=disk_state)
            self._bus.publish(SpinningDiskStateChanged(
                is_spinning=disk_state.is_spinning,
                mode=disk_state.disk_position,
                dichroic=disk_state.dichroic,
                emission_filter=disk_state.emission_filter
            ))
    
    # --- Piezo ---
    def _on_set_piezo(self, cmd: SetPiezoPositionCommand) -> None:
        try:
            actual = self._peripherals.set_piezo_position(cmd.position_um)
            self._state = replace(self._state, piezo_position_um=actual)
            self._bus.publish(PiezoPositionChanged(position_um=actual))
        except HardwareNotAvailable:
            pass
    
    def _on_move_piezo(self, cmd: MovePiezoRelativeCommand) -> None:
        try:
            actual = self._peripherals.move_piezo_relative(cmd.delta_um)
            self._state = replace(self._state, piezo_position_um=actual)
            self._bus.publish(PiezoPositionChanged(position_um=actual))
        except HardwareNotAvailable:
            pass
    
    # --- DAC ---
    def _on_set_dac(self, cmd: SetDACCommand) -> None:
        try:
            actual = self._peripherals.set_dac(cmd.channel, cmd.value)
            self._state.dac_values[cmd.channel] = actual
            self._bus.publish(DACValueChanged(channel=cmd.channel, value=actual))
        except HardwareNotAvailable:
            pass
```

### LiveController

```python
# squid/controllers/live_controller.py
@dataclass
class LiveState:
    is_live: bool
    current_channel: str | None
    trigger_mode: str
    trigger_fps: float


class LiveController:
    """Orchestrates live view (camera + illumination + triggering)."""
    
    def __init__(
        self,
        camera_controller: CameraController,
        illumination_service: IlluminationService,
        microcontroller: Microcontroller | None,
        event_bus: EventBus,
        streams: DataStreams,
        channel_configs: dict[str, ChannelConfig]
    ):
        self._camera = camera_controller
        self._illumination = illumination_service
        self._mcu = microcontroller
        self._bus = event_bus
        self._streams = streams
        self._channel_configs = channel_configs
        
        self._state = LiveState(
            is_live=False,
            current_channel=None,
            trigger_mode="Continuous",
            trigger_fps=10.0
        )
        
        self._trigger_timer: threading.Timer | None = None
        
        self._bus.subscribe(StartLiveCommand, self._on_start_live)
        self._bus.subscribe(StopLiveCommand, self._on_stop_live)
        self._bus.subscribe(SetMicroscopeModeCommand, self._on_set_channel)
        self._bus.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode)
        self._bus.subscribe(SetTriggerFPSCommand, self._on_set_fps)
    
    @property
    def state(self) -> LiveState:
        return self._state
    
    def _on_start_live(self, cmd: StartLiveCommand) -> None:
        if self._state.is_live:
            return
        
        # Apply channel if specified
        channel = cmd.configuration or self._state.current_channel
        if channel and channel in self._channel_configs:
            self._apply_channel(channel)
        
        # Start streaming
        self._camera.start_streaming(self._on_frame)
        
        # Start triggering
        self._start_triggering()
        
        # Turn on illumination
        if channel:
            self._illumination.turn_on(channel)
        
        self._state = replace(self._state, is_live=True, current_channel=channel)
        self._bus.publish(LiveStateChanged(is_live=True, configuration=channel))
    
    def _on_stop_live(self, cmd: StopLiveCommand) -> None:
        if not self._state.is_live:
            return
        
        # Stop triggering
        self._stop_triggering()
        
        # Turn off illumination
        self._illumination.turn_off()
        
        # Stop streaming
        self._camera.stop_streaming()
        
        self._state = replace(self._state, is_live=False)
        self._bus.publish(LiveStateChanged(is_live=False, configuration=None))
    
    def _on_set_channel(self, cmd: SetMicroscopeModeCommand) -> None:
        channel = cmd.configuration_name
        if channel not in self._channel_configs:
            logger.warning(f"Unknown channel: {channel}")
            return
        
        self._apply_channel(channel)
        
        if self._state.is_live:
            self._illumination.turn_on(channel)
        
        self._state = replace(self._state, current_channel=channel)
        self._bus.publish(MicroscopeModeChanged(configuration_name=channel))
    
    def _apply_channel(self, channel: str) -> None:
        config = self._channel_configs[channel]
        
        # Set camera parameters
        self._camera._camera.set_exposure(config.exposure_ms)
        self._camera._camera.set_analog_gain(config.analog_gain)
        
        # Set illumination
        self._illumination.set_channel(config.illumination_source, config.intensity)
    
    def _on_set_trigger_mode(self, cmd: SetTriggerModeCommand) -> None:
        self._state = replace(self._state, trigger_mode=cmd.mode)
        
        if self._state.is_live:
            self._stop_triggering()
            self._start_triggering()
        
        self._bus.publish(TriggerModeChanged(mode=cmd.mode))
    
    def _on_set_fps(self, cmd: SetTriggerFPSCommand) -> None:
        self._state = replace(self._state, trigger_fps=cmd.fps)
        
        if self._state.is_live and self._state.trigger_mode == "Software":
            self._stop_triggering()
            self._start_triggering()
        
        self._bus.publish(TriggerFPSChanged(fps=cmd.fps))
    
    def _start_triggering(self) -> None:
        if self._state.trigger_mode == "Software":
            self._schedule_trigger()
        elif self._state.trigger_mode == "Hardware":
            if self._mcu:
                # Configure hardware trigger at specified FPS
                pass
    
    def _stop_triggering(self) -> None:
        if self._trigger_timer:
            self._trigger_timer.cancel()
            self._trigger_timer = None
    
    def _schedule_trigger(self) -> None:
        interval = 1.0 / self._state.trigger_fps
        self._trigger_timer = threading.Timer(interval, self._do_trigger)
        self._trigger_timer.daemon = True
        self._trigger_timer.start()
    
    def _do_trigger(self) -> None:
        if self._state.is_live and self._state.trigger_mode == "Software":
            self._camera.send_trigger()
            self._schedule_trigger()
    
    def _on_frame(self, frame: CameraFrame) -> None:
        # Forward to data stream
        self._streams.frames.publish(FrameData(
            frame=frame.frame,
            frame_id=frame.frame_id,
            timestamp=frame.timestamp,
            channel=self._state.current_channel
        ))
```

### AcquisitionController

**Note:** This largely reuses the existing `MultiPointController` and `MultiPointWorker` logic.

```python
# squid/controllers/acquisition_controller.py
@dataclass
class AcquisitionState:
    is_running: bool
    is_paused: bool
    config: AcquisitionConfig | None
    current_fov: int
    total_fovs: int
    current_round: int
    total_rounds: int
    current_channel: str
    progress: float
    eta_seconds: float | None


class AcquisitionController:
    """Orchestrates multi-point acquisition."""
    
    def __init__(
        self,
        camera_controller: CameraController,
        stage_controller: StageController,
        illumination_service: IlluminationService,
        autofocus_controller: 'AutofocusController',
        live_controller: LiveController,
        peripherals_controller: PeripheralsController,
        event_bus: EventBus,
        streams: DataStreams,
        channel_configs: dict[str, ChannelConfig]
    ):
        self._camera = camera_controller
        self._stage = stage_controller
        self._illumination = illumination_service
        self._autofocus = autofocus_controller
        self._live = live_controller
        self._peripherals = peripherals_controller
        self._bus = event_bus
        self._streams = streams
        self._channel_configs = channel_configs
        
        self._state = AcquisitionState(
            is_running=False,
            is_paused=False,
            config=None,
            current_fov=0,
            total_fovs=0,
            current_round=0,
            total_rounds=0,
            current_channel="",
            progress=0.0,
            eta_seconds=None
        )
        
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()
        self._worker_thread: threading.Thread | None = None
        
        self._bus.subscribe(StartAcquisitionCommand, self._on_start)
        self._bus.subscribe(StopAcquisitionCommand, self._on_stop)
        self._bus.subscribe(PauseAcquisitionCommand, self._on_pause)
        self._bus.subscribe(ResumeAcquisitionCommand, self._on_resume)
    
    @property
    def state(self) -> AcquisitionState:
        return self._state
    
    def _on_start(self, cmd: StartAcquisitionCommand) -> None:
        if self._state.is_running:
            return
        
        # Stop live if running
        if self._live.state.is_live:
            self._bus.publish(StopLiveCommand())
        
        self._stop_flag.clear()
        self._pause_flag.clear()
        
        config = cmd.config
        total_fovs = len(config.positions) if hasattr(config, 'positions') else config.nx * config.ny
        
        self._state = AcquisitionState(
            is_running=True,
            is_paused=False,
            config=config,
            current_fov=0,
            total_fovs=total_fovs,
            current_round=0,
            total_rounds=config.n_rounds if hasattr(config, 'n_rounds') else 1,
            current_channel="",
            progress=0.0,
            eta_seconds=None
        )
        
        self._bus.publish(AcquisitionStarted(
            experiment_id=config.experiment_id if hasattr(config, 'experiment_id') else str(uuid.uuid4()),
            timestamp=time.time()
        ))
        
        self._worker_thread = threading.Thread(target=self._run_acquisition, daemon=True)
        self._worker_thread.start()
    
    def _on_stop(self, cmd: StopAcquisitionCommand) -> None:
        self._stop_flag.set()
        self._pause_flag.set()  # Unblock if paused
    
    def _on_pause(self, cmd: PauseAcquisitionCommand) -> None:
        self._pause_flag.set()
        self._state = replace(self._state, is_paused=True)
        self._bus.publish(AcquisitionPaused())
    
    def _on_resume(self, cmd: ResumeAcquisitionCommand) -> None:
        self._pause_flag.clear()
        self._state = replace(self._state, is_paused=False)
        self._bus.publish(AcquisitionResumed())
    
    def _run_acquisition(self) -> None:
        """Main acquisition loop. Runs in background thread."""
        config = self._state.config
        start_time = time.time()
        images_captured = 0
        
        try:
            for round_idx in range(self._state.total_rounds):
                if self._stop_flag.is_set():
                    break
                
                self._state = replace(self._state, current_round=round_idx + 1)
                
                for fov_idx, position in enumerate(self._generate_positions(config)):
                    if self._stop_flag.is_set():
                        break
                    
                    # Handle pause
                    while self._pause_flag.is_set() and not self._stop_flag.is_set():
                        time.sleep(0.1)
                    
                    # Move stage
                    self._stage.move_to_blocking(position.x, position.y, position.z)
                    
                    # Autofocus if enabled
                    if config.autofocus and config.autofocus.enabled:
                        if fov_idx % config.autofocus.every_n_fovs == 0:
                            self._run_autofocus()
                    
                    # Acquire each channel
                    for channel_config in config.channels:
                        if self._stop_flag.is_set():
                            break
                        
                        self._acquire_channel(position, channel_config, fov_idx, round_idx)
                        images_captured += 1
                    
                    # Update progress
                    self._update_progress(fov_idx, round_idx, start_time, images_captured)
            
            # Success
            self._state = replace(self._state, is_running=False)
            self._bus.publish(AcquisitionFinished(success=True))
            
        except Exception as e:
            logger.exception(f"Acquisition failed: {e}")
            self._state = replace(self._state, is_running=False)
            self._bus.publish(AcquisitionFinished(success=False, error=str(e)))
    
    def _generate_positions(self, config: AcquisitionConfig):
        """Generate FOV positions from config."""
        # Reuse existing ScanCoordinates logic
        # This would yield Position objects
        ...
    
    def _acquire_channel(self, position, channel_config, fov_idx, round_idx) -> None:
        """Acquire single channel at position."""
        # Set illumination
        self._illumination.set_channel(channel_config.name, channel_config.intensity)
        self._illumination.turn_on()
        
        # Set camera
        self._camera._camera.set_exposure(channel_config.exposure_ms)
        
        # Capture
        frame = self._camera.capture_single()
        
        # Turn off illumination
        self._illumination.turn_off()
        
        # Save (async via job queue - reuse existing JobRunner)
        # ...
        
        # Publish for display
        self._streams.frames.publish(FrameData(
            frame=frame.frame,
            frame_id=frame.frame_id,
            timestamp=frame.timestamp,
            channel=channel_config.name
        ))
        
        self._state = replace(self._state, current_channel=channel_config.name)
    
    def _update_progress(self, fov_idx, round_idx, start_time, images_captured) -> None:
        total_images = self._state.total_fovs * self._state.total_rounds * len(self._state.config.channels)
        progress = images_captured / total_images if total_images > 0 else 0
        
        elapsed = time.time() - start_time
        eta = (elapsed / progress - elapsed) if progress > 0 else None
        
        self._state = replace(
            self._state,
            current_fov=fov_idx + 1,
            progress=progress,
            eta_seconds=eta
        )
        
        self._bus.publish(AcquisitionProgress(
            current_fov=fov_idx + 1,
            total_fovs=self._state.total_fovs,
            current_round=round_idx + 1,
            total_rounds=self._state.total_rounds,
            current_channel=self._state.current_channel,
            progress_percent=progress * 100,
            eta_seconds=eta
        ))
```

---

## 6. Widget Pattern

**Location:** `control/widgets/` (existing, refactor to new pattern)

Widgets are dumb: they render state and emit commands. No business logic.

```python
# control/widgets/camera/settings.py
from qtpy.QtWidgets import QWidget, QDoubleSpinBox, QVBoxLayout
from qtpy.QtCore import QObject, Signal

class CameraSettingsWidget(QWidget):
    """Camera settings. Publishes commands, renders state."""
    
    def __init__(self, event_bus: EventBus, parent: QWidget | None = None):
        super().__init__(parent)
        self._bus = event_bus
        
        self._setup_ui()
        
        # Subscribe to state changes
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

### Qt Signal Bridge for DataStreams

```python
# control/widgets/display/bridge.py
from qtpy.QtCore import QObject, Signal

class DisplayBridge(QObject):
    """Bridges DataStreams to Qt signals for thread-safe display updates."""
    
    frame_received = Signal(object)  # FrameData
    position_received = Signal(object)  # PositionData
    
    def __init__(self, streams: DataStreams):
        super().__init__()
        streams.frames.subscribe(self._on_frame)
        streams.positions.subscribe(self._on_position)
    
    def _on_frame(self, data: FrameData) -> None:
        # Called from camera thread - emit signal to marshal to GUI thread
        self.frame_received.emit(data)
    
    def _on_position(self, data: PositionData) -> None:
        self.position_received.emit(data)


class NapariLiveWidget(QWidget):
    """Live camera display using napari."""
    
    def __init__(self, bridge: DisplayBridge, parent: QWidget | None = None):
        super().__init__(parent)
        self._setup_ui()
        
        # Connect to bridge signal (thread-safe)
        bridge.frame_received.connect(self._on_frame)
    
    def _on_frame(self, data: FrameData) -> None:
        # Now on GUI thread, safe to update
        self._viewer.layers[0].data = data.frame
```

---

## 7. Application Wiring

**Location:** `squid/application.py` (existing, update)

```python
# squid/application.py
@dataclass
class Controllers:
    """Container for all controllers."""
    camera: CameraController
    stage: StageController
    peripherals: PeripheralsController
    live: LiveController
    acquisition: AcquisitionController
    autofocus: AutofocusController
    # tracking: TrackingController  # if needed


class ApplicationContext:
    """Dependency injection container. Creates and owns all components."""
    
    def __init__(self, config: AppConfig, simulated: bool = False):
        self._config = config
        self._simulated = simulated
        
        # Build in order
        self._event_bus = EventBus()
        self._data_streams = DataStreams()
        self._hardware = self._build_hardware()
        self._services = self._build_services()
        self._controllers = self._build_controllers()
    
    @property
    def event_bus(self) -> EventBus:
        return self._event_bus
    
    @property
    def data_streams(self) -> DataStreams:
        return self._data_streams
    
    @property
    def controllers(self) -> Controllers:
        return self._controllers
    
    def _build_hardware(self) -> Hardware:
        """Create hardware instances."""
        if self._simulated:
            camera = SimulatedCamera(self._config.camera)
            stage = SimulatedStage(self._config.stage)
            # ... other simulated hardware
        else:
            camera = get_camera(self._config.camera)
            stage = CephlaStage(self._config.stage, self._mcu)
            # ... real hardware
        
        return Hardware(camera=camera, stage=stage, ...)
    
    def _build_services(self) -> Services:
        """Create services wrapping hardware."""
        return Services(
            camera=CameraService(self._hardware.camera),
            stage=StageService(self._hardware.stage),
            illumination=IlluminationService(
                self._hardware.light_sources,
                self._hardware.microcontroller
            ),
            peripherals=PeripheralsService(
                microcontroller=self._hardware.microcontroller,
                filter_wheel=self._hardware.filter_wheel,
                objective_changer=self._hardware.objective_changer,
                spinning_disk=self._hardware.spinning_disk,
                piezo=self._hardware.piezo,
            ),
        )
    
    def _build_controllers(self) -> Controllers:
        """Create controllers."""
        camera = CameraController(
            self._services.camera,
            self._event_bus,
            self._data_streams
        )
        
        stage = StageController(
            self._services.stage,
            self._event_bus,
            self._data_streams
        )
        
        peripherals = PeripheralsController(
            self._services.peripherals,
            self._event_bus
        )
        
        autofocus = AutofocusController(
            camera,
            stage,
            self._event_bus
        )
        
        live = LiveController(
            camera,
            self._services.illumination,
            self._hardware.microcontroller,
            self._event_bus,
            self._data_streams,
            self._config.channels
        )
        
        acquisition = AcquisitionController(
            camera,
            stage,
            self._services.illumination,
            autofocus,
            live,
            peripherals,
            self._event_bus,
            self._data_streams,
            self._config.channels
        )
        
        return Controllers(
            camera=camera,
            stage=stage,
            peripherals=peripherals,
            live=live,
            acquisition=acquisition,
            autofocus=autofocus,
        )
    
    def create_gui(self) -> 'HighContentScreeningGui':
        """Create the main GUI window."""
        from control.gui_hcs import HighContentScreeningGui
        return HighContentScreeningGui(
            event_bus=self._event_bus,
            data_streams=self._data_streams,
            controllers=self._controllers,
            config=self._config,
        )
    
    def shutdown(self) -> None:
        """Clean shutdown of all components."""
        self._event_bus.shutdown()
        # Close hardware
        # ...
```

---

## 8. Directory Structure

```
squid/
├── abc.py                      # Hardware protocols (existing, extend)
├── events.py                   # EventBus + events (existing, extend)
├── data_streams.py             # NEW: Real-time data streams
├── application.py              # DI container (existing, update)
├── registry.py                 # Plugin registry (existing, keep)
├── exceptions.py               # Exceptions (existing, keep)
├── logging.py                  # Logging setup (existing, keep)
├── config/
│   ├── __init__.py             # Config models (existing, keep)
│   └── acquisition.py          # Acquisition config (existing, keep)
├── services/
│   ├── __init__.py
│   ├── camera_service.py       # Refactor: remove EventBus
│   ├── stage_service.py        # Refactor: remove EventBus
│   ├── illumination_service.py # Refactor: remove EventBus
│   └── peripherals_service.py  # NEW: consolidated peripheral access
├── controllers/
│   ├── __init__.py
│   ├── camera_controller.py    # NEW
│   ├── stage_controller.py     # NEW
│   ├── peripherals_controller.py # NEW
│   ├── live_controller.py      # Refactor from control/core/
│   ├── acquisition_controller.py # Refactor from control/core/
│   └── autofocus_controller.py # Refactor from control/core/
└── utils/
    ├── safe_callback.py        # Existing, keep
    ├── thread_safe_state.py    # Existing, keep
    └── worker_manager.py       # Existing, keep

control/
├── gui_hcs.py                  # Main window (update to use new pattern)
├── widgets/
│   ├── camera/
│   │   ├── settings.py         # Refactor to new pattern
│   │   └── live_control.py
│   ├── stage/
│   │   ├── navigation.py
│   │   └── autofocus.py
│   ├── display/
│   │   ├── bridge.py           # NEW: Qt signal bridge
│   │   ├── napari_live.py
│   │   └── stats.py
│   ├── hardware/
│   │   ├── filter_controller.py
│   │   ├── objectives.py
│   │   ├── confocal.py         # Spinning disk controls
│   │   ├── dac.py
│   │   └── piezo.py
│   ├── acquisition/
│   │   ├── flexible_multipoint.py
│   │   └── wellplate_multipoint.py
│   └── wellplate/
│       ├── format.py
│       ├── calibration.py
│       └── well_selection.py
├── peripherals/                # Hardware drivers (existing, keep)
│   ├── cameras/
│   ├── stage/
│   ├── lighting/
│   └── filter_wheel/
└── _def.py                     # Constants (existing, migrate to config)
```

---

## 9. Migration Plan

### Phase 1: Infrastructure
1. Create `squid/data_streams.py`
2. Add new events to `squid/events.py`
3. Add new protocols to `squid/abc.py`

### Phase 2: Services
1. Refactor existing services to remove EventBus subscriptions
2. Create `PeripheralsService` consolidating peripheral access
3. Ensure all services are pure (stateless except hardware + lock)

### Phase 3: Controllers
1. Create `CameraController`, `StageController`, `PeripheralsController`
2. Refactor `LiveController` to use new service pattern
3. Refactor `MultiPointController` → `AcquisitionController`
4. Refactor `AutoFocusController`

### Phase 4: Widgets
1. Create `DisplayBridge` for thread-safe frame delivery
2. Refactor widgets to use EventBus only (no direct service calls)
3. Update `HighContentScreeningGui` to use new `ApplicationContext`

### Phase 5: Cleanup
1. Remove old service files that subscribed to EventBus
2. Move reusable logic from `control/core/` to `squid/controllers/`
3. Update tests

---

## 10. Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Services** | Subscribe to EventBus | Pure, no EventBus |
| **State ownership** | Scattered | Controllers own state |
| **Real-time data** | Through EventBus | DataStreams (separate) |
| **Peripheral hardware** | Ad-hoc | Consolidated PeripheralsService + Controller |
| **Widget communication** | Mixed (direct + events) | EventBus only |
| **Threading** | Implicit | Explicit (Services have locks, Controllers spawn threads) |
| **Hardware availability** | Crashes on None | Graceful `HardwareNotAvailable` |

This architecture provides:
- **Testability**: Mock services, publish events, verify behavior
- **Robustness**: Clear error boundaries, explicit state
- **Extensibility**: Add new hardware by implementing protocols
- **Maintainability**: Clear responsibilities, no inheritance hierarchies