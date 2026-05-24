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
self._current_z_offset_um: float = 0.0   # reset to 0 at the start of each FOV
```

New helpers on `MultiPointWorker`:

```python
def _apply_channel_z_offset(self, config) -> None:
    """Move stage by the delta to reach this channel's per-channel z-offset.

    No-op when laser AF is not the active AF method, when the acquisition-widget
    'Apply channel offset' checkbox is off, or when the delta is zero.
    """
    if not (self.apply_channel_offset and self.do_reflection_af):
        return
    target_um = config.z_offset_um or 0.0
    delta_um = target_um - self._current_z_offset_um
    if delta_um != 0:
        self.stage.move_z(delta_um / 1000)
        self.wait_till_operation_is_completed()
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
    self._current_z_offset_um = target_um

def _reset_channel_z_offset(self) -> None:
    """Undo any remaining offset so the stage returns to the un-offset baseline."""
    if self._current_z_offset_um == 0:
        return
    self.stage.move_z(-self._current_z_offset_um / 1000)
    self.wait_till_operation_is_completed()
    self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
    self._current_z_offset_um = 0.0
```

Loop integration (around the existing per-z-level / per-channel loops near `multi_point_worker.py:1050-1095`):

```python
for z_level in range(self.NZ):
    for config_idx, config in enumerate(self.selected_configurations):
        self._apply_channel_z_offset(config)          # was: handle_z_offset(config, True) gated on NZ==1
        # ... acquire image ...
    self._reset_channel_z_offset()                    # tail correction once per z_level

    if z_level < self.NZ - 1:
        self.move_z_for_stack()                       # unchanged: advances un-offset baseline by deltaZ
```

`prepare_z_stack`, `move_z_for_stack`, and `move_z_back_after_stack` are **unchanged** — the tail correction guarantees the stage is at the un-offset baseline before they run.

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

- **"Apply on channel switch"** checkbox — when checked AND laser AF has a reference, the channel-dropdown handler (`update_microscope_mode` / equivalent in NapariLiveWidget) calls a new live-view helper `_apply_live_channel_offset(new_config)` that uses the same delta-tracking pattern with an instance-level `self._live_current_z_offset_um`. Lifecycle:
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

Both `FlexibleMultiPointWidget` (`widgets.py:5303`) and `WellplateMultiPointWidget` (`widgets.py:6779`) gain a single checkbox in the AF section:

```
[x] Apply per-channel z-offset
```

- Enabled only when an AF mode is laser AF (otherwise visually disabled with tooltip *"Requires laser autofocus"*).
- Default state: **checked** when laser AF is the active AF method.
- Wired through `MultiPointController.set_apply_channel_offset(bool)` (new) → `MultiPointWorker.apply_channel_offset` (new attribute, default True).

### 5.4 Repository / config persistence

- New setting key `"ZOffset"` in `update_channel_setting`.
- Reads use the existing channel-loading flow (`get_objective_config` → `get_channel_by_name`), no changes.
- Existing YAMLs already include `z_offset_um: 0.0` because of the model default — no migration needed.

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
| Stage hits soft-limit during offset move | `stage.move_z` already clamps / raises as today; offset move is no different from any other relative move. |

## 8. Files changed

| Path | Change |
|---|---|
| `software/control/core/multi_point_worker.py` | Remove `handle_z_offset`. Add `_current_z_offset_um`, `_apply_channel_z_offset`, `_reset_channel_z_offset`, `apply_channel_offset`. Update inner loop. Add §4.6 log. |
| `software/control/core/multi_point_controller.py` | Plumb `apply_channel_offset` flag to worker. |
| `software/control/core/config/repository.py` | Add `"ZOffset"` mapping + `location == "channel"` branch in `update_channel_setting`. |
| `software/control/widgets.py` | Add UI in `LiveControlWidget`, `NapariLiveWidget`, `LaserAutofocusSettingWidget`, `FlexibleMultiPointWidget`, `WellplateMultiPointWidget`. |
| `software/control/models/acquisition_config.py` | Field description update only (clarify semantics: "from laser AF reference, applied only when laser AF active"). |
| `software/tests/test_multi_point_worker_offsets.py` (new) | Unit tests for delta-tracking. |
| `software/tests/test_config_repository_zoffset.py` (new) | Unit tests for `update_channel_setting("ZOffset", ...)`. |

## 9. Test strategy

### 9.1 Unit tests

`software/tests/test_multi_point_worker_offsets.py`:

- Mock `stage`, capture every `move_z(rel_mm)` call.
- Cases:
  1. Three channels `[0, +2, +2, −1]` µm offsets, NZ=1, laser AF on, checkbox on: expect moves `[+2, −3, +1]` µm (delta + reset).
  2. Same channels, NZ=3, FROM_CENTER, step=5: expect per-z_level move sequences match §4.3 table; z_level transitions still happen via `move_z_for_stack`.
  3. Laser AF off → no offset moves.
  4. Checkbox off → no offset moves even with laser AF on.
  5. All-zero offsets → zero offset moves (only z_level steps remain).
  6. Reset called when `_current_z_offset_um == 0` → no move.

`software/tests/test_config_repository_zoffset.py`:

- Setting `"ZOffset"` via `update_channel_setting` writes through to `z_offset_um` on the model and persists to YAML.
- Round-trip: write 1.5, reload config, value == 1.5.
- Write 0.0 → field becomes 0.0 (not omitted, since defaults are explicit).
- Unsupported setting names still warn and return False.

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
