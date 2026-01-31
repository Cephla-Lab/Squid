# Workflow Runner

The Workflow Runner allows you to automate sequences of external scripts and acquisitions. This is useful for integrating Squid with external hardware such as liquid handling systems, fluidics controllers, or robotic arms.

## Accessing the Workflow Runner

Open the Workflow Runner from the menu: **Utils > Workflow Runner**

## Overview

A workflow consists of one or more **sequences** that run in order. Each sequence can be either:

- **Acquisition**: The built-in multipoint acquisition (uses current tab settings)
- **Script**: An external Python script

Workflows can repeat multiple times using **Cycles**. Each cycle runs all sequences in order.

## The Workflow Runner Dialog

### Sequences Table

| Column | Description |
|--------|-------------|
| Include | Checkbox to include/skip this sequence |
| Name | Display name for the sequence |
| Command | Script path and arguments (or "Built-in Acquisition") |
| Cycle Arg | Argument name to pass cycle-specific values (e.g., `port`) |
| Cycle Arg Values | Comma-separated values for each cycle (e.g., `1,2,3,4,5`) |

### Controls

- **Cycles**: Number of times to repeat the entire workflow (default: 1)
- **Insert Above/Below**: Add a new script sequence
- **Remove**: Remove the selected sequence (Acquisition cannot be removed)
- **Save/Load**: Save or load workflow configurations as YAML files
- **Run**: Start the workflow
- **Pause**: Pause after the current sequence completes (click again to Resume)
- **Stop**: Stop the workflow after the current sequence completes

## Adding a Script Sequence

1. Click **Insert Above** or **Insert Below**
2. Fill in the dialog:
   - **Name**: A descriptive name (e.g., "Fluidics Control")
   - **Script Path**: Path to the Python script
   - **Arguments**: Command-line arguments for the script
   - **Python Path** (optional): Specific Python executable to use
   - **Conda Env** (optional): Conda environment name (overrides Python Path)

If both Python Path and Conda Env are left empty, the script runs with the same Python that runs Squid.

## Using Cycle Arguments

Cycle arguments allow you to pass different values to a script for each cycle. This is useful for sequential operations like cycling through fluidics ports.

**Example**: Running 5 cycles with different port numbers

1. Set **Cycles** to `5`
2. For your script sequence:
   - Set **Cycle Arg** to `port`
   - Set **Cycle Arg Values** to `1,2,3,4,5`

The script will be called with:
- Cycle 1: `python script.py --port 1`
- Cycle 2: `python script.py --port 2`
- Cycle 3: `python script.py --port 3`
- etc.

**Important**: The number of cycle arg values must match the number of cycles.

## Workflow Execution

When you click **Run**:

1. The workflow starts in the background
2. GUI controls are disabled to prevent accidental changes
3. Each sequence runs in order:
   - Scripts: Output is displayed in the log area
   - Acquisition: Uses current multipoint tab settings
4. After each acquisition, the save path is logged
5. The cycle repeats until all cycles complete

### During Execution

- **Pause**: Pauses after the current sequence. GUI is re-enabled while paused.
- **Stop**: Stops after the current sequence completes.
- The log shows script output and acquisition paths.

### Save Log

Click **Save Log...** to save the execution log to a text file.

## Example Workflow

A typical cyclic sequencing workflow:

```
Cycles: 5

Sequences:
1. Fluidics (script) - Cycle Arg: port, Values: 1,2,3,4,5
2. Acquisition (built-in)
```

This runs:
1. Fluidics script with `--port 1`, then Acquisition
2. Fluidics script with `--port 2`, then Acquisition
3. ... and so on for 5 cycles

## Saving and Loading Workflows

Workflows are saved as YAML files. Example:

```yaml
num_cycles: 5
sequences:
  - name: Fluidics
    type: script
    included: true
    script_path: /home/user/scripts/fluidics.py
    arguments: --wash --volume 500
    python_path: null
    conda_env: fluidics_env
    cycle_arg_name: port
    cycle_arg_values: "1,2,3,4,5"

  - name: Acquisition
    type: acquisition
    included: true
```

## Writing Scripts for Workflow Runner

Scripts should:

1. Accept command-line arguments (use `argparse`)
2. Exit with code 0 on success, non-zero on failure
3. Print status messages to stdout (displayed in the log)

**Example script**:

```python
#!/usr/bin/env python3
import argparse
import time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--volume", type=int, default=500)
    args = parser.parse_args()

    print(f"Dispensing {args.volume}uL from port {args.port}")
    # ... do the actual work ...
    time.sleep(2)
    print("Done")

if __name__ == "__main__":
    main()
```

## Tips

- Test scripts independently before adding to workflow
- Use the **Pause** feature to inspect results between cycles
- The Acquisition sequence uses whichever multipoint tab is active when you click Run
- Script errors are displayed in the log (workflow continues with remaining sequences)
- You can uncheck **Include** on the Acquisition sequence to run only scripts
