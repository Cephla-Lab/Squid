# Persistence Features

**Our Commit:** 6acb9b35
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| fc57e3da | feat: Persist last used base saving path for multipoint |
| 98c7fbd6 | feat: Save and restore camera settings on close/startup |
| cc205460 | fix: Fix NapariLiveWidget Qt signal connection and camera attribute |

Note: cc205460 was SKIPPED (already-fixed in arch_v2)

## Summary

Adds persistence for user settings:
1. Last used saving path remembered across sessions
2. Camera settings (exposure, gain, binning) restored on startup

## Files Created/Modified

### Created
- `core/utils/cache.py` (102 lines) - Generic text file cache utilities
- `backend/drivers/cameras/settings_cache.py` (153 lines) - Camera settings YAML persistence

### Modified
- `ui/main_window.py` - Camera settings save/restore integration
- `ui/widgets/acquisition/flexible_multipoint.py` - Use cached saving path
- `ui/widgets/acquisition/wellplate_multipoint.py` - Use cached saving path

## Architecture

### Cache Location
```
software/cache/
├── last_saving_path.txt
└── camera_settings.yaml
```

### Cache API
```python
# core/utils/cache.py
def get_cache_dir() -> Path
def read_cached_value(name: str) -> Optional[str]
def write_cached_value(name: str, value: str) -> bool

# backend/drivers/cameras/settings_cache.py
def save_camera_settings(camera, path) -> bool
def load_camera_settings(camera, path) -> bool
```

## Tests

**Status:** Missing dedicated test file

Should add tests for:
- Cache read/write operations
- Invalid cache handling
- Camera settings round-trip

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Proper layer separation (cache utils in core, driver code in backend)
- [ ] Tests added
