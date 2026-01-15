# Manual E2E Testing Scripts

This directory contains interactive scripts for manually testing the Squid microscope control software workflows.

## Prerequisites

Run scripts from the `software/` directory:

```bash
cd /path/to/Squid/software
```

## Available Scripts

### run_imaging.py

Test imaging acquisition workflows without orchestrator.

```bash
# Single FOV
python -m tests.e2e.manual.run_imaging --single-fov

# 3x3 grid
python -m tests.e2e.manual.run_imaging --grid 3x3

# 2x2 grid with 5-plane z-stack
python -m tests.e2e.manual.run_imaging --grid 2x2 --zstack 5 --z-step 1.0

# Multiple regions
python -m tests.e2e.manual.run_imaging --grid 2x2 --regions 3

# With specific channels
python -m tests.e2e.manual.run_imaging --grid 2x2 --channels "BF LED matrix full" "Fluorescence 488 nm Ex"

# Save images to disk
python -m tests.e2e.manual.run_imaging --grid 2x2 --save-images --output ./test_output

# Verbose output
python -m tests.e2e.manual.run_imaging --grid 3x3 --verbose
```

### run_orchestrator.py

Test multi-round orchestrated experiments.

```bash
# Run single-round imaging protocol
python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/single_round_imaging.yaml

# Run multi-round FISH protocol
python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/multi_round_fish.yaml

# With 2x2 grid regions
python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/tiled_zstack.yaml \
    --grid-size 2 --regions 2

# Pause at specific round (interactive)
python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/multi_round_fish.yaml \
    --pause-at-round 2

# Manual intervention acknowledgment
python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/intervention_protocol.yaml \
    --no-auto-acknowledge

# Verbose output
python -m tests.e2e.manual.run_orchestrator --protocol tests/e2e/configs/protocols/multi_round_fish.yaml -v
```

## Protocol Files

Available in `tests/e2e/configs/protocols/`:

| Protocol | Description |
|----------|-------------|
| `single_round_imaging.yaml` | Minimal single imaging round |
| `single_round_imaging_save.yaml` | Single imaging round with saving enabled |
| `tiled_zstack.yaml` | Tiled imaging with 5-plane z-stack |
| `multi_round_fish.yaml` | 4-round FISH with fluidics and imaging |
| `fluidics_only.yaml` | Fluidics-only workflow (no imaging) |
| `intervention_protocol.yaml` | Protocol with operator interventions |

## Notes

- All scripts run in **simulation mode** - no real hardware required
- Output directories are created automatically (temp dir if not specified)
- Images are not saved by default (`--save-images` to enable)
- Protocol durations are shortened for testing (0.1-0.5s incubations)
- Press Ctrl+C to abort at any time
