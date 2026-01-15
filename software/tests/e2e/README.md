# End-to-End Testing

This directory contains comprehensive end-to-end tests for the Squid microscope control software.

## Overview

The e2e tests validate complete workflows including:

- **Tiled imaging** with configurable grid sizes and overlap
- **Z-stack acquisition** from bottom, center, or top
- **Multi-round orchestration** with fluidics and imaging
- **Checkpoint and recovery** for fault tolerance
- **Intervention handling** for operator-in-the-loop workflows

All tests run in **simulation mode** - no real hardware required.

## Directory Structure

```
tests/e2e/
├── configs/                     # Realistic configuration files
│   ├── protocols/               # Protocol YAML files
│   └── fluidics/                # Fluidics config JSON
├── harness/                     # E2E test harness
│   ├── orchestrator_simulator.py
│   └── e2e_assertions.py
├── tests/                       # Programmatic pytest tests
│   ├── test_imaging_workflows.py
│   ├── test_orchestrator_e2e.py
│   ├── test_checkpoint_recovery.py
│   ├── test_protocol_smoke.py
│   ├── test_orchestrator_validation.py
│   └── test_fault_injection.py
└── manual/                      # Interactive testing scripts
    ├── run_imaging.py
    └── run_orchestrator.py
```

## Running Tests

### Programmatic Tests (pytest)

From the `software/` directory:

```bash
# Run all e2e tests
pytest tests/e2e/ -v

# Run with specific markers
pytest tests/e2e/ -v -m orchestrator     # Orchestrator tests only
pytest tests/e2e/ -v -m imaging          # Imaging tests only
pytest tests/e2e/ -v -m checkpoint       # Checkpoint tests only
pytest tests/e2e/ -v -m fluidics         # Fluidics tests only

# Run excluding slow tests
pytest tests/e2e/ -v -m "e2e and not slow"

# Run with output capture disabled (see print statements)
pytest tests/e2e/ -v -s
```

### Manual/Interactive Tests

See [manual/README.md](manual/README.md) for interactive testing.

```bash
# Quick imaging test
python -m tests.e2e.manual.run_imaging --grid 2x2 --zstack 3

# Full orchestrator test
python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/multi_round_fish.yaml
```

## Protocol Files

| Protocol | Rounds | Description |
|----------|--------|-------------|
| `single_round_imaging.yaml` | 1 | Minimal imaging |
| `single_round_imaging_save.yaml` | 1 | Single-round imaging with saving enabled |
| `tiled_zstack.yaml` | 1 | Tiled imaging + z-stack |
| `multi_round_fish.yaml` | 4 | Full FISH with fluidics |
| `fluidics_only.yaml` | 4 | Fluidics-only |
| `intervention_protocol.yaml` | 5 | With operator interventions |

## Coverage Matrix

| Workflow Dimension | Coverage | Tests |
|--------------------|----------|-------|
| Imaging modes | grids, z-stacks, timelapse, piezo, autofocus, coordinate modes | `test_imaging_workflows.py` |
| Orchestration | single/multi-round, fluidics-only, interventions, multi-region | `test_orchestrator_e2e.py`, `test_protocol_smoke.py` |
| Output validation | round folders, coordinates.csv, image files | `test_protocol_smoke.py` |
| Checkpoint/recovery | pause/resume, resume-from-checkpoint | `test_checkpoint_recovery.py` |
| Validation + warnings | protocol validation events, warning pause/clear | `test_orchestrator_validation.py` |
| Fault handling | injected camera faults (acquisition + orchestrator) | `test_fault_injection.py` |

## Test Harness

### OrchestratorSimulator

High-level API for orchestrator testing:

```python
from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator

with BackendContext() as ctx:
    sim = OrchestratorSimulator(ctx)

    sim.load_protocol("path/to/protocol.yaml")
    sim.add_grid_region("region_1", center=(10, 10, 1), n_x=2, n_y=2)

    result = sim.run_and_wait(timeout_s=120)

    assert result.success
    assert result.completed_rounds == 4
```

### E2E Assertions

```python
from tests.e2e.harness import (
    assert_orchestrator_completed,
    assert_round_sequence,
    assert_checkpoint_created,
    assert_output_structure_valid,
)

# In test
assert_orchestrator_completed(monitor, expected_rounds=4)
assert_round_sequence(monitor, ["Round 1", "Round 2", "Final"])
assert_checkpoint_created("/path/to/experiment")
```

## Fixtures

Key fixtures in `conftest.py`:

| Fixture | Type | Description |
|---------|------|-------------|
| `e2e_backend_ctx` | `BackendContext` | Simulated backend |
| `e2e_acquisition_sim` | `AcquisitionSimulator` | For imaging tests |
| `e2e_orchestrator` | `OrchestratorSimulator` | For orchestrator tests |
| `single_round_imaging_protocol` | `str` | Protocol path |
| `tiled_zstack_protocol` | `str` | Protocol path |
| `multi_round_fish_protocol` | `str` | Protocol path |
| `intervention_protocol` | `str` | Protocol path |

## Test Markers

- `@pytest.mark.e2e` - All e2e tests
- `@pytest.mark.orchestrator` - Orchestrator tests
- `@pytest.mark.imaging` - Imaging tests
- `@pytest.mark.checkpoint` - Checkpoint tests
- `@pytest.mark.fluidics` - Fluidics tests

## Notes

- Protocol durations are shortened for fast testing (0.1-0.5s incubations)
- Images are not saved by default (`skip_saving: true` in protocols) except `single_round_imaging_save.yaml`
- All tests are self-contained and clean up after themselves
- Temporary directories are used for output unless specified
