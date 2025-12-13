# Actor Refactor Simplification — Appendix: GUI Signal/Command Matrix

This appendix is the “wiring contract” for the frontend. For each user-visible GUI action/feature, it specifies:

- **UI publishes (commands)**: what the widget must publish (via `UIEventBus.publish(...)`)
- **UI subscribes (state/progress)**: what the widget must subscribe to (via `UIEventBus.subscribe(...)`)
- **Data plane**: which StreamHandler/QtStreamHandler signals provide frames (never EventBus)
- **Forbidden legacy paths**: what must be deleted to avoid cheating/backcompat

If a behavior is not covered here, add it before implementing to avoid drift.

## Global rules (non-negotiable)

- Widgets publish commands; they do not call controllers/services/hardware directly.
- Widgets do not call other widgets directly (no cross-widget Qt signal “fan-out”).
- UI never publishes “UI state toggles” to drive itself. UI state derives from backend truth events.
- The only images the UI displays come from StreamHandler/QtStreamHandler signals.
- No Qt signal bridge classes are used to carry control-plane or acquisition display state.

## Legend

- **Bus**: publish/subscribe via `UIEventBus` (handlers run on Qt main thread).
- **State events**: published by controllers/services via core EventBus.
- **Data**: StreamHandler/QtStreamHandler, camera callback thread → throttled → UI.

---

## A) Acquisition (Wellplate / Flexible / Fluidics)

### A1. Start acquisition (WellplateMultiPointWidget / FlexibleMultiPointWidget / FluidicsMultiPointWidget)

**UI publishes (Bus commands)**
- `SetAcquisitionPathCommand(base_path=...)` (when user chooses directory)
- `SetAcquisitionChannelsCommand(channel_names=[...])`
- `SetAcquisitionParametersCommand(...)` (dz/nz/dt/nt/flags/z_range/focus_map/etc.)
- `StartNewExperimentCommand(experiment_id=...)`
- `StartAcquisitionCommand()` (or `StartAcquisitionCommand(acquire_current_fov=True)` for snaps)
- `StopAcquisitionCommand()` on stop button toggle

**UI subscribes (Bus state/progress)**
- `AcquisitionStateChanged(experiment_id, in_progress, is_aborting, ...)`
  - drives enabling/disabling controls, button label, tab gating, “finished” cleanup
- `AcquisitionProgress(experiment_id, current_round, total_rounds, current_fov, ..., progress_percent, eta_seconds)`
  - drives progress bar + ETA
- `AcquisitionRegionProgress(experiment_id, current_region, total_regions, ...)` (if used)
- `LoadingPositionReached` / `ScanningPositionReached` (if used for “disable start while moving”)

**Data plane**
- Acquisition frames for display (single-FOV snap + mosaic) must come from StreamHandler only.
  - UI subscribes to StreamHandler’s Qt signals in the display widgets (Napari or ImageDisplayWindow).
  - No multipoint callback bridge (no `MultiPointSignalBridge`).

**Forbidden legacy paths**
- Publishing `AcquisitionUIToggleCommand` (delete).
- Emitting `signal_acquisition_started` / `signal_acquisition_shape` / `signal_toggle_live_scan_grid` (delete).
- Any connection in `src/squid/ui/gui/signal_connector.py` of `signal_acquisition_started → main_window.toggleAcquisitionStart` (delete).
- Any `MultiPointController(..., on_new_image_fn=..., on_acquisition_start_fn=..., ...)` wiring (delete).

**Notes**
- UI must always transition based on backend `AcquisitionStateChanged` (authoritative), not on “button pressed”.

---

### A2. Snap Images button

**UI publishes**
- `SetAcquisitionChannelsCommand(...)`
- `SetAcquisitionParametersCommand(delta_z_um=0, n_z=1, delta_t_s=0, n_t=1, ...)`
- `StartNewExperimentCommand(experiment_id=...)` (or a dedicated `StartSnapExperimentCommand` if you prefer)
- `StartAcquisitionCommand(acquire_current_fov=True)`

**UI subscribes**
- Same as A1 (`AcquisitionStateChanged`, `AcquisitionProgress`) to restore UI on completion.

**Data plane**
- The snapped frame must appear via StreamHandler display path (not via callbacks).

**Forbidden**
- Any “special-case snap callbacks” from worker/controller to UI.

---

### A3. Acquisition display tabs / mosaic initialization

**UI publishes**
- Nothing. This is display state derived from backend state + stream metadata.

**UI subscribes**
- If you need to update tab layout based on selected channels/NZ:
  - Prefer an explicit backend state event such as `AcquisitionDisplayPlanChanged(config_names, nz, ...)`.
  - Alternatively derive from:
    - current UI selection (channels list) + `SetAcquisitionParametersCommand` values
    - and update on acquisition start (from backend `AcquisitionStateChanged(in_progress=True)`).

**Data plane**
- Mosaic layers are driven by the acquisition data-plane stream (AcquisitionStream/StreamHandler) + Napari widget logic.
- Placement/scale must come from `CaptureInfo` metadata (`position`, `physical_size_x_um/physical_size_y_um`), not from Objective/Binning side channels.

**Forbidden**
- `_multipoint_signal_bridge.signal_set_display_tabs` or other bridge-driven tab setup.

---

## B) Live view + camera settings

### B1. Start/stop live

**UI publishes**
- `StartLiveCommand(configuration=...)`
- `StopLiveCommand()`
- `SetTriggerModeCommand(...)`, `SetTriggerFPSCommand(...)` (if exposed)
- `UpdateIlluminationCommand(...)` (if exposed)

**UI subscribes**
- `LiveStateChanged(is_live, configuration, ...)`
- `TriggerModeChanged(...)`, `TriggerFPSChanged(...)` (if used)
- Camera state events as needed (`ExposureTimeChanged`, gain/pixel format/ROI events)

**Data plane**
- Live frames displayed via StreamHandler only.

**Forbidden**
- Widget→widget direct calls for camera settings (e.g., `LiveControlWidget.signal_newExposureTime → CameraSettingsWidget.set_exposure_time`).
  - Replace with command publish + state subscribe.

---

### B2. Exposure/gain/ROI/binning/pixel format

**UI publishes**
- `SetExposureTimeCommand(exposure_time_ms=...)`
- `SetAnalogGainCommand(gain=...)`
- `SetROICommand(...)`
- `SetBinningCommand(...)`
- `SetPixelFormatCommand(...)`
- Any temperature/black-level commands if present

**UI subscribes**
- Corresponding `*Changed` events from `CameraService`

**Forbidden**
- Direct service/hardware calls from widgets.

---

## C) Navigation + stage movement + click-to-move

### C1. Manual stage moves (buttons/joystick/navigation)

**UI publishes**
- `MoveStageCommand(axis="x|y|z", distance_mm=...)`
- `MoveStageRelativeCommand(x_mm=..., y_mm=..., z_mm=...)`
- `MoveStageToCommand(x_mm=..., y_mm=..., z_mm=...)`
- `HomeStageCommand(...)`, `ZeroStageCommand(...)`
- Loading/scanning position commands (without callbacks):
  - `MoveStageToLoadingPositionCommand(blocking=...)`
  - `MoveStageToScanningPositionCommand(blocking=...)`

**UI subscribes**
- `StagePositionChanged(...)`
- `StageMovementStopped(...)` (optional; overlays may use `StagePositionChanged` with widget-side debouncing)
- `LoadingPositionReached`, `ScanningPositionReached`

**Forbidden**
- Any callback fields on stage commands.
- Any `StageService.move_to_*_position(..., callback=...)` use from UI.

---

### C2. Click-to-move (image click)

**UI publishes**
- `ImageCoordinateClickedCommand(x_pixel, y_pixel, image_width, image_height, from_napari=...)`
- (Optional) `ClickToMoveEnabledChanged(enabled=...)` is **forbidden**; prefer backend-published state:
  - If you need a “click-to-move enabled” state, create `ClickToMoveStateChanged(enabled=...)` produced by a backend controller/service.

**UI subscribes**
- `ClickToMoveStateChanged(enabled=...)` (backend truth), not a UI toggle command.

**Backend**
- `ImageClickController` subscribes to `ImageCoordinateClickedCommand` and publishes `MoveStageCommand`.

**Forbidden**
- `main_window.move_from_click_image` doing coordinate math.
- Any secondary routing thread for control-plane commands.

---

## D) Wellplate format + well selector + scan coordinates

### D1. Wellplate format selection

**UI publishes**
- One command only (new/renamed as needed): `SetWellplateFormatCommand(format_name=...)`
  - (Audit: `WellplateConfigurationCommand` currently exists but is unused; either wire it as the canonical command or delete it and introduce one canonical name.)

**UI subscribes**
- `WellplateFormatChanged(format_name, ...)` (if you have it)
- `ScanCoordinatesUpdated(total_regions, total_fovs, region_ids)`
- Navigation viewer state events (if you have them): `NavigationViewerStateChanged(...)`

**Forbidden**
- Qt fan-out chains:
  - `WellplateFormatWidget.signalWellplateSettings → navigationViewer.update_wellplate_settings`
  - `... → scanCoordinates.update_wellplate_settings`
  - `... → wellSelectionWidget.onWellplateChanged`
  - `... → main_window.onWellplateChanged`

---

### D2. Well selection

**UI publishes**
- Well selection command (create if missing): `SelectWellCommand(well_id, ...)` or similar.

**UI subscribes**
- State events that indicate current well selection / region list updates.

**Forbidden**
- Direct calls from selection widget into multipoint widgets (`update_well_coordinates`) via Qt signals.

---

### D3. Scan coordinate editing (add/remove regions/FOVs)

**UI publishes**
- `AddScanRegionCommand(...)`
- `RemoveScanRegionCommand(region_id=...)`
- `ClearScanCoordinatesCommand()`
- `AddFovToRegionCommand(...)` / `RemoveFovFromRegionCommand(...)`
  - (Use whatever command names your ScanCoordinates backend service/controller defines; the key is “commands only”.)

**UI subscribes**
- `ScanCoordinatesUpdated(...)`
- Any finer-grained events if needed (optional).

**Forbidden**
- `ScanCoordinates.set_update_callback(...)` with UI callback objects.

---

## E) Focus map

### E1. Build/update focus map surface

**UI publishes**
- Commands describing the focus-map inputs, not raw objects:
  - `SetFocusMapModeCommand(...)`
  - `SetFocusMapPointsCommand(points=[...])`
  - `FitFocusMapSurfaceCommand()` (if the backend fits it)

**UI subscribes**
- `FocusMapStateChanged(...)` / `FocusMapFitResult(...)`

**Forbidden**
- Passing a `focus_map` Python object from UI into `SetAcquisitionParametersCommand(focus_map=...)`.
  - The UI should not pass executable objects into backend; pass serializable data only.

---

## F) Laser autofocus

### F1. Initialize / set reference / measure displacement

**UI publishes**
- Laser AF command events already present (Set properties, initialize, set reference, capture, measure)

**UI subscribes**
- Laser AF state events already present (`LaserAFInitialized`, `LaserAFReferenceSet`, `LaserAFDisplacementMeasured`, etc.)

**Data plane**
- Any “laser AF frame” display must use StreamHandler/QtStreamHandler, not controller Qt signals.

**Forbidden**
- Qt signals owned/emitted by the backend controller (`LaserAutofocusController` must be non-Qt).
- UI callback plumbing into controller.

---

## G) Plotting (Z plot, progress plots)

**UI publishes**
- Nothing.

**UI subscribes**
- Plot-driving events from backend:
  - Prefer `AcquisitionWorkerProgress` / `AcquisitionProgress`
  - If you need per-frame positions: publish a lightweight event like `AcquisitionFrameCaptured(position, z_index, channel)` (no image payload).

**Forbidden**
- `_multipoint_signal_bridge.signal_coordinates` or `.signal_acquisition_progress`.

---

## H) Tracking (major refactor required)

**Current state (violates architecture)**
- Backend tracking is Qt-based (`QObject`, `QThread`, signals), uses raw hardware and UI objects.

**Target**
- UI publishes tracking commands
- Backend tracking controller/service uses services only, publishes `TrackingStateChanged` + progress events
- Data plane frames via StreamHandler

---

## Verification checklist (use during implementation)

Grep-based “cheat detector”:
- `rg "signal_connector\\.connect_|signal_acquisition_started|signal_toggle_live_scan_grid|MultiPointSignalBridge|_multipoint_signal_bridge" src/squid/ui -S`
- `rg "on_new_image_fn|on_acquisition_start_fn|on_acquisition_finish_fn" src/squid -S`
- `rg "AcquisitionUIToggleCommand|ClickToMoveEnabledChanged" src/squid/ui -S`
- `rg "set_update_callback\\(|update_callback=" src/squid/ui -S`

Runtime acceptance:
- “Snap Images” shows a frame in the UI and resets controls on completion via `AcquisitionStateChanged`.
- “Start Acquisition” shows progress/ETA updates via `AcquisitionProgress`, disables/enables controls via `AcquisitionStateChanged`, and shows frames via StreamHandler only.
