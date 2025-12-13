# Actor Refactor Simplification — Step 03: Replace ResourceCoordinator With Minimal Mode Gate

## Goal

Replace the current lease-based `ResourceCoordinator` with a simple **backend-owned mode gate**:

- `GlobalMode = IDLE | LIVE | ACQUIRING | ABORTING | ERROR`
- Controllers set mode when they start/stop long ops.
- Services reject/ignore unsafe commands when mode disallows them.

This provides the safety of Step 4 in Plan 1 without lease complexity.

## Why

The lease system adds:

- UUID leases, multi-resource ownership bookkeeping.
- Expiration watchdog.
- Callbacks and extra events.

But it **does not actually prevent** unsafe commands today because services don’t consult it. A mode gate is simpler and more directly enforces correctness (invariant I6).

## Design

### New component

Create `GlobalModeGate` (name flexible):

- Location: `src/squid/core/mode_gate.py`
- Runs on EventBus thread (single control plane).
- Holds `self._mode: GlobalMode`.
- Exposes:
  - `get_mode()`
  - `set_mode(new_mode, reason)`
  - `can_execute(command_type)` or per-resource helpers.
- Publishes `GlobalModeChanged(old_mode, new_mode, reason)` via EventBus.

### Enforcement point

Add mode checks in services’ command handlers:

- `StageService`: block all movement/homing/zeroing while `mode in {ACQUIRING, ABORTING}`.
- `CameraService`: block configuration changes during acquisition (except ones called directly by controllers).
- `IlluminationService`, `PeripheralService`, `PiezoService`, etc.: block unsafe toggles if required.

Controllers do not need to publish commands for operations they own; they can call services directly regardless of mode.

## Files involved

- Remove: `src/squid/core/coordinator.py`
- Remove coordinator events from `src/squid/core/events.py`:
  - `LeaseAcquired`, `LeaseReleased`, `LeaseRevoked` (and any lease-only helpers).
  - Keep or repurpose `GlobalModeChanged`.
- Wiring: `src/squid/application.py` no longer builds coordinator.
- Controllers:
  - `LiveController` remove `_acquire_resources/_release_resources`, replace with mode changes.
  - `MultiPointController` same.
  - `AutoFocusController` same.
- Services in `src/squid/mcs/services/*`.

## Implementation checklist

### 3.1 Introduce GlobalModeGate
- [x] Add `src/squid/core/mode_gate.py` with `GlobalMode` enum (reuse existing names).
- [x] Add `GlobalModeChanged` event (if not already present post-cleanup).
- [x] Gate is created in `ApplicationContext` and stored as `self._mode_gate`.
- [x] Provide `.mode_gate` property for dependency injection.

### 3.2 Remove ResourceCoordinator
- [x] Delete `src/squid/core/coordinator.py`.
- [x] Remove all imports/uses of `ResourceCoordinator`, `ResourceLease`, `ACQUISITION_REQUIRED_RESOURCES`, etc.
  - Grep: `rg "ResourceCoordinator|ResourceLease|acquire\\(|release\\(|LEASE" src/squid -S`
- [x] Remove coordinator build/start/stop from `ApplicationContext`.

### 3.3 Update controllers to use mode gate
- [x] `LiveController`:
  - On successful start: `mode_gate.set_mode(LIVE, reason="live start")`
  - On stop: `mode_gate.set_mode(IDLE, reason="live stop")`
  - On start failure: restore previous mode.
- [x] `MultiPointController`:
  - Before spawning worker: set `ACQUIRING`.
  - On abort request: set `ABORTING`.
  - On completion/cleanup: restore to `IDLE` or `LIVE` if previously live.
- [x] `AutoFocusController`:
  - Full autofocus start: if idle, set `ACQUIRING` (or a separate `FOCUSING` if desired).
  - On completion: restore previous mode.
  - Focus-map quick path must also respect mode (see Step 04/05).

### 3.4 Enforce in services
- [x] Add optional `mode_gate` dependency to each service ctor OR access via EventBus subscription to `GlobalModeChanged`.
- [x] For each command handler:
  - If `mode_gate.mode in blocked_modes` and handler was invoked via EventBus command, **return without touching hardware** and log.
  - Publish a rejection event if UI needs feedback (optional).

Blocked command types (initial list):
- Stage moves, homes, zeroes, loading/scanning moves.
- StartLive/StopLive (StartLive blocked during acquisition).
- Microscope mode changes.
- Illumination/peripheral changes that could interfere with acquisition.

### 3.5 Tests + doc cleanup
- [x] Delete coordinator unit tests.
- [x] Add tests for:
  - Mode transitions from controllers.
  - StageService rejects moves during acquisition.
- [x] Remove lease/coordinator docs.

## Verification

- Unit: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/core tests/unit/squid/services -v`
- Integration acquisition safety: start acquisition then publish MoveStageCommand; assert stage doesn’t move.

## Exit criteria

- No coordinator/leases remain.
- Mode gate is the only backend arbitration.
- Unsafe commands are blocked at service layer.

## Audit reference

- `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`
