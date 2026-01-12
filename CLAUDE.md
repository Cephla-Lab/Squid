# CLAUDE.md - Project Context for Claude Code

## Project Overview

Squid is a microscope control software with a PyQt5 GUI, TCP control server for automation, and support for wellplate imaging.

## Key Patterns

### Qt Thread-Safe GUI Updates

When updating GUI from a non-Qt thread and needing to wait for completion, use this pattern:

```python
import threading
from PyQt5.QtCore import QTimer

event = threading.Event()
def update():
    # GUI work here
    event.set()
QTimer.singleShot(0, update)
if not event.wait(timeout=5.0):
    log.warning("GUI update timed out")
```

### TCP Control Server

- Port: **5050** (not 5000)
- Enable via `--start-server` flag or Settings checkbox
- Always check for empty buffer before `json.loads()` to avoid JSONDecodeError

### CI/Testing

- Timing test thresholds should be generous (e.g., 750ms instead of 500ms) to account for CI runner variability
- Tests are in `software/tests/`
- Run tests with: `pytest software/tests/`

### Pydantic Field() Default Gotcha

When using `Field(None, ...)` as a default parameter in `@schema_method` decorated functions, calling the method directly (not through JSON parsing) results in a `FieldInfo` object, not `None`.

```python
# BUG: FieldInfo is not None!
if param is not None:
    do_something(param)

# FIX: Check for actual type
if isinstance(param, bool):
    do_something(param)
```

### Runtime Settings Access Pattern

For MCP-modifiable settings in `control._def`:

- **Use:** `import control._def` then `control._def.VARIABLE` (reads current runtime value)
- **Don't use:** `from control._def import VARIABLE` (creates stale local binding that won't see MCP updates)

### Subprocess Settings Behavior

Settings read by `JobRunner` subprocess (e.g., `SIMULATED_DISK_IO_SPEED_MB_S`) take effect on **next acquisition** since each acquisition starts a fresh subprocess. Only UI elements (warning banners, dialogs) require app restart.

### Cross-Process Backpressure Tracking

When tracking state across main process and subprocess (e.g., job counts, byte counts):

- Use `multiprocessing.Value` for counters (thread-safe with `.get_lock()`)
- Use `multiprocessing.Event` to signal capacity available
- Increment counters BEFORE `queue.put()`, with rollback on failure
- Decrement counters in `finally` block after job completion

### Backpressure Byte Tracking

Backpressure tracks bytes in the **main process queue**, not subprocess memory:

- Bytes are incremented when a job is dispatched (enters queue)
- Bytes are decremented when a job completes (leaves queue and is processed)
- For `DownsampledViewJob`, bytes are released immediately on job completion (not when well completes), because the image data moves to subprocess memory when processed
- Signal capacity event for ALL job completions

### Rollback Error Handling Pattern

When rollback operations can fail, handle separately to preserve original error:

```python
try:
    do_operation()
except Exception as original_exc:
    try:
        rollback()
    except Exception as rollback_exc:
        log.error(f"Rollback failed: {rollback_exc}. Original: {original_exc}")
    raise original_exc
```

## Development Features

### Simulated Disk I/O

For development without SSD wear. Enable in **Settings > Preferences > Advanced > Development Settings**.

- **Enable/disable** - Requires app restart (for warning banner/dialog)
- **Speed/compression** - Takes effect on next acquisition (no restart needed)
- Images are encoded to memory (exercises RAM/CPU) but not saved to disk
- Plate view continues working (images generated for display, not saved)

## Running the Software

### Launch in Simulation Mode (Claude Code)

```bash
cd "/Users/hongquan/Cephla Dropbox/Hongquan Li/Github/AI/Squid-Claude2/software"
source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid && python3 main_hcs.py --simulation
```

### Command Line Options

- `--simulation` - Run with simulated hardware (no physical microscope needed)
- `--live-only` - Run with only the live viewer
- `--verbose` - Enable DEBUG level logging
- `--start-server` - Auto-start the MCP control server on port 5050

## Directory Structure

- `software/` - Main application code
  - `control/` - Core control logic, widgets, and server
  - `scripts/` - Automation scripts (e.g., `run_acquisition.py`)
  - `docs/` - Documentation
  - `tests/` - Test suite
