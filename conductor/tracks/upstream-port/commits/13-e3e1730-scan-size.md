# PR 13: Scan Size Consistency

**Upstream Commit:** `e3e1730` - fix: Preserve scan size consistency in Select Wells mode (#393)
**Priority:** Medium
**Effort:** Medium (+149 lines new file + widget changes + tests)
**Status:** COMPLETED (2025-12-29)

## Summary

New geometry utilities to ensure scan size remains consistent when switching between well selection modes.

## Upstream Changes

**Files Created:**
- `software/control/core/geometry_utils.py` (NEW, +149 lines)

**Files Modified:**
- `software/control/gui_hcs.py` (+4 lines)
- `software/control/widgets.py` (refactored)
- `software/tests/control/test_scan_size_consistency.py` (NEW, +118 lines)

## arch_v2 Targets

| Upstream File | arch_v2 Location |
|---------------|------------------|
| `geometry_utils.py` | `src/squid/core/utils/geometry_utils.py` (pure functions) |
| `gui_hcs.py` | N/A (handled via EventBus in arch_v2) |
| `widgets.py` | `src/squid/ui/widgets/acquisition/wellplate_multipoint.py` |
| `test_scan_size_consistency.py` | `tests/unit/squid/core/utils/test_geometry_utils.py` |

## Implementation Checklist

### Step 1: Create Geometry Utilities
- [x] Create `src/squid/core/utils/geometry_utils.py`
- [x] Port geometry calculation functions
- [x] These should be pure functions (no dependencies)

### Step 2: Integrate with Wellplate Widgets
- [x] Import geometry_utils in wellplate widgets
- [x] Apply scan size consistency logic
- [x] Maintain size when switching modes

### Step 3: Update Main Window
- [x] N/A - arch_v2 uses EventBus for objective changes, already connected via `_on_objective_changed`

### Step 4: Port Tests (REQUIRED)

**Test Files to Port:**
| Upstream Test | arch_v2 Location | Lines |
|---------------|------------------|-------|
| `tests/control/test_scan_size_consistency.py` | `tests/unit/squid/core/utils/test_geometry_utils.py` | +118 |

- [x] Create test file `test_geometry_utils.py`
- [x] Port all test cases for scan size calculations
- [x] Port mode switching tests
- [x] Update imports for arch_v2 structure
- [x] Run tests and verify they pass (11 passed)

### Step 5: Testing
- [x] Verify geometry functions work correctly
- [x] Test coverage capped at 100%
- [x] Test different plate formats (round/square wells)

## geometry_utils.py Functions

Implemented functions:
```python
def get_effective_well_size(well_size_mm, fov_size_mm, shape, is_round_well=True):
    """Calculate the default scan size for a well based on shape."""
    pass

def get_tile_positions(scan_size_mm, fov_size_mm, overlap_percent, shape):
    """Get tile center positions for a scan pattern."""
    pass

def calculate_well_coverage(scan_size_mm, fov_size_mm, overlap_percent, shape, well_size_mm, is_round_well=True):
    """Calculate what fraction of the well is covered by FOV tiles."""
    pass
```

## arch_v2 Implementation Notes

- Pure utility functions placed in `core/utils/geometry_utils.py`
- Coverage is now read-only in "Select Wells" mode (derived from scan_size, FOV, overlap)
- `scan_size` is the source of truth, not coverage
- Removed `update_scan_size_from_coverage()` - coverage is always derived
- Added `on_shape_changed()` method to handle shape changes
- Added `handle_objective_change()` method to recalculate coverage when objective changes
- The `_on_objective_changed` event handler now calls `handle_objective_change()` instead of directly calling `update_coordinates()`

## Files Modified

1. `src/squid/core/utils/geometry_utils.py` (NEW)
2. `src/squid/core/utils/__init__.py` (updated exports)
3. `src/squid/ui/widgets/acquisition/wellplate_multipoint.py` (refactored)
4. `tests/unit/squid/core/utils/test_geometry_utils.py` (NEW)
5. `tests/unit/squid/core/utils/__init__.py` (NEW)
