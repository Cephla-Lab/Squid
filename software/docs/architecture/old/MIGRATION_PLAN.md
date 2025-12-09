# Migration Plan: Clean Architecture

This document consolidates and updates the migration strategy to align with the clean architecture defined in `CLEAN_ARCHITECTURE.md`.

## Architecture Change Summary

### Previous Approach (Services on EventBus)
```
Widget → EventBus → Service → Hardware
```
Services subscribed to commands and published state events.

### New Approach (Controllers on EventBus, Services Stateless)
```
Widget → EventBus → Controller → Service → Hardware
```
- **Controllers** subscribe to commands, own state, publish state events
- **Services** are stateless hardware APIs (no EventBus)

This separation provides:
- Clearer state ownership
- Simpler services (easier to test)
- Controllers can orchestrate multiple services
- Better separation of concerns

---

## Current Violations Inventory

The following violations must be fixed. The fix changes from "use Service via EventBus" to "use Controller via EventBus".

### Direct Service Calls (25 widgets, ~150 calls)

| Widget | File | Violations |
|--------|------|------------|
| CameraSettingsWidget | `camera/settings.py` | 30+ `_service.*` calls |
| NavigationWidget | `stage/navigation.py` | 5 `_service.*` calls |
| FocusMapWidget | `display/focus_map.py` | 5 `_stage_service.*` calls |
| NapariLiveWidget | `display/napari_live.py` | 2 `_camera_service.*` calls |
| NapariMultiChannelWidget | `display/napari_multichannel.py` | 1 `_camera_service.*` call |
| NapariMosaicWidget | `display/napari_mosaic.py` | 1 `_camera_service.*` call |
| FlexibleMultiPointWidget | `acquisition/flexible_multipoint.py` | 12 `_stage_service.*` calls |
| WellplateMultiPointWidget | `acquisition/wellplate_multipoint.py` | 12 `_stage_service.*` calls |
| WellplateCalibration | `wellplate/calibration.py` | 2 `_stage_service.*` calls |
| TemplateMultiPointWidget | `custom_multipoint.py` | 1 `_stage_service.*` call |
| TrackingControllerWidget | `tracking/controller.py` | 1 `peripheral_service.*` call |

### Direct Controller Calls (15 widgets, ~200 calls)

| Widget | File | Violations |
|--------|------|------------|
| LiveControlWidget | `camera/live_control.py` | `liveController.*`, `streamHandler.*`, `camera.*` |
| NapariLiveWidget | `display/napari_live.py` | `liveController.*`, `streamHandler.*` |
| RecordingWidget | `camera/recording.py` | `imageSaver.*`, `streamHandler.*` |
| AutoFocusWidget | `stage/autofocus.py` | `autofocusController.*`, `stage.*` |
| StageUtils | `stage/utils.py` | `live_controller.*` |
| PiezoWidget | `stage/piezo.py` | `piezo.*` |
| LaserAutofocusWidgets | `hardware/laser_autofocus.py` | `laserAutofocusController.*`, `liveController.*` |
| FilterControllerWidget | `hardware/filter_controller.py` | `filterController.*`, `liveController.*` |
| TrackingControllerWidget | `tracking/controller.py` | `trackingController.*` |
| DisplacementMeasurementWidget | `tracking/displacement.py` | `displacementMeasurementController.*` |
| PlateReaderWidgets | `tracking/plate_reader.py` | `plateReadingController.*` |
| FlexibleMultiPointWidget | `acquisition/flexible_multipoint.py` | `multipointController.*` (50+ calls) |
| WellplateMultiPointWidget | `acquisition/wellplate_multipoint.py` | `multipointController.*`, `liveController.*` |
| ConfocalWidgets | `hardware/confocal.py` | `xlight.*`, `dragonfly.*` |
| NL5Widget | `nl5.py` | `nl5.*` |
| ObjectivesWidget | `hardware/objectives.py` | `objectiveStore.*`, `objective_changer.*` |

---

## Detailed Violation Inventory (Line Numbers)

### A. Direct Service Calls

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

### B. Direct Controller Calls

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
- Many `set_*` methods, acquisition control, etc.

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

## Migration Phases

### Phase 1: Infrastructure (Foundation)

#### 1.1 Create State Dataclasses

**Create:** `squid/state/` package

```
squid/state/
├── __init__.py
├── camera.py      # CameraState
├── stage.py       # StageState, StageLimits
├── live.py        # LiveState, TriggerMode
├── autofocus.py   # AutofocusState
├── acquisition.py # AcquisitionState, AcquisitionConfig
├── tracking.py    # TrackingState
└── laser_af.py    # LaserAFState
```

Each state is a frozen dataclass representing controller state.

**Commit:** `feat(state): Add state dataclasses for controllers`

---

#### 1.2 Create Controller Base Class

**Create:** `squid/controllers/base.py`

```python
"""Base class for controllers."""
from __future__ import annotations
import logging
from typing import Callable, Type, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from squid.events import Event, EventBus

E = TypeVar("E", bound="Event")


class BaseController:
    """Base class for controllers with EventBus integration."""

    def __init__(self, event_bus: "EventBus"):
        self._bus = event_bus
        self._log = logging.getLogger(self.__class__.__name__)
        self._subscriptions: list[tuple[Type[E], Callable]] = []

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        self._bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def publish(self, event: "Event") -> None:
        self._bus.publish(event)

    def shutdown(self) -> None:
        for event_type, handler in self._subscriptions:
            self._bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
```

**Commit:** `feat(controllers): Add BaseController class`

---

#### 1.3 Create Stateless Service Base

**Create:** `squid/services/stateless.py`

```python
"""Base class for stateless services."""
import logging


class StatelessService:
    """Base class for stateless hardware services (no EventBus)."""

    def __init__(self):
        self._log = logging.getLogger(self.__class__.__name__)
```

**Commit:** `feat(services): Add StatelessService base class`

---

#### 1.4 Create ReactiveWidget Base Class

**Create:** `control/widgets/reactive.py`

```python
"""Base class for reactive widgets."""
from __future__ import annotations
from typing import Callable, Type, TypeVar, TYPE_CHECKING
from PyQt5.QtWidgets import QWidget, QFrame

if TYPE_CHECKING:
    from squid.events import Event, EventBus

E = TypeVar("E", bound="Event")


class ReactiveWidget(QFrame):
    """Base class for widgets that communicate only via EventBus."""

    def __init__(self, event_bus: "EventBus", parent: QWidget | None = None):
        super().__init__(parent)
        self._bus = event_bus
        self._subscriptions: list[tuple[Type[E], Callable]] = []

    def subscribe(self, event_type: Type[E], handler: Callable[[E], None]) -> None:
        self._bus.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def publish(self, event: "Event") -> None:
        self._bus.publish(event)

    def request_state(self, query_event: "Event") -> None:
        """Request current state from controller."""
        self._bus.publish(query_event)

    def closeEvent(self, event) -> None:
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except ValueError:
                pass
        self._subscriptions.clear()
        super().closeEvent(event)
```

**Commit:** `feat(widgets): Add ReactiveWidget base class`

---

### Phase 2: Stateless Services

Convert existing services to stateless APIs. Remove all EventBus code.

#### 2.1 CameraService (Stateless)

**Modify:** `squid/services/camera_service.py`

Remove:
- `BaseService` inheritance → use `StatelessService`
- All `subscribe()` calls
- All `publish()` calls

Keep:
- All hardware wrapper methods
- Validation and clamping logic

```python
class CameraService(StatelessService):
    """Stateless API for camera operations."""

    def __init__(self, camera: AbstractCamera):
        super().__init__()
        self._camera = camera

    def set_exposure_time(self, ms: float) -> float:
        """Set exposure, returns actual clamped value."""
        limits = self._camera.get_exposure_limits()
        clamped = max(limits[0], min(limits[1], ms))
        self._camera.set_exposure_time(clamped)
        return clamped

    def get_exposure_time(self) -> float:
        return self._camera.get_exposure_time()

    # ... rest of methods, no EventBus code
```

**Commit:** `refactor(services): Make CameraService stateless`

---

#### 2.2 StageService (Stateless)

**Modify:** `squid/services/stage_service.py`

Same pattern - remove EventBus, keep hardware API.

**Commit:** `refactor(services): Make StageService stateless`

---

#### 2.3 PeripheralService (Stateless)

**Modify:** `squid/services/peripheral_service.py`

**Commit:** `refactor(services): Make PeripheralService stateless`

---

#### 2.4 New Services to Create

| Service | File | Wraps |
|---------|------|-------|
| IlluminationService | `illumination_service.py` | Microcontroller, LightSource |
| PiezoService | `piezo_service.py` | PiezoStage |
| FilterWheelService | `filter_wheel_service.py` | AbstractFilterWheelController |
| StreamService | `stream_service.py` | StreamHandler, ImageSaver |

Each follows the stateless pattern.

**Commits:** One per service

---

### Phase 3: Controllers

Create controllers that own state and integrate with EventBus.

#### 3.1 CameraController

**Create:** `squid/controllers/camera_controller.py`

```python
"""Camera controller - owns camera state."""
from __future__ import annotations
from copy import deepcopy
from typing import TYPE_CHECKING

from squid.controllers.base import BaseController
from squid.state.camera import CameraState
from squid.events import (
    SetExposureCommand, SetGainCommand, SetBinningCommand,
    SetROICommand, SetPixelFormatCommand,
    RequestCameraStateQuery, CameraStateChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services.camera_service import CameraService


class CameraController(BaseController):
    """Owns camera state, handles camera commands."""

    def __init__(self, camera_service: CameraService, event_bus: EventBus):
        super().__init__(event_bus)
        self._service = camera_service
        self._state = self._read_initial_state()

        self.subscribe(SetExposureCommand, self._on_set_exposure)
        self.subscribe(SetGainCommand, self._on_set_gain)
        self.subscribe(SetBinningCommand, self._on_set_binning)
        self.subscribe(SetROICommand, self._on_set_roi)
        self.subscribe(SetPixelFormatCommand, self._on_set_pixel_format)
        self.subscribe(RequestCameraStateQuery, self._on_request_state)

    def _read_initial_state(self) -> CameraState:
        return CameraState(
            exposure_ms=self._service.get_exposure_time(),
            gain=self._service.get_analog_gain(),
            binning=self._service.get_binning(),
            roi=self._service.get_roi(),
            pixel_format=self._service.get_pixel_format(),
            is_streaming=self._service.is_streaming(),
            acquisition_mode=self._service.get_acquisition_mode(),
            exposure_limits=self._service.get_exposure_limits(),
            gain_range=self._service.get_gain_range(),
            binning_options=self._service.get_binning_options(),
            pixel_formats=self._service.get_available_pixel_formats(),
        )

    def _publish_state(self) -> None:
        self.publish(CameraStateChanged(state=deepcopy(self._state)))

    def _on_set_exposure(self, cmd: SetExposureCommand) -> None:
        actual = self._service.set_exposure_time(cmd.exposure_ms)
        self._state = self._state._replace(exposure_ms=actual)
        self._publish_state()

    def _on_set_gain(self, cmd: SetGainCommand) -> None:
        actual = self._service.set_analog_gain(cmd.gain)
        self._state = self._state._replace(gain=actual)
        self._publish_state()

    def _on_set_binning(self, cmd: SetBinningCommand) -> None:
        actual = self._service.set_binning(cmd.x, cmd.y)
        self._state = self._state._replace(binning=actual)
        self._publish_state()

    def _on_set_roi(self, cmd: SetROICommand) -> None:
        actual = self._service.set_roi(cmd.x, cmd.y, cmd.width, cmd.height)
        self._state = self._state._replace(roi=actual)
        self._publish_state()

    def _on_set_pixel_format(self, cmd: SetPixelFormatCommand) -> None:
        actual = self._service.set_pixel_format(cmd.pixel_format)
        self._state = self._state._replace(pixel_format=actual)
        self._publish_state()

    def _on_request_state(self, query: RequestCameraStateQuery) -> None:
        self._publish_state()

    @property
    def state(self) -> CameraState:
        return deepcopy(self._state)
```

**Commit:** `feat(controllers): Add CameraController`

---

#### 3.2 StageController

**Create:** `squid/controllers/stage_controller.py`

Handles: `MoveStageRelativeCommand`, `MoveStageToCommand`, `HomeStageCommand`, `ZeroStageCommand`
Publishes: `StageStateChanged`

**Commit:** `feat(controllers): Add StageController`

---

#### 3.3 LiveController (New Version)

**Create:** `squid/controllers/live_controller.py`

This is a new controller that replaces the existing `control/core/display/live_controller.py` for EventBus integration.

Handles: `StartLiveCommand`, `StopLiveCommand`, `SetTriggerModeCommand`, `SetTriggerFPSCommand`, `SetMicroscopeModeCommand`
Publishes: `LiveStateChanged`
Uses: CameraService, IlluminationService, PeripheralService

**Commit:** `feat(controllers): Add LiveController`

---

#### 3.4 Additional Controllers

| Controller | Handles | Uses |
|------------|---------|------|
| AutofocusController | `StartAutofocusCommand`, params | CameraService, StageService |
| AcquisitionController | `StartAcquisitionCommand`, abort, pause | All services, other controllers |
| LaserAFController | init, reference, measure, move | CameraService, StageService, PiezoService |
| TrackingController | start/stop, params | CameraService, StageService |
| PiezoController | move, home | PiezoService |
| FilterWheelController | position, home | FilterWheelService |

**Commits:** One per controller

---

### Phase 4: Events Update

#### 4.1 Add Query Events

Widgets need to request initial state from controllers.

```python
# Query events - controllers respond with state
@dataclass
class RequestCameraStateQuery(Event):
    pass

@dataclass
class RequestStageStateQuery(Event):
    pass

@dataclass
class RequestLiveStateQuery(Event):
    pass

@dataclass
class RequestAutofocusStateQuery(Event):
    pass

@dataclass
class RequestAcquisitionStateQuery(Event):
    pass
```

---

#### 4.2 Update State Events

State events now carry full state objects:

```python
@dataclass
class CameraStateChanged(Event):
    state: CameraState  # Full state, not individual fields

@dataclass
class StageStateChanged(Event):
    state: StageState

@dataclass
class LiveStateChanged(Event):
    state: LiveState

@dataclass
class AutofocusStateChanged(Event):
    state: AutofocusState

@dataclass
class AcquisitionStateChanged(Event):
    state: AcquisitionState
```

---

#### 4.3 Organize Events by Domain

```python
# squid/events.py organization:

# === QUERIES ===
# RequestCameraStateQuery, RequestStageStateQuery, etc.

# === CAMERA COMMANDS ===
# SetExposureCommand, SetGainCommand, SetBinningCommand, etc.

# === STAGE COMMANDS ===
# MoveStageRelativeCommand, MoveStageToCommand, HomeStageCommand, etc.

# === LIVE COMMANDS ===
# StartLiveCommand, StopLiveCommand, SetTriggerModeCommand, etc.

# === AUTOFOCUS COMMANDS ===
# StartAutofocusCommand, SetAutofocusParamsCommand, etc.

# === ACQUISITION COMMANDS ===
# StartAcquisitionCommand, StopAcquisitionCommand, PauseAcquisitionCommand, etc.

# === PERIPHERAL COMMANDS ===
# SetDACCommand, TurnOnAFLaserCommand, etc.

# === STATE EVENTS ===
# CameraStateChanged, StageStateChanged, LiveStateChanged, etc.
```

**Commit:** `refactor(events): Reorganize and add query events`

---

### Phase 5: Widget Migration

Migrate widgets to use only EventBus via `ReactiveWidget` base class.

#### Widget Migration Pattern

**Before:**
```python
class CameraSettingsWidget(QFrame):
    def __init__(self, camera_service: CameraService, ...):
        self._service = camera_service
        # Direct service calls
        limits = self._service.get_exposure_limits()
        self.spinbox.setValue(self._service.get_exposure_time())

    def _on_exposure_changed(self, value):
        self._service.set_exposure_time(value)  # Direct call
```

**After:**
```python
class CameraSettingsWidget(ReactiveWidget):
    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)

        # Subscribe to state
        self.subscribe(CameraStateChanged, self._on_state_changed)

        # Connect UI to publish commands
        self.spinbox.valueChanged.connect(
            lambda v: self.publish(SetExposureCommand(exposure_ms=v))
        )

        # Request initial state
        self.request_state(RequestCameraStateQuery())

    def _on_state_changed(self, event: CameraStateChanged) -> None:
        state = event.state

        # Update UI with blocking
        self.spinbox.blockSignals(True)
        self.spinbox.setValue(state.exposure_ms)
        self.spinbox.setRange(*state.exposure_limits)
        self.spinbox.blockSignals(False)
```

---

#### Migration Order (by dependency)

**Tier 1: Foundation widgets (no dependencies on other widgets)**
1. `camera/settings.py` - CameraSettingsWidget
2. `stage/navigation.py` - NavigationWidget
3. `hardware/dac.py` - DACControlWidget (already done - GOLD STANDARD)
4. `hardware/trigger.py` - TriggerControlWidget
5. `stage/piezo.py` - PiezoWidget

**Tier 2: Display widgets**
6. `display/napari_live.py` - NapariLiveWidget
7. `display/napari_multichannel.py` - NapariMultiChannelWidget
8. `display/napari_mosaic.py` - NapariMosaicWidget
9. `display/focus_map.py` - FocusMapWidget

**Tier 3: Control widgets**
10. `camera/live_control.py` - LiveControlWidget
11. `stage/autofocus.py` - AutoFocusWidget
12. `hardware/laser_autofocus.py` - LaserAutofocusWidgets
13. `hardware/filter_controller.py` - FilterControllerWidget
14. `hardware/objectives.py` - ObjectivesWidget

**Tier 4: Complex widgets**
15. `camera/recording.py` - RecordingWidget
16. `tracking/controller.py` - TrackingControllerWidget
17. `wellplate/calibration.py` - WellplateCalibration

**Tier 5: Acquisition widgets (most complex)**
18. `acquisition/flexible_multipoint.py` - FlexibleMultiPointWidget
19. `acquisition/wellplate_multipoint.py` - WellplateMultiPointWidget
20. `acquisition/fluidics_multipoint.py` - MultiPointWithFluidicsWidget

---

### Phase 6: Application Wiring

#### 6.1 Update Application Bootstrap

**Modify:** `squid/application.py`

```python
class SquidApplication:
    def __init__(self, microscope: Microscope, event_bus: EventBus):
        self._microscope = microscope
        self._event_bus = event_bus

        # Create services (stateless)
        self._services = self._create_services()

        # Create controllers (stateful, on EventBus)
        self._controllers = self._create_controllers()

    def _create_services(self) -> dict:
        """Create stateless hardware services."""
        return {
            "camera": CameraService(self._microscope.camera),
            "stage": StageService(self._microscope.stage),
            "illumination": IlluminationService(
                self._microscope.microcontroller,
                self._microscope.light_source,
            ),
            "peripheral": PeripheralService(self._microscope.microcontroller),
            "piezo": PiezoService(self._microscope.piezo)
                     if self._microscope.piezo else None,
            "filter_wheel": FilterWheelService(self._microscope.filter_controller)
                            if self._microscope.filter_controller else None,
        }

    def _create_controllers(self) -> dict:
        """Create controllers with EventBus integration."""
        s = self._services
        bus = self._event_bus

        camera = CameraController(s["camera"], bus)
        stage = StageController(s["stage"], bus)
        live = LiveController(s["camera"], s["illumination"], s["peripheral"], bus)
        autofocus = AutofocusController(s["camera"], s["stage"], live, bus)
        acquisition = AcquisitionController(
            s["camera"], s["stage"], s["illumination"],
            s["piezo"], autofocus, live, bus
        )

        return {
            "camera": camera,
            "stage": stage,
            "live": live,
            "autofocus": autofocus,
            "acquisition": acquisition,
        }

    def shutdown(self) -> None:
        for controller in self._controllers.values():
            controller.shutdown()
```

---

#### 6.2 Update Widget Factory

**Modify:** `control/gui/widget_factory.py`

Widgets now only receive `event_bus`:

```python
def create_camera_settings_widget(self) -> CameraSettingsWidget:
    return CameraSettingsWidget(self._event_bus)

def create_navigation_widget(self) -> NavigationWidget:
    return NavigationWidget(self._event_bus)

# etc.
```

---

### Phase 7: Cleanup

#### 7.1 Remove Legacy Services

Delete EventBus code from services that were converted:
- Remove `TriggerService` (functionality in `LiveController`)
- Remove `MicroscopeModeService` (functionality in `LiveController`)
- Remove `LiveService` (functionality in `LiveController`)

#### 7.2 Remove Legacy Controllers

The old controllers in `control/core/` can be:
- Kept as internal implementation (called by new Controllers)
- Or gradually replaced

For complex controllers like `MultiPointController`, keep them and wrap with `AcquisitionController`.

---

## File Structure After Migration

```
squid/
├── events.py                      # All events (Commands, Queries, State)
├── state/
│   ├── __init__.py
│   ├── camera.py
│   ├── stage.py
│   ├── live.py
│   ├── autofocus.py
│   └── acquisition.py
├── services/                      # Stateless hardware APIs
│   ├── __init__.py
│   ├── stateless.py              # StatelessService base
│   ├── camera_service.py
│   ├── stage_service.py
│   ├── illumination_service.py
│   ├── peripheral_service.py
│   ├── piezo_service.py
│   └── filter_wheel_service.py
├── controllers/                   # Stateful, EventBus integration
│   ├── __init__.py
│   ├── base.py                   # BaseController
│   ├── camera_controller.py
│   ├── stage_controller.py
│   ├── live_controller.py
│   ├── autofocus_controller.py
│   ├── acquisition_controller.py
│   ├── laser_af_controller.py
│   └── tracking_controller.py
└── application.py                 # Wires services + controllers

control/
├── widgets/
│   ├── reactive.py               # ReactiveWidget base
│   ├── camera/
│   ├── stage/
│   ├── display/
│   ├── hardware/
│   ├── acquisition/
│   └── ...
└── core/                          # Legacy controllers (wrapped or removed)
```

---

## Verification

After migration, verify no violations remain:

```bash
# Should return ZERO results
grep -r "self\._service\." control/widgets/
grep -r "self\._.*_service\." control/widgets/
grep -r "Controller\." control/widgets/ | grep -v "ReactiveWidget"
grep -r "self\.camera\." control/widgets/
grep -r "self\.stage\." control/widgets/
grep -r "self\.liveController" control/widgets/
```

Enable debug mode to verify events:
```python
from squid.events import event_bus
event_bus.set_debug(True)
```

---

## Commit Summary

| Phase | Commits | Description |
|-------|---------|-------------|
| 1 | 4 | Infrastructure (state, bases) |
| 2 | 7 | Stateless services |
| 3 | 8 | Controllers |
| 4 | 1 | Events reorganization |
| 5 | 20 | Widget migrations |
| 6 | 2 | Application wiring |
| 7 | 2 | Cleanup |
| **Total** | **~44** | |

---

## Testing Strategy

### Unit Tests

**Services:** Mock hardware, verify method calls
```python
def test_camera_service_clamps_exposure():
    mock_camera = Mock()
    mock_camera.get_exposure_limits.return_value = (1.0, 1000.0)

    service = CameraService(mock_camera)
    actual = service.set_exposure_time(5000.0)

    assert actual == 1000.0
```

**Controllers:** Mock services, verify state + events
```python
def test_camera_controller_publishes_state():
    mock_service = Mock()
    mock_service.set_exposure_time.return_value = 50.0
    bus = EventBus()

    controller = CameraController(mock_service, bus)

    received = []
    bus.subscribe(CameraStateChanged, lambda e: received.append(e))
    bus.publish(SetExposureCommand(exposure_ms=50.0))

    assert len(received) == 1
    assert received[0].state.exposure_ms == 50.0
```

**Widgets:** Mock EventBus, verify commands published
```python
def test_widget_publishes_command_on_input():
    bus = Mock(spec=EventBus)
    widget = CameraSettingsWidget(bus)

    widget._exposure_spinbox.setValue(100.0)

    bus.publish.assert_called()
    cmd = bus.publish.call_args[0][0]
    assert isinstance(cmd, SetExposureCommand)
    assert cmd.exposure_ms == 100.0
```

### Integration Tests

```python
def test_full_flow_exposure_change():
    """Widget → Controller → Service → Controller → Widget"""
    bus = EventBus()
    mock_camera = Mock()
    mock_camera.get_exposure_limits.return_value = (1.0, 1000.0)
    mock_camera.set_exposure_time.return_value = None
    mock_camera.get_exposure_time.return_value = 50.0

    service = CameraService(mock_camera)
    controller = CameraController(service, bus)
    widget = CameraSettingsWidget(bus)

    # Simulate user input
    widget._exposure_spinbox.setValue(50.0)

    # Verify full round-trip
    assert widget._exposure_spinbox.value() == 50.0
    mock_camera.set_exposure_time.assert_called_with(50.0)
```

---

## Common Pitfalls

### 1. Forgetting blockSignals()
```python
def _on_state_changed(self, event):
    self.spinbox.blockSignals(True)   # MUST block
    self.spinbox.setValue(event.state.value)
    self.spinbox.blockSignals(False)  # MUST unblock
```

### 2. Not requesting initial state
```python
def __init__(self, event_bus):
    super().__init__(event_bus)
    self.subscribe(CameraStateChanged, self._on_state_changed)
    self.request_state(RequestCameraStateQuery())  # DON'T FORGET
```

### 3. Heavy objects in events
```python
# BAD - numpy array in event
@dataclass
class FrameCaptured(Event):
    frame: np.ndarray  # Large!

# GOOD - ID reference
@dataclass
class FrameCaptured(Event):
    frame_id: int  # Look up from cache
```

### 4. Circular imports
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid.services.camera_service import CameraService
```

### 5. Not unsubscribing
Use `ReactiveWidget` base class which handles this in `closeEvent()`.
