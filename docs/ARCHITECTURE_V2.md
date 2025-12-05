# Squid Microscopy Software Architecture (V2)

This version documents the current architecture **and** the gaps between the intended design and what the code actually does. It is meant to be a realistic map for anyone refactoring for robustness, modularity, and easier feature additions.

## Table of Contents

1. [Scope and Intent](#1-scope-and-intent)
2. [Overall Project Structure](#2-overall-project-structure)
3. [Core Modules and Their Responsibilities](#3-core-modules-and-their-responsibilities)
4. [Entry Points](#4-entry-points)
5. [Key Classes and Abstractions](#5-key-classes-and-abstractions)
6. [Hardware Abstraction Layer](#6-hardware-abstraction-layer)
7. [GUI Architecture](#7-gui-architecture)
8. [Configuration System](#8-configuration-system)
9. [Data Flow - Image Acquisition](#9-data-flow---image-acquisition)
10. [Key Design Patterns](#10-key-design-patterns)
11. [External Dependencies](#11-external-dependencies)
12. [Threading and Concurrency](#12-threading-and-concurrency)
13. [Special Features](#13-special-features)
14. [Current Gaps vs Intended Architecture](#14-current-gaps-vs-intended-architecture)

---

## 1. Scope and Intent

The goal remains a modular microscopy stack with hardware abstractions (`squid/`) sitting under higher-level control (`control/`) and a Qt GUI. In practice, the codebase is mid-migration: global state and legacy drivers are still heavily used. This document calls out both the intended structure and the observed deviations.

---

## 2. Overall Project Structure

```
/Squid
├── firmware/              # Teensy microcontroller firmware
├── software/
│   ├── control/           # Main control logic and legacy drivers
│   ├── squid/             # Modern abstraction layer (currently imports control globals)
│   ├── configurations/    # Hardware configuration files (.ini)
│   ├── tools/             # Utility scripts
│   ├── tests/             # Unit and integration tests
│   ├── drivers and libraries/  # Manufacturer SDKs
│   └── fluidics_v2/       # Fluidics module (git submodule)
└── docs/                  # Documentation
```

**Primary Entry Point**: `/software/main_hcs.py` (High Content Screening GUI launcher)

---

## 3. Core Modules and Their Responsibilities

### A. squid Package (Hardware Abstraction Layer – partial)

Located in `/software/squid/` and intended to define abstract interfaces. Today it still imports `control._def` and other control utilities at import time, so it is not standalone.

| File | Purpose |
|------|---------|
| `abc.py` | Abstract base classes defining hardware contracts |
| `config.py` | Pydantic models wrapping values pulled from `control._def` |
| `camera/utils.py` | Factory for camera implementations (falls back to legacy drivers) |
| `stage/cephla.py` | Cephla-designed stage with microcontroller |
| `stage/prior.py` | Prior Scientific stage implementation |
| `filter_wheel_controller/` | Filter wheel implementations (cephla, zaber, optospin, dragonfly) |

**Key Abstractions in `abc.py`:**
- `AbstractFilterWheelController` — filter wheel interface
- `LightSource` — light source abstraction
- `AbstractStage` — stage interface
- `AbstractCamera` — camera interface with frame callbacks
- `CameraFrame` — dataclass for frames with metadata

### B. control Package (Legacy & Main Business Logic)

Located in `/software/control/` — primary application logic and most drivers.

**Core Hardware Drivers:**
- `camera_*.py` — camera-specific implementations (FLIR, Toupcam, Hamamatsu, IDS, Tucsen, Andor, Photometrics, TIS)
- `microcontroller.py` — Teensy serial protocol
- `serial_peripherals.py` — XLight, Dragonfly, Celesta, NL5, CellX
- `lighting.py` — illumination controller abstraction
- `piezo.py` — objective piezo stage

**High-Level Managers:**
- `core.py` — `QtStreamHandler`, `ImageSaver`, `TrackingController`, `AutoFocusController`
- `live_controller.py` — live view controller with streaming/triggering
- `multi_point_controller.py` — multi-point orchestrator
- `multi_point_worker.py` — acquisition worker thread
- `auto_focus_controller.py` — autofocus logic
- `laser_auto_focus_controller.py` — laser autofocus (displacement measurement)
- `job_processing.py` — job queue for parallel image saving
- `stream_handler.py` — frame streaming with FPS throttling

**Configuration Managers:**
- `channel_configuration_mananger.py` — channel (wavelength) configuration
- `configuration_mananger.py` — profile/objective configs
- `contrast_manager.py` — contrast adjustments
- `laser_af_settings_manager.py` — laser AF parameters
- `objective_store.py` — objective metadata
- `scan_coordinates.py` — scan coordinate management

**GUI Modules:**
- `gui_hcs.py` — main GUI window
- `widgets.py` — GUI widgets (camera settings, live control, recording, etc.)

**Utilities:**
- `_def.py` — global configuration constants (populated at import from `.ini`)
- `utils.py` — helper functions
- `utils_config.py` — channel mode/config structures
- `utils_channel.py` — channel utilities
- `utils_acquisition.py` — acquisition utilities

---

## 4. Entry Points

**Primary Entry**: `/software/main_hcs.py`

```
1. Parse CLI args (--simulation, --live-only, --verbose)
2. Setup logging via squid.logging
3. Load configuration from .ini into control._def (module-level globals)
4. Build Microscope from global config (no DI)
5. Create HighContentScreeningGui window
6. Show GUI and start Qt event loop
7. Optional: start terminal console for debugging
```

---

## 5. Key Classes and Abstractions

### Core Abstraction Hierarchy (intended)

```
AbstractCamera (abc.py)
├── ToupcamCamera (legacy)
├── FlirCamera (legacy)
├── HamamatsuCamera (legacy)
├── AndorCamera (legacy)
├── PhotometricsCamera (legacy)
├── TucsenCamera (legacy)
├── SimulatedCamera (AbstractCamera)
└── DefaultCamera (AbstractCamera)

AbstractStage (abc.py)
├── CephlaStage
└── PriorStage

AbstractFilterWheelController (abc.py)
├── SquidFilterWheelController
├── ZaberFilterWheelController
├── OptospinFilterWheelController
└── DragonflyFilterWheelController

LightSource (abc.py)
├── various implementations
```

### Key Data Classes

- `CameraFrame` — frame data, ID, timestamp, pixel format
- `Pos` — x_mm, y_mm, z_mm, theta_rad
- `StageState` — busy flag
- `CaptureInfo` — capture metadata

### Controller Classes

- `Microscope` — composes stage, camera, illumination, addons, low-level drivers
- `MicroscopeAddons` — optional hardware (filters, fluidics, piezo, etc.)
- `LowLevelDrivers` — microcontroller wrapper
- `LiveController` — live streaming/trigger orchestration
- `MultiPointController` — multi-point orchestrator
- `MultiPointWorker` — worker performing acquisition
- `AutoFocusController` — autofocus logic
- `LaserAutofocusController` — laser-based displacement

---

## 6. Hardware Abstraction Layer

- Factory pattern in `squid.camera.utils.get_camera()`; dynamically imports camera drivers.
- Stages: `CephlaStage` (microcontroller) and `PriorStage` (serial).
- Filter wheels: Squid, Zaber, Optospin, Dragonfly.
- Light sources: LDI (serial), CELESTA (Ethernet), Andor lasers (USB), LED arrays (PWM), spinning disk.
- Addons: objective piezo, autofocus camera, fluidics, LED array (SciMicroscopy).

**Reality check:** Many camera drivers are legacy and do not subclass `AbstractCamera`; the factory falls back to them. HAL modules import `control` globals, so the layer is not cleanly separated yet.

---

## 7. GUI Architecture

- Qt5/qtpy, pyqtgraph for visualization; optional Napari integration.
- Main window: `HighContentScreeningGui` (`control/gui_hcs.py`).
- Widgets: live control, autofocus, filters, camera settings, recording, navigation, wellplate scanning, fluidics, stats, laser AF, etc.
- Signal-based communication (PyQt signals) for thread-safe updates: movement, frame updates, acquisition events.

---

## 8. Configuration System

**Where config actually comes from**
- INI files under `/software/configurations/` loaded into `control/_def.py` at import time (module-level globals).
- `squid/config.py` wraps those globals into Pydantic models **once** at import; values are not re-validated per-run.
- `conf_attribute_reader()` (`control/_def.py`) coerces strings to Python types but bypasses strict validation.
- Cache files under `cache/` record last-used config path and sample/objective selections.

**Implications**
- Configuration is effectively a process-global singleton; multiple microscopes or per-session overrides aren’t supported without reload.
- Mutating `_def` during import means order of imports influences runtime state.
- Pydantic models provide structure but not enforcement of source data (values are already coerced and stored globally).

Key parameters (as in v1): camera type/format/binning/ROI, stage limits, illumination hardware, filter wheels, autofocus, multipoint defaults, fluidics, piezo.

---

## 9. Data Flow - Image Acquisition

### Live View

```
Camera.start_streaming()
  → Camera.add_frame_callback(StreamHandler.on_new_frame)
    → StreamHandler throttles + scales
    → image_to_display signal
      → GUI updates viewer
```

### Multi-Point Acquisition

```
GUI triggers MultiPointController.acquire_()
  → Spawns MultiPointWorker thread
    → For each region/FOV:
      1. Move stage
      2. Optional autofocus
      3. For each Z, for each channel:
         - Set illumination/filter
         - Trigger camera
         - Frame callback queues SaveImageJob
    → JobRunner saves images (optionally multiprocessing)
    → Progress + final signals to GUI
```

### Image Saving

`SaveImageJob` writes BMP/TIFF/OME-TIFF with metadata; multiprocessing supported but optional.

---

## 10. Key Design Patterns

- **ABC pattern** (intended) for cameras/stages/filter wheels/lights; incomplete in legacy drivers.
- **Factory**: `squid.camera.utils.get_camera()`, `squid.filter_wheel_controller.utils.get_filter_wheel_controller()`, `Microscope.build_from_global_config()` (uses globals, not DI).
- **Composition**: `Microscope` composes hardware and controllers.
- **Strategy**: camera acquisition modes (software, hardware, continuous).
- **Observer**: Qt signals/slots and camera callbacks.
- **Template Method**: `AbstractCamera._process_raw_frame()` override hook.
- **Job Queue**: `JobRunner` + `SaveImageJob` for persistence.
- **Configuration as Code** (aspirational): Pydantic models exist, but source data is global and pre-validated loosely.

---

## 11. External Dependencies

Scientific: numpy, scipy, opencv-cv2, imageio, tifffile, pillow  
Hardware: pyserial; vendor SDKs (pyspin, dcam, toupcam, tucsen, ids-peak, gxipy, Andor/Photometrics)  
GUI: PyQt5/qtpy, pyqtgraph, napari, matplotlib  
Data formats: pandas, json, yaml, tifffile (OME-TIFF)  
Interop: optional pyimagej/scyjava

---

## 12. Threading and Concurrency

Threads: GUI (Qt), camera callback threads, MultiPointWorker thread, optional position polling, timers in `LiveController`.  
Processes: optional multiprocessing pool for image saving.  
Sync: `threading.Event`, `threading.Lock`, `queue.Queue`, `multiprocessing.Queue`.  
Hot path: camera callbacks must be fast; `StreamHandler` throttles and emits display/save events.

---

## 13. Special Features

- Autofocus: reflection-based, laser-based (separate camera), manual focus maps.
- Multi-point: well arrays, per-region autofocus, Z-stacks with direction control, time-lapse (XYZT), fluidics integration.
- Image processing: FPS throttling, resolution scaling, OME-TIFF metadata, parallel saving.
- Stage control: encoder feedback, backlash handling, PID (where enabled), homing and safety limits.

---

## 14. Current Gaps vs Intended Architecture

These are the main discrepancies that impact robustness, modularity, and ease of extension:

- **Global configuration singleton**: `software/control/_def.py` populates module-level variables at import (from `.ini` via `ConfigParser` and `conf_attribute_reader`). It writes cache files and never validates with Pydantic. `squid/config.py` snapshots those globals once. Runtime reconfiguration, per-instance configs, and strict typing are not in place.
- **HAL not isolated**: `squid/config.py` and `squid/abc.py` import `control` modules, so the “modern” layer depends on legacy globals. `Microscope.build_from_global_config` constructs hardware directly from `_def` flags rather than injected config objects.
- **Incomplete AbstractCamera adoption**: Several drivers in `software/control/camera_*.py` do not subclass `AbstractCamera`; `squid.camera.utils` falls back to them. `SimulatedCamera` even fabricates missing attributes via `__getattr__`, masking interface gaps. This weakens the promise of swappable implementations.
- **Configuration immutability during a run**: Key values (pixel formats, stage limits, filter wheel options) are fixed at import time and cannot be swapped per session without restarting. The doc’s “configuration hierarchy” is aspirational until config objects are injected and validated rather than read from globals.

**Recommended direction** (to align code with the intended architecture):
1) Replace `_def` globals with validated config objects passed through constructors (no `locals()` mutation, no runtime `exec`).  
2) Finish AbstractCamera (and other ABC) migration; ensure all drivers implement the interfaces and remove placeholder `__getattr__` fallbacks.  
3) Decouple `squid` from `control` by moving shared helpers/config into a clean dependency that does not mutate global state.  
4) Make microscope construction dependency-injected (take config objects, not global flags) to allow multiple instances, simulations, and tests without side effects.

