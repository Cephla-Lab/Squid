# Code Mapping: Experiment Orchestrator

This document maps the orchestrator components to specific file locations and shows dependencies on the refactored multipoint components.

---

## Directory Structure

```
software/src/squid/
├── core/
│   ├── events.py                          # ADD: Orchestrator events
│   ├── utils/
│   │   └── cancel_token.py                # NEW: Cooperative cancellation
│   └── protocol/                          # NEW DIRECTORY
│       ├── __init__.py
│       ├── schema.py                      # Protocol dataclasses
│       └── loader.py                      # YAML loader + validation
│
├── backend/
│   └── controllers/
│       └── orchestrator/                  # NEW DIRECTORY
│           ├── __init__.py
│           ├── state.py                   # State machine + progress dataclasses
│           ├── checkpoint.py              # Checkpoint persistence
│           ├── orchestrator_controller.py # Main controller
│           ├── fluidics_executor.py       # Fluidics sequence runner
│           └── imaging_executor.py        # Per-round imaging runner
│
└── ui/
    └── widgets/
        └── orchestrator/                  # NEW DIRECTORY
            ├── __init__.py
            ├── performance_mode.py        # Main monitoring widget
            ├── protocol_loader.py         # Load protocol dialog
            └── timeline_widget.py         # Protocol timeline visualization

software/configurations/
└── protocols/                             # NEW DIRECTORY
    ├── example_sequential_fish.yaml
    └── example_cyclic_if.yaml

software/tests/
├── unit/squid/
│   ├── core/protocol/
│   │   ├── test_schema.py
│   │   └── test_loader.py
│   └── backend/controllers/orchestrator/
│       ├── test_state.py
│       ├── test_checkpoint.py
│       ├── test_fluidics_executor.py
│       └── test_imaging_executor.py
└── integration/squid/controllers/
    └── test_orchestrator_integration.py
```

---

## Component Dependencies

### On Refactored Multipoint Components

The orchestrator **requires** these components from the multipoint refactoring:

| Refactored Component | Used By | Purpose |
|---------------------|---------|---------|
| `AcquisitionService` | `ImagingExecutor` | Apply channel configs, trigger acquisitions |
| `ExperimentManager` | `OrchestratorController` | Create experiment folders, write metadata |
| `AcquisitionPlanner` | `OrchestratorController` | Validate protocol, estimate duration |
| `PositionController` | `ImagingExecutor` | Move to FOV positions with stabilization |
| `ZStackExecutor` | `ImagingExecutor` | Execute z-stack sequences |
| `ImageCaptureExecutor` | `ImagingExecutor` | Capture images with proper context |
| `ProgressTracker` | `ImagingExecutor` | Publish progress events per-round |
| `CoordinateTracker` | `ImagingExecutor` | Record actual positions to CSV |

### On Existing Services

| Service | Used By | Purpose |
|---------|---------|---------|
| `FluidicsService` | `FluidicsExecutor` | Execute fluidics commands |
| `ScanCoordinates` | `OrchestratorController` | Get FOV position list |
| `ChannelConfigurationManager` | `OrchestratorController` | Validate channel names |
| `ObjectiveStore` | `OrchestratorController` | Get objective info |
| `EventBus` | All components | Publish/subscribe events |

---

## New Events (core/events.py additions)

### Commands (UI → Controller)

```python
# Line ~XXX (add after existing acquisition commands)

@dataclass(frozen=True)
class LoadProtocolCommand(Event):
    protocol_path: str

@dataclass(frozen=True)
class StartExperimentCommand(Event):
    resume_from_checkpoint: bool = False

@dataclass(frozen=True)
class PauseExperimentCommand(Event):
    pass

@dataclass(frozen=True)
class ResumeExperimentCommand(Event):
    pass

@dataclass(frozen=True)
class SkipToRoundCommand(Event):
    round_index: int
    skip_fluidics: bool = False

@dataclass(frozen=True)
class SkipCurrentFOVCommand(Event):
    pass

@dataclass(frozen=True)
class AbortExperimentCommand(Event):
    pass
```

### State Events (Controller → UI)

```python
@dataclass(frozen=True)
class ProtocolLoaded(Event):
    protocol_name: str
    total_rounds: int
    total_fovs: int
    estimated_duration_hours: float

@dataclass(frozen=True)
class ProtocolLoadFailed(Event):
    path: str
    errors: List[str]

@dataclass(frozen=True)
class ExperimentStateChanged(Event):
    state: OrchestratorState
    previous_state: OrchestratorState

@dataclass(frozen=True)
class ExperimentProgressUpdate(Event):
    progress: ExperimentProgress  # Full progress snapshot

@dataclass(frozen=True)
class RoundStarted(Event):
    round_index: int
    round_name: str
    has_fluidics: bool
    has_imaging: bool

@dataclass(frozen=True)
class RoundCompleted(Event):
    round_index: int
    round_name: str
    success: bool
    error: Optional[str] = None

@dataclass(frozen=True)
class FluidicsStepStarted(Event):
    round_index: int
    step_index: int
    action: str
    description: str

@dataclass(frozen=True)
class FluidicsStepCompleted(Event):
    round_index: int
    step_index: int
    success: bool

@dataclass(frozen=True)
class ExperimentCompleted(Event):
    experiment_id: str
    success: bool
    total_duration_hours: float
    error: Optional[str] = None
```

---

## Protocol Schema Types

### core/protocol/schema.py

| Class | Fields | Purpose |
|-------|--------|---------|
| `FluidicsAction` | Enum: ADD_PROBE, WASH, INCUBATE, CLEAVE, CUSTOM | Fluidics step types |
| `FluidicsStep` | action, probe, volume_ul, time_min, cycles | Single fluidics operation |
| `FluidicsSequence` | steps: List[FluidicsStep] | Ordered fluidics operations |
| `ZStackConfig` | range_um, step_um, center_on_autofocus | Z-stack parameters |
| `AutofocusConfig` | enabled, use_laser_af, interval_fovs | Autofocus settings |
| `ImagingConfig` | channels, z_stack, autofocus, exposure_overrides | Per-round imaging settings |
| `Round` | name, fluidics, imaging, skip_imaging | Single experiment round |
| `PositionSource` | file, inline | FOV position source |
| `MicroscopeConfig` | objective, binning | Microscope settings |
| `Protocol` | name, version, microscope, positions, rounds | Complete protocol |

---

## State Machine Types

### backend/controllers/orchestrator/state.py

| Class | Purpose |
|-------|---------|
| `OrchestratorState` | Enum of controller states |
| `RoundProgress` | Progress within a single round |
| `ExperimentProgress` | Full experiment progress (serializable) |
| `ExperimentCheckpoint` | Checkpoint for crash recovery |

### State Transitions

```
IDLE
  │
  ▼ LoadProtocolCommand
LOADING
  │
  ├──► FAILED (on error)
  │
  ▼
VALIDATING
  │
  ├──► FAILED (validation errors)
  │
  ▼
READY
  │
  ▼ StartExperimentCommand
RUNNING_FLUIDICS ◄────────────────┐
  │                               │
  ├──► PAUSED ──► (resume) ───────┤
  │                               │
  ├──► ABORTING ──► COMPLETED     │
  │                               │
  ▼ (fluidics complete)           │
RUNNING_IMAGING                   │
  │                               │
  ├──► PAUSED ──► (resume) ───────┤
  │                               │
  ├──► ABORTING ──► COMPLETED     │
  │                               │
  ▼ (imaging complete)            │
COMPLETING_ROUND                  │
  │                               │
  ├──► (next round) ──────────────┘
  │
  ▼ (all rounds done)
COMPLETED
  │
  ▼
IDLE
```

---

## Integration Points

### application.py

```python
# Add to Application.__init__() or _create_controllers():

from squid.backend.controllers.orchestrator import OrchestratorController

self._orchestrator = OrchestratorController(
    event_bus=self._event_bus,
    # From refactored multipoint:
    experiment_manager=self._experiment_manager,
    acquisition_planner=self._acquisition_planner,
    acquisition_service=self._acquisition_service,
    position_controller=self._position_controller,
    # Existing services:
    fluidics_service=self._fluidics_service,
    scan_coordinates=self._scan_coordinates,
    channel_config_manager=self._channel_config_manager,
    objective_store=self._objective_store,
)
```

### main_window.py

```python
# Add menu:
def _create_menus(self):
    # ... existing menus ...

    experiment_menu = self.menuBar().addMenu("Experiment")

    load_protocol = QAction("Load Protocol...", self)
    load_protocol.triggered.connect(self._show_protocol_loader)
    experiment_menu.addAction(load_protocol)

    experiment_menu.addSeparator()

    performance_mode = QAction("Performance Mode", self)
    performance_mode.triggered.connect(self._show_performance_mode)
    experiment_menu.addAction(performance_mode)
```

---

## File Creation Order

### Phase 1: Protocol System (2 days)
1. `core/protocol/__init__.py`
2. `core/protocol/schema.py`
3. `core/protocol/loader.py`
4. `tests/unit/squid/core/protocol/test_schema.py`
5. `tests/unit/squid/core/protocol/test_loader.py`
6. `configurations/protocols/example_sequential_fish.yaml`

### Phase 2: State Management + CancelToken (1.5 days)
7. `core/utils/cancel_token.py`
8. `tests/unit/squid/core/utils/test_cancel_token.py`
9. `backend/controllers/orchestrator/__init__.py`
10. `backend/controllers/orchestrator/state.py`
11. `backend/controllers/orchestrator/checkpoint.py`
12. `tests/unit/squid/backend/controllers/orchestrator/test_state.py`
13. `tests/unit/squid/backend/controllers/orchestrator/test_checkpoint.py`

### Phase 3: Controller (3 days)
14. Add events to `core/events.py`
15. `backend/controllers/orchestrator/fluidics_executor.py`
16. `backend/controllers/orchestrator/imaging_executor.py`
17. `backend/controllers/orchestrator/orchestrator_controller.py`
18. `tests/unit/squid/backend/controllers/orchestrator/test_fluidics_executor.py`
19. `tests/unit/squid/backend/controllers/orchestrator/test_imaging_executor.py`

### Phase 4: UI (2 days)
20. `ui/widgets/orchestrator/__init__.py`
21. `ui/widgets/orchestrator/timeline_widget.py`
22. `ui/widgets/orchestrator/performance_mode.py`
23. `ui/widgets/orchestrator/protocol_loader.py`

### Phase 5: Integration (2 days)
24. Modify `application.py`
25. Modify `main_window.py`
26. `tests/integration/squid/controllers/test_orchestrator_integration.py`

---

## Lines of Code Estimate

| File | Estimated LOC |
|------|---------------|
| cancel_token.py | ~120 |
| schema.py | ~150 |
| loader.py | ~200 |
| state.py | ~100 |
| checkpoint.py | ~80 |
| fluidics_executor.py | ~120 |
| imaging_executor.py | ~200 |
| orchestrator_controller.py | ~400 |
| performance_mode.py | ~350 |
| protocol_loader.py | ~150 |
| timeline_widget.py | ~100 |
| events.py additions | ~100 |
| **Total New Code** | **~2,070** |

| Test File | Estimated LOC |
|-----------|---------------|
| test_cancel_token.py | ~150 |
| test_schema.py | ~100 |
| test_loader.py | ~150 |
| test_state.py | ~80 |
| test_checkpoint.py | ~100 |
| test_fluidics_executor.py | ~150 |
| test_imaging_executor.py | ~150 |
| test_orchestrator_integration.py | ~200 |
| **Total Test Code** | **~1,080** |

---

## CancelToken Architecture

### Checkpoint Locations

Pause/abort only happens at **operation boundaries**, not mid-operation:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Experiment Execution                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  for round in rounds:                                           │
│      ──► CHECKPOINT (between rounds) ◄──                        │
│      │                                                          │
│      │  [Fluidics Phase]                                        │
│      │  for step in fluidics_steps:                             │
│      │      ──► CHECKPOINT (between steps) ◄──                  │
│      │      │                                                   │
│      │      └── execute_step()  ← ATOMIC (wash, incubate, etc.) │
│      │                                                          │
│      │  [Imaging Phase]                                         │
│      │  for fov in fovs:                                        │
│      │      ──► CHECKPOINT (between FOVs) ◄──                   │
│      │      │                                                   │
│      │      └── acquire_fov()   ← ATOMIC (move + AF + z-stack)  │
│      │                                                          │
└─────────────────────────────────────────────────────────────────┘
```

### Atomic Operations (No Pause/Abort)

These operations run to completion once started:

| Operation | Includes | Duration |
|-----------|----------|----------|
| Fluidics Step | Wash cycle (all cycles), incubation, cleavage | Seconds to minutes |
| FOV Acquisition | Stage move + stabilization, autofocus, z-stack (all z-levels), all channels | 1-30 seconds |

### CancelToken Flow

```
UI Thread                          Worker Thread
─────────                          ─────────────
                                   checkpoint(ctx) ──┐
                                                     │ checks flags
                                                     │
request_pause() ──────────────────►  _pause.set()   │
                                                     │
                                   ┌─────────────────┘
                                   │ pause detected
                                   ▼
                                   on_paused(ctx) callback
                                   │
                                   │ blocked in while loop
                                   │
request_resume() ─────────────────► _pause.clear()
                                   │
                                   │ unblocked
                                   ▼
                                   on_resumed() callback
                                   │
                                   │ continues execution
                                   ▼
```

### AbortMode

```python
class AbortMode(Enum):
    SOFT = auto()   # Wait for current atomic operation, then abort
    HARD = auto()   # Future: immediate stop (may leave hardware in bad state)
```

Currently only SOFT abort is implemented. HARD abort would require additional logic to safely stop mid-operation.

---

## Architectural Notes

### Why This Design

1. **Composition over Inheritance**: OrchestratorController delegates to executors rather than extending MultiPointController

2. **Event-Driven UI**: Performance mode widget subscribes to events, doesn't poll controller

3. **Checkpoint After Every FOV**: Enables resume from any point, not just round boundaries

4. **Separate Executors**: FluidicsExecutor and ImagingExecutor can be tested independently

5. **Protocol as Data**: YAML protocol is pure data, no code execution

### Threading Model

```
Main Thread (Qt)
    │
    ├── Performance Mode Widget (subscribes to events)
    │
    └── OrchestratorController (receives commands)
            │
            └── Worker Thread (runs experiment)
                    │
                    ├── FluidicsExecutor (blocking fluidics calls)
                    │
                    └── ImagingExecutor
                            │
                            └── [Uses refactored multipoint components]
                                    │
                                    └── Camera callbacks (from SDK thread)
```

### Pause/Resume Implementation

```python
# Check flags at safe points:
def _main_loop():
    while ...:
        # Safe pause point: between FOVs
        if pause_flag.is_set():
            save_checkpoint()
            while pause_flag.is_set() and not abort_flag.is_set():
                time.sleep(0.1)

        if abort_flag.is_set():
            break

        # Do work...
```

Flags are `threading.Event` objects, checked:
- Before each fluidics step
- Before each FOV
- After each FOV completes
- NOT during camera exposure or stage movement (unsafe)
