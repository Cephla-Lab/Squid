# Workflow Runner

The Workflow Runner allows you to automate sequences of external scripts and acquisitions. This is useful for integrating Squid with external hardware such as liquid handling systems, fluidics controllers, or robotic arms.

## Accessing the Workflow Runner

Open the Workflow Runner from the menu: **Utils > Workflow Runner**

## Overview

A workflow consists of one or more **sequences** that run in order. Each sequence can be either:

- **Acquisition**: The built-in multipoint acquisition. You can add multiple acquisition sequences, each optionally loading settings from a different YAML config file.
- **Script**: An external Python script

Workflows can repeat multiple times using **Cycles**. Each cycle runs all sequences in order.

## The Workflow Runner Dialog

### Sequences Table

| Column | Description |
|--------|-------------|
| Include | Checkbox to include/skip this sequence |
| Name | Display name for the sequence |
| Command/Path | Script path and arguments, or config file path for acquisitions |
| Cycle Arg | Argument name to pass cycle-specific values (e.g., `port`) |
| Cycle Arg Values | Comma-separated values for each cycle (e.g., `1,2,3,4,5`) |

### Controls

- **Cycles**: Number of times to repeat the entire workflow (default: 1)
- **Insert Above/Below**: Add a new sequence (Script or Acquisition)
- **Edit**: Edit the selected sequence
- **Remove**: Remove the selected sequence
- **Save/Load**: Save or load workflow configurations as YAML files
- **Run**: Start the workflow
- **Pause**: Pause after the current sequence completes (click again to Resume)
- **Stop**: Stop the workflow after the current sequence completes

## Adding Sequences

1. Click **Insert Above** or **Insert Below**
2. Choose the sequence type: **Script** or **Acquisition**

### Script Sequences

Fill in the dialog:
- **Name**: A descriptive name (e.g., "Fluidics Control")
- **Script Path**: Path to the Python script
- **Arguments**: Command-line arguments for the script
- **Python Path** (optional): Specific Python executable to use
- **Conda Env** (optional): Conda environment name (overrides Python Path)

If both Python Path and Conda Env are left empty, the script runs with the same Python that runs Squid.

### Acquisition Sequences

Fill in the dialog:
- **Name**: A descriptive name (e.g., "Brightfield Scan")
- **Config File** (optional): Path to an `acquisition.yaml` file
- **Use the targets from the Analyze step** (optional): run whatever the most recent
  [Analyze sequence](#analyze-sequences) produced; the config becomes `{last_targets}`
- **Wells** (optional): a typed well list such as `C5, C6, D5`, which replaces the wells
  stored in a wellplate config file (the file's scan size and overlap still apply)

If no config file is specified, the acquisition uses the current GUI settings of the current
tab. If a config file is provided, **the file chooses the tab**: a `wellplate` file runs in
Wellplate Multipoint, a `flexible` file in Flexible Multipoint and a `record_zstack` file in
Time at XY, whichever tab was focused. Settings are loaded from the file before starting.

When a workflow runs, nothing asks for confirmation: a config that cannot be loaded (hardware
mismatch, wells not on the plate, missing saving directory, laser AF requested without a
reference) fails the step with the reason in the log instead of showing a dialog, and the
time-lapse pacing and disk-space dialogs are skipped.

### Objective Sequences

- **Objective**: the objective to switch to (rotates the turret when the machine has one)
- **Require a laser-AF reference**: fail the step unless a laser-AF reference is stored for
  that objective (`user_profiles/<profile>/laser_af_configs/<objective>.yaml`)

Switching reloads that objective's laser-AF reference, exactly as choosing it in the Objective
Lens dropdown does. If the objective is already selected nothing moves, but the reference is
still checked. A failed objective step always stops the workflow.

### Analyze Sequences

Turns the acquisition that ran before it into a new set of stage coordinates and writes a
runnable `acquisition.yaml` (see [Smart Acquisition](smart-acquisition.md)):

- **Analyzer** and **Parameters** (`key = value` lines; the analyzer's main parameters are
  pre-filled)
- **Template**: the follow-up run's `acquisition.yaml` (channels, recording, time series come
  from here; only the coordinates are new)
- **Survey** (optional): analyse this folder instead of the previous acquisition of the run
- **Output** (optional): where to write; default `<survey>/smart_targets`
- **Objective offset** dx, dy, dz in mm: follow-up position = survey position + offset
- **Carry survey Z** (+ dz), **Sweep z-stack**, **Write overlays**

The step runs inside Squid (no script, no conda environment). A survey in which nothing is
found fails the step, which always stops the workflow; the overlays are still written for
inspection. The path of the emitted `acquisition.yaml` is available to later acquisition
sequences as `{last_targets}` and to scripts as `SQUID_LAST_TARGETS_PATH`.

### Stop at the first failed step

The checkbox next to **Cycles**. Without it a failed script or acquisition is logged and the
next sequence still runs (the original behaviour). Objective and Analyze sequences stop the
workflow when they fail whatever the setting, since nothing after them could be right.

### New Smart Ring Workflow...

Builds the survey -> analyze -> switch objective -> image-every-target recipe from six
inputs (wells, survey `acquisition.yaml`, survey objective, follow-up `acquisition.yaml`,
follow-up objective, objective offset). The five sequences land in the table with stop on
failure set; Save the workflow, then Run. The dialog shows whether each objective has a
laser-AF reference and warns about a survey file recorded with a different objective, and
about a Time at XY template that ticks Use Focus Map, uses Set Range planes or has Laser AF
unticked (see [Smart Acquisition](smart-acquisition.md), "Making the Time at XY template").

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
   - Acquisition: Loads config file (if specified) then starts acquisition
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
    config_path: null
    cycle_arg_name: port
    cycle_arg_values: "1,2,3,4,5"

  - name: Acquisition
    type: acquisition
    included: true
    script_path: null
    arguments: null
    python_path: null
    conda_env: null
    config_path: /home/user/configs/multipoint_settings.yaml
    cycle_arg_name: null
    cycle_arg_values: null
```

The `config_path` field for acquisition sequences points to an `acquisition.yaml` file. If `null`, the acquisition uses current GUI settings.

## Writing Scripts for Workflow Runner

Scripts should:

1. Accept command-line arguments (use `argparse`)
2. Exit with code 0 on success, non-zero on failure
3. Print status messages to stdout (displayed in the log)

### Environment variables

Every script sequence receives two environment variables:

| Variable | Meaning |
|----------|---------|
| `SQUID_LAST_ACQUISITION_PATH` | Folder of the most recent acquisition in this workflow run (empty if none yet) |
| `SQUID_LAST_TARGETS_PATH` | `acquisition.yaml` written by the most recent Analyze sequence (empty if none yet) |
| `SQUID_CYCLE` | The current cycle number |

This is how a script acts on data that was just acquired without anyone pasting a
timestamped path into the workflow. They are passed through the environment
rather than appended to **Arguments** because arguments are parsed with
`shlex.split`, which eats the backslashes in a Windows path and splits it on
spaces.

```python
import os
acquisition = os.environ.get("SQUID_LAST_ACQUISITION_PATH")
if not acquisition:
    raise SystemExit("no acquisition to analyse yet")
```

See [Smart Acquisition](smart-acquisition.md) for a worked
`Acquisition -> Analyze -> Acquisition` workflow built on this.

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
- Acquisitions use whichever multipoint tab is active when you click Run
- Script errors are displayed in the log (workflow continues with remaining sequences)
- You can uncheck **Include** on any sequence to skip it without removing it
- Use **Edit** to modify sequences without removing and re-adding them
- Multiple acquisition sequences can use different config files for different imaging settings
