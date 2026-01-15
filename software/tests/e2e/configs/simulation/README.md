# Quick Simulation Configs

Fast, manually-loadable configs for testing the full acquisition workflow.

## Fluidics Sequence CSVs

See `../fluidics/` for fluidics sequence CSV files. Load them directly into the fluidics UI or use with the orchestrator.

## Protocol YAML Files (for Orchestrator)

| File | Rounds | Est. Time | Description |
|------|--------|-----------|-------------|
| `quick_fish_2round.yaml` | 2 | ~30s | 2-round FISH with fluidics + imaging |
| `quick_tiled_zstack.yaml` | 1 | ~15s | Tiled imaging with 3-plane z-stack |
| `quick_fluidics_wash.yaml` | 3 | ~15s | Fluidics-only wash sequence |
| `demo_full_workflow.yaml` | 3 | ~45s | Complete setup → imaging → cleanup |
| `quick_multipoint.yaml` | 1 | ~10s | Simple multipoint for region testing |

## Recommended FOV Counts

- **1-2 FOVs**: For quick testing of workflow logic
- **2x2 grid (4 FOVs)**: For tiled imaging tests
- **3-4 FOVs across 2 regions**: For multi-region tests

## Usage

### Via Manual Test Scripts

```bash
cd software

# Run FISH protocol with 2 FOVs
python -m tests.e2e.manual.run_orchestrator \
    --protocol tests/e2e/configs/simulation/quick_fish_2round.yaml \
    --regions 1 --grid-size 1

# Run tiled z-stack with 2x2 grid
python -m tests.e2e.manual.run_orchestrator \
    --protocol tests/e2e/configs/simulation/quick_tiled_zstack.yaml \
    --grid-size 2

# Run full demo workflow
python -m tests.e2e.manual.run_orchestrator \
    --protocol tests/e2e/configs/simulation/demo_full_workflow.yaml
```

### Via Python

```python
from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator

with BackendContext(simulation=True) as ctx:
    sim = OrchestratorSimulator(ctx)

    # Load protocol
    sim.load_protocol("tests/e2e/configs/simulation/quick_fish_2round.yaml")

    # Add 2 FOVs
    center = ctx.get_stage_center()
    sim.add_single_fov("fov_1", center[0], center[1], center[2])
    sim.add_single_fov("fov_2", center[0] + 0.5, center[1], center[2])

    # Run
    result = sim.run_and_wait(timeout_s=60)
    print(f"Success: {result.success}")
    print(f"Completed rounds: {result.completed_rounds}")
```

## Timing Notes

- Fluidics `incubate` steps: 2-3 seconds each
- Flow operations: Simulated instantly in simulation mode
- Imaging: ~0.5-1s per FOV per z-plane
- All protocols designed to complete in under 1 minute with 2-4 FOVs
