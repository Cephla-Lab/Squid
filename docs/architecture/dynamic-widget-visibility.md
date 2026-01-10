# Dynamic Widget Visibility Architecture

**Status:** Proposal / Future Work
**Created:** 2026-01-10
**Related PRs:** #424 (MCP view settings for RAM debugging)

## Context

Currently, GUI widgets like `napariMosaicDisplayWidget` and `napariPlateViewWidget` are conditionally created at application startup based on `_def` flags:

```python
# gui_hcs.py lines 876-886
if USE_NAPARI_FOR_MOSAIC_DISPLAY:
    self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(...)

if DISPLAY_PLATE_VIEW:
    self.napariPlateViewWidget = widgets.NapariPlateViewWidget(...)
```

This means MCP commands can change the flags, but the widgets won't appear/disappear until restart.

## Current Behavior (as of PR #424)

| Component | Respects MCP Changes? | When Evaluated |
|-----------|----------------------|----------------|
| Downsampled image generation | Yes | Acquisition start |
| Downsampled image saving | Yes | Acquisition start |
| Mosaic view updates | **Yes** | Each `updateMosaic` call |
| Mosaic view widget creation | No | GUI startup |
| Plate view widget creation | No | GUI startup |
| Signal connections | No | GUI startup |

**For RAM debugging**, MCP can now control:
- Downsampled image generation (set `SAVE_DOWNSAMPLED_WELL_IMAGES=False` and `DISPLAY_PLATE_VIEW=False`)
- Mosaic view updates (set `USE_NAPARI_FOR_MOSAIC_DISPLAY=False` - widget still exists but doesn't process images)

## Proposed Change

Always create widgets and connect signals, but:
- Hide widgets when feature is disabled
- Gate signal emissions based on feature flags

### Affected Code

1. **gui_hcs.py** - Widget creation (~line 876-886)
2. **gui_hcs.py** - Signal connections (~line 1361-1402)
3. **multi_point_controller.py** - Signal emissions
4. **widgets.py** - Possibly add enable/disable methods to widgets

### Pros

1. **Full MCP control** - Toggle features at runtime without restart
2. **Simpler code** - No `if widget is not None` checks needed
3. **Better testability** - Can test widgets even when disabled by default
4. **Consistent behavior** - Widgets always exist, state is visibility/enabled
5. **Easier debugging** - Enable features mid-session to investigate

### Cons

1. **Memory overhead** - Widgets exist even when unused (~small vs image data)
2. **Startup time** - Creating unused widgets adds initialization cost
3. **Complexity** - Need signal gating logic
4. **Bug risk** - Signals might fire when feature is "disabled"
5. **YAGNI** - Creating things you might not need

## Implementation Sketch

```python
# gui_hcs.py - Always create widgets
self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(...)
self.napariPlateViewWidget = widgets.NapariPlateViewWidget(...)

# Add/remove from tabs based on flag
def update_view_tabs(self):
    if control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
        if self.napariMosaicDisplayWidget not in self.imageDisplayTabs:
            self.imageDisplayTabs.addTab(self.napariMosaicDisplayWidget, "Mosaic View")
    else:
        idx = self.imageDisplayTabs.indexOf(self.napariMosaicDisplayWidget)
        if idx >= 0:
            self.imageDisplayTabs.removeTab(idx)
```

```python
# multi_point_controller.py - Gate signal emissions
if control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
    self.napari_layers_update.emit(layer_data)
```

## Implementation (PR #424)

Instead of the full architectural change proposed above, we implemented a simpler approach:

```python
# widgets.py - Gate updateMosaic at runtime
import control._def  # Module import for runtime access

def updateMosaic(self, image, x_mm, y_mm, k, channel_name):
    if not control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
        return  # Skip processing, save RAM
    # ... rest of method
```

This follows the established codebase pattern (used in `multi_point_controller.py`, `laser_auto_focus_controller.py`, etc.) where `import control._def` + `control._def.VARIABLE` provides runtime access to MCP-modifiable settings.

## Decision

**Partially implemented** - Runtime gating for RAM debugging is now functional. Full widget creation/destruction at runtime is deferred. Revisit if:
- Users need widgets to appear/disappear dynamically
- More features need runtime enable/disable
- Refactoring GUI architecture anyway

## Related Files

- `software/control/gui_hcs.py` - Main GUI, widget creation
- `software/control/widgets.py` - Widget implementations
- `software/control/core/multi_point_controller.py` - Signal emissions
- `software/control/_def.py` - Feature flags
- `software/control/microscope_control_server.py` - MCP commands

## Notes

- The `performance_mode` setting already demonstrates a pattern for dynamic behavior changes
- Consider whether a unified "feature flags" system with observers would be cleaner
- Qt's show/hide is cheap; the question is whether creating unused widgets is acceptable
