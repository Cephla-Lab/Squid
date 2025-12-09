# Event Bus Migration Plan - COMPREHENSIVE

## The Problem

Widgets are bypassing the event bus in THREE ways:
1. **Direct controller calls** - `self.liveController.set_microscope_mode()`
2. **Direct service calls** - `self._stage_service.move_to()`, `self._service.set_binning()`
3. **Direct hardware access** - `self.camera.get_exposure_time()`, `self.stage.get_config()`

**ALL of these must be replaced with event bus patterns.**

---

## The Correct Pattern

```
User Action → Widget publishes COMMAND → Service handles → Hardware → Service publishes STATE → Widget updates UI
```

**Widgets should ONLY:**
1. Publish Command events (e.g., `SetExposureTimeCommand`)
2. Subscribe to State events (e.g., `ExposureTimeChanged`)
3. Use `blockSignals()` to prevent feedback loops

**Widgets should NEVER:**
- Call service methods directly
- Call controller methods directly
- Access hardware objects directly

---

## Reference Files

| File | Purpose |
|------|---------|
| `squid/events.py` | All event definitions |
| `squid/services/base.py` | BaseService class |
| `control/widgets/hardware/dac.py` | **GOLD STANDARD** - pure event bus widget |
| `squid/services/camera_service.py` | Service pattern example |
| `tests/unit/squid/services/test_camera_service.py` | Test pattern example |

---

## COMPLETE INVENTORY OF VIOLATIONS

### A. Direct Service Calls (MUST BE REMOVED)

#### `control/widgets/camera/settings.py` - CameraSettingsWidget
**Direct `_service` (CameraService) calls:**
- Line 43-44: `get_exposure_limits()`
- Line 52: `get_gain_range()`
- Line 67: `get_available_pixel_formats()`
- Line 79, 225: `get_pixel_format()`
- Line 84, 136: `set_pixel_format()`
- Line 94, 354: `get_region_of_interest()`
- Line 95, 289, 308, 355: `get_resolution()`
- Line 159: `get_binning()`
- Line 165: `get_binning_options()`
- Line 189: `set_temperature_reading_callback()`
- Line 263, 265: `set_auto_white_balance()`
- Line 266: `get_white_balance_gains()`
- Line 267: `set_white_balance_gains()`
- Line 295, 314, 322: `set_region_of_interest()`
- Line 331: `set_temperature()`
- Line 343: `set_binning()`
- Line 376: `set_black_level()`

#### `control/widgets/stage/navigation.py` - NavigationWidget
**Direct `_service` (StageService) calls:**
- Line 49: `get_position()`
- Line 210: `get_x_mm_per_ustep()`
- Line 215: `get_y_mm_per_ustep()`
- Line 220: `get_z_mm_per_ustep()`

#### `control/widgets/display/focus_map.py` - FocusMapWidget
**Direct `_stage_service` calls:**
- Line 284: `get_position().z_mm`
- Line 318: `get_position()`
- Line 392: `move_to(x_mm=x, y_mm=y, z_mm=z)`
- Line 397: `get_position().z_mm`

#### `control/widgets/display/napari_live.py` - NapariLiveWidget
**Direct `_camera_service` calls:**
- Line 218: `get_exposure_limits()`

#### `control/widgets/display/napari_multichannel.py` - NapariMultiChannelWidget
**Direct `_camera_service` calls:**
- Line 89: `get_pixel_size_binned_um()`

#### `control/widgets/display/napari_mosaic.py` - NapariMosaicWidget
**Direct `_camera_service` calls:**
- Line 192: `get_pixel_size_binned_um()`

#### `control/widgets/acquisition/flexible_multipoint.py` - FlexibleMultiPointWidget
**Direct `_stage_service` calls:**
- Lines 286, 303, 606, 622, 639, 655, 660, 682, 874, 986, 1392: `get_position()`
- Line 1169: `move_to()`

#### `control/widgets/acquisition/wellplate_multipoint.py` - WellplateMultiPointWidget
**Direct `_stage_service` calls:**
- Lines 194, 216, 1169, 1322, 1506, 1669, 1674, 1714, 1746, 1843, 2000: `get_position()`
- Line 1688: `move_to()`

#### `control/widgets/wellplate/calibration.py` - WellplateCalibration
**Direct `_stage_service` calls:**
- Line 327: `get_position()`

#### `control/widgets/custom_multipoint.py` - TemplateMultiPointWidget
**Direct `_stage_service` calls:**
- Line 143: `get_position()`

#### `control/widgets/tracking/controller.py` - TrackingControllerWidget
**Direct `peripheral_service` calls:**
- Line 49: `add_joystick_button_listener()`

---

### B. Direct Controller Calls (MUST BE REMOVED)

#### `control/widgets/camera/live_control.py` - LiveControlWidget
**Direct `liveController` calls:**
- Line 309, 314, 319: `set_microscope_mode()`

**Direct `streamHandler` calls:**
- Line 35: `set_display_fps()`
- Line 212: `set_display_fps` connection

**Direct `camera` access:**
- Line 81: `get_acquisition_mode()`
- Line 114-115: `get_exposure_limits()`
- Line 122: `get_gain_range()`
- Line 354: `get_exposure_time()`
- Line 359: `get_analog_gain()`

#### `control/widgets/display/napari_live.py` - NapariLiveWidget
**Direct `liveController` calls:**
- Line 546: `update_illumination()`
- Line 550: `set_display_resolution_scaling()`

**Direct `streamHandler` calls:**
- Line 273: `set_display_fps()` connection
- Line 549: `set_display_resolution_scaling()`

#### `control/widgets/camera/recording.py` - RecordingWidget
**Direct `imageSaver` calls:**
- Line 34, 95: `set_base_path()`
- Line 88: `set_recording_time_limit()` connection
- Line 90: `stop_recording.connect()`
- Line 109: `start_new_experiment()`

**Direct `streamHandler` calls:**
- Line 44, 86: `set_save_fps()`
- Line 110: `start_recording()`
- Line 112, 120: `stop_recording()`

#### `control/widgets/stage/autofocus.py` - AutoFocusWidget
**Direct `autofocusController` calls:**
- Line 35, 90: `set_deltaZ()`
- Line 46, 79: `set_N()`
- Line 75: `autofocus()`
- Line 80: `autofocusFinished.connect()`

**Direct `stage` access:**
- Line 83: `get_config().Z_AXIS.convert_real_units_to_ustep()`

#### `control/widgets/stage/utils.py` - StageUtils
**Direct `live_controller` calls:**
- Line 167: `stop_live()`
- Line 196, 210: `start_live()`

#### `control/widgets/stage/piezo.py` - PiezoWidget
**Direct `piezo` calls:**
- Line 98: `move_to()`
- Line 113: `home()`

#### `control/widgets/hardware/laser_autofocus.py` - LaserAutofocusSettingWidget & LaserAutofocusControlWidget
**Direct `liveController` calls:**
- Lines 321, 612, 624, 632: `start_live()`
- Lines 325, 609, 618, 629: `stop_live()`
- Line 64: `set_trigger_fps()`
- Line 99: `microscope.camera.get_exposure_limits()`
- Line 437: `trigger_acquisition()`
- Line 440: `camera.read_frame()`

**Direct `streamHandler` calls:**
- Line 65: `set_display_fps()`

**Direct `laserAutofocusController` calls:**
- Lines 102, 113, 227, 306, 350, 356, 359, 364, 427: property access
- Lines 252, 335: `characterization_mode`
- Line 371, 546, 556, 571, 598: `is_initialized`
- Line 401: `set_laser_af_properties()`
- Line 402: `initialize_auto()`
- Line 416: `update_threshold_properties()`
- Line 600, 603: `laser_af_properties.has_reference`
- Line 610: `move_to_target()`
- Line 619: `set_reference()`
- Line 630: `measure_displacement()`
- Line 593: `signal_displacement_um.connect()`

#### `control/widgets/hardware/filter_controller.py` - FilterControllerWidget
**Direct `filterController` calls:**
- Line 34, 116: `get_filter_wheel_info()`
- Line 84: `home()`
- Line 89, 113, 131: `get_filter_wheel_position()`
- Line 106, 121, 137: `set_filter_wheel_position()`

**Direct `liveController` access:**
- Line 147, 149: `enable_channel_auto_filter_switching`

#### `control/widgets/tracking/controller.py` - TrackingControllerWidget
**Direct `trackingController` calls:**
- Line 61, 220: `set_base_path()`
- Line 153: `toggle_stage_tracking`
- Line 156: `toggel_enable_af`
- Line 159: `toggel_save_images`
- Line 162: `set_tracking_time_interval`
- Line 173: `signal_tracking_stopped.connect()`
- Line 179: `update_image_resizing_factor()`
- Lines 202, 235: `start_new_experiment()`
- Lines 205-207, 238-240: `set_selected_configurations()`
- Lines 208, 241: `start_tracking()`
- Lines 210, 243: `stop_tracking()`
- Lines 254-256: `update_tracker_selection()`
- Line 260: `objective`
- Line 270: `update_pixel_size()`

#### `control/widgets/tracking/displacement.py` - DisplacementMeasurementWidget
**Direct `displacementMeasurementController` calls:**
- Line 121: `update_settings()`

#### `control/widgets/tracking/plate_reader.py` - PlateReaderAcquisitionWidget & PlateReaderNavigationWidget
**Direct `plateReadingController` calls:**
- Lines 49, 129: `set_base_path()`
- Line 118: `set_af_flag()`
- Line 122: `acquisitionFinished.connect()`
- Line 144: `start_new_experiment()`
- Line 147: `set_selected_configurations()`
- Line 150: `set_selected_columns()`
- Line 157: `run_acquisition()`
- Line 159: `stop_acquisition()`

**Direct `plateReaderNavigationController` calls:**
- Line 256: `home()`
- Line 259: `moveto()`

#### `control/widgets/acquisition/flexible_multipoint.py` - FlexibleMultiPointWidget
**Direct `multipointController` calls:**
- Line 97, 835, 897: `set_base_path()`
- Lines 248, 534, 891, 1388: `set_af_flag()`
- Lines 256, 537, 894, 1389: `set_reflection_af_flag()`
- Lines 524, 888: `set_deltat()`
- Line 525: `set_NX()`
- Line 526: `set_NY()`
- Lines 527, 887, 1384: `set_NZ()`
- Lines 528, 889, 1386: `set_Nt()`
- Line 530: `set_gen_focus_map_flag()`
- Lines 539, 578, 890, 1387: `set_use_piezo()`
- Line 542: `acquisition_finished.connect()`
- Line 550: `signal_acquisition_progress.connect()`
- Line 553: `signal_region_progress.connect()`
- Line 675: `laserAutoFocusController.set_reference()`
- Lines 830, 886, 1383: `set_deltaZ()`
- Line 856: `acquisition_in_progress()`
- Lines 872, 877, 1393: `set_z_range()`
- Lines 881, 883: `set_focus_map()`
- Lines 898, 1390: `set_use_fluidics()`
- Lines 899, 1379: `set_selected_configurations()`
- Lines 902, 1396: `start_new_experiment()`
- Lines 930, 1399: `run_acquisition()`
- Line 933: `request_abort_aquisition()`

**Direct `stage` access:**
- Line 826: `get_config().Z_AXIS.convert_real_units_to_ustep()`

#### `control/widgets/acquisition/wellplate_multipoint.py` - WellplateMultiPointWidget
**Direct `multipointController` calls:** (similar to flexible_multipoint.py)
- Many set_* methods, acquisition control, etc.

**Direct `liveController` calls:**
- Lines 1701, 1703, 1709: `is_live`, `stop_live()`, `start_live()`

**Direct `stage` access:**
- Line 2013: `get_config().Z_AXIS.convert_real_units_to_ustep()`

#### `control/widgets/acquisition/fluidics_multipoint.py` - MultiPointWithFluidicsWidget
**Direct `multipointController` calls:** (similar to flexible_multipoint.py)

**Direct `stage` access:**
- Line 350: `get_config().Z_AXIS.convert_real_units_to_ustep()`

#### `control/widgets/hardware/confocal.py` - SpinningDiskConfocalWidget & DragonflyConfocalWidget
**Direct `xlight`/`dragonfly` calls:**
- All filter/dichroic/motor control methods

#### `control/widgets/nl5.py` - NL5Widget & NL5SettingsDialog
**Direct `nl5` calls:**
- Line 55-58: `set_scan_amplitude()`, `set_offset_x()`, `set_bypass_offset()`, `save_settings()`
- Line 109: `start_acquisition()`
- Line 127: `set_bypass()`
- Line 131: `set_exposure_delay()`
- Line 134: `set_line_speed()`
- Line 137: `set_fov_x()`

#### `control/widgets/hardware/led_matrix.py` - LedMatrixSettingsDialog
**Direct `led_array` calls:**
- Line 47: `set_NA()`

#### `control/widgets/hardware/objectives.py` - ObjectivesWidget
**Direct `objectiveStore` calls:**
- Line 55: `set_current_objective()`

**Direct `objective_changer` calls:**
- Line 59: `currentPosition()`
- Line 61: `moveToPosition1()`
- Line 65: `moveToPosition2()`

#### `control/widgets/spectrometer.py` - SpectrometerControlWidget
**Direct `streamHandler`/`imageSaver` calls:** (same pattern as recording.py)

---

## IMPLEMENTATION PLAN

### Phase 1: Add Missing Events to `squid/events.py`

**New Command Events needed:**

```python
# Stage position query (for widgets that need current position)
@dataclass
class GetStagePositionCommand(Event):
    """Request current stage position - service responds with StagePositionChanged"""
    pass

# Camera property queries
@dataclass
class GetCameraPropertiesCommand(Event):
    """Request camera properties - service responds with CameraPropertiesChanged"""
    pass

@dataclass
class CameraPropertiesChanged(Event):
    """Camera properties for initialization"""
    exposure_limits: Tuple[float, float]
    gain_range: Tuple[float, float]
    resolution: Tuple[int, int]
    binning_options: List[Tuple[int, int]]
    pixel_formats: List[str]

# ROI commands
@dataclass
class SetROICommand(Event):
    x_offset: int
    y_offset: int
    width: int
    height: int

# Binning commands
@dataclass
class SetBinningCommand(Event):
    binning_x: int
    binning_y: int

# Temperature commands
@dataclass
class SetCameraTemperatureCommand(Event):
    temperature_c: float

# White balance commands
@dataclass
class SetWhiteBalanceCommand(Event):
    r: float
    g: float
    b: float
    auto: bool = False

# Black level command
@dataclass
class SetBlackLevelCommand(Event):
    level: float

# Pixel format command
@dataclass
class SetPixelFormatCommand(Event):
    format: str

# Display FPS
@dataclass
class SetDisplayFPSCommand(Event):
    fps: float

@dataclass
class DisplayFPSChanged(Event):
    fps: float

# Recording
@dataclass
class StartRecordingCommand(Event):
    experiment_id: str
    base_path: str

@dataclass
class StopRecordingCommand(Event):
    pass

@dataclass
class RecordingStateChanged(Event):
    is_recording: bool

# Autofocus
@dataclass
class StartAutofocusCommand(Event):
    use_focus_map: bool = False

@dataclass
class SetAutofocusParamsCommand(Event):
    delta_z_um: Optional[float] = None
    n_planes: Optional[int] = None

@dataclass
class AutofocusFinished(Event):
    success: bool
    z_mm: Optional[float] = None

# Laser AF
@dataclass
class InitializeLaserAFCommand(Event):
    auto: bool = True

@dataclass
class SetLaserAFReferenceCommand(Event):
    pass

@dataclass
class MoveToLaserAFTargetCommand(Event):
    target_um: float

@dataclass
class LaserAFDisplacementChanged(Event):
    displacement_um: float

# Filter wheel
@dataclass
class SetFilterWheelCommand(Event):
    wheel_index: int
    position: int

@dataclass
class HomeFilterWheelCommand(Event):
    wheel_index: int

@dataclass
class FilterWheelPositionChanged(Event):
    wheel_index: int
    position: int

# Tracking
@dataclass
class StartTrackingCommand(Event):
    experiment_id: str
    configurations: List[str]

@dataclass
class StopTrackingCommand(Event):
    pass

@dataclass
class TrackingStateChanged(Event):
    is_tracking: bool

# Acquisition
@dataclass
class StartAcquisitionCommand(Event):
    experiment_id: str

@dataclass
class AbortAcquisitionCommand(Event):
    pass

@dataclass
class AcquisitionProgressChanged(Event):
    current: int
    total: int
    region: Optional[str] = None

# Piezo
@dataclass
class MovePiezoCommand(Event):
    position_um: float

@dataclass
class HomePiezoCommand(Event):
    pass

@dataclass
class PiezoPositionChanged(Event):
    position_um: float

# Objective
@dataclass
class SetObjectiveCommand(Event):
    objective_name: str

@dataclass
class ObjectiveChanged(Event):
    objective_name: str

# Confocal
@dataclass
class SetConfocalModeCommand(Event):
    confocal: bool

@dataclass
class SetDiskMotorCommand(Event):
    on: bool

# NL5
@dataclass
class SetNL5ParamsCommand(Event):
    exposure_delay_ms: Optional[int] = None
    line_speed: Optional[int] = None
    fov_x_px: Optional[int] = None
    bypass: Optional[bool] = None

# LED Matrix
@dataclass
class SetLEDMatrixNACommand(Event):
    na: float
```

---

### Phase 2: New Services to Create

| Service File | Wraps | Key Methods |
|--------------|-------|-------------|
| `squid/services/stream_service.py` | StreamHandler, ImageSaver | display FPS, recording |
| `squid/services/autofocus_service.py` | AutoFocusController | autofocus, params |
| `squid/services/laser_af_service.py` | LaserAutofocusController | init, reference, move |
| `squid/services/filter_wheel_service.py` | FilterController | position, home |
| `squid/services/tracking_service.py` | TrackingController | start/stop, config |
| `squid/services/acquisition_service.py` | MultiPointController | run, abort, params |
| `squid/services/piezo_service.py` | PiezoStage | move, home |
| `squid/services/objective_service.py` | ObjectiveStore, ObjectiveChanger | set objective |
| `squid/services/confocal_service.py` | XLight, Dragonfly | mode, motor |
| `squid/services/nl5_service.py` | NL5 | params, acquisition |
| `squid/services/led_matrix_service.py` | LEDArray | NA |

---

### Phase 3: Widget Migrations (in dependency order)

#### 3.1 CameraSettingsWidget
**File:** `control/widgets/camera/settings.py`

**Remove all direct `_service` calls. Replace with:**
- Subscribe to `CameraPropertiesChanged` for initialization
- Publish `SetROICommand`, `SetBinningCommand`, `SetPixelFormatCommand`, etc.
- Subscribe to corresponding state events

**Test:** Verify all spinbox/dropdown changes emit commands, UI updates on state events.

---

#### 3.2 NavigationWidget
**File:** `control/widgets/stage/navigation.py`

**Remove all direct `_service` calls. Replace with:**
- Subscribe to `StagePositionChanged` (already does this partially)
- Remove `get_position()` polling - rely only on events
- Remove `get_*_mm_per_ustep()` - add to `StagePropertiesChanged` event

---

#### 3.3 FocusMapWidget
**File:** `control/widgets/display/focus_map.py`

**Remove all direct `_stage_service` calls. Replace with:**
- Publish `MoveStageToCommand` instead of `move_to()`
- Subscribe to `StagePositionChanged` instead of `get_position()`
- Cache position locally, update on `StagePositionChanged`

---

#### 3.4 LiveControlWidget
**File:** `control/widgets/camera/live_control.py`

**Remove:**
- All `liveController.set_microscope_mode()` → use `SetMicroscopeModeCommand`
- All `streamHandler.set_display_fps()` → use `SetDisplayFPSCommand`
- All `camera.*` access → subscribe to camera state events

---

#### 3.5 NapariLiveWidget
**File:** `control/widgets/display/napari_live.py`

**Remove:**
- `_camera_service.get_exposure_limits()` → subscribe to `CameraPropertiesChanged`
- `liveController.update_illumination()` → use `SetIlluminationCommand`
- `streamHandler.set_display_fps()` → use `SetDisplayFPSCommand`

---

#### 3.6 NapariMultiChannelWidget & NapariMosaicWidget
**Files:** `control/widgets/display/napari_multichannel.py`, `napari_mosaic.py`

**Remove:**
- `_camera_service.get_pixel_size_binned_um()` → subscribe to camera state

---

#### 3.7 RecordingWidget
**File:** `control/widgets/camera/recording.py`

**Remove all direct handler calls. Use:**
- `StartRecordingCommand`, `StopRecordingCommand`
- `SetSaveFPSCommand`, `SetBaseSavePathCommand`
- Subscribe to `RecordingStateChanged`

---

#### 3.8 AutoFocusWidget
**File:** `control/widgets/stage/autofocus.py`

**Remove all direct controller calls. Use:**
- `StartAutofocusCommand`
- `SetAutofocusParamsCommand`
- Subscribe to `AutofocusFinished`

---

#### 3.9 StageUtils
**File:** `control/widgets/stage/utils.py`

**Remove direct `live_controller` calls. Use:**
- `StartLiveCommand`, `StopLiveCommand` (already exist!)
- Subscribe to `LiveStateChanged` to know when to re-enable

---

#### 3.10 PiezoWidget
**File:** `control/widgets/stage/piezo.py`

**Remove direct `piezo` calls. Use:**
- `MovePiezoCommand`, `HomePiezoCommand`
- Subscribe to `PiezoPositionChanged`

---

#### 3.11 LaserAutofocusSettingWidget & LaserAutofocusControlWidget
**File:** `control/widgets/hardware/laser_autofocus.py`

**This is the most complex migration. Remove:**
- All `laserAutofocusController.*` calls
- All `liveController.*` calls
- All `streamHandler.*` calls

**Use appropriate command/state events for each operation.**

---

#### 3.12 FilterControllerWidget
**File:** `control/widgets/hardware/filter_controller.py`

**Remove all direct calls. Use:**
- `SetFilterWheelCommand`, `HomeFilterWheelCommand`
- Subscribe to `FilterWheelPositionChanged`

---

#### 3.13 TrackingControllerWidget
**File:** `control/widgets/tracking/controller.py`

**Remove all direct calls. Use:**
- `StartTrackingCommand`, `StopTrackingCommand`
- Subscribe to `TrackingStateChanged`

---

#### 3.14 DisplacementMeasurementWidget
**File:** `control/widgets/tracking/displacement.py`

**Remove direct calls. Create:**
- `SetDisplacementMeasurementParamsCommand`

---

#### 3.15 PlateReaderWidgets
**File:** `control/widgets/tracking/plate_reader.py`

**Remove all direct calls. Create plate reader events and service.**

---

#### 3.16 FlexibleMultiPointWidget
**File:** `control/widgets/acquisition/flexible_multipoint.py`

**THE LARGEST MIGRATION (~50+ direct calls). Remove:**
- All `multipointController.*` calls
- All `_stage_service.*` calls
- All `stage.*` access

**Use acquisition service events.**

---

#### 3.17 WellplateMultiPointWidget
**File:** `control/widgets/acquisition/wellplate_multipoint.py`

**Similar to FlexibleMultiPointWidget, plus:**
- Remove `liveController.*` calls

---

#### 3.18 FluidicsMultiPointWidget
**File:** `control/widgets/acquisition/fluidics_multipoint.py`

**Similar to FlexibleMultiPointWidget.**

---

#### 3.19 ConfocalWidgets
**File:** `control/widgets/hardware/confocal.py`

**Remove all direct `xlight`/`dragonfly` calls. Use confocal service events.**

---

#### 3.20 NL5Widget
**File:** `control/widgets/nl5.py`

**Remove all direct `nl5` calls. Use NL5 service events.**

---

#### 3.21 LEDMatrixSettingsDialog
**File:** `control/widgets/hardware/led_matrix.py`

**Remove direct `led_array` call. Use `SetLEDMatrixNACommand`.**

---

#### 3.22 ObjectivesWidget
**File:** `control/widgets/hardware/objectives.py`

**Remove direct calls. Use:**
- `SetObjectiveCommand`
- Subscribe to `ObjectiveChanged`

---

#### 3.23 SpectrometerWidgets
**File:** `control/widgets/spectrometer.py`

**Same pattern as RecordingWidget.**

---

#### 3.24 WellplateCalibration
**File:** `control/widgets/wellplate/calibration.py`

**Remove direct `_stage_service` calls. Subscribe to `StagePositionChanged`.**

---

#### 3.25 CustomMultiPointWidget
**File:** `control/widgets/custom_multipoint.py`

**Remove direct `_stage_service` calls.**

---

### Phase 4: Wire Up Services

**File:** `squid/application.py`
- Instantiate all new services
- Pass event bus to each

**File:** `control/gui/widget_factory.py`
- Remove controller/service parameters from widget constructors
- Widgets only need event_bus (or use global `event_bus`)

**File:** `squid/services/__init__.py`
- Export all new services

---

### Phase 5: Testing

**For each service:**
```python
def test_handles_command():
    mock_hw = Mock()
    bus = EventBus()
    service = MyService(mock_hw, bus)

    bus.publish(MyCommand(value=42))

    mock_hw.do_thing.assert_called_once_with(42)

def test_publishes_state():
    mock_hw = Mock()
    bus = EventBus()
    service = MyService(mock_hw, bus)

    received = []
    bus.subscribe(MyStateChanged, lambda e: received.append(e))

    service.do_thing(42)

    assert received[0].value == 42
```

**For each widget:**
- Test that user actions publish correct commands
- Test that state events update UI correctly
- Test that `blockSignals()` prevents loops

---

## Files Summary

### New Files (14 services + tests)

| File | Description |
|------|-------------|
| `squid/services/stream_service.py` | Display/recording |
| `squid/services/autofocus_service.py` | Contrast AF |
| `squid/services/laser_af_service.py` | Laser AF |
| `squid/services/filter_wheel_service.py` | Filter wheels |
| `squid/services/tracking_service.py` | Cell tracking |
| `squid/services/acquisition_service.py` | Multipoint |
| `squid/services/piezo_service.py` | Piezo stage |
| `squid/services/objective_service.py` | Objectives |
| `squid/services/confocal_service.py` | Spinning disk |
| `squid/services/nl5_service.py` | NL5 scanner |
| `squid/services/led_matrix_service.py` | LED array |
| `squid/services/plate_reader_service.py` | Plate reader |
| `squid/services/displacement_service.py` | Displacement |
| `squid/services/spectrometer_service.py` | Spectrometer |

### Files to Modify (25 widgets + infrastructure)

| File | Violations | Priority |
|------|------------|----------|
| `control/widgets/camera/settings.py` | 30+ service calls | HIGH |
| `control/widgets/acquisition/flexible_multipoint.py` | 50+ mixed calls | HIGH |
| `control/widgets/acquisition/wellplate_multipoint.py` | 40+ mixed calls | HIGH |
| `control/widgets/hardware/laser_autofocus.py` | 40+ controller calls | HIGH |
| `control/widgets/camera/live_control.py` | 15+ mixed calls | HIGH |
| `control/widgets/display/napari_live.py` | 10+ mixed calls | MEDIUM |
| `control/widgets/tracking/controller.py` | 20+ controller calls | MEDIUM |
| `control/widgets/camera/recording.py` | 12+ controller calls | MEDIUM |
| `control/widgets/display/focus_map.py` | 5+ service calls | MEDIUM |
| `control/widgets/stage/navigation.py` | 5+ service calls | MEDIUM |
| `control/widgets/stage/autofocus.py` | 6+ controller calls | MEDIUM |
| `control/widgets/stage/utils.py` | 4+ controller calls | MEDIUM |
| `control/widgets/hardware/filter_controller.py` | 8+ controller calls | MEDIUM |
| `control/widgets/hardware/confocal.py` | 20+ hardware calls | MEDIUM |
| `control/widgets/nl5.py` | 10+ hardware calls | LOW |
| `control/widgets/stage/piezo.py` | 3+ hardware calls | LOW |
| `control/widgets/hardware/objectives.py` | 4+ mixed calls | LOW |
| `control/widgets/tracking/displacement.py` | 1 controller call | LOW |
| `control/widgets/tracking/plate_reader.py` | 10+ controller calls | LOW |
| `control/widgets/display/napari_multichannel.py` | 1 service call | LOW |
| `control/widgets/display/napari_mosaic.py` | 1 service call | LOW |
| `control/widgets/hardware/led_matrix.py` | 1 hardware call | LOW |
| `control/widgets/spectrometer.py` | 10+ controller calls | LOW |
| `control/widgets/wellplate/calibration.py` | 2+ service calls | LOW |
| `control/widgets/custom_multipoint.py` | 1 service call | LOW |
| `control/widgets/acquisition/fluidics_multipoint.py` | 30+ mixed calls | LOW |
| `squid/events.py` | Add ~40 new events | FIRST |
| `squid/services/__init__.py` | Export services | LAST |
| `squid/application.py` | Wire services | LAST |
| `control/gui/widget_factory.py` | Update constructors | LAST |

---

## Execution Order

1. **Add all events to `squid/events.py`** (1 commit)
2. **Create services with tests** (14 commits, can parallelize)
3. **Migrate HIGH priority widgets** (5 commits)
4. **Migrate MEDIUM priority widgets** (9 commits)
5. **Migrate LOW priority widgets** (11 commits)
6. **Wire up in application** (2 commits)
7. **Update widget factory** (1 commit)
8. **Integration testing** (1 commit)

**Total: ~44 commits**

---

## Verification

After completing all migrations:

```python
# Enable debug mode
from squid.events import event_bus
event_bus.set_debug(True)

# Run application
# Every UI interaction should produce event logs
# No direct service/controller/hardware calls should remain
```

Search for violations:
```bash
# Should return ZERO results after migration
grep -r "self\._.*service\." control/widgets/
grep -r "Controller\." control/widgets/
grep -r "self\.camera\." control/widgets/
grep -r "self\.stage\." control/widgets/
```
