# Fluidics Configurations

## Hardware Configuration

- `simulation_fluidics.json` - Simulated fluidics hardware config with common solutions

## Fluidics Protocol YAML

- `test_fluidics_protocols.yaml` - Named fluidics protocols for E2E testing

Protocols are organized by name and referenced from experiment protocol YAMLs
via fluidics steps. Experiment protocols should not inline fluidics protocols.

## Usage

### Manual Testing (Fluidics UI)

1. Open the Fluidics panel
2. Click "Load Protocols..." and select a YAML file
3. Select a protocol from the dropdown and click "Run"

### Full Experiment (Orchestrator)

Reference protocols by name in experiment YAML files:

```yaml
rounds:
  - name: "Hybridization 1"
    steps:
      - step_type: fluidics
        protocol: probe_1_hybridization
      - step_type: imaging
        protocol: fish_standard
```

Load fluidics protocols into the FluidicsController separately (UI or test harness).

## YAML Protocol Format

```yaml
protocols:
  protocol_name:
    description: "Human-readable description"
    steps:
      - operation: flow
        solution: probe_1
        volume_ul: 100
        flow_rate_ul_per_min: 500
        description: "Flow probe to chamber"
      - operation: incubate
        duration_s: 60
        description: "Incubation step"
      - operation: wash
        solution: wash_buffer
        volume_ul: 200
        flow_rate_ul_per_min: 500
        description: "Wash cycle"
```

### Operations

| Operation | Required Fields | Optional Fields | Description |
|-----------|-----------------|-----------------|-------------|
| `flow` | `solution`, `volume_ul` | `flow_rate_ul_per_min` | Flow solution through chamber |
| `wash` | `solution`, `volume_ul` | `flow_rate_ul_per_min` | Flow solution + empty to waste |
| `incubate` | `duration_s` | - | Wait for specified duration |
| `prime` | `solution`, `volume_ul` | `flow_rate_ul_per_min` | Prime lines with solution |
| `empty` | - | - | Empty syringe to waste |

### Field Descriptions

- **solution**: Solution name from fluidics hardware config
- **volume_ul**: Volume in microliters
- **flow_rate_ul_per_min**: Flow rate (default varies by hardware, max typically 1000 uL/min)
- **duration_s**: Duration in seconds for incubation
- **description**: Human-readable step description (optional)

## Flow Rate Limits

The simulation driver enforces a maximum flow rate of 1 mL/min (1000 uL/min).
Flow operations simulate realistic timing based on volume and rate:
- 300 uL at 500 uL/min = 36 seconds
- 100 uL at 1000 uL/min = 6 seconds
