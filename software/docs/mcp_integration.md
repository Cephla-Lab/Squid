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

### Option A: Launch from GUI (Recommended)

1. Start the Squid GUI
2. Go to **Settings → Launch Claude Code**
3. If Claude Code is not installed, you'll be prompted to install it automatically
4. A terminal will open with Claude Code running in the correct directory

This automatically:
- Starts the MCP control server (on-demand)
- Configures the MCP connection
- Pre-approves all microscope commands

### On-Demand Control Server

The MCP control server does **not** start automatically when the GUI launches. It starts when:

| Action | Result |
|--------|--------|
| **Settings → Launch Claude Code** | Auto-starts server, then launches Claude Code |
| **Settings → Enable MCP Control Server** | Manually start/stop the server |

This improves security by only running the server when needed.

### Pre-configured Permissions

The repository includes `.claude/settings.json` which pre-approves all squid-microscope MCP commands. This means Claude Code won't ask for permission each time you run a microscope command.

If you need to customize permissions, create `.claude/settings.local.json` (gitignored) to override the defaults.

### Option B: Manual Configuration

Create a `.mcp.json` file in the `software` directory:

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

Then start Claude Code from the `software` directory:
```bash
cd /path/to/Squid-microscope/software
claude
```

### Verify Connection

In Claude Code, test the connection with:
```
microscope_ping
```

### Enable Python Exec (Optional)

The `python_exec` command is disabled by default for security. To enable it:

1. In Squid GUI, go to **Settings → Enable MCP Python Exec**
2. Read and accept the security warning
3. The setting resets to disabled when the GUI restarts

## Available Commands

> **Note:** When accessed via MCP (e.g., from Claude Code), commands are exposed with a `microscope_` prefix. For example, `ping` becomes `microscope_ping`, `move_to` becomes `microscope_move_to`, etc.

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
| `home` | - | Home all axes (X, Y, Z) |

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

### Direct Python Access

| Command | Parameters | Description |
|---------|------------|-------------|
| `python_exec` | `code` | Execute Python with direct access to all microscope objects |
| `get_python_exec_status` | - | Check if python_exec is enabled |

> **Note:** `python_exec` is disabled by default. Enable it via **Settings → Enable MCP Python Exec** in the GUI.

**Available objects in `python_exec`:**
- `microscope` - Main Microscope instance
- `stage` - microscope.stage (shortcut)
- `camera` - microscope.camera (shortcut)
- `live_controller` - microscope.live_controller
- `objective_store` - microscope.objective_store
- `multipoint_controller` - MultiPointController
- `scan_coordinates` - ScanCoordinates
- `gui` - GUI reference
- `np` - numpy module

**Special variables:**
- `result` - Set to return JSON-serializable data
- `image` - Set to ndarray to auto-save and return path

## Examples

> **Note:** The examples below show MCP tool calls as you would describe them to Claude Code. They are not raw Python - Claude Code translates these into the appropriate MCP protocol messages.

### Basic Imaging

```
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

```
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

### Direct Python Access

```
# Explore available objects
microscope_python_exec(code="result = dir(microscope)")

# Get position using direct access
microscope_python_exec(code="""
pos = stage.get_pos()
result = {'x': pos.x_mm, 'y': pos.y_mm, 'z': pos.z_mm}
""")

# Acquire and return image
microscope_python_exec(code="""
image = microscope.acquire_image()
result = f"Acquired {image.shape}, mean={image.mean():.1f}"
""")
# Then use Read tool on returned image_path to view it

# Complex operations
microscope_python_exec(code="""
# Access any nested object
af_camera = microscope.addons.camera_focus
if af_camera:
    result = {'has_af': True, 'methods': [m for m in dir(af_camera) if not m.startswith('_')]}
else:
    result = {'has_af': False}
""")
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
- Enable the control server via **Settings → Enable MCP Control Server** (or use **Launch Claude Code** which auto-starts it)
- Verify port 5050 is not blocked

### Command timeout
- Long acquisitions may exceed the default 30s timeout
- Check `get_acquisition_status` for progress on running scans

### "Channel not found"
- Channel names are objective-specific
- Use `get_channels` to list available channels for current objective
