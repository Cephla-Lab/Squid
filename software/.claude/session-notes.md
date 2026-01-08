# Claude Code Session Notes

## Session: 2026-01-07

### Code Style Notes (from PR #420 review)

**Unicode characters:**
- Use ASCII `...` instead of Unicode ellipsis `...` (U+2026) for consistency and to avoid encoding issues
- This applies to UI strings in widgets.py and similar files

### Files Modified

- `software/control/widgets.py` - NDViewerTab placeholder text (lines 151, 167)

### Commits Made

- `f06e80eb` - fix: Replace Unicode ellipsis with ASCII periods in NDViewer placeholder
