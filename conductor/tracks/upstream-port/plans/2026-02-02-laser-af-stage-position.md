# Display Stage Position on Laser AF Focus Tab

**Status:** COMPLETED
**Started:** 2026-02-02

## Upstream Commits

- [x] `5708299d` - feat: Display stage position on laser-based focus tab (#476)

## Context

In upstream, the laser-based focus tab's live view (`imageDisplayWindow_focus`) doesn't show stage position because the `liveController` was not passed to it.

### arch_v2 Root Cause (Investigated)

`ImageDisplayWindow` already has a `@handles(StagePositionChanged)` handler (line 331 of `image_display.py`) that updates a `stage_position_label` in the status bar. **However**, the focus tab's instance is created without an event bus:

```python
# main_window.py line 256 — NO event_bus, so @handles decorators are dead
self.imageDisplayWindow_focus = ImageDisplayWindow(
    show_LUT=False, autoLevels=False
)

# Compare with main display (line 470) — HAS event_bus, stage position works
self.imageDisplayWindow = ImageDisplayWindow(
    contrastManager=self.contrastManager, event_bus=self._ui_event_bus
)
```

The fix is a **one-line change**: pass `event_bus=self._ui_event_bus` when creating `imageDisplayWindow_focus`.

## Implementation Checklist

- [x] Pass `event_bus=self._ui_event_bus` to `ImageDisplayWindow()` constructor at line 256 of `main_window.py`

### Tests
- [ ] Visual verification that stage position appears on focus tab
- [ ] Verify piezo position also displays when available

## Notes

- This is a one-line fix in arch_v2
- The `@handles` decorator pattern requires `auto_subscribe()` to be called with an event bus to actually register the handlers
- No backend changes needed — `StagePositionChanged` is already being published by `StageService`
