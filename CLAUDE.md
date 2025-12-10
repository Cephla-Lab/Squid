# Squid Microscope Control Software

## What

Python microscopy control system for Cephla Squid microscopes. PyQt5 GUI with napari/pyqtgraph visualization.

**Structure:**
```
software/
├── squid/                # Core abstractions & services
│   ├── abc.py            # Hardware ABCs (AbstractCamera, AbstractStage, LightSource)
│   ├── events.py         # EventBus + typed events
│   ├── application.py    # ApplicationContext DI container
│   ├── services/         # CameraService, StageService, PeripheralService, etc.
│   ├── controllers/      # MicroscopeModeController, PeripheralsController
│   ├── config/           # Pydantic config models
│   └── utils/            # ThreadSafeValue, WorkerManager, safe_callback
├── control/              # Hardware implementations & GUI
│   ├── peripherals/      # Drivers: cameras/, stage/, lighting/, filter_wheel/
│   ├── core/             # Controllers: acquisition/, autofocus/, display/, navigation/
│   ├── widgets/          # GUI: camera/, display/, stage/, hardware/, wellplate/
│   ├── gui_hcs.py        # Main window
│   ├── microscope.py     # Microscope orchestrator
│   ├── microcontroller.py # Teensy serial protocol
│   └── _def.py           # Config loaded from .ini files
├── configurations/       # Hardware config files per microscope (.ini)
└── tests/                # unit/, integration/, manual/
```

## Architecture

**Widgets** (`control/widgets/`) - Pure UI layer with no business logic. Widgets subscribe to state events from the EventBus to update their display, and publish command events when the user interacts. This decoupling means widgets don't need references to hardware or services—they just react to events. Organized by domain: `camera/`, `display/`, `stage/`, `hardware/`, `wellplate/`, `acquisition/`.

**EventBus** (`squid/events.py`) - The control plane for decoupled communication. Typed dataclass events fall into two categories: Commands (user intent like `SetExposureTimeCommand`, `MoveStageCommand`) flow down from widgets, and State events (`ExposureTimeChanged`, `StagePositionChanged`) flow up from services/controllers to update widgets. Thread-safe with internal locking.

**StreamHandler** (`control/core/display/`) - The data plane for high-frequency camera frames (60fps). Separate from EventBus because frames would overwhelm the event system. Receives frames from camera callbacks, throttles for display, routes to viewers via Qt signals. `QtStreamHandler` bridges to GUI thread safely.

**Controllers** (`control/core/`, `squid/controllers/`) - Own state and orchestrate multi-step workflows by coordinating services. Subscribe to command events and publish state events. Key controllers:
- `LiveController`: Manages live camera streaming, triggering modes, illumination coordination
- `MultiPointController`: Orchestrates multi-position acquisitions (moves stage, captures images, saves)
- `AutoFocusController`/`LaserAFController`: Software and hardware autofocus algorithms
- `MicroscopeModeController`: Switches between imaging configurations (exposure, illumination, filters)
- `TrackingController`: Object tracking during acquisition
- `PeripheralsController`: Objective changer, spinning disk, piezo control

**Services** (`squid/services/`) - Thread-safe wrappers around hardware with `threading.RLock()`. Validate inputs, clamp to hardware limits, and publish state events after changes. Controllers call services directly; services call hardware ABCs directly. Key services:
- `CameraService`: Exposure, gain, ROI, binning, streaming, triggering
- `StageService`: XYZ movement, homing, position queries
- `IlluminationService`: Light source intensity and shutters across multiple sources
- `PeripheralService`: DAC outputs, digital I/O via microcontroller
- `FilterWheelService`: Filter wheel position control

**Hardware ABCs** (`squid/abc.py`) - Abstract base classes defining contracts that all hardware implementations must follow. `AbstractCamera` (~40 methods), `AbstractStage`, `AbstractFilterWheelController`, `LightSource`. Also defines key dataclasses: `Pos` (position), `CameraFrame` (frame data). Check here first to understand what any hardware can do.

**Drivers** (`control/peripherals/`) - Vendor-specific implementations of the ABCs. `cameras/` has 8+ implementations (FLIR, Toupcam, Hamamatsu, etc.), `stage/` has Cephla/Prior/Simulated, `lighting/` has LED arrays and laser sources, `filter_wheel/` has various controllers. Each talks to vendor SDKs. `SimulatedCamera` and `SimulatedStage` enable full testing without hardware.

**Control vs Data Plane Rule:** Commands and state changes go through EventBus. Camera frames go through StreamHandler. Never mix them—this separation prevents frame floods from blocking UI updates.

**Threading Model:**
- **GUI Thread:** Qt event loop. Never block or UI freezes. Use Qt signals for cross-thread updates.
- **EventBus Thread:** Processes event queue. Handlers must return quickly; spawn worker threads for long operations.
- **Camera Thread:** SDK callbacks deliver frames. Must be fast—just hand off to StreamHandler.
- **Worker Threads:** Long operations (acquisition loops, autofocus sweeps, stage moves). Always use services, which have internal locks for thread safety.

## Why

Modular microscopy platform supporting: slide scanning, live cell imaging, high content screening, spatial omics. Abstracts 8+ camera vendors, multiple stage/filter wheel types. Full simulation mode for offline development.

## How

**Run:**
```bash
cd software
python main_hcs.py --simulation  # No hardware needed
python main_hcs.py               # Real hardware
```

**Test:**
```bash
cd software
pytest tests/unit -v             # Fast unit tests
pytest tests/integration -v      # Simulated hardware tests
pytest -m "not slow" tests/      # Skip slow tests
```

**Key patterns:**
- Hardware ABCs in `squid/abc.py` - check these first for interfaces
- Services wrap hardware with business logic + events
- Widgets in `control/widgets/` organized by domain
- Config via `configurations/*.ini` files

**Current branch:** `arch_v2` - service layer modernization in progress

## Code Style

Prefer modern, Pythonic approaches over extensive OOP:
- **Dataclasses** for data containers and events, not classes with boilerplate
- **Composition over inheritance** - services/controllers take dependencies via constructor, minimal class hierarchies
- **Protocols/ABCs** for interfaces (`squid/abc.py`), not deep inheritance trees
- **Type hints** throughout, especially public APIs
- **Simple functions** where a class isn't needed

Conventions:
- **Formatting:** Black, 120 char line length
- **Events:** Frozen dataclasses inheriting from `Event`
- **Thread safety:** `threading.RLock()` in services; acquire lock, do work, release, then publish events outside lock
- **Logging:** `_log = squid.logging.get_logger(__name__)`
- **Private:** Prefix with `_`
- **No direct hardware from widgets:** Always go through services or publish commands
