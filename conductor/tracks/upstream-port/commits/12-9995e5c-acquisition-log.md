# PR 12: Save Log in Acquisition Folder

**Upstream Commit:** `9995e5c` - feat: save log in acquisition folder (#374)
**Priority:** Medium
**Effort:** Medium (+250 lines)
**Status:** COMPLETED (2025-12-29)

## Summary

Save acquisition log files in the acquisition folder for each run. Enhances debugging and audit trail.

## Upstream Changes

**Files Modified:**
- `software/control/_def.py` (+4 lines)
- `software/control/core/multi_point_controller.py` (+196 lines, major changes)
- `software/squid/logging.py` (+50 lines)

## arch_v2 Targets

| Upstream File | arch_v2 Location |
|---------------|------------------|
| `_def.py` | `src/_def.py` |
| `multi_point_controller.py` | `src/squid/backend/controllers/multipoint/multi_point_controller.py` |
| `squid/logging.py` | `src/squid/core/logging.py` |

## Implementation Checklist

### Step 1: Review Existing Logging
- [x] Check arch_v2 `core/logging.py` current implementation
- [x] Note: arch_v2 already has `add_file_logging()` function
- [x] Determine what's new vs overlapping

### Step 2: Update Logging Infrastructure
- [x] Review upstream logging.py changes
- [x] Add any new functionality not in arch_v2
- [x] Added `add_file_handler()` function - returns handler for dynamic management
- [x] Added `remove_handler()` function - removes and closes handler safely

### Step 3: Integrate with MultiPointController
- [x] Add acquisition folder log creation
- [x] Create log file when acquisition starts (`_start_per_acquisition_log()`)
- [x] Stop logging when acquisition completes (`_stop_per_acquisition_log()`)
- [x] Include experiment metadata in log (uses same logging format)

### Step 4: Update Definitions
- [x] Add `ENABLE_PER_ACQUISITION_LOG` constant to `_def.py`
- [x] Log file naming: `<base_path>/<experiment_ID>/acquisition.log`

### Step 5: Testing
- [x] Unit tests for `add_file_handler()` function
- [x] Unit tests for `remove_handler()` function
- [x] Unit tests for duplicate handler handling
- [x] Unit tests for safe double-removal

## Log File Structure

```
acquisition_2025-01-15_14-30-00/
├── images/
│   └── ...
├── coordinates.csv
└── acquisition.log  # NEW: Log file
```

## Key Implementation

### Logging Setup
```python
def setup_acquisition_logging(acquisition_folder: Path):
    """Set up logging for a specific acquisition."""
    log_file = acquisition_folder / "acquisition.log"
    squid.core.logging.add_file_logging(str(log_file))
```

### Controller Integration
```python
def _start_acquisition(self):
    # Create acquisition folder
    acq_folder = self._create_acquisition_folder()

    # Set up acquisition-specific logging
    setup_acquisition_logging(acq_folder)

    # Log acquisition start
    self._log.info(f"Starting acquisition: {acq_folder}")
```

## Notes

- Valuable for debugging failed acquisitions
- Log should include: timestamps, parameters, errors
- arch_v2 logging already has infrastructure - extend it
