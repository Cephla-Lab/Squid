# TCP YAML Command

**Our Commit:** 47b385e0
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| f8c05d0d | feat: Add run_acquisition_from_yaml TCP command and CLI |

## Summary

Adds a TCP control server that accepts JSON commands for headless automation. The main command is `run_acquisition_from_yaml` which loads acquisition parameters from a YAML file and starts the acquisition.

## Files Created/Modified

### Created
- `backend/services/tcp_control_server.py` (668 lines) - Full TCP server
- `tools/run_acquisition.py` (294 lines) - CLI script
- `tests/unit/backend/services/test_tcp_control_server.py` (382 lines) - **15 tests**

### Modified
- `main_hcs.py` - Added `--start-server` and `--server-port` CLI flags

## Architecture Adaptation (SIGNIFICANT)

**Upstream approach:** Direct widget manipulation via QTimer callbacks
**arch_v2 approach:** EventBus commands for complete decoupling

### Commands Used
```python
ClearScanCoordinatesCommand()
LoadScanCoordinatesCommand(coordinates)
SetAcquisitionParametersCommand(params)
SetAcquisitionChannelsCommand(channels)
StartAcquisitionCommand()
```

### Event Subscriptions
```python
@handles(AcquisitionStarted)
def _on_acquisition_started(self, event): ...

@handles(AcquisitionWorkerFinished)
def _on_acquisition_finished(self, event): ...
```

## CLI Usage

```bash
# Start server
python main_hcs.py --simulation --start-server --server-port 5050

# Run acquisition from CLI
python tools/run_acquisition.py --yaml path/to/acquisition.yaml
```

## JSON Protocol

```json
{"command": "run_acquisition_from_yaml", "yaml_path": "/path/to/file.yaml"}
{"command": "get_status"}
{"command": "stop_acquisition"}
```

## Tests

**File:** `tests/unit/backend/services/test_tcp_control_server.py`
**Count:** 15 tests

Covers:
- Command parsing
- YAML loading
- Hardware validation
- Event publishing
- Error handling

Note: Upstream had 19 tests; 4 were GUI-specific and not applicable to EventBus architecture.

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed (EventBus commands)
- [x] Thread safety verified (RLock)
- [x] Tests added (15 tests)
