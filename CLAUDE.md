# Squid Microscope Control Software

## What

Python microscopy control system for Cephla Squid microscopes. PyQt5 GUI with napari/pyqtgraph visualization.

**Structure (3-layer architecture):**
```
software/src/
├── squid/
│   ├── core/              # Layer 0: Foundation
│   │   ├── abc.py             # Hardware ABCs (AbstractCamera, AbstractStage, LightSource)
│   │   ├── events.py          # EventBus + typed events
│   │   ├── config/            # Pydantic config models
│   │   ├── utils/             # ThreadSafeValue, safe_callback, hardware_utils
│   │   ├── mode_gate.py       # Global mode management
│   │   └── state_machine.py   # State machine utilities
│   │
│   ├── backend/           # Layer 1: Hardware + orchestration
│   │   ├── microscope.py      # Hardware orchestrator
│   │   ├── microcontroller.py # Teensy serial protocol
│   │   ├── drivers/           # Vendor implementations (cameras/, stages/, lighting/, etc.)
│   │   ├── services/          # Thread-safe wrappers (CameraService, StageService, etc.)
│   │   ├── controllers/       # Workflow orchestration (LiveController, AutoFocus, MultiPoint)
│   │   ├── managers/          # Stateful managers (ObjectiveStore, ChannelConfig, ScanCoordinates)
│   │   ├── processing/        # Algorithms (image processing, tracking)
│   │   └── io/                # Data I/O (StreamHandler, writers)
│   │
│   ├── ui/                # Layer 2: Frontend
│   │   ├── main_window.py     # Main PyQt5 window
│   │   ├── widgets/           # Pure UI by domain (camera/, display/, stage/, hardware/)
│   │   ├── ui_event_bus.py    # Thread-safe UI event wrapper
│   │   └── qt_stream_handler.py # Qt frame handling
│   │
│   └── application.py     # DI container
│
├── configurations/        # Hardware config files per microscope (.ini)
└── tests/                 # unit/, integration/, manual/
```

## Architecture

**3-Layer Design:**
```
     ui (Layer 2)
        │
        ▼ (events only)
    backend (Layer 1)
        │
        ▼ (implements ABCs)
     core (Layer 0)
```

**Core** (`squid/core/`) - Foundation layer with no dependencies on other squid modules. Contains hardware ABCs, EventBus, config models, utilities.

**Backend** (`squid/backend/`) - All hardware interaction and orchestration:
- `drivers/` - Vendor-specific hardware implementations (8+ camera vendors, multiple stages/filters)
- `services/` - Thread-safe wrappers around drivers with `threading.RLock()`
- `controllers/` - State machines for workflows (live view, acquisition, autofocus)
- `managers/` - Stateful managers (ObjectiveStore, ChannelConfigurationManager, ScanCoordinates)
- `processing/` - Image processing and tracking algorithms
- `io/` - Frame streaming and file writers

**UI** (`squid/ui/`) - Pure PyQt5 widgets with no business logic. Widgets communicate exclusively via EventBus events - publishing Commands when users interact and subscribing to State events for display updates.

**Where to Put New Code:**
| I want to... | Put it in... |
|--------------|--------------|
| Add a new camera driver | `backend/drivers/cameras/` |
| Add a new service | `backend/services/` |
| Add workflow logic | `backend/controllers/` |
| Add stateful config | `backend/managers/` |
| Add image/tracking algorithm | `backend/processing/` |
| Add file I/O | `backend/io/` |
| Add UI widget | `ui/widgets/<domain>/` |
| Add shared ABC | `core/abc.py` |
| Add new event type | `core/events.py` |

**Key Components:**

**EventBus** (`core/events.py`) - Control plane for decoupled communication. Commands flow from UI to backend, State events flow from backend to UI.

**StreamHandler** (`backend/io/stream_handler.py`) - Data plane for 60fps camera frames. Separate from EventBus to prevent frame floods.

**Controllers** (`backend/controllers/`):
- `LiveController` - Camera streaming, triggering, illumination
- `MultiPointController` - Multi-position acquisitions
- `AutoFocusController`/`LaserAutoFocusController` - Focus algorithms
- `MicroscopeModeController` - Channel/mode switching
- `TrackingController` - Object tracking

**Services** (`backend/services/`): Thread-safe wrappers - CameraService, StageService, IlluminationService, FilterWheelService, etc.

**Managers** (`backend/managers/`): Stateful configuration - ObjectiveStore, ChannelConfigurationManager, ScanCoordinates, FocusMap

**Threading Model:**
- **GUI Thread:** Qt event loop. Never block.
- **EventBus Thread:** Processes event queue. Handlers must return quickly.
- **Camera Thread:** SDK callbacks. Hand off to StreamHandler immediately.
- **Worker Threads:** Long operations via services with internal locks.

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
- Hardware ABCs in `core/abc.py` - check these first for interfaces
- Services wrap hardware with business logic + events
- Widgets in `ui/widgets/` organized by domain
- Config via `configurations/*.ini` files

**Current branch:** `arch_v2` - service layer modernization

## Code Style

Prefer modern, Pythonic approaches over extensive OOP:
- **Dataclasses** for data containers and events
- **Composition over inheritance** - dependencies via constructor
- **Protocols/ABCs** for interfaces (`core/abc.py`)
- **Type hints** throughout, especially public APIs
- **Simple functions** where a class isn't needed

Conventions:
- **Formatting:** Black, 120 char line length
- **Events:** Frozen dataclasses inheriting from `Event`
- **Thread safety:** `threading.RLock()` in services; acquire lock, do work, release, then publish events outside lock
- **Logging:** `_log = squid.logging.get_logger(__name__)`
- **Private:** Prefix with `_`
- **No direct hardware from widgets:** Always go through services or publish commands
