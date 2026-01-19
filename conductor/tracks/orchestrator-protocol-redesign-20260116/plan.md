# Orchestrator Protocol Schema Redesign

## Overview

Redesign the protocol schema to support **flexible step sequences** per round, **named resources** (imaging configs, FOV sets, fluidics protocols), and **simple looping** via `repeat: N` with `{i}` substitution.

---

## Goals

1. **Flexible step ordering** - Fluidics and imaging can be interleaved in any order
2. **Named resources** - Define imaging configs, FOV sets, and fluidics protocols as reusable variables
3. **Simple looping** - `repeat: N` with `{i}` substitution for multi-round experiments
4. **Reduced verbosity** - No more duplicating 20 identical round definitions
5. **Configurable error handling** - Define failure behaviors in the protocol

---

## Design

### Protocol Structure

```yaml
name: "20-Round FISH Experiment"
version: "2.0"

# ============ Settings ============

output_directory: "/data/experiments/fish_2024"

error_handling:
  focus_failure: "skip"         # skip, abort, pause
  fluidics_failure: "abort"
  imaging_failure: "warn"

# ============ Resources ============

fluidics_protocols:
  prime:
    steps:
      - operation: prime
        solution: "wash_buffer"
        volume_ul: 500
  wash:
    file: "protocols/standard_wash.yaml"

imaging_configs:
  fish_standard:
    description: "Standard FISH imaging"
    channels:
      - name: "DAPI"
        exposure_time_ms: 100
      - name: "Cy5"
        exposure_time_ms: 200
        illumination_intensity: 80
    z_stack:
      planes: 5
      step_um: 0.5
    focus:
      enabled: true
      method: "laser"
  overview:
    file: "configs/overview.yaml"

fov_sets:
  main_grid: "positions/main_grid.csv"
  sparse: "positions/sparse_qc.csv"

# ============ Workflow ============

rounds:
  # Setup
  - name: "Prime System"
    steps:
      - step_type: fluidics
        protocol: prime

  # Main experiment loop
  - name: "Round {i}"
    repeat: 20
    steps:
      - step_type: fluidics
        protocol: hybridize_{i}
      - step_type: fluidics
        protocol: flow_buffer
      - step_type: imaging
        config: fish_standard
        fovs: main_grid
      - step_type: fluidics
        protocol: cleave
      - step_type: fluidics
        protocol: wash

  # Final QC
  - name: "QC Overview"
    steps:
      - step_type: imaging
        config: overview
        fovs: sparse
```

### Key Concepts

**Resource Definitions** - Named resources at the top level, either inline or via file reference:
- `fluidics_protocols:` - Named fluidics sequences
- `imaging_configs:` - Named imaging configurations with channels, z-stack, focus lock
- `fov_sets:` - Named FOV position sets (CSV file paths)

**Step-Based Rounds** - Each round contains an ordered list of steps using `step_type` discriminator:
- `step_type: fluidics` + `protocol: "protocol_name"` - Run a fluidics protocol
- `step_type: imaging` + `config: "..."` + `fovs: "..."` - Run imaging with specified config and FOV set
- `step_type: intervention` + `message: "..."` - Pause for operator intervention

**Repeat for Looping** - `repeat: N` expands a round N times with `{i}` substitution (1, 2, 3, ...)

**Error Handling** - Protocol-level defaults for how to handle failures:
- `skip` - Skip the failed item, continue
- `abort` - Stop the experiment
- `pause` - Pause for operator intervention
- `warn` - Log warning, continue

---

## Schema Models

### Core Models

```python
class StepType(str, Enum):
    FLUIDICS = "fluidics"
    IMAGING = "imaging"
    INTERVENTION = "intervention"


class ImagingStepConfig(BaseModel):
    config: str  # Name of imaging config
    fovs: str    # Name of FOV set


class Step(BaseModel):
    type: StepType
    protocol: Optional[str] = None        # For fluidics
    imaging: Optional[ImagingStepConfig] = None  # For imaging
    message: Optional[str] = None         # For intervention


class Round(BaseModel):
    name: str
    steps: List[Step] = Field(default_factory=list)
    repeat: Optional[int] = None  # If set, expand with {i} substitution
```

### Imaging Config Models

```python
class ChannelConfig(BaseModel):
    """Per-channel imaging parameters.

    The `name` must reference a channel from the system's channel_definitions.
    Other fields override the channel defaults for this imaging config.
    """
    name: str                                    # Reference to channel_definitions
    exposure_time_ms: Optional[float] = None     # Override default exposure
    analog_gain: Optional[float] = None          # Override default gain
    illumination_intensity: Optional[float] = None  # 0-100%, override default
    z_offset_um: float = 0.0                     # Per-channel z offset


class ZStackConfig(BaseModel):
    planes: int = 1
    step_um: float = 0.5
    direction: Literal["from_center", "from_bottom", "from_top"] = "from_center"


class FocusConfig(BaseModel):
    """Focus acquisition settings (gate before imaging each FOV)."""
    enabled: bool = False
    method: Literal["laser", "contrast", "reflection", "none"] = "laser"
    channel: Optional[str] = None    # For contrast AF: which channel to use
    interval_fovs: int = 1           # Run every N FOVs (1 = every FOV)
    # Additional parameters TBD (settle_time, timeout, retries)


class ImagingConfig(BaseModel):
    """Named imaging configuration."""
    description: str = ""
    channels: List[Union[str, ChannelConfig]]  # String = use channel defaults
    z_stack: ZStackConfig = Field(default_factory=ZStackConfig)
    focus: FocusConfig = Field(default_factory=FocusConfig)
    skip_saving: bool = False        # For preview/test rounds
    file: Optional[str] = None       # Alternative: load from file
```

### Error Handling Models

```python
class FailureAction(str, Enum):
    SKIP = "skip"
    ABORT = "abort"
    PAUSE = "pause"
    WARN = "warn"


class ErrorHandlingConfig(BaseModel):
    focus_failure: FailureAction = FailureAction.SKIP
    fluidics_failure: FailureAction = FailureAction.ABORT
    imaging_failure: FailureAction = FailureAction.WARN
```

### Protocol Model

```python
class ExperimentProtocol(BaseModel):
    name: str
    version: str = "2.0"

    # Settings
    output_directory: Optional[str] = None
    error_handling: ErrorHandlingConfig = Field(default_factory=ErrorHandlingConfig)

    # Resource definitions
    fluidics_protocols: Dict[str, Any] = Field(default_factory=dict)
    imaging_configs: Dict[str, Any] = Field(default_factory=dict)
    fov_sets: Dict[str, str] = Field(default_factory=dict)

    # Workflow
    rounds: List[Round] = Field(default_factory=list)
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/core/protocol/schema.py` | Add `Step`, `StepType`, update `Round`, update `ExperimentProtocol` |
| `software/src/squid/core/protocol/step.py` | **New** - `FluidicsStep`, `ImagingStep`, `InterventionStep` (discriminated union) |
| `software/src/squid/core/protocol/imaging_config.py` | **New** - `ImagingConfig`, `ChannelConfigOverride`, `FocusConfig` |
| `software/src/squid/core/protocol/error_handling.py` | **New** - `ErrorHandlingConfig`, `FailureAction` |
| `software/src/squid/core/protocol/loader.py` | Expand `repeat`, resolve file references, load resources, validate `{i}` usage |
| `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py` | Execute step sequences, apply error handling |
| `software/src/squid/backend/controllers/orchestrator/imaging_executor.py` | Execute imaging with `ImagingConfig`, apply channel overrides |
| `software/src/squid/backend/controllers/orchestrator/state.py` | Add `step_index` to checkpoint schema |
| `software/src/squid/backend/managers/channel_configuration_manager.py` | Add `apply_channel_overrides()` method |

---

## Implementation Phases

### Phase 1: Schema Foundation
- Add `Step`, `StepType` to `schema.py`
- Create `imaging_config.py` with `ImagingConfig`, `FocusLockConfig`, etc.
- Create `error_handling.py` with `ErrorHandlingConfig`, `FailureAction`
- Update `Round` to support `steps` list and `repeat`
- Update `ExperimentProtocol` with resource sections, output_directory, error_handling
- Unit tests for new models

### Phase 2: Loader Updates
- Parse resource definitions (fluidics_protocols, imaging_configs, fov_sets)
- Resolve `file:` references to load external configs
- Expand `repeat: N` into N rounds with `{i}` substitution
- Unit tests for loader

### Phase 3: Executor Integration
- Update `_execute_round()` to iterate over steps
- Update checkpoint schema with `step_index`
- Pass FOV set to imaging executor
- Apply focus lock config before imaging
- Apply error handling config for failure cases
- Integration tests

### Phase 4: Validation & Testing
- Validate all resource references exist
- Validate `{i}` only appears in repeated rounds
- Clear error messages for missing resources
- Update test protocols to new format
- E2E tests with simulation

---

## Verification

```bash
cd software

# Unit tests
pytest tests/unit/squid/core/protocol/ -v

# Integration tests
pytest tests/integration/orchestrator/ -v

# E2E with simulation
python main_hcs.py --simulation
# Load a v2.0 protocol and run it
```
