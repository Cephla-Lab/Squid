# Alignment Button for Sample Registration

**Our Commit:** b4aa7255
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 47e7aff7 | feat: Add alignment button for sample registration with previous acquisitions |

## Summary

Adds an alignment widget that allows users to register the current sample position with a previous acquisition. Users can:
1. Load a previous acquisition's coordinates
2. Navigate to the reference position
3. Physically align the sample
4. Calculate and apply an offset to all future positions

## Files Created/Modified

### Created
- `ui/widgets/stage/alignment_widget.py` (384 lines) - Main widget
- `tests/unit/ui/widgets/stage/test_alignment_widget.py` (397 lines) - **24 tests**

### Modified
- `backend/controllers/multipoint/multi_point_controller.py` - Alignment widget reference
- `backend/controllers/multipoint/multi_point_worker.py` - Offset application in moves
- `ui/main_window.py` - Widget integration

## State Machine

```
IDLE ─────────────────────────────────────────┐
  │                                            │
  │ [Load Acquisition]                         │
  ▼                                            │
ALIGN ───────────────────────────────────────► │
  │                                            │
  │ [Set Reference]                            │
  │   - Store reference position               │
  │   - Create napari overlay                  │
  ▼                                            │
CONFIRM ─────────────────────────────────────► │
  │                                            │
  │ [Apply Offset]                             │
  │   - Calculate offset                       │
  │   - Emit signal_offset_set                 │
  ▼                                            │
OFFSET_ACTIVE ───[Clear]──────────────────────►│
                                               │
◄──────────────────────────────────────────────┘
```

## Key Features

- **Reference Layer:** Magenta colormap overlay in napari showing reference position
- **Offset Calculation:** `current_position - reference_position`
- **Signal-based:** Uses Qt signals for decoupled communication

## Offset Application

In `multi_point_worker.py`:
```python
def move_to_coordinate(self, x_mm, y_mm, z_mm):
    if self._alignment_widget:
        offset = self._alignment_widget.get_offset()
        if offset:
            x_mm += offset.x_mm
            y_mm += offset.y_mm
```

## Tests

**File:** `tests/unit/ui/widgets/stage/test_alignment_widget.py`
**Count:** 24 tests

Covers:
- State machine transitions
- Napari layer management
- Signal emissions
- Offset calculation
- Acquisition folder parsing

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed (Signals for decoupling)
- [x] State machine correctly implemented
- [x] Tests added (24 tests)
