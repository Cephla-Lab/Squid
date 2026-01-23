# Automated Acquisition via Scripts

This document describes how to run automated acquisitions using the `run_acquisition.py` script. This approach is ideal for batch processing, CI pipelines, or headless operation.

For AI-assisted control via Claude Code, see [MCP Integration](mcp_integration.md).

## Overview

The automation workflow:
1. Configure and save an acquisition in the GUI (creates `acquisition.yaml`)
2. Run the acquisition programmatically using the saved YAML
3. Optionally override parameters like wells or save location

**Note:** Only wellplate mode acquisitions are supported via scripting. FlexibleMultiPoint acquisitions must be run from the GUI.

## Prerequisites

- Squid software installed and configured
- Python environment with Squid dependencies

## Enabling the Control Server

The TCP control server must be running to accept commands.

**Option 1: Via command line (recommended for automation)**
```bash
python3 main_hcs.py --start-server
```

**Option 2: Via GUI**
- Go to Settings and check "Enable MCP Control Server"

## Basic Usage

### Run an acquisition
```bash
python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --wait
```

### Run in simulation mode
```bash
python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --simulation --wait
```

### Validate YAML without running (dry run)
```bash
python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --dry-run
```

## Parameter Overrides

You can override certain parameters from the saved YAML:

### Override wells
```bash
# Range format
python scripts/run_acquisition.py --yaml acquisition.yaml --wells "A1:B3" --wait

# List format
python scripts/run_acquisition.py --yaml acquisition.yaml --wells "A1,A2,B1,B2" --wait
```

### Override save location
```bash
python scripts/run_acquisition.py --yaml acquisition.yaml --base-path /data/experiments --wait
```

## Connection Options

### Connect to already-running GUI
```bash
python scripts/run_acquisition.py --yaml acquisition.yaml --no-launch --wait
```

### Custom host/port
```bash
python scripts/run_acquisition.py --yaml acquisition.yaml --host 192.168.1.100 --port 5050
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--yaml`, `-y` | Path to acquisition.yaml file (required) |
| `--wells`, `-w` | Override wells from YAML (e.g., 'A1:B3' or 'A1,A2,B1') |
| `--base-path` | Override save location |
| `--simulation` | Run in simulation mode (no hardware) |
| `--wait` | Wait for acquisition to complete |
| `--no-launch` | Don't launch GUI, connect to existing one |
| `--dry-run` | Validate YAML without running |
| `--verbose`, `-v` | Show detailed output |
| `--host` | Server host (default: localhost) |
| `--port` | Server port (default: 5050) |

## Exit Codes

The script returns appropriate exit codes for automation:

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (server unavailable, acquisition failed, etc.) |

## Example: Batch Processing

```bash
#!/bin/bash
# Run acquisitions for multiple YAML configs

CONFIGS=(
    "/data/configs/plate1.yaml"
    "/data/configs/plate2.yaml"
    "/data/configs/plate3.yaml"
)

for config in "${CONFIGS[@]}"; do
    echo "Running: $config"
    python scripts/run_acquisition.py --yaml "$config" --wait --verbose

    if [ $? -ne 0 ]; then
        echo "Failed: $config"
        exit 1
    fi
done

echo "All acquisitions complete"
```

## Example: CI Pipeline

```yaml
# GitHub Actions example
jobs:
  acquisition:
    runs-on: self-hosted
    steps:
      - name: Run acquisition
        run: |
          python scripts/run_acquisition.py \
            --yaml configs/test_acquisition.yaml \
            --simulation \
            --wait \
            --verbose
```

## Troubleshooting

### "Control server did not become available"
- Ensure the GUI is running with `--start-server` flag
- Or enable via Settings → Enable MCP Control Server
- Check that port 5050 is not blocked

### "TCP command only supports wellplate mode"
- The YAML was saved from FlexibleMultiPoint mode
- Re-save the acquisition using wellplate mode, or run from GUI

### "Hardware configuration mismatch"
- The current objective or camera binning differs from when YAML was saved
- Switch to the correct objective before running

### Connection errors during monitoring
- The script will retry up to 10 consecutive errors before failing
- Check network connectivity and GUI status

## See Also

- [MCP Integration](mcp_integration.md) - Control via Claude Code / AI agents
- [Configuration System](configuration-system.md) - Setting up imaging channels and profiles
