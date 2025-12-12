# Phase 5B: Additional Widget EventBus Migration

## Overview

Phase 5 is complete for the core widgets. This plan addresses the **remaining widgets** that still have direct hardware/controller access. These are more complex widgets that were deferred from the initial Phase 5 scope.

## Audit Summary

| Widget | File | Violations | Complexity | Priority |
|--------|------|------------|------------|----------|
| FilterControllerWidget | `hardware/filter_controller.py` | 14 direct calls | LOW | **1 (First)** |
| NapariLiveWidget | `display/napari_live.py` | 4 violations | LOW-MEDIUM | **2** |
| LaserAutofocusSettingWidget | `hardware/laser_autofocus.py` | 13+ direct calls | HIGH | **3** |
| WellplateMultiPointWidget | `acquisition/wellplate_multipoint.py` | 20+ violations | VERY HIGH | **4 (Defer?)** |
| FlexibleMultiPointWidget | `acquisition/flexible_multipoint.py` | 15+ violations | HIGH | **5 (Defer?)** |
| FluidicsMultiPointWidget | `acquisition/fluidics_multipoint.py` | 5 violations | MEDIUM | **6 (Defer?)** |

---

## Priority 1: FilterControllerWidget (LOW complexity)

**File:** `control/widgets/hardware/filter_controller.py` (~150 lines)

### Current Violations

| Line | Violation | Severity |
|------|-----------|----------|
| 26 | `self.liveController = liveController` | HIGH |
| 34 | `self.filterController.get_filter_wheel_info()` | MEDIUM |
| 84 | `self.filterController.home()` | HIGH |
| 89-91 | `self.filterController.get_filter_wheel_position()` | HIGH |
| 106-108 | `self.filterController.set_filter_wheel_position()` | HIGH |
| 113-123 | Multiple `get/set_filter_wheel_position()` | HIGH |
| 131-139 | More `get/set_filter_wheel_position()` | HIGH |
| 147 | `self.liveController.enable_channel_auto_filter_switching = False` | HIGH |
| 149 | `self.liveController.enable_channel_auto_filter_switching = True` | HIGH |

### Events Needed

**Already exist:**
- `SetFilterPositionCommand` (squid/events.py:481)
- `FilterPositionChanged` (squid/events.py:543)

**New events needed:**
```python
@dataclass(frozen=True)
class HomeFilterWheelCommand(Event):
    """Command to home filter wheel."""
    wheel_index: int

@dataclass(frozen=True)
class SetFilterAutoSwitchCommand(Event):
    """Command to enable/disable automatic filter switching."""
    enabled: bool

@dataclass(frozen=True)
class FilterAutoSwitchChanged(Event):
    """State event: auto-switch mode changed."""
    enabled: bool
```

### Refactoring Steps

1. Inherit from `EventBusFrame`
2. Accept `event_bus` parameter
3. Keep `filterController` for read-only `get_filter_wheel_info()` (config query)
4. Replace `set_filter_wheel_position()` with `SetFilterPositionCommand`
5. Replace `home()` with `HomeFilterWheelCommand`
6. Subscribe to `FilterPositionChanged` to update UI
7. Replace liveController property writes with `SetFilterAutoSwitchCommand`
8. Remove `liveController` dependency

### Files to Modify

- `squid/events.py` - Add new events
- `control/widgets/hardware/filter_controller.py` - Full refactor
- `control/core/display/live_controller.py` - Subscribe to `SetFilterAutoSwitchCommand`
- `squid/services/filter_wheel_service.py` - Subscribe to `HomeFilterWheelCommand` (if exists)

---

## Priority 2: NapariLiveWidget (LOW-MEDIUM complexity)

**File:** `control/widgets/display/napari_live.py`

### Current Violations

| Line | Violation | Issue |
|------|-----------|-------|
| 87 | `self.liveController.currentConfiguration` | State access at init |
| 546 | `self.liveController.update_illumination()` | Direct method call |
| 550 | `self.liveController.set_display_resolution_scaling()` | Direct method call |
| 629 | `self.liveController.currentConfiguration.name` | State access in loop |

### Good Patterns Already Present

The widget already correctly uses EventBus in several places:
- Line 266: `event_bus.publish(SetTriggerFPSCommand(fps=fps))`
- Lines 436-438: `StartLiveCommand` / `StopLiveCommand`
- Line 501: `SetMicroscopeModeCommand`
- Lines 110-112: Subscribes to `LiveStateChanged`, `TriggerFPSChanged`, `MicroscopeModeChanged`

### Events Needed

**New events:**
```python
@dataclass(frozen=True)
class UpdateIlluminationCommand(Event):
    """Command to update illumination for current configuration."""
    pass  # Uses current configuration from controller

@dataclass(frozen=True)
class SetDisplayResolutionScalingCommand(Event):
    """Command to set display resolution scaling."""
    scaling: float  # 10-100 percentage
```

### Refactoring Steps

1. **Line 87**: Pass `initial_configuration` as constructor param OR rely on `MicroscopeModeChanged` subscription (already exists at line 455-459)
2. **Line 546**: Publish `UpdateIlluminationCommand` instead of direct call
3. **Line 550**: Publish `SetDisplayResolutionScalingCommand` instead of direct call
4. **Line 629**: Remove - trust event-synchronized state from `_on_microscope_mode_changed` handler

### Files to Modify

- `squid/events.py` - Add 2 new events
- `control/widgets/display/napari_live.py` - Replace 4 violations
- `control/core/display/live_controller.py` - Subscribe to new commands

---

## Priority 3: LaserAutofocusSettingWidget (HIGH complexity)

**File:** `control/widgets/hardware/laser_autofocus.py` (~692 lines)

### Current Violations (13+ HIGH severity)

| Line | Violation | Type |
|------|-----------|------|
| 66 | `self.liveController.set_trigger_fps(10)` | Method call |
| 67 | `self.streamHandler.set_display_fps(10)` | Method call |
| 101 | `self.liveController.microscope.camera.get_exposure_limits()` | Deep traversal |
| 338 | `self.liveController.start_live()` | Method call |
| 356 | `self.liveController.stop_live()` | Method call |
| 369 | `self.laserAutofocusController.characterization_mode = state` | Property write |
| 435 | `self.laserAutofocusController.set_laser_af_properties()` | Method call |
| 436 | `self.laserAutofocusController.initialize_auto()` | Method call |
| 474 | `self.laserAutofocusController.update_threshold_properties()` | Method call |
| 495 | `self.liveController.trigger_acquisition()` | Method call |
| 498 | `self.liveController.camera.read_frame()` | Hardware access |
| 666-691 | Multiple direct calls in button handlers | Method calls |

### Good Pattern Already Present

- Lines 494, 500: Correctly uses `TurnOnAFLaserCommand` / `TurnOffAFLaserCommand`

### Events Needed

```python
@dataclass(frozen=True)
class SetLaserAFPropertiesCommand(Event):
    """Command to set laser AF properties."""
    properties: dict  # Property updates

@dataclass(frozen=True)
class InitializeLaserAFCommand(Event):
    """Command to initialize laser AF."""
    pass

@dataclass(frozen=True)
class SetLaserAFCharacterizationModeCommand(Event):
    """Command to set characterization mode."""
    enabled: bool

@dataclass(frozen=True)
class UpdateLaserAFThresholdCommand(Event):
    """Command to update threshold properties."""
    updates: dict

@dataclass(frozen=True)
class MoveToLaserAFTargetCommand(Event):
    """Command to move to AF target."""
    displacement: Optional[float] = None

@dataclass(frozen=True)
class SetLaserAFReferenceCommand(Event):
    """Command to set AF reference point."""
    pass

@dataclass(frozen=True)
class MeasureLaserAFDisplacementCommand(Event):
    """Command to measure displacement."""
    pass

@dataclass(frozen=True)
class LaserAFPropertiesChanged(Event):
    """State: AF properties changed."""
    properties: dict

@dataclass(frozen=True)
class LaserAFInitializationChanged(Event):
    """State: AF initialization status changed."""
    is_initialized: bool

@dataclass(frozen=True)
class TriggerSingleAcquisitionCommand(Event):
    """Command to trigger single frame acquisition."""
    pass
```

### Refactoring Steps

1. Inherit from `EventBusFrame`
2. Pass exposure limits as constructor param (from camera at GUI init)
3. Replace `start_live()`/`stop_live()` with existing `StartLiveCommand`/`StopLiveCommand`
4. Create command events for all AF operations
5. Subscribe to state events for UI updates
6. For `illuminate_and_get_frame()` workflow (lines 491-502):
   - Option A: Move to LaserAutofocusController and expose via event
   - Option B: Keep as is but document as acceptable (specialized hardware workflow)
7. Remove direct liveController and laserAutofocusController references

### Files to Modify

- `squid/events.py` - Add ~10 new events
- `control/widgets/hardware/laser_autofocus.py` - Major refactor
- `control/core/autofocus/laser_autofocus_controller.py` - Subscribe to commands

---

## Priority 4: FluidicsMultiPointWidget (MEDIUM complexity)

**File:** `control/widgets/acquisition/fluidics_multipoint.py`

### Current Violations

| Line | Violation | Severity |
|------|-----------|----------|
| 60 | `self.stage = stage` | MEDIUM |
| 61 | `self._stage_service = stage_service` | MEDIUM |
| 306 | `self.multipointController.fluidics.set_rounds(rounds)` | HIGH |
| 350 | `self.stage.get_config().Z_AXIS.convert_real_units_to_ustep()` | HIGH |

### Events Needed

```python
@dataclass(frozen=True)
class SetFluidicsRoundsCommand(Event):
    """Command to set fluidics rounds."""
    rounds: int
```

### Refactoring Steps

1. Inherit from `EventBusFrame`
2. Remove direct `stage` reference
3. Replace `fluidics.set_rounds()` with `SetFluidicsRoundsCommand`
4. Cache Z-axis config at init or query via service
5. Keep `stage_service` for read-only queries (acceptable)

---

## Priority 5: FlexibleMultiPointWidget (HIGH complexity)

**File:** `control/widgets/acquisition/flexible_multipoint.py`

### Current Violations

| Line | Violation | Severity |
|------|-----------|----------|
| 68 | `self.stage = stage` | MEDIUM |
| 69 | `self._stage_service = stage_service` | MEDIUM |
| 286, 303, 606, 622, 639, 655, 660, 682, 874, 986, 1392 | `stage_service.get_position()` (9 instances) | MEDIUM |
| 1169 | `stage_service.move_to()` | HIGH |
| 826 | `stage.get_config().Z_AXIS.convert_real_units_to_ustep()` | HIGH |
| 675 | `multipointController.laserAutoFocusController.set_reference()` | HIGH |

### Refactoring Steps

1. Inherit from `EventBusFrame`
2. Subscribe to `StagePositionChanged` to cache position instead of 9 `get_position()` calls
3. Replace `stage_service.move_to()` with `MoveStageCommand`
4. Replace `laserAutoFocusController.set_reference()` with `SetLaserAFReferenceCommand`
5. Cache Z-axis config at init
6. Remove direct `stage` reference

---

## Priority 6: WellplateMultiPointWidget (VERY HIGH complexity)

**File:** `control/widgets/acquisition/wellplate_multipoint.py`

### Current Violations

| Line | Violation | Severity |
|------|-----------|----------|
| 76 | `self.liveController = liveController` | HIGH |
| 77 | `self.stage = stage` | MEDIUM |
| 1701 | `self.liveController.is_live` | HIGH |
| 1703 | `self.liveController.stop_live()` | HIGH |
| 1709 | `self.liveController.start_live()` | HIGH |
| 1688 | `stage_service.move_to(z_mm=z_mm)` | HIGH |
| 1704 | `multipointController.laserAutoFocusController.set_reference()` | HIGH |
| 2015 | `stage.get_config().Z_AXIS.convert_real_units_to_ustep()` | HIGH |
| Multiple | `stage_service.get_position()` calls | MEDIUM |

### Refactoring Steps

1. Inherit from `EventBusFrame`
2. Subscribe to `LiveStateChanged` to track `is_live` state
3. Replace `liveController.start_live()`/`stop_live()` with `StartLiveCommand`/`StopLiveCommand`
4. Replace `stage_service.move_to()` with `MoveStageCommand`
5. Replace `laserAutoFocusController.set_reference()` with `SetLaserAFReferenceCommand`
6. Subscribe to `StagePositionChanged` for position tracking
7. Cache Z-axis config at init
8. Remove direct `liveController` and `stage` references

---

## Implementation Order

### Phase 5B.1: FilterControllerWidget
- Add 3 events
- Simple 1:1 replacement of method calls with events
- ~2 hours work

### Phase 5B.2: NapariLiveWidget
- Add 2 events
- Fix 4 violations while preserving existing good patterns
- ~2 hours work

### Phase 5B.3: LaserAutofocusSettingWidget
- Add ~10 events + FocusCameraService
- Complex refactor with specialized hardware workflow
- ~8 hours work (including new service)

### Phase 5B.4: FluidicsMultiPointWidget
- Add 1 event
- Minimal changes needed
- ~2 hours work

### Phase 5B.5: FlexibleMultiPointWidget
- Reuse existing events
- Position caching pattern
- ~4 hours work

### Phase 5B.6: WellplateMultiPointWidget
- Reuse existing events
- Most complex due to size
- ~6 hours work

---

## New Service: FocusCameraService

**File:** `squid/services/focus_camera_service.py` (NEW)

The laser autofocus widget needs to capture single frames from the focus camera for calibration. This currently bypasses the EventBus by directly calling `liveController.camera.read_frame()`.

### Service Design

```python
class FocusCameraService(BaseService):
    """Service for focus camera operations (laser autofocus)."""

    def __init__(self, camera: AbstractCamera, event_bus: EventBus):
        super().__init__(event_bus)
        self._camera = camera
        self._subscribe(TriggerSingleAcquisitionCommand, self._on_trigger_acquisition)

    def _on_trigger_acquisition(self, event: TriggerSingleAcquisitionCommand) -> None:
        """Capture single frame and publish result."""
        frame = self._camera.read_frame()
        self._publish(SingleFrameCaptured(frame=frame))

    def get_exposure_limits(self) -> tuple[float, float]:
        """Read-only: get camera exposure limits."""
        return self._camera.get_exposure_limits()
```

### Events for Service

```python
@dataclass(frozen=True)
class TriggerSingleAcquisitionCommand(Event):
    """Command to trigger single frame acquisition."""
    pass

@dataclass(frozen=True)
class SingleFrameCaptured(Event):
    """State: single frame captured."""
    frame: Any  # CameraFrame
```

---

## Verification Commands

```bash
# After each widget refactor:
grep -n "\.liveController\." control/widgets/hardware/filter_controller.py
grep -n "\.liveController\." control/widgets/display/napari_live.py
grep -n "\.liveController\." control/widgets/hardware/laser_autofocus.py
grep -n "\.liveController\." control/widgets/acquisition/*.py

# Run tests
NUMBA_DISABLE_JIT=1 pytest tests/unit/control/widgets/ -v

# Test application
python main_hcs.py --simulation
```

---

## Summary of All Events to Create

### Filter Controller Events
- `HomeFilterWheelCommand(wheel_index: int)`
- `SetFilterAutoSwitchCommand(enabled: bool)`
- `FilterAutoSwitchChanged(enabled: bool)`

### Display Events
- `UpdateIlluminationCommand()`
- `SetDisplayResolutionScalingCommand(scaling: float)`

### Laser AF Events
- `SetLaserAFPropertiesCommand(properties: dict)`
- `InitializeLaserAFCommand()`
- `SetLaserAFCharacterizationModeCommand(enabled: bool)`
- `UpdateLaserAFThresholdCommand(updates: dict)`
- `MoveToLaserAFTargetCommand(displacement: Optional[float])`
- `SetLaserAFReferenceCommand()`
- `MeasureLaserAFDisplacementCommand()`
- `LaserAFPropertiesChanged(properties: dict)`
- `LaserAFInitializationChanged(is_initialized: bool)`
- `TriggerSingleAcquisitionCommand()`
- `SingleFrameCaptured(frame: Any)`

### Fluidics Events
- `SetFluidicsRoundsCommand(rounds: int)`

**Total: ~17 new events**

---

## Files to Modify

| File | Action |
|------|--------|
| `squid/events.py` | Add ~17 new events |
| `squid/services/focus_camera_service.py` | NEW - Focus camera service |
| `control/widgets/hardware/filter_controller.py` | Refactor to EventBusFrame |
| `control/widgets/display/napari_live.py` | Fix 4 violations |
| `control/widgets/hardware/laser_autofocus.py` | Major refactor |
| `control/widgets/acquisition/fluidics_multipoint.py` | Refactor to EventBusFrame |
| `control/widgets/acquisition/flexible_multipoint.py` | Refactor to EventBusFrame |
| `control/widgets/acquisition/wellplate_multipoint.py` | Refactor to EventBusFrame |
| `control/core/display/live_controller.py` | Subscribe to new commands |
| `control/core/autofocus/laser_autofocus_controller.py` | Subscribe to new commands |
| `control/gui/widget_factory.py` | Update widget instantiation |
| `squid/application.py` | Add FocusCameraService creation |

---

## Commit Sequence

1. `feat(events): Add filter controller events`
2. `refactor(widgets): Update FilterControllerWidget to use EventBus`
3. `feat(events): Add display events (illumination, scaling)`
4. `refactor(widgets): Update NapariLiveWidget to use EventBus`
5. `feat(services): Add FocusCameraService`
6. `feat(events): Add laser autofocus events`
7. `refactor(widgets): Update LaserAutofocusSettingWidget to use EventBus`
8. `feat(events): Add fluidics events`
9. `refactor(widgets): Update FluidicsMultiPointWidget to use EventBus`
10. `refactor(widgets): Update FlexibleMultiPointWidget to use EventBus`
11. `refactor(widgets): Update WellplateMultiPointWidget to use EventBus`
12. `test(widgets): Add tests for new widget event patterns`
