# Simulation and Throttling UI Improvements

**Our Commit:** 6c9cb672
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 5ad9252a | fix: Regenerate SimulatedCamera frame when binning changes |
| 57378358 | feat: Add acquisition throttling settings in Preferences |

## Summary

Two small improvements:
1. Fix SimulatedCamera to regenerate frames when binning changes
2. Add throttling settings UI to Preferences dialog

## Files Modified

- `backend/drivers/cameras/simulated.py` - Added frame cache invalidation in `set_binning()`
- `ui/widgets/config.py` - Added throttling settings in Advanced tab

## Key Changes

### SimulatedCamera Fix
```python
def set_binning(self, binning: int) -> None:
    self._binning = binning
    self._current_raw_frame = None  # Invalidate cache to regenerate
```

### Throttling UI
Added to AdvancedPreferencesTab:
- Enable Acquisition Throttling checkbox
- Max Pending Jobs spinbox
- Max Pending MB spinbox
- Throttle Timeout spinbox

## Tests

**Status:** No dedicated tests needed (UI feature, existing camera tests cover binning)

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Minimal feature, no tests needed
