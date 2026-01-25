# Orchestrator Protocol V2 - Implementation Audit

Audit performed: 2026-01-18

## Phase 1: Schema Foundation

### 1.1 `software/src/squid/core/protocol/step.py` (NEW)

- [x] File exists
- [x] `FluidicsStep` class with `step_type: Literal["fluidics"]` and `protocol: str`
- [x] `ImagingStep` class with `step_type: Literal["imaging"]`, `config: str`, `fovs: str = "default"`
- [x] `InterventionStep` class with `step_type: Literal["intervention"]` and `message: str`
- [x] `Step` discriminated union using `Field(discriminator="step_type")`

### 1.2 `software/src/squid/core/protocol/imaging_config.py` (NEW)

- [x] File exists
- [x] `ChannelConfigOverride` with name, exposure_time_ms, analog_gain, illumination_intensity, z_offset_um
- [x] `ZStackConfig` with planes, step_um, direction (from_center/from_bottom/from_top)
- [x] `ZStackConfig.validate_planes()` ensures planes >= 1
- [x] `ZStackConfig.validate_step_um()` ensures step_um > 0
- [x] `FocusConfig` with enabled, method (laser/contrast/none), channel, interval_fovs
- [x] `FocusConfig.validate_interval_fovs()` ensures interval_fovs >= 1
- [x] `ImagingConfig` with description, channels, z_stack, focus, skip_saving
- [x] `ImagingConfig.validate_channels()` ensures non-empty channels list
- [x] `ImagingConfig.get_channel_names()` method
- [x] `ImagingConfig.get_channel_overrides()` method

### 1.3 `software/src/squid/core/protocol/error_handling.py` (NEW)

- [x] File exists
- [x] `FailureAction` enum with SKIP, ABORT, PAUSE, WARN values
- [x] `ErrorHandlingConfig` with focus_failure, fluidics_failure, imaging_failure
- [x] Default values: focus_failure=SKIP, fluidics_failure=ABORT, imaging_failure=WARN

### 1.4 `software/src/squid/core/protocol/schema.py` (MODIFIED)

- [x] V1 `RoundType` enum removed
- [x] V1 `ImagingStep` (old inline format) removed
- [x] V1 `ImagingDefaults`, `FluidicsDefaults`, `ProtocolDefaults` removed
- [x] V2 `Round` class with name, steps: List[Step], repeat, metadata
- [x] `Round.validate_repeat()` ensures repeat >= 1 if provided
- [x] V2 `ExperimentProtocol` with named resources (fluidics_protocols, imaging_configs, fov_sets)
- [x] `ExperimentProtocol.validate_rounds()` ensures at least one round
- [x] `ExperimentProtocol.validate_references()` validates step references exist
- [x] `ExperimentProtocol.get_imaging_steps()` returns all imaging steps
- [x] `ExperimentProtocol.total_imaging_steps()` counts imaging steps

---

## Phase 2: Loader Updates

### 2.1 `software/src/squid/core/protocol/loader.py` (MODIFIED)

- [x] V1 `_parse_round()` method removed (Pydantic handles parsing)
- [x] V1 `_parse_imaging_step()` method removed
- [x] `_resolve_resources()` resolves file: references for imaging_configs
- [x] `_resolve_resources()` resolves file: references for fluidics_protocols
- [x] `_resolve_resources()` makes FOV paths absolute
- [x] `_expand_repeats()` expands rounds with repeat: N
- [x] `_substitute()` replaces {i} recursively in strings, dicts, lists
- [x] `save()` uses `mode="json"` to preserve step_type discriminator
- [x] `validate_channels()` method still exists for channel validation

---

## Phase 3: Orchestrator Integration

### 3.1 `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py` (MODIFIED)

- [x] Imports V2 step types (FluidicsStep, ImagingStep, InterventionStep)
- [x] `_initialize_fluidics_protocols()` loads protocol's fluidics_protocols into FluidicsController
- [x] `_load_fov_set()` parses CSV with flexible column name matching
- [x] `_load_fov_set()` publishes LoadScanCoordinatesCommand
- [x] `_execute_round()` iterates over round.steps
- [x] `_execute_round()` handles resume_step_index for checkpoint recovery
- [x] `_execute_round()` dispatches to correct handler based on step type (isinstance checks)
- [x] `_execute_fluidics_step()` runs named fluidics protocol
- [x] `_execute_imaging_step()` uses ImagingConfig from protocol
- [x] `_execute_imaging_step()` loads FOV set if specified
- [x] `_wait_for_intervention()` handles InterventionStep
- [x] `_handle_step_failure()` applies error_handling config per failure type
- [x] `_save_checkpoint()` includes step_index

### 3.2 `software/src/squid/backend/controllers/orchestrator/imaging_executor.py` (MODIFIED)

- [x] `execute_with_config()` method added
- [x] Configures z-stack from ImagingConfig.z_stack
- [x] Configures focus method from ImagingConfig.focus
- [x] Configures focus interval via autofocus_executor.configure(fovs_per_af=...)
- [x] Applies channel overrides via `_apply_channel_overrides()`
- [x] Sets skip_saving from ImagingConfig

### 3.3 `software/src/squid/backend/controllers/orchestrator/state.py` (MODIFIED)

- [x] `Checkpoint` dataclass has `step_index: int = 0` field
- [x] `ExperimentProgress` has `current_step_index: int = 0` field

### 3.4 `software/src/squid/backend/controllers/orchestrator/checkpoint.py` (MODIFIED)

- [x] `save()` includes step_index in JSON
- [x] `save()` creates directory if it doesn't exist (bug fix)
- [x] `load()` reads step_index with default of 0 for backwards compatibility
- [x] `create_checkpoint()` accepts step_index parameter

### 3.5 `software/src/squid/backend/controllers/orchestrator/warnings.py` (MODIFIED)

- [x] `WarningCategory.EXECUTION` added for step execution failures

### 3.6 `software/src/squid/backend/controllers/orchestrator/protocol_validator.py` (MODIFIED)

- [x] Validates V2 step-based rounds
- [x] `_validate_round()` iterates over steps
- [x] `_validate_fluidics_step()` checks protocol exists
- [x] `_validate_imaging_step()` checks config and fovs exist
- [x] Handles InterventionStep

---

## Phase 4: Cleanup

### 4.1 V1 Code Removal

- [x] No `RoundType` in src/ (verified via grep)
- [x] No `ImagingDefaults` in src/ (verified via grep)
- [x] No `FluidicsDefaults` in src/ (verified via grep)
- [x] No `ProtocolDefaults` in src/ (verified via grep)
- [x] No `_parse_round` in src/ (verified via grep)
- [x] No `_parse_imaging_step` in src/ (verified via grep)
- [x] No `create_from_template` in src/ (verified via grep)

### 4.2 `software/src/squid/core/protocol/__init__.py` (MODIFIED)

- [x] Exports Step, FluidicsStep, ImagingStep, InterventionStep
- [x] Exports ImagingConfig, ChannelConfigOverride, ZStackConfig, FocusConfig
- [x] Exports ErrorHandlingConfig, FailureAction
- [x] Exports ExperimentProtocol, Round
- [x] Exports FluidicsProtocol, FluidicsProtocolStep, FluidicsCommand
- [x] Exports ProtocolLoader, ProtocolValidationError

---

## Bugs Found and Fixed During Audit

| # | Location | Issue | Fix |
|---|----------|-------|-----|
| 1 | `test_orchestrator_controller.py:24` | V1 imports (RoundType, old ImagingStep) | Updated to V2 imports |
| 2 | `test_orchestrator_controller.py:87-96` | V1 protocol fixture format | Updated to V2 format with imaging_configs and steps |
| 3 | `test_orchestrator_controller.py:57` | Mock context missing experiment_id | Added `context.experiment_id = "test_experiment_001"` |
| 4 | `test_orchestrator_controller.py:78` | Mock missing execute_with_config | Added `mock.execute_with_config.return_value = True` |
| 5 | `test_orchestrator_controller.py:311` | Checking wrong method | Changed `execute.called` to `execute_with_config.called` |
| 6 | `test_checkpoint.py` (8 occurrences) | V1 attribute name | Changed `fluidics_step_index` to `step_index` |
| 7 | `checkpoint.py:59` | Directory not created before save | Added `os.makedirs(experiment_path, exist_ok=True)` |

---

## Test Results

```
tests/unit/orchestrator/test_protocol.py             21 passed
tests/unit/orchestrator/test_protocol_validator.py   25 passed
tests/unit/orchestrator/test_orchestrator_controller.py  13 passed
tests/unit/orchestrator/test_checkpoint.py            8 passed
tests/unit/orchestrator/test_warning_manager.py      40 passed
─────────────────────────────────────────────────────────────
TOTAL                                               107 passed
```

---

## Observations / Potential Issues

1. **Private attribute access**: `imaging_executor.py:169` accesses `self._multipoint._autofocus_executor` which is a private attribute. This coupling could be brittle if the internal implementation changes.

2. **Channel override restoration**: The plan states "at experiment end, original channel settings should be restored (via AcquisitionContext)" but this restoration logic is not explicitly visible in the audited code.

3. **FOV CSV column matching**: The `_load_fov_set()` method uses substring matching (`"region" in col_lower`, `"x" in col_lower and "mm" in col_lower`) which could match unexpected columns in edge cases.
