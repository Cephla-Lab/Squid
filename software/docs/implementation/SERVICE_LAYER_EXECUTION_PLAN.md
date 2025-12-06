# Service Layer Execution Plan (Event Bus Everywhere)

This plan is a step-by-step guide to finish the service-layer refactor with the event bus as the sole communication path between GUI and hardware. It assumes zero prior context. Follow DRY, YAGNI, TDD, and make frequent small commits.

## Quick Context
- Architecture intent: `docs/implementation/SERVICE_LAYER_ARCHITECTURE.md`
- Event bus and event types: `squid/events.py`
- Services: `squid/services/{base.py,camera_service.py,stage_service.py,peripheral_service.py}`
- GUI wiring examples: `control/gui_hcs.py`, `control/gui/widget_factory.py`
- Service-aware widgets: `control/widgets/camera/settings.py`, `control/widgets/stage/navigation.py`, `control/widgets/stage/utils.py`, `control/widgets/hardware/dac.py`
- Abstract hardware models: `squid/abc.py` (notably `Pos`)
- Tests: `tests/unit/squid/services/*`, `tests/integration/squid/test_application_services.py`

## Test Environment Hygiene
- Napari/numba caching can break pytest. Run tests with `NUMBA_DISABLE_JIT=1` (or set `NUMBA_CACHE_DIR` to a writable temp dir).
- Always run commands from repo root: `/Users/wea/src/allenlab/Squid/software`.

## Command Snippets
- Unit (services): `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services -v`
- Integration (services wiring): `NUMBA_DISABLE_JIT=1 pytest tests/integration/squid/test_application_services.py -v`
- Grep helpers: `rg "stage\.move" control`, `rg "microcontroller" control/widgets`

## Tasks (small, testable steps)

### 1) Fix Stage position event (blocker)
- Files: `squid/events.py`, `squid/services/stage_service.py`, `tests/unit/squid/services/test_stage_service.py`
- What: Make `StagePositionChanged` accept optional `theta_rad`. In `_publish_position`, read `theta_rad` if present else pass `None`.
- TDD: Update/extend tests to cover missing `theta_rad` and ensure move publishes without crashing.
- Test: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_stage_service.py -v`

### 2) Enforce services + bus presence in GUI
- Files: `control/gui_hcs.py`, `tests/integration/control/test_HighContentScreeningGui.py`
- What: Require `services` in `HighContentScreeningGui` (fail fast). GUI should only interact via services/event bus, never raw hardware fallbacks.
- Tests: Adjust integration test to build GUI with `ApplicationContext.services`; ensure construction succeeds.
- Test: `NUMBA_DISABLE_JIT=1 pytest tests/integration/control/test_HighContentScreeningGui.py -v`

### 3) Stage actions -> bus commands
- Files: `control/gui_hcs.py` (cached moves, click-to-move, shutdown), `control/widgets/stage/navigation.py`, `control/widgets/stage/utils.py`, `control/widgets/wellplate/calibration.py`
- What: UI publishes `MoveStageCommand`/`MoveStageToCommand`/`HomeStageCommand` on `event_bus`. StageService handles and publishes `StagePositionChanged`. Remove direct `stage.*` calls in GUI/widgets.
- If special moves (loading/scanning) need commands: add new command types in `squid/events.py` and handlers in `squid/services/stage_service.py`.
- Tests: Extend `tests/unit/squid/services/test_stage_service.py` for any new handlers. Run stage unit slice.
- Test: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_stage_service.py -v`

### 4) Camera actions -> bus commands
- Files: `control/widgets/display/napari_live.py`, `control/widgets/camera/settings.py`, `control/gui_hcs.py` (any remaining camera calls)
- What: UI publishes `SetExposureTimeCommand`/`SetAnalogGainCommand` (and any needed camera commands) via `event_bus`. CameraService handles and publishes `ExposureTimeChanged`/`AnalogGainChanged` (and other state events). Remove direct camera calls in GUI.
- Tests: Update/add unit tests in `tests/unit/squid/services/test_camera_service.py` for new command handlers/state publishes.
- Test: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_camera_service.py -v`

### 5) Peripheral/trigger/AF -> bus
- Files to scan: `control/widgets/hardware/dac.py`, `control/widgets/hardware/trigger.py`, `control/widgets/tracking/controller.py`, `control/widgets/hardware/laser_autofocus.py`
- What: Replace direct microcontroller calls with commands through the bus. If needed, add command/state types (e.g., trigger start/stop/frequency, AF laser on/off) to `squid/events.py`. Implement handlers in `PeripheralService` or a small new service (e.g., TriggerService) and subscribe in ctor.
- Tests: Add unit tests under `tests/unit/squid/services/` for each new handler (assert hardware call + state publish).
- Test: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services -v`

### 6) Ensure state flows via bus
- Files: Widgets subscribing to state: `control/widgets/stage/navigation.py`, `control/widgets/camera/settings.py`, `control/widgets/hardware/dac.py`, others as needed.
- What: Verify widgets subscribe via `event_bus` to state events and do not poll hardware. Avoid duplicate subscriptions.
- Tests: Leverage existing service tests; add lightweight widget-level tests only if behavior changes.

### 7) Command publisher audit
- Files: Sweep with `rg "set_exposure_time|set_analog_gain|set_dac|move_" control`
- What: Replace direct service/hardware calls in UI with `event_bus.publish(Command(...))` for consistency. Keep direct calls only when synchronous result is required (YAGNI).
- Tests: Use existing service tests; add targeted tests if new commands are added.

### 8) Testing hardening
- Files: `tests/conftest.py` (optional), docs
- What: Document `NUMBA_DISABLE_JIT=1` requirement (or set in conftest). Add a short “How to test” note in this doc/architecture doc.
- Tests: Run unit + integration slices to confirm guidance works.

### 9) Documentation touch-up
- Files: `docs/implementation/SERVICE_LAYER_ARCHITECTURE.md` (append status/testing/gaps), this plan.
- What: Add “current status”, “testing”, “known gaps” (any remaining direct hardware).

## Commit Cadence
- One small commit per task/subtask. Include which tests ran (or note if skipped due to env).
- Examples: “fix: allow theta_rad None in StagePositionChanged”, “refactor: require services for HCS GUI”, “feat: route stage moves via bus commands”, “chore: add trigger commands/handlers”.

## Execution Order (suggested)
1) Task 1 (event shape) — unblock moves.
2) Task 2 (GUI requires services) — ensure app can start.
3) Task 3 (stage commands via bus).
4) Task 4 (camera commands via bus).
5) Task 5 (peripheral/trigger/AF commands via bus).
6) Task 6–7 (audits to ensure bus-first).
7) Task 8–9 (testing/docs polish).

Stick to TDD: write/adjust tests first when adding command/handler/state logic, then implement, then run the smallest relevant test slice. Keep changes minimal (YAGNI) and avoid duplicating logic (DRY).

---

## Alternative Task Breakdown (verbatim checklist)

Here’s a pragmatic, self-contained implementation plan that assumes zero context. It follows DRY, YAGNI, TDD, and frequent small commits.

Read First (context + patterns)
- Architecture intent: `docs/implementation/SERVICE_LAYER_ARCHITECTURE.md`
- Current services/events: `squid/events.py`, `squid/services/{base.py,camera_service.py,stage_service.py,peripheral_service.py}`
- GUI wiring examples: `control/gui_hcs.py`, `control/gui/widget_factory.py`
- Service-aware widgets: `control/widgets/camera/settings.py`, `control/widgets/stage/navigation.py`, `control/widgets/stage/utils.py`, `control/widgets/hardware/dac.py`
- Tests layout: `tests/unit/squid/services/*`, `tests/integration/squid/test_application_services.py`
- Abstract hardware: `squid/abc.py` (Stage Pos model is important)

Pre-flight (env/test hygiene)
- Napari/numba cache issue: run pytest with `NUMBA_DISABLE_JIT=1` or set `NUMBA_CACHE_DIR` to a writable temp dir, e.g. `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services -v`
- Stay in repo root: `/Users/wea/src/allenlab/Squid/software`

### Task 1: Fix Stage position event shape (blocker)
Goal: Prevent StageService move methods from crashing due to missing `theta_rad`.
Files:
- `squid/events.py` (StagePositionChanged dataclass)
- `squid/services/stage_service.py` (_publish_position)
- Tests: `tests/unit/squid/services/test_stage_service.py` (add/adjust expected fields)
Steps (TDD):
1. Update/extend tests to allow optional `theta_rad` and verify publishing doesn’t crash with mock Pos lacking theta.
2. Implement: make `StagePositionChanged.theta_rad: Optional[float] = None`; guard attribute access in `_publish_position` and pass `None` if absent.
3. Run: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_stage_service.py -v`

### Task 2: Enforce/guarantee services provided to GUI
Goal: Avoid `None` services breaking widgets; make GUI creation deterministic.
Files:
- `control/gui_hcs.py` (constructor + usage)
- `tests/integration/control/test_HighContentScreeningGui.py` (build GUI with services)
- Maybe `main_hcs.py` (already passes services; confirm)
Steps (TDD-ish):
1. Add/adjust integration test to construct GUI with `ApplicationContext.services` and fail fast if services missing.
2. In `HighContentScreeningGui.__init__`, assert services is not `None` (or create a minimal `ServiceRegistry` fallback when simulation flag is set) to avoid silent `None`.
3. Ensure `widget_factory` always pulls services from `self._services` (already) and the constructor signature documents that services are required.
4. Run: `NUMBA_DISABLE_JIT=1 pytest tests/integration/control/test_HighContentScreeningGui.py -v`

### Task 3: Route GUI stage moves through StageService (no direct hardware)
Goal: Remove remaining direct stage calls; use service for all moves/positions.
Files (primary):
- `control/gui_hcs.py` (cached position restore, click-to-move, shutdown, safety moves)
- `control/widgets/wellplate/calibration.py` (still uses raw stage)
- Check other stragglers via `rg "stage\.move" control`
Steps:
1. Identify direct `stage.move_*`/`stage.get_pos()`/`stage.set_limits()` calls and replace with service equivalents; publish commands where appropriate.
2. If service lacks a needed helper, add minimal method to `StageService` (only if necessary—YAGNI).
3. Add/extend small unit tests in `tests/unit/squid/services/test_stage_service.py` for any new helper methods.
4. Smoke in integration: `NUMBA_DISABLE_JIT=1 pytest tests/integration/squid/test_application_services.py -v` and optionally a narrowed selection involving GUI if feasible.

### Task 4: Route GUI camera controls through CameraService
Goal: Ensure camera settings paths use service/event flow; no direct hardware calls.
Files:
- `control/gui_hcs.py` (any remaining `camera.*` calls beyond streaming lifecycle)
- `control/widgets/display/napari_live.py` (currently updates configs but doesn’t publish commands)
- `control/widgets/display/napari_multichannel.py`, `napari_mosaic.py` (already use service for geometry; verify no direct camera calls)
Steps:
1. Replace any lingering direct exposure/gain changes with publishing `SetExposureTimeCommand`/`SetAnalogGainCommand` to the event bus or direct service calls (stay consistent).
1.5. If you add new command types, put them in `squid/events.py` and subscribe in `CameraService`.
2. Add/adjust unit tests if new service helpers are introduced (use `tests/unit/squid/services/test_camera_service.py`).
3. Run targeted: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services/test_camera_service.py -v`

### Task 5: Peripheral/trigger/AF odds and ends
Goal: Eliminate direct microcontroller usage in widgets where feasible.
Files (scan):
- `control/widgets/hardware/trigger.py` (direct microcontroller)
- `control/widgets/tracking/controller.py` (joystick listeners)
- `control/widgets/hardware/laser_autofocus.py` (AF laser on/off)
- Others found via `rg "microcontroller" control/widgets`
Steps:
1. Decide if these should be new service responsibilities (e.g., a `TriggerService` or extend `PeripheralService`). Keep scope tight—only extract what GUI currently needs.
2. Add minimal service + tests if you introduce a new service.
3. Update widgets to call service, not hardware.
4. Run relevant unit tests (add new ones) under `tests/unit/squid/services/` and any impacted widget tests if present.

### Task 6: Event bus usage for commands/state
Goal: Make sure command events are actually published by UI actions, and state events are consumed by widgets.
Files:
- Command publishers: where UI changes exposure/gain/DAC/move (napari_live controls, sliders, navigation controls)
- State subscribers: ensure widgets subscribe once (already in DAC widget, camera settings, navigation)
Steps:
1. Ensure UI publishes commands or calls services that subscribe to commands; avoid direct hardware.
2. Avoid duplicate subscriptions; keep subscriptions in widget constructors.
3. No new tests needed if behavior unchanged; otherwise add small unit tests around event handling for services.

### Task 7: Testing hardening
Goal: Make tests runnable for others.
Files:
- `tests/conftest.py` (add env tweak if acceptable)
- Developer docs: `docs/implementation/SERVICE_LAYER_ARCHITECTURE.md` (append “How to test” note)
Steps:
1. Add a pytest fixture or session hook to set `NUMBA_DISABLE_JIT=1` when running tests, or document the env var in README/docs.
2. Document test commands in the architecture doc or a short README snippet.
3. Validate: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services -v` then `NUMBA_DISABLE_JIT=1 pytest tests/integration/squid/test_application_services.py -v`.

### Task 8: Cleanup/documentation
Goal: Make the service-layer intent obvious and reduce surprises.
Files:
- `docs/implementation/SERVICE_LAYER_ARCHITECTURE.md` (append “current state + how to run tests + known gaps”)
- Inline comments sparingly where flows are non-obvious (e.g., why event bus vs direct call).
Steps:
1. Add a short “Current Status” and “Testing” section noting service requirements and env var for pytest.
2. Keep comments minimal and only where necessary.

### Commit cadence
- One small commit per task or subtask with focused message, e.g., “fix: allow theta_rad None in StagePositionChanged”, “refactor: require services for HCS GUI”, “chore: route cached moves via StageService”.
- Run the smallest relevant test slice before each commit; note in commit message if tests were skipped due to env.

### Quick command reference
- Unit services: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/services -v`
- Integration (services wiring): `NUMBA_DISABLE_JIT=1 pytest tests/integration/squid/test_application_services.py -v`
- Grep helpers: `rg "stage\.move" control`, `rg "microcontroller" control/widgets`

This sequence gets the service layer stable (events/types fixed), ensures the GUI can boot with services, and progressively removes direct hardware coupling, all with small, testable steps.
