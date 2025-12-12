# Event Catalog for Thread Safety

This document catalogs all events in `squid/events.py` by their source thread and consumers.
This is critical for determining which events need UIEventBus marshalling for thread-safe widget updates.

## Thread Categories

1. **Main Thread (GUI)**: Qt event loop, widget interactions
2. **Worker Threads**: Acquisition workers, autofocus workers, background timers
3. **Camera Thread**: Camera SDK callbacks
4. **Service Thread**: Service polling timers (movement updates)

## Event Classification

### Legend

- **Source**: Where the event is published from
- **Consumers**: Who subscribes to this event
- **UIEventBus**: Whether widget handlers need UIEventBus (Yes if published from worker thread and consumed by widgets)

---

## Worker-Thread Events (REQUIRE UIEventBus for Widget Handlers)

These events are published from worker threads and are consumed by widgets. Widget handlers
MUST be subscribed via UIEventBus to ensure they run on the Qt main thread.

### Acquisition Events (from MultiPointWorker)

| Event | Source Thread | Widget Consumers | UIEventBus |
|-------|---------------|------------------|------------|
| `AcquisitionStarted` | MultiPointWorker | FlexibleMultiPointWidget, WellplateMultiPointWidget | **Yes** |
| `AcquisitionFinished` | MultiPointWorker | FlexibleMultiPointWidget, WellplateMultiPointWidget, NapariMosaicDisplayWidget | **Yes** |
| `AcquisitionProgress` | MultiPointWorker | Progress bars in acquisition widgets | **Yes** |
| `AcquisitionRegionProgress` | MultiPointWorker | Progress bars in acquisition widgets | **Yes** |
| `AcquisitionStateChanged` | MultiPointWorker | Multiple acquisition widgets | **Yes** |
| `AcquisitionPaused` | MultiPointWorker | Acquisition widgets | **Yes** |
| `AcquisitionResumed` | MultiPointWorker | Acquisition widgets | **Yes** |

### Autofocus Events (from AutoFocusController worker)

| Event | Source Thread | Widget Consumers | UIEventBus |
|-------|---------------|------------------|------------|
| `AutofocusProgress` | Autofocus worker thread | AutoFocusWidget | **Yes** |
| `AutofocusCompleted` | Autofocus worker thread | AutoFocusWidget, acquisition widgets | **Yes** |
| `FocusChanged` | Autofocus worker thread | NavigationWidget, PiezoWidget | **Yes** |

### Laser Autofocus Events (from LaserAFController worker)

| Event | Source Thread | Widget Consumers | UIEventBus |
|-------|---------------|------------------|------------|
| `LaserAFInitialized` | LaserAF worker | LaserAutofocusControlWidget | **Yes** |
| `LaserAFReferenceSet` | LaserAF worker | LaserAutofocusControlWidget | **Yes** |
| `LaserAFDisplacementMeasured` | LaserAF worker | LaserAutofocusSettingWidget | **Yes** |
| `LaserAFPropertiesChanged` | LaserAF worker | LaserAutofocusSettingWidget | **Yes** |
| `LaserAFFrameCaptured` | LaserAF worker | ImageDisplayWindow (focus camera) | **Yes** |

### Stage Movement Events (from MovementService/MovementUpdater timer)

| Event | Source Thread | Widget Consumers | UIEventBus |
|-------|---------------|------------------|------------|
| `StagePositionChanged` | Movement polling timer | NavigationWidget, NavigationViewer | **Yes** |
| `StageMovedTo` | Stage movement thread | NavigationWidget, NavigationViewer | **Yes** |
| `PiezoPositionChanged` | Movement polling timer | PiezoWidget | **Yes** |

### Camera Events (from camera callbacks/services)

| Event | Source Thread | Widget Consumers | UIEventBus |
|-------|---------------|------------------|------------|
| `ExposureTimeChanged` | Camera service/callback | CameraSettingsWidget, LiveControlWidget | **Yes** |
| `AnalogGainChanged` | Camera service/callback | CameraSettingsWidget, LiveControlWidget | **Yes** |
| `ROIChanged` | Camera service | CameraSettingsWidget | **Yes** |
| `BinningChanged` | Camera service | CameraSettingsWidget, NavigationViewer | **Yes** |
| `PixelFormatChanged` | Camera service | CameraSettingsWidget | **Yes** |

---

## Main-Thread Events (Safe without UIEventBus)

These events are published from the GUI main thread (user interactions). They are safe
to use with either EventBus or UIEventBus.

### Command Events (GUI -> Services/Controllers)

All command events are published from widget button clicks or value changes, which
happen on the main thread. These do NOT need UIEventBus for their handlers (which
are typically services/controllers).

| Event | Source | Handler |
|-------|--------|---------|
| `SetExposureTimeCommand` | CameraSettingsWidget, LiveControlWidget | CameraService |
| `SetAnalogGainCommand` | CameraSettingsWidget, LiveControlWidget | CameraService |
| `SetDACCommand` | DACControlWidget | PeripheralService |
| `MoveStageCommand` | NavigationWidget | StageService |
| `MoveStageToCommand` | NavigationViewer, WellSelectionWidget | StageService |
| `MoveStageRelativeCommand` | NavigationWidget | StageService |
| `HomeStageCommand` | NavigationWidget, StageUtils | StageService |
| `ZeroStageCommand` | NavigationWidget | StageService |
| `MoveStageToLoadingPositionCommand` | StageUtils | StageService |
| `MoveStageToScanningPositionCommand` | StageUtils | StageService |
| `SetIlluminationCommand` | LiveControlWidget | IlluminationService |
| `StartLiveCommand` | LiveControlWidget | LiveController |
| `StopLiveCommand` | LiveControlWidget | LiveController |
| `StartAcquisitionCommand` | Acquisition widgets | MultiPointController |
| `StopAcquisitionCommand` | Acquisition widgets | MultiPointController |
| `StartAutofocusCommand` | AutoFocusWidget | AutoFocusController |
| `StopAutofocusCommand` | AutoFocusWidget | AutoFocusController |
| `SetFilterPositionCommand` | FilterControllerWidget | FilterWheelService |
| `SetObjectiveCommand` | ObjectivesWidget | ObjectiveChangerService |
| `SetPiezoPositionCommand` | PiezoWidget | PiezoService |
| `MovePiezoRelativeCommand` | PiezoWidget | PiezoService |
| `SetMicroscopeModeCommand` | LiveControlWidget | MicroscopeModeController |
| `UpdateChannelConfigurationCommand` | LiveControlWidget | ChannelConfigurationManager |
| `SetTriggerModeCommand` | LiveControlWidget | TriggerService |
| `SetTriggerFPSCommand` | LiveControlWidget | TriggerService |
| `SetROICommand` | CameraSettingsWidget | CameraService |
| `SetBinningCommand` | CameraSettingsWidget | CameraService |
| `SetPixelFormatCommand` | CameraSettingsWidget | CameraService |
| `StartCameraTriggerCommand` | LiveControlWidget | TriggerService |
| `StopCameraTriggerCommand` | LiveControlWidget | TriggerService |
| `SetCameraTriggerFrequencyCommand` | LiveControlWidget | TriggerService |
| `TurnOnAFLaserCommand` | LaserAutofocusControlWidget | LaserAutofocusController |
| `TurnOffAFLaserCommand` | LaserAutofocusControlWidget | LaserAutofocusController |
| `SetSpinningDiskPositionCommand` | SpinningDiskConfocalWidget | SpinningDiskService |
| `SetSpinningDiskSpinningCommand` | SpinningDiskConfocalWidget | SpinningDiskService |
| `SetDiskDichroicCommand` | SpinningDiskConfocalWidget | SpinningDiskService |
| `SetDiskEmissionFilterCommand` | SpinningDiskConfocalWidget | SpinningDiskService |
| `SetAcquisitionParametersCommand` | Acquisition widgets | MultiPointController |
| `SetAcquisitionPathCommand` | Acquisition widgets | MultiPointController |
| `SetAcquisitionChannelsCommand` | Acquisition widgets | MultiPointController |
| `SetAutofocusParamsCommand` | AutoFocusWidget | AutoFocusController |
| `SetLaserAFPropertiesCommand` | LaserAutofocusSettingWidget | LaserAutofocusController |
| `InitializeLaserAFCommand` | LaserAutofocusControlWidget | LaserAutofocusController |
| `SetLaserAFReferenceCommand` | LaserAutofocusControlWidget | LaserAutofocusController |
| `MeasureLaserAFDisplacementCommand` | LaserAutofocusControlWidget | LaserAutofocusController |
| `MoveToLaserAFTargetCommand` | LaserAutofocusControlWidget | LaserAutofocusController |
| `HomeFilterWheelCommand` | FilterControllerWidget | FilterWheelService |
| `SetFilterAutoSwitchCommand` | FilterControllerWidget | FilterWheelService |
| `SetTrackingParametersCommand` | TrackingControllerWidget | TrackingController |
| `StartTrackingCommand` | TrackingControllerWidget | TrackingController |
| `StopTrackingCommand` | TrackingControllerWidget | TrackingController |
| `SetPlateReaderParametersCommand` | PlateReaderWidget | PlateReaderController |
| `StartPlateReaderCommand` | PlateReaderWidget | PlateReaderController |
| `StopPlateReaderCommand` | PlateReaderWidget | PlateReaderController |
| `SetDisplacementMeasurementSettingsCommand` | DisplacementMeasurementWidget | DisplacementMeasurementController |
| `SetWaveformDisplayNCommand` | WaveformDisplay | DisplacementMeasurementController |

---

## State Events Consumed by Services/Controllers Only (No UIEventBus Needed)

These state events are consumed by backend services or controllers, not widgets.
They don't need UIEventBus.

| Event | Source | Handler |
|-------|--------|---------|
| `MicroscopeModeChanged` | MicroscopeModeController | LiveController, acquisition controllers |
| `ChannelConfigurationsChanged` | ChannelConfigurationManager | MicroscopeModeController |
| `LiveStateChanged` | LiveController | Acquisition controllers |
| `TriggerModeChanged` | TriggerService | LiveController |
| `TriggerFPSChanged` | TriggerService | LiveController |
| `DACValueChanged` | PeripheralService | Internal logging only |
| `IlluminationStateChanged` | IlluminationService | LiveController |

---

## State Events Consumed by Widgets (REQUIRE UIEventBus)

These are published from services (which may be on worker threads) and consumed by widgets.

| Event | Source | Widget Consumers | UIEventBus |
|-------|--------|------------------|------------|
| `FilterPositionChanged` | FilterWheelService | FilterControllerWidget | **Yes** |
| `FilterAutoSwitchChanged` | FilterWheelService | FilterControllerWidget | **Yes** |
| `ObjectiveChanged` | ObjectiveChangerService | ObjectivesWidget, NavigationViewer | **Yes** |
| `PixelSizeChanged` | ObjectiveChangerService, CameraService | NapariWidgets | **Yes** |
| `SpinningDiskStateChanged` | SpinningDiskService | SpinningDiskConfocalWidget | **Yes** |
| `CameraTemperatureChanged` | CameraService | CameraSettingsWidget | **Yes** |
| `BlackLevelChanged` | CameraService | CameraSettingsWidget | **Yes** |
| `AutoWhiteBalanceChanged` | CameraService | CameraSettingsWidget | **Yes** |
| `TrackingStateChanged` | TrackingController | TrackingControllerWidget | **Yes** |
| `PlateReaderStateChanged` | PlateReaderController | PlateReaderWidget | **Yes** |
| `PlateReaderLocationChanged` | PlateReaderController | PlateReaderWidget | **Yes** |
| `PlateReaderHomingComplete` | PlateReaderController | PlateReaderWidget | **Yes** |
| `PlateReaderAcquisitionFinished` | PlateReaderController | PlateReaderWidget | **Yes** |
| `DisplacementReadingsChanged` | DisplacementMeasurementController | DisplacementMeasurementWidget | **Yes** |
| `WellplateCalibrationSaved` | WellplateCalibrationService | WellplateCalibrationWidget | **Yes** |

---

## Summary Statistics

- **Total Events**: ~110+
- **Worker-Thread Events (Need UIEventBus)**: ~35-40
- **Main-Thread Command Events (Safe)**: ~50+
- **Service-Only State Events**: ~10
- **Widget State Events (Need UIEventBus)**: ~20

---

## Migration Priority

### Phase 1: Critical (Causes Crashes)
1. `StagePositionChanged` - from MovementUpdater timer
2. `PiezoPositionChanged` - from MovementUpdater timer
3. `AcquisitionStarted/Finished/Progress` - from MultiPointWorker
4. `AutofocusProgress/Completed` - from autofocus worker

### Phase 2: Important (Causes Glitches)
1. `ExposureTimeChanged` / `AnalogGainChanged` - from camera callbacks
2. `LaserAF*` events - from laser AF worker
3. `FilterPositionChanged` - from filter wheel service

### Phase 3: Polish
1. All remaining widget state events
2. Plate reader events
3. Tracking events

---

## Image Data: Separate Channel

**IMPORTANT**: Image data does NOT go through EventBus. High-frequency camera frames
(30-60 fps) would overwhelm the event system.

Instead, images flow through **StreamHandler** which uses Qt signals directly:
- `image_to_display` signal in StreamHandler, AutofocusController, MultiPointController
- These are already Qt signals which handle cross-thread marshalling

The EventBus carries **control plane** events (start/stop, progress, state changes).
StreamHandler carries **data plane** (actual image frames).

This separation is intentional and correct. Do NOT attempt to route images through EventBus.
