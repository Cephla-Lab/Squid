# Per-Channel Z-Offset for Laser Autofocus — Design

**Status:** Approved (2026-05-23)
**Branch:** `feat/laser-af-channel-offset`
**Related code:** `software/control/core/multi_point_worker.py`, `software/control/core/laser_auto_focus_controller.py`, `software/control/widgets.py`, `software/control/core/config/repository.py`, `software/control/models/acquisition_config.py`

## 1. Motivation

When laser autofocus (laser AF / reflection AF) is enabled, every FOV starts from the same laser-AF reference plane. Different fluorescence channels have different best-focus positions because:

- **Chromatic aberration** of the optical path (wavelength-dependent focal shift).
- **Sample-induced effects** — refractive-index variation in mounting medium, coverslip thickness, sample thickness — which shift each wavelength differently.

The existing `AcquisitionChannel.z_offset_um` field captures a per-channel z offset and is applied between channels in `MultiPointWorker.handle_z_offset()`, but:

- It is not editable in any UI (always 0 when channels are created).
- It is silently skipped for z-stacks (`if self.NZ == 1` guard).
- It is applied regardless of AF mode, which is not desired when there is no AF reference to offset from.

This design exposes the field with a clear capture workflow, applies it only when laser AF is active, makes it work for z-stacks, and gives the user an explicit "ignore offsets this run" escape hatch in the acquisition widgets.

## 2. Scope

In scope:
- Reusing the existing `AcquisitionChannel.z_offset_um` field.
- Acquisition logic in `MultiPointWorker` (single z and z-stack).
- UI in `LiveControlWidget`, `NapariLiveWidget`, `LaserAutofocusSettingWidget`, `FlexibleMultiPointWidget`, `WellplateMultiPointWidget`.
- Persistence via the existing channel-config YAML.

Out of scope:
- Per-channel laser-AF *references* (i.e., separate calibrations per channel).
- Automatic chromatic-correction lookup tables tied to objective/filter.
- Migration of historical config files (existing files already have `z_offset_um = 0.0` defaults).

## 3. Data model

**Field:** `AcquisitionChannel.z_offset_um` (existing — `software/control/models/acquisition_config.py:136`). No rename, no schema bump.

**Interpretation:** *Signed z displacement from the laser AF reference plane to the channel's best-focus position, in micrometers.* Persisted in the channel config (`general.yaml` and/or per-objective YAML), so it survives app restarts within the same sample/experiment.

**Sample dependence:** The value is treated as sample-dependent — RI, mounting, and coverslip variations shift it. The persisted value is a starting point; the user is expected to re-capture or reset it per sample.

## 4. Acquisition behavior

### 4.1 Gating

The offset is applied during acquisition iff **both** of:
1. `do_reflection_af` is True (laser AF is the active AF method).
2. `apply_channel_offset` is True (acquisition-widget checkbox; default on when laser AF is the AF method).

In all other modes (manual focus, contrast AF, laser-AF-with-checkbox-off), the new helpers are no-ops and existing behavior is preserved exactly.

### 4.2 Delta-tracking algorithm

Replace today's `handle_z_offset(config, not_offset)` and its two call sites with delta-tracking. New worker state:

```python
self._current_z_offset_um: float = 0.0   # reset to 0 at the start of each FOV and after every z_level
```

**Invariant:** `_current_z_offset_um == 0` whenever `prepare_z_stack`, `move_z_for_stack`, `move_z_back_after_stack`, or `_last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm` (line 1058) runs. The tail correction (`_reset_channel_z_offset`) and the abort path (§4.7) both enforce this. Reviewers / implementers MUST keep this invariant — it is what lets the existing z-stack and time-lapse code stay unchanged.

New helpers on `MultiPointWorker` (note: dispatch to stage vs piezo to match `move_z_for_stack`):

```python
def _apply_channel_z_offset(self, config) -> None:
    """Move by the delta to reach this channel's per-channel z-offset.

    Uses the piezo when self.use_piezo is True (matches move_z_for_stack), the stage
    otherwise. No-op when laser AF is not the active AF method, when the acquisition
    'Apply channel offset' checkbox is off, or when the delta is zero.
    """
    if not (self.apply_channel_offset and self.do_reflection_af):
        return
    target_um = config.z_offset_um or 0.0
    delta_um = target_um - self._current_z_offset_um
    if delta_um == 0:
        return
    self._move_z_for_offset(delta_um)
    self._current_z_offset_um = target_um

def _reset_channel_z_offset(self) -> None:
    """Undo any remaining offset so the z axis returns to the un-offset baseline."""
    if self._current_z_offset_um == 0:
        return
    self._move_z_for_offset(-self._current_z_offset_um)
    self._current_z_offset_um = 0.0

def _move_z_for_offset(self, delta_um: float) -> None:
    """Dispatch a relative z move to piezo or stage to match the active z-stack axis."""
    if self.use_piezo:
        new_piezo_um = self.z_piezo_um + delta_um
        # Clamp to piezo range; if clamped, log a warning — user's offset exceeds piezo travel.
        if new_piezo_um < 0 or new_piezo_um > self.piezo.range_um:
            self._log.warning(
                f"channel z-offset {delta_um:+.2f} µm would drive piezo out of range "
                f"({new_piezo_um:.2f} µm vs [0, {self.piezo.range_um}]); clamping"
            )
            new_piezo_um = max(0.0, min(self.piezo.range_um, new_piezo_um))
        self.z_piezo_um = new_piezo_um
        self.piezo.move_to(self.z_piezo_um)
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
    else:
        self.stage.move_z(delta_um / 1000)
        self.wait_till_operation_is_completed()
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
```

Loop integration (around the existing per-z-level / per-channel loops near `multi_point_worker.py:1050-1108`):

```python
for z_level in range(self.NZ):
    acquire_pos = self.stage.get_pos()  # captured BEFORE any per-channel offset is applied
    # ... existing _last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm runs here at z_level==0,
    # which is correct because _current_z_offset_um is guaranteed 0 at this point.
    try:
        for config_idx, config in enumerate(self.selected_configurations):
            self._apply_channel_z_offset(config)       # was: handle_z_offset(config, True) gated on NZ==1
            # ... acquire image ...
    finally:
        self._reset_channel_z_offset()                  # tail correction; also runs on exception / abort

    if z_level < self.NZ - 1:
        self.move_z_for_stack()                        # unchanged: advances un-offset baseline by deltaZ
```

`prepare_z_stack`, `move_z_for_stack`, and `move_z_back_after_stack` are **unchanged** — the tail correction (in `finally`) guarantees the z axis is at the un-offset baseline before they run, regardless of whether the channel loop completed, raised, or was aborted.

### 4.3 Z-stack semantics

Each channel's z-stack is shifted by its own offset relative to the un-offset baseline:

| Channel | offset (µm) | Stack positions (FROM CENTER, NZ=3, step=5 µm, ref Z0) |
|---|---|---|
| A | 0 | Z0−5, Z0, Z0+5 |
| B | +2 | Z0−3, Z0+2, Z0+7 |

### 4.4 Backlash

The per-channel offset relies on the existing backlash compensation in `CephlaStage.move_z()` (`software/squid/stage/cephla.py:69`):

- Downward moves (`rel_mm < 0`) overshoot past target by `_BACKLASH_COMPENSATION_DISTANCE_MM` (5 µm), then approach upward.
- Upward moves go directly.
- The stage therefore always rests on the gravity-loaded ("down") side of the drive mechanism.

Calling `self.stage.move_z(delta / 1000)` inherits this behavior for every delta the algorithm emits. No new logic is added; we must not bypass `stage.move_z` (e.g., by issuing raw µstep commands).

**Caveat at soft limits:** For a downward delta that brings the stage within 5 µm of the configured Z minimum, `CephlaStage.move_z_to` clamps the overshoot target (`cephla.py:125`), but `CephlaStage.move_z` does not (`cephla.py:78-87`). In that narrow regime the actual landing position may be 0-5 µm above the requested baseline. With expected offsets ≤ 50 µm and typical Z stages with ≫ 5 µm of free travel from any laser-AF reference, this is not a practical concern, but the spec acknowledges it: do not rely on sub-micron accuracy of the reset move when the laser-AF reference is < 5 µm above the Z min.

**Piezo path:** The piezo uses absolute positioning via `piezo.move_to()` — no backlash compensation is applied or needed. The clamp in `_move_z_for_offset` (§4.2) handles range overflow explicitly.

### 4.5 Move count

For N channels per z_level with all-distinct, all-nonzero offsets:

- Worst case: N (deltas) + 1 (reset) = **N+1 stage moves** per z_level.
- Best case (all offsets zero, or all equal): 0 moves added.

The naive "apply +offset / undo −offset around every image" approach (which is what `handle_z_offset` does today for NZ==1) would be 2N moves per z_level — the delta-tracking algorithm roughly halves stage motion.

### 4.6 Logging

Add an info log once per acquisition start when offsets exist on channels but won't be applied (laser AF off or checkbox off), summarising which channels have non-zero offsets that are being ignored:

```
[multi-point] laser AF off (or 'Apply channel offset' unchecked) — ignoring non-zero z-offsets on channels: ['mCherry: +1.2µm', 'Cy5: -0.6µm']
```

### 4.7 Abort handling

`handle_acquisition_abort` is invoked from inside the channel loop (`multi_point_worker.py:1101-1102`). Without care, an abort mid-channel would leave the z axis offset and corrupt subsequent acquisitions. Two protections, both required:

1. The `try/finally` around the channel loop (see §4.2) calls `_reset_channel_z_offset()` before `handle_acquisition_abort` propagates up the stack.
2. Belt-and-braces: `handle_acquisition_abort` itself begins with `self._reset_channel_z_offset()` to handle code paths that bypass the inner loop's `finally`.

## 5. UI design

### 5.1 LiveControlWidget and NapariLiveWidget

Both live-view widgets gain the same offset row. Both already have `is_switching_mode` guards (see CLAUDE.md > "Widget Mode Switch Guards") — the new spinbox follows that pattern.

**"Show Z-offset" toggle:** A small checkbox (or chevron) in the live-controller header collapses the new row. Default off — no visual change to today's widget when toggled off.

When toggled on, an extra row appears under the channel dropdown:

```
Z offset (µm):  [ −2.5  ▲▼ ]   [ Capture current ]   [ Reset ]   [x] Apply on channel switch
```

- **Spinbox** — range ±50, step 0.1, suffix "µm", `setKeyboardTracking(False)`. `valueChanged` calls a new `update_config_z_offset(new_value)` that mirrors the existing `update_config_exposure_time` pattern, persisting via `config_repo.update_channel_setting(objective, channel_name, "ZOffset", new_value)`.
- **Capture current** — pseudo-code:
  ```python
  def capture_current_z_offset(self):
      if not self.laser_auto_focus_controller.laser_af_properties.has_reference:
          return  # button should also be disabled in this state
      try:
          displacement_um = self.laser_auto_focus_controller.measure_displacement()
      except Exception as e:
          QMessageBox.warning(self, "Capture failed",
              f"Could not read laser AF spot: {e}\nOffset unchanged.")
          return
      self.currentConfiguration.z_offset_um = displacement_um
      self.liveController.microscope.config_repo.update_channel_setting(
          self.objectiveStore.current_objective,
          self.currentConfiguration.name,
          "ZOffset", displacement_um,
      )
      try:
          self.is_switching_mode = True
          self.entry_zOffset.setValue(displacement_um)
      finally:
          self.is_switching_mode = False
  ```
  Disabled when laser AF has no reference. Tooltip: *"Read displacement from laser AF reference and save as this channel's offset."*

- **Reset** — sets `z_offset_um = 0` for the current channel, persists, updates spinbox via the same `is_switching_mode` guard.

- **"Apply on channel switch"** checkbox — when checked AND laser AF has a reference, the channel-dropdown handler (`update_microscope_mode` / equivalent in NapariLiveWidget) calls a new live-view helper `_apply_live_channel_offset(new_config)`. **Use absolute positioning, not relative-delta tracking:** the helper computes `target_z_mm = laser_af_reference_z_mm + new_config.z_offset_um / 1000` and calls `self.stage.move_z_to(target_z_mm)`. This is robust against manual z jogs by the user between channel switches (a relative delta scheme would go stale). Implementation note: the laser AF system tracks displacement, not absolute stage z of the reference, so the live-view helper queries `self.stage.get_pos().z_mm` and subtracts the current `laser_af_controller.measure_displacement()` to derive the reference absolute z just before moving — that subtraction is robust to drift.
  - Lifecycle:
    - Initial state on widget creation: checkbox is **disabled** if laser AF has no reference; otherwise **enabled and checked**.
    - Reference acquired (none → has): enable the checkbox (don't change checked state — preserves user choice across reference recalibration).
    - Reference cleared (has → none): disable the checkbox (don't change checked state). While disabled, channel switches do not apply offsets.
  - Updates driven by a new signal on `LaserAutofocusController` (e.g., `signal_reference_changed`) — implementation detail for the plan.

**Tooltip on spinbox:** *"Per-channel z-offset from the laser AF reference plane. Sample-dependent — re-capture or reset when starting a new sample."*

**Repository support:** Add to `update_channel_setting` (`software/control/core/config/repository.py:699+`):

```python
setting_mapping = {
    ...,
    "ZOffset": ("channel", "z_offset_um"),
}
```

Add a `location == "channel"` branch that does `setattr(acq_channel, field, value)` directly on the top-level field, then saves.

### 5.2 LaserAutofocusSettingWidget

Add **"Reset all channel offsets"** button near "Set Reference". Behavior:

```python
def reset_all_channel_offsets(self):
    objective = self.objectiveStore.current_objective
    channels = self.config_repo.get_channels(objective)
    if not channels:
        return
    msg = QMessageBox.question(self, "Reset channel offsets",
        f"Set z-offset to 0 for all {len(channels)} channels of objective '{objective}'?\n"
        f"Recommended when starting a new sample.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if msg != QMessageBox.Yes:
        return
    for ch in channels:
        self.config_repo.update_channel_setting(objective, ch.name, "ZOffset", 0.0)
    # signal live widgets to refresh their spinbox if showing one of these channels
    self.signal_channel_offsets_reset.emit()
```

No automatic clearing on `set_reference()`. Tooltip on button: *"Set z-offset to 0 for every channel of this objective. Use when starting a new sample."*

### 5.3 Acquisition widgets

`FlexibleMultiPointWidget` (`widgets.py:5303`), `WellplateMultiPointWidget` (`widgets.py:6779`), and `MultiPointWithFluidicsWidget` (`widgets.py:8997`) gain a single checkbox in the AF section:

```
[x] Apply per-channel z-offset
```

- Enabled only when an AF mode is laser AF (otherwise visually disabled with tooltip *"Requires laser autofocus"*).
- Default state: **checked** when laser AF is the active AF method.
- Wired through `MultiPointController.set_apply_channel_offset(bool)` (new) → `MultiPointWorker.apply_channel_offset` (new attribute, default True).
- **Read timing:** the widget pushes the flag to `MultiPointController` whenever the checkbox changes; the controller stores it and passes the current value to the worker in `MultiPointController.run()` *before* `worker.run()` is invoked. Workers receive the value at construction (or via setter before `run()`); reactive mid-acquisition changes are not supported.

### 5.4 Repository / config persistence

- New setting key `"ZOffset"` in `update_channel_setting`. Add to `setting_mapping`:
  ```python
  "ZOffset": ("channel", "z_offset_um"),
  ```
  And a new `location == "channel"` branch that does `setattr(acq_channel, field, value)` directly on the top-level field.
- **Bug fix in the "create objective config from general" path** (`repository.py:761-782`): the existing list comprehension copies channels field-by-field but omits `z_offset_um`. Add `z_offset_um=ch.z_offset_um` to the copy so a capture writes through correctly on the first save (without this, the first `update_channel_setting("ZOffset", ...)` call would create an objective config with `z_offset_um=0.0` defaults that overwrite the just-set field unless the call path is exactly right). This is a pre-existing latent bug; the new feature exposes it, so the fix lands here.
- Reads use the existing channel-loading flow (`get_objective_config` → `get_channel_by_name`), no changes.
- Existing YAMLs already include `z_offset_um: 0.0` because of the model default — no migration needed.

**Live-view in-memory sync pattern:** When the user edits the spinbox or clicks Capture / Reset, the widget must update `self.currentConfiguration.z_offset_um = value` **before** calling `config_repo.update_channel_setting(...)`. The existing exposure / gain / intensity flows follow this pattern (`widgets.py:4237-4271`). Without this in-memory mutation, the live channel-switch handler would read a stale value from `self.currentConfiguration` until the next config reload. Document explicitly in the implementation plan.

## 6. Reference and sample changes

- **`set_reference()` does NOT clear offsets.** Recalibration on the same sample shouldn't be destructive.
- **Objective change** is already handled: channel configs are objective-scoped, so each objective has its own `z_offset_um` per channel via `repository.update_channel_setting(objective=...)`.
- **New sample:** use "Reset all channel offsets" in the laser AF widget, then "Capture current" per channel as you set up imaging.

## 7. Edge cases and error handling

| Case | Behavior |
|---|---|
| Capture clicked, no laser AF reference | Button disabled. If somehow invoked, no-op with warning log. |
| Capture clicked, spot unmeasurable | `measure_displacement()` exception is caught; `QMessageBox.warning`; offset unchanged. |
| Spinbox edited while in `is_switching_mode` | Guarded by existing pattern — no duplicate persistence. |
| Live view "Apply on switch" + dropdown change with no laser AF reference | Checkbox auto-disabled; no stage move triggered. |
| Acquisition with laser AF off + non-zero offsets | Helpers no-op; one info log lists ignored offsets (see §4.6). |
| TCP / MCP control server starts acquisition | Existing entry points pass through `MultiPointController`. The new `apply_channel_offset` attribute defaults to True so behavior matches default GUI run; remote callers can override via a new parameter in a follow-up. |
| Stage hits soft-limit during offset move | `stage.move_z` already clamps / raises as today; offset move is no different from any other relative move. See §4.4 backlash caveat for the < 5 µm overshoot regime. |
| Piezo z-stack with channel offsets | Offsets applied via the piezo (§4.2 `_move_z_for_offset`); clamped to `[0, piezo.range_um]` with a warning log if overflow. |
| User manually jogs z between channel switches in live view | Live-view helper uses **absolute** positioning (`stage.move_z_to(reference_z + offset / 1000)`), so manual jog state is irrelevant — the next switch goes to the right absolute position. |
| Acquisition aborted mid-channel | `try/finally` in z-level body resets offset; `handle_acquisition_abort` also calls `_reset_channel_z_offset` defensively. Next FOV / time-point starts from a clean baseline. |
| Time-lapse (`Nt > 1`) | `_last_time_point_z_pos[(region_id, fov)]` is written and read at `_current_z_offset_um == 0` per the §4.2 invariant; subsequent time-point moves to the un-offset baseline correctly. |

## 8. Files changed

| Path | Change |
|---|---|
| `software/control/core/multi_point_worker.py` | Remove `handle_z_offset`. Add `_current_z_offset_um`, `_apply_channel_z_offset`, `_reset_channel_z_offset`, `_move_z_for_offset`, `apply_channel_offset`. Wrap z-level body in `try/finally` for tail correction (§4.2). Add `_reset_channel_z_offset()` call at the top of `handle_acquisition_abort` (§4.7). Add §4.6 log. |
| `software/control/core/multi_point_controller.py` | Plumb `apply_channel_offset` flag to worker (read at `run()` time, before `worker.run()`). |
| `software/control/core/config/repository.py` | Add `"ZOffset"` mapping + `location == "channel"` branch in `update_channel_setting`. **Fix pre-existing bug** at `repository.py:761-782`: add `z_offset_um=ch.z_offset_um` to the channel-copy when creating an objective config from general. |
| `software/control/widgets.py` | Add UI in `LiveControlWidget`, `NapariLiveWidget`, `LaserAutofocusSettingWidget`, `FlexibleMultiPointWidget`, `WellplateMultiPointWidget`, `MultiPointWithFluidicsWidget`. |
| `software/control/core/laser_auto_focus_controller.py` | Add `signal_reference_changed` (or equivalent) emitted from `set_reference` / `set_reference_image(None)` so live widgets can update checkbox enable state. |
| `software/control/models/acquisition_config.py` | Field description update only (clarify semantics: "from laser AF reference, applied only when laser AF active"). |
| `software/tests/test_multi_point_worker_offsets.py` (new) | Unit tests for delta-tracking (stage + piezo paths, abort, time-lapse). |
| `software/tests/test_config_repository_zoffset.py` (new) | Unit tests for `update_channel_setting("ZOffset", ...)` including the create-from-general path. |

## 9. Test strategy

### 9.1 Unit tests

`software/tests/test_multi_point_worker_offsets.py`:

- Mock `stage` (capture every `move_z(rel_mm)` and `move_z_to`) and `piezo` (capture `move_to` calls).
- Cases:
  1. Four channels with offsets `[0, +2, +2, −1]` µm, NZ=1, laser AF on, checkbox on: expect moves `[+2, −3, +1]` µm (3 deltas + 1 reset; the two `+2` channels share an offset so no move between them).
  2. Same channels, NZ=3, FROM_CENTER, step=5: expect per-z_level move sequences match §4.3 table; z_level transitions still happen via `move_z_for_stack`; `_current_z_offset_um == 0` at the start of every z_level.
  3. Laser AF off → no offset moves (only z-stack moves if NZ > 1).
  4. Checkbox off → no offset moves even with laser AF on.
  5. All-zero offsets → zero offset moves (only z_level steps remain).
  6. Reset called when `_current_z_offset_um == 0` → no move.
  7. **Piezo path** (`self.use_piezo = True`): offsets go to `piezo.move_to`, NOT `stage.move_z`. Verify z-stack still uses piezo for `move_z_for_stack` and offsets correctly compose with piezo position tracking.
  8. **Piezo out-of-range**: offset that would drive `z_piezo_um` outside `[0, piezo.range_um]` is clamped and emits a warning log.
  9. **Time-lapse** (`Nt > 1`): at `z_level == 0` of every time point > 0, `_last_time_point_z_pos[(region_id, fov)]` is read/written with `_current_z_offset_um == 0` (use `move_to_coordinate` flow). Verify across two consecutive time points that the second time-point's stage start matches the first time-point's recorded z exactly (no offset bleed).
  10. **Abort mid-channel** (`abort_requested_fn` returns True after channel 1 image): `_reset_channel_z_offset` is invoked exactly once and `_current_z_offset_um == 0` afterward; verify also that calling `handle_acquisition_abort` directly with a non-zero offset triggers a single reset.
  11. **Multi-region** (≥ 2 regions, each with its own FOV list): `_current_z_offset_um` is 0 at the start of each region's first FOV, even if a prior region ended at a non-zero offset due to abort or exception (defensive reset at FOV entry).

`software/tests/test_config_repository_zoffset.py`:

- Setting `"ZOffset"` via `update_channel_setting` writes through to `z_offset_um` on the model and persists to YAML.
- Round-trip: write 1.5, reload config, value == 1.5.
- Write 0.0 → field becomes 0.0 (not omitted, since defaults are explicit).
- Unsupported setting names still warn and return False.
- **Create-from-general path:** set up a profile with `general.yaml` having channels with non-zero `z_offset_um`, NO objective file. Call `update_channel_setting(objective="20x", channel="DAPI", setting="ZOffset", value=2.0)`. Verify: (a) the resulting objective YAML contains `z_offset_um: 2.0` for DAPI, (b) other channels in the new objective config retain their `general.yaml` `z_offset_um` values (this proves the M1 fix is in place).

### 9.2 Integration / simulation

- `python3 main_hcs.py --simulation`:
  - Toggle "Show Z-offset", enter values, restart app, confirm persistence in `general.yaml` / `objective.yaml`.
  - Switch channels in live view with "Apply on switch" on; verify stage moves (visible in log).
  - Run acquisition with NZ=3 z-stack, 2 channels with distinct offsets, inspect the stage-move log to confirm the move sequence matches §4.3 (the coordinates dataframe records one z per `z_level`, not per channel — per-channel z is derivable as `dataframe.z + channel.z_offset_um` from the saved `acquisition.yaml`).
  - Run acquisition with checkbox off → identical stage-move sequence to a no-offset run.

### 9.3 Manual smoke

- Capture current with laser AF in characterization mode.
- Reset all from laser AF widget; confirm spinbox in live view returns to 0.
- Capture-then-acquire workflow: capture for each of 3 channels at different z positions, run acquisition, verify each saved image is at the captured z.

## 10. Risks

- **Time penalty in z-stacks with many offsets.** N+1 stage moves per z_level (with backlash compensation on each downward delta) add up. Mitigation: §4.5 already minimises move count vs the naive 2N approach. If still slow, follow-up can reorder the channel loop to be channel-major within a z_level (channels with similar offsets grouped) — not in this scope.
- **Stale offsets across samples.** Mitigated by sample-dependence tooltip and "Reset all" button; user must learn the workflow.
- **TCP/MCP backwards compatibility.** New `apply_channel_offset` flag defaults to True; remote callers see no behavior change for laser-AF-off paths and laser-AF-on paths only differ if non-zero offsets exist in config (which today are always 0). Acceptable.

## 11. Open questions

None — all decisions captured above. Plumbing details (exact widget layout in the napari variant, signal names) finalised during implementation.
