# Experiment Orchestrator

## Status: Planning

## Problem

Currently, complex multi-round experiments (e.g., sequential FISH, cyclic immunofluorescence, fluidics-based spatial omics) require:
1. Manual intervention between imaging rounds
2. No pause/resume capability for long acquisitions
3. No unified protocol file format for experiment reproducibility
4. GUI-heavy interaction that precludes "walk-away" experiments

Users want to define an entire experiment upfront and have it execute autonomously with:
- Multiple imaging rounds with different parameters
- Fluidics sequences between rounds (probe addition, washes, etc.)
- Ability to pause, resume, skip, or restart at any point
- Progress visualization in a simplified "performance mode" UI
- Manual intervention capability (adjust autofocus, skip positions) without aborting

## Solution

Create an **Experiment Orchestrator** system with three main components:

### 1. Protocol Definition Format (YAML)
A declarative schema for defining complete experiments:
```yaml
protocol:
  name: "10-Round Sequential FISH"
  version: "1.0"

microscope:
  objective: "20x"

positions:
  source: "positions.csv"  # or inline definition

rounds:
  - name: "Round 1 - DAPI + Probe A"
    fluidics:
      steps:
        - action: add_probe
          probe: "Probe_A"
          volume_ul: 200
        - action: incubate
          time_min: 30
        - action: wash
          cycles: 3
    imaging:
      channels: ["DAPI", "FITC"]
      z_stack:
        range_um: 10
        step_um: 0.5
      autofocus:
        enabled: true
        interval: 5  # every 5 FOVs
```

### 2. Orchestrator Controller
A state machine that sequences rounds of acquisition + fluidics:
- Wraps refactored `MultiPointController` components
- Manages round transitions and checkpointing
- Handles pause/resume/skip commands
- Persists state for crash recovery

### 3. Performance Mode UI
A simplified "operator" interface showing:
- Overall experiment progress (rounds, FOVs, time)
- Current activity indicator
- Protocol timeline with position marker
- Intervention controls (pause, skip, adjust AF)
- Live image preview

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Experiment Orchestrator                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Protocol Loader ──────► Protocol (dataclass)                       │
│       │                       │                                     │
│       ▼                       ▼                                     │
│  OrchestratorController ◄──── ExperimentState                       │
│       │                       │                                     │
│       │                       ▼                                     │
│       │                  Checkpoint Manager ◄──► checkpoint.json    │
│       │                                                             │
│       ├────────────────────────────────────────────────────────────┤
│       │                                                             │
│       │  Per-Round Execution:                                       │
│       │  ┌─────────────────┐    ┌─────────────────┐                │
│       ├──► FluidicsExecutor │    │ ImagingExecutor │◄──┤            │
│       │  └─────────────────┘    └─────────────────┘   │            │
│       │         │                       │              │            │
│       │         ▼                       ▼              │            │
│       │  FluidicsService         Refactored Components:            │
│       │                          - ExperimentManager               │
│       │                          - AcquisitionPlanner              │
│       │                          - ImageCaptureExecutor            │
│       │                          - ProgressTracker                 │
│       │                          - PositionController              │
│       │                          - FocusOperations                 │
│       │                                                             │
└───────┴─────────────────────────────────────────────────────────────┘
         │
         ▼ Events
┌─────────────────────────────────────────────────────────────────────┐
│                    Performance Mode UI                              │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │ Progress    │  │ Timeline    │  │ Intervention│                 │
│  │ Dashboard   │  │ View        │  │ Controls    │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   Live Image Preview                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Dependencies

**Assumes completed:**
- Multipoint refactoring (Phases 1-5) from `conductor/tracks/multipoint-refactor-20251230/`
- Specifically requires:
  - `AcquisitionService` - for hardware orchestration primitives
  - `ExperimentManager` - for per-round folder/metadata setup
  - `AcquisitionPlanner` - for validation and estimation
  - `ProgressTracker` - for progress events
  - `PositionController` - for pause/resume position state
  - `ImageCaptureExecutor` - for capture operations

## Scope

### In Scope
- Protocol YAML schema and parser with validation
- OrchestratorController state machine
- Pause/resume/skip/restart functionality
- Checkpoint persistence for crash recovery
- Performance mode UI widget
- Integration with existing FluidicsService
- Basic protocol editor (load/save/validate)

### Out of Scope (Future)
- Visual protocol designer/builder
- Real-time analysis triggers (e.g., "if cell count > X, skip round")
- Multi-microscope orchestration
- Cloud-based protocol sharing
- Advanced scheduling (time-based triggers)

## Success Metrics

| Metric | Target |
|--------|--------|
| Protocol load time | < 2 seconds |
| Checkpoint save frequency | Every FOV completion |
| Resume from checkpoint | < 5 seconds |
| UI update latency | < 100ms |
| Memory overhead | < 50MB additional |

## Documents

- [plan.md](plan.md) - Detailed implementation plan
- [code-mapping.md](code-mapping.md) - Code structure and file mapping
