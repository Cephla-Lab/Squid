# Infrastructure Patterns Execution Plan

Goal: close all remaining gaps from the infrastructure-patterns plan (Phases 1-4), validate Phase 5 migration consistency, and resolve all TODOs called out in the target modules. This plan is a living checklist; items are checked off as they are completed.

## 0) Inventory and Alignment

- [x] Collect TODO list across ScanCoordinates, LaserAutofocusController, MultiPointController/Worker, focus_operations.
  - TODOs found:
    - ScanCoordinates: manual region membership in `region_contains_coordinate`.
    - LaserAutofocusController: "get current image" helper, return debug image instead of storing `self.image`.
    - MultiPointController: mm unit consistency, runtime update coverage, objective-store data usage, camera abstract update, focus map surface handling.
    - MultiPointWorker: abort-on-failure behavior, z-offset handling for z-stacks, RGB config flag vs string, NL5 trigger handling, illumination controller usage, display emission decision.
- [x] Audit manual EventBus subscriptions for `@handles`/`auto_subscribe` conversion.
  - Controllers: autofocus (contrast/laser/lock), multipoint, live, tracking, microscope mode, peripherals, image click.
  - Managers: scan coordinates, channel configuration manager, navigation state service.
  - UI/Widgets: main window, ui_event_bus, image_display, napari widgets, confocal, laser_autofocus, filter_controller, well_selection, well_1536, tracking widgets, fluidics_multipoint.
- [x] Audit mode gate inline checks for `@gated_command` migration.
  - Remaining inline mode-gate checks: StageService special moves, IlluminationService command handler.
- [x] Audit remaining wildcard imports to remove.
  - Remaining: multi_point_worker.py, andor.py, photometrics.py, toupcam.py, tucsen.py.

## 1) ScanCoordinates Refactor + @handles

- [x] Replace inline geometry helpers with `scan_coordinates/geometry.py`:
  - [x] Remove `_is_in_polygon` usage and route through `point_in_polygon`.
  - [x] Remove `_is_in_circle` usage and route through `point_in_circle`/`fov_corners_in_circle`.
- [x] Replace inline grid generation with `scan_coordinates/grid.py`:
  - [x] Use `GridConfig` and `generate_*` helpers for square/rect/circle/polygon.
  - [x] Use `apply_s_pattern` where required.
- [x] Replace wellplate helpers with `scan_coordinates/wellplate.py`:
  - [x] Use `well_id_to_position`, `row_col_to_well_id`, `parse_well_range`, etc.
- [x] Implement TODO: manual-region membership handling in `region_contains_coordinate`.
- [x] Migrate to `@handles` + `auto_subscribe`:
  - [x] Add decorators to all handlers (15 handlers).
  - [x] Replace `_subscribe_to_commands` with `auto_subscribe`.
  - [x] Add `auto_unsubscribe` teardown.
- [x] Update and run related tests for ScanCoordinates modules and event handling.

## 2) LaserAutofocusController Refactor + @handles

- [x] Add a dedicated helper to fetch the current image (handles trigger mode + AF laser toggling) and replace TODO.
- [x] Replace inline spot detection and correlation with `laser_spot.py` functions.
- [x] Return debug image via results instead of storing `self.image` (remove TODO).
- [x] Migrate to `@handles` + `auto_subscribe` and add teardown unsubscription.
- [x] Update/extend laser AF tests to cover new helpers and event subscription changes.

## 3) Feature Flags Registry Adoption

- [x] Replace direct `_def` feature-flag reads in touched modules with `get_feature_flags()`.
  - [x] MultiPointController: MERGE_CHANNELS, downsampled view toggles.
  - [x] MultiPointWorker: NL5 gating flags.
  - [x] UI + Application entrypoints (main_window, widget_factory, layout_builder, napari/image_display, acquisition widgets).
- [x] Update tests or add new ones for the unified access path.

## 4) MultiPoint Consolidation

- [x] Replace ad-hoc controller state with `AcquisitionConfig`.
- [x] Implement `update_config` for runtime updates (resolve `use_piezo` TODO).
- [x] Use `ObjectiveStore` for per-objective values (resolve TODO).
- [x] Ensure mm unit consistency for all Z and grid settings (resolve TODO).
- [x] Update controller to use `AcquisitionDependencies` for worker creation.
 - [x] Update tests and integration fixtures for new config/dependency APIs.

## 5) Focus Map Generation Move

- [x] Move focus map grid/corner logic into `AutofocusExecutor`.
- [x] Add support for grid-based surface mapping when possible (resolve TODO).
- [x] Update controller to call `generate_focus_map_for_acquisition`.
- [x] Add unit tests for focus map generation behavior.

## 6) MultiPointWorker TODOs

- [x] Decide and enforce abort-on-failure behavior using `abort_on_failed_jobs`.
- [x] Implement z-offset handling for z-stacks (both entry/exit of per-config offsets).
- [x] Replace string check for RGB config with explicit flag in config data.
- [x] Fix NL5 trigger path to use consistent camera APIs.
- [x] Route illumination changes through the illumination controller when available.
- [x] Decide whether RGB generation should emit display events and implement accordingly.
- [x] Restore MERGE_CHANNELS handling in `job_processing.py` (remove TODO or implement merge path).
- [x] Update tests to cover the changed behavior.

## 7) P2 Remaining Items

- [x] Split microscope factory/build logic into `microscope_factory.py` and update callsites.
- [x] Add `acquisition_context` helper and refactor controller to use it.
- [x] Update tests and integration fixtures for new file structure and context manager.

## 8) Phase 5 Consistency Cleanup

- [x] Remove wildcard imports:
  - [x] `software/src/squid/backend/controllers/multipoint/multi_point_worker.py`
  - [x] `software/src/squid/backend/drivers/cameras/andor.py`
  - [x] `software/src/squid/backend/drivers/cameras/photometrics.py`
  - [x] `software/src/squid/backend/drivers/cameras/toupcam.py`
  - [x] `software/src/squid/backend/drivers/cameras/tucsen.py`
- [x] Global `subscribe()` migration to `@handles` + `auto_subscribe`:
  - [x] Controllers: autofocus (contrast/laser/lock), multipoint, live, tracking, microscope_mode, peripherals, image_click.
  - [x] Managers: scan_coordinates, channel_configuration_manager, navigation_state_service.
  - [x] UI/Widgets:
    - [x] camera widgets (settings, live_control)
    - [x] stage widgets (autofocus, navigation, utils, piezo)
    - [x] hardware widgets (trigger, dac)
    - [x] display widgets (image_display, focus_map, navigation_viewer)
    - [x] napari widgets (live, multichannel, mosaic)
    - [x] wellplate widgets (format, calibration, sample_settings, well_selection, well_1536)
    - [x] acquisition widgets (wellplate_multipoint, fluidics_multipoint)
    - [x] tracking widgets (displacement)
    - [x] remaining: main_window, filter_controller, confocal, laser_autofocus, focus_lock_status, tracking controller, plate_reader
  - [x] Replace lambdas with named handlers to enable `@handles`.
  - [x] Ensure `auto_unsubscribe` on teardown for all migrated classes.
- [x] Mode gate consistency:
  - [x] Update `gated_command` to support a blocked-handler hook OR refactor stage special-move handlers to use the decorator while still publishing blocked completion events.
  - [x] Migrate IlluminationService handler to `@gated_command`.
  - [x] Add regression tests for subscription cleanup and blocked-command behavior.

## 9) Test Regression Fixes

- [x] Fix autofocus FOV counter increment to occur per FOV (not per z-level) and sync with progress tracker.
- [x] Stop timelapse acquisitions from skipping late timepoints; always capture all configured timepoints.
- [x] Update integration test coordinate helper to use center-stage positions (avoid software-limit clipping).
- [x] Resolve contrast-AF acquisition timeout in integration tests (ensure AF cadence or timing expectations align).
- [x] Investigate remaining integration failures: `test_multi_fov_grid`, `test_timelapse_with_zstack`, `test_timelapse_multiwell_zstack`.
- [x] Rerun affected integration tests to confirm stability.
