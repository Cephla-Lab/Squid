# Dynamic Widget Visibility Architecture

**Status:** Partially Implemented
**Created:** 2026-01-10
**Updated:** 2026-01-10
**Related PRs:**
- #424 (merged 2026-01-10) - Config dialog `control._def` pattern + mosaic view runtime gating
- #425 - MCP commands for view settings control

## Problem

During high-content screening acquisitions, RAM usage can become a bottleneck. The main RAM consumers are:
- **Mosaic view**: Accumulates all acquired tiles in napari layers
- **Plate view**: Generates downsampled composite images of entire wells
- **Downsampled well images**: Saves multiple resolution versions of each well

To debug RAM issues, we need to toggle these features at runtime without restarting the application. This document tracks the implementation of runtime control via MCP (Microscope Control Protocol - the TCP server exposing microscope control commands).

## Current Behavior

GUI widgets like `napariMosaicDisplayWidget` and `napariPlateViewWidget` are conditionally created at application startup based on `control._def` flags:

```python
# gui_hcs.py (approximate locations, may drift)
if USE_NAPARI_FOR_MOSAIC_DISPLAY:
    self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(...)

if DISPLAY_PLATE_VIEW:
    self.napariPlateViewWidget = widgets.NapariPlateViewWidget(...)
```

### What Respects MCP Changes

| Component | Respects MCP Changes? | When Evaluated |
|-----------|----------------------|----------------|
| Downsampled image generation | Yes | Acquisition start |
| Downsampled image saving | Yes | Acquisition start |
| Mosaic view updates | **Yes** | Each `updateMosaic` call |
| Mosaic view widget creation | No | GUI startup |
| Plate view widget creation | No | GUI startup |
| Signal connections | No | GUI startup |

## Usage: RAM Debugging Workflow

```python
# 1. Check current settings
get_view_settings()
# Returns: {save_downsampled_well_images: True, display_plate_view: True, ...}

# 2. Disable all RAM-heavy features
set_view_settings(
    save_downsampled_well_images=False,
    display_plate_view=False,
    display_mosaic_view=False
)

# 3. Start new acquisition - observe reduced RAM usage

# 4. Re-enable features one by one to isolate the culprit
set_display_mosaic_view(enabled=True)
# Start acquisition, check RAM...
```

## Implementation

### PR #424 - Runtime Gating

Instead of the full architectural change (see "Alternative Approach" below), we implemented a simpler approach:

```python
# widgets.py - Gate updateMosaic at runtime
import control._def  # Module import for runtime access

def updateMosaic(self, image, x_mm, y_mm, k, channel_name):
    if not control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
        return  # Skip processing, save RAM
    # ... rest of method
```

This follows the established codebase pattern (used in `multi_point_controller.py`, `laser_auto_focus_controller.py`, etc.) where `import control._def` + `control._def.VARIABLE` provides runtime access.

**Key insight**: `from control._def import *` creates local bindings that don't see runtime updates. Must use `import control._def` then `control._def.VARIABLE`.

**Also includes:**
- Config dialog reads from `control._def` (shows MCP changes when opened)
- Change detection compares against `control._def` (no phantom changes)
- Standardized `control._def.VARIABLE` pattern throughout widgets.py

### PR #425 - MCP Commands

Adds MCP commands to control view settings at runtime:

| Command | Description |
|---------|-------------|
| `get_view_settings` | Query current state of all view settings |
| `set_save_downsampled_images` | Toggle downsampled well image saving |
| `set_display_plate_view` | Toggle plate view generation |
| `set_display_mosaic_view` | Toggle mosaic view updates |
| `set_view_settings` | Set multiple settings at once |

## Decision

**Partially implemented** - Runtime gating for RAM debugging is now functional:
- ✅ Mosaic view updates can be disabled at runtime
- ✅ Plate view/downsampled image generation controlled at acquisition start
- ✅ MCP commands available for all view settings
- ✅ Config dialog reflects MCP changes
- ❌ Widget creation/destruction still at startup only (tabs don't appear/disappear)

## Alternative Approach (Deferred)

A more complete solution would always create widgets and connect signals, but:
- Hide/show widgets (tabs) when feature is toggled
- Gate signal emissions based on feature flags

### Pros
1. Full MCP control - Toggle features at runtime without restart
2. Simpler code - No `if widget is not None` checks needed
3. Better testability - Can test widgets even when disabled by default

### Cons
1. Memory overhead - Widgets exist even when unused
2. Startup time - Creating unused widgets adds initialization cost
3. Complexity - Need signal gating logic
4. Bug risk - Signals might accidentally fire when feature is "disabled"

### Implementation Sketch (if needed later)

```python
# gui_hcs.py - Always create widgets
self.napariMosaicDisplayWidget = widgets.NapariMosaicDisplayWidget(...)
self.napariPlateViewWidget = widgets.NapariPlateViewWidget(...)

# Add/remove from tabs based on flag
def update_view_tabs(self):
    if control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
        if self.imageDisplayTabs.indexOf(self.napariMosaicDisplayWidget) < 0:
            self.imageDisplayTabs.addTab(self.napariMosaicDisplayWidget, "Mosaic View")
    else:
        idx = self.imageDisplayTabs.indexOf(self.napariMosaicDisplayWidget)
        if idx >= 0:
            self.imageDisplayTabs.removeTab(idx)
```

Revisit this approach if:
- Users need widgets to appear/disappear dynamically (tab visibility)
- More features need runtime enable/disable
- Refactoring GUI architecture anyway

## Related Files

- `software/control/gui_hcs.py` - Main GUI, widget creation
- `software/control/widgets.py` - Widget implementations, `updateMosaic` gating
- `software/control/core/multi_point_controller.py` - Acquisition parameters from `control._def`
- `software/control/_def.py` - Feature flags (runtime state)
- `software/control/microscope_control_server.py` - MCP commands

## Notes

- The `performance_mode` setting demonstrates dynamic signal connection/disconnection (could be extended)
- `control._def` pattern requires `import control._def` (not `from control._def import *`) for runtime access
- Consider unified "feature flags" system with observers if more settings need this pattern
