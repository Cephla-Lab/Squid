# Actor Refactor Simplification — Step 08: Validation, Race Tests, and Documentation Convergence

## Goal

Finish with a stable, documented architecture:

- One control-plane thread.
- No callbacks.
- Mode gate enforced.
- UI-only frontend.
- Tests updated to the new world (no legacy skips).

## Implementation checklist

### 8.1 Test suite updates
- [x] Remove any skipped legacy callback tests.
- [x] Rewrite tests to:
  - publish commands via EventBus
  - assert controller/service effects via events/state
- [x] Ensure tests do not assume lease-based coordinator behavior.

Audit-required unskips:
- [x] Replace module-level skips:
  - `tests/unit/control/gui/test_qt_signal_bridges.py`
  - `tests/unit/control/core/test_multi_point_utils.py`
  with tests that validate StreamHandler + EventBus routing in the new architecture.

### 8.2 Targeted race/stress tests
- [x] Add or update tests for:
  - Rapid Start/Stop Live sequences.
  - Abort during acquisition startup.
  - Second acquisition after first completes.
  - Command interleaving (MoveStage during acquisition is rejected).
- [x] Keep these small and deterministic (no sleeps unless bounded).

### 8.3 Manual end-to-end validation
- [ ] Simulation run:
  - `python src/main_hcs.py --simulation`
  - start live, stop live, start acquisition, abort, start again.
- [ ] Real hardware smoke (when available):
  - verify no deadlocks on second acquisition.

### 8.4 Documentation finalization
- [ ] Update `ACTOR_MODEL_REFACTOR.md`:
  - mark removed components and replaced mechanisms as deleted.
  - note final mode gate + control-thread model.
- [ ] Add a “How to extend” section:
  - how to add a new command/event
  - where to put controller vs service logic
  - how to add a worker safely
- [ ] Update any architecture diagrams to match final state.

### 8.5 Codebase hygiene
- [x] Grep for forbidden patterns:
  - `rg "callback|Callbacks|QObject|qtpy" src/squid/mcs src/squid/ops src/squid/core -S`
  - UI-only Qt usage should remain in `src/squid/ui`.
- [x] Ensure data-plane frames never published on EventBus.

Audit reference:
- `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`

## Exit criteria

- Tests pass locally (or known unrelated failures are documented).
- Manual simulation run is stable.
- Docs reflect final architecture and invariants.

## Audit reference

- `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`
