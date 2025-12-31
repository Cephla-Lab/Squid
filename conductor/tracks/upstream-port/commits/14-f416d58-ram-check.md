# PR 14: RAM Usage Check for Mosaic

**Upstream Commit:** `f416d58` - feat: Add RAM usage check for mosaic view before acquisition (#382)
**Priority:** Medium
**Effort:** Medium (+387 lines)

## Summary

Check available RAM before starting mosaic acquisition. Warn user if insufficient RAM to prevent crashes.

## Upstream Changes

**Files Modified:**
- `software/control/core/multi_point_controller.py` (+74 lines)
- `software/control/gui_hcs.py` (+9 lines)
- `software/control/widgets.py` (+72 lines)
- `software/setup_22.04.sh` (+2 lines)

**Files Created:**
- `software/tests/control/test_MultiPointController.py` (+91 lines)
- `software/tests/control/test_widgets.py` (+148 lines)

## arch_v2 Targets

| Upstream File | arch_v2 Location |
|---------------|------------------|
| `multi_point_controller.py` | `src/squid/backend/controllers/multipoint/multi_point_controller.py` |
| `gui_hcs.py` | `src/squid/ui/main_window.py` |
| `widgets.py` | `src/squid/ui/widgets/display/napari_mosaic.py` |
| `test_MultiPointController.py` | `tests/unit/squid/backend/controllers/` |
| `test_widgets.py` | `tests/unit/squid/ui/widgets/` |

## Implementation Checklist

### Step 1: Add psutil Dependency
- [x] Check if psutil is in requirements
- [x] Add if not present: `pip install psutil`
- [x] Update pyproject.toml or requirements.txt

### Step 2: Implement RAM Estimation
- [x] Add RAM estimation function to MultiPointController
- [x] Calculate expected memory usage based on:
  - Number of tiles
  - Image dimensions
  - Data type (8-bit, 16-bit)
  - Number of channels

### Step 3: Add RAM Check Before Acquisition
- [x] Get available system RAM
- [x] Compare with estimated usage
- [x] Emit warning event if insufficient

### Step 4: Add Warning Dialog
- [x] Create RAM warning dialog in UI
- [x] Show estimated vs available RAM
- [x] Allow user to proceed or cancel

### Step 5: Port Tests (REQUIRED)

**Test Files to Port:**
| Upstream Test | arch_v2 Location | Lines |
|---------------|------------------|-------|
| `tests/control/test_MultiPointController.py` | `tests/unit/control/core/test_multi_point_controller_ram_estimate.py` | +91 |
| `tests/control/test_widgets.py` | `tests/unit/control/widgets/test_ram_check.py` | +148 |

- [x] Create test files in arch_v2 test structure
- [x] Port all test cases from `test_MultiPointController.py`
- [x] Port RAM-related tests from `test_widgets.py`
- [x] Mock psutil for consistent cross-platform testing
- [x] Ensure tests use arch_v2 imports
- [x] Run tests and verify they pass

### Step 6: Integration Testing
- [ ] Test with various mosaic sizes
- [ ] Verify warning appears when RAM low
- [ ] Test on system with limited RAM

## Key Implementation

### RAM Estimation
```python
import psutil

def estimate_mosaic_memory(self, num_tiles: int, tile_shape: tuple, dtype=np.uint16) -> int:
    """Estimate memory required for mosaic in bytes."""
    bytes_per_pixel = np.dtype(dtype).itemsize
    tile_bytes = tile_shape[0] * tile_shape[1] * bytes_per_pixel
    total_bytes = num_tiles * tile_bytes
    # Add overhead for napari layers, processing, etc.
    return int(total_bytes * 1.5)

def check_available_ram(self) -> int:
    """Get available system RAM in bytes."""
    return psutil.virtual_memory().available
```

### Warning Dialog
```python
class RAMWarningDialog(QDialog):
    def __init__(self, required_gb: float, available_gb: float):
        # Show warning with proceed/cancel options
        pass
```

## Notes

- Important for preventing OOM crashes on large mosaics
- psutil is cross-platform (Linux, macOS, Windows)
- Consider adding RAM monitoring during acquisition too
