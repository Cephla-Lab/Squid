# Backpressure Lifecycle Enforcement Port

**Upstream commit:** `be019383`
**Branch:** `multipoint-refactor` (arch_v2)
**Date:** 2026-02-07

## Summary

Port lifecycle tracking, TOCTOU race prevention, pre-warming infrastructure,
and test coverage from upstream BackpressureController to arch_v2.

## Implementation Steps

### 1. BackpressureController improvements (`backpressure.py`)
- [x] Add `__all__` export list
- [x] Add `_BYTES_PER_MB` constant, replace all `1024 * 1024` with it
- [x] Add `BackpressureValues` type alias
- [x] Add `create_backpressure_values()` factory function
- [x] Add `is_closed` property
- [x] Add `_warn_if_closed(method_name)` helper
- [x] Accept optional `bp_values` parameter in `__init__()` for pre-created values
- [x] Use capture-reference pattern in `get_pending_jobs()`, `get_pending_mb()`
- [x] Use capture-reference pattern in `should_throttle()`
- [x] Use capture-reference pattern in `wait_for_capacity()`
- [x] Add `_warn_if_closed` guard to `wait_for_capacity()`
- [x] Replace `time.time()` with `time.monotonic()` in `wait_for_capacity()`
- [x] Use capture-reference pattern in `job_dispatched()`
- [x] Make `get_stats()` atomic with nested locks
- [x] Handle closed state in `get_stats()` (return zeroed stats)
- [x] Add `_warn_if_closed` guard and pending-jobs warning to `reset()`
- [x] Signal capacity event before clearing refs in `close()` (wake blocked threads)
- [x] Update docstrings with ownership model, thread safety, and None-after-close notes

### 2. JobRunner improvements (`job_processing.py`)
- [x] Add `_ready_event` (multiprocessing.Event) to signal subprocess readiness
- [x] Add `wait_ready(timeout_s=5.0)` method
- [x] Add `is_ready()` method
- [x] Add `set_acquisition_info()` post-init setter
- [x] Add `set_zarr_writer_info()` post-init setter
- [x] Add shutdown sentinel (None) put in `shutdown()` to wake blocked `queue.get()`
- [x] Handle None sentinel in `run()` loop
- [x] Signal `_ready_event.set()` at start of `run()`
- [x] Use try-finally in `SaveZarrJob.clear_writers()` for safer cleanup

### 3. MultiPointController pre-warming (`multi_point_controller.py`)
- [x] Convert `print()` to `self._log.info()` in `set_z_stacking_config()`
- [ ] Pre-warming not ported (arch_v2 has different architecture - worker creates runners internally)

### 4. Worker logging cleanup (`multi_point_worker.py`)
- [x] Add module-level `_log` logger for static methods
- [x] Convert `print("camera.read_frame() returned None")` to `self._log.warning()`
- [x] Convert `print("writing R, G, B channels")` to `self._log.debug()`
- [x] Convert `print("constructing RGB image")` to `self._log.debug()` (instance method)
- [x] Convert `print("constructing RGB image")` + dtype/shape prints to `_log.debug()` (static method)
- [x] Convert `print("writing RGB image")` to `_log.debug()` (static method)
- [x] Convert `print("writing RGB image")` to `self._log.debug()` (instance method)

### 5. Tests (`test_backpressure.py`)
- [x] `test_is_closed_property` - lifecycle tracking
- [x] `test_properties_return_none_after_close` - shared values cleared
- [x] `test_close_is_idempotent` - multiple close() calls safe
- [x] `test_should_throttle_on_closed_controller_returns_false` - safe default
- [x] `test_wait_for_capacity_returns_immediately_when_closed` - no blocking
- [x] `test_reset_on_closed_controller_is_noop` - no crash
- [x] `test_get_pending_jobs_on_closed_controller_returns_zero` - safe default
- [x] `test_get_pending_mb_on_closed_controller_returns_zero` - safe default
- [x] `test_get_stats_on_closed_controller_returns_zeroed_stats` - safe defaults with config
- [x] `test_job_dispatched_on_closed_controller_is_noop` - no crash
- [x] `test_constructor_with_bp_values_uses_provided_values` - pre-warming path
- [x] `test_create_backpressure_values_returns_tuple` - factory function
- [x] `test_bp_values_counters_start_at_zero` - initial state
- [x] `test_reset_warns_when_jobs_pending` - warning log
- [x] `test_reset_no_warning_when_no_jobs_pending` - no false warning
- [x] `test_close_is_thread_safe` - concurrent close()
- [x] `test_close_wakes_blocked_wait_for_capacity` - thread unblocking
- [x] Basic functionality tests (8 additional tests)

## What Was NOT Ported

- **MultiPointController pre-warming**: The arch_v2 architecture has the worker create
  job runners internally (the controller doesn't touch `USE_MULTIPROCESSING`). Pre-warming
  from the controller level would require architectural changes that are out of scope.
  The `create_backpressure_values()` factory and `bp_values` parameter are in place if
  pre-warming is added later.

## Test Results

- 25 new tests, all passing
- 77 total backend unit tests passing (no regressions)
