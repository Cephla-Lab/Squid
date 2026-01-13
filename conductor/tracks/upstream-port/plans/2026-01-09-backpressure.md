# Backpressure/RAM Management Suite

**Status:** COMPLETED
**Ported:** 2026-01-12
**Our Commit:** f5130544
**Tracking File:** `commits/18-f5130544-backpressure-suite.md`

## Upstream Commits

- [x] `081fd7e9` - feat: Add acquisition backpressure to prevent RAM exhaustion
- [x] `c28b372b` - feat: Add live RAM usage monitoring display in status bar
- [x] `97f85d1b` - feat: Add backpressure status bar widget for acquisition throttling
- [x] `c3322bb1` - fix: Resolve backpressure deadlock with z-stack acquisitions (CRITICAL)
- [x] `e9c6249b` - fix: Backpressure byte tracking and multiprocessing reset
- [x] `6bffd2d3` - refactor: Simplify code from PRs 434/436, fix saving path

## Implementation Checklist

### Phase 1: Configuration
- [x] Add ACQUISITION_THROTTLING_ENABLED to _def.py
- [x] Add ACQUISITION_MAX_PENDING_JOBS to _def.py
- [x] Add ACQUISITION_MAX_PENDING_MB to _def.py
- [x] Add ACQUISITION_THROTTLE_TIMEOUT_S to _def.py

### Phase 2: BackpressureController
- [x] Create `backend/controllers/multipoint/backpressure.py`
- [x] Implement multiprocessing.Value for pending_jobs/bytes
- [x] Implement multiprocessing.Event for capacity signaling
- [x] Add should_throttle(), wait_for_capacity(), get_stats(), close()

### Phase 3: MemoryProfiler
- [x] Create `backend/processing/memory_profiler.py`
- [x] Background thread for periodic sampling
- [x] Peak RSS tracking
- [x] Platform-specific footprint methods

### Phase 4: JobRunner Integration
- [x] Accept backpressure shared values in __init__()
- [x] Increment counters in dispatch()
- [x] CRITICAL: Release bytes immediately in run() finally block

### Phase 5: MultiPointWorker Integration
- [x] Create BackpressureController in __init__()
- [x] Pass shared values to JobRunner
- [x] Add throttle check in acquisition loop
- [x] Call close() in _finish_jobs()

### Phase 6: UI Widgets
- [x] Create RAMMonitorWidget
- [x] Create BackpressureMonitorWidget
- [x] Add to status bar in main_window.py

### Phase 7: Tests
- [ ] Add unit tests for BackpressureController
- [ ] Add unit tests for MemoryProfiler

## Notes

Bytes are released immediately per-job (not per-well) to prevent z-stack deadlock, as fixed in upstream c3322bb1.
