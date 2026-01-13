# Backpressure/RAM Management Suite - Porting Plan

**STATUS: COMPLETED** - Ported in commit f5130544 (2026-01-12)

See `18-f5130544-backpressure-suite.md` for implementation details.

---

## Overview

Port 6 commits implementing acquisition backpressure (throttling) and RAM monitoring to prevent memory exhaustion during high-speed acquisitions.

**Commits (in order):**
1. `081fd7e9` - Core backpressure controller
2. `c28b372b` - RAM usage monitoring
3. `97f85d1b` - Backpressure status bar widget
4. `c3322bb1` - Z-stack deadlock fix (CRITICAL)
5. `e9c6249b` - Resource cleanup
6. `6bffd2d3` - Simplification and polish

**Recommendation:** Port as consolidated feature, incorporating final behavior from c3322bb1/6bffd2d3.

---

## File Mapping

| Upstream | arch_v2 | Action |
|----------|---------|--------|
| `control/core/backpressure.py` | `backend/controllers/multipoint/backpressure.py` | **Create** |
| `control/core/memory_profiler.py` | `backend/processing/memory_profiler.py` | **Create** |
| `control/core/job_processing.py` | `backend/controllers/multipoint/job_processing.py` | Modify |
| `control/core/multi_point_worker.py` | `backend/controllers/multipoint/multi_point_worker.py` | Modify |
| `control/core/multi_point_controller.py` | `backend/controllers/multipoint/multi_point_controller.py` | Modify |
| `control/widgets.py` (RAMMonitor) | `ui/widgets/display/ram_monitor.py` | **Create** |
| `control/widgets.py` (BackpressureMonitor) | `ui/widgets/display/backpressure_monitor.py` | **Create** |
| `control/gui_hcs.py` | `ui/main_window.py` | Modify |

---

## Implementation Phases

### Phase 1: Configuration
- Add to `_def.py`:
  ```python
  ACQUISITION_THROTTLING_ENABLED = True
  ACQUISITION_MAX_PENDING_JOBS = 10
  ACQUISITION_MAX_PENDING_MB = 500.0
  ACQUISITION_THROTTLE_TIMEOUT_S = 30.0
  ENABLE_MEMORY_PROFILING = False
  ```

### Phase 2: BackpressureController
Create `backend/controllers/multipoint/backpressure.py`:
- `multiprocessing.Value` for pending_jobs and pending_bytes
- `multiprocessing.Event` for capacity signaling
- Methods: `should_throttle()`, `wait_for_capacity()`, `get_stats()`, `close()`

### Phase 3: MemoryProfiler
Create `backend/processing/memory_profiler.py`:
- Background thread for periodic sampling
- Peak RSS tracking
- Qt signals for live updates
- Platform-specific footprint methods

### Phase 4: JobRunner Integration
Modify `backend/controllers/multipoint/job_processing.py`:
- Accept backpressure shared values in `__init__()`
- Increment counters in `dispatch()`, rollback on failure
- **CRITICAL:** Release bytes immediately in `run()` finally block (not per-well)

### Phase 5: MultiPointWorker Integration
Modify `backend/controllers/multipoint/multi_point_worker.py`:
- Create `BackpressureController` in `__init__()`
- Pass shared values to `JobRunner`
- Add throttle check in acquisition loop
- Call `close()` in `_finish_jobs()`

### Phase 6: MultiPointController Integration
Modify `backend/controllers/multipoint/multi_point_controller.py`:
- Add `@property backpressure_controller`
- Add `close()` method for shutdown

### Phase 7: UI Widgets
Create two new widgets in `ui/widgets/display/`:
- `RAMMonitorWidget` - "RAM: X.XX GB | peak: X.XX GB"
- `BackpressureMonitorWidget` - "Queue: X/Y jobs | X.X/Y.Y MB [THROTTLED]"

### Phase 8: MainWindow Integration
Modify `ui/main_window.py`:
- Add widgets to status bar
- Connect to acquisition events via EventBus
- Add closeEvent cleanup

---

## Key Considerations

1. **Immediate byte release** (from c3322bb1): Release bytes when each job completes, NOT when well completes. Prevents z-stack deadlock.

2. **EventBus vs Signals**: Keep Qt signals for status bar (UI-only). Use EventBus for acquisition start/stop.

3. **Daemon processes**: JobRunner should be daemon to prevent orphan processes.

---

## Critical Files
- `backend/controllers/multipoint/job_processing.py`
- `backend/controllers/multipoint/multi_point_worker.py`
- `backend/controllers/multipoint/multi_point_controller.py`
- `ui/main_window.py`
