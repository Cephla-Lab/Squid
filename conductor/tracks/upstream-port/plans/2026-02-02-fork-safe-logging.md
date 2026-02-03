# Fork-Safe Logging Handler

**Status:** COMPLETED
**Started:** 2026-02-02

## Upstream Commits

- [x] `5a9d3c12` - fix: Replace Qt-based logging handler with fork-safe BufferingHandler (#487)

## Implementation Checklist

### Phase 1: BufferingHandler
- [x] Add `BufferingHandler` class to `squid/core/logging.py`
- [x] `queue.Queue(maxsize=1000)` for bounded buffering
- [x] `emit()` puts formatted records into queue (non-blocking, drops on overflow)
- [x] `get_pending()` returns list of (level, logger_name, message) tuples
- [x] `dropped_count` property for overflow visibility
- [x] Thread-safe, no Qt dependencies

### Phase 2: Widget Integration
- [x] Add `connect_handler()` / `disconnect_handler()` to `WarningErrorWidget`
- [x] Add QTimer (100ms) polling via `_poll_messages()`
- [x] Error handling in `_poll_messages()` (Qt silently swallows timer exceptions)
- [x] Update `main_window._connect_warning_handler()` to use `BufferingHandler`
- [x] Update `main_window._disconnect_warning_handler()` to call `disconnect_handler()`

### Phase 3: Cleanup
- [x] `QtLoggingHandler` preserved for backward compat (not removed)
- [x] `closeEvent` calls `disconnect_handler()` before cleanup

### Files Modified
- `software/src/squid/core/logging.py` — Added `BufferingHandler` class
- `software/src/squid/ui/widgets/warning_error_widget.py` — Added `connect_handler()`, `disconnect_handler()`, `_poll_messages()`, QTimer import
- `software/src/squid/ui/main_window.py` — Switched to `BufferingHandler` + `connect_handler()` API

### Files Created
- `software/tests/unit/squid/core/test_buffering_handler.py` — 5 tests

### Tests
- [x] Basic emit and get_pending
- [x] Level filtering (only WARNING+)
- [x] Queue drain (get_pending empties queue)
- [x] Queue overflow tracks dropped_count
- [x] Thread safety (4 threads × 50 msgs)
