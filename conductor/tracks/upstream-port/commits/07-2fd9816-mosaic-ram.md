# PR 7: Free RAM When Clearing Mosaic

**Upstream Commit:** `2fd9816` - fix: Actually free RAM when clearing mosaic view layers (#385)
**Priority:** High
**Effort:** Small (+36 lines, refactor)
**Status:** COMPLETED

## Summary

Fix memory leak when clearing mosaic view layers. Previously, RAM was not actually freed when layers were cleared.

## Upstream Changes

**Files Modified:**
- `software/control/widgets.py` (+36 lines, refactored layer clearing logic)

## arch_v2 Target

**Location:** `software/src/squid/ui/widgets/display/napari_mosaic.py`

Look for `NapariMosaicDisplayWidget` class and layer clearing methods.

## Implementation Checklist

### Step 1: Review Upstream
- [x] Read upstream diff for layer clearing changes
- [x] Understand the memory management fix
- [x] Identify key changes to layer cleanup

### Step 2: Locate arch_v2 Code
- [x] Find `NapariMosaicDisplayWidget` in napari_mosaic.py
- [x] Identify layer clearing method(s)
- [x] Check current implementation

### Step 3: Apply Fix
- [x] Apply refactored layer clearing logic
- [x] Ensure proper garbage collection triggers
- [x] Handle napari layer references correctly

### Step 4: Testing
- [ ] Run mosaic acquisition
- [ ] Monitor RAM usage
- [ ] Clear mosaic layers
- [ ] Verify RAM is actually freed

## Key Changes

The fix involves:
1. Properly removing all references to layer data (remove layers instead of zeroing data)
2. Explicitly calling garbage collection
3. Ensuring napari viewer releases layer memory
4. Added safeguard in on_shape_change() to only convert shapes when mosaic is initialized

## Implementation Details

Changes made to `software/src/squid/ui/widgets/display/napari_mosaic.py`:

1. Added `import gc` at top of file
2. Updated `clearAllLayers()` to:
   - Remove layers entirely instead of replacing with same-size zero arrays
   - Call `gc.collect()` to force memory return to OS
3. Updated `on_shape_change()` to:
   - Only convert shapes to mm if mosaic is initialized (has valid coordinate system)
   - Preserves existing shapes_mm when coordinate system not available

## Memory Management Pattern
```python
def clearAllLayers(self) -> None:
    # Clear pending updates and compositor state
    self._pending_updates.clear()
    self._compositor.clear()

    # Remove all layers except Manual ROI to free memory and allow proper reinitialization
    layers_to_remove = [layer for layer in self.viewer.layers if layer.name != "Manual ROI"]
    for layer in layers_to_remove:
        self.viewer.layers.remove(layer)

    # Reset mosaic-related state so reinitialization logic can run cleanly
    self.channels = set()
    self.viewer_extents = None
    self.layers_initialized = False
    self.top_left_coordinate = None
    self.mosaic_dtype = None

    # Force garbage collection to return memory to OS
    gc.collect()

    self.signal_clear_viewer.emit()
```

## Notes

- Important for long-running acquisition sessions
- Memory leaks can cause crashes on large mosaics
- Test with RAM monitoring tools (e.g., `htop`, `psutil`)
