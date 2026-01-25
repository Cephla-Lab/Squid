# Test Files for Fluidics and Orchestrator

This directory contains test files for end-to-end testing of the fluidics system and experiment orchestrator.

## Files

### Fluidics Widget Testing

**`fluidics_sequences_fast.csv`** - Fast fluidics sequences for UI testing
- Short incubation times (5-10 seconds)
- Uses solution names from `fluidics_simulation.json`
- Protocols: Wash, Hybridization Round 1 & 2, Imaging Prep, Strip

### Orchestrator Testing

**`test_protocol_fast.yaml`** - Complete orchestrator protocol
- 2 imaging rounds with fluidics
- Short incubations (3-8 seconds)
- 3 z-planes per FOV
- Uses "BF LED matrix full" channel

## Solutions Available (fluidics_simulation.json)

The simulation config includes these solutions:
- `probe_1` through `probe_24` (ports 1-24)
- `wash_buffer` (port 25)
- `imaging_buffer` (port 26)
- `cleavage_buffer` (port 27)
- `rinse_buffer` (port 28)

**Note:** Do NOT use "PBS" - it's not in the simulation config. Use `wash_buffer` instead.

## Usage

### Testing Fluidics Widget Only

```bash
cd software
python main_hcs.py --simulation

# In the UI:
# 1. Go to Fluidics tab
# 2. Select config: configurations/fluidics_simulation.json
# 3. Click Initialize
# 4. Load sequences: test_files/fluidics_sequences_fast.csv
# 5. Select a protocol and click "Run Protocol"
```

### Testing Orchestrator with Fluidics

```bash
cd software
python main_hcs.py --simulation

# In the UI:
# 1. Initialize fluidics (as above)
# 2. Set up FOVs (add a few regions in Navigation tab)
# 3. Go to Experiment Orchestrator
# 4. Load protocol: test_files/test_protocol_fast.yaml
# 5. Click Start
```

## Setting Up FOVs

For orchestrator testing, you need FOV positions. In simulation mode:

1. Go to Navigation tab
2. Click "Add Region"
3. Set a small grid (e.g., 2x2)
4. Add 1-2 regions

Or use the API:
```python
# From the orchestrator UI or programmatically
scan_coords.add_region_by_grid(
    region_id="test_region",
    center_x=10.0,
    center_y=10.0,
    nx=2, ny=2,
    step_x=0.5, step_y=0.5
)
```

## Expected Timing

- **fluidics_sequences_fast.csv**: ~1-2 minutes total
- **test_protocol_fast.yaml**: ~2-3 minutes total (depends on FOV count)

## Troubleshooting

### "Solution not found" error
Make sure you're using solution names from `fluidics_simulation.json`:
- Use `wash_buffer` NOT `PBS`
- Use `cleavage_buffer` NOT `strip_buffer`

### Skip not working
The skip functionality requires:
1. Sequence is running
2. Click "Skip to Next Step" during a step
3. Check logs for error messages (now visible in UI)
