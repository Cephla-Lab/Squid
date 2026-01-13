# NDViewer Tab

**Our Commit:** d9966007
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 4234d34b | feat: NDViewer tab with live viewing and plate navigation |

## Summary

Adds an NDViewer tab that displays the current acquisition using napari's n-dimensional viewer. Automatically loads when an acquisition starts and supports navigation from plate view.

## Files Created/Modified

### Created
- `ui/widgets/display/ndviewer_tab.py` (181 lines) - Main widget
- `ui/widgets/ndviewer_light/` - Submodule for lightweight viewer
- `tests/unit/ui/widgets/display/test_ndviewer_tab.py` (223 lines) - **15 tests**

### Modified
- `ui/main_window.py` - Tab integration
- `core/events.py` - Added `base_path` to AcquisitionStarted event

## Architecture Adaptation

**Upstream approach:** Polled multipoint controller directly for acquisition path
**arch_v2 approach:** Subscribes to AcquisitionStarted event which includes base_path

```python
@handles(AcquisitionStarted)
def _on_acquisition_started(self, event: AcquisitionStarted) -> None:
    if event.base_path:
        self._load_acquisition(event.base_path)
```

## Key Features

- **Lazy loading:** NDViewer imported only when tab is shown
- **Placeholder:** Shows "Waiting for acquisition to start..." until data available
- **FOV navigation:** `go_to_fov(region_id, fov_index)` method for plate view integration
- **Cleanup:** Proper resource release in `cleanup()` method

## Tests

**File:** `tests/unit/ui/widgets/display/test_ndviewer_tab.py`
**Count:** 15 tests

Covers:
- Widget initialization
- Placeholder display
- Acquisition loading
- FOV navigation
- Resource cleanup

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed (EventBus subscription)
- [x] Lazy loading implemented
- [x] Tests added (15 tests)
