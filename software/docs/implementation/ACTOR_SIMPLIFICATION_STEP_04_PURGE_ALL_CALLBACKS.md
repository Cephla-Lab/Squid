# Actor Refactor Simplification — Step 04: Purge All Callbacks

## Goal

Remove *every* control-plane callback and any callback-shaped API surface. After this:

- Controllers/services/workers communicate **only via EventBus events**.
- Frames/debug images travel **only via StreamHandler**.
- Command dataclasses contain no callables.

This is invariant I3.

## Scope: known callback sites from audit

1. **Command-level callbacks**
   - `MoveStageToLoadingPositionCommand.callback`
   - `MoveStageToScanningPositionCommand.callback`
   (`src/squid/core/events.py`)

2. **Controller callbacks**
   - `AutoFocusController.finished_fn`
   - `AutoFocusController.image_to_display_fn`
   - `AutoFocusController.set_*_callback` methods
   (`src/squid/mcs/controllers/autofocus/auto_focus_controller.py`)

3. **Laser autofocus callbacks**
   - `LaserAFCallbacks` + uses in `LaserAutofocusController`
   (`src/squid/mcs/controllers/autofocus/laser_auto_focus_controller.py`)

4. **Any residual `NoOpCallbacks` / legacy helpers**
   - Grep: `rg "NoOpCallbacks|create_eventbus_callbacks|Callbacks" src/squid -S`

5. **Service methods that still accept callbacks**
   - `StageService.move_to_loading_position(..., callback=...)`
   - `StageService.move_to_scanning_position(..., callback=...)`
   (`src/squid/mcs/services/stage_service.py`)
   These must be refactored to events as well (even if UI no longer passes callbacks).

## Implementation checklist

### 4.1 Remove callback fields from commands
- [ ] In `src/squid/core/events.py`, delete callback fields from the two stage commands.
- [ ] Add replacement completion events:
  - Option A: `StageMoveToLoadingPositionFinished(success: bool, error: Optional[str])`
  - Option B: one generic `StageSpecialMoveFinished(kind: Literal["loading","scanning"], success, error)`
- [ ] Update `StageService._on_move_to_loading_command/_on_move_to_scanning_command`:
  - Remove `callback` plumbing.
  - Perform move.
  - Publish completion event.
- [ ] Update any UI code that passed callbacks to instead subscribe to completion events via UIEventBus.
  - Grep uses: `rg "MoveStageToLoadingPositionCommand\\(|MoveStageToScanningPositionCommand\\(" src -S`

### 4.2 Remove AutoFocusController callbacks
- [ ] Delete ctor args `finished_fn`, `image_to_display_fn`.
- [ ] Delete `_finished_fn`, `_image_to_display_fn` fields and setters.
- [ ] Delete `_emit_finished`, `_emit_finished_failed`, `_emit_image_if_needed`.
- [ ] Ensure autofocus completion is represented via events:
  - Existing `AutofocusStateChanged` is sufficient if UI only needs “running/not running”.
  - If UI needs success/failure, add `AutofocusFinished(success: bool, error: Optional[str])`.
- [ ] Debug images:
  - If autofocus currently emits images for UI, route them through StreamHandler.
  - Pattern: create a small `AutofocusStreamChannel` on the StreamHandler side if needed.
  - Do **not** publish images on EventBus.

### 4.3 Remove LaserAutofocusController callbacks
- [ ] Delete `LaserAFCallbacks` dataclass and callback storage.
- [ ] Remove all `if self._callbacks.*: self._callbacks.*(...)` call sites.
- [ ] Confirm all UI-observable outputs are already events:
  - `LaserAFPropertiesChanged`
  - `LaserAFInitialized`
  - `LaserAFReferenceSet`
  - `LaserAFDisplacementMeasured`
  - `LaserAFFrameCaptured` (frame should be a StreamHandler path if it is a raw image).
- [ ] Move any remaining Qt adapter into UI-only package:
  - Controller should not import Qt, even optionally.
  - UI adapter subscribes to EventBus and emits Qt signals.

### 4.4 Sweep and remove any other callbacks
- [ ] Global grep:
  - `rg "callback|callbacks|finished_fn|image_to_display_fn|SignalBridge" src/squid -S`
- [ ] For each match:
  - If it is control-plane, remove/replace with events.
  - If it is data-plane (StreamHandler), keep.

## Tests

- Update/add unit tests for new stage completion events.
- Update autofocus/laser AF tests to assert events rather than callbacks.

Run:
- `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid -v`

## Exit criteria

- `rg "callback|Callbacks|finished_fn|image_to_display_fn" src/squid -S` returns only StreamHandler/data-plane results.
- No command dataclass contains callables.
- No controller imports Qt or accepts UI callbacks.
