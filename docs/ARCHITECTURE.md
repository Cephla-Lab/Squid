# Squid Microscopy Software Architecture

This document provides a comprehensive overview of the Squid microscopy control software architecture.

## Table of Contents

1. [Overall Project Structure](#1-overall-project-structure)
2. [Core Modules and Their Responsibilities](#2-core-modules-and-their-responsibilities)
3. [Entry Points](#3-entry-points)
4. [Key Classes and Abstractions](#4-key-classes-and-abstractions)
5. [Hardware Abstraction Layer](#5-hardware-abstraction-layer)
6. [GUI Architecture](#6-gui-architecture)
7. [Configuration System](#7-configuration-system)
8. [Data Flow - Image Acquisition](#8-data-flow---image-acquisition)
9. [Key Design Patterns](#9-key-design-patterns)
10. [External Dependencies](#10-external-dependencies)
11. [Threading and Concurrency](#11-threading-and-concurrency)
12. [Special Features](#12-special-features)

---

## 1. Overall Project Structure

The project is organized into three main directories at the root level:

```
/Squid
├── firmware/              # Teensy microcontroller firmware
├── software/
│   ├── control/           # Main control logic and legacy drivers
│   ├── squid/             # Modern abstraction layer
│   ├── configurations/    # Hardware configuration files (.ini)
│   ├── tools/             # Utility scripts
│   ├── tests/             # Unit and integration tests
│   ├── drivers and libraries/  # Manufacturer SDKs
│   └── fluidics_v2/       # Fluidics module (git submodule)
└── docs/                  # Documentation
```

**Primary Entry Point**: `/software/main_hcs.py` - High Content Screening GUI launcher

---

## 2. Core Modules and Their Responsibilities

### A. squid Package (Modern Hardware Abstraction Layer)

Located in `/software/squid/` - Defines abstract interfaces:

| File | Purpose |
|------|---------|
| `abc.py` | Abstract base classes defining hardware contracts |
| `config.py` | Configuration data models (Pydantic-based) |
| `camera/utils.py` | Factory for camera implementations |
| `stage/cephla.py` | Cephla-designed stage with microcontroller |
| `stage/prior.py` | Prior Scientific stage implementation |
| `filter_wheel_controller/` | Filter wheel implementations (cephla, zaber, optospin) |

**Key Abstractions in `abc.py`:**
- `AbstractFilterWheelController` - Filter wheel interface
- `LightSource` - Light source abstraction (lasers, LEDs)
- `AbstractStage` - Stage (XYZ movement) interface
- `AbstractCamera` - Camera interface with frame callbacks
- `CameraFrame` - Data class for camera frames with metadata

### B. control Package (Legacy & Main Business Logic)

Located in `/software/control/` - Primary application logic:

**Core Hardware Drivers:**
- `camera_*.py` - Camera-specific implementations (FLIR, Toupcam, Hamamatsu, IDS, Tucsen, Andor, Photometrics, TIS)
- `microcontroller.py` - Teensy serial communication protocol
- `serial_peripherals.py` - XLight, Dragonfly, Celesta, NL5, CellX
- `lighting.py` - Illumination controller abstraction
- `piezo.py` - Objective piezo stage

**High-Level Managers:**
- `core.py` - Main `QtStreamHandler`, `ImageSaver`, `TrackingController`, `AutoFocusController`
- `live_controller.py` - Live view controller with streaming
- `multi_point_controller.py` - Multi-point acquisition orchestrator
- `multi_point_worker.py` - Worker thread for acquisition
- `auto_focus_controller.py` - Auto-focus logic
- `laser_auto_focus_controller.py` - Laser autofocus with displacement measurement
- `job_processing.py` - Job queue system for parallel image saving
- `stream_handler.py` - Frame streaming with FPS throttling

**Configuration Managers:**
- `channel_configuration_mananger.py` - Channel (wavelength) configuration
- `configuration_mananger.py` - Overall config management
- `contrast_manager.py` - Image contrast adjustments
- `laser_af_settings_manager.py` - Laser AF parameters
- `objective_store.py` - Objective metadata storage
- `scan_coordinates.py` - Multi-point scan coordinate management

**GUI Modules:**
- `gui_hcs.py` - Main GUI window (High Content Screening)
- `widgets.py` - GUI widgets (CameraSettingsWidget, LiveControlWidget, RecordingWidget, etc.)

**Utilities:**
- `_def.py` - Global configuration constants (loaded from .ini files)
- `utils.py` - Helper functions
- `utils_config.py` - Channel mode, configuration data structures
- `utils_channel.py` - Channel-specific utilities
- `utils_acquisition.py` - Acquisition utilities

---

## 3. Entry Points

**Primary Entry**: `/software/main_hcs.py`

```
Entry flow:
1. Parse command-line args (--simulation, --live-only, --verbose)
2. Setup logging via squid.logging
3. Load configuration from .ini files
4. Build Microscope object from global config
5. Create HighContentScreeningGui window
6. Show GUI and start Qt event loop
7. Optional: Start terminal console for debugging
```

Configuration loading pattern:
- Reads `.ini` files from `/software/configurations/` directory
- Uses `ConfigParser` to populate `control._def` module attributes
- Falls back to legacy configuration if no .ini found

---

## 4. Key Classes and Abstractions

### Core Abstraction Hierarchy

```
AbstractCamera (abc.py)
├── ToupcamCamera
├── FlirCamera
├── HamamatsuCamera
├── AndorCamera
├── PhotometricsCamera
├── TucsenCamera
├── SimulatedCamera
└── DefaultCamera

AbstractStage (abc.py)
├── CephlaStage (with Microcontroller)
└── PriorStage (with Serial)

AbstractFilterWheelController (abc.py)
├── SquidFilterWheelController
├── ZaberFilterWheelController
├── OptospinFilterWheelController
└── DragonflyFilterWheelController

LightSource (abc.py)
├── Various laser/LED implementations
```

### Key Data Classes

- `CameraFrame` - Dataclass with frame data, ID, timestamp, pixel format
- `Pos` - Pydantic model with x_mm, y_mm, z_mm, theta_rad
- `StageState` - State of stage (busy flag)
- `CaptureInfo` - Metadata for image capture

### Controller Classes

- `Microscope` - Composition of all hardware
- `MicroscopeAddons` - Optional hardware (filters, fluidics, piezo, etc.)
- `LowLevelDrivers` - Microcontroller wrapper
- `LiveController` - Live viewing stream management
- `MultiPointController` - Multi-point acquisition orchestrator
- `MultiPointWorker` - Worker thread performing actual acquisition
- `AutoFocusController` - Automatic focusing logic
- `LaserAutofocusController` - Laser-based displacement measurement

---

## 5. Hardware Abstraction Layer

### Multi-Camera Support (8+ camera types)

- Factory pattern in `squid.camera.utils.get_camera()`
- Dynamic import based on `config.camera_type`
- Each camera implements `AbstractCamera` interface
- Hardware trigger functions passed as callbacks

### Multi-Stage Support (2 types)

| Stage | Communication | Features |
|-------|---------------|----------|
| CephlaStage | Teensy 4.1 via serial | Native integration |
| PriorStage | Serial protocol | Third-party hardware |

Both implement: movement (relative/absolute), homing, position queries

### Filter Wheel Support (4 variants)

- **Squid**: Via microcontroller
- **Zaber**: Serial commands
- **Optospin**: Via serial with TTL trigger option
- **Dragonfly**: Via serial

### Light Source Support

- LDI (Laser Diode Illumination): Serial control
- CELESTA: Ethernet control
- Andor Lasers: USB control
- LED arrays: Microcontroller PWM
- Spinning disk confocal: XLight/Dragonfly

### Optional Addons

- Objective Piezo (Xeryon)
- Autofocus Camera (separate USB camera)
- Fluidics system (git submodule)
- LED array (SciMicroscopy)

---

## 6. GUI Architecture

**Pattern**: Qt5-based (PyQt5 via qtpy abstraction)

### Main Window

`HighContentScreeningGui` (gui_hcs.py)
- Uses pyqtgraph for image display and plotting
- DockArea layout for flexible widget arrangement
- Napari integration for tiled display

### Widget Organization

**Display Widgets**: Image viewers, plots

**Control Widgets**:
- `LiveControlWidget` - Live preview settings
- `AutoFocusWidget` - AF configuration
- `FilterControllerWidget` - Filter selection
- `CameraSettingsWidget` - Exposure, gain, ROI
- `RecordingWidget` - Recording control
- `NavigationWidget` - Manual stage movement

**Acquisition Widgets**:
- `WellplateMultiPointWidget` - Well-based scanning
- `MultiPointWithFluidicsWidget` - Fluidics-integrated scanning
- `FocusMapWidget` - Z-position mapping

**Utility Widgets**:
- `StatsDisplayWidget` - Image statistics
- `LaserAutofocusSettingWidget` - Laser AF config

### Signal-Based Communication

Uses PyQt signals for thread-safe communication:
- `position_after_move`, `position` signals (movement updates)
- `image_to_display` signals (frame updates)
- `acquisition_finished` signals (workflow events)

---

## 7. Configuration System

### Configuration Storage

INI files in `/software/configurations/`

### Configuration Variants

- `configuration_Squid+.ini` - Standard Squid+ setup
- `configuration_HCS_v2.ini` - High Content Screening
- `configuration_octopi_v2.ini` - OpenFlexure octopus variant
- Platform/camera-specific variants

### Configuration Hierarchy

1. Global constants in `control/_def.py`
2. INI file values override defaults
3. Pydantic models validate and type-check
4. `conf_attribute_reader()` handles JSON/bool/int/float parsing

### Key Configuration Parameters

- Camera type, exposure, gain, binning
- Stage movement signs, motor currents, encoder config
- Light source type and parameters
- Filter wheel controller type
- Autofocus settings (laser-based, reflection-based)
- Multi-point acquisition defaults (DX, DY, DZ, NX, NY, NZ)

---

## 8. Data Flow - Image Acquisition

### Live View Flow

```
Camera.start_streaming()
  → Camera generates frames
    → Camera.add_frame_callback(stream_handler.on_new_frame)
      → StreamHandler processes frame
        → FPS throttling
        → Display resolution scaling
        → Emit image_to_display signal
          → GUI updates viewer
```

### Multi-Point Acquisition Flow

```
GUI triggers MultiPointController.acquire_()
  → Spawns MultiPointWorker in thread
    → For each scan region/field-of-view:
      1. Move stage to position
      2. Optional: Run autofocus
      3. For each Z level:
        4. For each channel/configuration:
          5. Set illumination
          6. Trigger camera
          7. Read frame
          8. Submit SaveImageJob to JobRunner
      → JobRunner saves image (multiprocessing)
    → Emit progress signals to GUI
    → Emit final_image for display
```

### Image Saving Pipeline

```
MultiPointWorker captures frame
  → Creates SaveImageJob with CaptureInfo and image array
    → JobRunner.enqueue(job) to multiprocessing queue
      → Worker process: Job.run()
        → Save as BMP/TIFF
        → Write OME-TIFF metadata
        → File locking for concurrent writes
```

---

## 9. Key Design Patterns

### 1. Abstract Base Class Pattern

Core abstractions in `squid.abc`: `AbstractCamera`, `AbstractStage`, `AbstractFilterWheelController`, `LightSource`

Allows swapping implementations without GUI changes.

### 2. Factory Pattern

- `squid.camera.utils.get_camera()` - Dynamically instantiates camera based on config
- `squid.filter_wheel_controller.utils.get_filter_wheel_controller()`
- `Microscope.build_from_global_config()` - Factory for entire microscope

### 3. Composition Over Inheritance

- `Microscope` composes: `stage`, `camera`, `illumination_controller`, `addons`, `low_level_drivers`
- `MicroscopeAddons` composes optional hardware
- `MultiPointWorker` composes controllers and workers

### 4. Strategy Pattern

- `CameraAcquisitionMode` enum (SOFTWARE_TRIGGER, HARDWARE_TRIGGER, CONTINUOUS)
- Different implementations handle each mode

### 5. Observer Pattern (Qt Signals)

- Camera frame callbacks registered via `add_frame_callback()`
- Multi-point worker emits signals for progress updates
- GUI responds via signal-slot connections

### 6. Template Method Pattern

- `AbstractCamera` defines frame processing template
- Subclasses implement `_process_raw_frame()` for camera-specific logic

### 7. Job Queue Pattern

- `Job` abstract class with `run()` method
- `JobRunner` manages queue (multiprocessing pool)
- `SaveImageJob` implements image persistence

### 8. Configuration as Code Pattern

- Pydantic models for type-safe configuration
- `.ini` files as external configuration source
- `build_from_global_config()` methods for object construction

---

## 10. External Dependencies

### Core Scientific Libraries

- `numpy` - Array processing
- `scipy` - Signal processing (ndimage)
- `opencv-cv2` - Image manipulation
- `imageio` - Image I/O
- `tifffile` - TIFF support
- `pillow` - Image processing

### Hardware Communication

- `pyserial` - Serial port communication (stages, lights, peripherals)
- Manufacturer SDKs (installed in `drivers and libraries/`):
  - `pyspin` - FLIR Spinnaker SDK
  - `dcam` - Hamamatsu DCAM SDK
  - `toupcam` - ToupTek SDK
  - `tucsen` SDK
  - `ids-peak` - IDS camera SDK
  - `gxipy` - Daheng camera SDK
  - Andor, Photometrics SDKs

### GUI/Visualization

- `PyQt5` (via qtpy abstraction) - GUI framework
- `pyqtgraph` - Scientific plotting
- `napari` - Multi-dimensional image viewer
- `matplotlib` - Additional plotting

### Data Format Support

- `pandas` - Data tables/CSV
- `json` - Configuration
- `yaml` - Configuration
- `tifffile` - OME-TIFF metadata

### Utilities

- `pydantic` - Data validation
- `dataclasses` - Data structure decoration
- `threading`, `queue`, `multiprocessing` - Concurrency

### Optional

- `pyimagej` - ImageJ integration
- `scyjava` - Java interop for ImageJ

---

## 11. Threading and Concurrency

### Thread Usage

1. **Main GUI Thread** - PyQt event loop
2. **Live Streaming Thread** - Camera frame callback thread (implicit)
3. **Multi-Point Worker Thread** - Acquisition loop (spawned by GUI)
4. **Multiprocessing Pool** - Image saving workers (if enabled)
5. **Position Polling Thread** - Prior stage position polling

### Synchronization

- `threading.Event` - Signals between threads (e.g., image ready)
- `threading.Lock` - Protects shared data
- `queue.Queue` - Thread-safe communication
- `multiprocessing.Queue` - Process-safe job queue

### Camera Frame Callback Pattern

- Camera calls registered callbacks with `CameraFrame`
- Callbacks must be fast (hot path)
- `StreamHandler` throttles FPS and resolution
- Multi-point worker updates metadata and queues save jobs

---

## 12. Special Features

### Autofocus Options

- **Reflection-based**: Hardware trigger with illumination timing
- **Laser-based**: Separate focus camera measuring displacement
- **Manual Focus Map**: User-defined Z positions per FOV

### Multi-Point Scanning

- Supports 2D well arrays
- Per-well/region autofocus
- Z-stack with configurable direction (bottom-up, center-out)
- Time-lapse (4D: XYZT)
- Fluidics integration (perfusion during scanning)

### Image Processing

- Real-time FPS throttling for display
- Resolution scaling for fast preview
- OME-TIFF metadata writing
- Parallel image saving via multiprocessing

### Stage Control

- Hardware encoders for closed-loop control
- Backlash compensation (Z-axis gravity)
- PID control for encoder feedback
- Movement limits and homing

---

## Summary Table

| Aspect | Implementation |
|--------|----------------|
| **Language** | Python 3.8+ |
| **GUI Framework** | PyQt5 (via qtpy) |
| **Hardware Interface** | Serial (microcontroller, stages, lights), USB (cameras), Ethernet (CELESTA) |
| **Concurrency** | Threading + multiprocessing |
| **Configuration** | INI files + Pydantic validation |
| **Abstractions** | ABC base classes in squid package |
| **Data Formats** | BMP, TIFF, OME-TIFF, JSON, CSV |
| **Tested On** | Ubuntu 22.04 (primary), Windows, macOS |
| **Cameras Supported** | 8+ vendors (FLIR, Toupcam, Hamamatsu, Andor, Tucsen, Photometrics, IDS, TIS) |
| **Stages Supported** | Cephla (microcontroller), Prior Scientific |
| **Light Sources** | LDI, CELESTA, Andor lasers, LED arrays, Spinning disk (XLight/Dragonfly) |
