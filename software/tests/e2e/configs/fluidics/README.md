# Fluidics Configurations

## Hardware Configuration

- `simulation_fluidics.json` - Simulated fluidics hardware config with common solutions

## Fluidics Sequence CSVs

These CSV files define fluidics operations for manual testing via the Fluidics UI.
Each CSV matches a corresponding orchestrator protocol YAML in `../simulation/`.

| CSV File | Matching YAML | Description |
|----------|---------------|-------------|
| `quick_fish_2round_sequences.csv` | `quick_fish_2round.yaml` | 2-round FISH (probe + wash) |
| `quick_fluidics_wash_sequences.csv` | `quick_fluidics_wash.yaml` | Simple wash sequence |
| `demo_full_workflow_sequences.csv` | `demo_full_workflow.yaml` | Prep + cleanup |
| `merfish_4round_sequences.csv` | - | Full 4-round MERFISH (standalone) |
| `simple_stain_sequences.csv` | - | Basic stain + wash (standalone) |

## Usage

### Manual Testing (Fluidics UI only)

1. Open the Fluidics panel
2. Click "Load Sequences from CSV..."
3. Select a CSV file
4. Select a protocol from the list and click "Run Protocol"

### Full Experiment (Orchestrator)

Use the matching YAML file with the orchestrator:
```bash
python -m tests.e2e.manual.run_orchestrator \
    --protocol tests/e2e/configs/simulation/quick_fish_2round.yaml
```

The YAML files contain the same fluidics steps as the CSV, plus imaging configuration.

## CSV Format

```csv
protocol,operation,solution,volume_ul,flow_rate_ul_per_min,incubation_time_s
Hybridization 1,flow,probe_1,100,500,0
Hybridization 1,incubate,probe_1,0,0,2
Hybridization 1,flow,wash_buffer,200,500,0
```

### Columns

- **protocol**: Round/step name (groups operations, must match YAML round names)
- **operation**: `flow` or `incubate`
- **solution**: Solution name from fluidics config
- **volume_ul**: Volume in microliters (0 for incubate)
- **flow_rate_ul_per_min**: Flow rate (0 for incubate, max 1000 = 1 mL/min)
- **incubation_time_s**: Incubation duration in seconds (0 for flow)

## Flow Rate Limits

The simulation driver enforces a maximum flow rate of 1 mL/min (1000 uL/min).
Flow operations simulate realistic timing based on volume and rate:
- 300 uL at 500 uL/min = 36 seconds
- 100 uL at 1000 uL/min = 6 seconds
