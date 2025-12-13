# Actor Refactor Simplification — Step 05: Controllers Use Services Only (No Direct Hardware)

## Goal

Make controllers backend‑pure and threadsafe by requiring **services as the only hardware access path**.

After this:

- Controllers do not hold raw `AbstractCamera`, `AbstractStage`, `Microcontroller`, etc.
- All hardware calls go through services, which have internal locks and validation.

This strengthens invariants I1/I2/I5.

Audit note: to keep frontend/backend separation, “display plumbing” (images to UI) must not be wired as controller/worker callbacks into UI objects. Use StreamHandler as the only data-plane boundary.

## Controllers to update (from audit)

### AutoFocusController
Resolved: `src/squid/mcs/controllers/autofocus/auto_focus_controller.py` and `src/squid/mcs/controllers/autofocus/auto_focus_worker.py` are now services-only (no raw devices, no Qt).

### LiveController
Resolved: `src/squid/mcs/controllers/live_controller.py` is now services-only (no raw camera access).

### LaserAutofocusController
Resolved: `src/squid/mcs/controllers/autofocus/laser_auto_focus_controller.py` is now services-only (no Qt, no raw devices).

### MultiPointController / MultiPointWorker
Resolved: `src/squid/ops/acquisition/multi_point_controller.py` and `src/squid/ops/acquisition/multi_point_worker.py` are now services-only (no raw devices, no `Microscope` passed to the worker).

### TrackingController (major mismatch)

Previously: `src/squid/ops/tracking/tracking.py` was a Qt `QObject` controller/worker with direct UI and raw hardware references.

Resolved:
- Backend controller is now `TrackingControllerCore` in `src/squid/mcs/controllers/tracking_controller.py` (Qt-free).
- Worker is a plain `threading.Thread` (no `QThread`).
- Old Qt tracking controller file deleted: `src/squid/ops/tracking/tracking.py`.
- UI now passes ROI explicitly via control-plane command payload (`StartTrackingCommand.roi_bbox`).

This removes the worst layering/threading violations from tracking.

## Implementation checklist

### 5.1 Enforce service requirements in ctors
- [x] For each controller, remove direct hardware params. (Live, AF, LaserAF, MultiPoint)
- [x] Require corresponding services (non‑Optional). (Live, AF, LaserAF, MultiPoint)
- [x] Remove any fallback logic “if service is None use raw device.” (Live, AF, LaserAF, MultiPoint)

Expected ctor signatures:
- `AutoFocusController(camera_service, stage_service, peripheral_service, live_controller, event_bus, mode_gate, ...)`
- `LiveController(camera_service, illumination_service, peripheral_service, filter_wheel_service, nl5_service, event_bus, mode_gate, ...)`
- `LaserAutofocusController(camera_service, stage_service, peripheral_service, piezo_service, event_bus, mode_gate, ...)`
- `MultiPointController(services..., event_bus, mode_gate, ...)`

Tracking should become:

- [x] **Backend**: `TrackingControllerCore` (no Qt) with a small state machine, using services only.
- [x] **Worker**: `TrackingWorker` as a plain `threading.Thread` / executor task (no QThread).
- **UI**: optional Qt adapter that subscribes via `UIEventBus` and emits Qt signals if needed for display widgets.

### 5.2 Remove direct hardware fields/uses
- [x] Delete `self.camera`, `self.stage`, `self.microcontroller` fields. (Live, AF, LaserAF, MultiPoint)
- [x] Replace all call sites with service equivalents. (Live, AF, LaserAF, MultiPoint)
  - Example: `self.stage.move_z_to(...)` → `self._stage_service.move_z_to(...)`
- [x] Ensure services expose any missing functionality; if not, add it to service layer, not controller. (added `CameraService.get_strobe_time`, `CameraService.set_reference_position`, `FluidicsService.set_rounds`)

### 5.3 Worker ownership
- [ ] For MultiPointWorker and AutofocusWorker:
  - [x] Do not accept `scope`/raw devices (removed `scope` and all raw device fields).
  - [ ] Reduce worker→controller coupling:
    - [x] Remove `LiveController` from `AutofocusWorker` (illumination handled via `IlluminationService` + config snapshot).
    - [x] Remove `LiveController` from `MultiPointWorker` (mode/illumination handled via services).
    - [ ] Remaining (intentional for now): `MultiPointWorker` still calls `AutoFocusController`/`LaserAutofocusController` for autofocus behaviors; remove or rework in a later step.
  - [x] If a raw device is unavoidable, create a minimal service wrapper for that operation. (added missing service wrappers instead)

### 5.4 Update ApplicationContext wiring
- [x] Update controller builders to pass required services.
- [x] Remove any leftover direct hardware injection (ApplicationContext now always creates controllers externally; UI fallbacks removed).
- [x] Construct `TrackingControllerCore` in `ApplicationContext` and expose on `Controllers`.
- [x] Update tracking UI to publish `StartTrackingCommand(roi_bbox=...)` (no UI object references in backend).

Additionally (audit finding):

- `ApplicationContext` currently constructs `StreamHandler` instances with `NoOpStreamHandlerFunctions` in some paths.
  - That is acceptable for tests, but **not** for real UI runs because it produces “logs happen but nothing displays”.
- Converge on one ownership model:
  - Backend uses a core `StreamHandler` instance (thread-safe, no Qt).
  - UI wraps it with `QtStreamHandler` (or sets handler functions) and attaches it to camera callbacks.
  - Workers write frames into the shared StreamHandler (no UI callbacks).

### 5.5 Tests
- [x] Update controller unit tests to construct with services.
- [x] Add tests ensuring controllers raise fast if services missing.
- [x] Run unit tests: `NUMBA_DISABLE_JIT=1 pytest tests/unit -q`

Notes:
- Tracking removed the unused `enable_autofocus` flag from `SetTrackingParametersCommand` to avoid a partially-implemented (“cheating”) option.

Run:
- `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/mcs/controllers tests/unit/squid/ops -v`

## Exit criteria

- No controller file imports `AbstractCamera`, `AbstractStage`, or vendor device types (except type hints for legacy removal).
- No controller branches on “if service is None.”
- All backend hardware interaction is via services.
- No backend controller subclasses `QObject` or imports Qt (Qt must be UI-only).

## Audit reference

- `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`
