# MCP Integration for Squid Microscope

This document describes how to use the Model Context Protocol (MCP) integration to control the Squid microscope from Claude Code or other MCP-compatible AI agents.

## Architecture

```
┌─────────────┐     stdio      ┌──────────────────┐     TCP:5050     ┌─────────────────────────┐
│ Claude Code │ ◄────────────► │ MCP Server       │ ◄──────────────► │ MicroscopeControlServer │
│             │                │ (mcp_microscope_ │                  │ (runs inside GUI)       │
│             │                │  server.py)      │                  │                         │
└─────────────┘                └──────────────────┘                  └────────────┬────────────┘
                                                                                  │
                                                                                  ▼
                                                                     ┌─────────────────────────┐
                                                                     │ Microscope Hardware     │
                                                                     │ (stage, camera, etc.)   │
                                                                     └─────────────────────────┘
```

1. **Claude Code** connects to the MCP server via stdio
2. **MCP Server** (`mcp_microscope_server.py`) translates MCP tool calls to TCP commands
3. **MicroscopeControlServer** (`control/microscope_control_server.py`) runs inside the GUI process and executes commands on the microscope

## Setup

### 1. Configure Claude Code

Create a `.mcp.json` file in your project directory (or `~/.claude/.mcp.json` for global config):

```json
{
  "mcpServers": {
    "squid-microscope": {
      "command": "python3",
      "args": ["/path/to/Squid-microscope/software/mcp_microscope_server.py"]
    }
  }
}
```

### 2. Start the Squid GUI

The MicroscopeControlServer starts automatically with the GUI on port 5050.

### 3. Verify Connection

In Claude Code, the microscope tools will be available. Test with:
```
microscope_ping
```

## Available Commands

### Status & Position

| Command | Description |
|---------|-------------|
| `ping` | Check if server is running |
| `get_status` | Get comprehensive microscope status |
| `get_position` | Get current XYZ stage position (mm) |

### Stage Movement

| Command | Parameters | Description |
|---------|------------|-------------|
| `move_to` | `x_mm`, `y_mm`, `z_mm`, `blocking` | Move to absolute position |
| `move_relative` | `dx_mm`, `dy_mm`, `dz_mm`, `blocking` | Move by relative amount |
| `home` | `x`, `y`, `z` | Home specified axes |

### Imaging

| Command | Parameters | Description |
|---------|------------|-------------|
| `start_live` | - | Start live camera preview |
| `stop_live` | - | Stop live preview |
| `acquire_image` | `save_path` | Capture single image |
| `acquire_laser_af_image` | `save_path`, `use_last_frame` | Get laser autofocus camera image |

### Channel & Illumination

| Command | Parameters | Description |
|---------|------------|-------------|
| `get_channels` | - | List available channels for current objective |
| `set_channel` | `channel_name` | Set active imaging channel |
| `set_exposure` | `exposure_ms`, `channel` | Set camera exposure |
| `set_illumination_intensity` | `channel`, `intensity` | Set illumination (0-100%) |
| `turn_on_illumination` | - | Turn on current channel illumination |
| `turn_off_illumination` | - | Turn off all illumination |

### Objectives

| Command | Parameters | Description |
|---------|------------|-------------|
| `get_objectives` | - | List available objectives |
| `get_current_objective` | - | Get current objective |
| `set_objective` | `objective_name` | Switch objective |

### Multi-Point Acquisition

| Command | Parameters | Description |
|---------|------------|-------------|
| `run_acquisition` | `wells`, `channels`, `nx`, `ny`, `wellplate_format`, `overlap_percent` | Run automated well plate scan |
| `get_acquisition_status` | - | Check acquisition progress |
| `abort_acquisition` | - | Stop running acquisition |

### Performance

| Command | Parameters | Description |
|---------|------------|-------------|
| `set_performance_mode` | `enabled` | Toggle performance mode (faster, less RAM) |
| `get_performance_mode` | - | Check performance mode state |

## Examples

### Basic Imaging

```python
# Get current position
microscope_get_position()

# Move to a specific location
microscope_move_to(x_mm=50.0, y_mm=25.0)

# Set channel and acquire image
microscope_set_channel(channel_name="Fluorescence 488 nm Ex")
microscope_set_exposure(exposure_ms=100)
microscope_acquire_image(save_path="/path/to/image.tiff")
```

### Well Plate Scanning

```python
# Scan wells A1-B2 with multiple fluorescence channels
microscope_run_acquisition(
    wells="A1:B2",
    channels=["Fluorescence 488 nm Ex", "Fluorescence 561 nm Ex"],
    nx=2,
    ny=2,
    wellplate_format="96 well plate",
    overlap_percent=10
)

# Check progress
microscope_get_acquisition_status()
```

## Protocol Details

The TCP protocol uses newline-delimited JSON:

**Request:**
```json
{"command": "move_to", "params": {"x_mm": 50.0, "y_mm": 25.0}}
```

**Response (success):**
```json
{"success": true, "result": {"moved_to": {"x_mm": 50.0, "y_mm": 25.0, "z_mm": 1.2}}}
```

**Response (error):**
```json
{"success": false, "error": "Error message here"}
```

## Troubleshooting

### "Cannot connect to microscope"
- Ensure the Squid GUI is running
- Check that the control server is enabled (default: on)
- Verify port 5050 is not blocked

### Command timeout
- Long acquisitions may exceed the default 30s timeout
- Check `get_acquisition_status` for progress on running scans

### "Channel not found"
- Channel names are objective-specific
- Use `get_channels` to list available channels for current objective
