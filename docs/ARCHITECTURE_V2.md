# Squid Microscopy Software Architecture (V2)

This document describes the current architecture of the Squid microscopy software after the v2 reorganization. It reflects the actual implementation as of the `arch_v2` branch.

## Table of Contents

1. [Overview](#1-overview)
2. [Project Structure](#2-project-structure)
3. [Package Organization](#3-package-organization)
4. [Hardware Abstraction Layer](#4-hardware-abstraction-layer)
5. [Service Layer](#5-service-layer)
6. [Event System](#6-event-system)
7. [Application Context](#7-application-context)
8. [Configuration System](#8-configuration-system)
9. [Test Infrastructure](#9-test-infrastructure)
10. [Data Flow](#10-data-flow)
11. [Threading Model](#11-threading-model)
12. [Key Design Patterns](#12-key-design-patterns)

---

## 1. Overview

The Squid microscopy software uses a layered architecture:

```
┌─────────────────────────────────────────────────────────┐
│                    GUI Layer                             │
│              (control/gui_hcs.py, widgets/)              │
├─────────────────────────────────────────────────────────┤
│                  Service Layer                           │
│     (squid/services/ - CameraService, StageService)      │
├─────────────────────────────────────────────────────────┤
│                 Controller Layer                         │
│    (control/core/ - LiveController, MultiPointController)│
├─────────────────────────────────────────────────────────┤
│              Hardware Abstraction Layer                  │
│        (squid/abc.py - AbstractCamera, AbstractStage)    │
├─────────────────────────────────────────────────────────┤
│              Hardware Implementations                    │
│   (control/peripherals/ - cameras/, stage/, lighting/)   │
├─────────────────────────────────────────────────────────┤
│              Low-Level Drivers                           │
│     (control/microcontroller.py, vendor SDKs)            │
└─────────────────────────────────────────────────────────┘
```

**Key Principles:**
- Abstract base classes (ABCs) define hardware contracts
- Factory functions enable runtime polymorphism
- Services provide business logic and event publishing
- Event bus enables decoupled communication
- Simulation support at every layer

---

## 2. Project Structure

```
/Squid
├── firmware/                    # Teensy microcontroller firmware
├── software/
│   ├── squid/                   # Core abstractions and services
│   │   ├── abc.py               # Abstract base classes
│   │   ├── application.py       # ApplicationContext (DI container)
│   │   ├── events.py            # Event bus and event definitions
│   │   ├── registry.py          # Plugin registry system
│   │   ├── logging.py           # Logging utilities
│   │   ├── config/              # Configuration models
│   │   ├── services/            # Service layer
│   │   └── utils/               # Utilities
│   │
│   ├── control/                 # Hardware control and GUI
│   │   ├── microscope.py        # Microscope orchestrator
│   │   ├── microcontroller.py   # Microcontroller serial protocol
│   │   ├── _def.py              # Machine configuration (from .ini)
│   │   ├── peripherals/         # Hardware implementations
│   │   │   ├── cameras/         # Camera drivers
│   │   │   ├── stage/           # Stage implementations
│   │   │   ├── lighting/        # Light source drivers
│   │   │   └── filter_wheel/    # Filter wheel controllers
│   │   ├── core/                # High-level controllers
│   │   ├── gui_hcs.py           # Main GUI window
│   │   └── widgets/             # GUI widgets
│   │
│   ├── tests/                   # Test suite
│   │   ├── unit/                # Unit tests
│   │   ├── integration/         # Integration tests
│   │   ├── manual/              # Manual verification tests
│   │   └── conftest.py          # Shared fixtures
│   │
│   └── configurations/          # Hardware configuration files (.ini)
│
└── docs/                        # Documentation
```

---

## 3. Package Organization

### 3.1 squid Package

The `squid/` package contains core abstractions independent of specific hardware:

| Module | Purpose |
|--------|---------|
| `abc.py` | Abstract base classes (AbstractCamera, AbstractStage, etc.) |
| `application.py` | ApplicationContext - dependency injection container |
| `events.py` | EventBus and event type definitions |
| `registry.py` | Generic registry for extensible hardware support |
| `logging.py` | Structured logging utilities |
| `config/` | Pydantic configuration models |
| `services/` | Service layer (CameraService, StageService, etc.) |
| `utils/` | Utility modules (image processing, worker management) |

### 3.2 control Package

The `control/` package contains hardware implementations and application logic:

| Module | Purpose |
|--------|---------|
| `microscope.py` | Microscope class - composes all hardware |
| `microcontroller.py` | Teensy serial communication |
| `_def.py` | Machine-specific configuration (loaded from .ini) |
| `peripherals/` | Hardware driver implementations |
| `core/` | High-level controllers (LiveController, etc.) |
| `gui_hcs.py` | Main GUI window |
| `widgets/` | GUI widget implementations |
| `processing/` | Image processing utilities |

---

## 4. Hardware Abstraction Layer

### 4.1 Abstract Base Classes

Defined in `squid/abc.py`:

```python
# Camera interface
class AbstractCamera(ABC):
    def start_streaming(self) -> None: ...
    def stop_streaming(self) -> None: ...
    def send_trigger(self) -> None: ...
    def read_frame(self) -> Optional[CameraFrame]: ...
    def set_exposure_time(self, ms: float) -> None: ...
    def set_analog_gain(self, gain: float) -> None: ...
    # ... additional methods for ROI, binning, pixel format

# Stage interface
class AbstractStage(ABC):
    def move_x(self, rel_mm: float, blocking: bool = True) -> None: ...
    def move_x_to(self, abs_mm: float, blocking: bool = True) -> None: ...
    def get_pos(self) -> Pos: ...
    def home(self, x=False, y=False, z=False, theta=False) -> None: ...
    # ... additional methods for all axes

# Filter wheel interface
class AbstractFilterWheelController(ABC):
    def set_filter_wheel_position(self, positions: Dict[int, int]) -> None: ...
    def home(self) -> None: ...
    # ...

# Light source interface
class LightSource(ABC):
    def set_intensity(self, channel: int, intensity: float) -> None: ...
    def set_shutter(self, channel: int, on: bool) -> None: ...
    # ...
```

### 4.2 Key Data Classes

```python
@dataclass
class Pos:
    x_mm: float
    y_mm: float
    z_mm: float
    theta_rad: Optional[float]

@dataclass
class CameraFrame:
    data: np.ndarray
    frame_id: int
    timestamp: float
    pixel_format: PixelFormat

@dataclass
class StageState:
    busy: bool
```

### 4.3 Hardware Implementations

#### Cameras (`control/peripherals/cameras/`)

| Implementation | File | Description |
|----------------|------|-------------|
| SimulatedCamera | `camera_utils.py` | Full simulation for testing |
| DefaultCamera | `base.py` | Daheng Galaxy cameras (gxipy) |
| ToupcamCamera | `toupcam.py` | Toupcam USB cameras |
| HamamatsuCamera | `hamamatsu.py` | Hamamatsu DCAM cameras |
| FlirCamera | `flir.py` | FLIR/Point Grey cameras |
| AndorCamera | `andor.py` | Andor cameras |
| PhotometricsCamera | `photometrics.py` | Photometrics cameras |
| TucsenCamera | `tucsen.py` | Tucsen cameras |
| IDSCamera | `ids.py` | IDS cameras |
| TISCamera | `tis.py` | The Imaging Source cameras |

#### Stages (`control/peripherals/stage/`)

| Implementation | File | Description |
|----------------|------|-------------|
| SimulatedStage | `simulated.py` | Full simulation for testing |
| CephlaStage | `cephla.py` | Cephla-designed stage via microcontroller |
| PriorStage | `prior.py` | Prior Scientific stages |

#### Filter Wheels (`control/peripherals/filter_wheel/`)

| Implementation | File | Description |
|----------------|------|-------------|
| SimulatedFilterWheelController | `utils.py` | Full simulation |
| SquidFilterWheel | `cephla.py` | Cephla via microcontroller |
| ZaberFilterController | `zaber.py` | Zaber filter wheels |
| Optospin | `optospin.py` | OptoSpin filter wheels |

#### Lighting (`control/peripherals/lighting/`)

| Implementation | File | Description |
|----------------|------|-------------|
| IlluminationController | `led.py` | Coordinates all light sources |
| XLight | `xlight.py` | CrestOptics spinning disk |
| Dragonfly | `dragonfly.py` | Andor spinning disk |
| LDI | `ldi.py` | 89 North LDI laser system |
| CELESTA | `celesta.py` | Lumencor CELESTA |
| CellX | `cellx.py` | CellX LED system |
| SciMicroscopyLEDArray | `sci_led_array.py` | SCI LED array |

### 4.4 Factory Functions

Factory functions enable runtime selection of implementations:

```python
# Camera factory
from control.peripherals.cameras.camera_utils import get_camera
camera = get_camera(config, simulated=True)

# Stage factory
from control.peripherals.stage.stage_utils import get_stage
stage = get_stage(stage_config, microcontroller=micro, simulated=True)

# Filter wheel factory
from control.peripherals.filter_wheel.utils import get_filter_wheel_controller
fw = get_filter_wheel_controller(config, microcontroller=micro, simulated=True)
```

---

## 5. Service Layer

The service layer (`squid/services/`) provides business logic on top of hardware abstractions. It acts as the primary interface between GUI widgets and hardware.

### 5.1 Design Philosophy

**Direct Method Calls (Not Command Events)**

The service layer uses direct method calls rather than command events:

```
# What we do (simple, debuggable):
Widget → service.set_exposure_time(100) → Hardware → publish(ExposureTimeChanged)

# NOT this (too much indirection):
Widget → publish(SetExposureTimeCommand) → Service → Hardware → publish(ExposureTimeChanged)
```

**Rationale:**
- Simpler to understand and debug
- Already working pattern in codebase
- Command events add indirection without clear benefit for this use case
- State events (service→GUI) continue working for synchronization

### 5.2 Service Architecture

```python
class BaseService(ABC):
    """Base class for all services."""
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._log = squid.logging.get_logger(self.__class__.__name__)

    def publish(self, event: Event) -> None:
        """Publish event to the bus."""
        self._event_bus.publish(event)

    def subscribe(self, event_type: Type[E], handler: Callable) -> None:
        """Subscribe to events (kept for future scripting API)."""
        self._event_bus.subscribe(event_type, handler)

    def shutdown(self) -> None: ...

class ServiceRegistry:
    """Central registry for all services."""
    def register(self, name: str, service: BaseService) -> None: ...
    def get(self, name: str) -> Optional[BaseService]: ...
    def shutdown(self) -> None: ...
```

### 5.3 Available Services

| Service | Purpose | Key Methods |
|---------|---------|-------------|
| `CameraService` | Camera operations with validation and events | `set_exposure_time`, `set_analog_gain`, `set_region_of_interest`, `set_binning`, `set_pixel_format`, `set_temperature`, `set_white_balance_gains`, `set_black_level` |
| `StageService` | Stage movement with position events | `move_x`, `move_y`, `move_z`, `move_to`, `move_theta`, `move_theta_to`, `home`, `get_position`, `get_config` |
| `PeripheralService` | DAC and digital I/O operations | `set_dac`, `set_pin` |

### 5.4 Service Methods Pattern

Each service method follows this pattern:

```python
def set_exposure_time(self, ms: float) -> None:
    """Set camera exposure time with validation and event publishing."""
    # 1. Validate/clamp input
    limits = self._camera.get_exposure_limits()
    clamped = max(limits[0], min(ms, limits[1]))

    # 2. Log the operation
    self._log.debug(f"Setting exposure time: {clamped}ms")

    # 3. Call hardware
    self._camera.set_exposure_time(clamped)

    # 4. Publish state event (for GUI synchronization)
    self.publish(ExposureTimeChanged(exposure_time_ms=clamped))
```

### 5.5 Using Services in Widgets

Widgets receive services via constructor injection with backward compatibility:

```python
class CameraSettingsWidget(QFrame):
    def __init__(
        self,
        camera: AbstractCamera = None,  # Legacy - keep for backward compat
        camera_service: Optional["CameraService"] = None,
        ...
    ):
        # Use service if provided, otherwise create from legacy param
        if camera_service is not None:
            self._service = camera_service
            self.camera = camera  # Keep for read-only getters
        elif camera is not None:
            from squid.services import CameraService
            self._service = CameraService(camera, event_bus)
            self.camera = camera

        # Subscribe to state events for synchronization
        event_bus.subscribe(ExposureTimeChanged, self._on_exposure_changed)

    def _on_exposure_changed(self, event: ExposureTimeChanged):
        """Handle exposure time changed event."""
        self.entry_exposureTime.blockSignals(True)
        self.entry_exposureTime.setValue(event.exposure_time_ms)
        self.entry_exposureTime.blockSignals(False)
```

### 5.6 Adding New Service Methods

To add a new method to a service:

1. **Add test first** (`tests/unit/squid/services/test_*_service.py`):
```python
def test_set_new_feature(self):
    mock_camera = Mock()
    bus = EventBus()
    service = CameraService(mock_camera, bus)

    service.set_new_feature(value)

    mock_camera.set_new_feature.assert_called_once_with(value)
```

2. **Add event if needed** (`squid/events.py`):
```python
@dataclass
class NewFeatureChanged(Event):
    value: float
```

3. **Implement method** (`squid/services/camera_service.py`):
```python
def set_new_feature(self, value: float):
    """Set new feature."""
    self._log.debug(f"Setting new feature: {value}")
    self._camera.set_new_feature(value)
    self.publish(NewFeatureChanged(value=value))
```

4. **Update widget** to use `self._service.set_new_feature(value)` instead of direct call.

---

## 6. Event System

The event bus (`squid/events.py`) enables decoupled communication between components.

### 6.1 EventBus

```python
class EventBus:
    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None: ...
    def unsubscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None: ...
    def publish(self, event: Event) -> None: ...
    def clear(self) -> None: ...
```

### 6.2 Event Categories

**Command Events (for future scripting API):**

These are defined but not currently used (services use direct method calls instead):
- `SetExposureTimeCommand`
- `SetAnalogGainCommand`
- `MoveStageCommand`
- `MoveStageToCommand`
- `HomeStageCommand`
- `SetDACCommand`
- `StartLiveCommand`
- `StopLiveCommand`

**State Events (Service → GUI):**

These are published by services to notify GUI of state changes:
- `ExposureTimeChanged` - Camera exposure time changed
- `AnalogGainChanged` - Camera analog gain changed
- `ROIChanged` - Camera region of interest changed
- `BinningChanged` - Camera binning changed
- `PixelFormatChanged` - Camera pixel format changed
- `StagePositionChanged` - Stage position changed
- `LiveStateChanged` - Live view started/stopped
- `DACValueChanged` - DAC output value changed

**Acquisition Events:**
- `AcquisitionStarted`
- `AcquisitionFinished`
- `ImageCaptured`

### 6.3 Usage Example

```python
from squid.events import event_bus, StagePositionChanged

# Subscribe to events
def on_stage_moved(event: StagePositionChanged):
    print(f"Stage at: {event.x_mm}, {event.y_mm}, {event.z_mm}")

event_bus.subscribe(StagePositionChanged, on_stage_moved)

# Publish events (typically from services)
event_bus.publish(StagePositionChanged(x_mm=10.0, y_mm=20.0, z_mm=5.0))
```

---

## 7. Application Context

The `ApplicationContext` (`squid/application.py`) serves as a dependency injection container.

### 7.1 Structure

```python
class ApplicationContext:
    def __init__(self, simulation: bool = False):
        self._build_microscope()   # Hardware layer
        self._build_controllers()  # Controller layer
        self._build_services()     # Service layer

    @property
    def microscope(self) -> Microscope: ...

    @property
    def controllers(self) -> Controllers: ...

    @property
    def services(self) -> ServiceRegistry: ...

    def create_gui(self) -> HighContentScreeningGui: ...

    def shutdown(self) -> None: ...
```

### 7.2 Controllers Container

```python
@dataclass
class Controllers:
    live: LiveController
    stream_handler: StreamHandler
    multipoint: Optional[MultiPointController] = None
    channel_config_manager: Optional[ChannelConfigurationManager] = None
    objective_store: Optional[ObjectiveStore] = None
```

### 7.3 Usage

```python
# Create application
context = ApplicationContext(simulation=True)

# Access components
camera = context.microscope.camera
stage_service = context.services.get('stage')

# Create and show GUI
gui = context.create_gui()
gui.show()

# Shutdown
context.shutdown()
```

---

## 8. Configuration System

### 8.1 Configuration Sources

1. **INI files** (`configurations/*.ini`) - Machine-specific settings
2. **`control/_def.py`** - Global configuration loaded at import time
3. **`squid/config/`** - Pydantic models for type-safe configuration

### 8.2 Configuration Models

```python
# squid/config/
class CameraConfig(BaseModel):
    camera_type: CameraType
    default_pixel_format: PixelFormat
    default_binning: int
    # ...

class StageConfig(BaseModel):
    X_AXIS: AxisConfig
    Y_AXIS: AxisConfig
    Z_AXIS: AxisConfig
    # ...

class AxisConfig(BaseModel):
    MOVEMENT_SIGN: int
    USE_ENCODER: bool
    ENCODER_SIGN: int
    MM_PER_USTEP: float
    # ...
```

### 8.3 Configuration Flow

```
configurations/*.ini
       ↓
control/_def.py (loaded at import)
       ↓
squid.config.get_*_config() functions
       ↓
Pydantic models (CameraConfig, StageConfig, etc.)
       ↓
Factory functions (get_camera, get_stage, etc.)
```

---

## 9. Test Infrastructure

### 9.1 Directory Structure

```
tests/
├── conftest.py              # Shared fixtures
├── unit/                    # Unit tests (no hardware simulation)
│   ├── squid/
│   │   ├── config/
│   │   ├── services/
│   │   └── utils/
│   └── control/
│       └── core/
├── integration/             # Integration tests (simulated hardware)
│   ├── squid/
│   └── control/
└── manual/                  # Manual verification tests
```

### 9.2 Key Fixtures

```python
# tests/conftest.py

@pytest.fixture
def simulated_camera(camera_config):
    """Provide a SimulatedCamera instance."""
    camera = get_camera(camera_config, simulated=True)
    yield camera
    camera.close()

@pytest.fixture
def simulated_stage(stage_config):
    """Provide a SimulatedStage instance."""
    stage = SimulatedStage(stage_config, simulate_delays=False)
    yield stage

@pytest.fixture
def simulated_microscope():
    """Provide a fully simulated Microscope."""
    scope = Microscope.build_from_global_config(simulated=True)
    yield scope
    scope.close()

@pytest.fixture
def simulated_application_context():
    """Provide a simulated ApplicationContext."""
    context = ApplicationContext(simulation=True)
    yield context
    context.shutdown()
```

### 9.3 Test Markers

```python
@pytest.mark.unit          # Unit tests without hardware simulation
@pytest.mark.integration   # Integration tests using simulated hardware
@pytest.mark.slow          # Tests that take >5 seconds
@pytest.mark.qt            # Tests requiring Qt/PyQt5
@pytest.mark.manual        # Tests for manual/visual verification
```

### 9.4 Running Tests

```bash
# All unit tests
pytest tests/unit/ -m unit

# All integration tests
pytest tests/integration/ -m integration

# Everything offline
pytest tests/

# With coverage
pytest --cov=squid --cov=control tests/
```

---

## 10. Data Flow

### 10.1 Live View

```
User clicks "Start Live"
       ↓
GUI → StartLiveCommand event
       ↓
LiveController.start_live()
       ↓
Camera.start_streaming()
       ↓
Camera frame callback → StreamHandler
       ↓
StreamHandler throttles/scales → image_to_display signal
       ↓
GUI updates image viewer
```

### 10.2 Multi-Point Acquisition

```
User starts acquisition
       ↓
MultiPointController.acquire()
       ↓
Spawns MultiPointWorker thread
       ↓
For each position:
  1. Stage.move_to(x, y)
  2. Optional: AutoFocusController.autofocus()
  3. For each channel:
     - IlluminationController.set_channel()
     - Camera.send_trigger()
     - Frame callback → SaveImageJob queue
       ↓
JobRunner saves images (OME-TIFF)
       ↓
AcquisitionFinished event → GUI
```

### 10.3 Service Communication

```
GUI Widget
    ↓ (calls service method)
CameraService.set_exposure_time(50.0)
    ↓ (validates and applies)
AbstractCamera.set_exposure_time(50.0)
    ↓ (publishes event)
EventBus.publish(ExposureTimeChanged(50.0))
    ↓ (subscribers notified)
Other GUI widgets update displays
```

---

## 11. Threading Model

### 11.1 Thread Types

| Thread | Purpose |
|--------|---------|
| Main (GUI) | Qt event loop, GUI updates |
| Camera callback | Frame delivery from camera SDK |
| MultiPointWorker | Acquisition orchestration |
| Position polling | Optional stage position updates |

### 11.2 Synchronization

- `threading.Lock` - Protects shared state (e.g., stage busy flag)
- `threading.Event` - Signals between threads
- `queue.Queue` - Thread-safe job queues
- Qt signals/slots - Thread-safe GUI updates

### 11.3 Thread Safety Rules

1. Camera frame callbacks must be fast (offload to queues)
2. GUI updates only via Qt signals from background threads
3. EventBus is thread-safe (uses internal lock)
4. Stage busy state uses lock for thread-safe access

---

## 12. Key Design Patterns

### 12.1 Abstract Factory

Factory functions create appropriate implementations based on configuration:

```python
def get_camera(config: CameraConfig, simulated: bool = False) -> AbstractCamera:
    if simulated:
        return SimulatedCamera(config)
    if config.camera_type == CameraType.TOUPCAM:
        return ToupcamCamera(config)
    # ...
```

### 12.2 Registry Pattern

The registry enables extensible hardware support:

```python
camera_registry = Registry[AbstractCamera]("camera")

@camera_registry.register("custom")
class CustomCamera(AbstractCamera):
    ...

# Later:
camera = camera_registry.create("custom", config)
```

### 12.3 Composition

The Microscope class composes all hardware:

```python
class Microscope:
    stage: AbstractStage
    camera: AbstractCamera
    illumination_controller: IlluminationController
    addons: MicroscopeAddons
    low_level_drivers: LowLevelDrivers
```

### 12.4 Observer (Event Bus)

Decoupled communication via publish/subscribe:

```python
event_bus.subscribe(StagePositionChanged, self.on_stage_moved)
event_bus.publish(StagePositionChanged(x_mm=10.0, ...))
```

### 12.5 Strategy

Different acquisition modes as strategies:

```python
class AcquisitionMode(Enum):
    SOFTWARE_TRIGGER = auto()
    HARDWARE_TRIGGER = auto()
    CONTINUOUS = auto()
```

### 12.6 Dependency Injection

ApplicationContext manages the object graph:

```python
context = ApplicationContext(simulation=True)
# All dependencies wired automatically
gui = context.create_gui()
```

---

## Appendix: Class Hierarchy

```
AbstractCamera
├── SimulatedCamera
├── DefaultCamera (Daheng)
├── ToupcamCamera
├── HamamatsuCamera
├── FlirCamera
├── AndorCamera
├── PhotometricsCamera
├── TucsenCamera
├── IDSCamera
└── TISCamera

AbstractStage
├── SimulatedStage
├── CephlaStage
└── PriorStage

AbstractFilterWheelController
├── SimulatedFilterWheelController
├── SquidFilterWheel
├── ZaberFilterController
└── Optospin

LightSource
├── XLight
├── Dragonfly
├── LDI
├── CELESTA
├── CellX
└── SciMicroscopyLEDArray

BaseService
├── CameraService
├── StageService
└── PeripheralService
```
