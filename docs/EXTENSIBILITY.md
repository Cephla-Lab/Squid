# Squid Extensibility Guide

This document analyzes Squid's current architecture from an extensibility perspective and proposes patterns that would make it easier to add new features, hardware support, and acquisition modes.

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [The Core Problem: Implicit Architecture](#2-the-core-problem-implicit-architecture)
3. [Pattern 1: Explicit Dependencies](#3-pattern-1-explicit-dependencies)
4. [Pattern 2: Configuration as Data](#4-pattern-2-configuration-as-data)
5. [Pattern 3: Plugin Registries](#5-pattern-3-plugin-registries)
6. [Pattern 4: Event Bus](#6-pattern-4-event-bus)
7. [Pattern 5: Composition Over Inheritance](#7-pattern-5-composition-over-inheritance)
8. [Pattern 6: Small, Focused Classes](#8-pattern-6-small-focused-classes)
9. [Pattern 7: Acquisition as Pipeline](#9-pattern-7-acquisition-as-pipeline)
10. [Implementation Roadmap](#10-implementation-roadmap)

---

## 1. Current State Analysis

### What Works Well

Squid has some good extensibility patterns already:

1. **Abstract Base Classes** (`squid/abc.py`)
   - `AbstractCamera`, `AbstractStage`, `AbstractFilterWheelController`
   - Clear contracts for hardware implementations
   - Good use of `@abstractmethod`

2. **Factory Functions** (`squid/camera/utils.py:22-97`)
   - `get_camera(config)` creates cameras from config
   - Centralizes instantiation logic
   - Easy to add new camera types

3. **Configuration Objects** (`squid/config.py`)
   - Pydantic models for `CameraConfig`, `StageConfig`
   - Type validation at load time
   - Better than raw dicts

### What Makes Extension Difficult

Adding a new feature to Squid typically requires:

1. **Modifying multiple god objects** - `MultiPointController` (568 lines), `widgets.py` (10,671 lines)
2. **Understanding hidden dependencies** - 40+ files import `from control._def import *`
3. **No extension points** - Must modify existing classes, not register new implementations
4. **Tight coupling** - GUI creates and owns controllers, controllers reach into globals
5. **Fixed callback structures** - `MultiPointControllerFunctions` is a fixed dataclass

### Extensibility Pain Points by Feature Type

| Feature Type | Current Difficulty | Why |
|--------------|-------------------|-----|
| New camera | Easy | Good ABC, factory pattern |
| New stage | Easy | Good ABC, factory pattern |
| New acquisition mode | Hard | Must modify MultiPointController |
| New autofocus algorithm | Hard | Embedded in AutoFocusController |
| New trigger mode | Hard | Scattered across controllers |
| New GUI widget | Medium | 10,671-line widgets.py |
| New hardware addon | Medium | MicroscopeAddons is extensible-ish |
| New processing step | Hard | No pipeline architecture |

---

## 2. The Core Problem: Implicit Architecture

The fundamental issue is that Squid's architecture is **implicit** rather than **explicit**. Dependencies are hidden, responsibilities are unclear, and extension requires reading thousands of lines of code to understand what's happening.

### Example: Adding a New Autofocus Algorithm

**Current state**: To add a new autofocus algorithm, you must:

1. Modify `AutoFocusController` (216 lines)
2. Modify `AutofocusWorker` (unknown lines)
3. Modify `AutoFocusWidget` to expose new options
4. Hope you don't break existing functionality

**Desired state**: To add a new autofocus algorithm, you should:

1. Create a new class implementing `AutofocusAlgorithm` protocol
2. Register it: `autofocus_registry.register("my_algorithm", MyAlgorithm)`
3. Done - it appears in the UI automatically

### Example: Adding a New Acquisition Pattern

**Current state**: To add a new acquisition pattern (e.g., spiral scan), you must:

1. Modify `MultiPointController` (568 lines)
2. Modify `MultiPointWorker` (899 lines)
3. Modify `ScanCoordinates`
4. Modify widgets to expose new options
5. Test that you didn't break the 5 existing acquisition modes

**Desired state**: To add a new acquisition pattern, you should:

1. Create a new class implementing `AcquisitionPattern` protocol
2. Register it: `acquisition_registry.register("spiral", SpiralAcquisition)`
3. Done - it appears in the UI automatically

---

## 3. Pattern 1: Explicit Dependencies

### The Problem

Dependencies are hidden behind `from control._def import *` (40+ files) and reached through long chains:

```python
# multi_point_controller.py - dependencies are hidden
self.deltaX = control._def.Acquisition.DX  # Where does this come from?
self.use_piezo = control._def.MULTIPOINT_USE_PIEZO_FOR_ZSTACKS  # Global mutable state
```

This makes it impossible to:
- Understand what a class needs without reading all its code
- Test classes in isolation
- Reuse classes in different contexts

### The Solution: Constructor Injection

Every dependency should be passed explicitly through the constructor:

```python
# BEFORE: Hidden dependencies
class MultiPointController:
    def __init__(self, microscope, live_controller, ...):
        self.deltaX = control._def.Acquisition.DX  # Hidden!
        self.use_piezo = control._def.MULTIPOINT_USE_PIEZO_FOR_ZSTACKS  # Hidden!

# AFTER: Explicit dependencies
@dataclass(frozen=True)
class AcquisitionDefaults:
    """Immutable configuration for acquisition defaults."""
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    delta_z_um: float = 1.5
    use_piezo_for_zstacks: bool = False
    image_format: str = "bmp"
    display_scaling: float = 0.3

class MultiPointController:
    def __init__(
        self,
        microscope: Microscope,
        live_controller: LiveController,
        defaults: AcquisitionDefaults,  # Explicit!
        # ... other explicit dependencies
    ):
        self.defaults = defaults
        self.delta_x = defaults.delta_x_mm
```

### Benefits

1. **Testable**: Pass mock dependencies for unit tests
2. **Understandable**: Constructor signature documents requirements
3. **Flexible**: Same class works with different configurations
4. **IDE support**: Autocomplete shows what's needed

### Implementation

Create a `Dependencies` module that wires everything together:

```python
# software/squid/dependencies.py
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class AcquisitionDefaults:
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    delta_z_um: float = 1.5
    use_piezo_for_zstacks: bool = False

@dataclass(frozen=True)
class AutofocusDefaults:
    n_steps: int = 10
    step_size_um: float = 1.524
    crop_width: int = 500
    crop_height: int = 500

class ApplicationConfig:
    """
    Central configuration that replaces _def.py globals.
    Loaded once at startup, immutable thereafter.
    """
    def __init__(self, config_path: str):
        self._load_from_file(config_path)

    @property
    def acquisition(self) -> AcquisitionDefaults:
        return self._acquisition

    @property
    def autofocus(self) -> AutofocusDefaults:
        return self._autofocus

    # ... etc
```

---

## 4. Pattern 2: Configuration as Data

### The Problem

Configuration is scattered across:
- `_def.py` (990 lines of mutable globals)
- Constructor parameters
- Setter methods (`set_NX`, `set_deltaZ`, etc.)
- Runtime state

This makes it impossible to:
- Validate configuration before starting
- Save/restore configurations
- Compare configurations
- Serialize configurations

### The Solution: Immutable Configuration Objects

Configuration should be:
1. **Defined as data** - Pydantic models or frozen dataclasses
2. **Validated at load time** - Fail fast with clear errors
3. **Immutable after creation** - No surprise mutations
4. **Serializable** - Can be saved, restored, compared

```python
# BEFORE: Scattered configuration
class MultiPointController:
    def set_NX(self, N):
        self.NX = N

    def set_deltaX(self, delta):
        self.deltaX = delta

    # 20 more setters...

# AFTER: Configuration as data
from pydantic import BaseModel, validator

class GridScanConfig(BaseModel):
    """Configuration for grid-based scanning."""
    nx: int = 1
    ny: int = 1
    nz: int = 1
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    delta_z_um: float = 1.5

    class Config:
        frozen = True  # Immutable

    @validator('nx', 'ny', 'nz')
    def must_be_positive(cls, v):
        if v < 1:
            raise ValueError('Must be at least 1')
        return v

class TimelapsConfig(BaseModel):
    """Configuration for timelapse acquisition."""
    n_timepoints: int = 1
    interval_seconds: float = 0

    class Config:
        frozen = True

class AcquisitionConfig(BaseModel):
    """Complete acquisition configuration."""
    grid: GridScanConfig
    timelapse: TimelapsConfig
    channels: List[ChannelConfig]
    autofocus: Optional[AutofocusConfig] = None
    output_path: Path
    experiment_id: str

    class Config:
        frozen = True

    def to_json(self) -> str:
        return self.json(indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> 'AcquisitionConfig':
        return cls.parse_raw(json_str)
```

### Benefits

1. **Validation**: Errors caught at configuration time, not during acquisition
2. **Serialization**: Save configurations for reproducibility
3. **Comparison**: `config1 == config2` works
4. **Documentation**: Schema is self-documenting
5. **IDE support**: Autocomplete for configuration fields

---

## 5. Pattern 3: Plugin Registries

### The Problem

Adding new implementations requires modifying existing code:

```python
# squid/camera/utils.py - Must modify this file to add new camera
def get_camera(config):
    if config.camera_type == CameraVariant.TOUPCAM:
        return ToupcamCamera(config)
    elif config.camera_type == CameraVariant.FLIR:
        return FlirCamera(config)
    elif config.camera_type == CameraVariant.HAMAMATSU:
        return HamamatsuCamera(config)
    # Must add new elif for each camera type!
```

### The Solution: Plugin Registry

Implementations register themselves; the registry provides discovery:

```python
# software/squid/registry.py
from typing import TypeVar, Generic, Dict, Type, Callable, Optional
from dataclasses import dataclass

T = TypeVar('T')

class Registry(Generic[T]):
    """
    Generic registry for plugin implementations.

    Usage:
        # Define registry
        camera_registry = Registry[AbstractCamera]("camera")

        # Register implementations (can be done at import time)
        @camera_registry.register("toupcam")
        class ToupcamCamera(AbstractCamera):
            ...

        # Or register with a factory
        camera_registry.register_factory("flir", lambda cfg: FlirCamera(cfg))

        # Get implementation
        camera = camera_registry.create("toupcam", config)

        # List available
        print(camera_registry.available())  # ["toupcam", "flir", ...]
    """

    def __init__(self, name: str):
        self.name = name
        self._implementations: Dict[str, Type[T]] = {}
        self._factories: Dict[str, Callable[..., T]] = {}

    def register(self, name: str):
        """Decorator to register a class."""
        def decorator(cls: Type[T]) -> Type[T]:
            self._implementations[name] = cls
            return cls
        return decorator

    def register_factory(self, name: str, factory: Callable[..., T]):
        """Register a factory function."""
        self._factories[name] = factory

    def create(self, name: str, *args, **kwargs) -> T:
        """Create an instance by name."""
        if name in self._factories:
            return self._factories[name](*args, **kwargs)
        if name in self._implementations:
            return self._implementations[name](*args, **kwargs)
        raise KeyError(
            f"Unknown {self.name}: '{name}'. "
            f"Available: {self.available()}"
        )

    def available(self) -> List[str]:
        """List available implementations."""
        return sorted(set(self._implementations.keys()) | set(self._factories.keys()))

    def get_class(self, name: str) -> Optional[Type[T]]:
        """Get the class for a name (if registered as class, not factory)."""
        return self._implementations.get(name)


# Create registries for extensible components
camera_registry = Registry[AbstractCamera]("camera")
stage_registry = Registry[AbstractStage]("stage")
autofocus_registry = Registry["AutofocusAlgorithm"]("autofocus")
acquisition_registry = Registry["AcquisitionPattern"]("acquisition")
```

### Using the Registry

```python
# In camera implementation file (e.g., camera_toupcam.py)
from squid.registry import camera_registry

@camera_registry.register("toupcam")
class ToupcamCamera(AbstractCamera):
    def __init__(self, config: CameraConfig, ...):
        ...

# In camera factory (replaces big if/elif chain)
from squid.registry import camera_registry

def get_camera(config: CameraConfig, simulated: bool = False) -> AbstractCamera:
    if simulated:
        return camera_registry.create("simulated", config)
    return camera_registry.create(config.camera_type.value, config)
```

### Autofocus Plugin Example

```python
# software/squid/autofocus/protocol.py
from typing import Protocol, List
import numpy as np

class AutofocusAlgorithm(Protocol):
    """Protocol for autofocus algorithms."""

    @property
    def name(self) -> str:
        """Human-readable name for UI."""
        ...

    def compute_focus_score(self, image: np.ndarray) -> float:
        """Compute focus score for an image. Higher = more in focus."""
        ...

    def find_best_z(self, scores: List[float], z_positions: List[float]) -> float:
        """Given scores at z positions, find optimal z."""
        ...


# software/squid/autofocus/algorithms.py
from squid.registry import autofocus_registry

@autofocus_registry.register("brenner_gradient")
class BrennerGradient:
    """Classic Brenner gradient autofocus."""

    name = "Brenner Gradient"

    def compute_focus_score(self, image: np.ndarray) -> float:
        # Brenner gradient: sum of squared horizontal differences
        diff = image[:, 2:].astype(float) - image[:, :-2].astype(float)
        return np.sum(diff ** 2)

    def find_best_z(self, scores: List[float], z_positions: List[float]) -> float:
        return z_positions[np.argmax(scores)]


@autofocus_registry.register("laplacian_variance")
class LaplacianVariance:
    """Laplacian variance autofocus."""

    name = "Laplacian Variance"

    def compute_focus_score(self, image: np.ndarray) -> float:
        from scipy import ndimage
        laplacian = ndimage.laplace(image.astype(float))
        return np.var(laplacian)

    def find_best_z(self, scores: List[float], z_positions: List[float]) -> float:
        return z_positions[np.argmax(scores)]


@autofocus_registry.register("normalized_variance")
class NormalizedVariance:
    """Normalized variance - good for low-contrast samples."""

    name = "Normalized Variance"

    def compute_focus_score(self, image: np.ndarray) -> float:
        mean = np.mean(image)
        if mean == 0:
            return 0
        return np.var(image) / mean

    def find_best_z(self, scores: List[float], z_positions: List[float]) -> float:
        # Could use curve fitting for sub-step precision
        return z_positions[np.argmax(scores)]
```

### Using Autofocus Plugins

```python
# In AutoFocusController
class AutoFocusController:
    def __init__(
        self,
        algorithm_name: str = "brenner_gradient",
        ...
    ):
        self.algorithm = autofocus_registry.create(algorithm_name)

    def set_algorithm(self, name: str):
        """Change autofocus algorithm at runtime."""
        self.algorithm = autofocus_registry.create(name)

    @staticmethod
    def available_algorithms() -> List[str]:
        """List available autofocus algorithms for UI."""
        return autofocus_registry.available()
```

---

## 6. Pattern 4: Event Bus

### The Problem

Components communicate through fixed callback dataclasses:

```python
# multi_point_utils.py - Fixed structure
@dataclass
class MultiPointControllerFunctions:
    signal_acquisition_start: Callable[[AcquisitionParameters], None]
    signal_acquisition_finished: Callable[[], None]
    signal_new_image: Callable[[CameraFrame, CaptureInfo], None]
    signal_current_configuration: Callable[[ChannelMode], None]
    signal_current_fov: Callable[[float, float], None]
    signal_overall_progress: Callable[[OverallProgressUpdate], None]
    signal_region_progress: Callable[[RegionProgressUpdate], None]
    # Can't add new events without modifying this!
```

To add a new event type:
1. Modify `MultiPointControllerFunctions`
2. Update all places that create it
3. Update `NoOpCallbacks`
4. Update `QtMultiPointController`

### The Solution: Event Bus

An event bus allows components to publish and subscribe to events without knowing about each other:

```python
# software/squid/events.py
from dataclasses import dataclass
from typing import Callable, Dict, List, Type, TypeVar, Any
from threading import Lock
import weakref

# Event types (just dataclasses)
@dataclass
class Event:
    """Base class for all events."""
    pass

@dataclass
class AcquisitionStarted(Event):
    parameters: 'AcquisitionParameters'
    timestamp: float

@dataclass
class AcquisitionFinished(Event):
    success: bool
    error: Optional[Exception] = None

@dataclass
class ImageCaptured(Event):
    frame: 'CameraFrame'
    info: 'CaptureInfo'

@dataclass
class FocusChanged(Event):
    z_mm: float
    source: str  # "autofocus", "manual", "focus_map"

@dataclass
class StageMovedTo(Event):
    x_mm: float
    y_mm: float
    z_mm: float

# You can add new event types without modifying existing code!
@dataclass
class MyCustomEvent(Event):
    data: Any


E = TypeVar('E', bound=Event)

class EventBus:
    """
    Simple event bus for decoupled communication.

    Usage:
        bus = EventBus()

        # Subscribe to events
        bus.subscribe(ImageCaptured, lambda e: display(e.frame))
        bus.subscribe(AcquisitionFinished, lambda e: cleanup())

        # Publish events
        bus.publish(ImageCaptured(frame=frame, info=info))

        # Unsubscribe
        bus.unsubscribe(ImageCaptured, handler)
    """

    def __init__(self):
        self._subscribers: Dict[Type[Event], List[Callable]] = {}
        self._lock = Lock()

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """Subscribe to an event type."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """Unsubscribe from an event type."""
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                except ValueError:
                    pass

    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        with self._lock:
            handlers = list(self._subscribers.get(type(event), []))

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                # Log but don't crash
                import squid.logging
                log = squid.logging.get_logger("EventBus")
                log.exception(f"Handler {handler} failed for event {event}")

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscribers.clear()


# Global event bus (or inject via dependency injection)
event_bus = EventBus()
```

### Qt Integration

For Qt GUI updates, events need to cross thread boundaries safely:

```python
# software/squid/events_qt.py
from PyQt5.QtCore import QObject, pyqtSignal, QMetaObject, Qt, Q_ARG
from typing import Type
from squid.events import Event, EventBus, E

class QtEventBridge(QObject):
    """
    Bridges EventBus to Qt signals for thread-safe GUI updates.

    Usage:
        bridge = QtEventBridge(event_bus)

        # Subscribe with automatic Qt thread marshalling
        bridge.subscribe_qt(ImageCaptured, self.on_image_captured)
    """

    # Generic signal that carries any event
    _event_signal = pyqtSignal(object)

    def __init__(self, bus: EventBus, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._qt_handlers: Dict[Type[Event], List[Callable]] = {}
        self._event_signal.connect(self._dispatch_event, Qt.QueuedConnection)

    def subscribe_qt(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        """Subscribe with Qt thread-safe delivery."""
        if event_type not in self._qt_handlers:
            self._qt_handlers[event_type] = []
            # Subscribe to bus once per event type
            self._bus.subscribe(event_type, lambda e: self._event_signal.emit(e))

        self._qt_handlers[event_type].append(handler)

    def _dispatch_event(self, event: Event):
        """Called on Qt main thread."""
        handlers = self._qt_handlers.get(type(event), [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                import squid.logging
                log = squid.logging.get_logger("QtEventBridge")
                log.exception(f"Qt handler failed for {event}")
```

### Using the Event Bus

```python
# In MultiPointWorker - publish events
class MultiPointWorker:
    def __init__(self, event_bus: EventBus, ...):
        self._bus = event_bus

    def _image_callback(self, frame: CameraFrame):
        # ... process image ...
        self._bus.publish(ImageCaptured(frame=frame, info=info))

    def run(self):
        self._bus.publish(AcquisitionStarted(parameters=self.params, timestamp=time.time()))
        try:
            # ... acquisition ...
            self._bus.publish(AcquisitionFinished(success=True))
        except Exception as e:
            self._bus.publish(AcquisitionFinished(success=False, error=e))


# In GUI - subscribe to events
class AcquisitionWidget(QFrame):
    def __init__(self, event_bridge: QtEventBridge):
        super().__init__()
        event_bridge.subscribe_qt(ImageCaptured, self._on_image)
        event_bridge.subscribe_qt(AcquisitionFinished, self._on_finished)

    def _on_image(self, event: ImageCaptured):
        self.display.show(event.frame)

    def _on_finished(self, event: AcquisitionFinished):
        self.start_button.setEnabled(True)
        if event.error:
            self.show_error(event.error)
```

### Benefits

1. **Extensible**: Add new event types without modifying existing code
2. **Decoupled**: Publishers don't know about subscribers
3. **Testable**: Easy to mock event bus for testing
4. **Debuggable**: Can log all events centrally
5. **Thread-safe**: Qt integration handles thread crossing

---

## 7. Pattern 5: Composition Over Inheritance

### The Problem

Squid uses inheritance and multiple inheritance for extension:

```python
# gui_hcs.py - Multiple inheritance
class QtMultiPointController(MultiPointController, QObject):
    acquisition_finished = Signal()
    # Mixes Qt signals into controller
```

This creates problems:
- Method resolution order (MRO) confusion
- Tight coupling between base and derived
- Can't reuse parts independently
- Hard to test in isolation

### The Solution: Composition and Protocols

Use composition to combine behaviors, and protocols to define interfaces:

```python
# BEFORE: Inheritance
class QtMultiPointController(MultiPointController, QObject):
    acquisition_finished = Signal()

    def _signal_acquisition_finished_fn(self):
        self.acquisition_finished.emit()

# AFTER: Composition
from typing import Protocol

class AcquisitionEvents(Protocol):
    """Protocol for acquisition event emission."""
    def on_acquisition_started(self, params: AcquisitionParameters) -> None: ...
    def on_acquisition_finished(self) -> None: ...
    def on_image_captured(self, frame: CameraFrame, info: CaptureInfo) -> None: ...


class QtAcquisitionEvents(QObject):
    """Qt signal-based implementation of AcquisitionEvents."""
    acquisition_started = Signal(object)
    acquisition_finished = Signal()
    image_captured = Signal(object, object)

    def on_acquisition_started(self, params):
        self.acquisition_started.emit(params)

    def on_acquisition_finished(self):
        self.acquisition_finished.emit()

    def on_image_captured(self, frame, info):
        self.image_captured.emit(frame, info)


class LoggingAcquisitionEvents:
    """Logging implementation of AcquisitionEvents."""
    def __init__(self):
        self._log = squid.logging.get_logger(self.__class__.__name__)

    def on_acquisition_started(self, params):
        self._log.info(f"Acquisition started: {params.experiment_id}")

    def on_acquisition_finished(self):
        self._log.info("Acquisition finished")

    def on_image_captured(self, frame, info):
        self._log.debug(f"Image captured: {info.file_id}")


class MultiPointController:
    """Pure Python controller - no Qt dependency."""

    def __init__(
        self,
        microscope: Microscope,
        events: AcquisitionEvents,  # Injected, not inherited
        ...
    ):
        self.events = events

    def _on_image(self, frame, info):
        self.events.on_image_captured(frame, info)


# Usage - compose behaviors
qt_events = QtAcquisitionEvents()
logging_events = LoggingAcquisitionEvents()

# Combine multiple event handlers
class CompositeEvents:
    def __init__(self, *handlers):
        self._handlers = handlers

    def on_acquisition_started(self, params):
        for h in self._handlers:
            h.on_acquisition_started(params)
    # ... etc

events = CompositeEvents(qt_events, logging_events)
controller = MultiPointController(microscope, events)
```

### Benefits

1. **Testable**: Use mock events for testing controller
2. **Flexible**: Combine different event handlers
3. **No MRO issues**: No diamond inheritance problems
4. **Reusable**: Qt events can be used elsewhere
5. **Type-safe**: Protocols provide type checking

---

## 8. Pattern 6: Small, Focused Classes

### The Problem

Squid has several god objects:

| File | Lines | Responsibilities |
|------|-------|------------------|
| `widgets.py` | 10,671 | 37 different widget classes |
| `multi_point_worker.py` | 899 | Acquisition, movement, autofocus, saving, display |
| `multi_point_controller.py` | 568 | Config, validation, focus maps, acquisition, cleanup |
| `gui_hcs.py` | 800+ | Hardware init, widget creation, layout, connections |
| `_def.py` | 990 | All configuration for everything |

### The Solution: Single Responsibility Principle

Each class should have one reason to change:

```python
# BEFORE: MultiPointWorker does everything
class MultiPointWorker:
    def run(self):
        # Coordinates
        # Movement
        # Autofocus
        # Illumination
        # Triggering
        # Saving
        # Display
        # Progress
        # Error handling
        # Cleanup

# AFTER: Focused classes composed together

class CoordinateGenerator:
    """Generates coordinates for acquisition patterns."""
    def generate_grid(self, config: GridScanConfig) -> Iterator[Coordinate]: ...
    def generate_spiral(self, config: SpiralConfig) -> Iterator[Coordinate]: ...

class PositionExecutor:
    """Executes movement to positions."""
    def move_to(self, coord: Coordinate) -> None: ...
    def wait_for_stable(self) -> None: ...

class ChannelExecutor:
    """Executes channel acquisition at a position."""
    def configure_channel(self, channel: ChannelConfig) -> None: ...
    def trigger_and_capture(self) -> CameraFrame: ...

class ZStackExecutor:
    """Executes z-stack at a position."""
    def __init__(self, channel_executor: ChannelExecutor): ...
    def execute_stack(self, config: ZStackConfig) -> List[CameraFrame]: ...

class ImageProcessor:
    """Processes and saves images."""
    def process(self, frame: CameraFrame, info: CaptureInfo) -> None: ...
    def save(self, frame: CameraFrame, path: Path) -> None: ...

class AcquisitionOrchestrator:
    """Orchestrates the acquisition flow."""
    def __init__(
        self,
        coordinates: CoordinateGenerator,
        position: PositionExecutor,
        z_stack: ZStackExecutor,
        processor: ImageProcessor,
        events: AcquisitionEvents,
    ):
        self._coords = coordinates
        self._position = position
        self._z_stack = z_stack
        self._processor = processor
        self._events = events

    def run(self, config: AcquisitionConfig):
        for coord in self._coords.generate(config):
            self._position.move_to(coord)
            for channel in config.channels:
                frames = self._z_stack.execute(config.z_stack)
                for frame in frames:
                    self._processor.process(frame, info)
                    self._events.on_image_captured(frame, info)
```

### Benefits

1. **Understandable**: Each class is small enough to understand fully
2. **Testable**: Test each component in isolation
3. **Reusable**: Use `ZStackExecutor` in different acquisition modes
4. **Maintainable**: Change saving without touching movement
5. **Extensible**: Add new coordinate patterns without touching acquisition

---

## 9. Pattern 7: Acquisition as Pipeline

### The Problem

Acquisition is implemented as monolithic methods with interleaved concerns:

```python
# Current: Everything interleaved
def acquire_at_position(self):
    if not self.perform_autofocus():  # Autofocus
        self._log.error(...)
    if self.NZ > 1:
        self.prepare_z_stack()  # Z setup
    for z_level in range(self.NZ):
        for config in self.selected_configurations:
            if self.NZ == 1:
                self.handle_z_offset(config, True)  # Z offset
            self.acquire_camera_image(...)  # Capture
            if self.NZ == 1:
                self.handle_z_offset(config, False)  # Z offset undo
            # Progress, coordinates, abort check...
```

Adding new behavior (e.g., focus tracking between z-slices) requires modifying this interleaved code.

### The Solution: Pipeline Architecture

Model acquisition as a pipeline of stages that can be composed:

```python
# software/squid/acquisition/pipeline.py
from typing import Protocol, Iterator, TypeVar, Generic
from dataclasses import dataclass
from abc import ABC, abstractmethod

T = TypeVar('T')

class PipelineStage(Protocol[T]):
    """A stage in the acquisition pipeline."""
    def process(self, input: T) -> Iterator[T]:
        """Process input and yield outputs."""
        ...

@dataclass
class AcquisitionContext:
    """Context passed through the pipeline."""
    config: AcquisitionConfig
    position: Coordinate
    z_level: int
    channel: ChannelConfig
    frame: Optional[CameraFrame] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# Pipeline stages

class PositionStage(PipelineStage[AcquisitionContext]):
    """Stage that iterates over positions."""
    def __init__(self, stage: AbstractStage, coordinates: CoordinateGenerator):
        self._stage = stage
        self._coords = coordinates

    def process(self, ctx: AcquisitionContext) -> Iterator[AcquisitionContext]:
        for coord in self._coords.generate(ctx.config):
            self._stage.move_to(coord.x_mm, coord.y_mm, coord.z_mm)
            yield dataclasses.replace(ctx, position=coord)


class AutofocusStage(PipelineStage[AcquisitionContext]):
    """Stage that performs autofocus."""
    def __init__(self, autofocus: AutoFocusController):
        self._af = autofocus

    def process(self, ctx: AcquisitionContext) -> Iterator[AcquisitionContext]:
        if ctx.config.autofocus and ctx.config.autofocus.enabled:
            self._af.autofocus()
            self._af.wait_till_autofocus_has_completed()
        yield ctx


class ZStackStage(PipelineStage[AcquisitionContext]):
    """Stage that iterates over z-stack."""
    def __init__(self, stage: AbstractStage):
        self._stage = stage

    def process(self, ctx: AcquisitionContext) -> Iterator[AcquisitionContext]:
        z_config = ctx.config.grid
        for z in range(z_config.nz):
            if z > 0:
                self._stage.move_z(z_config.delta_z_um / 1000)
            yield dataclasses.replace(ctx, z_level=z)


class ChannelStage(PipelineStage[AcquisitionContext]):
    """Stage that iterates over channels."""
    def __init__(self, live_controller: LiveController):
        self._live = live_controller

    def process(self, ctx: AcquisitionContext) -> Iterator[AcquisitionContext]:
        for channel in ctx.config.channels:
            self._live.set_microscope_mode(channel)
            yield dataclasses.replace(ctx, channel=channel)


class CaptureStage(PipelineStage[AcquisitionContext]):
    """Stage that captures images."""
    def __init__(self, camera: AbstractCamera):
        self._camera = camera

    def process(self, ctx: AcquisitionContext) -> Iterator[AcquisitionContext]:
        self._camera.send_trigger()
        frame = self._camera.read_camera_frame()
        yield dataclasses.replace(ctx, frame=frame)


class SaveStage(PipelineStage[AcquisitionContext]):
    """Stage that saves images."""
    def __init__(self, saver: ImageSaver):
        self._saver = saver

    def process(self, ctx: AcquisitionContext) -> Iterator[AcquisitionContext]:
        if ctx.frame:
            self._saver.save(ctx.frame, ctx.config.output_path, ctx.metadata)
        yield ctx


class Pipeline:
    """Composes stages into a pipeline."""

    def __init__(self, stages: List[PipelineStage]):
        self._stages = stages

    def run(self, initial_context: AcquisitionContext) -> Iterator[AcquisitionContext]:
        """Run the pipeline, yielding each final context."""

        def run_stages(ctx: AcquisitionContext, stage_idx: int) -> Iterator[AcquisitionContext]:
            if stage_idx >= len(self._stages):
                yield ctx
                return

            stage = self._stages[stage_idx]
            for output in stage.process(ctx):
                yield from run_stages(output, stage_idx + 1)

        yield from run_stages(initial_context, 0)
```

### Using the Pipeline

```python
# Build standard acquisition pipeline
pipeline = Pipeline([
    PositionStage(stage, coord_gen),
    AutofocusStage(autofocus),
    ZStackStage(stage),
    ChannelStage(live_controller),
    CaptureStage(camera),
    SaveStage(saver),
])

# Run it
initial_ctx = AcquisitionContext(config=config)
for ctx in pipeline.run(initial_ctx):
    event_bus.publish(ImageCaptured(frame=ctx.frame, info=ctx.metadata))

# Custom pipeline with focus tracking
pipeline_with_tracking = Pipeline([
    PositionStage(stage, coord_gen),
    AutofocusStage(autofocus),
    ZStackStage(stage),
    FocusTrackingStage(laser_af),  # New! Just add to list
    ChannelStage(live_controller),
    CaptureStage(camera),
    SaveStage(saver),
])
```

### Benefits

1. **Extensible**: Add new stages without modifying existing code
2. **Composable**: Build different pipelines for different acquisition types
3. **Testable**: Test each stage in isolation
4. **Debuggable**: Log at stage boundaries
5. **Reorderable**: Change order by rearranging list

---

## 10. Implementation Roadmap

### Phase 1: Foundation (Minimal Disruption)

**Goal**: Add extension points without breaking existing code.

1. **Add Registry Infrastructure** (~200 lines)
   - Create `squid/registry.py`
   - Register existing cameras, stages

2. **Add Event Bus** (~150 lines)
   - Create `squid/events.py`
   - Add Qt bridge

3. **Create Configuration Objects** (~300 lines)
   - Pydantic models for acquisition config
   - Validation at creation time

**Impact**: No changes to existing code paths.

### Phase 2: Incremental Refactoring

**Goal**: Migrate existing code to use new patterns.

1. **Extract Autofocus Algorithms**
   - Create `AutofocusAlgorithm` protocol
   - Move existing algorithm to implementation
   - Register with `autofocus_registry`

2. **Extract Coordinate Generators**
   - Create `CoordinateGenerator` protocol
   - Move grid generation to implementation
   - Add spiral, radial patterns as plugins

3. **Convert Callbacks to Events**
   - Replace `MultiPointControllerFunctions` with event bus
   - Maintain backwards compatibility wrapper

### Phase 3: Architectural Improvements

**Goal**: Decompose god objects.

1. **Split MultiPointWorker**
   - Extract `PositionExecutor`
   - Extract `ChannelExecutor`
   - Extract `ZStackExecutor`

2. **Split widgets.py**
   - One file per widget
   - Consistent naming: `widgets/autofocus_widget.py`

3. **Replace _def.py**
   - Create typed configuration classes
   - Migrate usage file by file

### Phase 4: Pipeline Architecture

**Goal**: Enable truly extensible acquisition.

1. **Implement Pipeline Stages**
   - Core stages for standard acquisition
   - Stage registry for extensions

2. **Convert Existing Acquisition**
   - Wrap existing code as "legacy" stage
   - Gradually decompose

3. **Document Extension Points**
   - How to add new hardware
   - How to add new acquisition patterns
   - How to add new processing steps

---

## Summary

Making Squid more extensible requires shifting from **implicit** to **explicit** architecture:

| From | To |
|------|-----|
| Hidden globals (`_def.py`) | Explicit dependency injection |
| Scattered configuration | Immutable config objects |
| Modifying existing code | Plugin registries |
| Fixed callback structures | Event bus |
| Inheritance hierarchies | Composition and protocols |
| God objects | Small, focused classes |
| Interleaved logic | Pipeline stages |

The key insight is that **extensibility comes from architecture, not code cleanliness**. Clean code in a rigid architecture is still hard to extend. Messy code in a flexible architecture can still be extended (and cleaned up incrementally).

Start with the foundation (registries, events, config objects) and incrementally refactor. Each step provides value while moving toward a more extensible system.
