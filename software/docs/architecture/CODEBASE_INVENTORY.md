# Squid Microscope Software - Codebase Inventory

This document provides a comprehensive inventory of all Python modules, classes, and key functions in the Squid microscope software.

---

## Table of Contents

1. [Entry Points](#1-entry-points)
2. [Core Framework (squid/)](#2-core-framework-squid)
3. [Control & GUI (control/)](#3-control--gui-control)
4. [Summary Statistics](#summary-statistics)

---

## 1. Entry Points

### main_hcs.py
**High Content Screening GUI Launcher**

| Item | Type | Description |
|------|------|-------------|
| `show_config()` | Function | Display configuration editor |
| Main block | - | Argument parsing (--simulation, --live-only, --verbose, --debug-bus) |

Creates: `ApplicationContext`, `HighContentScreeningGui`

---

## 2. Core Framework (squid/)

### 2.1 Application Core

#### squid/application.py

| Class | Description |
|-------|-------------|
| `Controllers` | Container dataclass for all controllers |
| `ApplicationContext` | Application-level dependency injection container |

**ApplicationContext Methods:**
- `_build_microscope()` - Initialize hardware
- `_build_controllers()` - Create controller instances
- `_build_services()` - Initialize service layer
- `create_gui()` - Build main GUI window
- `shutdown()` - Cleanup resources

---

#### squid/events.py
**Event Bus System**

| Class | Description |
|-------|-------------|
| `Event` | Base event class (dataclass) |
| `EventBus` | Pub/sub event system |

**EventBus Methods:**
- `subscribe(event_type, handler)` - Register event handler
- `unsubscribe(event_type, handler)` - Remove handler
- `publish(event)` - Dispatch event to subscribers
- `clear()` - Remove all subscriptions
- `set_debug(enabled)` - Enable debug logging

**Command Events (GUI → Service):**

| Event | Purpose |
|-------|---------|
| `SetExposureTimeCommand` | Set camera exposure |
| `SetAnalogGainCommand` | Set camera gain |
| `SetDACCommand` | Set DAC value |
| `StartCameraTriggerCommand` | Start triggering |
| `StopCameraTriggerCommand` | Stop triggering |
| `SetCameraTriggerFrequencyCommand` | Set trigger FPS |
| `TurnOnAFLaserCommand` | Enable AF laser |
| `TurnOffAFLaserCommand` | Disable AF laser |
| `MoveStageCommand` | Relative stage move |
| `MoveStageToCommand` | Absolute stage move |
| `HomeStageCommand` | Home stage axis |
| `ZeroStageCommand` | Zero stage position |
| `MoveStageToLoadingPositionCommand` | Go to loading position |
| `MoveStageToScanningPositionCommand` | Go to scanning position |
| `SetIlluminationCommand` | Set illumination |
| `StartLiveCommand` | Start live view |
| `StopLiveCommand` | Stop live view |
| `SetTriggerModeCommand` | Set trigger mode |
| `SetTriggerFPSCommand` | Set trigger FPS |
| `SetMicroscopeModeCommand` | Set microscope mode |

**State Events (Service → GUI):**

| Event | Purpose |
|-------|---------|
| `ExposureTimeChanged` | Exposure time updated |
| `AnalogGainChanged` | Gain updated |
| `StagePositionChanged` | Stage moved |
| `LiveStateChanged` | Live view state changed |
| `DACValueChanged` | DAC value updated |
| `ROIChanged` | Region of interest changed |
| `BinningChanged` | Binning changed |
| `PixelFormatChanged` | Pixel format changed |
| `TriggerModeChanged` | Trigger mode changed |
| `TriggerFPSChanged` | Trigger FPS changed |
| `MicroscopeModeChanged` | Mode changed |

**General Events:**

| Event | Purpose |
|-------|---------|
| `AcquisitionStarted` | Acquisition began |
| `AcquisitionFinished` | Acquisition completed |
| `ImageCaptured` | Frame captured |
| `StageMovedTo` | Stage reached position |
| `FocusChanged` | Focus updated |

---

#### squid/registry.py

| Class | Description |
|-------|-------------|
| `Registry[T]` | Generic plugin registry with decorator-based registration |

**Methods:**
- `register(name)` - Decorator for registration
- `register_factory(name, factory)` - Register factory function
- `create(name, *args)` - Create registered instance
- `available()` - List registered names
- `get_class(name)` - Get registered class
- `is_registered(name)` - Check if registered

---

### 2.2 Services Layer

#### squid/services/base.py

| Class | Description |
|-------|-------------|
| `BaseService` | Abstract base for all services |

**Methods:**
- `subscribe(event_type, handler)` - Subscribe to events
- `publish(event)` - Publish event
- `shutdown()` - Cleanup

---

#### squid/services/__init__.py

| Class | Description |
|-------|-------------|
| `ServiceRegistry` | Central service registry |

**Methods:**
- `register(name, service)` - Register service
- `get(name)` - Get service
- `shutdown()` - Shutdown all services

---

#### squid/services/camera_service.py

| Class | Description |
|-------|-------------|
| `CameraService` | Camera control service |

**Methods:**
| Method | Description |
|--------|-------------|
| `set_exposure_time(ms)` | Set exposure |
| `get_exposure_time()` | Get exposure |
| `set_analog_gain(gain)` | Set gain |
| `get_analog_gain()` | Get gain |
| `set_region_of_interest(roi)` | Set ROI |
| `get_region_of_interest()` | Get ROI |
| `get_resolution()` | Get resolution |
| `set_binning(binning)` | Set binning |
| `get_binning()` | Get binning |
| `get_binning_options()` | Get available binning |
| `set_pixel_format(fmt)` | Set pixel format |
| `get_pixel_format()` | Get pixel format |
| `get_available_pixel_formats()` | Get formats |
| `set_temperature(temp)` | Set temperature |
| `set_temperature_reading_callback(cb)` | Set callback |
| `set_white_balance_gains(gains)` | Set WB |
| `get_white_balance_gains()` | Get WB |
| `set_auto_white_balance()` | Auto WB |
| `set_black_level(level)` | Set black level |

---

#### squid/services/stage_service.py

| Class | Description |
|-------|-------------|
| `StageService` | Stage control service |

**Methods:**
| Method | Description |
|--------|-------------|
| `move_x(mm)` | Relative X move |
| `move_y(mm)` | Relative Y move |
| `move_z(mm)` | Relative Z move |
| `move_theta(rad)` | Relative theta move |
| `move_to(x, y, z)` | Absolute move |
| `move_theta_to(rad)` | Absolute theta move |
| `home(axis)` | Home axis |
| `zero(axis)` | Zero axis |
| `move_to_loading_position()` | Go to loading |
| `move_to_scanning_position()` | Go to scanning |
| `move_to_safety_position()` | Go to safety |
| `wait_for_idle()` | Wait for completion |
| `set_limits(limits)` | Set travel limits |
| `get_config()` | Get stage config |
| `get_x_mm_per_ustep()` | Get X resolution |
| `get_y_mm_per_ustep()` | Get Y resolution |
| `get_z_mm_per_ustep()` | Get Z resolution |

---

#### squid/services/peripheral_service.py

| Class | Description |
|-------|-------------|
| `PeripheralService` | Microcontroller peripherals service |

**Methods:**
- `set_dac(channel, value)` - Set DAC output
- `add_joystick_button_listener(callback)` - Register joystick handler

---

#### squid/services/live_service.py

| Class | Description |
|-------|-------------|
| `LiveService` | Live view control service |

**Event Subscriptions:**
- `StartLiveCommand` - Start live view
- `StopLiveCommand` - Stop live view

---

#### squid/services/trigger_service.py

| Class | Description |
|-------|-------------|
| `TriggerService` | Camera trigger control service |

**Event Subscriptions:**
- `SetTriggerModeCommand` - Set mode
- `SetTriggerFPSCommand` - Set FPS

---

#### squid/services/microscope_mode_service.py

| Class | Description |
|-------|-------------|
| `MicroscopeModeService` | Microscope mode/channel configuration |

**Event Subscriptions:**
- `SetMicroscopeModeCommand` - Set mode

---

### 2.3 Configuration

#### squid/config/__init__.py

**Enums:**

| Enum | Values |
|------|--------|
| `FilterWheelControllerVariant` | SQUID, ZABER, OPTOSPIN, DRAGONFLY, XLIGHT |
| `DirectionSign` | POSITIVE (1), NEGATIVE (-1) |
| `CameraVariant` | TOUPCAM, FLIR, HAMAMATSU, IDS, TUCSEN, PHOTOMETRICS, TIS, GXIPY, ANDOR |
| `CameraSensor` | IMX290, IMX178, IMX226, IMX250, IMX252, IMX273, IMX264, IMX265, IMX571, PYTHON300 |
| `CameraPixelFormat` | MONO8, MONO10, MONO12, MONO14, MONO16, RGB24, RGB32, RGB48, BAYER_RG8, BAYER_RG12 |

**Models (Pydantic):**

| Model | Description |
|-------|-------------|
| `SquidFilterWheelConfig` | Squid filter wheel config |
| `ZaberFilterWheelConfig` | Zaber filter wheel config |
| `OptospinFilterWheelConfig` | Optospin config |
| `FilterWheelConfig` | Union type |
| `PIDConfig` | PID controller settings |
| `AxisConfig` | Single axis config |
| `StageConfig` | Complete stage config |
| `RGBValue` | RGB color value |
| `CameraConfig` | Camera configuration |

**Functions:**
- `get_filter_wheel_config()` - Get FW config
- `get_stage_config()` - Get stage config
- `get_camera_config()` - Get camera config
- `get_autofocus_camera_config()` - Get AF camera config

---

#### squid/config/acquisition.py

| Model | Description |
|-------|-------------|
| `GridScanConfig` | Grid scanning (nx, ny, nz, delta_x_mm, delta_y_mm, delta_z_um) |
| `TimelapseConfig` | Timelapse (n_timepoints, interval_seconds) |
| `ChannelConfig` | Channel (name, exposure_ms, analog_gain, illumination_source, z_offset_um) |
| `AutofocusConfig` | Autofocus (enabled, algorithm, n_steps, step_size_um, every_n_fovs) |
| `AcquisitionConfig` | Complete acquisition config |

---

### 2.4 Abstract Base Classes

#### squid/abc.py

| Class | Description |
|-------|-------------|
| `FilterWheelInfo` | Filter wheel metadata |
| `FilterControllerError` | Filter controller exception |
| `AbstractFilterWheelController` | Base for filter controllers |
| `LightSource` | Base for illumination sources |
| `Pos` | Position dataclass (x_mm, y_mm, z_mm, theta_rad) |
| `StageState` | Stage state (busy) |
| `AbstractStage` | Base for stage controllers |
| `CameraAcquisitionMode` | Enum: SOFTWARE_TRIGGER, HARDWARE_TRIGGER, CONTINUOUS |
| `CameraFrameFormat` | Enum: RAW, RGB |
| `CameraGainRange` | Gain range model |
| `CameraFrame` | Image frame dataclass |
| `CameraError` | Camera exception |
| `AbstractCamera` | Base for camera drivers |

**AbstractFilterWheelController Methods:**
- `initialize()`, `home()`, `set_filter_wheel_position()`, `get_filter_wheel_position()`
- `set_delay_offset_ms()`, `get_delay_offset_ms()`, `set_delay_ms()`, `get_delay_ms()`
- `get_filter_wheel_info()`, `close()`

**LightSource Methods:**
- `initialize()`, `set_intensity_control_mode()`, `get_intensity_control_mode()`
- `set_shutter_control_mode()`, `get_shutter_control_mode()`, `set_shutter_state()`, `get_shutter_state()`
- `set_intensity()`, `get_intensity()`, `shut_down()`

**AbstractStage Methods:**
- `move_x()`, `move_y()`, `move_z()`, `move_x_to()`, `move_y_to()`, `move_z_to()`
- `get_pos()`, `get_state()`, `wait_for_idle()`, `home()`, `zero()`, `set_limits()`, `get_config()`

**AbstractCamera Methods:**
- Exposure: `set_exposure_time()`, `get_exposure_time()`, `get_exposure_limits()`, `get_strobe_time()`, `get_total_frame_time()`
- Format: `set_frame_format()`, `get_frame_format()`, `set_pixel_format()`, `get_pixel_format()`, `get_available_pixel_formats()`
- Binning: `set_binning()`, `get_binning()`, `get_binning_options()`
- Resolution: `get_resolution()`, `get_pixel_size_unbinned_um()`, `get_pixel_size_binned_um()`
- Gain: `set_analog_gain()`, `get_analog_gain()`, `get_gain_range()`
- Streaming: `start_streaming()`, `stop_streaming()`, `get_is_streaming()`
- Acquisition: `read_frame()`, `read_camera_frame()`, `get_frame_id()`, `send_trigger()`, `get_ready_for_trigger()`, `set_acquisition_mode()`, `get_acquisition_mode()`
- ROI: `set_region_of_interest()`, `get_region_of_interest()`
- Temperature: `set_temperature()`, `get_temperature()`, `set_temperature_reading_callback()`
- White balance: `get_white_balance_gains()`, `set_white_balance_gains()`, `set_auto_white_balance_gains()`
- Black level: `set_black_level()`, `get_black_level()`
- Callbacks: `add_frame_callback()`, `remove_frame_callback()`, `enable_callbacks()`, `get_callbacks_enabled()`
- Close: `close()`

---

### 2.5 Utilities

#### squid/utils/safe_callback.py

| Item | Type | Description |
|------|------|-------------|
| `CallbackResult[T]` | Class | Result wrapper (success, value, error, stack_trace) |
| `safe_callback()` | Function | Execute callback with error containment |

---

#### squid/utils/thread_safe_state.py

| Class | Description |
|-------|-------------|
| `ThreadSafeValue[T]` | Thread-safe value wrapper |
| `ThreadSafeFlag` | Thread-safe boolean with wait |

**ThreadSafeValue Methods:**
- `get()`, `set()`, `update()`, `get_and_clear()`, `locked()`

**ThreadSafeFlag Methods:**
- `set()`, `clear()`, `is_set()`, `wait()`, `wait_and_clear()`

---

#### squid/utils/worker_manager.py

| Class | Description |
|-------|-------------|
| `WorkerResult` | Worker task result |
| `WorkerSignals` | Qt signals (started, completed, error, timeout) |
| `WorkerManager` | Thread pool manager with timeout detection |

---

#### squid/logging.py

| Function | Description |
|----------|-------------|
| `get_logger()` | Get logger instance |
| `set_stdout_log_level()` | Set console log level |
| `register_crash_handler()` | Register exception handler |
| `setup_uncaught_exception_logging()` | Setup exception logging |
| `get_default_log_directory()` | Get log directory path |
| `add_file_logging()` | Add file handler |

---

#### squid/exceptions.py

| Exception | Description |
|-----------|-------------|
| `SquidError` | Base Squid exception |
| `SquidTimeout` | Timeout exception |

---

## 3. Control & GUI (control/)

### 3.1 Main GUI

#### control/gui_hcs.py

| Class | Description |
|-------|-------------|
| `HighContentScreeningGui` | Main application window (QMainWindow) |

**Key Attributes:**
- `microscope` - Hardware abstraction
- `stage` - Stage controller
- `camera` - Camera controller
- `microcontroller` - MCU interface
- `services` - Service registry
- 50+ widget references

**Methods:**
- `load_objects()` - Initialize hardware
- `setup_hardware()` - Configure hardware
- `setup_movement_updater()` - Position display timer

---

### 3.2 GUI Support (control/gui/)

#### control/gui/qt_controllers.py

| Class | Description |
|-------|-------------|
| `MovementUpdater` | Updates position display on timer |
| `QtAutoFocusController` | Qt-wrapped autofocus controller |
| `QtMultiPointController` | Qt-wrapped multipoint controller |

---

#### control/gui/widget_factory.py
Widget factory functions for creating UI components.

#### control/gui/layout_builder.py
Layout building helper functions.

#### control/gui/signal_connector.py
Signal connection helper functions.

---

### 3.3 Widgets

#### Camera Widgets

| File | Classes |
|------|---------|
| `control/widgets/camera/settings.py` | `CameraSettingsWidget` |
| `control/widgets/camera/live_control.py` | `LiveControlWidget` |
| `control/widgets/camera/recording.py` | `RecordingWidget`, `MultiCameraRecordingWidget` |

---

#### Display Widgets

| File | Classes |
|------|---------|
| `control/widgets/display/napari_live.py` | `NapariLiveWidget` |
| `control/widgets/display/napari_multichannel.py` | `NapariMultiChannelWidget` |
| `control/widgets/display/napari_mosaic.py` | `NapariMosaicDisplayWidget` |
| `control/widgets/display/stats.py` | `StatsDisplayWidget` |
| `control/widgets/display/plotting.py` | `WaveformDisplay`, `PlotWidget`, `SurfacePlotWidget` |
| `control/widgets/display/focus_map.py` | `FocusMapWidget` |

---

#### Hardware Widgets

| File | Classes |
|------|---------|
| `control/widgets/hardware/trigger.py` | `TriggerControlWidget` |
| `control/widgets/hardware/dac.py` | `DACControWidget` |
| `control/widgets/hardware/laser_autofocus.py` | `LaserAutofocusSettingWidget`, `LaserAutofocusControlWidget` |
| `control/widgets/hardware/objectives.py` | `ObjectivesWidget` |
| `control/widgets/hardware/confocal.py` | `SpinningDiskConfocalWidget`, `DragonflyConfocalWidget` |
| `control/widgets/hardware/led_matrix.py` | `LedMatrixSettingsDialog` |
| `control/widgets/hardware/filter_controller.py` | `FilterControllerWidget` |

---

#### Stage Widgets

| File | Classes |
|------|---------|
| `control/widgets/stage/navigation.py` | `NavigationWidget` |
| `control/widgets/stage/autofocus.py` | `AutoFocusWidget` |
| `control/widgets/stage/piezo.py` | `PiezoWidget` |
| `control/widgets/stage/utils.py` | `StageUtils` |

---

#### Acquisition Widgets

| File | Classes |
|------|---------|
| `control/widgets/acquisition/flexible_multipoint.py` | `FlexibleMultiPointWidget` |
| `control/widgets/acquisition/wellplate_multipoint.py` | `WellplateMultiPointWidget` |
| `control/widgets/acquisition/fluidics_multipoint.py` | `MultiPointWithFluidicsWidget` |
| `control/widgets/custom_multipoint.py` | `TemplateMultiPointWidget` |

---

#### Wellplate Widgets

| File | Classes |
|------|---------|
| `control/widgets/wellplate/format.py` | `WellplateFormatWidget` |
| `control/widgets/wellplate/calibration.py` | `WellplateCalibration`, `CalibrationLiveViewer` |
| `control/widgets/wellplate/well_selection.py` | `WellSelectionWidget` |
| `control/widgets/wellplate/well_1536.py` | `Well1536SelectionWidget` |
| `control/widgets/wellplate/sample_settings.py` | `SampleSettingsWidget` |

---

#### Tracking Widgets

| File | Classes |
|------|---------|
| `control/widgets/tracking/controller.py` | `TrackingControllerWidget` |
| `control/widgets/tracking/displacement.py` | `DisplacementMeasurementWidget` |
| `control/widgets/tracking/joystick.py` | `Joystick` |
| `control/widgets/tracking/plate_reader.py` | `PlateReaderAcquisitionWidget`, `PlateReaderNavigationWidget` |

---

#### Other Widgets

| File | Classes |
|------|---------|
| `control/widgets/base.py` | `WrapperWindow`, `CollapsibleGroupBox`, `PandasTableModel` |
| `control/widgets/config.py` | `ConfigEditor`, `ConfigEditorBackwardsCompatible`, `ProfileWidget` |
| `control/widgets/fluidics.py` | `FluidicsWidget` |
| `control/widgets/spectrometer.py` | `SpectrometerControlWidget`, `RecordingWidget`, `SpectrumDisplay` |
| `control/widgets/nl5.py` | `NL5SettingsDialog`, `NL5Widget` |

---

### 3.4 Core Controllers

#### Acquisition (control/core/acquisition/)

| File | Classes |
|------|---------|
| `multi_point_controller.py` | `MultiPointController` |
| `multi_point_worker.py` | `MultiPointWorker` |
| `multi_point_utils.py` | `ScanPositionInformation`, `AcquisitionParameters`, `OverallProgressUpdate`, `RegionProgressUpdate`, `MultiPointControllerFunctions` |
| `job_processing.py` | `CaptureInfo`, `JobImage`, `Job[T]`, `JobResult[T]`, `SaveImageJob`, `JobRunner` |
| `platereader.py` | `PlateReadingWorker`, `PlateReadingController` |

---

#### Display (control/core/display/)

| File | Classes |
|------|---------|
| `live_controller.py` | `LiveController` |
| `stream_handler.py` | `StreamHandlerFunctions`, `StreamHandler`, `QtStreamHandler`, `ImageSaver`, `ImageSaver_Tracking` |
| `image_display.py` | `ImageDisplay`, `ImageDisplayWindow`, `ImageArrayDisplayWindow` |
| `volumetric_imaging.py` | `StreamHandler`, `ImageArrayDisplayWindow` |

**LiveController Methods:**
- `start_live()`, `stop_live()`, `set_microscope_mode()`, `set_trigger_mode()`, `set_trigger_fps()`

---

#### Autofocus (control/core/autofocus/)

| File | Classes |
|------|---------|
| `auto_focus_controller.py` | `AutoFocusController` |
| `auto_focus_worker.py` | `AutofocusWorker` |
| `laser_auto_focus_controller.py` | `LaserAutofocusController` |
| `laser_af_settings_manager.py` | `LaserAFSettingManager` |
| `pdaf.py` | `PDAFController`, `TwoCamerasPDAFCalibrationController` |

---

#### Navigation (control/core/navigation/)

| File | Classes |
|------|---------|
| `scan_coordinates.py` | `ScanCoordinatesUpdate`, `FovCenter`, `RemovedScanCoordinateRegion`, `AddScanCoordinateRegion`, `ClearedScanCoordinates`, `ScanCoordinates`, `ScanCoordinatesSiLA2` |
| `focus_map.py` | `FocusMap`, `NavigationViewer` |
| `objective_store.py` | `ObjectiveStore` |

---

#### Configuration (control/core/configuration/)

| File | Classes |
|------|---------|
| `configuration_manager.py` | `ConfigurationManager` |
| `channel_configuration_manager.py` | `ConfigType`, `ChannelConfigurationManager` |
| `contrast_manager.py` | `ContrastManager` |

---

#### Tracking (control/core/tracking/)

| File | Classes |
|------|---------|
| `tracking.py` | `TrackingController`, `TrackingWorker` |
| `displacement_measurement.py` | `DisplacementMeasurementController` |
| `tracking_dasiamrpn.py` | `Tracker_Image` |

---

#### Output (control/core/output/)

| File | Classes |
|------|---------|
| `usb_spectrometer.py` | `SpectrumStreamHandler`, `SpectrumSaver` |
| `utils_acquisition.py` | Image saving utilities |
| `utils_ome_tiff_writer.py` | OME-TIFF writing utilities |

---

### 3.5 Peripherals

#### Cameras (control/peripherals/cameras/)

| File | Classes |
|------|---------|
| `base.py` | `DefaultCameraCapabilities`, `DefaultCamera` |
| `toupcam.py` | `ToupCamCapabilities`, `StrobeInfo`, `ToupcamCamera` |
| `flir.py` | `ReadType`, `ImageEventHandler`, `Camera` |
| `hamamatsu.py` | `HamamatsuCapabilities`, `HamamatsuCamera` |
| `tucsen.py` | `Mode400BSIV3`, `ModeFL26BW`, `ModeAries`, `TucsenModelProperties`, `TucsenCamera` |
| `photometrics.py` | `PhotometricsCamera` |
| `andor.py` | `AndorCapabilities`, `AndorCamera` |
| `ids.py` | `Camera` |
| `tis.py` | `Camera` |
| `camera_utils.py` | `SimulatedCamera` |
| `dcam.py` | `Dcamapi`, `Dcam` |
| `dcamapi4.py` | DCAM API 4 enums/structures (100+ classes) |

---

#### Stages (control/peripherals/stage/)

| File | Classes |
|------|---------|
| `cephla.py` | `CephlaStage` |
| `prior.py` | `PriorStage` |
| `simulated.py` | `SimulatedStage` |
| `serial.py` | `AbstractCephlaMicroSerial`, `SimSerial`, `MicrocontrollerSerial` |
| `stage_utils.py` | Stage utility functions |

---

#### Lighting (control/peripherals/lighting/)

| File | Classes |
|------|---------|
| `led.py` | `LightSourceType`, `IntensityControlMode`, `ShutterControlMode`, `IlluminationController` |
| `celesta.py` | `CELESTA` |
| `xlight.py` | `XLight`, `XLight_Simulation` |
| `dragonfly.py` | `Dragonfly`, `Dragonfly_Simulation` |
| `cellx.py` | `CellX`, `CellX_Simulation` |
| `sci_led_array.py` | `SciMicroscopyLEDArray`, `SciMicroscopyLEDArray_Simulation` |
| `ldi.py` | `LDI`, `LDI_Simulation` |
| `illumination_andor.py` | `LaserCommands`, `LaserUnit`, `AndorLaser` |

---

#### Filter Wheels (control/peripherals/filter_wheel/)

| File | Classes |
|------|---------|
| `cephla.py` | `SquidFilterWheel` |
| `zaber.py` | `ZaberFilterController` |
| `optospin.py` | `Optospin` |
| `utils.py` | `SimulatedFilterWheelController` |

---

#### Other Peripherals

| File | Classes |
|------|---------|
| `piezo.py` | `PiezoStage` |
| `objective_changer.py` | `ObjectiveChanger2PosController`, `ObjectiveChanger2PosController_Simulation` |
| `xeryon.py` | `Xeryon`, `Units`, `Axis`, `Communication`, `Stage` |
| `fluidics.py` | `Fluidics` |
| `spectrometer_oceanoptics.py` | `Spectrometer`, `Spectrometer_Simulation` |
| `rcm.py` | `RCM_API` |
| `nl5.py` | `NL5`, `NL5_Simulation` |
| `serial_base.py` | `SerialDevice`, `SerialDeviceError` |

---

### 3.6 Configuration & Constants

#### control/_def.py

**Enums:**

| Enum | Description |
|------|-------------|
| `TriggerMode` | SOFTWARE, HARDWARE, CONTINUOUS |
| `Acquisition` | Acquisition defaults |
| `PosUpdate` | Position update interval |
| `MicrocontrollerDef` | MCU protocol definitions |
| `MCU_PINS` | Pin assignments |
| `CMD_SET` | Command set (50+ commands) |
| `CMD_SET2` | Extended commands |
| `HOME_OR_ZERO` | Homing modes |
| `AXIS` | Axis identifiers |

**Key Constants (200+):**
- Stage: `STAGE_MOVEMENT_SIGN_X/Y/Z/THETA`, `USE_ENCODER_X/Y/Z`, `SCREW_PITCH_X/Y/Z_MM`, `MICROSTEPPING_DEFAULT_X/Y/Z`
- Motor: `FULLSTEPS_PER_REV_X/Y/Z`, `MAX_VELOCITY_X/Y/Z_mm`, `MAX_ACCELERATION_X/Y/Z_mm`
- Camera: `CAMERA_TYPE`, `PRINT_CAMERA_FPS`, `DEFAULT_TRIGGER_MODE`, `BUFFER_SIZE_LIMIT`
- Hardware flags: `USE_PRIOR_STAGE`, `SUPPORT_LASER_AUTOFOCUS`, `USE_XERYON`, `USE_JUPYTER_CONSOLE`, `RUN_FLUIDICS`

**Functions:**
- `conf_attribute_reader()` - Parse config values
- `populate_class_from_dict()` - Populate class from dict

---

#### control/console.py

| Class | Description |
|-------|-------------|
| `QtCompleter` | Custom autocompleter |
| `MainThreadCall` | Execute functions on main thread |
| `GuiProxy` | Proxy for thread-safe GUI access |
| `EnhancedInteractiveConsole` | Enhanced Python console |
| `ConsoleThread` | Console thread |
| `NoFileCompleter` | IPython completer without file completion |
| `JupyterWidget` | Embedded Jupyter console |

---

## Summary Statistics

### File Counts

| Directory | Python Files |
|-----------|--------------|
| squid/ | 20 |
| control/core/ | 39 |
| control/widgets/ | 53 |
| control/peripherals/ | 45 |
| control/gui/ | 5 |
| **Total** | **162+** |

### Component Counts

| Component Type | Count |
|----------------|-------|
| Services | 7 |
| Event types | 45+ |
| Abstract base classes | 4 |
| Camera drivers | 10+ |
| Stage drivers | 3+ |
| Lighting controllers | 7+ |
| Widgets | 50+ |
| Controllers | 15+ |
| Configuration models | 20+ |

### Architecture

The codebase follows a **layered architecture**:

1. **Hardware Layer** (`control/peripherals/`) - Device drivers
2. **Service Layer** (`squid/services/`) - Hardware orchestration via event bus
3. **Controller Layer** (`control/core/`) - Business logic
4. **Presentation Layer** (`control/widgets/`, `control/gui/`) - Qt GUI
5. **Configuration Layer** (`squid/config/`, `control/_def.py`) - Settings management

The **event bus** (`squid/events.py`) decouples GUI from hardware, enabling testability and modularity.
