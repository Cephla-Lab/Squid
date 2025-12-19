# Headless GUI Coverage Matrix (Draft)

Columns: Widget -> UI actions -> EventBus commands -> Handler to assert -> Feature flags

## Core imaging + hardware
- LiveControlWidget (squid.ui.widgets.camera.live_control)
  - Actions: start/stop live, change trigger mode/FPS, switch channel, change exposure/gain/illumination
  - Commands: StartLiveCommand, StopLiveCommand, SetTriggerModeCommand, SetTriggerFPSCommand,
    SetMicroscopeModeCommand, UpdateChannelConfigurationCommand, AutoLevelCommand
  - Handler/state: LiveController (live state + trigger), ChannelConfigurationManager (config update)
  - Flags: none

- CameraSettingsWidget (squid.ui.widgets.camera.settings)
  - Actions: set exposure, gain, pixel format, ROI, binning, temp, black level, auto WB
  - Commands: SetExposureTimeCommand, SetAnalogGainCommand, SetPixelFormatCommand, SetROICommand,
    SetBinningCommand, SetCameraTemperatureCommand, SetBlackLevelCommand, SetAutoWhiteBalanceCommand
  - Handler/state: CameraService (camera config + state events)
  - Flags: CAMERA_TYPE, DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS

- NavigationWidget (squid.ui.widgets.stage.navigation)
  - Actions: relative moves + click-to-move toggle
  - Commands: MoveStageRelativeCommand
  - Handler/state: StageService (position updates)
  - Flags: ENABLE_CLICK_TO_MOVE_BY_DEFAULT

- StageUtils (squid.ui.widgets.stage.utils)
  - Actions: home axes, zero axes, toggle loading/scanning position
  - Commands: HomeStageCommand, ZeroStageCommand, MoveStageToLoadingPositionCommand,
    MoveStageToScanningPositionCommand, MoveStageToCommand, StartLiveCommand, StopLiveCommand
  - Handler/state: StageService, LiveController
  - Flags: HOMING_ENABLED_* (per axis)

- DACControlWidget (squid.ui.widgets.hardware.dac)
  - Actions: set DAC values
  - Commands: SetDACCommand
  - Handler/state: PeripheralService
  - Flags: USE_SEPARATE_MCU_FOR_DAC

- FilterControllerWidget (squid.ui.widgets.hardware.filter_controller)
  - Actions: set filter position, home, toggle auto-switch
  - Commands: SetFilterPositionCommand, HomeFilterWheelCommand, SetFilterAutoSwitchCommand
  - Handler/state: FilterWheelService, LiveController
  - Flags: USE_EMISSION_FILTER_WHEEL

- ObjectivesWidget (squid.ui.widgets.hardware.objectives)
  - Actions: select objective
  - Commands: ObjectiveChanged (direct publish), ObjectiveStore update
  - Handler/state: ObjectiveStore, ApplicationContext (objective change handling)
  - Flags: USE_XERYON (objective changer)

- PiezoWidget (squid.ui.widgets.stage.piezo)
  - Actions: set position, move relative
  - Commands: SetPiezoPositionCommand, MovePiezoRelativeCommand
  - Handler/state: PiezoService
  - Flags: HAS_OBJECTIVE_PIEZO

- Illumination widgets (squid.ui.widgets.hardware.led_matrix / confocal)
  - Actions: illumination intensity/laser control
  - Commands: SetIlluminationCommand, SetSpinningDiskPositionCommand, SetSpinningDiskSpinningCommand,
    SetDiskDichroicCommand, SetDiskEmissionFilterCommand
  - Handler/state: IlluminationService, SpinningDiskService
  - Flags: ENABLE_SPINNING_DISK_CONFOCAL, USE_DRAGONFLY, SUPPORT_SCIMICROSCOPY_LED_ARRAY

- NL5Widget (squid.ui.widgets.nl5)
  - Actions: set exposure delay, line speed, FOV, toggle bypass, start acquisition
  - Commands: direct NL5 addon calls (no EventBus)
  - Handler/state: NL5 addon + NL5Service (if used in controllers)
  - Flags: ENABLE_NL5, NL5_USE_DOUT, NL5_USE_AOUT

## Wellplate + navigation
- WellplateFormatWidget (squid.ui.widgets.wellplate.format)
  - Actions: change wellplate format, open calibration
  - Commands: WellplateFormatChanged, SaveWellplateCalibrationCommand
  - Handler/state: WellplateFormatChanged handlers, calibration flow
  - Flags: WELLPLATE_FORMAT, WELLPLATE_FORMAT_SETTINGS

- WellSelectionWidget / Well1536SelectionWidget
  - Actions: select wells, move to well
  - Commands: SelectedWellsChanged, MoveStageToCommand
  - Handler/state: StageService + acquisition widgets
  - Flags: WELLPLATE_FORMAT

- WellplateCalibration (squid.ui.widgets.wellplate.calibration)
  - Actions: start/stop live, stage moves, save calibration
  - Commands: StartLiveCommand, StopLiveCommand, MoveStageRelativeCommand,
    SaveWellplateCalibrationCommand
  - Handler/state: LiveController, StageService
  - Flags: wellplate format selection

- FocusMapWidget (squid.ui.widgets.display.focus_map)
  - Actions: add/remove focus points, toggle overlay, move to focus point
  - Commands: FocusPointOverlaySet, FocusPointOverlayVisibilityChanged,
    RequestScanCoordinatesSnapshotCommand, MoveStageToCommand
  - Handler/state: StageService + focus map manager
  - Flags: none

- NavigationViewer (squid.ui.widgets.display.navigation_viewer)
  - Actions: move stage via nav view, clear scan coords
  - Commands: MoveStageToCommand, ClearScanCoordinatesCommand
  - Handler/state: StageService, ScanCoordinates manager
  - Flags: USE_NAPARI_WELL_SELECTION (if applicable)

## Acquisition + multipoint
- FlexibleMultiPointWidget (squid.ui.widgets.acquisition.flexible_multipoint)
  - Actions: set path/params/channels, add/remove regions, start/stop acquisition,
    set laser AF reference, move to points
  - Commands: SetAcquisitionParametersCommand, SetAcquisitionPathCommand,
    SetAcquisitionChannelsCommand, StartNewExperimentCommand, StartAcquisitionCommand,
    StopAcquisitionCommand, AddFlexibleRegionCommand, AddFlexibleRegionWithStepSizeCommand,
    RemoveScanCoordinateRegionCommand, RenameScanCoordinateRegionCommand,
    UpdateScanCoordinateRegionZCommand, ClearScanCoordinatesCommand, SetLaserAFReferenceCommand,
    MoveStageCommand
  - Handler/state: MultiPointController + ScanCoordinates manager
  - Flags: ENABLE_FLEXIBLE_MULTIPOINT, SUPPORT_LASER_AUTOFOCUS

- WellplateMultiPointWidget (squid.ui.widgets.acquisition.wellplate_multipoint)
  - Actions: select wells/ROI, set path/params/channels, start/stop acquisition,
    manual shapes, sort scan coordinates, set laser AF reference
  - Commands: SetWellSelectionScanCoordinatesCommand, SetManualScanCoordinatesCommand,
    ManualShapeDrawingEnabledChanged, ManualShapesChanged, SortScanCoordinatesCommand,
    SetAcquisitionParametersCommand, SetAcquisitionPathCommand, SetAcquisitionChannelsCommand,
    StartNewExperimentCommand, StartAcquisitionCommand, StopAcquisitionCommand,
    ClearScanCoordinatesCommand, SetLaserAFReferenceCommand
  - Handler/state: MultiPointController + ScanCoordinates manager
  - Flags: ENABLE_WELLPLATE_MULTIPOINT

- TemplateMultiPointWidget (squid.ui.widgets.custom_multipoint)
  - Actions: template-based regions
  - Commands: AddTemplateRegionCommand, SetAcquisitionParametersCommand, StartAcquisitionCommand
  - Handler/state: MultiPointController
  - Flags: USE_TEMPLATE_MULTIPOINT

- MultiPointWithFluidicsWidget (squid.ui.widgets.acquisition.fluidics_multipoint)
  - Actions: set rounds/params, start/stop acquisition
  - Commands: SetFluidicsRoundsCommand, SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand, SetAcquisitionChannelsCommand,
    StartNewExperimentCommand, StartAcquisitionCommand, StopAcquisitionCommand
  - Handler/state: MultiPointController + FluidicsService
  - Flags: RUN_FLUIDICS

## Autofocus + focus lock
- AutoFocusWidget (squid.ui.widgets.stage.autofocus)
  - Actions: set params, start/stop autofocus
  - Commands: SetAutofocusParamsCommand, StartAutofocusCommand, StopAutofocusCommand
  - Handler/state: AutoFocusController
  - Flags: none

- LaserAutofocusSettingWidget / LaserAutofocusControlWidget (squid.ui.widgets.hardware.laser_autofocus)
  - Actions: initialize, set properties, set reference, measure displacement, move to target,
    capture laser AF frame, toggle focus camera live
  - Commands: SetLaserAFPropertiesCommand, InitializeLaserAFCommand,
    SetLaserAFCharacterizationModeCommand, UpdateLaserAFThresholdCommand,
    SetLaserAFReferenceCommand, MeasureLaserAFDisplacementCommand, MoveToLaserAFTargetCommand,
    CaptureLaserAFFrameCommand, StartLiveCommand(camera="focus"), StopLiveCommand(camera="focus")
  - Handler/state: LaserAutofocusController + LiveController (focus camera)
  - Flags: SUPPORT_LASER_AUTOFOCUS

## Recording
- RecordingWidget (squid.ui.widgets.camera.recording)
  - Actions: set path, set FPS/time limit, start/stop recording
  - Commands: direct StreamHandler/ImageSaver calls (no EventBus)
  - Handler/state: StreamHandler + ImageSaver
  - Flags: ENABLE_RECORDING

## Tracking (optional)
- TrackingControllerWidget / plate reader widgets
  - Actions: set tracking params/path/channels, start/stop tracking or plate reader
  - Commands: SetTrackingParametersCommand, SetTrackingPathCommand, SetTrackingChannelsCommand,
    StartTrackingExperimentCommand, StartTrackingCommand, StopTrackingCommand,
    SetPlateReaderParametersCommand, SetPlateReaderPathCommand, SetPlateReaderChannelsCommand,
    StartPlateReaderExperimentCommand, StartPlateReaderCommand, StopPlateReaderCommand
  - Handler/state: TrackingControllerCore
  - Flags: ENABLE_TRACKING
