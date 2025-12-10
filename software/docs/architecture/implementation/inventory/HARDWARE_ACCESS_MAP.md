# Hardware Access Map

This document catalogs all direct hardware access that bypasses the service layer. Each entry needs to be replaced with service calls during refactoring.

---

## Overview

| Component | File | Direct Hardware Calls | Priority |
|-----------|------|----------------------|----------|
| **LiveController** | `control/core/display/live_controller.py` | Camera, Illumination | High |
| **MultiPointWorker** | `control/core/acquisition/multi_point_worker.py` | Camera, Stage, MCU, Piezo | High |
| **MultiPointController** | `control/core/acquisition/multi_point_controller.py` | Camera, Stage, Piezo | High |
| **AutoFocusController** | `control/core/autofocus/auto_focus_controller.py` | Camera, Stage | Medium |
| **LaserAutofocusController** | `control/core/autofocus/laser_auto_focus_controller.py` | MCU, Camera | Medium |
| **TrackingController** | `control/core/tracking/tracking.py` | Stage, Camera | Medium |
| **Widgets** | `control/widgets/**/*.py` | Various | Medium |

---

## LiveController Direct Hardware Access

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/display/live_controller.py`

### Camera Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~150 | `self.camera.start_streaming()` | `self._camera_service.start_streaming(callback)` |
| ~155 | `self.camera.stop_streaming()` | `self._camera_service.stop_streaming()` |
| ~180 | `self.camera.send_trigger(exposure_time)` | `self._camera_service.send_trigger()` |
| ~185 | `self.camera.enable_callbacks(True)` | `self._camera_service.enable_callbacks(True)` |
| ~190 | `self.camera.set_acquisition_mode(mode)` | `self._camera_service.set_acquisition_mode(mode)` |
| ~200 | `self.camera.set_exposure_time(ms)` | `self._camera_service.set_exposure_time(ms)` |
| ~205 | `self.camera.set_analog_gain(gain)` | `self._camera_service.set_analog_gain(gain)` |

### Illumination Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~250 | `self.microscope.illumination_controller.turn_on_illumination(wavelength)` | `self._illumination_service.turn_on(channel)` |
| ~260 | `self.microscope.illumination_controller.turn_off_illumination()` | `self._illumination_service.turn_off()` |
| ~280 | `self.microscope.addons.sci_microscopy_led_array.turn_on_illumination()` | `self._illumination_service.turn_on(channel)` |
| ~290 | `self.microscope.addons.xlight.set_emission_filter(idx)` | Via PeripheralsController |
| ~300 | `self.microscope.addons.dragonfly.set_emission_filter(idx)` | Via PeripheralsController |
| ~310 | `self.microscope.addons.emission_filter_wheel.set_filter_wheel_position(pos)` | `self._filter_wheel_service.set_position(pos)` |
| ~320 | `self.microscope.addons.nl5.set_active_channel(ch)` | Via IlluminationService |

### Microcontroller Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~350 | `self.microscope.low_level_drivers.microcontroller.set_illumination_led_matrix(...)` | Via IlluminationService |

---

## MultiPointWorker Direct Hardware Access

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_worker.py`

### Camera Operations (~15 calls)

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~202 | `self.camera.start_streaming()` | `self._camera_service.start_streaming(callback)` |
| ~210 | `self.camera.add_frame_callback(self._image_callback)` | `self._camera_service.add_frame_callback(...)` |
| ~220 | `self.camera.send_trigger(illumination_time)` | `self._camera_service.send_trigger()` |
| ~230 | `self.camera.read_frame()` | `self._camera_service.read_frame()` |
| ~240 | `self.camera.get_ready_for_trigger()` | `self._camera_service.get_ready_for_trigger()` |
| ~250 | `self.camera.get_frame_id()` | `self._camera_service.get_frame_id()` |
| ~260 | `self.camera.enable_callbacks(bool)` | `self._camera_service.enable_callbacks(bool)` |
| ~270 | `self.camera.remove_frame_callback(id)` | `self._camera_service.remove_frame_callback(id)` |
| ~280 | `self.camera.stop_streaming()` | `self._camera_service.stop_streaming()` |

### Stage Operations (~20 calls)

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~417 | `self.stage.move_x_to(x_mm)` | `self._stage_service.move_to_blocking(x=x_mm)` |
| ~425 | `self.stage.move_y_to(y_mm)` | `self._stage_service.move_to_blocking(y=y_mm)` |
| ~433 | `self.stage.move_z_to(z_mm)` | `self._stage_service.move_to_blocking(z=z_mm)` |
| ~441 | `self.stage.get_pos()` | `self._stage_service.get_position()` |
| ~480 | `self.stage.move_z(relative_mm)` | `self._stage_service.move_relative_blocking(z=relative_mm)` |
| ~520 | `self.stage.wait_for_idle()` | Already part of `move_to_blocking` |
| ~560 | `self.stage.get_pos().z_mm` | `self._stage_service.get_position().z_mm` |

### Microcontroller Operations (~5 calls)

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~336 | `self.microcontroller.enable_joystick(False)` | `self._peripheral_service.enable_joystick(False)` |
| ~350 | `self.microcontroller.enable_joystick(True)` | `self._peripheral_service.enable_joystick(True)` |
| ~360 | `self.microcontroller.wait_till_operation_is_completed()` | `self._peripheral_service.wait_for_idle()` |

### Piezo Operations (~5 calls)

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~523 | `self.piezo.move_to(z_um)` | Via PeripheralsController or direct (simple hardware) |
| ~1079 | `self.piezo.position` | Via PeripheralsController |
| ~1091 | `self.piezo.move_to(z_um)` | Via PeripheralsController |

### LiveController Operations (~10 calls)

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~614 | `self.liveController.set_microscope_mode(config)` | `self._bus.publish(SetMicroscopeModeCommand(...))` |
| ~625 | `self.liveController.turn_on_illumination()` | `self._illumination_service.turn_on(channel)` |
| ~635 | `self.liveController.turn_off_illumination()` | `self._illumination_service.turn_off()` |
| ~645 | `self.liveController.update_illumination()` | Via MicroscopeModeController |

---

## MultiPointController Direct Hardware Access

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/acquisition/multi_point_controller.py`

### Camera Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~60 | `self.camera: AbstractCamera = microscope.camera` | Inject `CameraService` |
| ~300-360 | `self.camera.start_streaming()` / `send_trigger()` / `read_camera_frame()` | Route through `CameraService` |
| ~600 | `self.camera.enable_callbacks(True/False)` | `camera_service.enable_callbacks(...)` |

### Stage/Piezo Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~62 | `self.stage: AbstractStage = microscope.stage` | Inject `StageService` |
| ~555 | `self.stage.move_x_to(x_center)` | `stage_service.move_to_blocking(x=x_center)` |
| ~662 | `self.stage.move_x_to(x_mm)` / `move_y_to` / `move_z_to` | `stage_service.move_to_blocking(...)` |
| ~64 | `self.piezo: ... = microscope.addons.piezo_stage` | Route piezo via PeripheralsController/Service |

**Notes:** Controller orchestrates worker but still holds direct hardware references; refactor to accept services and forward to worker.

---

## AutoFocusController Direct Hardware Access

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/autofocus/auto_focus_controller.py`

### Camera Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~100 | `self.camera.read_frame()` | `self._camera_service.read_frame()` |
| ~110 | `self.camera.send_trigger()` | `self._camera_service.send_trigger()` |

### Stage Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~150 | `self.stage.move_z_to(z)` | `self._stage_service.move_to_blocking(z=z)` |
| ~160 | `self.stage.get_pos().z_mm` | `self._stage_service.get_position().z_mm` |

---

## LaserAutofocusController Direct Hardware Access

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/autofocus/laser_auto_focus_controller.py`

### Microcontroller Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~150-540 | `self.microcontroller.turn_on_AF_laser()` / `turn_off_AF_laser()` / `wait_till_operation_is_completed()` | `PeripheralService` methods for AF laser control |

### Camera Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~430 | `self.camera.read_frame()` | `CameraService.read_frame()` |
| ~470 | `self.camera.send_trigger()` | `CameraService.send_trigger()` |

---

## TrackingController Direct Hardware Access

**File:** `/Users/wea/src/allenlab/Squid/software/control/core/tracking/tracking.py`

### Stage/Camera Operations

| Line | Current Code | Replace With |
|------|--------------|--------------|
| ~350 | `pos = self.stage.get_pos()` | `stage_service.get_position()` |
| ~420 | `self.stage.move_x(...)` / `move_y(...)` | `stage_service.move_x/move_y` |
| ~45 | `self.camera: AbstractCamera = camera` | Inject `CameraService` |

---

## Widget Direct Hardware/Service Access

Run these commands to find widgets with direct access:

```bash
cd /Users/wea/src/allenlab/Squid/software

# Find direct stage access
grep -rn "\.stage\." control/widgets/
grep -rn "self\.stage" control/widgets/

# Find direct camera access
grep -rn "\.camera\." control/widgets/
grep -rn "self\.camera" control/widgets/

# Find direct microcontroller access
grep -rn "microcontroller\." control/widgets/

# Find direct service calls (should use events instead)
grep -rn "camera_service\." control/widgets/
grep -rn "stage_service\." control/widgets/
grep -rn "liveController\." control/widgets/
```

### Known Widget Violations

| Widget | File | Issue | Fix |
|--------|------|-------|-----|
| NavigationWidget | `stage/navigation.py` | Direct stage calls | Use `MoveStageCommand` events |
| StageUtils | `stage/utils.py` | Direct stage calls | Use stage events |
| AutoFocusWidget | `stage/autofocus.py` | Direct controller calls | Use autofocus events |
| CameraSettingsWidget | `camera/settings.py` | May have direct calls | Verify uses events |
| LiveControlWidget | `camera/live_control.py` | Direct LiveController | Use `StartLiveCommand` |
| TriggerControlWidget | `hardware/trigger.py` | Direct calls | Use trigger events |
| WellplateCalibration | `wellplate/calibration.py` | Direct stage calls | Use stage events |

---

## Replacement Strategy

### Phase 3: LiveController Refactoring

1. Add services to constructor:
   ```python
   def __init__(
       self,
       camera_service: CameraService,
       illumination_service: IlluminationService,
       peripheral_service: PeripheralService,
       stream_handler: StreamHandler,
       event_bus: EventBus,
       ...
   ):
   ```

2. Replace camera calls:
   ```python
   # Before
   self.camera.start_streaming()

   # After
   self._camera_service.start_streaming(self._stream_handler.on_new_frame)
   ```

3. Replace illumination calls:
   ```python
   # Before
   if self.microscope.illumination_controller:
       self.microscope.illumination_controller.turn_on_illumination(wavelength)

   # After
   self._illumination_service.turn_on(channel)
   ```

### Phase 4: MultiPointWorker Refactoring

1. Update constructor to receive services:
   ```python
   def __init__(
       self,
       camera_service: CameraService,
       stage_service: StageService,
       illumination_service: IlluminationService,
       peripheral_service: PeripheralService,
       event_bus: EventBus,
       ...
   ):
   ```

2. Replace camera operations:
   ```python
   # Before
   self.camera.start_streaming()
   self.camera.send_trigger(illumination_time)

   # After
   self._camera_service.start_streaming(self._image_callback)
   self._camera_service.send_trigger()
   ```

3. Replace stage operations:
   ```python
   # Before
   self.stage.move_x_to(x_mm)
   self.stage.move_y_to(y_mm)
   self.stage.move_z_to(z_mm)
   self.stage.wait_for_idle()

   # After
   self._stage_service.move_to_blocking(x=x_mm, y=y_mm, z=z_mm)
   ```

4. Replace microcontroller operations:
   ```python
   # Before
   self.microcontroller.enable_joystick(False)

   # After
   self._peripheral_service.enable_joystick(False)
   ```

5. Replace LiveController operations with events:
   ```python
   # Before
   self.liveController.set_microscope_mode(config)

   # After
   self._bus.publish(SetMicroscopeModeCommand(configuration_name=config.name))
   ```

### Phase 5: Widget Refactoring

1. Add EventBus to widget constructors:
   ```python
   def __init__(self, event_bus: EventBus, parent=None):
       self._bus = event_bus
   ```

2. Replace direct calls with event publishing:
   ```python
   # Before
   self.stage.move_x(distance)

   # After
   self._bus.publish(MoveStageCommand(axis='x', distance_mm=distance))
   ```

3. Subscribe to state events for feedback:
   ```python
   self._bus.subscribe(StagePositionChanged, self._on_position_changed)
   ```

---

## Verification Commands

After each refactoring, verify no direct access remains:

### Phase 3 Verification (LiveController)
```bash
# Should return NO matches
grep -n "self\.camera\." control/core/display/live_controller.py
grep -n "self\.microscope\." control/core/display/live_controller.py
```

### Phase 4 Verification (MultiPointWorker)
```bash
# Should return NO matches
grep -n "self\.camera\." control/core/acquisition/multi_point_worker.py
grep -n "self\.stage\." control/core/acquisition/multi_point_worker.py
grep -n "self\.microcontroller\." control/core/acquisition/multi_point_worker.py
grep -n "self\.liveController\." control/core/acquisition/multi_point_worker.py
```

### Phase 5 Verification (Widgets)
```bash
# Should return NO matches (or only type annotations)
grep -rn "self\.stage\." control/widgets/
grep -rn "self\.camera\." control/widgets/
grep -rn "self\.microcontroller\." control/widgets/
grep -rn "\.liveController\." control/widgets/
```

---

## Summary Statistics

| Component | Direct Camera | Direct Stage | Direct MCU | Direct Illumination | Total |
|-----------|---------------|--------------|------------|---------------------|-------|
| LiveController | ~12 | 0 | ~1 | ~15 | ~28 |
| MultiPointWorker | ~14 | ~13 | ~5 | ~3 | ~35 |
| MultiPointController | ~12 | ~7 | 0 | 0 | ~19 |
| AutoFocusController | ~3 | ~8 | 0 | 0 | ~11 |
| LaserAutofocusController | ~10 | ~1 | ~20 | 0 | ~31 |
| TrackingController | ~5 | ~3 | ~2 | 0 | ~10 |
| Widgets (estimate) | 0 | ~4 | 0 | 0 | ~4 |
| **Total** | **~56** | **~36** | **~28** | **~18** | **~138** |

Approximately **140 direct hardware calls** remain to be replaced with service/controller calls.
