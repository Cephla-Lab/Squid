# Acquisition YAML Save/Load

**Our Commit:** 8e032023
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 88db4da8 | feat: Save and load acquisition parameters via YAML |

## Summary

Adds ability to save acquisition parameters to YAML and restore them via drag-drop. YAML files are automatically saved with each acquisition and can be dragged onto the acquisition widget to restore settings.

## Files Created/Modified

### Created
- `backend/io/acquisition_yaml.py` (429 lines) - YAML parsing, validation, serialization
- `ui/widgets/acquisition/yaml_drop_mixin.py` (257 lines) - Drag-drop functionality
- `tests/unit/backend/io/test_acquisition_yaml.py` (351 lines) - **19 tests**

### Modified
- `backend/controllers/multipoint/multi_point_controller.py` - Auto-save on acquisition
- `ui/widgets/acquisition/flexible_multipoint.py` - Drag-drop support
- `ui/widgets/acquisition/wellplate_multipoint.py` - Drag-drop support

## Key Features

### YAML Structure
```yaml
hardware:
  objective: "20x"
  camera_binning: 1
  use_piezo: false

acquisition:
  mode: "multipoint"
  z_stack_enabled: true
  z_range_um: 10.0
  z_step_um: 0.5

channels:
  - name: "BF LED matrix full"
    exposure_ms: 10.0
    analog_gain: 1.0

coordinates:
  - region_id: "A1"
    x_mm: 10.5
    y_mm: 20.3
```

### Hardware Validation
- Validates objective matches current
- Validates binning matches current
- Shows warning dialog for mismatches

## Tests

**File:** `tests/unit/backend/io/test_acquisition_yaml.py`
**Count:** 19 tests

Covers:
- YAML parsing for all field types
- Hardware validation (objective/binning mismatch)
- Serialization with Enums, numpy arrays, dataclasses
- Error handling for invalid YAML

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Backend handles I/O, UI handles drag-drop
- [x] Tests added (19 tests)
