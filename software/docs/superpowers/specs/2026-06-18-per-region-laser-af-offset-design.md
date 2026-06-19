# Per-region laser-AF offset (focus-map constant-z + laser AF)

- **Date:** 2026-06-18
- **Status:** Approved (design)
- **Scope:** `software/` tree

## Summary

Let the user combine laser autofocus (laser AF) with a constant-z focus map so
that each well/region is focused at its **own offset relative to the single
global laser-AF reference plane**, rather than all wells being driven to that one
shared plane.

Workflow: the user sets the laser-AF reference once, defines one focus point per
well (the existing focus-map "constant + Fit by Region, 1×1" case), and at each
well navigates to the desired focus z and records it. At that moment the system
captures the laser-AF displacement — `measure_displacement()` = `(x −
x_reference)·pixel_to_um` — and stores it as that region's offset. During
acquisition, laser AF at every FOV in that well drives the stage to the well's
stored offset (`move_to_target(offset)`) instead of to the global reference
(`move_to_target(0)`).

This "pins" each well to its capture-time relationship with the reference plane;
laser AF then maintains that pin against z drift, including across time-lapse
timepoints (because the existing per-FOV laser-AF behavior is unchanged — only
its *target* changes).

## Motivation

Today the two subsystems can both be enabled, but they fight:

- The focus map bakes an **absolute** stage z into every FOV
  (`multi_point_controller.py:755-765`). Absolute z does not track sample/stage
  drift over a long acquisition.
- Laser AF runs per FOV and drives the spot back to **one** global reference
  plane (`move_to_target(0)` hardcoded at `multi_point_worker.py:1158`). It has
  no per-well notion, so it overrides the focus map's per-well z by snapping
  every well to the same plane.

There is no way to say "well A1 should be focused 3 µm above the reference plane,
well B2 at −1 µm, and keep them there as the system drifts." This feature adds
exactly that, reusing existing machinery.

## Background: current behavior (verified against code)

### Laser AF — `control/core/laser_auto_focus_controller.py`
- Single global reference: `laser_af_properties.x_reference` / `has_reference`,
  set once via `set_reference()` (line 395). `signal_reference_changed` is emitted
  on change (line 464).
- `measure_displacement()` (line 302) returns `(x − x_reference)·pixel_to_um` in
  µm, or `float('nan')` on failure (lines 319/335/339).
- `move_to_target(target_um)` (line 346): measures current displacement, then
  moves `um_to_move = target_um − current_displacement_um` (line 372) so the spot
  lands at `target_um` of displacement from reference. **Already supports an
  arbitrary target** — no controller change needed.
  - Returns `False` (soft failure, no raise) if: no reference (355), NaN
    displacement (362), `|measured displacement| > laser_af_range` (366), or
    cross-correlation mismatch (378). Note the range guard checks the *measured*
    displacement, not `(measured − target)`.

### Focus map — `control/core/core.py` (`FocusMap`) + `control/widgets.py` (`FocusMapWidget`)
- `FocusMapWidget` (widgets.py:10371) holds `focus_points: list[(region_id, x, y,
  z)]` (10386). Capture points: `add_current_point` (10596) and
  `update_current_z` (10664). Grid generation (`generate_grid`) replaces
  `focus_points`.
- The "1 point per well" case is `method="constant"` + `Fit by Region` + 1×1 grid.
  `fit_surface` (10680) validates `focus_regions == scan_regions` when by-region
  (10688-10697) and enforces 1×1 for constant (10699-10705).
- On acquisition start the controller bakes absolute z into every FOV
  (`multi_point_controller.py:755-765` via `scanCoordinates.update_fov_z_level`).
  This stays as the **coarse pre-position** in the new mode (unchanged).

### Per-channel z-offset — `control/core/multi_point_worker.py`
- `_apply_channel_z_offset` (1220) applies each channel's `z_offset_um` **relative
  to wherever laser AF left the stage**, gated on `apply_channel_offset AND
  do_reflection_af AND af_succeeded`, tracked in `_current_z_offset_um`, reset per
  z-level (1256). Because it is relative to the laser-AF-anchored z, it composes
  additively with the new per-region target **with no math change**.

### How they combine today
Both `set_focus_map(focusMap)` and `set_reflection_af_flag(True)` can be set in
one run. Per-FOV z precedence: base z → focus-map absolute z (baked) → laser AF
relative correction to global plane (overrides focus-map z) → per-channel offset.
So the focus-map per-well z is effectively discarded the moment laser AF runs.

## Goal / non-goals

**Goal:** a per-region µm offset, captured in the Focus Map panel and applied as
the laser-AF target during acquisition, active only when explicitly enabled and
only for the constant 1-point-per-well focus map.

**Non-goals (YAGNI):**
- No change to laser-AF per-FOV scheduling or timepoint behavior — laser AF runs
  exactly as today; only its target changes.
- No combining offsets with non-constant (spline/RBF surface) focus maps.
- No "measure once per well and reuse for all FOVs" optimization.
- No change to `move_to_target`, to the focus-map z-baking, or to
  `_apply_channel_z_offset` internals.

## Design

### State / data model
- A new dict `region_laser_af_offsets: dict[str, float]` (region_id → offset µm).
- **Owned by** `FocusMapWidget` (parallel to `focus_points`). Rationale: the
  offset is a laser-AF concern captured in the Focus Map panel, not surface-fit
  geometry, so it stays out of the `FocusMap` core class. (Approved over the
  alternative of co-locating it with `FocusMap.region_surface_fits`.)
- Threaded to the worker via `AcquisitionParameters` (the focus map itself is not
  passed to the worker; its z is pre-baked, but offsets must reach
  `perform_autofocus`).

### Capture — `FocusMapWidget`
1. Add a `laserAutofocusController` parameter to `FocusMapWidget.__init__`
   (widgets.py:10374), passed from `gui_hcs.py:976` as
   `self.laserAutofocusController` (which is `Optional` and may be `None` when
   `SUPPORT_LASER_AUTOFOCUS` is off — already defined by gui_hcs.py:647, before
   line 976). Store as `self.laserAutofocusController`.
2. New instance state: `self.region_laser_af_offsets: dict[str, float] = {}` and a
   `self.capture_laser_af_offset_enabled: bool = False` flag.
3. Capture helper `_capture_region_offset(region_id)`:
   - No-op (and clear any stale entry for that region) unless
     `capture_laser_af_offset_enabled` is true, the controller is not `None`, and
     `laser_af_properties.has_reference` is true.
   - Call `measure_displacement()`. If `NaN` → warn (status label + log), do **not**
     store. If `|offset| > laser_af_properties.laser_af_range` → warn the well will
     fail AF at acquisition, but still store (user may re-target).
   - Else store `region_laser_af_offsets[region_id] = offset`.
4. Call `_capture_region_offset(region_id)` at the end of `add_current_point`
   (10633-10636) and `update_current_z` (10664-10670).
5. Keep offsets in sync with the focus-point lifecycle:
   - `remove_current_point` (10640): drop the region's offset if that was its last
     point.
   - `generate_grid` (replaces `focus_points`) and any clear path: clear
     `region_laser_af_offsets`.
   - `import_focus_points`: load offsets if present (see Persistence).

### Activation / UI
- New checkbox **in the Focus Map panel** (the shared `FocusMapWidget`), e.g.
  `self.checkbox_perRegionLaserAFOffset = QCheckBox("Per-region laser AF offset")`,
  added in `setup_ui` (near 10456). Its `toggled` sets
  `self.capture_laser_af_offset_enabled` and updates the status hint.
- Enable-state: enabled only when (a) the panel is enabled (the panel is already
  toggled by `checkbox_useFocusMap.toggled → focusMapWidget.setEnabled`, at
  widgets.py:6154 and 7698) **and** (b) Reflection AF is on. Add a
  `FocusMapWidget.set_reflection_af_available(bool)` method that stores the flag and
  re-derives the checkbox `setEnabled`. Connect **both** multipoint widgets'
  `checkbox_withReflectionAutofocus.toggled` to it (alongside the existing
  connections at widgets.py:6156-6157 and 7695-7696), mirroring the
  `_update_apply_channel_offset_enable_state` precedent. Set the initial state
  during each widget's init (next to 6191 / 7745).
- When the checkbox is disabled or unchecked, capture is off and (see below) no
  offsets are pushed to the controller — so behavior is identical to today.

### Acquisition apply
1. `MultiPointController` (multi_point_controller.py):
   - New field `self.region_laser_af_offsets: dict[str, float] = {}` near line 228.
   - New setter `set_region_laser_af_offsets(self, offsets)` near line 415,
     mirroring `set_focus_map`.
   - In `build_params` (921), pass `region_laser_af_offsets=self.region_laser_af_offsets`
     into the new `AcquisitionParameters` field.
2. `AcquisitionParameters` (multi_point_utils.py:29): add
   `region_laser_af_offsets: Dict[str, float]` (default `field(default_factory=dict)`
   if the dataclass uses defaults; otherwise add to the constructor call in
   `build_params` and to the field list).
3. `MultiPointWorker` (multi_point_worker.py): unpack
   `self.region_laser_af_offsets = params.region_laser_af_offsets` in `__init__`
   (next to where `self.do_reflection_af` is set, ~line 120).
4. `perform_autofocus` (multi_point_worker.py:1158): replace
   `self.laser_auto_focus_controller.move_to_target(0)` with
   `self.laser_auto_focus_controller.move_to_target(self.region_laser_af_offsets.get(region_id, 0.0))`.
   `region_id` is already a parameter (1133). Log the target used; a missing
   region defaults to `0.0` (current behavior).
5. GUI gating — both acquisition-start blocks. The mode is active only when **all
   three** hold: `checkbox_useFocusMap.isChecked()` **and**
   `checkbox_withReflectionAutofocus.isChecked()` **and**
   `focusMapWidget.checkbox_perRegionLaserAFOffset.isChecked()`. (A Qt checkbox
   stays checked while disabled, so checking only the mode box is not sufficient —
   all three must be verified at start.)
   - Flexible path (widgets.py:6480-6493): inside the existing
     `if self.checkbox_useFocusMap.isChecked():` block, when the other two
     conditions also hold, call
     `self.multipointController.set_region_laser_af_offsets(self.focusMapWidget.region_laser_af_offsets)`;
     in every other branch call `set_region_laser_af_offsets({})`.
   - Wellplate path (widgets.py:8830-8849): same addition.
   - Optionally warn at start if the mode is on but some scan regions lack an
     offset (those FOVs fall back to target 0).

### Correctness / edge cases
- **Reference invalidation:** connect `laserAutofocusController.signal_reference_changed`
  (laser_auto_focus_controller.py:464) to a `FocusMapWidget` slot that clears
  `region_laser_af_offsets` and shows a status message. This covers the auto-reset
  on z-range edits (`widgets.py:6285` calls `set_reference`), which otherwise would
  silently invalidate every captured offset.
- **No controller / feature off:** all capture is a clean no-op when the controller
  is `None` (`SUPPORT_LASER_AUTOFOCUS` off) or the mode checkbox is off.
- **NaN / no reference at capture:** never store; warn.
- **Range at acquisition:** unchanged `move_to_target` range guard; an out-of-range
  measured displacement returns `False` → that FOV's AF fails (existing path:
  `_laser_af_failures += 1`, per-channel offset skipped). Documented, not changed.
- **Composition with per-channel offset:** unchanged; additive by construction.

### Persistence
- Extend `export_focus_points` to write an optional `offset_um` column and
  `import_focus_points` to read it when present (back-compatible: files without the
  column import as before, with empty offsets). Keep this minimal; it is the only
  persistence added.

## File-by-file change list

| File | Change |
|---|---|
| `control/gui_hcs.py:976` | Pass `self.laserAutofocusController` to `FocusMapWidget(...)`. |
| `control/widgets.py` (`FocusMapWidget`, ~10371-10720) | New ctor param + state; `checkbox_perRegionLaserAFOffset`; `_capture_region_offset`; hook `add_current_point`/`update_current_z`/`remove_current_point`/`generate_grid`/import-export; `set_reflection_af_available`; `signal_reference_changed` handler. |
| `control/widgets.py` (FlexibleMultiPointWidget) | Connect `checkbox_withReflectionAutofocus.toggled` → `focusMapWidget.set_reflection_af_available` (~6156); init enable state (~6191); push offsets at acquisition start (~6480-6493). |
| `control/widgets.py` (WellplateMultiPointWidget) | Same connections (~7695), init (~7745), and push (~8830-8849). |
| `control/core/multi_point_controller.py` | `region_laser_af_offsets` field (~228); `set_region_laser_af_offsets` (~415); pass through `build_params` (~921). |
| `control/core/multi_point_utils.py:29` | Add `region_laser_af_offsets: Dict[str, float]` to `AcquisitionParameters`. |
| `control/core/multi_point_worker.py` | Unpack field in `__init__` (~120); use per-region target at `perform_autofocus` (1158). |

## Testing

Use simulated backends (`tests/tools.py`, `tests/control/conftest.py`):
- **Capture:** with a stub laser-AF controller returning a known displacement,
  `add_current_point`/`update_current_z` store the offset per region; `NaN`
  and no-reference cases store nothing; controller `None` is a no-op.
- **Apply:** `MultiPointWorker.perform_autofocus(region_id, fov)` calls
  `move_to_target` with the region's offset (and `0.0` for an unmapped region).
  Mock `laser_auto_focus_controller.move_to_target` and assert the argument.
- **Invalidation:** emitting `signal_reference_changed` clears stored offsets.
- **Plumbing:** `set_region_laser_af_offsets` → `build_params` →
  `AcquisitionParameters` → worker field round-trips.
- **Regression:** mode off / unmapped regions → `move_to_target(0)` (no behavior
  change). Per-channel offset math unchanged when a region offset is present.

Run: `python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py`
plus `black --config pyproject.toml --check .`

## Risks

- Two multipoint widgets share one `FocusMapWidget`; the reflection-AF enable
  wiring must be connected from **both** (only one is active at a time).
- The laser-AF range guard is against measured-from-reference displacement, so a
  large per-well offset plus drift can exceed `laser_af_range` and silently fail AF
  for that FOV. Mitigated by the capture-time `|offset| > laser_af_range` warning.
- `measure_displacement()` toggles the AF laser and adds latency at each capture;
  acceptable because capture is a manual, per-well setup action.
