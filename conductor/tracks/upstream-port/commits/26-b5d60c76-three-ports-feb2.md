# Three Upstream Ports: Logging, X-Light V1/V2, Laser AF Stage Position

**Our Commit:** b5d60c76
**Date:** 2026-02-02
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 5a9d3c12 | fix: Replace Qt-based logging handler with fork-safe BufferingHandler (#487) |
| 1221c7e6 | feat: Add X-Light V1/V2 protocol support with auto-detection (#477) |
| 5708299d | feat: Display stage position on laser-based focus tab (#476) |

## Upstream Commits Skipped

| Hash | Title | Reason |
|------|-------|--------|
| 7fc1ce34 | refactor: Remove dead code ImageArrayDisplayWindow (#482) | not-applicable |
| c99140b7 | fix: Use threaded operations for XLight controls (#475) | superseded |
| da8f193a | feat: Add Workflow Runner (#480) | deferred |
| 996d53b3 | feat: Add zarr v3 saving (#474) | deferred |

## Summary

Ported three upstream commits to arch_v2 and created plan files for the remaining two larger suites (illumination and filter wheel W2).

**Fork-safe logging:** Replaced Qt-signal-based `QtLoggingHandler` with `BufferingHandler` using `queue.Queue` for fork-compatible message buffering. Widget now polls via QTimer (100ms) instead of direct signal connection. This prevents crashes when multiprocessing uses fork on Linux.

**X-Light V1/V2 protocol:** Added automatic protocol version detection to the X-Light spinning disk driver. Tries V3 (115200 baud, `idc` command) first, falls back to V1/V2 (9600 baud) with assumed standard configuration. Added filter slider feature guards to the confocal widget to prevent errors on V1/V2 devices.

**Laser AF stage position:** One-line fix — passed `event_bus=self._ui_event_bus` to the focus tab's `ImageDisplayWindow` constructor so the existing `@handles(StagePositionChanged)` subscription activates.

## Files Created/Modified

### Created
- `software/tests/unit/squid/core/test_buffering_handler.py` (97 lines) - 5 unit tests for BufferingHandler
- `conductor/tracks/upstream-port/plans/2026-02-02-illumination-suite.md` - Plan for illumination port suite
- `conductor/tracks/upstream-port/plans/2026-02-02-fork-safe-logging.md` - Plan (completed)
- `conductor/tracks/upstream-port/plans/2026-02-02-filter-wheel-w2.md` - Plan for W2 port suite
- `conductor/tracks/upstream-port/plans/2026-02-02-xlight-v1v2.md` - Plan (completed)
- `conductor/tracks/upstream-port/plans/2026-02-02-laser-af-stage-position.md` - Plan (completed)

### Modified
- `software/src/squid/core/logging.py` - Added `BufferingHandler` class (56 lines)
- `software/src/squid/backend/drivers/lighting/xlight.py` - Protocol auto-detection, `_open_serial()`, `_connect_and_detect()`
- `software/src/squid/ui/widgets/warning_error_widget.py` - Added `connect_handler()`, `disconnect_handler()`, `_poll_messages()`
- `software/src/squid/ui/widgets/hardware/confocal.py` - Filter slider feature guards
- `software/src/squid/ui/main_window.py` - Switched to BufferingHandler API, passed event_bus to focus display

## Architecture Adaptations

- **Logging:** Upstream puts `BufferingHandler` in `squid/logging.py`; arch_v2 puts it in `squid/core/logging.py` (layer 0). Widget integration uses the same QTimer polling pattern.
- **X-Light:** Direct semantic port — driver-level change, no service layer impact. `SerialDeviceError` already existed in arch_v2's `serial_base.py`.
- **Laser AF:** Upstream passes `liveController` reference; arch_v2 uses EventBus subscription pattern — just needed the event_bus wired up.

## Tests

**File:** `software/tests/unit/squid/core/test_buffering_handler.py`
**Count:** 5 tests

Covers:
- Basic emit and get_pending
- Level filtering (only WARNING+)
- Queue drain semantics
- Queue overflow and dropped_count tracking
- Thread safety (4 threads × 50 messages)

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Tests added (5 for BufferingHandler)
- [x] Skip decisions documented with justifications
- [x] Plan files created for remaining suites
