# PR 9: Napari Icon Replacement

**Upstream Commit:** `c7bf416` - fix: Replace napari icon and menu with Cephla's (#388)
**Priority:** Low
**Effort:** Small (+11 lines)

## Summary

Replace napari's default icon and menu branding with Cephla branding.

## Upstream Changes

**Files Modified:**
- `software/control/widgets.py` (+10 lines)
- `software/main_hcs.py` (+1 line)

## arch_v2 Targets

| Upstream File | arch_v2 Location |
|---------------|------------------|
| `control/widgets.py` | `src/squid/ui/widgets/display/napari_live.py` or `napari_mosaic.py` |
| `main_hcs.py` | `software/main_hcs.py` |

## Implementation Checklist

### Step 1: Review Upstream
- [x] Read upstream diff for icon/branding changes
- [x] Identify icon resource location
- [x] Understand menu customization

### Step 2: Locate arch_v2 Code
- [x] Find napari viewer initialization in display widgets
- [x] Locate main window/app initialization

### Step 3: Apply Branding
- [x] Update icon setting code
- [x] Update menu branding
- [x] Ensure icon resources are available

### Step 4: Testing
- [ ] Launch application
- [ ] Verify window icon is Cephla logo
- [ ] Verify menu branding

## Expected Changes

```python
# Set window icon
from PyQt5.QtGui import QIcon
viewer.window.qt_viewer.setWindowIcon(QIcon('path/to/cephla_icon.png'))

# Customize napari menu/title
viewer.window.qt_viewer.setWindowTitle('Squid Microscope')
```

## Notes

- Cosmetic change, low priority
- Requires icon resource files to be available
- Quick win for branding consistency
