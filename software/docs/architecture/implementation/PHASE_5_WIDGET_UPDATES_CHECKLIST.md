Phase 5 Widget Updates – Work Checklist
======================================

Use this checklist to track completion of the missing work identified during the review of `PHASE_5_WIDGET_UPDATES.md`.

Events & Base Wiring
- [x] Add missing event dataclasses in `squid/events.py` (absolute/relative stage commands, bare `HomeStageCommand`, `SaveWellplateCalibrationCommand`, `SetDACCommand`/`DACValueChanged` with 0–1 payloads); decide whether to keep the legacy axis/distance command for compatibility.
- [x] Ensure all widgets accept an injected `event_bus` (no global `event_bus` usage) while keeping subscription cleanup in `control/widgets/base.py`.

NavigationWidget
- [x] Update ctor to take `event_bus`; remove global bus usage.
- [x] Stop polling `StageService` for positions; rely on `StagePositionChanged` subscription.
- [x] Publish `MoveStageRelativeCommand` for jogs and `MoveStageCommand` for go-to; add home handler if needed; no direct stage/service writes.

LiveControlWidget
- [x] Accept injected `event_bus`.
- [x] Publish `StartLiveCommand`/`StopLiveCommand`, `SetTriggerModeCommand`, `SetTriggerFPSCommand`; subscribe to `LiveStateChanged`, `TriggerModeChanged`, `TriggerFPSChanged`, `MicroscopeModeChanged`.
- [x] Remove any controller/service references.

CameraSettingsWidget
- [x] Accept injected `event_bus`; publish `SetExposureTimeCommand`, `SetAnalogGainCommand`, `SetROICommand`, `SetBinningCommand`, `SetPixelFormatCommand`, `SetCameraTemperatureCommand`, `SetBlackLevelCommand`, `SetAutoWhiteBalanceCommand`.
- [x] Subscribe to camera state events (`ExposureTimeChanged`, `AnalogGainChanged`, `ROIChanged`, `BinningChanged`, `PixelFormatChanged`, `BlackLevelChanged`, `AutoWhiteBalanceChanged`, or a consolidated `CameraSettingsChanged` if added).
- [ ] Minimize direct `CameraService` use; only read-only queries if permitted.

AutoFocusWidget
- [x] Refactor to `EventBusFrame`/`EventBusWidget` with injected `event_bus`; remove direct `AutoFocusController`/`stage` usage.
- [x] Publish `SetAutofocusParamsCommand`, `StartAutofocusCommand`, `StopAutofocusCommand`; subscribe to `AutofocusProgress`/`AutofocusCompleted` for UI updates and re-enable controls.

TriggerControlWidget
- [x] Accept injected `event_bus`; replace Start/Stop camera trigger commands with `SetTriggerModeCommand`/`SetTriggerFPSCommand` (and live start/stop if required).
- [x] Subscribe to `TriggerModeChanged`/`TriggerFPSChanged` to sync UI; remove custom controller wiring.

WellplateCalibration & WellplateFormatWidget
- [x] Accept injected `event_bus`; drop direct stage writes; use `StagePositionChanged` for positions and publish `MoveStageCommand`/`MoveStageRelativeCommand` for moves.
- [x] Replace calibration saving with `SaveWellplateCalibrationCommand`.
- [x] Fix double `StartLiveCommand` when `was_live` is False; ensure `_stop_live_if_needed` behaves consistently.
- [ ] Keep format/CSV logic but avoid hardware/controller access.

StageUtils
- [x] Convert to `EventBusDialog` (or similar) with injected `event_bus`; publish `HomeStageCommand`, `ZeroStageCommand`, `MoveStageToLoadingPositionCommand`, `MoveStageToScanningPositionCommand`; replace direct live controller calls with live events.

DACControWidget
- [x] Accept injected `event_bus`; align `SetDACCommand` payload with 0–1 range and convert UI percent accordingly; subscribe to `DACValueChanged`.

Tests
- [x] Add `tests/unit/control/widgets/test_widget_events.py` to verify publishes for Navigation jog/go-to, LiveControl start/stop, Trigger mode/fps, CameraSettings changes (exposure/gain/ROI/binning/pixel format), Autofocus start/stop/params, DAC sliders.
- [ ] Add subscription tests for Navigation (`StagePositionChanged`), LiveControl (`LiveStateChanged`/trigger events), TriggerControl (`TriggerModeChanged`/`TriggerFPSChanged`), CameraSettings (camera state events), Autofocus (autofocus state), WellplateCalibration (`StagePositionChanged`/`LiveStateChanged`).
- [ ] Add “no direct hardware/controller attributes” assertions for each widget.
- [ ] Extend base-class tests to require `event_bus` ctor arg (TypeError without it) while keeping subscription cleanup coverage.

Verification
- [ ] Grep: no `self.stage`, `self.camera`, `liveController`, `AutoFocusController`, or global `event_bus` in widgets; constructors take `event_bus`.
- [ ] Run `NUMBA_DISABLE_JIT=1 pytest tests/unit/control/widgets/ -v`.
- [ ] Optional: `python main_hcs.py --simulation` smoke to confirm wiring.

Decisions Needed
- [ ] Clarify if read-only service calls are allowed; if not, emit events for limits/ROI/binning.
- [ ] Confirm trigger API direction (legacy Start/Stop vs `SetTriggerMode`/`SetTriggerFPS` only).
- [ ] Confirm DAC value scale (0–1 vs 0–100) and whether backward-compatible aliases are needed.
