# Actor Refactor Simplification — Master Plan (Sequential Execution)

This is the single, sequential set of plan files to execute to converge to a **simple, correct**, fully event-driven architecture with strict frontend/backend separation.

Principles:
- **Delete before adding.**
- **One control-plane thread** (single queued EventBus dispatch thread).
- **No control-plane callbacks** (no “compat”, no “deprecated”, no no-op bridges).
- **StreamHandler-only data plane** (frames never on EventBus).
- **UI state derives from backend truth** (no UI “toggle” commands that can desync).

## Step 00 — Hard audit baseline (read + agree)

- File: `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`
- Output: shared understanding of the exact “cheat chains” and what must be deleted/replaced.

Also read:
- `docs/implementation/ACTOR_MODEL_REFACTOR.md`

## Step files (execute in order)

### Step 01 — Freeze architecture contract
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_01_FREEZE_ARCHITECTURE.md`
- Output: final invariants + “no cheating” rules written down and agreed.

### Step 02 — Remove BackendActor/Router (single control-plane thread)
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_02_SINGLE_CONTROL_THREAD_REMOVE_BACKENDACTOR.md`
- Output: controllers + services all run on EventBus dispatch thread; delete actor routing toggles.

### Step 03 — Replace ResourceCoordinator with minimal mode gate
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_03_REPLACE_COORDINATOR_WITH_MODE_GATE.md`
- Output: one gating mechanism enforced in services; no leases/watchdog.

### Step 04 — Purge callbacks (including multipoint “display callbacks”)
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_04_PURGE_ALL_CALLBACKS.md`
- Output: no command/controller/service uses callables for control-plane; no multipoint UI bridges.

### Step 05 — Controllers/services separation: services-only backend
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_05_SERVICES_ONLY_CONTROLLERS.md`
- Output: controllers/workers call services only; remove Qt from backend; tracking refactor included.

### Step 06 — Multipoint correctness + finish event model
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_06_MULTIPOINT_CORRECTNESS_FIXES.md`
- Output: fix focus-map bug, remove compatibility publishes, ensure single completion path.

### Step 07 — UI wiring cleanup + remove signal_connector
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_07_UI_ONLY_WIDGETS_AND_MAIN_WINDOW.md`
- Output: no cross-widget Qt signal chains for control plane; all UI driven via UIEventBus subscriptions.

### Step 08 — Validation: unskip/replace tests, race checks, docs converge
- File: `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_STEP_08_VALIDATION_AND_DOCS.md`
- Output: tests validate the new architecture (no skips hiding legacy); simulation run works end-to-end.

## Appendix (wiring contract)

- `docs/implementation/actor_simplification/ACTOR_SIMPLIFICATION_APPENDIX_GUI_SIGNAL_MATRIX.md`

## “No cheating” checklist (global, applies to every step)

- [ ] No module-level `pytest.skip(...)` left for removed legacy paths (replace with real tests).
- [ ] No “primary mechanism is Qt signals” claims remain; `signal_connector.py` deleted.
- [ ] No `subscribe_to_bus=False` / `detach_event_bus_commands()` patterns remain.
- [ ] No UI publishes “UI state” commands to toggle UI; UI state follows backend `*StateChanged` events.
- [ ] No multipoint UI callback bridges remain; images reach UI via StreamHandler only.

## User-facing acceptance criteria (what “done” means)

- Clicking **Snap Images** and **Start Acquisition** produces correct GUI updates:
  - progress bar/tab state updates from backend events only
  - images/mosaics update via StreamHandler only
- No direct widget→widget calls for control-plane behavior; no lingering “compat” signal chains.
- The app works with controllers constructed in `ApplicationContext` (no UI-side controller construction required).
