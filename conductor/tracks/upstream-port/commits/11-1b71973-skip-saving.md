# PR 11: Skip Saving Checkbox

**Upstream Commit:** `1b71973` - feat: Add Skip Saving checkbox to multipoint acquisition widgets (#383)
**Priority:** Medium
**Effort:** Small (+26 lines across 4 files)

## Summary

Add a "Skip Saving" checkbox to multipoint acquisition widgets. When enabled, images are acquired but not saved to disk (useful for preview/testing).

## Upstream Changes

**Files Modified:**
- `software/control/core/multi_point_controller.py` (+5 lines)
- `software/control/core/multi_point_utils.py` (+1 line)
- `software/control/core/multi_point_worker.py` (+3 lines)
- `software/control/widgets.py` (+20 lines)

## arch_v2 Targets

| Upstream File | arch_v2 Location |
|---------------|------------------|
| `multi_point_controller.py` | `src/squid/backend/controllers/multipoint/multi_point_controller.py` |
| `multi_point_utils.py` | `src/squid/backend/controllers/multipoint/multi_point_utils.py` |
| `multi_point_worker.py` | `src/squid/backend/controllers/multipoint/multi_point_worker.py` |
| `widgets.py` | `src/squid/ui/widgets/acquisition/wellplate_multipoint.py` |

## Implementation Checklist

### Step 1: Update Data Model
- [x] Add `skip_saving: bool = False` to acquisition parameters dataclass
- [x] Located in `multi_point_utils.py`

### Step 2: Add UI Checkbox
- [x] Add checkbox to WellplateMultiPointWidget
- [x] Connect to acquisition parameters
- [x] Emit command when changed

### Step 3: Pass Through Controller
- [x] Receive skip_saving in acquisition command
- [x] Pass to worker initialization

### Step 4: Implement Skip Logic in Worker
- [x] Check skip_saving flag before dispatching SaveImageJob
- [x] Still process images for display
- [x] Skip file I/O when flag is set

### Step 5: Testing
- [x] Enable skip saving checkbox
- [ ] Run acquisition (manual testing required)
- [ ] Verify no files are saved (manual testing required)
- [ ] Verify images still display live (manual testing required)

## Key Changes

### multi_point_utils.py
```python
@dataclass
class AcquisitionParameters:
    # ... existing fields ...
    skip_saving: bool = False
```

### multi_point_worker.py
```python
def _process_frame(self, frame):
    # ... processing ...
    if not self._skip_saving:
        self._dispatch_save_job(frame)
    # Still emit for display
    self._emit_frame_event(frame)
```

### Widget
```python
self.skip_saving_checkbox = QCheckBox("Skip Saving")
self.skip_saving_checkbox.stateChanged.connect(self._on_skip_saving_changed)
```

## Notes

- Useful for previewing acquisition without disk I/O
- Can speed up testing of acquisition parameters
- Should still show live view during acquisition
