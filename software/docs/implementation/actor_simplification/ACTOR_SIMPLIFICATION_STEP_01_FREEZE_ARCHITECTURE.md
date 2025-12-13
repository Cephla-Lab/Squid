# Actor Refactor Simplification — Step 01: Freeze Target Architecture and Invariants

This step is *documentation + alignment only*. No code changes yet.

## Goal

Lock in the final architecture so every subsequent change converges to the same model:

- **Single control-plane queue + thread**: the queued `EventBus` dispatch thread is the backend “actor.”
- **Widgets render + emit commands only** via `UIEventBus`.
- **Controllers own state/workflows**; subscribe/publish on core `EventBus`; call services directly.
- **Services own hardware access**; thread-safe; may subscribe/publish on core `EventBus`.
- **Data plane is StreamHandler-only** (frames never on EventBus).
- **No callbacks anywhere in control plane** (worker/controller/UI all via events, frames via StreamHandler).

## Rationale

The repo currently has *two* control-plane threads (EventBus dispatch + BackendActor). This violates your clarified architecture and reintroduces cross-thread hardware access. Before refactoring, we need a firm contract to prevent drift.

## Deliverables

1. A short “final architecture” section added to:
   - `docs/implementation/ACTOR_MODEL_REFACTOR.md`
   - `docs/implementation/SERVICE_LAYER_ARCHITECTURE.md`
2. A single list of **non-negotiable invariants** (below) referenced by later steps.

## Non‑negotiable invariants (acceptance criteria)

- **I1**: *Only one backend control thread exists.* All controller + service handlers execute on that thread.
- **I2**: *UI thread never runs controller/service logic.*
- **I3**: *No control-plane callbacks exist.* All control-plane communication is via EventBus events.
- **I4**: *Frames never go through EventBus.* Frames travel only via StreamHandler/Qt signals.
- **I5**: *Long operations never block the control thread.* They run in worker threads and report via events.
- **I6**: *Unsafe commands are backend-gated during acquisition/live conflicts.*

## Mechanical steps

- [x] Update `ACTOR_MODEL_REFACTOR.md` with:
  - A “Final Architecture” section matching the diagram you provided.
  - A “Single control-plane thread” note: EventBus dispatch thread is the backend actor.
  - A “No callbacks” note: callbacks are treated as hard failures.
- [x] Update `SERVICE_LAYER_ARCHITECTURE.md` to reference the same invariants.
- [x] Add a short “Control vs Data plane” paragraph to both docs.
- [x] Add a “Do not add new shims/compat” warning.

## Verification

- Documentation review only. No tests.

## Exit criteria

- Both docs updated and cross-linked.
- Invariants I1–I6 are explicitly written and agreed on.

## Inputs (audit)

- `docs/implementation/actor_simplification/ACTOR_HARD_AUDIT.md`
