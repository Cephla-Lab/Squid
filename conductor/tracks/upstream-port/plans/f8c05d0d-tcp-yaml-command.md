# Port Plan: TCP Command for YAML Acquisitions (f8c05d0d)

## Overview

**Upstream commit:** f8c05d0d - feat: Add run_acquisition_from_yaml TCP command and CLI script (#422)
**Lines changed:** ~1508
**Priority:** Medium - enables headless automation
**Dependency:** Requires 88db4da8 (acquisition YAML save/load) to be ported first

### What It Does

1. **TCP Command**: `run_acquisition_from_yaml` loads and executes acquisitions from saved YAML files
2. **CLI Flag**: `--start-server` to auto-start TCP server on launch
3. **Automation Script**: `scripts/run_acquisition.py` for headless acquisition control
4. **GUI State Sync**: Properly updates GUI when acquisition starts via TCP

## Architecture Mapping

### Upstream Files → arch_v2 Locations

| Upstream | arch_v2 Location | Notes |
|----------|------------------|-------|
| `control/microscope_control_server.py` | `backend/services/tcp_server.py` | Check if exists, may need new |
| `main_hcs.py` (`--start-server`) | `main_hcs.py` | Add CLI flag |
| `scripts/run_acquisition.py` | `tools/run_acquisition.py` | Automation script |
| `docs/automation.md` | `docs/automation.md` | Documentation |

### Key Differences to Handle

1. **TCP Server Architecture**: Need to verify arch_v2 has TCP server or create one
2. **Thread Safety**: GUI updates must use `QTimer.singleShot` + `threading.Event` pattern
3. **Widget Access**: TCP handler needs safe access to multipoint widgets

---

## Pre-requisite Check

Before starting this port, verify:
- [ ] 88db4da8 (acquisition YAML) is ported
- [ ] arch_v2 has TCP server infrastructure (check `backend/services/`)

---

## Implementation Checklist

### Phase 1: Investigate TCP Server Infrastructure
**Research needed first**

- [ ] Check if arch_v2 has `microscope_control_server.py` or equivalent
- [ ] Understand arch_v2's TCP command pattern
- [ ] Document existing TCP commands for reference

### Phase 2: Add TCP Command
**File:** `software/src/squid/backend/services/tcp_server.py` (or equivalent)

- [ ] Add `AcquisitionResult` TypedDict:
  ```python
  class AcquisitionResult(TypedDict):
      success: bool
      experiment_id: str
      save_path: str
      error: Optional[str]
  ```

- [ ] Add `_cmd_run_acquisition_from_yaml(self, yaml_path, **overrides)`:
  - Load and parse YAML file
  - Validate widget_type is "wellplate" (reject flexible with clear error)
  - Get widget reference via `_get_widget_for_type()`
  - Validate hardware (objective, binning)
  - Validate channels exist
  - Apply parameter overrides (experiment_id, base_path, wells)
  - Update GUI state thread-safely
  - Configure regions from YAML
  - Start acquisition
  - Return AcquisitionResult

- [ ] Add helper methods:
  - `_get_widget_for_type(widget_type)` - lookup widget by type
  - `_get_z_from_center(center)` - extract Z with fallback
  - `_update_gui_from_yaml(widget, yaml_data)` - thread-safe GUI update with wait
  - `_set_gui_acquisition_state(widget, running)` - set state with wait
  - `_validate_channels(yaml_data, widget)` - verify channels exist
  - `_configure_regions_from_yaml(yaml_data, widget)` - setup regions
  - `_configure_controller_from_yaml(yaml_data, controller)` - apply settings

### Phase 3: Add Widget Signal Support
**File:** `software/src/squid/ui/widgets/acquisition/wellplate_multipoint.py`

- [ ] Add `signal_set_acquisition_running = Signal(bool)` signal
- [ ] Add `set_acquisition_running_state(running: bool)` slot:
  - Handle acquisition state from TCP command
  - Call internal `_set_ui_acquisition_running(running)`
- [ ] Refactor to share `_set_ui_acquisition_running(running)` helper

### Phase 4: Add CLI Flag
**File:** `software/main_hcs.py`

- [ ] Add `--start-server` argument to argparse
- [ ] Auto-start TCP server when flag is present
- [ ] Document in help text

### Phase 5: Create Automation Script
**File:** `software/tools/run_acquisition.py`

- [ ] Add command-line interface:
  - `yaml_path` - required positional argument
  - `--host` - TCP server host (default: localhost)
  - `--port` - TCP server port (default: configured port)
  - `--dry-run` - validate without running
  - `--base-path` - override save location
  - `--experiment-id` - override experiment ID
  - `--wells` - override well selection

- [ ] Implement `send_command(host, port, command, **params)`:
  - Connect to TCP server
  - Send JSON command
  - Handle empty response (ConnectionError)
  - Parse and return result

- [ ] Implement `run_acquisition(yaml_path, **options)`:
  - Call `run_acquisition_from_yaml` command
  - Monitor progress if desired
  - Return result with proper exit codes

- [ ] Add error handling:
  - Connection errors
  - Validation errors
  - Acquisition errors
  - Consecutive error tracking for monitor loop

### Phase 6: Update YAML Loader
**File:** `software/src/squid/backend/io/acquisition_yaml.py`

- [ ] Add `use_piezo: bool = False` field to `AcquisitionYAMLData`

### Phase 7: Write Tests
**File:** `software/tests/unit/backend/services/test_tcp_yaml_command.py`

- [ ] Test YAML parsing and validation
- [ ] Test hardware mismatch detection
- [ ] Test parameter overrides
- [ ] Test FlexibleMultiPoint rejection
- [ ] Test helper method functionality

### Phase 8: Documentation
**File:** `software/docs/automation.md`

- [ ] Document `run_acquisition_from_yaml` command
- [ ] Document CLI script usage
- [ ] Document parameter override options
- [ ] Add example workflows

---

## TCP Command Interface

### Request
```json
{
  "command": "run_acquisition_from_yaml",
  "yaml_path": "/path/to/acquisition.yaml",
  "experiment_id": "optional_override",
  "base_path": "/optional/save/path",
  "wells": ["A1", "A2", "B1"]
}
```

### Response (Success)
```json
{
  "success": true,
  "experiment_id": "experiment_2026-01-12_14-30-00",
  "save_path": "/data/experiment_2026-01-12_14-30-00"
}
```

### Response (Error)
```json
{
  "success": false,
  "error": "Hardware mismatch: objective 20x expected, 10x current"
}
```

---

## Thread Safety Pattern

GUI updates from TCP thread must use:

```python
def _update_gui_from_yaml(self, widget, yaml_data):
    event = threading.Event()

    def do_update():
        try:
            widget._apply_yaml_settings(yaml_data)
        finally:
            event.set()

    QTimer.singleShot(0, do_update)
    event.wait(timeout=5.0)  # Wait for GUI thread
```

---

## Testing Strategy

1. **Unit tests**: Command parsing, validation, helper methods
2. **Integration tests**: Mock TCP server and widget interaction
3. **Manual tests**:
   - Launch with `--start-server`
   - Run acquisition via script
   - Verify GUI state updates
   - Test error handling

---

## Dependencies

- Requires 88db4da8 (acquisition YAML) ported first
- TCP server infrastructure (verify or create)

## Risk Assessment

- **Medium risk**: Thread safety is critical for GUI updates
- **Complexity**: TCP server integration depends on existing arch_v2 infrastructure
