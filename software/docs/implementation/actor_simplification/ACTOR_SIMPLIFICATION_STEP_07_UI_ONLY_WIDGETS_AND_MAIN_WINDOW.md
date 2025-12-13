# Actor Refactor Simplification — Step 07: Finish UI Decoupling (Widgets + Main Window)

## Goal

Make the UI a pure frontend per your architecture:

- Widgets render state + publish commands only.
- No widget or main window holds controllers/services for business logic.
- All cross-widget wiring is via EventBus events.

This completes Phases 7–8 from `ACTOR_MODEL_REFACTOR.md`.

## Remaining work (from Phase 7.5/7.6/8.*)

### Complex acquisition widgets
- `src/squid/ui/widgets/acquisition/wellplate_multipoint.py`
- `src/squid/ui/widgets/acquisition/flexible_multipoint.py`
 - `src/squid/ui/widgets/acquisition/fluidics_multipoint.py`

Must remove:
- Direct `navigationViewer` references.
- Direct `scanCoordinates` references.
- Any direct controller calls.

Replace with:
- Subscribe to `ScanCoordinatesUpdated`, `NavigationViewerStateChanged`, `AcquisitionProgress`, etc. via UIEventBus.
- Publish only commands (StartNewExperimentCommand, SetAcquisitionParametersCommand, etc.).
- Do **not** publish UI-state “toggle” commands; UI state must follow backend truth via `AcquisitionStateChanged`.

Additionally (audit finding):

- These widgets still emit Qt signals “for backwards compatibility” alongside EventBus publishing.
  - Remove the Qt signals and any `signal_connector.py` glue once the EventBus path is verified end-to-end.

### Phase 8 artifacts to de-cruft (audit findings)

Phase 8 introduced some UI-decoupling events/files. As of audit:

- `AcquisitionUIToggleCommand` exists but should be removed as part of UI truth-from-backend cleanup (audit: UI-intent toggles can desync).
- `ImageClickController` is active and subscribes directly on the core `EventBus` (single control thread).
- The following appear **unused** and should be either wired fully or deleted:
  - `WellplateConfigurationCommand` (currently declared but no publisher/subscriber).
  - `LiveScanGridCommand`, `WellSelectorVisibilityCommand` (declared and only referenced by `AcquisitionUICoordinator`).
  - `src/squid/ui/acquisition_ui_coordinator.py` (not constructed anywhere; main window still owns UI transitions).

Decide one direction and do it consistently:

- **Option A (keep it simple):** delete the unused events + `AcquisitionUICoordinator` and keep UI handling in widgets/main window.
- **Option B (more modular UI):** actually construct `AcquisitionUICoordinator` in `main_window` and remove duplicated UI logic from `toggleAcquisitionStart`.
  - If you pick B, keep the coordinator UI-only; do not let it leak into backend/controller layer.

Audit preference: delete unused Phase 8 artifacts unless they clearly reduce complexity without introducing new command types or duplicating existing state events.

### NavigationViewer / ScanCoordinates backend move
- Convert `NavigationViewer` to a backend controller/service.
- Create `ScanCoordinatesService` if not already done.
- These backend components:
  - subscribe to scan/navigation commands
  - publish `ScanCoordinatesUpdated` and `NavigationViewerStateChanged`

### Main window cleanup
- `src/squid/ui/main_window.py`
- `src/squid/ui/gui/signal_connector.py`

Must:
- Remove business logic methods:
  - `move_from_click_image`
  - `toggleAcquisitionStart`
  - `onWellplateChanged`
- Remove cross-widget Qt signal plumbing in `signal_connector.py` after migrating to events.
- Main window retains only:
  - UIEventBus
  - widget instances
  - layout/container code

### Display-plane cleanup (audit finding)

Multipoint acquisition display is currently wired via a callback bridge:

- `src/squid/ui/gui/qt_controllers.py` (`MultiPointSignalBridge`)
- `src/squid/ui/main_window.py` (`_multipoint_signal_bridge` setup)
- `src/squid/ui/gui/signal_connector.py` (connects bridge signals to display + plots)

This breaks when controllers are injected from `ApplicationContext` (no callback wiring), and it violates the desired separation.

- [x] Remove `MultiPointSignalBridge` and `_multipoint_signal_bridge`.
- [x] Feed acquisition frames into `StreamHandler` from the worker/data-plane.
- [x] Wire display updates from `StreamHandler` only (Napari and non-Napari).

## Implementation checklist

### 7.1 Widget refactors
- [x] WellplateMultiPointWidget:
  - remove `navigationViewer`/`scanCoordinates` args.
  - subscribe to `ScanCoordinatesUpdated` for region/fov lists.
  - publish commands for scan editing (add/remove regions/FOVs).
  - filter events by `experiment_id` where applicable.
- [x] FlexibleMultiPointWidget:
  - same pattern as wellplate widget.
- [x] Any remaining widget still referencing controllers/services directly:
  - grep: `rg "controllers\\.|services\\.|navigationViewer|scanCoordinates" src/squid/ui/widgets -S`

### 7.2 Backend navigation/scan services
- [x] Create backend scan/navigation service(s) with clear EventBus API.
- [x] Wire them in `ApplicationContext._build_services` or `_build_controllers`.
- [x] Remove UI ownership of these objects.

### 7.3 Remove signal_connector cross wiring
- [x] Identify remaining connections in `signal_connector.py`.
- [x] Replace each with EventBus command/state events.
- [x] Delete file once empty.

Audit note: `signal_connector.py` currently declares Qt signals as the “primary mechanism”.
That should be treated as a hard failure against the architecture; the end state is **no cross-widget Qt wiring** for the control plane.

Minimum required removals in `src/squid/ui/gui/signal_connector.py` (from audit Step 00):
- [x] `connect_acquisition_signals` (acquisition_started/toggle_live_scan_grid/fluidics init)
- [x] `connect_wellplate_signals` (WellplateFormatWidget signal fan-out)
- [x] `connect_plot_signals` (multipoint bridge-driven plotting)
- [x] Multipoint bridge dependencies inside `connect_navigation_signals` and `connect_display_signals`
- [x] Widget→widget direct calls in `connect_live_control_signals` (publish commands + render state instead)

Additional audit-driven removals:
- [x] Remove `scan_coordinate_callback` / `ScanCoordinates.set_update_callback(...)` usage from `src/squid/ui/main_window.py`.
  - Replace overlay updates with subscriptions to `ScanCoordinatesUpdated` via UIEventBus.

### 7.5 Fix “Snap/Start Acquisition logs but GUI doesn’t update” (required)

These symptoms are expected in the current state because multipoint display and some UI state changes still rely on legacy Qt signal/callback chains.

- [x] Fix click-to-move ordering when immediately followed by snap/acquire-current-FOV:
  - Do **not** publish `MoveStageCommand` from inside `ImageCoordinateClickedCommand` handling (those move commands can land *behind* already-queued `StartAcquisitionCommand`).
  - Move the stage synchronously via `StageService` inside `ImageClickController` so the next queued acquisition command observes the updated position.
- [x] Remove acquisition-widget Qt signals used for control-plane state:
  - `signal_acquisition_started`, `signal_acquisition_shape`, `signal_toggle_live_scan_grid`
  - and all corresponding connections in `src/squid/ui/gui/signal_connector.py`
- [x] Remove `AcquisitionUIToggleCommand` publishing/subscriptions (widgets + main window).
- [x] Ensure the following are the only mechanisms:
  - UI state: driven by `AcquisitionStateChanged` subscriptions (UIEventBus)
  - images/mosaics: driven by StreamHandler only (see Step 04/Display-plane cleanup)

### 7.4 Main window becomes container
- [x] Remove direct controller/service properties after init.
- [x] Reduce to layout + widget creation + UIEventBus injection.

## Verification

- GUI integration tests (if present): `NUMBA_DISABLE_JIT=1 pytest tests/integration/control -v`
- Manual simulation:
  - `python src/main_hcs.py --simulation`
  - verify widgets still update and only publish commands.

## Exit criteria

- Widgets accept UIEventBus only (plus read-only initial state if needed).
- No UI code calls controller/service methods directly.
- `signal_connector.py` is removed.

## Audit reference

- `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`
