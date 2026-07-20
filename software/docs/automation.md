# Automated Acquisition via Scripts

This document describes how to run automated acquisitions using the `run_acquisition.py` script, or directly via the REST API. This approach is ideal for batch processing, CI pipelines, or headless operation.

**New here?** The [API Quickstart](quickstart-api.md) is the fastest way in — three ways to launch a scan, copy-paste examples, verified against the simulator.

For AI-assisted control via Claude Code, see [MCP Integration](mcp_integration.md). For the full REST API reference (endpoints, faults, jobs, SSE), see [Core Service API](core-service-api.md).

## Overview

The automation workflow:
1. Configure and save an acquisition in the GUI (creates `acquisition.yaml`), or define a named server-side method under `machine_configs/acquisition_methods/`
2. Run the acquisition programmatically using the saved YAML (or method name) via the REST API
3. Optionally override parameters like wells or save location

**Note:** Only wellplate mode acquisitions are supported via scripting/the REST API. FlexibleMultiPoint acquisitions must be run from the GUI.

## Prerequisites

- Squid software installed and configured
- Python environment with Squid dependencies (includes `httpx`)

## Enabling the Control Server

The Squid GUI process serves the automation API on two ports:

- **REST API (port 8060)** — the current API; used by `run_acquisition.py`, the MCP bridge, and any `curl`/`httpx` client. See [Core Service API](core-service-api.md).
- **Legacy TCP control server (port 5050)** — newline-delimited JSON protocol; **deprecated**, kept only for backward compatibility with older integrations.

Both start together.

**Option 1: Via command line (recommended for automation)**
```bash
python3 main_hcs.py --start-server
```

**Option 2: Via GUI**
- Go to Settings and check "Enable MCP Control Server"

### curl quick-start

```bash
# Is the service alive?
curl http://127.0.0.1:8060/v1/healthz

# Instrument state, active job, latest fault
curl http://127.0.0.1:8060/v1/system/status

# Start an acquisition from a saved YAML (returns 202 + job handle)
curl -X POST http://127.0.0.1:8060/v1/acquisitions \
  -H "Content-Type: application/json" \
  -d '{"yaml_path": "/path/to/acquisition.yaml"}'
```

## Basic Usage

### Run an acquisition
```bash
python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --wait
```

### Run from a named server-side method
```bash
python scripts/run_acquisition.py --method my_method --wait
```

### Run in simulation mode
```bash
python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --simulation --wait
```

### Validate against the live instrument without running (dry run)
```bash
python scripts/run_acquisition.py --yaml /path/to/acquisition.yaml --no-launch --dry-run
```
This runs the server-side preflight checks (`POST /v1/acquisitions/preflight`) — YAML parsing, widget type,
hardware match, channel names, regions, and output path — without starting the acquisition. It requires a
reachable server (launch the GUI first, or omit `--no-launch` to let the script launch it).

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
python scripts/run_acquisition.py --yaml acquisition.yaml --host 192.168.1.100 --port 8060
```

Non-loopback binds **require** authentication (the service refuses to start without `auth_enabled=true` +
`auth_token`; see [Core Service API — Authentication](core-service-api.md#authentication)). The script reads
the bearer token from the `SQUID_API_TOKEN` environment variable and sends it on every request:

```bash
SQUID_API_TOKEN=your-token python scripts/run_acquisition.py --yaml acquisition.yaml --host 192.168.1.100 --port 8060
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--yaml`, `-y` | Path to acquisition.yaml file (exactly one of `--yaml`/`--method` required) |
| `--method` | Name of a server-side acquisition method under `machine_configs/acquisition_methods/` (alternative to `--yaml`) |
| `--wells`, `-w` | Override wells from YAML (e.g., 'A1:B3' or 'A1,A2,B1') |
| `--base-path` | Override save location |
| `--simulation` | Run in simulation mode (no hardware) |
| `--wait` | Wait for acquisition to complete |
| `--timeout` | Acquisition timeout in seconds (only with `--wait`) |
| `--no-launch` | Don't launch GUI, connect to existing one |
| `--dry-run` | Run server-side preflight checks only; don't start the acquisition |
| `--verbose`, `-v` | Show detailed output |
| `--host` | REST API host (default: 127.0.0.1) |
| `--port` | REST API port (default: 8060) |

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
- Check that port 8060 (REST API) is not blocked

### "Only wellplate-mode YAMLs are supported by the API"
- The YAML was saved from FlexibleMultiPoint mode
- FlexibleMultiPoint acquisitions must be run from the GUI, not via the script/REST API

### "Hardware configuration mismatch"
- The current objective or camera binning differs from when YAML was saved
- Switch to the correct objective before running

### Connection errors during monitoring
- The script will retry up to 10 consecutive errors before failing
- Check network connectivity and GUI status

### 401 Unauthorized
- Auth is only required when the server is bound to a non-loopback host; see
  [Core Service API — Authentication](core-service-api.md#authentication)
- Pass a token with `-H "Authorization: Bearer <token>"` (curl) or set `SQUID_API_TOKEN` (MCP bridge)

## See Also

- [Core Service API](core-service-api.md) - Full REST API reference (endpoints, faults, jobs, SSE)
- [MCP Integration](mcp_integration.md) - Control via Claude Code / AI agents
- [Configuration System](configuration-system.md) - Setting up imaging channels and profiles
