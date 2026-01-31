# CLAUDE.md

## Project: Squid

Microscopy control software with Qt GUI and multiprocessing support.

## Code Style

- Python with Qt (PyQt5/PySide)
- Tests in `software/tests/`
- Docs in `software/docs/development/`

## Lessons Learned

### Documentation Examples Must Match Implementation
When writing code examples in documentation, ensure they match the actual implementation details (locks, cleanup calls, error handling). Simplified examples mislead readers into implementing incorrect patterns. Either match exactly or explicitly note "simplified for clarity."

**Example:** Doc showed `self._dropped_count += 1` but real code uses `with self._dropped_count_lock:` - this caused a review comment.

### Clean Up Vestigial Code After Changing Approach
When changing implementation strategy mid-development, review for leftover code from the previous approach. Save/restore patterns, unused variables, and setup code often become dead code.

**Example:** Test saved `original_max` but never modified the class constant - switched to manipulating instance queue instead.

### Avoid Redundant Imports in Hot Paths
Don't add inner imports that duplicate module-level imports, especially in frequently-called code like timer callbacks. The module is already available.

**Example:** `_poll_messages()` (called every 100ms) had `import squid.logging` despite module-level import.

## Key Files

- `software/squid/logging.py` - Fork-safe BufferingHandler for multiprocessing
- `software/control/widgets.py` - Qt widgets including WarningErrorWidget
- `software/docs/development/logging-and-fork-safety.md` - Logging architecture docs
