# Actor Refactor Simplification — Step 05: Controllers Use Services Only (No Direct Hardware)

## Goal

Make controllers backend‑pure and threadsafe by requiring **services as the only hardware access path**.

After this:

- Controllers do not hold raw `AbstractCamera`, `AbstractStage`, `Microcontroller`, etc.
- All hardware calls go through services, which have internal locks and validation.

This strengthens invariants I1/I2/I5.

## Controllers to update (from audit)

### AutoFocusController
Currently holds direct:
- `self.camera`, `self.stage`, `self.microcontroller`
Fallback paths still use these if services missing.

### LiveController
Still holds `self.camera` (AbstractCamera) and does some logic directly; services are optional.

### LaserAutofocusController
Has mixed direct vs service access; also optional Qt adapter (moved in Step 04).

### MultiPointController / MultiPointWorker
Mostly service-based now, but still takes `microscope` and some direct hardware is passed to worker.

### TrackingController (major mismatch)

`src/squid/ops/tracking/tracking.py` currently:

- Is a `QObject` with Qt `Signal`s (backend must not depend on Qt).
- Holds raw `AbstractCamera`, `AbstractStage`, `Microcontroller`, and UI objects (`ImageDisplayWindow`).
- Uses `QThread` + Qt signal/slot wiring for the worker.
- Directly toggles camera callbacks and calls controller methods from Qt thread contexts.

This violates the target layering and is a hotspot for races/deadlocks.

## Implementation checklist

### 5.1 Enforce service requirements in ctors
- [ ] For each controller, remove direct hardware params.
- [ ] Require corresponding services (non‑Optional).
- [ ] Remove any fallback logic “if service is None use raw device.”

Expected ctor signatures:
- `AutoFocusController(camera_service, stage_service, peripheral_service, live_controller, event_bus, mode_gate, ...)`
- `LiveController(camera_service, illumination_service, peripheral_service, filter_wheel_service, nl5_service, event_bus, mode_gate, ...)`
- `LaserAutofocusController(camera_service, stage_service, peripheral_service, piezo_service, event_bus, mode_gate, ...)`
- `MultiPointController(services..., event_bus, mode_gate, ...)`

Tracking should become:

- **Backend**: `TrackingControllerCore` (no Qt) with a small state machine, using services only.
- **Worker**: `TrackingWorker` as a plain `threading.Thread` / executor task (no QThread).
- **UI**: optional Qt adapter that subscribes via `UIEventBus` and emits Qt signals if needed for display widgets.

### 5.2 Remove direct hardware fields/uses
- [ ] Delete `self.camera`, `self.stage`, `self.microcontroller` fields.
- [ ] Replace all call sites with service equivalents.
  - Example: `self.stage.move_z_to(...)` → `self._stage_service.move_z_to(...)`
- [ ] Ensure services expose any missing functionality; if not, add it to service layer, not controller.

### 5.3 Worker ownership
- [ ] For MultiPointWorker and AutofocusWorker:
  - Accept services only.
  - Do not accept `scope`/raw devices except where unavoidable.
  - If a raw device is unavoidable, create a minimal service wrapper for that operation.

### 5.4 Update ApplicationContext wiring
- [ ] Update controller builders to pass required services.
- [ ] Remove any leftover direct hardware injection.

### 5.5 Tests
- [ ] Update controller unit tests to construct with services.
- [ ] Add tests ensuring controllers raise fast if services missing.

Run:
- `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/mcs/controllers tests/unit/squid/ops -v`

## Exit criteria

- No controller file imports `AbstractCamera`, `AbstractStage`, or vendor device types (except type hints for legacy removal).
- No controller branches on “if service is None.”
- All backend hardware interaction is via services.
- No backend controller subclasses `QObject` or imports Qt (Qt must be UI-only).
