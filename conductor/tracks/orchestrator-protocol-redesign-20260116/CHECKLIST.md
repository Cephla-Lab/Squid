# Orchestrator Protocol Schema Redesign - Implementation Checklist

## Overview

Redesign the protocol schema to support flexible step sequences, named resources (imaging configs, FOV sets, fluidics protocols), and simple looping via `repeat: N`.

**Target:** Flexible round definitions with arbitrary step sequences, named resources, reduced verbosity.

---

## Implementation Status Summary

**Overall Completion: ~90%**

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1 | COMPLETE | Schema foundation (3 new files, 1 modified) |
| Phase 2 | COMPLETE | Loader updates |
| Phase 3 | COMPLETE | Orchestrator integration |
| Phase 4 | COMPLETE | ImagingExecutor integration |
| Phase 5 | PARTIAL | Validation done, tests need specific file locations |

---

## Subsystem Connection Points Reference

### FluidicsController API
```python
# Load protocols programmatically (used at experiment start)
fluidics_controller.add_protocol(name: str, protocol: FluidicsProtocol) -> None

# Execute a protocol by name
fluidics_controller.run_protocol(protocol_name: str) -> bool

# Poll for completion
fluidics_controller.state  # FluidicsControllerState enum (IDLE, RUNNING, COMPLETED, FAILED, STOPPED)
fluidics_controller.last_terminal_state  # COMPLETED, FAILED, or STOPPED

# Control
fluidics_controller.stop() -> bool
```

### MultiPointController API (via ImagingExecutor)
```python
# Configuration via dot notation
multipoint.update_config(**{
    "zstack.nz": int,
    "zstack.delta_z_um": float,
    "zstack.stacking_direction": "FROM CENTER" | "FROM BOTTOM" | "FROM TOP",
    "focus.do_contrast_af": bool,
    "focus.do_reflection_af": bool,
    "skip_saving": bool,
})

# Channel selection
multipoint.set_selected_configurations(channel_names: List[str])

# Path setup
multipoint.base_path = str
multipoint.experiment_ID = str

# Execute
multipoint.run_acquisition(acquire_current_fov=False)
```

### ScanCoordinates API (FOV Loading)
```python
# Load FOVs via EventBus command (clears and replaces current)
event_bus.publish(LoadScanCoordinatesCommand(
    region_fov_coordinates: Dict[str, Tuple[Tuple[float, ...], ...]],
    region_centers: Dict[str, Tuple[float, ...]],
))
```

### ChannelConfigurationManager API
```python
# Lookup channel by name
channel_config_manager.get_channel_configuration_by_name(
    objective: str, name: str
) -> Optional[ChannelMode]

# Update channel settings
channel_config_manager.update_configuration(
    objective: str, config_id: str, attr_name: str, value: Any
) -> None
# attr_name: "ExposureTime", "AnalogGain", "IlluminationIntensity", "ZOffset"
```

---

## Phase 1: Schema Foundation

### 1.1 Create `squid/core/protocol/step.py` (NEW FILE)

**Purpose:** Define Step discriminated union using Pydantic's discriminator pattern

#### 1.1.1 FluidicsStep Model
- [x] Create `FluidicsStep(BaseModel)` class
  - [x] `step_type: Literal["fluidics"] = "fluidics"` - Discriminator field
  - [x] `protocol: str` - Name of protocol from `fluidics_protocols`
- [x] Add docstring explaining this references protocol by name

#### 1.1.2 ImagingStep Model
- [x] Create `ImagingStep(BaseModel)` class
  - [x] `step_type: Literal["imaging"] = "imaging"` - Discriminator field
  - [x] `config: str` - Name of imaging config from `imaging_configs`
  - [x] `fovs: str = "default"` - Name of FOV set from `fov_sets`, or "default" for current
- [x] Add docstring explaining resource references

#### 1.1.3 InterventionStep Model
- [x] Create `InterventionStep(BaseModel)` class
  - [x] `step_type: Literal["intervention"] = "intervention"` - Discriminator field
  - [x] `message: str` - Message to display to operator
- [x] Add docstring

#### 1.1.4 Step Type Alias
- [x] Create discriminated union type alias:
  ```python
  Step = Annotated[
      Union[FluidicsStep, ImagingStep, InterventionStep],
      Field(discriminator="step_type")
  ]
  ```
- [x] Add imports: `from typing import Annotated, Literal, Union`
- [x] Export all classes in `__all__` (via __init__.py)

---

### 1.2 Create `squid/core/protocol/imaging_config.py` (NEW FILE)

**Purpose:** Define ImagingConfig and related models for named imaging configurations

#### 1.2.1 ChannelConfigOverride Model
- [x] Create `ChannelConfigOverride(BaseModel)` class
  - [x] `name: str` - Must match channel in channel_definitions
  - [x] `exposure_time_ms: Optional[float] = None` - Override default exposure
  - [x] `analog_gain: Optional[float] = None` - Override default gain
  - [x] `illumination_intensity: Optional[float] = None` - 0-100%, override default
  - [x] `z_offset_um: float = 0.0` - Per-channel z offset
- [x] Add field validator for `exposure_time_ms > 0` if provided
- [x] Add field validator for `illumination_intensity` in range 0-100 if provided
- [x] Add docstring explaining override semantics

#### 1.2.2 ZStackConfig Model
- [x] Create `ZStackConfig(BaseModel)` class
  - [x] `planes: int = 1`
  - [x] `step_um: float = 0.5`
  - [x] `direction: Literal["from_center", "from_bottom", "from_top"] = "from_center"`
- [x] Add `@field_validator("planes")` ensuring `planes >= 1`
- [x] Add `@field_validator("step_um")` ensuring `step_um > 0`
- [x] Add docstring with direction explanations

#### 1.2.3 FocusConfig Model
- [x] Create `FocusConfig(BaseModel)` class
  - [x] `enabled: bool = False`
  - [x] `method: Literal["laser", "contrast", "none"] = "laser"`
  - [x] `channel: Optional[str] = None` - For contrast AF: which channel to use
  - [x] `interval_fovs: int = 1` - Run every N FOVs (1 = every FOV)
- [x] Add `@field_validator("interval_fovs")` ensuring `interval_fovs >= 1`
- [x] Add docstring explaining:
  - `"laser"` → maps to `do_reflection_af=True` in MultiPointController
  - `"contrast"` → maps to `do_contrast_af=True` in MultiPointController
  - `"none"` → both False

#### 1.2.4 ImagingConfig Model
- [x] Create `ImagingConfig(BaseModel)` class
  - [x] `description: str = ""`
  - [x] `channels: List[Union[str, ChannelConfigOverride]]` - Channel names or override objects
  - [x] `z_stack: ZStackConfig = Field(default_factory=ZStackConfig)`
  - [x] `focus: FocusConfig = Field(default_factory=FocusConfig)`
  - [x] `skip_saving: bool = False`
- [x] Add `@field_validator("channels")` ensuring non-empty list
- [x] Add docstring explaining channel list semantics (string = use defaults)
- [x] Export all classes in `__all__` (via __init__.py)

---

### 1.3 Create `squid/core/protocol/error_handling.py` (NEW FILE)

**Purpose:** Define error handling configuration for protocol-level failure behaviors

#### 1.3.1 FailureAction Enum
- [x] Create `FailureAction(str, Enum)` class
  - [x] `SKIP = "skip"` - Skip failed item, continue to next step
  - [x] `ABORT = "abort"` - Stop experiment immediately
  - [x] `PAUSE = "pause"` - Pause for operator intervention
  - [x] `WARN = "warn"` - Log warning, continue execution
- [x] Add docstring explaining each action

#### 1.3.2 ErrorHandlingConfig Model
- [x] Create `ErrorHandlingConfig(BaseModel)` class
  - [x] `focus_failure: FailureAction = FailureAction.SKIP`
  - [x] `fluidics_failure: FailureAction = FailureAction.ABORT`
  - [x] `imaging_failure: FailureAction = FailureAction.WARN`
- [x] Add docstring explaining default rationale
- [x] Export all classes in `__all__` (via __init__.py)

---

### 1.4 Replace `squid/core/protocol/schema.py`

**Purpose:** Replace existing Round and ExperimentProtocol with step-based models

#### 1.4.1 Imports
- [x] Add import for `Step` from `squid.core.protocol.step`
- [x] Add import for `ImagingConfig` from `squid.core.protocol.imaging_config`
- [x] Add import for `ErrorHandlingConfig` from `squid.core.protocol.error_handling`
- [x] Add import for `FluidicsProtocol` from `squid.core.protocol.fluidics_protocol`

#### 1.4.2 Round Model (REPLACE)
- [x] Replace existing `Round` class with:
  - [x] `name: str`
  - [x] `steps: List[Step] = Field(default_factory=list)` - Ordered list of steps
  - [x] `repeat: Optional[int] = None` - If set, expand with `{i}` substitution
  - [x] `metadata: Dict[str, Any] = Field(default_factory=dict)`
- [x] Remove legacy fields: `type`, `fluidics_protocol`, `imaging`, `requires_intervention`, `intervention_message`
- [x] Add docstring explaining step-based execution

#### 1.4.3 ExperimentProtocol Model (REPLACE)
- [x] Replace existing `ExperimentProtocol` class with:
  - [x] `name: str`
  - [x] `version: str = "2.0"`
  - [x] `description: str = ""`
  - [x] `output_directory: Optional[str] = None`
  - [x] `error_handling: ErrorHandlingConfig = Field(default_factory=ErrorHandlingConfig)`
  - [x] `fluidics_protocols: Dict[str, FluidicsProtocol] = Field(default_factory=dict)`
  - [x] `imaging_configs: Dict[str, ImagingConfig] = Field(default_factory=dict)`
  - [x] `fov_sets: Dict[str, str] = Field(default_factory=dict)` - name → CSV path
  - [x] `rounds: List[Round] = Field(default_factory=list)`
- [x] Remove legacy fields: `defaults`, `fov_positions_file`, `fluidics_protocols_file`
- [x] Remove legacy helper methods: `apply_defaults_to_round()`, `get_imaging_rounds()`, etc.
- [x] Add docstring explaining resource-based design

#### 1.4.4 Remove Legacy Classes
- [x] Remove `RoundType` enum (no longer needed)
- [x] Remove `ImagingStep` (replaced by `ImagingConfig`) - Note: ImagingStep now refers to the step type
- [x] Remove `FluidicsStep` (legacy inline format) - Note: FluidicsStep now refers to the step type
- [x] Remove `ImagingDefaults`, `FluidicsDefaults`, `ProtocolDefaults`

---

### 1.5 Update `squid/core/protocol/__init__.py`

- [x] Export `FluidicsStep`, `ImagingStep`, `InterventionStep`, `Step` from `step`
- [x] Export `ChannelConfigOverride`, `ZStackConfig`, `FocusConfig`, `ImagingConfig` from `imaging_config`
- [x] Export `FailureAction`, `ErrorHandlingConfig` from `error_handling`
- [x] Update existing exports for modified `schema.py`

---

### 1.6 Unit Tests - Schema Models

**File:** `software/tests/unit/orchestrator/test_protocol.py` (actual location)

#### 1.6.1 Step Tests
- [x] Test `FluidicsStep` creation with protocol name
- [x] Test `FluidicsStep` serialization to dict (verify `step_type` present)
- [x] Test `ImagingStep` creation with config and fovs
- [x] Test `ImagingStep` with default fovs value
- [x] Test `InterventionStep` creation with message
- [x] Test `Step` discriminated union parsing from dict (fluidics)
- [x] Test `Step` discriminated union parsing from dict (imaging)
- [x] Test `Step` discriminated union parsing from dict (intervention)
- [ ] Test invalid step_type raises validation error

#### 1.6.2 ImagingConfig Tests
- [x] Test `ChannelConfigOverride` with name only
- [x] Test `ChannelConfigOverride` with all overrides
- [ ] Test `ChannelConfigOverride` validation: exposure > 0
- [ ] Test `ChannelConfigOverride` validation: intensity 0-100
- [x] Test `ZStackConfig` defaults (planes=1, step_um=0.5, from_center)
- [x] Test `ZStackConfig` validation: planes >= 1
- [x] Test `ZStackConfig` validation: step_um > 0
- [x] Test `ZStackConfig` direction options
- [x] Test `FocusConfig` defaults
- [x] Test `FocusConfig` with contrast method and channel
- [x] Test `FocusConfig` validation: interval_fovs >= 1
- [x] Test `ImagingConfig` with string channel names only
- [x] Test `ImagingConfig` with ChannelConfigOverride objects
- [x] Test `ImagingConfig` with mixed channels
- [x] Test `ImagingConfig` validation: channels non-empty

#### 1.6.3 ErrorHandling Tests
- [x] Test `FailureAction` enum values
- [x] Test `ErrorHandlingConfig` defaults
- [x] Test `ErrorHandlingConfig` custom values

#### 1.6.4 Round & Protocol Tests
- [x] Test `Round` with steps list
- [x] Test `Round` with repeat field
- [x] Test `Round` with metadata
- [x] Test `ExperimentProtocol` with all resource sections
- [x] Test `ExperimentProtocol` with error_handling
- [x] Test `ExperimentProtocol` serialization round-trip

---

## Phase 2: Loader Updates

### 2.1 Rewrite `squid/core/protocol/loader.py`

**Purpose:** Load v2.0 protocols with resource resolution and repeat expansion

#### 2.1.1 Resource Resolution
- [x] Add `_resolve_resources(data: Dict, protocol_dir: Path) -> Dict` method
  - [x] For each entry in `imaging_configs`:
    - [x] If dict has `file:` key, load YAML from `protocol_dir / file_path`
    - [x] Replace entry with loaded content
  - [x] For each entry in `fluidics_protocols`:
    - [x] If dict has `file:` key, load YAML from `protocol_dir / file_path`
    - [x] Replace entry with loaded content
  - [x] For each entry in `fov_sets`:
    - [x] If path is relative, make absolute: `str(protocol_dir / csv_path)`
  - [x] Return modified data dict

#### 2.1.2 Repeat Expansion
- [x] Add `_expand_repeats(data: Dict) -> Dict` method
  - [x] Create empty `expanded_rounds` list
  - [x] For each round in `data["rounds"]`:
    - [x] Pop `repeat` value (None if not present)
    - [x] If `repeat` is None: append round as-is
    - [x] If `repeat` is int:
      - [x] For `i` in `range(1, repeat + 1)`:
        - [x] Deep copy round dict
        - [x] Call `_substitute(round_copy, i)`
        - [x] Append to expanded_rounds
  - [x] Replace `data["rounds"]` with `expanded_rounds`
  - [x] Return data

- [x] Add `_substitute(obj: Any, i: int) -> Any` method (recursive)
  - [x] If `obj` is `str`: return `obj.replace("{i}", str(i))`
  - [x] If `obj` is `dict`: return `{k: _substitute(v, i) for k, v in obj.items()}`
  - [x] If `obj` is `list`: return `[_substitute(item, i) for item in obj]`
  - [x] Otherwise: return `obj` unchanged

#### 2.1.3 Main Load Method
- [x] Rewrite `load(path: Union[str, Path]) -> ExperimentProtocol`:
  ```python
  path = Path(path)
  data = yaml.safe_load(open(path))
  data = self._resolve_resources(data, path.parent)
  data = self._expand_repeats(data)
  return ExperimentProtocol.model_validate(data)
  ```

- [x] Rewrite `load_from_string(content: str, base_path: Optional[Path] = None)`:
  - [x] Parse YAML
  - [x] If `base_path` provided, resolve resources
  - [x] Expand repeats
  - [x] Validate and return

#### 2.1.4 Remove Legacy Methods
- [x] Remove `_parse_round()` (Pydantic handles this now)
- [x] Remove `_parse_imaging_step()`
- [x] `validate_channels()` kept - still useful for validation
- [x] Remove `create_from_template()` (no longer applicable)

---

### 2.2 Unit Tests - Loader

**File:** `software/tests/unit/orchestrator/test_protocol.py` (actual location)

#### 2.2.1 Resource Resolution Tests
- [x] Test loading protocol with inline `fluidics_protocols`
- [ ] Test loading protocol with `file:` reference in `fluidics_protocols`
- [x] Test loading protocol with inline `imaging_configs`
- [ ] Test loading protocol with `file:` reference in `imaging_configs`
- [ ] Test loading protocol with relative paths in `fov_sets`
- [ ] Test loading protocol with absolute paths in `fov_sets`
- [ ] Test file not found error for bad `file:` reference

#### 2.2.2 Repeat Expansion Tests
- [x] Test round without repeat (passed through unchanged)
- [x] Test round with `repeat: 3` expands to 3 rounds
- [x] Test `{i}` substitution in round name: `"Round {i}"` → `"Round 1"`, `"Round 2"`, etc.
- [x] Test `{i}` substitution in fluidics step protocol: `"hybridize_{i}"` → `"hybridize_1"`, etc.
- [ ] Test `{i}` substitution in nested structures
- [ ] Test repeat expansion preserves other round fields
- [ ] Test multiple repeated rounds in same protocol

#### 2.2.3 Full Load Tests
- [x] Test loading minimal v2.0 protocol
- [x] Test loading protocol with all features (resources, repeat, error_handling)
- [x] Test round-trip: load → dump → load produces same result
- [x] Test validation error for invalid protocol structure

---

## Phase 3: Orchestrator Integration

### 3.1 Update `squid/backend/controllers/orchestrator/state.py`

**Purpose:** Add step tracking to checkpoint and progress

#### 3.1.1 Checkpoint Updates
- [x] Add `step_index: int = 0` field to `Checkpoint` dataclass
- [x] Update `Checkpoint` docstring to explain step_index

#### 3.1.2 RoundProgress Updates
- [x] Add `current_step_index: int = 0` field to `RoundProgress` dataclass
- [x] Add `total_steps: int = 0` field to `RoundProgress` dataclass

#### 3.1.3 ExperimentProgress Updates
- [x] Add `current_step_index: int = 0` field to `ExperimentProgress`

---

### 3.2 Update `squid/backend/controllers/orchestrator/orchestrator_controller.py`

**Purpose:** Implement step-based round execution

#### 3.2.1 Imports
- [x] Add import for `FluidicsStep`, `ImagingStep`, `InterventionStep` from `squid.core.protocol.step`
- [x] Add import for `ImagingConfig` from `squid.core.protocol.imaging_config`
- [x] Add import for `FailureAction` from `squid.core.protocol.error_handling`
- [x] Add import for `LoadScanCoordinatesCommand` from `squid.core.events`
- [x] Add import for `pandas` (for CSV loading)

#### 3.2.2 Fluidics Protocol Loading
- [x] Add `_initialize_fluidics_protocols()` method
- [x] Call `_initialize_fluidics_protocols()` in `_run_experiment()` after protocol load

#### 3.2.3 FOV Set Loading
- [x] Add `_load_fov_set(csv_path: str) -> None` method with flexible column matching
- [x] Publish LoadScanCoordinatesCommand after parsing CSV

#### 3.2.4 Step-Based Round Execution
- [x] Replace `_execute_round()` method with step-based version
- [x] Iterate over round.steps
- [x] Handle resume_step_index for checkpoint recovery
- [x] Dispatch to correct handler based on isinstance checks

#### 3.2.5 Fluidics Step Execution
- [x] Add `_execute_fluidics_step(round_idx: int, step: FluidicsStep) -> None` method
- [x] Verify `step.protocol` exists in `self._protocol.fluidics_protocols`
- [x] If `_fluidics_controller` is None, log simulation and return
- [x] Call `self._cancel_token.check_point()`
- [x] Call `self._fluidics_controller.run_protocol(step.protocol)`
- [x] If returns False, raise RuntimeError
- [x] Poll loop until terminal state with cancellation checks
- [x] Check `last_terminal_state` for FAILED/STOPPED

#### 3.2.6 Imaging Step Execution
- [x] Add `_execute_imaging_step(round_idx: int, step: ImagingStep, *, resume_fov: int = 0) -> None` method
- [x] Resolve imaging config: `config = self._protocol.imaging_configs.get(step.config)`
- [x] If None, raise RuntimeError
- [x] If `step.fovs != "default"`, load FOV set
- [x] Get round output path
- [x] Call `self._imaging_executor.execute_with_config(...)`

#### 3.2.7 Error Handling
- [x] Add `_handle_step_failure(step: Step, error: Exception) -> None` method
- [x] Check step type and apply appropriate error_handling action
- [x] Handle ABORT, PAUSE, SKIP, WARN actions correctly

#### 3.2.8 Checkpoint Updates
- [x] Update `_save_checkpoint()` to include `step_index`

#### 3.2.9 Resume Updates
- [x] Update `_run_experiment()` to pass `resume_step_index` from checkpoint

#### 3.2.10 Remove Legacy Code
- [x] Remove `_execute_fluidics()` (replaced by `_execute_fluidics_step()`)
- [x] Remove `_execute_imaging()` (replaced by `_execute_imaging_step()`)
- [x] Remove legacy round execution logic

---

### 3.3 Update `squid/backend/controllers/orchestrator/checkpoint.py`

- [x] Update `create_checkpoint()` to accept `step_index` parameter
- [x] Update checkpoint serialization to include `step_index`
- [x] Update checkpoint deserialization to read `step_index`

---

## Phase 4: ImagingExecutor Integration

### 4.1 Update `squid/backend/controllers/orchestrator/imaging_executor.py`

**Purpose:** Add `execute_with_config()` method for ImagingConfig-based execution

#### 4.1.1 Imports
- [x] Add import for `ImagingConfig`, `ChannelConfigOverride` from `squid.core.protocol.imaging_config`

#### 4.1.2 Add `execute_with_config()` Method
- [x] Add new method signature with all parameters
- [x] Build channel name list from ImagingConfig
- [x] Configure paths (base_path, experiment_ID)
- [x] Configure z-stack (planes, step_um, direction mapping)
- [x] Configure focus (contrast/laser based on method)
- [x] Configure other settings (skip_saving)
- [x] Set channels via set_selected_configurations
- [x] Apply channel overrides
- [x] Run acquisition and wait with cancellation support

#### 4.1.3 Add `_apply_channel_overrides()` Method
- [x] Add helper method to apply ChannelConfigOverride settings
- [x] Handle exposure_time_ms, analog_gain, illumination_intensity

---

## Phase 5: Validation & Testing

### 5.1 Update `squid/backend/controllers/orchestrator/protocol_validator.py`

**Purpose:** Validate v2.0 protocol resource references

#### 5.1.1 Resource Reference Validation
- [x] Add `_validate_resource_references(protocol: ExperimentProtocol) -> List[str]` method
- [x] Validate imaging config references
- [x] Validate FOV set references
- [x] Validate fluidics protocol references

#### 5.1.2 Channel Validation
- [x] Add channel existence validation (if `available_channels` provided)

#### 5.1.3 Update Main Validate Method
- [x] Update `validate()` to call new validation methods
- [x] Return errors in `ValidationSummary`

---

### 5.2 Integration Tests

**File:** `software/tests/integration/orchestrator/test_orchestrator_workflows.py` (actual location)

#### 5.2.1 Basic Step Tests
- [ ] Test round with single fluidics step executes correctly
- [ ] Test round with single imaging step executes correctly
- [ ] Test round with single intervention step pauses and resumes
- [ ] Test round with multiple steps in sequence (fluidics → imaging → fluidics)
- [ ] Test round with interleaved steps (imaging → fluidics → imaging)

#### 5.2.2 Resource Tests
- [ ] Test imaging step loads correct ImagingConfig
- [ ] Test imaging step with channel overrides applies them
- [ ] Test imaging step with non-default FOV set loads CSV
- [ ] Test imaging step with `fovs: "default"` uses current positions
- [ ] Test fluidics step loads protocol from `fluidics_protocols`

#### 5.2.3 Repeat Tests
- [ ] Test repeated round expands correctly
- [ ] Test `{i}` substitution in protocol names works
- [ ] Test execution order matches expanded rounds

#### 5.2.4 Error Handling Tests
- [ ] Test `focus_failure: skip` continues on focus error
- [ ] Test `fluidics_failure: abort` stops on fluidics error
- [ ] Test `imaging_failure: warn` logs warning and continues
- [ ] Test `*_failure: pause` pauses for intervention

#### 5.2.5 Checkpoint Tests
- [ ] Test checkpoint saves current step_index
- [ ] Test resume from checkpoint continues at correct step
- [ ] Test resume mid-round works correctly

---

### 5.3 Example Protocols

**Directory:** `software/tests/e2e/configs/protocols/`

#### 5.3.1 Create `v2_minimal.yaml`
- [ ] Simple protocol with one round, one imaging step
- [ ] Inline imaging_config with single channel
- [ ] No repeat, no error_handling customization

#### 5.3.2 Create `v2_fluidics_imaging.yaml`
- [ ] Protocol with fluidics → imaging sequence
- [ ] Inline fluidics_protocol and imaging_config
- [ ] Tests basic step ordering

#### 5.3.3 Create `v2_repeated.yaml`
- [ ] Protocol using `repeat: 5` with `{i}` substitution
- [ ] Tests round expansion

#### 5.3.4 Create `v2_full.yaml`
- [ ] Full-featured protocol demonstrating all capabilities:
  - [ ] Multiple `fluidics_protocols`
  - [ ] Multiple `imaging_configs` with channel overrides
  - [ ] Multiple `fov_sets`
  - [ ] Repeated rounds with `{i}` substitution
  - [ ] Custom `error_handling`
  - [ ] Mixed step sequences

---

### 5.4 E2E Testing

#### 5.4.1 Simulation Tests
- [ ] Run `v2_minimal.yaml` in simulation mode - verify completion
- [ ] Run `v2_fluidics_imaging.yaml` - verify step order in logs
- [ ] Run `v2_repeated.yaml` - verify round expansion
- [ ] Run `v2_full.yaml` - verify all features work together

#### 5.4.2 Checkpoint Tests
- [ ] Start `v2_full.yaml`, pause mid-experiment
- [ ] Verify checkpoint file contains correct step_index
- [ ] Resume from checkpoint, verify correct step continues

---

## Testing Commands

### Unit Tests
```bash
cd software
pytest tests/unit/orchestrator/test_protocol.py -v
pytest tests/unit/orchestrator/test_protocol_validator.py -v
pytest tests/unit/orchestrator/test_checkpoint.py -v
pytest tests/unit/orchestrator/test_orchestrator_controller.py -v
```

### Integration Tests
```bash
cd software
pytest tests/integration/orchestrator/test_orchestrator_workflows.py -v
```

### E2E Testing
```bash
cd software
python main_hcs.py --simulation
# Load v2 protocol from tests/e2e/configs/protocols/
# Run acquisition and verify logs
```

---

## File Summary

### New Files (3)
| File | Purpose |
|------|---------|
| `squid/core/protocol/step.py` | Step discriminated union (FluidicsStep, ImagingStep, InterventionStep) |
| `squid/core/protocol/imaging_config.py` | ImagingConfig, ZStackConfig, FocusConfig, ChannelConfigOverride |
| `squid/core/protocol/error_handling.py` | ErrorHandlingConfig, FailureAction |

### Modified Files (6)
| File | Changes |
|------|---------|
| `squid/core/protocol/schema.py` | Replace Round and ExperimentProtocol with step-based models |
| `squid/core/protocol/loader.py` | Add repeat expansion, resource resolution |
| `squid/core/protocol/__init__.py` | Export new classes |
| `squid/backend/controllers/orchestrator/orchestrator_controller.py` | Step-based execution, FOV loading, error handling |
| `squid/backend/controllers/orchestrator/imaging_executor.py` | Add `execute_with_config()`, channel overrides |
| `squid/backend/controllers/orchestrator/state.py` | Add step_index to Checkpoint and ExperimentProgress |

### Test Files (3)
| File | Purpose |
|------|---------|
| `tests/unit/orchestrator/test_protocol.py` | Unit tests for schema and loader |
| `tests/unit/orchestrator/test_protocol_validator.py` | Unit tests for protocol validator |
| `tests/integration/orchestrator/test_orchestrator_workflows.py` | Integration tests for orchestrator |

---

## Remaining Items (Priority Order)

1. **Phase 5.3**: Create example V2 protocol YAML files
2. **Phase 5.2/5.4**: Add integration and E2E tests for step execution
3. **Phase 1.6/2.2**: Add remaining test cases (invalid step_type, file: references, etc.)
