# Port Plan: Alignment Button for Sample Registration (47e7aff7)

## Overview

**Upstream commit:** 47e7aff7 - feat: Add alignment button for sample registration with previous acquisitions (#448)
**Lines changed:** ~433
**Priority:** Medium-Low - specialized workflow feature

### What It Does

1. **Align Button**: Visible in navigation viewer when using napari for live view
2. **Reference Image Loading**: Load image from previous acquisition as overlay
3. **Manual Alignment**: User moves stage to align current sample with reference
4. **Offset Calculation**: Calculate and apply X/Y offset to scan coordinates
5. **Offset Application**: Offset applied during acquisition to all movements

### Workflow

1. Click "Align" → select past acquisition folder
2. Stage moves to middle FOV position, reference image appears as translucent overlay
3. Manually align sample using stage controls
4. Click "Confirm Offset" → offset calculated and stored
5. During acquisition: offset automatically applied to all coordinates
6. Click "Clear Offset" → offset removed

---

## Architecture Mapping

### Upstream Files → arch_v2 Locations

| Upstream | arch_v2 Location | Notes |
|----------|------------------|-------|
| `control/widgets.py` (AlignmentWidget) | `ui/widgets/navigation/alignment_widget.py` | New widget file |
| `control/core/core.py` (NavigationViewer) | `ui/widgets/navigation/navigation_viewer.py` | Add alignment support |
| `control/core/multi_point_controller.py` | `backend/controllers/multipoint_controller.py` | Add alignment_widget ref |
| `control/core/multi_point_worker.py` | `backend/controllers/multipoint_worker.py` | Apply offset in moves |
| `control/gui_hcs.py` | `ui/main_window.py` | Wire up alignment |

### Key Differences

1. **Widget Location**: arch_v2 organizes widgets by domain, so AlignmentWidget goes in `navigation/`
2. **Signal Pattern**: arch_v2 uses EventBus more than direct signals - evaluate which pattern fits
3. **Napari Integration**: Verify arch_v2's napari setup supports layer management

---

## Implementation Checklist

### Phase 1: Create AlignmentWidget
**File:** `software/src/squid/ui/widgets/navigation/alignment_widget.py`

- [ ] Create `AlignmentWidget(QWidget)`:
  - State constants: `STATE_ALIGN`, `STATE_CONFIRM`, `STATE_CLEAR`
  - Reference layer name constant

- [ ] Add signals:
  - `signal_move_to_position = Signal(float, float)` - request stage move
  - `signal_offset_set = Signal(float, float)` - offset confirmed
  - `signal_offset_cleared = Signal()` - offset cleared
  - `signal_request_current_position = Signal()` - ask for stage position

- [ ] Add state attributes:
  - `_offset_x_mm, _offset_y_mm: float`
  - `_has_offset: bool`
  - `_reference_fov_position: Optional[Tuple[float, float]]`
  - `_current_folder: Optional[str]`
  - `_original_live_opacity, _original_live_blending`

- [ ] Implement UI:
  - Single button that changes text/action based on state
  - "Align" → "Confirm Offset" → "Clear Offset"

- [ ] Implement `_on_align_clicked()`:
  - Open folder dialog to select acquisition folder
  - Find middle FOV position from coordinates
  - Move stage to position
  - Load reference image as napari layer
  - Update state to CONFIRM

- [ ] Implement `_on_confirm_clicked()`:
  - Request current stage position
  - Calculate offset (current - reference)
  - Store offset, emit signal
  - Update state to CLEAR

- [ ] Implement `_on_clear_clicked()`:
  - Clear offset values
  - Remove reference layer
  - Emit signal
  - Update state to ALIGN

- [ ] Implement `set_current_position(x, y)`:
  - Slot for receiving current position from main window

- [ ] Implement `apply_offset(x, y) -> Tuple[float, float]`:
  - Return (x + offset_x, y + offset_y)

- [ ] Property `has_offset: bool`

- [ ] Implement `enable()` / `disable()`:
  - Show/hide widget appropriately

- [ ] Add exports to `ui/widgets/navigation/__init__.py`

### Phase 2: Update NavigationViewer
**File:** `software/src/squid/ui/widgets/navigation/navigation_viewer.py`

- [ ] Add `alignment_widget: Optional[AlignmentWidget] = None`

- [ ] Implement `set_alignment_widget(widget)`:
  - Store reference
  - Set as child of graphics_widget
  - Position button

- [ ] Update `_position_button()`:
  - Position clear button (rightmost)
  - Position alignment widget (left of clear) if present

- [ ] Update `resizeEvent()`:
  - Reposition alignment widget on resize

### Phase 3: Update MultiPointController
**File:** `software/src/squid/backend/controllers/multipoint_controller.py`

- [ ] Add `_alignment_widget = None` attribute

- [ ] Add `set_alignment_widget(widget)` method

- [ ] Pass alignment_widget to MultiPointWorker in `run()`:
  ```python
  self.multiPointWorker = MultiPointWorker(
      ...,
      alignment_widget=self._alignment_widget,
  )
  ```

### Phase 4: Update MultiPointWorker
**File:** `software/src/squid/backend/controllers/multipoint_worker.py`

- [ ] Add `_alignment_widget` parameter to `__init__`

- [ ] Update `move_to_coordinate(coordinate_mm, region_id, fov)`:
  ```python
  x_mm = coordinate_mm[0]
  y_mm = coordinate_mm[1]

  if self._alignment_widget is not None and self._alignment_widget.has_offset:
      x_mm, y_mm = self._alignment_widget.apply_offset(x_mm, y_mm)
      self._log.info(f"moving to ({x_mm:.4f}, {y_mm:.4f}) [offset applied]")
  else:
      self._log.info(f"moving to coordinate {coordinate_mm}")

  self.stage.move_x_to(x_mm)
  # ... rest of move logic
  ```

### Phase 5: Integrate in Main Window
**File:** `software/src/squid/ui/main_window.py`

- [ ] Add `alignmentWidget: Optional[AlignmentWidget] = None`

- [ ] Add `_setup_alignment_widget()`:
  - Only create if using napari for live view
  - Create AlignmentWidget with napari viewer
  - Connect signals:
    - `signal_move_to_position` → `_alignment_move_to`
    - `signal_request_current_position` → `_alignment_provide_position`
    - `signal_offset_set` → log info
    - `signal_offset_cleared` → log info
  - Set on controller and navigation viewer

- [ ] Add `_alignment_move_to(x_mm, y_mm)`:
  - Move stage to position

- [ ] Add `_alignment_provide_position()`:
  - Get stage position
  - Call `alignmentWidget.set_current_position()`

- [ ] Call `_setup_alignment_widget()` after napari setup

- [ ] In `onStartLive()`:
  - Enable alignment widget

### Phase 6: Write Tests
**File:** `software/tests/unit/ui/widgets/test_alignment_widget.py`

- [ ] Test state transitions
- [ ] Test offset calculation
- [ ] Test offset application
- [ ] Test clear functionality
- [ ] Test enable/disable

---

## Coordinate System

The offset is applied as:
```
actual_x = target_x + offset_x
actual_y = target_y + offset_y
```

Where:
- `target_x/y`: Coordinates from scan definition
- `offset_x/y`: Calculated from (current_position - reference_position) after manual alignment
- `actual_x/y`: Where stage actually moves

---

## UI State Machine

```
   ┌─────────────┐
   │   ALIGN     │ (Initial state)
   │ "Align"     │
   └──────┬──────┘
          │ Click + select folder
          ▼
   ┌─────────────┐
   │   CONFIRM   │ (Reference loaded)
   │"Confirm     │
   │ Offset"     │
   └──────┬──────┘
          │ Click
          ▼
   ┌─────────────┐
   │   CLEAR     │ (Offset active)
   │"Clear       │
   │ Offset"     │
   └──────┬──────┘
          │ Click
          └──────────► Back to ALIGN
```

---

## Testing Strategy

1. **Unit tests**: State machine, offset calculation
2. **Integration tests**: Controller/worker offset application
3. **Manual tests**:
   - Load reference image from previous acquisition
   - Verify stage moves to reference position
   - Verify offset calculated correctly after manual adjustment
   - Verify offset applied during acquisition

---

## Dependencies

- napari (for reference image overlay)
- No new external dependencies

## Risk Assessment

- **Low-Medium risk**: Self-contained feature
- **UI complexity**: State machine requires careful implementation
- **napari integration**: Layer management needs testing
