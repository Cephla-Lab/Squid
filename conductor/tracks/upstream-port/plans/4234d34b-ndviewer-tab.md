# Port Plan: NDViewer Tab with Live Viewing (4234d34b)

## Overview

**Upstream commit:** 4234d34b - feat: NDViewer tab with live viewing and plate navigation (#428)
**Lines changed:** ~680 + git submodule
**Priority:** Medium - nice-to-have viewer integration

### What It Does

1. **Embedded NDViewer**: Lightweight viewer tab in main GUI for browsing acquisitions
2. **Auto-load**: Automatically points to current acquisition when it starts
3. **Plate View Navigation**: Double-click in plate view → navigate NDViewer to that FOV
4. **Git Submodule**: Uses ndviewer_light as external dependency

## Architecture Decisions Needed

### Key Questions

1. **Submodule vs pip package**: Should we use git submodule like upstream, or check if ndviewer_light is pip-installable?
2. **Location**: Where should submodule live in arch_v2? (`software/src/squid/vendor/ndviewer_light`?)
3. **Integration point**: How does this fit with arch_v2's napari-based viewing?

### Recommendation

Consider:
- If ndviewer_light provides unique value over napari (better ND navigation), add submodule
- If functionality overlaps significantly with existing napari integration, evaluate need

---

## Implementation Checklist

### Phase 0: Evaluation
**Research needed**

- [ ] Evaluate ndviewer_light capabilities vs existing napari integration
- [ ] Check if ndviewer_light is available as pip package
- [ ] Test ndviewer_light with arch_v2's dataset format
- [ ] Decide: proceed with port or mark as not-needed

### Phase 1: Add Git Submodule (if proceeding)
**Repository root**

- [ ] Add submodule:
  ```bash
  git submodule add https://github.com/Cephla-Lab/ndviewer_light.git software/src/squid/vendor/ndviewer_light
  ```
- [ ] Update `.gitmodules`
- [ ] Update README with submodule instructions

### Phase 2: Create NDViewer Widget
**File:** `software/src/squid/ui/widgets/display/ndviewer_tab.py`

- [ ] Create `NDViewerTab(QWidget)`:
  - `__init__`: Setup layout with placeholder label
  - `_viewer: Optional[LightweightViewer]` - lazy-loaded viewer
  - `_dataset_path: Optional[str]` - current dataset
  - `_placeholder: QLabel` - shown when no acquisition

- [ ] Implement `_show_placeholder(message: str)`:
  - Show placeholder, hide viewer

- [ ] Implement `set_dataset_path(dataset_path: Optional[str])`:
  - Skip if path unchanged
  - Show placeholder if None
  - Verify path exists
  - Lazy import ndviewer_light
  - Create or reload viewer
  - Handle import/runtime errors gracefully

- [ ] Implement `go_to_fov(well_id: str, fov_index: int) -> bool`:
  - Check viewer exists and has FOV dimension
  - Find flat FOV index for (well_id, fov_index)
  - Navigate viewer to index
  - Return success/failure

- [ ] Implement `_find_flat_fov_index(well_id, fov_index) -> Optional[int]`:
  - Get FOV list from viewer
  - Find matching (region, fov) entry

- [ ] Implement `close()`:
  - Clean up viewer resources
  - Stop timers, close file handles

### Phase 3: Integrate with Main Window
**File:** `software/src/squid/ui/main_window.py`

- [ ] Add `self.ndviewerTab: Optional[NDViewerTab] = None`

- [ ] In display tab setup:
  ```python
  # After napari widgets initialized
  try:
      self.ndviewerTab = NDViewerTab()
      self.imageDisplayTabs.addTab(self.ndviewerTab, "NDViewer")
  except ImportError:
      self._log.warning("NDViewer unavailable: ndviewer_light not installed")
  except (RuntimeError, OSError) as e:
      self._log.exception(f"Failed to initialize NDViewer: {e}")
  ```

- [ ] Connect plate view → NDViewer navigation:
  ```python
  if self.napariPlateViewWidget and self.ndviewerTab:
      self.napariPlateViewWidget.signal_well_fov_clicked.connect(
          self._on_plate_view_fov_clicked
      )
  ```

- [ ] Add `_update_ndviewer_for_acquisition()`:
  - Get base_path and experiment_ID from controller
  - Call `ndviewerTab.set_dataset_path()`
  - Call at acquisition start

- [ ] Add `_on_plate_view_fov_clicked(well_id, fov_index)`:
  - Call `ndviewerTab.go_to_fov()`
  - Switch to NDViewer tab on success

- [ ] Update `closeEvent`:
  - Call `ndviewerTab.close()` for cleanup

### Phase 4: Add Plate View Signal
**File:** `software/src/squid/ui/widgets/display/plate_view.py`

- [ ] Verify `signal_well_fov_clicked = Signal(str, int)` exists
- [ ] Emit on double-click with (well_id, fov_index)
- [ ] Make boundaries layer non-interactive

### Phase 5: Update Documentation
**File:** `software/docs/ndviewer-tab.md`

- [ ] Document feature overview
- [ ] Document usage workflow
- [ ] Document plate view integration
- [ ] Document troubleshooting

### Phase 6: Write Tests
**File:** `software/tests/unit/ui/widgets/test_ndviewer_tab.py`

- [ ] Test initialization (placeholder shown)
- [ ] Test set_dataset_path (viewer created)
- [ ] Test path unchanged skip
- [ ] Test invalid path handling
- [ ] Test go_to_fov navigation
- [ ] Test close cleanup

---

## OpenGL Context Note

From upstream commit message:
> NDViewer is initialized AFTER napari widgets because NDV and napari both use vispy for OpenGL rendering. Initializing NDV first can cause OpenGL context conflicts.

Ensure NDViewerTab is created after napari widgets are initialized.

---

## API Reference (ndviewer_light)

```python
class LightweightViewer(QWidget):
    def __init__(self, dataset_path: str): ...
    def load_dataset(self, path: str): ...
    def refresh(self): ...
    def has_fov_dimension(self) -> bool: ...
    def get_fov_list(self) -> List[Dict]: ...  # [{"region": "A1", "fov": 0}, ...]
    def set_current_index(self, dim: str, index: int) -> bool: ...
    def close(self): ...
```

---

## Testing Strategy

1. **Unit tests**: Widget lifecycle, navigation logic
2. **Integration tests**: Main window integration (mock ndviewer_light)
3. **Manual tests**:
   - Start acquisition, verify tab loads
   - Double-click plate view, verify navigation
   - Verify live updates during acquisition
   - Verify clean shutdown

---

## Dependencies

- ndviewer_light (git submodule or pip)
- vispy (transitive via ndviewer_light)

## Risk Assessment

- **Medium risk**: External dependency adds complexity
- **OpenGL conflicts**: Possible with existing napari integration
- **Evaluation needed**: May overlap with existing napari capabilities
