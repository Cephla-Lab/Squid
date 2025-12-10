# Phase 5C: Widget Interface Cleanup

## Overview

Phase 5B completed the EventBus migration for the major acquisition widgets. This phase addresses **all remaining direct service/controller access** in widgets to achieve a clean, consistent interface where:

1. **Widgets subscribe to events for ALL state** (both initial and updates)
2. **Widgets publish commands for all actions** (no direct method calls)
3. **No services or controllers are passed to widgets**
4. **Constructor params are only for truly static config** (e.g., UI layout options)

## Design Principle

**Widgets are pure UI** - they:
- Render state they receive via events
- Publish commands when users interact
- **NEVER** call methods on services or controllers
- **NEVER** receive services or controllers as constructor parameters
- **NEVER** query state from services or controllers

**Key distinction:**
- **Static config** (doesn't change at runtime): Can be passed via constructor (e.g., `num_filter_positions`, `exposure_limits`)
- **Dynamic state** (can change at runtime): MUST be received via events (e.g., `pixel_size_binned_um`, `current_position`, `current_objective`)

---

## PHASE 0: Identify Dynamic vs Static Values (CRITICAL)

Before implementing, we must correctly categorize each value:

### Dynamic Values (MUST use events)

These values can change at runtime and widgets MUST subscribe to events:

| Value | Changes When | Required Event |
|-------|--------------|----------------|
| `pixel_size_binned_um` | Camera binning changes | `BinningChanged` |
| `pixel_size_factor` | Objective changes | `ObjectiveChanged` |
| `current_objective` | User changes objective | `ObjectiveChanged` |
| `channel_configurations` | Objective changes | `ChannelConfigurationsChanged` (NEW) |
| `stage_position` | Stage moves | `StagePositionChanged` |
| `filter_position` | Filter wheel moves | `FilterPositionChanged` |
| `live_state` | Live view starts/stops | `LiveStateChanged` |
| `microscope_mode` | Mode changes | `MicroscopeModeChanged` |
| `laser_af_properties` | AF calibration changes | `LaserAFPropertiesChanged` |
| `acquisition_state` | Acquisition starts/stops | `AcquisitionStateChanged` |

### CRITICAL GAP: ObjectiveStore and ChannelConfigurationManager

**Many widgets still directly access these - this is a VIOLATION:**

1. **`objectiveStore.current_objective`** - Used in 8+ widgets to get current objective name
   - MUST subscribe to `ObjectiveChanged` and cache `objective_name`

2. **`objectiveStore.get_pixel_size_factor()`** - Used in 2+ widgets
   - MUST subscribe to `ObjectiveChanged` and cache `pixel_size_um`

3. **`channelConfigurationManager.get_channel_configurations_for_objective()`** - Used in 6+ widgets
   - MUST subscribe to new `ChannelConfigurationsChanged` event OR receive configs at construction

**Affected widgets:**
- `napari_live.py`
- `live_control.py`
- `tracking/controller.py`
- `flexible_multipoint.py`
- `fluidics_multipoint.py`
- `wellplate_multipoint.py`
- `objectives.py`

### Static Config (can use constructor params)

These values are fixed at startup and don't change:

| Value | Why Static | Pass Via |
|-------|------------|----------|
| `exposure_limits` | Camera hardware limits | Constructor |
| `num_filter_positions` | Hardware config | Constructor |
| `wheel_index` | UI config | Constructor |
| `widget_configuration` | UI layout option | Constructor |
| `mm_per_ustep` (x, y, z) | Stage hardware config (screw pitch, microstepping) | Constructor |

### Events That Were Created/Updated (COMPLETED)

The following events now exist in `squid/events.py`:

```python
# Camera state events - UPDATED to include pixel_size_binned_um
@dataclass
class BinningChanged(Event):
    """Notification that binning changed."""
    binning_x: int
    binning_y: int
    pixel_size_binned_um: Optional[float] = None  # Pixel size after binning

# Objective events - ALREADY EXISTED
@dataclass
class ObjectiveChanged(Event):
    """Objective lens changed."""
    position: int
    objective_name: Optional[str] = None
    magnification: Optional[float] = None
    pixel_size_um: Optional[float] = None
```

**Publishers:**
- `CameraService.set_binning()` - publishes `BinningChanged` with `pixel_size_binned_um`
- `ObjectiveStore.set_current_objective()` - publishes `ObjectiveChanged` (event_bus injected via ApplicationContext)

---

## Audit Summary (UPDATED)

| Category | Files Affected | Violations | Complexity | Dynamic Values | Status |
|----------|----------------|------------|------------|----------------|--------|
| Stage Service | 2 files | 6 violations | LOW | position (has event) | ✅ DONE |
| Camera Service | 3 files | 3 violations | **HIGH** | pixel_size | ✅ DONE |
| Filter Controller | 1 file | 2 violations | LOW | num_positions (static) | ✅ Already clean |
| Laser AF Controller | 1 file | 18+ violations | HIGH | properties (needs events) | ✅ DONE |
| Live Controller | 1 file | 1 violation | LOW | config (needs event) | ✅ DONE |
| Multipoint Controller | 3 files | ~90 calls | HIGH | state (needs events) | ✅ DONE |
| Tracking Controllers | 3 files | ~31 calls | HIGH | state (needs events) | ✅ DONE |
| Navigation Widget | 1 file | 3 violations | LOW | mm_per_ustep (static) | ✅ DONE (passed at construction) |
| **ObjectiveStore** | **7+ files** | **50+ violations** | **CRITICAL** | **current_objective** | ✅ DONE |
| **ChannelConfigMgr** | **6+ files** | **40+ violations** | **CRITICAL** | **configurations** | ✅ DONE |

---

## Category 0: Missing Events ✅ COMPLETED

Events have been created/updated and publishers configured:

### 0.1 Camera Events ✅

**File:** `squid/events.py` - `BinningChanged` updated to include `pixel_size_binned_um`

**Publisher:** `CameraService.set_binning()` publishes with pixel size.

### 0.2 Objective Events ✅

**File:** `squid/events.py` - `ObjectiveChanged` already existed with all needed fields.

**Publisher:** `ObjectiveStore.set_current_objective()` publishes event (event_bus injected via ApplicationContext).

### 0.3 Stage Config Events ✅ NOT NEEDED

`mm_per_ustep` values are **STATIC** - determined by hardware config (screw pitch, microstepping) that doesn't change at runtime. Can safely use constructor params.

---

## Category 1: Stage Service Violations

**Priority: HIGH** - These are direct hardware access patterns that should use EventBus.

**Note:** `StagePositionChanged` event already exists - widgets just need to subscribe.

### 1.1 focus_map.py (4 violations)

**File:** `control/widgets/display/focus_map.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 284 | `self._stage_service.get_position().z_mm` | Use `self._cached_z_mm` |
| 318 | `pos = self._stage_service.get_position()` | Use `self._cached_x_mm`, `_cached_y_mm`, `_cached_z_mm` |
| 392 | `self._stage_service.move_to(x_mm=x, y_mm=y, z_mm=z)` | `self._event_bus.publish(MoveStageCommand(...))` |
| 397 | `self._stage_service.get_position().z_mm` | Use `self._cached_z_mm` |

**Changes Required:**
- [x] Add imports: `from squid.events import EventBus, StagePositionChanged, MoveStageCommand`
- [x] Update constructor: remove `stage: AbstractStage`, remove `stage_service`, add `event_bus: EventBus`
- [x] Add cached position variables: `_cached_x_mm`, `_cached_y_mm`, `_cached_z_mm`
- [x] Subscribe to `StagePositionChanged` in constructor
- [x] Add `_on_stage_position_changed()` handler
- [x] Replace all service calls with cached values or commands
- [x] Remove `self._stage_service` attribute and `self.stage` attribute entirely

---

### 1.2 custom_multipoint.py (1 violation)

**File:** `control/widgets/custom_multipoint.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 143 | `pos = self._stage_service.get_position()` | Use cached position from parent |

**Changes Required:**
- [x] Inherits from FlexibleMultiPointWidget which already has cached position
- [x] Replace line 143: use `self._cached_x_mm`, `_cached_y_mm`, `_cached_z_mm`

---

### 1.3 wellplate/calibration.py (1 violation)

**File:** `control/widgets/wellplate/calibration.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 350 | `pos = self._stage_service.get_position()` (fallback) | Remove fallback entirely |

**Changes Required:**
- [x] Remove fallback path
- [x] Remove `_stage_service` parameter and attribute entirely
- [x] Show error if `_current_position` is None

---

## Category 2: Camera Service Violations ✅ COMPLETED

**Priority: HIGH** - These query dynamic values that change at runtime!

**CRITICAL:** `pixel_size_binned_um` changes when camera binning changes. Widgets MUST subscribe to `BinningChanged` event.

### 2.1 napari_live.py ✅

**File:** `control/widgets/display/napari_live.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 222 | `self._camera_service.get_exposure_limits()` | `exposure_limits` is STATIC - OK as constructor param |

**Changes Required:**
- [x] `exposure_limits` is static hardware config - can be constructor param
- [x] Remove `_camera_service` parameter and attribute entirely

---

### 2.2 napari_multichannel.py ✅ COMPLETED

**File:** `control/widgets/display/napari_multichannel.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 89 | `self._camera_service.get_pixel_size_binned_um()` | Subscribe to `BinningChanged` |

**Changes Completed:**
- [x] Add `_pixel_size_binned_um` cached state
- [x] Subscribe to `BinningChanged` event
- [x] Subscribe to `ObjectiveChanged` event for `_pixel_size_factor`
- [x] Add handlers to update cached values
- [x] Remove `_camera_service` and `objectiveStore` parameters entirely
- [x] Pass initial values via `initial_pixel_size_factor` and `initial_pixel_size_binned_um`
- [x] Update `gui_hcs.py` to pass `event_bus` and initial values

---

### 2.3 napari_mosaic.py ✅ COMPLETED

**File:** `control/widgets/display/napari_mosaic.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 197 | `self._camera_service.get_pixel_size_binned_um()` | Subscribe to `BinningChanged` |

**Changes Completed:**
- [x] Add `_pixel_size_binned_um` cached state
- [x] Subscribe to `BinningChanged` event
- [x] Subscribe to `ObjectiveChanged` event for `_pixel_size_factor`
- [x] Add handlers to update cached values
- [x] Remove `_camera_service` and `objectiveStore` parameters entirely
- [x] Pass initial values via `initial_pixel_size_factor` and `initial_pixel_size_binned_um`
- [x] Update `gui_hcs.py` to pass `event_bus` and initial values

---

## Category 3: Filter Controller Violations

**Priority: MEDIUM** - `num_positions` is STATIC hardware config.

### 3.1 filter_controller.py (2 violations)

**File:** `control/widgets/hardware/filter_controller.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 48 | `self.filterController.get_filter_wheel_info(self.wheel_index)` | `num_positions` is STATIC - OK as constructor param |
| 107 | `self.filterController.get_filter_wheel_position()` | Remove - use cached position from events |

**Changes Required:**
- [x] `num_positions` is static hardware config - can be constructor param
- [x] Remove `update_position_from_controller()` method entirely
- [x] Remove "Get Position" button from UI
- [x] Remove `filterController` parameter and attribute entirely

---

## Category 3: Filter Controller Violations ✅ COMPLETED

**Priority: MEDIUM**

### 3.1 filter_controller.py (2 violations) ✅

**File:** `control/widgets/hardware/filter_controller.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 48 | `self.filterController.get_filter_wheel_info(self.wheel_index)` | Pass `num_positions` to constructor |
| 107 | `self.filterController.get_filter_wheel_position()` | Remove method entirely |

**Changes Completed:**
- [x] Add constructor parameter: `num_positions: int` (required)
- [x] Store as `self._num_positions = num_positions`
- [x] Remove try/except block at lines 46-52, use `self._num_positions` directly
- [x] Remove `update_position_from_controller()` method entirely (lines 100-119)
- [x] Remove "Get Position" button from UI and its connection
- [x] Remove `filterController` parameter and attribute entirely

---

## Category 4: Laser Autofocus Controller Violations ✅ COMPLETED

**Priority: HIGH** - Many violations, complex state management

### 4.1 laser_autofocus.py - LaserAutofocusSettingWidget ✅

**Changes Completed:**
- [x] Events already existed: `LaserAFPropertiesChanged`, `LaserAFDisplacementMeasured`, `LaserAFInitialized`, `LaserAFReferenceSet`
- [x] Add constructor parameter: `initial_properties: Dict[str, Any]` (from LaserAFConfig.model_dump())
- [x] Add constructor params: `initial_is_initialized`, `initial_characterization_mode`
- [x] Cache all properties in `_laser_af_properties`, `_is_initialized`, `_characterization_mode`
- [x] Subscribe to `LaserAFPropertiesChanged`, `LaserAFInitialized`, `LaserAFReferenceSet`, `LaserAFFrameCaptured`
- [x] Replace all `self.laserAutofocusController.*` reads with cached values (`_laser_af_properties.get(...)`)
- [x] Remove `laserAutofocusController` parameter and attribute entirely
- [x] Update `widget_factory.py` to pass initial values

### 4.2 laser_autofocus.py - LaserAutofocusControlWidget ✅

**Changes Completed:**
- [x] Add constructor params: `initial_is_initialized`, `initial_has_reference`
- [x] Cache state in `_is_initialized`, `_has_reference`
- [x] Subscribe to `LaserAFInitialized`, `LaserAFReferenceSet`, `LaserAFDisplacementMeasured`
- [x] Replace `signal_displacement_um.connect()` with `LaserAFDisplacementMeasured` event subscription
- [x] Replace all reads with cached values
- [x] Remove `laserAutofocusController` parameter and attribute entirely
- [x] Update `widget_factory.py` to pass initial values

---

## Category 5: Live Controller Violations ✅ COMPLETED

**Priority: LOW** - Single fallback path

### 5.1 napari_live.py (1 violation) ✅

**File:** `control/widgets/display/napari_live.py`

| Line | Current Code | Fix |
|------|--------------|-----|
| 91 | `self.liveController.currentConfiguration` (fallback) | Remove fallback, require parameter |

**Changes Completed:**
- [x] Make `initial_configuration` a required parameter (not Optional)
- [x] Remove fallback: `if initial_configuration is not None else self.liveController.currentConfiguration`
- [x] Remove `liveController` parameter and attribute entirely

---

## Category 6: Multipoint Controller Configuration

**Priority: HIGH** - Large scope, but clean pattern

**Files:**
- `acquisition/fluidics_multipoint.py` (~30 calls)
- `acquisition/flexible_multipoint.py` (~30 calls)
- `acquisition/wellplate_multipoint.py` (~30+ calls)

### Current Pattern (VIOLATION)
```python
self.multipointController.set_deltaZ(value)
self.multipointController.set_NZ(value)
self.multipointController.set_base_path(path)
self.multipointController.run_acquisition()
```

### Target Pattern (CLEAN)
```python
self._event_bus.publish(SetAcquisitionParametersCommand(delta_z=value, n_z=value))
self._event_bus.publish(SetAcquisitionPathCommand(base_path=path))
self._event_bus.publish(StartAcquisitionCommand())
```

### New Events Needed

```python
@dataclass(frozen=True)
class SetAcquisitionParametersCommand(Event):
    """Set acquisition parameters."""
    delta_z: Optional[float] = None
    n_z: Optional[int] = None
    n_x: Optional[int] = None
    n_y: Optional[int] = None
    delta_t: Optional[float] = None
    n_t: Optional[int] = None
    use_piezo: Optional[bool] = None
    use_autofocus: Optional[bool] = None
    use_reflection_af: Optional[bool] = None
    z_range: Optional[Tuple[float, float]] = None

@dataclass(frozen=True)
class SetAcquisitionPathCommand(Event):
    """Set acquisition save path."""
    base_path: str
    experiment_id: Optional[str] = None

@dataclass(frozen=True)
class SetAcquisitionChannelsCommand(Event):
    """Set channels for acquisition."""
    channel_names: List[str]

@dataclass(frozen=True)
class StartAcquisitionCommand(Event):
    """Start the acquisition."""
    pass

@dataclass(frozen=True)
class StopAcquisitionCommand(Event):
    """Stop/abort the acquisition."""
    pass

@dataclass(frozen=True)
class AcquisitionStateChanged(Event):
    """Acquisition state has changed."""
    in_progress: bool
    is_aborting: bool = False

@dataclass(frozen=True)
class AcquisitionProgressChanged(Event):
    """Acquisition progress update."""
    current_region: int
    total_regions: int
    current_time_point: int
    current_fov: int
    total_fovs: int
```

### Changes Completed for Each Acquisition Widget ✅

- [x] Remove `multipointController` parameter and attribute
- [x] Replace all `.set_*()` calls with `SetAcquisitionParametersCommand`
- [x] Replace `.set_base_path()` with `SetAcquisitionPathCommand`
- [x] Replace `.set_selected_configurations()` with `SetAcquisitionChannelsCommand`
- [x] Replace `.run_acquisition()` with `StartAcquisitionCommand`
- [x] Replace `.request_abort_acquisition()` with `StopAcquisitionCommand`
- [x] Replace `.acquisition_in_progress()` query with cached state from `AcquisitionStateChanged`
- [x] Replace Qt signal connections with EventBus subscriptions:
  - `acquisition_finished` → `AcquisitionStateChanged(in_progress=False)`
  - `signal_acquisition_progress` → `AcquisitionProgressChanged`
  - `signal_region_progress` → `AcquisitionProgressChanged`

---

## Category 7: Tracking Controller Configuration

**Priority: HIGH** - Same pattern as multipoint

**Files:**
- `tracking/controller.py` (~19 calls)
- `tracking/plate_reader.py` (~11 calls)
- `tracking/displacement.py` (1 call)

### New Events Needed

```python
@dataclass(frozen=True)
class SetTrackingParametersCommand(Event):
    """Set tracking parameters."""
    time_interval: Optional[float] = None
    enable_stage_tracking: Optional[bool] = None
    enable_autofocus: Optional[bool] = None
    save_images: Optional[bool] = None
    tracker_type: Optional[str] = None
    pixel_size: Optional[float] = None
    objective: Optional[str] = None

@dataclass(frozen=True)
class StartTrackingCommand(Event):
    """Start tracking."""
    experiment_id: str
    channels: List[str]

@dataclass(frozen=True)
class StopTrackingCommand(Event):
    """Stop tracking."""
    pass

@dataclass(frozen=True)
class TrackingStateChanged(Event):
    """Tracking state changed."""
    is_tracking: bool
```

### Changes Completed ✅

- [x] Remove controller parameters and attributes from widgets
- [x] Replace all method calls with appropriate command events
- [x] Subscribe to state change events for UI updates

---

## Category 8: ObjectiveStore and ChannelConfigurationManager Violations (CRITICAL GAP) ✅ COMPLETED

**Priority: CRITICAL** - These are pervasive violations across many widgets!

### 8.1 Problem Analysis

**`objectiveStore` is passed to and queried by many widgets:**
- They call `objectiveStore.current_objective` to get the current objective name
- They call `objectiveStore.get_pixel_size_factor()` to get pixel size
- They call `objectiveStore.objectives_dict` to enumerate objectives
- They call `objectiveStore.set_current_objective()` to change objectives

**`channelConfigurationManager` is passed to and queried by many widgets:**
- They call `get_channel_configurations_for_objective(objective)` to get available modes
- They call `get_channel_configuration_by_name(objective, name)` to get specific mode
- They call `update_configuration(objective, name, ...)` to update mode settings

### 8.2 Required Events

```python
# Already exists - just need to subscribe
@dataclass
class ObjectiveChanged(Event):
    position: int
    objective_name: Optional[str] = None
    magnification: Optional[float] = None
    pixel_size_um: Optional[float] = None

# NEW - needed for channel configurations
@dataclass
class ChannelConfigurationsChanged(Event):
    """Channel configurations for current objective have changed."""
    objective_name: str
    configurations: List[str]  # List of available configuration names

# NEW - for updating channel config from widget
@dataclass
class UpdateChannelConfigurationCommand(Event):
    """Command to update a channel configuration setting."""
    objective_name: str
    config_name: str
    exposure_time_ms: Optional[float] = None
    analog_gain: Optional[float] = None
    illumination_intensity: Optional[float] = None
```

### 8.3 Widgets Requiring Changes

| Widget | File | Uses objectiveStore | Uses channelConfigManager |
|--------|------|---------------------|---------------------------|
| NapariLiveWidget | `napari_live.py` | ✓ `current_objective` | ✓ get/update configs |
| LiveControlWidget | `live_control.py` | ✓ `current_objective` | ✓ get/update configs |
| TrackingControllerWidget | `tracking/controller.py` | ✓ `current_objective`, `objectives_dict` | ✓ get configs |
| FlexibleMultiPointWidget | `flexible_multipoint.py` | ✓ `current_objective` | ✓ get configs |
| FluidicsMultiPointWidget | `fluidics_multipoint.py` | ✓ `current_objective` | ✓ get configs |
| WellplateMultiPointWidget | `wellplate_multipoint.py` | ✓ multiple uses | ✓ get configs |
| ObjectivesWidget | `objectives.py` | ✓ all methods | - |

### 8.4 Changes Completed Per Widget ✅

**For ALL widgets using `objectiveStore.current_objective`:**
- [x] Remove `objectiveStore` constructor parameter
- [x] Add `_current_objective: str` cached state
- [x] Subscribe to `ObjectiveChanged` event
- [x] Update handler: `self._current_objective = event.objective_name`
- [x] Replace all `objectiveStore.current_objective` with `self._current_objective`

**For ALL widgets using `channelConfigurationManager`:**
- [x] Remove `channelConfigurationManager` constructor parameter
- [x] Add `_channel_configs: List[str]` cached state
- [x] Subscribe to `ChannelConfigurationsChanged` event
- [x] Pass initial configs via constructor `initial_channel_configs`
- [x] For config updates, publish `UpdateChannelConfigurationCommand`

**Special case - ObjectivesWidget:**
- [x] This widget CHANGES the objective, publishes `ObjectiveChanged` (already done)
- [x] Retains `ObjectiveStore` reference as it is the source of truth for objective management
- [x] This is intentional - ObjectivesWidget is the owner of objective state

---

## Implementation Order

### Phase 5C.1: Stage Service Cleanup (3 files, 6 violations) ✅ COMPLETED
1. [x] `focus_map.py` - 4 violations - Now uses cached position and publishes MoveStageToCommand
2. [x] `custom_multipoint.py` - 1 violation - Uses cached position from parent
3. [x] `wellplate/calibration.py` - 1 violation - Removed _stage_service, uses cached position

### Phase 5C.2: Camera Service Cleanup (3 files, 3 violations) ✅ COMPLETED
4. [x] `napari_live.py` - exposure_limits passed at construction
5. [x] `napari_multichannel.py` - Uses BinningChanged/ObjectiveChanged events, passes initial values
6. [x] `napari_mosaic.py` - Uses BinningChanged/ObjectiveChanged events, passes initial values

### Phase 5C.3: Filter Controller Cleanup (1 file, 2 violations) ✅ COMPLETED
7. [x] `filter_controller.py` - Removed filterController, uses num_positions constructor param

### Phase 5C.4: Live Controller Cleanup (1 file, 1 violation) ✅ COMPLETED
8. [x] `napari_live.py` - Removed liveController fallback, uses event-driven state

### Phase 5C.5: Laser AF Controller Cleanup (1 file, 18+ violations) ✅ COMPLETED
9. [x] Events already existed in `squid/events.py` (LaserAFPropertiesChanged, LaserAFInitialized, etc.)
10. [x] Update `LaserAutofocusSettingWidget` - receives initial_properties dict, caches values, subscribes to events
11. [x] Update `LaserAutofocusControlWidget` - receives initial state, caches values, subscribes to events
12. [x] `LaserAutofocusController` already publishes events

### Phase 5C.6: Acquisition Controller Cleanup (3 files, ~90 calls) ✅ COMPLETED
13. [x] Create acquisition command/state events in `squid/events.py`
    - `SetAcquisitionParametersCommand` - All acquisition parameters in one event
    - `SetAcquisitionPathCommand` - Save path
    - `SetAcquisitionChannelsCommand` - Channel selection
    - `StartNewExperimentCommand` - Create new experiment
    - `StartAcquisitionCommand` - Start acquisition
    - `StopAcquisitionCommand` - Stop/abort acquisition
    - `AcquisitionStateChanged` - State notifications (in_progress, is_aborting)
    - `AcquisitionRegionProgress` - Region progress updates
14. [x] Update `MultiPointController` to subscribe to commands and publish state
15. [x] Update `fluidics_multipoint.py` - Removed multipointController, objectiveStore, channelConfigurationManager
16. [x] Update `flexible_multipoint.py` - Removed multipointController, objectiveStore, channelConfigurationManager
17. [x] Update `wellplate_multipoint.py` - Removed multipointController, channelConfigurationManager (objectiveStore retained for coordinate save feature)

### Phase 5C.7: Tracking Controller Cleanup (3 files, ~31 calls) ✅ COMPLETED
18. [x] Create tracking command/state events in `squid/events.py`
    - `SetTrackingParametersCommand` - All tracking parameters (interval, enable_stage_tracking, enable_autofocus, save_images, tracker_type, pixel_size_um, objective)
    - `SetTrackingPathCommand` - Save path
    - `SetTrackingChannelsCommand` - Channel selection
    - `StartTrackingExperimentCommand` - Create new experiment
    - `StartTrackingCommand` - Start tracking
    - `StopTrackingCommand` - Stop tracking
    - `TrackingStateChanged` - State notifications (is_tracking)
    - Plate reader events: `SetPlateReaderParametersCommand`, `SetPlateReaderPathCommand`, `SetPlateReaderChannelsCommand`, `SetPlateReaderColumnsCommand`, `StartPlateReaderExperimentCommand`, `StartPlateReaderCommand`, `StopPlateReaderCommand`, `PlateReaderAcquisitionFinished`, `PlateReaderHomeCommand`, `PlateReaderMoveToCommand`, `PlateReaderHomingComplete`, `PlateReaderLocationChanged`
    - Displacement events: `SetDisplacementMeasurementSettingsCommand`, `SetWaveformDisplayNCommand`, `DisplacementReadingsChanged`
19. [x] Update `TrackingController` to subscribe to commands and publish state
20. [x] Update `tracking/controller.py` - Removed trackingController and objectiveStore, uses events via ObjectivesWidget.objectiveStore for pixel size calculation
21. [x] Update `tracking/plate_reader.py` - Removed plateReadingController, plateReaderNavigationController, configurationManager
22. [x] Update `tracking/displacement.py` - Removed displacementMeasurementController and waveformDisplay

### Phase 5C.8: ObjectiveStore & ChannelConfigManager Cleanup (7+ files, 50+ violations) ✅ COMPLETED
23. [x] Create `ChannelConfigurationsChanged` event in `squid/events.py`
24. [x] Create `UpdateChannelConfigurationCommand` event
25. [x] `SetObjectiveCommand` already exists - `ObjectivesWidget` already publishes `ObjectiveChanged`
26. [x] Update `ChannelConfigurationManager` to:
    - Accept optional `event_bus` in constructor
    - Subscribe to `UpdateChannelConfigurationCommand`
    - Publish `ChannelConfigurationsChanged` on `load_configurations()`
27. [x] Update widgets to subscribe instead of query:
    - [x] `napari_live.py` - Removed objectiveStore, channelConfigurationManager, stage; now uses cached `_current_objective`, `_channel_config_names`
    - [x] `live_control.py` - Removed objectiveStore, channelConfigurationManager; now uses `initial_configuration`, `initial_objective`, `initial_channel_configs`
    - [x] `tracking/controller.py` - Already completed in Phase 5C.7 (uses ObjectivesWidget.objectiveStore for pixel size)
    - [x] `flexible_multipoint.py` - Already completed in Phase 5C.6 (uses initial_channel_configs)
    - [x] `fluidics_multipoint.py` - Already completed in Phase 5C.6 (uses initial_channel_configs)
    - [x] `wellplate_multipoint.py` - Already completed in Phase 5C.6 (objectiveStore retained for coordinate save)
    - [x] `objectives.py` - Already publishes ObjectiveChanged events; retains ObjectiveStore as source of truth (this is intentional)

### Phase 5C.9: Stage Service Widget Cleanup ✅ COMPLETED
28. [x] `stage/utils.py` - `StageUtils` - Removed unused `stage_service` parameter
29. [x] `stage/navigation.py` - `NavigationWidget` - Removed `stage_service`, now passes `x_mm_per_ustep`, `y_mm_per_ustep`, `z_mm_per_ustep` at construction

---

## Constructor Signature Changes Summary

### focus_map.py - FocusMapWidget
```python
# Before
def __init__(self, stage, navigationViewer, scanCoordinates, focusMap, stage_service=None)

# After
def __init__(self, navigationViewer, scanCoordinates, focusMap, event_bus, initial_z_mm=0.0)
```

### napari_live.py - NapariLiveWidget ✅
```python
# Before
def __init__(self, streamHandler, stage, objectiveStore, channelConfigurationManager, contrastManager, ...)

# After
def __init__(self, streamHandler, contrastManager, exposure_limits, initial_configuration, initial_objective, initial_channel_configs, ...)
```

### filter_controller.py - FilterControllerWidget
```python
# Before
def __init__(self, filterController, event_bus, wheel_index=0, main=None)

# After
def __init__(self, event_bus, wheel_index=0, num_positions=7, initial_position=1, main=None)
```

### laser_autofocus.py - LaserAutofocusSettingWidget
```python
# Before
def __init__(self, streamHandler, laserAutofocusController, event_bus, exposure_limits, ...)

# After
def __init__(self, streamHandler, event_bus, initial_properties, exposure_limits, ...)
```

### Acquisition widgets (all 3) ✅
```python
# Before
def __init__(self, ..., multipointController, ...)

# After
def __init__(self, ..., event_bus, ...)
# No controller reference at all
```

### live_control.py - LiveControlWidget ✅
```python
# Before
def __init__(self, event_bus, streamHandler, objectiveStore, channelConfigurationManager, ...)

# After
def __init__(self, event_bus, streamHandler, initial_configuration, initial_objective, initial_channel_configs, ...)
```

---

## Verification Checklist

After completing all changes, these greps should return **no results**:

```bash
# No service calls
grep -r "_stage_service\." control/widgets/
grep -r "_camera_service\." control/widgets/

# No controller method calls (only signal connections temporarily allowed)
grep -r "\.filterController\." control/widgets/
grep -r "\.liveController\." control/widgets/
grep -r "\.laserAutofocusController\." control/widgets/
grep -r "\.multipointController\." control/widgets/
grep -r "\.trackingController\." control/widgets/
grep -r "\.plateReadingController\." control/widgets/
grep -r "\.plateReaderNavigationController\." control/widgets/
grep -r "\.displacementMeasurementController\." control/widgets/

# No direct store/manager access (CRITICAL!)
grep -r "\.objectiveStore\." control/widgets/
grep -r "\.channelConfigurationManager\." control/widgets/
grep -r "self\.objectiveStore" control/widgets/
grep -r "self\.channelConfigurationManager" control/widgets/
```

Final verification:
- [x] All widgets pass syntax check
- [ ] Application starts in simulation mode: `python main_hcs.py --simulation`
- [ ] All widget tests pass

## Notes on Remaining References

**Intentionally retained references (source of truth widgets):**
- `objectives.py` - Retains `ObjectiveStore` as this widget manages objective state
- `wellplate_multipoint.py` - Retains `objectiveStore` for coordinate save feature only
- `tracking/controller.py` - Retains `peripheral_service` for joystick button listener callback registration
