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
- Publish `AcquisitionUIToggleCommand` for acquisition lifecycle UI transitions (start/stop).

Additionally (audit finding):

- These widgets still emit Qt signals “for backwards compatibility” alongside EventBus publishing.
  - Remove the Qt signals and any `signal_connector.py` glue once the EventBus path is verified end-to-end.

### Phase 8 artifacts to de-cruft (audit findings)

Phase 8 introduced some UI-decoupling events/files. As of audit:

- `AcquisitionUIToggleCommand` is in active use by acquisition widgets and main window.
- `ImageClickController` is active, but is currently wired for BackendActor routing (to be removed in Step 02).
- The following appear **unused** and should be either wired fully or deleted:
  - `WellplateConfigurationCommand` (currently declared but no publisher/subscriber).
  - `LiveScanGridCommand`, `WellSelectorVisibilityCommand` (declared and only referenced by `AcquisitionUICoordinator`).
  - `src/squid/ui/acquisition_ui_coordinator.py` (not constructed anywhere; main window still owns UI transitions).

Decide one direction and do it consistently:

- **Option A (keep it simple):** delete the unused events + `AcquisitionUICoordinator` and keep UI handling in widgets/main window.
- **Option B (more modular UI):** actually construct `AcquisitionUICoordinator` in `main_window` and remove duplicated UI logic from `toggleAcquisitionStart`.
  - If you pick B, keep the coordinator UI-only; do not let it leak into backend/controller layer.

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

## Implementation checklist

### 7.1 Widget refactors
- [ ] WellplateMultiPointWidget:
  - remove `navigationViewer`/`scanCoordinates` args.
  - subscribe to `ScanCoordinatesUpdated` for region/fov lists.
  - publish commands for scan editing (add/remove regions/FOVs).
  - filter events by `experiment_id` where applicable.
- [ ] FlexibleMultiPointWidget:
  - same pattern as wellplate widget.
- [ ] Any remaining widget still referencing controllers/services directly:
  - grep: `rg "controllers\\.|services\\.|navigationViewer|scanCoordinates" src/squid/ui/widgets -S`

### 7.2 Backend navigation/scan services
- [ ] Create backend scan/navigation service(s) with clear EventBus API.
- [ ] Wire them in `ApplicationContext._build_services` or `_build_controllers`.
- [ ] Remove UI ownership of these objects.

### 7.3 Remove signal_connector cross wiring
- [ ] Identify remaining connections in `signal_connector.py`.
- [ ] Replace each with EventBus command/state events.
- [ ] Delete file once empty.

### 7.4 Main window becomes container
- [ ] Remove direct controller/service properties after init.
- [ ] Reduce to layout + widget creation + UIEventBus injection.

## Verification

- GUI integration tests (if present): `NUMBA_DISABLE_JIT=1 pytest tests/integration/control -v`
- Manual simulation:
  - `python src/main_hcs.py --simulation`
  - verify widgets still update and only publish commands.

## Exit criteria

- Widgets accept UIEventBus only (plus read-only initial state if needed).
- No UI code calls controller/service methods directly.
- `signal_connector.py` is removed.
