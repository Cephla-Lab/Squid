# Focus Lock Implementation Chunks

## Overview

UI-first implementation with simulator. Build UI and iterate on design with simulated data, then implement real backend.

## Phases

### Phase A: Foundation + UI (Start Here)
| Chunk | File | Description | Status |
|-------|------|-------------|--------|
| 1 | [chunk-01-events-config.md](chunk-01-events-config.md) | Events and Configuration | [x] |
| 2 | [chunk-02-simulator.md](chunk-02-simulator.md) | Focus Lock Simulator | [x] |
| 3 | [chunk-03-ui-widget.md](chunk-03-ui-widget.md) | UI Widget | [x] |

**Milestone**: Demo UI with simulated data, iterate on design.

### Phase B: Backend Implementation
| Chunk | File | Description | Status |
|-------|------|-------------|--------|
| 4 | [chunk-04-laser-af-result.md](chunk-04-laser-af-result.md) | LaserAFResult and Spot Metrics | [x] |
| 5 | [chunk-05-continuous-api.md](chunk-05-continuous-api.md) | Continuous Measurement API | [x] |
| 6 | [chunk-06-controller.md](chunk-06-controller.md) | ContinuousFocusLockController | [x] |

**Milestone**: Real focus lock controller working in simulation.

### Phase C: Integration
| Chunk | File | Description | Status |
|-------|------|-------------|--------|
| 7 | [chunk-07-mode-gate.md](chunk-07-mode-gate.md) | Mode Gate Bypass | [x] |
| 8 | [chunk-08-app-wiring.md](chunk-08-app-wiring.md) | Application Wiring | [x] |
| 9 | [chunk-09-acquisition.md](chunk-09-acquisition.md) | Acquisition Integration | [x] |

**Milestone**: Full integration with multipoint acquisition.

### Phase D: Polish
| Chunk | File | Description | Status |
|-------|------|-------------|--------|
| 10 | [chunk-10-safety.md](chunk-10-safety.md) | Safety and Polish | [ ] |
| 11 | [chunk-11-af-preview.md](chunk-11-af-preview.md) | AF Camera Preview (Optional) | [ ] |

**Milestone**: Production-ready with safety features.

## Dependency Graph

```
Phase A:  1 ──> 2 ──> 3

Phase B:  4 ──> 5 ──> 6

Phase C:  7 ──┐
              ├──> 8 ──> 9
          6 ──┘

Phase D:  10 ──> 11 (optional)
```

## Key Architecture Decisions

### EventBus Scope

**EventBus is for decoupled communication across the system.**

Primary patterns:
- **UI → Backend**: Widgets publish Commands → Controllers/Services subscribe
- **Backend → UI**: Controllers/Services publish State events → Widgets subscribe
- **Backend → Backend**: Used for coordination and state awareness between components

Backend-to-backend usage examples (from codebase):
- `MultiPointController` subscribes to `AcquisitionWorkerFinished` from worker thread
- `TrackingController`, `LaserAutofocusController` subscribe to `ObjectiveChanged`
- `NavigationStateService` aggregates events from multiple components

```
┌─────────────────────────────────────────────────────────┐
│  UI Layer                                               │
│    └── Widgets publish Commands, subscribe to State     │
└──────────────────────────┬──────────────────────────────┘
                           │ EventBus
┌──────────────────────────┴──────────────────────────────┐
│  Backend Layer                                          │
│    ├── Controllers subscribe to Commands from UI        │
│    ├── Controllers publish State events to UI           │
│    ├── Workers publish completion events to Controllers │
│    └── Components subscribe to shared events            │
│        (e.g., ObjectiveChanged, AcquisitionStarted)     │
└─────────────────────────────────────────────────────────┘
```

**For Focus Lock**, this means:
- Controller subscribes to UI commands (`StartFocusLockCommand`, etc.)
- Controller publishes state events to UI (`FocusLockStatusChanged`, etc.)
- Controller subscribes to `ObjectiveChanged` to invalidate reference
- Controller subscribes to `AcquisitionStarted`/`AcquisitionFinished` for auto_lock mode
- Direct method calls still OK for synchronous operations (e.g., `wait_for_lock()`)

### Data Plane Separation (EventBus vs StreamHandler)

- **EventBus** = Control plane only (commands, state changes, lightweight events)
- **StreamHandler pattern** = Data plane for frames (direct callbacks, no queue)

For AF camera preview (Chunk 11), use dedicated `FocusLockStreamHandler`:
```
Backend (no Qt):
  FocusLockStreamHandler.push_frame(FocusLockFrame)

Frontend (Qt):
  QtFocusLockStreamHandler wraps backend, emits Qt signals
```

This maintains strict backend/frontend separation - same pattern as camera `StreamHandler`/`QtStreamHandler`.

## Reference Documents
- [Spec](../spec.md)
- [Full Plan](../plan.md)
