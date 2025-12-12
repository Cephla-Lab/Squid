# Actor Refactor Simplification — Step 02: One Control Thread (Remove BackendActor/Router)

## Goal

Converge to **one control-plane queue/thread** by eliminating `BackendActor` and `BackendCommandRouter`. The queued `EventBus` dispatch thread becomes the only backend “actor.”

After this step:

- UI publishes commands → queued `EventBus` → controllers/services handle on EventBus thread.
- No command hop into a second actor thread.

## Why this is required

Current state:

- `EventBus` already runs a dispatch thread and executes handlers there.
- `BackendCommandRouter` subscribes to *commands* on `EventBus` and enqueues to `BackendActor`.
- Controllers handle routed commands on BackendActor thread, while services still handle some commands on EventBus thread.

This creates **two backend threads** touching services/hardware (violates invariant I1).

## Files/symbols involved

Backend actor infra:
- `src/squid/core/actor/backend_actor.py`
- `src/squid/core/actor/command_router.py`
- `src/squid/core/actor/thread_assertions.py`
- `src/squid/core/actor/__init__.py`

Wiring:
- `src/squid/application.py::_build_backend_actor`
- `src/squid/application.py::shutdown` (actor stop/unregister)

Controller detach hooks:
- `detach_event_bus_commands` methods on controllers.

Tests/docs:
- `tests/unit/squid/core/test_backend_actor.py`
- `tests/unit/squid/core/test_command_router.py`
- Any doc referencing BackendActor.

## Implementation checklist

### 2.1 Remove infra + wiring
- [ ] Delete `src/squid/core/actor/` package entirely.
- [ ] Remove imports of `squid.core.actor.*` everywhere.
  - Grep: `rg "squid.core.actor" src/squid -S`
- [ ] Delete `ApplicationContext._backend_actor` and `_command_router` fields.
- [ ] Delete `_build_backend_actor()` and its invocation in `ApplicationContext.__init__`.
- [ ] Remove actor stop/unregister in `ApplicationContext.shutdown`.

### 2.2 Re-enable direct EventBus command subscriptions
- [ ] In `ApplicationContext`, stop calling `detach_event_bus_commands()` on controllers.
- [ ] Ensure controllers are built with:
  - `event_bus=event_bus`
  - `subscribe_to_bus=True` (or defaulted true).
- [ ] Verify each controller subscribes to its commands in `_subscribe_to_bus`.

Controllers currently actor-routed:
- LiveController: `StartLiveCommand`, `StopLiveCommand`, etc.
- MicroscopeModeController: `SetMicroscopeModeCommand`, `UpdateChannelConfigurationCommand`.
- PeripheralsController: DAC, spinning disk, objective, etc.
- AutoFocusController: autofocus commands.
- LaserAutofocusController: laser AF commands.
- MultiPointController: acquisition commands.
- ImageClickController: `ImageCoordinateClickedCommand`, `ClickToMoveEnabledChanged`.

### 2.3 Remove actor-only controller toggles

These patterns exist solely to support “detach EventBus subscriptions and route via BackendActor”:

- `subscribe_to_bus: bool`
- `detach_event_bus_commands()`
- `attach_event_bus()` (when only used for actor wiring)

After BackendActor removal, delete these toggles and make controllers always subscribe when given a bus.

- [ ] Delete `subscribe_to_bus` params across controllers.
- [ ] Delete `detach_event_bus_commands` across controllers.
- [ ] Delete `attach_event_bus` if no longer needed (keep only if genuinely required for DI/test wiring).

### 2.3 Remove priority routing assumptions
- [ ] Search for places relying on “Stop commands go first” due to BackendActor priority.
- [ ] For now: accept FIFO ordering (still deterministic).
- [ ] Optional follow-up (Step 08): add priority to EventBus if needed.

### 2.4 Tests + cleanup
- [ ] Remove unit tests that are only about BackendActor/router.
- [ ] Replace any integration tests expecting backend actor thread with “EventBus dispatch thread” expectations.
- [ ] Update docs to remove mentions of BackendActor/Router.

## Verification

Run locally (no network):

- Unit: `NUMBA_DISABLE_JIT=1 pytest tests/unit/squid/core -v`
- Integration smoke (if present): `NUMBA_DISABLE_JIT=1 pytest tests/integration -v`
- Simulation launch: `python src/main_hcs.py --simulation`

## Exit criteria

- No `src/squid/core/actor/` left.
- No references to BackendActor/Router in code or docs.
- Controllers/services handle all commands on EventBus dispatch thread.
