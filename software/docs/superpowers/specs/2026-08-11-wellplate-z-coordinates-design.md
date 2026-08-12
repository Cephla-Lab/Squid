# Wellplate Multipoint: Save/Load Z Coordinates — Design

**Date:** 2026-08-11
**Status:** Approved via conversation (design Q&A with You Yan); pending spec review
**Scope:** `software/` — `control/widgets.py`, `control/core/multi_point_utils.py`, `control/core/multi_point_controller.py`, tests

## Problem

The wellplate multipoint widget's coordinate CSVs are XY-only. `save_coordinates` writes
`region, x (mm), y (mm)` and `load_coordinates` builds `(x, y)` 2-tuples, so a loaded scan
plan cannot control the Z at which each FOV is acquired — even though the acquisition
engine already supports per-FOV Z (`MultiPointWorker.move_to_coordinate` moves Z whenever a
coordinate is a 3-tuple), the flexible multipoint widget already round-trips a `z (mm)`
column, and every acquisition already emits a Z-aware top-level `coordinates.csv`.

The same code path also has three pre-existing defects that this work fixes because they
sit directly on the load/save path:

1. **Load Coordinates + "Use Focus Map" crashes at acquisition start.** The loaders store
   region centers as immutable tuples (`widgets.py:9126`, `:9167`); the focus-map path
   mutates centers via `ScanCoordinates.update_fov_z_level` (`scan_coordinates.py:690-693`)
   → `AttributeError`. Reproduced.
2. **Switching back to "Load Coordinates" mode crashes.** `restore_cached_coordinates`
   calls `navigationViewer.register_fov_to_image` (`widgets.py:9130`), a method that does
   not exist (only the plural `register_fovs_to_image` does).
3. **Focus-map Z leaks into GUI state.** The acquisition's coordinate snapshot shares its
   inner per-region lists with the GUI's `ScanCoordinates`
   (`ScanPositionInformation.from_scan_coordinates` does `dict(...)`,
   `multi_point_utils.py:24`), and the controller's focus-map branch additionally writes
   back via `update_fov_z_level` (`multi_point_controller.py:786`). After a focus-map run
   the GUI plan permanently holds interpolated Z; a later run without the focus map
   silently reuses it, and `save_coordinates`' `for x, y in fov_coords`
   (`widgets.py:9204`) crashes on the resulting 3-tuples.

## Design

### CSV format

`region, x (mm), y (mm)` plus an **optional `z (mm)` column** — identical to the flexible
widget's location list and the acquisition's top-level `coordinates.csv`
(`multi_point_controller.py:742-750`). A file without the column loads exactly as today.
A useful consequence: the `coordinates.csv` an acquisition writes becomes directly
re-loadable, Z included.

### Save (`WellplateMultiPointWidget.save_coordinates`)

- Every FOV row gets a `z (mm)` value derived from the **current stage Z at save time**,
  adjusted per objective for parfocality (below). The file for the objective in use gets
  exactly the current stage Z.
- `_helper_save_coordinates` becomes length-tolerant: `x, y = coord[:2]`, and Z is taken
  from the tuple when `len(coord) == 3`, else the stamped per-objective Z. In practice the
  per-objective regeneration produces 2-tuples, so the stamped Z applies; the tolerance
  removes the crash-on-3-tuple hazard permanently.

### Parfocal correction across objectives

No new configuration: the correction reuses the objective switcher's existing per-machine
Z offset in `control/_def.py`. The Xeryon 2-position changer
(`objective_changer_2_pos_controller.py`) parks the stage
`XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM` lower whenever a position-2 objective is in
use (`stage.move_z(-offset)` on pos1→pos2, reverted on pos2→pos1), so the stage-Z frame
per objective is already defined by `XERYON_OBJECTIVE_SWITCHER_POS_1` /
`XERYON_OBJECTIVE_SWITCHER_POS_2` membership.

- **Relative frame:** `rel_z(o) = −XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM` when
  `USE_XERYON` and *o* is in `XERYON_OBJECTIVE_SWITCHER_POS_2`; `0.0` otherwise
  (position-1 objectives, names not in either list, machines without the switcher).
- **Save math:** for each objective *o*, `z_o = z_current + (rel_z(o) − rel_z(current))`.
  Same-position objectives and non-Xeryon machines (turret restores the same Z; manual
  changers) get identical Z in every file — exactly the pre-correction behavior.
- If the shifted Z falls outside `SOFTWARE_POS_LIMIT` for some objective, load-time
  validation rejects that file with a clear error — those values were physically
  unreachable anyway.
- Safe to compute inside the save loop: `objectiveStore.set_current_objective` is
  store-only (updates the pixel-size factor; no signals, no hardware motion).
- Load side needs no parfocal logic — each per-objective file already carries
  objective-appropriate Z. Which file matches which objective remains a filename
  convention (`{folder}_{objective}.csv`), as today.

### Clear Coordinates (toggle on the load button)

The existing Save↔Clear button-morphing machinery is unreachable (`has_loaded_coordinates`
is never set `True`; the Save button lives in `xy_controls_frame`, hidden in Load
Coordinates mode). Decision: the Save Coordinates button never toggles; the toggle lives
on the **"Load New Coords" button** in `load_coordinates_frame` — the control that is
actually visible when coordinates are loaded.

- `btn_load_scan_coordinates` becomes a two-state toggle driven by
  `has_loaded_coordinates`:
  - **Nothing loaded** → text "Load New Coords"; clicking opens the file dialog (and the
    auto-dialog on entering the mode with no cache stays as-is).
  - **File loaded** → text "Clear Coords"; clicking clears the scan regions and
    navigation-viewer overlay, `cached_loaded_coordinates_df`,
    `cached_loaded_file_path`, and the loaded-path text field, then reverts the button
    to "Load New Coords". Loading a different file is Clear-then-Load.
- `has_loaded_coordinates` becomes real state: set `True` on successful
  `load_coordinates` / `restore_cached_coordinates`, `False` on Clear. It means "a
  coordinates file is loaded (cache present)".
- **Delete the dead Save-button morphing machinery:** `toggle_coordinate_controls`,
  `on_save_or_clear_coordinates_clicked` (the Save button connects directly to
  `save_coordinates`), and the no-op call in `acquisition_is_finished`
  (`widgets.py:8976`).

### Load (`load_coordinates`, `restore_cached_coordinates`, and the fluidics widget's loader)

One shared dataframe→regions helper (module-level in `control/widgets.py`; it populates
the passed `ScanCoordinates` and returns the per-region coordinate lists so each widget
does its own `navigationViewer.register_fovs_to_image` call, avoiding the update-callback
path and any double registration) replaces the three near-identical loops
(`widgets.py:9136`, `:9107`, `:9829` in `MultiPointWithFluidicsWidget`). Behavior:

- **FOV entries:** `(x, y, z)` tuples when the CSV has a clean `z (mm)` column, `(x, y)`
  otherwise. Tuples, not lists — `NavigationViewer.register_fovs_to_image` branches on
  `isinstance(fov, tuple)` (`core.py:1789`).
- **Region centers:** `[mean_x, mean_y]` **mutable lists, without Z**. Nothing reads
  center Z (the worker takes Z only from FOV tuples; the MCP server and
  `acquisition.yaml` readers are length-guarded); mutable lists keep loaded centers
  consistent with the `add_*` builders. Crash (1) was eliminated outright — the
  center-mutating write-back path (`update_fov_z_level`) was removed as dead code
  once the snapshot fix left it with no callers.
- **NaN policy:** if `z (mm)` exists but contains any NaN, warn and load the whole file
  XY-only. Regions must stay length-homogeneous (`get_region_bounds`' `np.array` raises
  `ValueError` on mixed 2-/3-tuples; verified on the installed numpy 1.26) and NaN must
  never reach `stage.move_z_to`.
- **Validation:** Z values are checked against
  `SOFTWARE_POS_LIMIT.Z_NEGATIVE..Z_POSITIVE`; violations reject the file with an error
  naming the offending rows. (X/Y validation is unchanged — out of scope.)
- `restore_cached_coordinates` uses the same helper, fixing crash (2) and keeping Z
  across mode round-trips.

### Focus-map snapshot fix

- `ScanPositionInformation.from_scan_coordinates` deep-copies the inner lists:
  `{k: list(v) for k, v in ...}` (`multi_point_utils.py:24`).
- The controller's focus-map branch keeps its in-place rewrite of the (now private)
  snapshot and **drops the `update_fov_z_level` write-back call**
  (`multi_point_controller.py:786`). That left `update_fov_z_level` with no production
  callers, and it was subsequently deleted as dead code.
- Net semantics, as decided: **the focus map wins for that run** (loaded Z is dropped
  while it's active), but the GUI's plan — including loaded Z — is untouched afterwards.
  The Z actually used per image remains recorded in the worker's per-timepoint
  `coordinates.csv`.

### Z precedence during acquisition (existing worker semantics, documented, not changed)

1. Focus map (per-run, rewrites the snapshot at start).
2. Contrast/reflection AF at timepoint > 0 (last AF Z per FOV).
3. Loaded per-FOV Z (`move_to_coordinate`, stack bottom in "From Bottom" mode; in
   "Set Range" mode a per-FOV Z displaces the range's minZ, matching the flexible widget).
4. Timepoint `z_range` initialization.

## Out of scope

- Persisting the loaded CSV path across app restarts.
- Other region builders that drop the Z they're handed (`add_single_fov_region`,
  `add_template_region`, `add_flexible_region_with_step_size`, `set_manual_coordinates`).
- Worker quirks: `FROM CENTER` + AF timepoint drift, `af_fov_count` z-level counting.

(Originally deferred but **fixed on this branch** after the smoke test demonstrated it
silently swapping a loaded plan for well-selector regions: the post-acquisition
`update_coordinates()` wipe. `update_coordinates()` now leaves loaded plans untouched —
they are owned by the Load / Clear / mode-switch flow.)

## Testing

New tests (no existing tests touch these paths; none break):

- Save→load round-trip with Z: 3-tuple FOVs, `[x, y]` list centers, values match.
- Parfocal math: with the Xeryon switcher enabled, files for position-2 objectives are
  shifted by −offset relative to position-1 files; the current objective's file carries
  the unmodified stage Z; same-position pairs and non-Xeryon machines → identical Z
  everywhere.
- Load-button toggle: after load the button reads "Clear Coords"; clicking it resets
  regions, cache, path text, and `has_loaded_coordinates`, and the button reverts to
  "Load New Coords".
- Load without `z (mm)`: 2-tuples, current behavior preserved.
- Load with NaN Z: warning path, XY-only result.
- Load with out-of-range Z: rejected with error.
- Fluidics widget loader shares the helper (same behaviors).
- `from_scan_coordinates` deep copy: mutating the snapshot leaves the source untouched.
- Controller focus-map branch: after the rewrite, `ScanCoordinates` is unchanged.
- Worker `move_to_coordinate` applies Z from a 3-tuple (currently untested).

Test homes: `tests/control/` (widget loader/saver via the unbound-method pattern used
elsewhere in the suite), `tests/control/core/` for `multi_point_utils` /
`scan_coordinates` coverage.
