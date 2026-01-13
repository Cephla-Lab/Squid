# Backpressure/RAM Management Suite

**Our Commit:** f5130544
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| 081fd7e9 | feat: Add acquisition backpressure to prevent RAM exhaustion |
| c28b372b | feat: Add live RAM usage monitoring display in status bar |
| 97f85d1b | feat: Add backpressure status bar widget for acquisition throttling |
| c3322bb1 | fix: Resolve backpressure deadlock with z-stack acquisitions |
| e9c6249b | fix: Backpressure byte tracking and multiprocessing reset |
| 6bffd2d3 | refactor: Simplify code from PRs 434/436, fix saving path |

## Summary

Adds acquisition throttling and RAM monitoring to prevent memory exhaustion during high-speed acquisitions. The critical z-stack deadlock fix from c3322bb1 is included.

## Files Created/Modified

### Created
- `backend/controllers/multipoint/backpressure.py` (228 lines) - BackpressureController
- `backend/processing/memory_profiler.py` (253 lines) - MemoryMonitor class
- `ui/widgets/display/monitoring.py` (126 lines) - RAMMonitorWidget, BackpressureMonitorWidget

### Modified
- `src/_def.py` - Configuration constants
- `backend/controllers/multipoint/job_processing.py` - JobRunner integration
- `backend/controllers/multipoint/multi_point_worker.py` - Throttle checks
- `ui/main_window.py` - Status bar widgets

## Configuration Added

```python
ACQUISITION_THROTTLING_ENABLED = True
ACQUISITION_MAX_PENDING_JOBS = 10
ACQUISITION_MAX_PENDING_MB = 500.0
ACQUISITION_THROTTLE_TIMEOUT_S = 30.0
```

## Architecture Adaptations

- Uses `multiprocessing.Value` and `multiprocessing.Event` for cross-process job tracking
- JobRunner.dispatch() increments counters BEFORE queueing (prevents race conditions)
- JobRunner.run() decrements counters in finally block (ensures cleanup)
- **Critical:** Bytes released per-job (not per-well) to prevent z-stack deadlock

## Tests

**Status:** Missing dedicated test file

Upstream had 720 lines of tests in `tests/control/core/test_backpressure.py`. These were not ported. Should add tests for:
- Counter increment/decrement correctness
- Cross-process synchronization
- Timeout handling
- Throttle wait behavior

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Thread safety verified
- [ ] Tests added
