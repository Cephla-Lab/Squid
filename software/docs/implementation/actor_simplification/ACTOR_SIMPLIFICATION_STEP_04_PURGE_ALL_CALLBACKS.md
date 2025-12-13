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

6. **Multipoint “display callback” plumbing**
   - `MultiPointController(..., on_new_image_fn=..., on_acquisition_start_fn=..., ...)`
   - `MultiPointWorker(..., on_new_image_fn=..., on_acquisition_start_fn=..., ...)`
   - `MultiPointSignalBridge` (Qt bridge consuming those callbacks)

   Even if these callbacks are treated as “data plane”, they still create frontend/backend coupling and are a major source of broken wiring (controllers injected from ApplicationContext do not provide these hooks).

   Replace with StreamHandler + events:
   - Images: StreamHandler/QtStreamHandler only
   - Start/finish/progress: EventBus events only

## Implementation checklist

### 4.1 Remove callback fields from commands
- [x] In `src/squid/core/events.py`, delete callback fields from the two stage commands.
- [x] Add replacement completion events:
  - Option A: `StageMoveToLoadingPositionFinished(success: bool, error: Optional[str])`
  - Option B: one generic `StageSpecialMoveFinished(kind: Literal["loading","scanning"], success, error)`
- [x] Update `StageService._on_move_to_loading_command/_on_move_to_scanning_command`:
  - Remove `callback` plumbing.
  - Perform move.
  - Publish completion event.
- [x] Update any UI code that passed callbacks to instead subscribe to completion events via UIEventBus.
  - Grep uses: `rg "MoveStageToLoadingPositionCommand\\(|MoveStageToScanningPositionCommand\\(" src -S`

### 4.2 Remove AutoFocusController callbacks
- [x] Delete ctor args `finished_fn`, `image_to_display_fn`.
- [x] Delete `_finished_fn`, `_image_to_display_fn` fields and setters.
- [x] Delete `_emit_finished`, `_emit_finished_failed`, `_emit_image_if_needed`.
- [x] Ensure autofocus completion is represented via events:
  - Existing `AutofocusStateChanged` is sufficient if UI only needs “running/not running”.
  - If UI needs success/failure, add `AutofocusFinished(success: bool, error: Optional[str])`.
- [x] Debug images:
  - If autofocus currently emits images for UI, route them through StreamHandler.
  - Pattern: create a small `AutofocusStreamChannel` on the StreamHandler side if needed.
  - Do **not** publish images on EventBus.

### 4.3 Remove LaserAutofocusController callbacks
- [x] Delete `LaserAFCallbacks` dataclass and callback storage.
- [x] Remove all `if self._callbacks.*: self._callbacks.*(...)` call sites.
- [x] Confirm all UI-observable outputs are already events:
  - `LaserAFPropertiesChanged`
  - `LaserAFInitialized`
  - `LaserAFReferenceSet`
  - `LaserAFDisplacementMeasured`
  - `LaserAFFrameCaptured` (frame should be a StreamHandler path if it is a raw image).
- [x] Move any remaining Qt adapter into UI-only package:
  - Controller should not import Qt, even optionally.
  - UI adapter subscribes to EventBus and emits Qt signals.

### 4.4 Sweep and remove any other callbacks
- [x] Global grep:
  - `rg "callback|callbacks|finished_fn|image_to_display_fn|SignalBridge" src/squid -S`
- [x] For each match:
  - If it is control-plane, remove/replace with events.
  - If it is data-plane (StreamHandler), keep.

### 4.5 Replace multipoint display callbacks with StreamHandler (required)

This is the primary cause of “Snap/Start Acquisition logs but GUI doesn’t update” when controllers are injected from `ApplicationContext`.

**Remove**
- `on_new_image_fn`, `on_acquisition_start_fn`, `on_acquisition_finish_fn`, `on_current_configuration_fn`, `on_current_fov_fn`:
  - `src/squid/ops/acquisition/multi_point_controller.py`
  - `src/squid/ops/acquisition/multi_point_worker.py`
- `src/squid/ui/gui/qt_controllers.py` (`MultiPointSignalBridge`)
- `_multipoint_signal_bridge` construction/wiring in:
  - `src/squid/ui/main_window.py`
  - `src/squid/ui/gui/signal_connector.py`

**Replace**
- Data-plane: route acquisition frames through StreamHandler only.
  - Add explicit StreamHandler “acquisition channel” functions if you need distinct behavior vs live.
  - Worker calls StreamHandler (thread-safe) directly; UI subscribes via QtStreamHandler/StreamHandler signals.
- Control-plane: rely on EventBus acquisition events for:
  - start/finish state (`AcquisitionStateChanged`)
  - progress (`AcquisitionProgress`, `AcquisitionWorkerProgress`)
  - plot points (either existing progress events or a dedicated lightweight event)

**Checklist**
- [x] Define a StreamHandler API for acquisition frames (no EventBus).
- [x] In `MultiPointWorker` frame callback path, call StreamHandler instead of `_on_new_image_fn`.
- [x] Remove all multipoint “display callback” parameters from worker/controller constructors.
- [x] Update UI display wiring to use StreamHandler only (Napari and non-Napari).
- [x] Delete bridge tests (or rewrite them) so nothing depends on the callback bridge.

## Tests

- Update/add unit tests for new stage completion events.
- Update autofocus/laser AF tests to assert events rather than callbacks.

Run:
- `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid -v`

## Exit criteria

- `rg "callback|Callbacks|finished_fn|image_to_display_fn" src/squid -S` returns only StreamHandler/data-plane results.
- No command dataclass contains callables.
- No controller imports Qt or accepts UI callbacks.

## Audit reference

- `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`
