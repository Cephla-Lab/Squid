# Squid Microscope Software - Codebase Inventory V2

This document provides a comprehensive inventory of all Python modules, classes, and key functions in the Squid microscope software, with detailed descriptions of what each component does.

---

## Table of Contents

1. [Entry Points](#1-entry-points)
2. [Core Framework (squid/)](#2-core-framework-squid)
3. [Control & GUI (control/)](#3-control--gui-control)
4. [Architecture Overview](#architecture-overview)

---

## 1. Entry Points

### main_hcs.py
**High Content Screening GUI Launcher**

The main entry point for the application. Parses command-line arguments and bootstraps the entire application.

| Item | Type | What It Does |
|------|------|--------------|
| `show_config()` | Function | Opens a configuration editor dialog before launching the main application, allowing users to modify settings |
| `--simulation` | CLI arg | Runs with simulated hardware instead of real microscope components |
| `--live-only` | CLI arg | Launches in minimal mode with only live view capabilities |
| `--verbose` | CLI arg | Enables detailed logging output for debugging |
| `--debug-bus` | CLI arg | Logs all event bus messages to help debug pub/sub issues |

**Startup Flow:**
1. Parse CLI arguments
2. Create `ApplicationContext` (handles all dependency injection)
3. Create `HighContentScreeningGui` via context
4. Show main window and start Qt event loop

---

## 2. Core Framework (squid/)

### 2.1 Application Core

#### squid/application.py
**Dependency Injection Container**

Centralizes creation and ownership of all major components. Replaces the legacy pattern where the GUI created and owned everything directly.

| Class | What It Does |
|-------|--------------|
| `Controllers` | A dataclass container that holds references to all controller instances (LiveController, StreamHandler, MultiPointController, etc.). Replaces having 20+ instance variables scattered across the GUI class. |
| `ApplicationContext` | The main dependency injection container. Creates microscope hardware, controllers, and services in the correct order with proper dependencies. Owns the lifecycle of all major components. |

**ApplicationContext Lifecycle:**
1. `_build_microscope()` - Initializes all hardware (camera, stage, microcontroller) either real or simulated
2. `_build_controllers()` - Creates controllers that orchestrate hardware operations
3. `_build_services()` - Creates the service layer that decouples GUI from hardware
4. `create_gui()` - Builds the main window with all widgets
5. `shutdown()` - Tears down everything in reverse order, ensuring clean resource release

---

#### squid/events.py
**Publish/Subscribe Event Bus**

Provides decoupled communication between components. Widgets publish commands, services handle them and publish state changes back. This eliminates direct dependencies between GUI and hardware layers.

| Class | What It Does |
|-------|--------------|
| `Event` | Base dataclass for all events. All events inherit from this to ensure type safety in the pub/sub system. |
| `EventBus` | Thread-safe publish/subscribe mechanism. Handlers that throw exceptions are logged but don't crash the bus. Supports debug mode to log all events for troubleshooting. |

**Event Patterns:**

The event bus uses a **Command/Event Sourcing** pattern:

1. **Command Events (GUI → Service)**: User actions that request changes
2. **State Events (Service → GUI)**: Notifications that state has changed

This separation ensures:
- GUI doesn't need to know how hardware works
- Multiple widgets can react to the same state change
- Testing is easier (just publish/subscribe to events)

**Command Events (Request Changes):**

| Event | What It Does |
|-------|--------------|
| `SetExposureTimeCommand` | Requests camera exposure time change. Contains `exposure_time_ms`. |
| `SetAnalogGainCommand` | Requests camera gain change. Contains `gain` value. |
| `SetDACCommand` | Requests DAC output change. Contains `channel` (0 or 1) and `value` (0-100%). |
| `MoveStageCommand` | Requests relative stage movement. Contains `axis` ('x'/'y'/'z') and `distance_mm`. |
| `MoveStageToCommand` | Requests absolute stage movement. Contains optional `x_mm`, `y_mm`, `z_mm`. |
| `HomeStageCommand` | Requests stage homing. Contains flags for which axes to home. |
| `ZeroStageCommand` | Requests setting current position as zero. Contains axis flags. |
| `MoveStageToLoadingPositionCommand` | Moves stage to sample loading position (typically front of microscope). |
| `MoveStageToScanningPositionCommand` | Moves stage back to scanning position after loading. |
| `StartLiveCommand` | Starts live camera preview. Optionally specifies which channel configuration. |
| `StopLiveCommand` | Stops live camera preview and turns off illumination. |
| `SetTriggerModeCommand` | Changes camera trigger mode ("Software", "Hardware", "Continuous"). |
| `SetTriggerFPSCommand` | Sets frame rate for software/hardware triggering. |
| `SetMicroscopeModeCommand` | Changes microscope channel (e.g., "BF", "Fluorescence 488nm"). |
| `TurnOnAFLaserCommand` | Enables autofocus laser. |
| `TurnOffAFLaserCommand` | Disables autofocus laser. |

**State Events (Notify Changes):**

| Event | What It Does |
|-------|--------------|
| `ExposureTimeChanged` | Camera exposure was changed. Contains new `exposure_time_ms`. |
| `AnalogGainChanged` | Camera gain was changed. Contains new `gain`. |
| `StagePositionChanged` | Stage moved. Contains new `x_mm`, `y_mm`, `z_mm`, optional `theta_rad`. |
| `DACValueChanged` | DAC output changed. Contains `channel` and new `value`. |
| `LiveStateChanged` | Live view started/stopped. Contains `is_live` and optional `configuration`. |
| `ROIChanged` | Camera region of interest changed. Contains offset and dimensions. |
| `BinningChanged` | Camera binning changed. Contains `binning_x` and `binning_y`. |
| `PixelFormatChanged` | Camera pixel format changed (MONO8, MONO12, etc.). |
| `TriggerModeChanged` | Trigger mode changed. Contains new `mode`. |
| `TriggerFPSChanged` | Trigger FPS changed. Contains new `fps`. |
| `MicroscopeModeChanged` | Microscope channel changed. Contains `configuration_name`. |

**General Events:**

| Event | What It Does |
|-------|--------------|
| `AcquisitionStarted` | Multi-point acquisition began. Contains `experiment_id` and `timestamp`. |
| `AcquisitionFinished` | Acquisition completed. Contains `success` flag and optional `error`. |
| `ImageCaptured` | A frame was captured. Contains `frame_id` for cache lookup. |
| `StageMovedTo` | Stage reached a position. Contains `x_mm`, `y_mm`, `z_mm`. |
| `FocusChanged` | Focus was adjusted. Contains `z_mm` and `source` (autofocus/manual/focus_map). |

---

#### squid/registry.py
**Plugin Registry System**

Enables plugin-style registration of implementations (cameras, stages, etc.) using decorators.

| Class | What It Does |
|-------|--------------|
| `Registry[T]` | Generic registry that maps string names to factory functions. Used to register camera implementations (e.g., "simulated", "toupcam") so they can be created by name from configuration. |

**Usage Pattern:**
```python
camera_registry = Registry[AbstractCamera]("camera")

@camera_registry.register("simulated")
class SimulatedCamera(AbstractCamera):
    ...

# Later, create by name from config:
camera = camera_registry.create("simulated", config)
```

---

### 2.2 Services Layer

The service layer provides a clean API for hardware operations. Widgets should **never** call hardware directly - they publish command events, and services handle them.

#### squid/services/base.py

| Class | What It Does |
|-------|--------------|
| `BaseService` | Abstract base class for all services. Provides `subscribe()` to listen for command events, `publish()` to emit state events, and `shutdown()` for cleanup. Services inherit from this and subscribe to the commands they handle. |

---

#### squid/services/camera_service.py

| Class | What It Does |
|-------|--------------|
| `CameraService` | Wraps all camera operations. Handles `SetExposureTimeCommand` and `SetAnalogGainCommand`, validates values against camera limits, calls the camera driver, then publishes state change events. Also exposes methods for ROI, binning, pixel format, temperature, white balance, and black level. |

**Key Behaviors:**
- Clamps exposure/gain values to valid camera limits before applying
- Publishes `ExposureTimeChanged`/`AnalogGainChanged` after successful changes
- Logs warnings if camera doesn't support a feature (e.g., analog gain)

---

#### squid/services/stage_service.py

| Class | What It Does |
|-------|--------------|
| `StageService` | Wraps all stage operations. Handles movement commands (relative, absolute, homing, zeroing), loading/scanning positions. Converts between mm and microsteps. Publishes `StagePositionChanged` after every movement. |

**Key Behaviors:**
- Subscribes to: `MoveStageCommand`, `MoveStageToCommand`, `HomeStageCommand`, `ZeroStageCommand`, `MoveStageToLoadingPositionCommand`, `MoveStageToScanningPositionCommand`
- Loading position: Retracts Z, moves to front of stage for sample access
- Scanning position: Returns to previous position after loading
- All movements publish position updates for UI synchronization

---

#### squid/services/peripheral_service.py

| Class | What It Does |
|-------|--------------|
| `PeripheralService` | Handles microcontroller peripherals: DAC outputs, joystick input, LED control. Subscribes to `SetDACCommand` and publishes `DACValueChanged`. Provides `add_joystick_button_listener()` for registering callbacks on button presses. |

---

#### squid/services/live_service.py

| Class | What It Does |
|-------|--------------|
| `LiveService` | Controls live camera preview. Subscribes to `StartLiveCommand` and `StopLiveCommand`. Coordinates with LiveController to start/stop camera streaming and illumination. Publishes `LiveStateChanged`. |

---

#### squid/services/trigger_service.py

| Class | What It Does |
|-------|--------------|
| `TriggerService` | Controls camera triggering. Subscribes to `SetTriggerModeCommand` and `SetTriggerFPSCommand`. Changes between software triggering (timer-based), hardware triggering (external signal), and continuous mode. Publishes `TriggerModeChanged` and `TriggerFPSChanged`. |

---

#### squid/services/microscope_mode_service.py

| Class | What It Does |
|-------|--------------|
| `MicroscopeModeService` | Handles channel/configuration switching. Subscribes to `SetMicroscopeModeCommand`. When switching modes, coordinates: camera exposure/gain changes, illumination source changes, filter wheel positioning, and emission filter changes. Publishes `MicroscopeModeChanged`. |

---

### 2.3 Configuration

#### squid/config/__init__.py
**Hardware Configuration Models**

Uses Pydantic for validated configuration models.

**Enums:**

| Enum | What It Defines |
|------|-----------------|
| `CameraVariant` | Supported camera manufacturers: TOUPCAM, FLIR, HAMAMATSU, IDS, TUCSEN, PHOTOMETRICS, TIS, GXIPY, ANDOR |
| `CameraSensor` | Known sensor models: IMX290, IMX178, IMX226, IMX250, etc. Used for pixel size calculations. |
| `CameraPixelFormat` | Pixel bit depths: MONO8, MONO10, MONO12, MONO14, MONO16, RGB24, RGB32, RGB48, BAYER_RG8, BAYER_RG12 |
| `FilterWheelControllerVariant` | Filter wheel types: SQUID (built-in), ZABER, OPTOSPIN, DRAGONFLY, XLIGHT |
| `DirectionSign` | Movement direction multipliers: POSITIVE (1) or NEGATIVE (-1) for axis inversion |

**Configuration Models:**

| Model | What It Configures |
|-------|-------------------|
| `CameraConfig` | Camera settings: type, sensor, pixel size, default binning, default pixel format, crop dimensions, rotation angle, flip settings |
| `StageConfig` | Stage settings: axis configs (steps/mm, limits, velocity, acceleration), encoder usage, PID parameters |
| `AxisConfig` | Single axis: screw pitch, steps per revolution, limits, max velocity, max acceleration, direction sign |
| `PIDConfig` | PID controller: Kp, Ki, Kd gains for closed-loop control |
| `FilterWheelConfig` | Filter wheel: controller type, wheel-specific settings (positions, slot names) |

---

#### squid/config/acquisition.py
**Acquisition Configuration Models**

| Model | What It Configures |
|-------|-------------------|
| `GridScanConfig` | Grid parameters: nx, ny, nz grid dimensions, delta_x_mm, delta_y_mm, delta_z_um step sizes |
| `TimelapseConfig` | Timelapse parameters: n_timepoints, interval_seconds between captures |
| `ChannelConfig` | Single channel: name, exposure_ms, analog_gain, illumination_source, z_offset_um for parfocal adjustment |
| `AutofocusConfig` | Autofocus settings: enabled flag, algorithm selection, n_steps, step_size_um, every_n_fovs frequency |
| `AcquisitionConfig` | Complete acquisition: combines grid, timelapse, channels, autofocus, output path. Method `total_images()` calculates expected image count. |

---

### 2.4 Abstract Base Classes

#### squid/abc.py
**Hardware Abstraction Interfaces**

Defines the contracts that all hardware drivers must implement. This enables swapping real hardware for simulated versions during testing.

**Filter Wheel:**

| Class | What It Defines |
|-------|-----------------|
| `FilterWheelInfo` | Dataclass holding filter wheel metadata: index, number_of_slots, slot_names list |
| `FilterControllerError` | Exception raised when filter wheel operations fail |
| `AbstractFilterWheelController` | Interface for filter wheel controllers. Methods: `initialize()`, `home()`, `set_filter_wheel_position()`, `get_filter_wheel_position()`, `set_delay_offset_ms()`, `close()`. A single controller may manage multiple wheels (e.g., Optospin has 4). |

**Light Source:**

| Class | What It Defines |
|-------|-----------------|
| `LightSource` | Interface for illumination sources (LEDs, lasers). Methods: `initialize()`, `set_intensity()`, `get_intensity()`, `set_shutter_state()`, `get_shutter_state()`, `set_intensity_control_mode()`, `set_shutter_control_mode()`, `shut_down()`. |

**Stage:**

| Class | What It Defines |
|-------|-----------------|
| `Pos` | Position dataclass: `x_mm`, `y_mm`, `z_mm`, optional `theta_rad` |
| `StageState` | Stage state dataclass: `busy` boolean indicating if movement is in progress |
| `AbstractStage` | Interface for motorized stages. Methods: `move_x/y/z()` relative, `move_x/y/z_to()` absolute, `get_pos()`, `get_state()`, `home()`, `zero()`, `set_limits()`, `wait_for_idle()`. All movements can be blocking or non-blocking. |

**Camera:**

| Class | What It Defines |
|-------|-----------------|
| `CameraAcquisitionMode` | Enum: SOFTWARE_TRIGGER (timer sends triggers), HARDWARE_TRIGGER (external signal), CONTINUOUS (free-running) |
| `CameraFrameFormat` | Enum: RAW (direct from sensor) or RGB (color-processed) |
| `CameraGainRange` | Dataclass: min_gain, max_gain, gain_step for valid gain values |
| `CameraFrame` | Dataclass: frame_id, timestamp, frame (numpy array), frame_format, frame_pixel_format. Method `is_color()` returns True for color formats. |
| `CameraError` | Exception raised when camera operations fail |
| `AbstractCamera` | Full camera interface (~40 methods). Covers exposure, gain, binning, ROI, pixel format, streaming, triggering, temperature, white balance, black level, frame callbacks. |

**AbstractCamera Key Methods:**

| Category | Methods | What They Do |
|----------|---------|--------------|
| Exposure | `set/get_exposure_time()`, `get_exposure_limits()`, `get_strobe_time()` | Control and query exposure timing |
| Gain | `set/get_analog_gain()`, `get_gain_range()` | Control amplification |
| Binning | `set/get_binning()`, `get_binning_options()` | Combine pixels for sensitivity/speed |
| Resolution | `get_resolution()`, `get_pixel_size_unbinned/binned_um()` | Query sensor dimensions |
| Format | `set/get_pixel_format()`, `get_available_pixel_formats()` | Set bit depth and color mode |
| Streaming | `start/stop_streaming()`, `get_is_streaming()` | Control frame acquisition |
| Triggering | `send_trigger()`, `get_ready_for_trigger()`, `set/get_acquisition_mode()` | Control when frames are captured |
| ROI | `set/get_region_of_interest()` | Capture subset of sensor |
| Callbacks | `add/remove_frame_callback()`, `enable_callbacks()` | Register handlers for new frames |
| Temperature | `set/get_temperature()`, `set_temperature_reading_callback()` | Cooling control |
| Color | `set/get_white_balance_gains()`, `set_auto_white_balance_gains()` | Color correction |
| Frame Access | `read_frame()`, `read_camera_frame()`, `get_frame_id()` | Get captured images |

---

### 2.5 Utilities

#### squid/utils/safe_callback.py

| Item | What It Does |
|------|--------------|
| `CallbackResult[T]` | Wraps callback results with success flag, value, error, and stack_trace. Method `raise_if_error()` re-raises captured exceptions. |
| `safe_callback()` | Executes a callback in a try/except, capturing any exception. Returns `CallbackResult`. Prevents one bad callback from crashing the system. |

---

#### squid/utils/thread_safe_state.py

| Class | What It Does |
|-------|--------------|
| `ThreadSafeValue[T]` | Generic thread-safe wrapper for any value. Methods: `get()`, `set()`, `update(fn)` for atomic read-modify-write, `get_and_clear()`, `locked()` context manager for complex operations. |
| `ThreadSafeFlag` | Thread-safe boolean optimized for signaling. Methods: `set()`, `clear()`, `is_set()`, `wait(timeout)` blocks until set, `wait_and_clear()` blocks then atomically clears. |

---

#### squid/utils/worker_manager.py

| Class | What It Does |
|-------|--------------|
| `WorkerResult` | Result of worker task: success, value, error, stack_trace, timed_out flag |
| `WorkerSignals` | Qt signals emitted by workers: started, completed, error, timeout |
| `WorkerManager` | Thread pool with timeout detection. Method `submit(fn, timeout)` runs function in thread, kills it if timeout exceeded. Useful for operations that might hang (e.g., camera communication). |

---

#### squid/logging.py

| Function | What It Does |
|----------|--------------|
| `get_logger(name)` | Returns a logger instance with the given name, configured with Squid's formatting |
| `set_stdout_log_level(level)` | Changes console output verbosity (DEBUG, INFO, WARNING, ERROR) |
| `register_crash_handler(handler)` | Registers a function to call on unhandled exceptions |
| `setup_uncaught_exception_logging()` | Configures Python to log all uncaught exceptions |
| `get_default_log_directory()` | Returns path to log files (typically ~/.squid/logs/) |
| `add_file_logging(path)` | Adds a file handler to write logs to disk |

---

#### squid/exceptions.py

| Exception | What It Represents |
|-----------|-------------------|
| `SquidError` | Base exception for all Squid-specific errors |
| `SquidTimeout` | Inherits from SquidError and TimeoutError. Raised when operations exceed their time limit. |

---

## 3. Control & GUI (control/)

### 3.1 Main GUI

#### control/gui_hcs.py

| Class | What It Does |
|-------|--------------|
| `HighContentScreeningGui` | Main application window (QMainWindow). Creates and manages 50+ widget instances, sets up docking panels, connects signals, and provides the top-level UI. Holds references to microscope hardware and services. |

**Key Responsibilities:**
- Creates all widgets and arranges them in dock areas
- Connects Qt signals between widgets
- Sets up position update timer for real-time coordinate display
- Manages application shutdown and cleanup
- Provides menu bar and toolbar actions

---

### 3.2 Widgets - Detailed Descriptions

#### Camera Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `CameraSettingsWidget` | `camera/settings.py` | Controls camera parameters: exposure time (spinbox + slider), analog gain, binning dropdown, pixel format dropdown, ROI settings. Publishes command events on user changes, subscribes to state events to update UI. |
| `LiveControlWidget` | `camera/live_control.py` | Start/Stop live preview button, trigger mode selector (Software/Hardware/Continuous), FPS control for triggering rate. Coordinates with LiveController for actual streaming control. |
| `RecordingWidget` | `camera/recording.py` | Record video/image sequences: set output path, file format, recording duration. Supports single frames and continuous recording. |
| `MultiCameraRecordingWidget` | `camera/recording.py` | Like RecordingWidget but for systems with multiple cameras (e.g., main camera + autofocus camera). Synchronizes recording across cameras. |

---

#### Display Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `NapariLiveWidget` | `display/napari_live.py` | Embedded napari viewer for live camera preview. Receives frames from StreamHandler callbacks, updates display at screen refresh rate. Supports contrast adjustment, zoom, pan. |
| `NapariMultiChannelWidget` | `display/napari_multichannel.py` | Multi-layer napari viewer for composite images. Overlays multiple channels (e.g., DAPI + GFP + mCherry) with individual color LUTs and opacity. |
| `NapariMosaicDisplayWidget` | `display/napari_mosaic.py` | Displays stitched mosaic images during/after grid scans. Places tiles at their XY coordinates to form a continuous image. |
| `StatsDisplayWidget` | `display/stats.py` | Shows image statistics: histogram, min/max/mean intensity, frame rate. Updates in real-time during live view. |
| `WaveformDisplay` | `display/plotting.py` | pyqtgraph-based waveform plotter for time-series data (e.g., focus score over time). |
| `PlotWidget` | `display/plotting.py` | Generic pyqtgraph plot widget for 2D data visualization. |
| `SurfacePlotWidget` | `display/plotting.py` | 3D surface plot for visualizing focus maps or Z-stacks. |
| `FocusMapWidget` | `display/focus_map.py` | Visualizes the autofocus map: shows measured focus points and interpolated surface across the sample area. |

---

#### Hardware Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `TriggerControlWidget` | `hardware/trigger.py` | Controls camera triggering: mode selector (Software/Hardware/Continuous), FPS spinbox, start/stop trigger buttons. For hardware triggering, controls the microcontroller's trigger output. |
| `DACControWidget` | `hardware/dac.py` | Controls two DAC channels (0-100%). Each channel has a slider and spinbox. Used for analog control of external devices (e.g., laser power, LED intensity). Publishes `SetDACCommand`, subscribes to `DACValueChanged`. |
| `LaserAutofocusSettingWidget` | `hardware/laser_autofocus.py` | Configures laser autofocus parameters: reference position, search range, step size. Provides "Set Reference" button to capture current position. |
| `LaserAutofocusControlWidget` | `hardware/laser_autofocus.py` | Runtime control of laser AF: enable/disable toggle, status display showing current offset, manual adjustment buttons. |
| `ObjectivesWidget` | `hardware/objectives.py` | Objective lens selector dropdown. When changing objectives, updates pixel size calculations, may trigger filter wheel changes, and updates channel configurations. |
| `SpinningDiskConfocalWidget` | `hardware/confocal.py` | Controls for CrestOptics xLight spinning disk: disk in/out, pinhole selection, dichroic position. |
| `DragonflyConfocalWidget` | `hardware/confocal.py` | Controls for Andor Dragonfly spinning disk: similar to xLight but with Dragonfly-specific options. |
| `LedMatrixSettingsDialog` | `hardware/led_matrix.py` | Configuration dialog for programmable LED matrix: illumination patterns (brightfield, darkfield, DPC), color settings, intensity. |
| `FilterControllerWidget` | `hardware/filter_controller.py` | Manual filter wheel control: dropdown for each wheel showing filter names, position display, home button. |

---

#### Stage Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `NavigationWidget` | `stage/navigation.py` | XYZ jogging controls. Each axis has: position display, step size spinbox, Forward/Backward buttons. Z displays in microns while storing mm internally. Subscribes to `StagePositionChanged` for real-time updates. Has "Click to Move" checkbox for napari integration. |
| `AutoFocusWidget` | `stage/autofocus.py` | Software autofocus controls: algorithm selection (e.g., Brenner, Laplacian), search range, step size, "Run Autofocus" button. Shows progress and final focus score. |
| `PiezoWidget` | `stage/piezo.py` | Controls piezo Z stage for fine focus: position display, target position spinbox, move button. Piezo provides faster, smaller Z movements than the main stage. |
| `StageUtils` | `stage/utils.py` | Utility dialog: Home buttons for each axis, Zero buttons, Go to Loading Position, Go to Scanning Position. Used for stage setup and sample loading. |

---

#### Acquisition Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `FlexibleMultiPointWidget` | `acquisition/flexible_multipoint.py` | Most versatile acquisition widget. Define arbitrary scan regions by clicking in napari, set grid parameters (NX, NY, NZ, deltas), select channels, configure autofocus, set output path, run acquisition. Shows progress during scanning. |
| `WellplateMultiPointWidget` | `acquisition/wellplate_multipoint.py` | Specialized for well plates. Select wells from plate visualization, configure imaging per well (grid size, channels), automatic well-to-well movement, well plate calibration integration. |
| `MultiPointWithFluidicsWidget` | `acquisition/fluidics_multipoint.py` | Extends WellplateMultiPointWidget with fluidics control. Coordinate imaging with fluid exchange protocols (e.g., wash, stain, image cycle). |
| `TemplateMultiPointWidget` | `custom_multipoint.py` | Template for custom acquisition workflows. Override methods to customize scan pattern, timing, image processing. |

---

#### Wellplate Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `WellplateFormatWidget` | `wellplate/format.py` | Selects well plate format: 6, 12, 24, 48, 96, 384, 1536 wells. Displays plate dimensions, well spacing, well size. Updates coordinate system when format changes. |
| `WellplateCalibration` | `wellplate/calibration.py` | Dialog for calibrating plate position. User navigates to 3 corner wells (A1, A12, H1 for 96-well), system calculates plate transformation. |
| `CalibrationLiveViewer` | `wellplate/calibration.py` | Live camera view during calibration. Shows crosshairs at well center, provides fine adjustment controls. |
| `WellSelectionWidget` | `wellplate/well_selection.py` | Interactive well plate grid (QTableWidget). Click wells to select/deselect for imaging. Supports row/column/rectangular selection. Color-codes selected vs. completed wells. |
| `Well1536SelectionWidget` | `wellplate/well_1536.py` | Specialized selection widget for 1536-well plates. Uses compact visualization due to high well count. |
| `SampleSettingsWidget` | `wellplate/sample_settings.py` | Sample metadata: sample ID, notes, expected cell type. Saved with acquisition data. |

---

#### Tracking Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `TrackingControllerWidget` | `tracking/controller.py` | Real-time object tracking controls. Select tracking algorithm (e.g., DaSiamRPN), draw ROI around target in live view, enable tracking. Stage follows tracked object to keep it centered. |
| `DisplacementMeasurementWidget` | `tracking/displacement.py` | Measures displacement/velocity of tracked objects. Records position over time, calculates speed, displays trajectory plot. Used for cell migration studies. |
| `Joystick` | `tracking/joystick.py` | Virtual joystick widget. Click and drag to move stage proportionally to distance from center. Provides intuitive stage control alternative to buttons. |
| `PlateReaderAcquisitionWidget` | `tracking/plate_reader.py` | Plate reader-style acquisition: sequential well imaging with autofocus per well, integration time adjustment, simple "Read Plate" workflow. |
| `PlateReaderNavigationWidget` | `tracking/plate_reader.py` | Quick navigation for plate reading: well selector dropdown, "Go to Well" button, position display. |

---

#### Utility Widgets

| Widget | File | What It Does |
|--------|------|--------------|
| `WrapperWindow` | `base.py` | Generic QMainWindow wrapper. Takes any widget and puts it in a floating window with title bar. Used to pop out dock widgets. |
| `CollapsibleGroupBox` | `base.py` | QGroupBox that can collapse to save space. Click title bar to expand/collapse. Used to organize complex widget panels. |
| `PandasTableModel` | `base.py` | QAbstractTableModel backed by a pandas DataFrame. Used to display tabular data (coordinates, measurements) in QTableView. |
| `ConfigEditor` | `config.py` | Dialog for editing configuration files (YAML/JSON). Tree view of settings, in-place editing, save/load. Used for modifying microscope configuration. |
| `ConfigEditorBackwardsCompatible` | `config.py` | ConfigEditor variant that handles legacy configuration file formats. |
| `ProfileWidget` | `config.py` | Manages configuration profiles. Save current settings as named profile, switch between profiles, export/import. |
| `FluidicsWidget` | `fluidics.py` | Controls fluidics system: pump selection, flow rate, volume, valves. Define and execute fluid handling protocols. |
| `SpectrometerControlWidget` | `spectrometer.py` | Ocean Optics spectrometer controls: integration time, averaging, dark correction. |
| `SpectrumDisplay` | `spectrometer.py` | Displays spectrometer data: wavelength vs. intensity plot, peak detection, cursor readout. |
| `NL5SettingsDialog` | `nl5.py` | Configuration dialog for NL5 laser control: channel assignment, power settings, trigger configuration. |
| `NL5Widget` | `nl5.py` | Runtime NL5 control: channel enable/disable, power adjustment, status indicators. |

---

### 3.3 Core Controllers

#### Acquisition Controllers

| Class | File | What It Does |
|-------|------|--------------|
| `MultiPointController` | `core/acquisition/multi_point_controller.py` | Orchestrates multi-point acquisitions. Manages scan coordinates, channel configurations, autofocus integration, timelapse timing. Creates `MultiPointWorker` for actual acquisition execution. Calculates expected image count and disk usage before starting. |
| `MultiPointWorker` | `core/acquisition/multi_point_worker.py` | Worker thread that executes acquisition. Iterates through regions, FOVs, Z-slices, channels, timepoints. Coordinates stage movement, microscope mode switching, image capture, and saving. Emits progress signals. |
| `ScanPositionInformation` | `core/acquisition/multi_point_utils.py` | Dataclass holding all scan positions: region names, region center coordinates, FOV coordinates within each region. |
| `AcquisitionParameters` | `core/acquisition/multi_point_utils.py` | Dataclass with all acquisition settings: grid dimensions, step sizes, channels, autofocus flags, output path, timing. |
| `JobRunner` | `core/acquisition/job_processing.py` | Background job processor. Handles image saving, metadata writing, processing tasks. Decouples capture from I/O for faster acquisition. |
| `SaveImageJob` | `core/acquisition/job_processing.py` | Job to save a single image with metadata. Queued to JobRunner for asynchronous execution. |

---

#### Display Controllers

| Class | File | What It Does |
|-------|------|--------------|
| `LiveController` | `core/display/live_controller.py` | Controls live camera preview. Manages camera streaming, triggering, illumination. Methods: `start_live()`, `stop_live()`, `set_microscope_mode()`, `set_trigger_mode()`, `set_trigger_fps()`. Coordinates illumination timing with exposure for software triggering. |
| `StreamHandler` | `core/display/stream_handler.py` | Receives camera frames via callback, distributes to displays. Handles frame rate limiting, display scaling, format conversion. Emits signals for new frames. |
| `QtStreamHandler` | `core/display/stream_handler.py` | Qt-signal-emitting wrapper around StreamHandler. Emits `signal_new_frame` for thread-safe GUI updates. |
| `ImageSaver` | `core/display/stream_handler.py` | Saves individual frames to disk on demand. Handles filename generation, format selection (PNG, TIFF), metadata embedding. |
| `ImageDisplay` | `core/display/image_display.py` | QObject that receives frames and updates a display widget. Handles contrast adjustment, color mapping, display timing. |

---

#### Autofocus Controllers

| Class | File | What It Does |
|-------|------|--------------|
| `AutoFocusController` | `core/autofocus/auto_focus_controller.py` | Software autofocus using image analysis. Moves Z through focus range, captures images, calculates focus metric (Brenner, Laplacian, etc.), finds best Z position. Supports focus map generation for sample tilt compensation. |
| `AutofocusWorker` | `core/autofocus/auto_focus_worker.py` | Worker thread for autofocus execution. Performs Z-scan without blocking GUI. Emits progress and completion signals. |
| `LaserAutofocusController` | `core/autofocus/laser_auto_focus_controller.py` | Hardware laser autofocus control. Uses reflection-based laser displacement sensor to measure sample distance. Much faster than software AF but requires setup. |
| `LaserAFSettingManager` | `core/autofocus/laser_af_settings_manager.py` | Persists laser AF settings: reference position, calibration data. Loads/saves to configuration file. |
| `PDAFController` | `core/autofocus/pdaf.py` | Phase Detection Autofocus using dual-camera phase shift measurement. Provides real-time focus feedback without Z scanning. |

---

#### Navigation Controllers

| Class | File | What It Does |
|-------|------|--------------|
| `ScanCoordinates` | `core/navigation/scan_coordinates.py` | Manages scan regions and FOV positions. Methods: `add_region()`, `clear_regions()`, `get_fov_coordinates()`, `get_scan_bounds()`. Supports different region types: single FOV, grid, polygon, wellplate wells. |
| `FocusMap` | `core/navigation/focus_map.py` | Interpolates focus values across sample. Takes measured (X, Y, Z) focus points, fits a surface (plane or polynomial), provides `interpolate(x, y)` for any position. Compensates for tilted samples. |
| `NavigationViewer` | `core/navigation/focus_map.py` | Widget showing XY navigation map. Displays scan regions, current stage position, FOV outlines. Click to move stage. |
| `ObjectiveStore` | `core/navigation/objective_store.py` | Manages objective lens metadata: magnification, NA, pixel size, parfocal offset. Provides current objective info for calculations. Persists objective selection across sessions. |

---

#### Configuration Controllers

| Class | File | What It Does |
|-------|------|--------------|
| `ConfigurationManager` | `core/configuration/configuration_manager.py` | Loads and manages global configuration. Handles configuration file parsing, validation, merging of defaults with user overrides. |
| `ChannelConfigurationManager` | `core/configuration/channel_configuration_manager.py` | Manages microscope channel configurations (e.g., "Brightfield", "DAPI", "GFP"). Stores per-channel settings: illumination source, intensity, exposure, gain, filter positions. Configurations can be objective-specific. |
| `ConfigType` | `core/configuration/channel_configuration_manager.py` | Enum distinguishing configuration types: IMAGING vs AUTOFOCUS. Some channels are only used for autofocus, not actual imaging. |
| `ContrastManager` | `core/configuration/contrast_manager.py` | Manages display contrast settings per channel. Stores min/max/gamma, auto-contrast settings. Persists user adjustments. |

---

#### Tracking Controllers

| Class | File | What It Does |
|-------|------|--------------|
| `TrackingController` | `core/tracking/tracking.py` | Coordinates object tracking. User selects target in live view, controller initializes tracker, continuously updates target position, sends stage commands to follow. Supports multiple tracking algorithms. |
| `TrackingWorker` | `core/tracking/tracking.py` | Worker thread for tracking loop. Runs at frame rate, updates track, calculates required stage movement. Decouples from GUI for smooth tracking. |
| `DisplacementMeasurementController` | `core/tracking/displacement_measurement.py` | Records tracked object positions over time. Calculates displacement, velocity, trajectory statistics. Exports data for analysis. |
| `Tracker_Image` | `core/tracking/tracking_dasiamrpn.py` | DaSiamRPN deep learning tracker implementation. Takes initial bounding box, predicts object location in subsequent frames. High accuracy for cell tracking. |

---

### 3.4 Peripherals (Hardware Drivers)

#### Camera Drivers

| Class | File | What It Does |
|-------|------|--------------|
| `SimulatedCamera` | `peripherals/cameras/camera_utils.py` | Fake camera for testing. Generates synthetic images with a moving square pattern. Implements full AbstractCamera interface. Useful for development without hardware. |
| `ToupcamCamera` | `peripherals/cameras/toupcam.py` | Driver for ToupTek cameras. Uses ToupCam SDK. Supports software/hardware triggering, various pixel formats, temperature control. |
| `HamamatsuCamera` | `peripherals/cameras/hamamatsu.py` | Driver for Hamamatsu cameras (ORCA series). Uses DCAM API. Scientific-grade cameras with low noise, high sensitivity. |
| `PhotometricsCamera` | `peripherals/cameras/photometrics.py` | Driver for Photometrics cameras (Prime, Kinetix). Uses PVCAM API. High-speed scientific cameras. |
| `AndorCamera` | `peripherals/cameras/andor.py` | Driver for Andor cameras (Zyla, Sona). Uses Andor SDK3. sCMOS cameras with high speed and sensitivity. |
| `TucsenCamera` | `peripherals/cameras/tucsen.py` | Driver for Tucsen cameras. Uses Tucsen SDK. Various scientific camera models. |
| `Camera` (FLIR) | `peripherals/cameras/flir.py` | Driver for FLIR/Point Grey cameras. Uses Spinnaker SDK. Industrial and scientific cameras. |
| `Camera` (IDS) | `peripherals/cameras/ids.py` | Driver for IDS cameras. Uses IDS Peak SDK. Industrial cameras. |
| `Camera` (TIS) | `peripherals/cameras/tis.py` | Driver for The Imaging Source cameras. Uses TIS SDK. Industrial cameras. |

**get_camera() Factory Function:**
```python
def get_camera(config, simulated=False, hw_trigger_fn=None, hw_set_strobe_delay_ms_fn=None):
    """
    Creates the appropriate camera driver based on config.camera_type.
    Returns SimulatedCamera if simulated=True.
    Handles import errors gracefully by falling back to default camera.
    """
```

---

#### Stage Drivers

| Class | File | What It Does |
|-------|------|--------------|
| `SimulatedStage` | `peripherals/stage/simulated.py` | Fake stage for testing. Tracks position in memory, simulates movement delays. Implements full AbstractStage interface. |
| `CephlaStage` | `peripherals/stage/cephla.py` | Driver for Squid/Cephla custom motorized stage. Communicates via microcontroller. Supports XYZ + optional theta. Open-loop with optional encoder feedback. |
| `PriorStage` | `peripherals/stage/prior.py` | Driver for Prior Scientific stages. Serial communication. Precision XY stages used in many microscopes. |

**Serial Communication:**

| Class | File | What It Does |
|-------|------|--------------|
| `AbstractCephlaMicroSerial` | `peripherals/stage/serial.py` | Abstract base for microcontroller serial communication. Defines packet format, command/response protocol. |
| `SimSerial` | `peripherals/stage/serial.py` | Simulated serial port. Responds to commands without hardware. Tracks virtual MCU state. |
| `MicrocontrollerSerial` | `peripherals/stage/serial.py` | Real serial communication with Teensy/Arduino microcontroller. Handles USB-serial connection, timeouts, error recovery. |

---

#### Lighting Drivers

| Class | File | What It Does |
|-------|------|--------------|
| `IlluminationController` | `peripherals/lighting/led.py` | High-level illumination control. Routes `turn_on_illumination(wavelength)` to correct light source. Manages intensity, shutter state across multiple sources. |
| `CELESTA` | `peripherals/lighting/celesta.py` | Driver for Lumencor Celesta laser engine. Multi-wavelength laser source with TTL triggering. High power for fluorescence. |
| `LDI` | `peripherals/lighting/ldi.py` | Driver for Lumencor LDI LED engine. Multi-wavelength LED source. Lower power but simpler than lasers. |
| `AndorLaser` | `peripherals/lighting/illumination_andor.py` | Driver for Andor laser combiner. Controls multiple laser lines with power and shutter control. |
| `CellX` | `peripherals/lighting/cellx.py` | Driver for CellX laser control system. Interfaces between software and laser sources. |
| `SciMicroscopyLEDArray` | `peripherals/lighting/sci_led_array.py` | Driver for SciMicroscopy LED matrix. Programmable LED patterns for brightfield, darkfield, DPC, phase contrast. |
| `XLight` | `peripherals/lighting/xlight.py` | Driver for CrestOptics xLight spinning disk confocal. Controls disk position, dichroics, emission filters. |
| `Dragonfly` | `peripherals/lighting/dragonfly.py` | Driver for Andor Dragonfly spinning disk confocal. Similar to xLight with Andor-specific controls. |

---

#### Filter Wheel Drivers

| Class | File | What It Does |
|-------|------|--------------|
| `SimulatedFilterWheelController` | `peripherals/filter_wheel/utils.py` | Fake filter wheel for testing. Tracks position in memory. |
| `SquidFilterWheel` | `peripherals/filter_wheel/cephla.py` | Driver for Squid built-in filter wheel. Controlled via microcontroller. |
| `ZaberFilterController` | `peripherals/filter_wheel/zaber.py` | Driver for Zaber filter wheels. Serial communication. High-speed filter switching. |
| `Optospin` | `peripherals/filter_wheel/optospin.py` | Driver for Cairn Optospin filter wheel. Up to 4 wheel positions, high-speed switching. |

---

#### Other Peripherals

| Class | File | What It Does |
|-------|------|--------------|
| `Microcontroller` | `microcontroller.py` | Main interface to Teensy/Arduino MCU. Commands: DAC control, LED control, trigger output, joystick input, homing/limit switches. All low-level hardware IO goes through this. |
| `PiezoStage` | `peripherals/piezo.py` | Driver for piezo Z stage (e.g., Physik Instrumente, Mad City Labs). Fast, precise Z positioning for focus control. |
| `ObjectiveChanger2PosController` | `peripherals/objective_changer.py` | Controls 2-position objective turret. Switches between objectives, handles parfocal offset. |
| `Xeryon` | `peripherals/xeryon.py` | Driver for Xeryon piezo stages. High-precision linear/rotary stages. |
| `Fluidics` | `peripherals/fluidics.py` | Controls fluidics hardware: pumps, valves, flow sensors. Executes fluid handling protocols. |
| `Spectrometer` | `peripherals/spectrometer_oceanoptics.py` | Driver for Ocean Optics spectrometers. Acquires spectra, handles wavelength calibration. |
| `NL5` | `peripherals/nl5.py` | Driver for NL5 laser controller. Multi-channel laser power and TTL control. |
| `RCM_API` | `peripherals/rcm.py` | API for Reflection Confocal Microscopy system. Specialized hardware for specific imaging modes. |

---

### 3.5 Configuration Constants

#### control/_def.py
**Global Constants and Defaults**

This file contains ~200 constants that configure the microscope behavior.

**Trigger Modes:**
| Enum | Values | Description |
|------|--------|-------------|
| `TriggerMode` | SOFTWARE, HARDWARE, CONTINUOUS | How camera frames are triggered |

**Key Constant Categories:**

| Category | Examples | What They Configure |
|----------|----------|---------------------|
| Stage | `STAGE_MOVEMENT_SIGN_X/Y/Z`, `SCREW_PITCH_X/Y/Z_MM`, `MAX_VELOCITY_X/Y/Z_mm` | Stage movement direction, resolution, speed limits |
| Motor | `FULLSTEPS_PER_REV_X/Y/Z`, `MICROSTEPPING_DEFAULT_X/Y/Z` | Stepper motor configuration |
| Encoder | `USE_ENCODER_X/Y/Z`, `ENCODER_RESOLUTION_UM_X/Y/Z` | Position feedback settings |
| Camera | `CAMERA_TYPE`, `DEFAULT_TRIGGER_MODE`, `BUFFER_SIZE_LIMIT` | Camera defaults |
| Acquisition | `Acquisition.DX/DY/DZ`, `IMAGE_DISPLAY_SCALING_FACTOR` | Default acquisition parameters |
| Hardware flags | `USE_PRIOR_STAGE`, `SUPPORT_LASER_AUTOFOCUS`, `ENABLE_SPINNING_DISK_CONFOCAL` | Feature toggles |
| Positions | `SLIDE_POSITION.LOADING_X/Y_MM`, `SLIDE_POSITION.SCANNING_X/Y_MM` | Predefined stage positions |

---

## Architecture Overview

### Layered Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Presentation Layer                        │
│  (control/widgets/, control/gui_hcs.py)                     │
│  Qt widgets, user interaction, display                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ Events (pub/sub)
┌──────────────────────────▼──────────────────────────────────┐
│                     Service Layer                            │
│  (squid/services/)                                          │
│  CameraService, StageService, PeripheralService, etc.       │
│  Decouples GUI from hardware, handles business logic         │
└──────────────────────────┬──────────────────────────────────┘
                           │ Direct calls
┌──────────────────────────▼──────────────────────────────────┐
│                   Controller Layer                           │
│  (control/core/)                                            │
│  LiveController, MultiPointController, AutoFocusController  │
│  Orchestrates hardware operations                            │
└──────────────────────────┬──────────────────────────────────┘
                           │ Abstract interfaces
┌──────────────────────────▼──────────────────────────────────┐
│                    Hardware Layer                            │
│  (control/peripherals/)                                      │
│  Camera drivers, stage drivers, lighting, etc.              │
│  Implements AbstractCamera, AbstractStage, etc.             │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Patterns

1. **Dependency Injection**: ApplicationContext creates everything, widgets receive dependencies
2. **Pub/Sub Events**: EventBus decouples GUI from hardware; widgets publish commands, services publish state
3. **Abstract Interfaces**: Hardware accessed through abstract base classes (AbstractCamera, AbstractStage)
4. **Registry Pattern**: Camera/stage implementations registered by name for configuration-driven instantiation
5. **Worker Threads**: Long-running operations (acquisition, autofocus) run in background threads

### Data Flow Example: User Changes Exposure

1. User changes spinbox in `CameraSettingsWidget`
2. Widget publishes `SetExposureTimeCommand(exposure_time_ms=50)`
3. `CameraService` receives command via subscription
4. Service calls `camera.set_exposure_time(50)` on hardware driver
5. Service publishes `ExposureTimeChanged(exposure_time_ms=50)`
6. All subscribed widgets receive event and update their displays

This architecture enables:
- Testing without hardware (use simulated drivers)
- Multiple widgets showing same state (all subscribe to events)
- Clean separation of concerns
- Easy addition of new hardware support

---

## Summary Statistics

| Component Type | Count | Description |
|----------------|-------|-------------|
| Python Files | 162+ | Total across squid/ and control/ |
| Services | 7 | Camera, Stage, Peripheral, Live, Trigger, MicroscopeMode, Base |
| Event Types | 45+ | Commands and state notifications |
| Abstract Interfaces | 4 | Camera, Stage, FilterWheel, LightSource |
| Camera Drivers | 10+ | ToupCam, Hamamatsu, Andor, FLIR, etc. |
| Widgets | 50+ | All Qt GUI components |
| Controllers | 15+ | LiveController, MultiPointController, etc. |
