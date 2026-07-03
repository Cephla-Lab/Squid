# MCP Integration for Squid Microscope

This document describes how to use the Model Context Protocol (MCP) integration to control the Squid microscope from Claude Code or other MCP-compatible AI agents.

## Architecture

```
┌─────────────┐     stdio      ┌──────────────────┐     REST :8060     ┌─────────────────────────┐
│ Claude Code │ ◄────────────► │ MCP Server       │ ◄──────────────►   │ squid_service           │
│             │                │ (curated tools;  │      HTTP/JSON     │ (SquidCoreService,      │
│             │                │  mcp_microscope_ │                    │  runs inside the GUI)   │
│             │                │  server.py)      │                    │                         │
└─────────────┘                └──────────────────┘                    └────────────┬────────────┘
                                                                                     │
                                                                                     ▼
                                                                        ┌─────────────────────────┐
                                                                        │ Microscope Hardware     │
                                                                        │ (stage, camera, etc.)   │
                                                                        └─────────────────────────┘
```

1. **Claude Code** connects to the MCP server via stdio
2. **MCP Server** (`mcp_microscope_server.py`) is a thin, static, curated-tool bridge that translates each MCP tool call into one or more REST calls (via `httpx`), targeting `SQUID_API_URL` (default `http://127.0.0.1:8060`)
3. **squid_service** (`squid_service/service.py` + `squid_service/rest/`) runs inside the GUI process, serves the REST+SSE API described in [Core Service API](core-service-api.md), and executes commands on the microscope

The legacy TCP control server (port 5050, newline-delimited JSON) still runs alongside the REST API for backward compatibility, but the MCP bridge no longer talks to it.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SQUID_API_URL` | `http://127.0.0.1:8060` | Base URL of the REST API the MCP bridge talks to |
| `SQUID_API_TOKEN` | unset | Bearer token, sent as `Authorization: Bearer <token>` when set (needed only if the server has auth enabled — see [Core Service API — Authentication](core-service-api.md#authentication)) |

## Setup

### Option A: Launch from GUI (Recommended)

1. Start the Squid GUI
2. *(Optional)* Go to **Settings → Set Anthropic API Key...** and enter your API key (get one from [console.anthropic.com](https://console.anthropic.com/settings/keys)). If you are already logged into claude.ai, you can skip this step.
3. Go to **Settings → Launch Claude Code**
4. If Claude Code is not installed, you'll be prompted to install it automatically
5. A terminal will open with Claude Code running in the correct directory

This automatically:
- Starts the MCP control server (on-demand)
- Passes the API key if one is set (via a temporary launcher script that keeps it out of command-line arguments; the key is set as an environment variable for the Claude Code process)
- Configures the MCP connection
- Pre-approves all microscope commands

**Authentication:** Claude Code supports two authentication methods:
- **claude.ai login** (OAuth) — If you are already logged in via `claude login`, no API key is needed
- **API key** — Set via **Settings → Set Anthropic API Key...**; cached locally in `cache/claude_api_key.yaml` and persists across restarts

### On-Demand Control Server

The control server does **not** start automatically when the GUI launches. Starting it brings up both the
REST API (port 8060, used by the MCP bridge) and the legacy TCP server (port 5050) together. It starts when:

| Action | Result |
|--------|--------|
| **Settings → Launch Claude Code** | Auto-starts server, then launches Claude Code |
| **Settings → Enable MCP Control Server** | Manually start/stop the server |
| `python3 main_hcs.py --start-server` | Starts the server at GUI launch |

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
| `get_status` | Get comprehensive microscope status (state, active job, latest fault) |
| `get_capabilities` | Channels, objectives, stage travel, camera, simulation flag |
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

### Autofocus

| Command | Parameters | Description |
|---------|------------|-------------|
| `autofocus` | `target_um` | Run reflection (laser) autofocus at the current position |
| `autofocus_status` | - | Reflection (laser) autofocus hardware/reference readiness |
| `store_af_reference` | - | Capture the current laser spot as the new reflection-AF reference |

### Multi-Point Acquisition & Methods

| Command | Parameters | Description |
|---------|------------|-------------|
| `run_acquisition` | `wells`, `channels`, `nx`, `ny`, `wellplate_format`, `overlap_percent`, `experiment_id`, `base_path` | Run a grid-mode multi-well acquisition; returns a job handle |
| `run_acquisition_from_yaml` | `yaml_path`, `wells`, `base_path`, `experiment_id` | Run acquisition from a saved YAML config; returns a job handle |
| `get_methods` | - | List named acquisition methods stored on the server |
| `run_method` | `method`, `experiment_id`, `wells`, `base_path`, `operator` | Start an acquisition from a named server-side method; returns a job handle |
| `get_acquisition_status` | - | Instrument status plus active or last job progress |
| `get_job` | `job_id` | Get a job record by id |
| `abort_acquisition` | `timeout_s` | Gracefully abort the running acquisition |

> **Note:** All acquisition commands only support wellplate mode. FlexibleMultiPoint acquisitions must be run
> from the GUI. For scripted automation, see [Automation](automation.md). Acquisition methods live under
> `machine_configs/acquisition_methods/`; see [Core Service API — Method registry](core-service-api.md#method-registry).

### Performance & View Settings

| Command | Parameters | Description |
|---------|------------|-------------|
| `set_performance_mode` | `enabled` | Toggle performance mode (faster, less RAM); requires a GUI |
| `get_performance_mode` | - | Get current performance/view debug settings |
| `get_view_settings` | - | Get downsampled-well-image saving + mosaic display + performance mode |
| `set_view_settings` | `save_downsampled_well_images`, `display_mosaic_view` | Set multiple view settings at once |
| `set_save_downsampled_images` | `enabled` | Enable/disable saving per-well downsampled TIFFs (next acquisition) |
| `set_display_mosaic_view` | `enabled` | Enable/disable mosaic view display (immediate) |

> **Note:** `microscope_set_display_plate_view` does not exist. The legacy `DISPLAY_PLATE_VIEW` flag it
> toggled was removed — plate view was unified into the mosaic view (`UnifiedMosaicWidget`), governed solely
> by `display_mosaic_view`.

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

The MCP bridge talks HTTP/JSON to the REST API (see [Core Service API](core-service-api.md) for the full
endpoint reference). Successful responses are plain JSON objects; every non-2xx response body is a
canonical Fault, so agents can branch on `category`/`code`/`terminal` instead of parsing free-text errors:

```json
{
  "error": {
    "category": "INVALID_PARAM",
    "code": 2001,
    "recoverable": false,
    "scheduler_action": "REJECT_PLATE",
    "component": "stage.x",
    "message": "x target 200.000 mm outside [0.0, 120.0]",
    "detail": {"axis": "x", "target_mm": 200.0},
    "timestamp": "2026-07-02T12:00:00Z",
    "terminal": false,
    "operator_intervention_required": false,
    "plate_removable": true
  }
}
```

The MCP bridge returns this JSON verbatim as the tool's text result (it does not raise/throw), so a tool
call that "failed" still returns successfully to Claude Code — inspect the `error` key to detect it.

## Troubleshooting

### "API Key Not Set" when launching Claude Code
- Go to **Settings → Set Anthropic API Key...** to enter your key
- Get a key from [console.anthropic.com](https://console.anthropic.com/settings/keys)
- The key is cached locally and persists across restarts

### "Auth conflict" warning in Claude Code
- This occurs when you have both an API key and an existing claude.ai login
- Claude Code will use the API key; the warning is informational and can be ignored

### "Cannot connect to microscope"
- Ensure the Squid GUI is running
- Enable the control server via **Settings → Enable MCP Control Server** (or use **Launch Claude Code** which auto-starts it)
- Verify port 8060 (REST API) is not blocked; the bridge reports the exact URL it tried in the error message
- If `SQUID_API_URL` is set, confirm it points at the right host/port

### Command timeout
- Long acquisitions run asynchronously as jobs; a "timeout" on `run_acquisition_from_yaml`/`run_method`/`run_acquisition`
  only means the *start* request was slow — the acquisition itself keeps running
- Check `get_acquisition_status` or `get_job` for progress on running scans

### "Channel not found"
- Channel names are objective-specific
- Use `get_channels` to list available channels for current objective

### 401 Unauthorized
- Only occurs when the server has auth enabled (non-default; required for non-loopback binds)
- Set `SQUID_API_TOKEN` in the environment Claude Code runs in

## See Also

- [Core Service API](core-service-api.md) - Full REST API reference (endpoints, faults, jobs, SSE)
- [Automation](automation.md) - Scripted acquisitions via `run_acquisition.py` or `curl`
