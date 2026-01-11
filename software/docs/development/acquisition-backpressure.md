# Acquisition Backpressure

A throttling mechanism that prevents RAM exhaustion when acquisition speed exceeds disk write speed. When the job queue fills up, acquisition pauses before triggering the next camera frame until capacity becomes available.

## The Problem

```
Without Backpressure:
  Camera (100 img/s) → Queue (unbounded) → Disk (50 img/s)
                            ↓
                    Queue grows forever
                            ↓
                    RAM exhaustion → Crash
```

During high-speed acquisitions, if images are captured faster than they can be written to disk, the job queue grows without bound. Each queued image holds 8-32 MB of raw data, quickly consuming available RAM.

## The Solution

```
With Backpressure:
  Camera → [Throttle Check] → Queue (bounded) → Disk
              ↓
    If queue full, wait for capacity
              ↓
    RAM stays bounded, acquisition continues
```

The backpressure controller tracks pending jobs and bytes across all JobRunner processes. When either limit is reached, acquisition pauses before the next camera trigger until jobs complete and capacity becomes available.

## Configuration

Settings in `control/_def.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `ACQUISITION_THROTTLING_ENABLED` | `True` | Enable/disable backpressure throttling |
| `ACQUISITION_MAX_PENDING_JOBS` | `10` | Max jobs in flight before throttling |
| `ACQUISITION_MAX_PENDING_MB` | `500.0` | Max pending MB before throttling |
| `ACQUISITION_THROTTLE_TIMEOUT_S` | `30.0` | Max wait time when throttled (continues with warning) |

### Recommended Settings by System RAM

| System RAM | max_jobs | max_mb | Notes |
|------------|----------|--------|-------|
| 8 GB | 5 | 200 | Conservative for limited RAM |
| 16 GB | 10 | 500 | Default settings |
| 32 GB | 20 | 1000 | High-throughput systems |
| 64 GB+ | 40 | 2000 | Large acquisitions |

## How It Works

### Tracking Mechanism

The `BackpressureController` uses multiprocessing-safe shared values:

1. **Job Count** (`multiprocessing.Value`) - Number of jobs dispatched but not yet completed
2. **Byte Count** (`multiprocessing.Value`) - Total bytes of pending images
3. **Capacity Event** (`multiprocessing.Event`) - Signals when a job completes

These shared values work across the main process (acquisition) and JobRunner subprocess (disk writing).

### Throttle Check Location

The throttle check happens in `acquire_camera_image()` **after** waiting for the previous frame to be dispatched but **before** triggering the next camera capture:

```
1. Wait for previous frame callback
2. [THROTTLE CHECK] ← If limits exceeded, wait here
3. Send camera trigger
4. Continue acquisition
```

This location ensures:
- Previous image's jobs are already dispatched (counters accurate)
- Camera doesn't capture images that can't be processed
- No blocking in the camera callback (which must return quickly)

### Special Handling for DownsampledViewJob

`DownsampledViewJob` accumulates tiles in memory until a well completes, then generates stitched images. The backpressure tracking handles this specially:

- **Intermediate FOVs**: Job count decrements, but bytes stay tracked (image held in accumulator). Capacity event IS signaled (allows wait loop to re-check).
- **Final FOV**: ALL accumulated bytes for the well are decremented. Capacity event signaled.

The byte tracking accurately reflects memory usage - bytes aren't freed until the well's accumulator is cleared on final FOV.

## Logging

When throttling activates, messages are logged at INFO level:

```
INFO:control.core.backpressure:Backpressure throttling: jobs=10/10, MB=450.2/500.0
```

When capacity becomes available:

```
DEBUG:control.core.backpressure:Backpressure released
```

If timeout is reached:

```
WARNING:control.core.backpressure:Backpressure timeout after 30.0s, continuing
ERROR:control.core.multi_point_worker:Backpressure timeout - disk I/O cannot keep up. Stats: BackpressureStats(...)
```

## Monitoring

The `BackpressureController` provides a `get_stats()` method that returns current status:

```python
stats = controller.get_stats()
# BackpressureStats(
#     pending_jobs=5,
#     pending_bytes_mb=125.5,
#     max_pending_jobs=10,
#     max_pending_mb=500.0,
#     is_throttled=False
# )
```

## Testing with Simulated Disk I/O

To test backpressure without actual slow disk:

1. Enable **Simulated Disk I/O** in Settings
2. Set a slow simulated speed (e.g., 10-50 MB/s)
3. Run a multi-point acquisition
4. Observe throttling messages in the log

See [Simulated Disk I/O](simulated-disk-io.md) for details.

## Technical Details

### Files

| File | Purpose |
|------|---------|
| `control/core/backpressure.py` | `BackpressureController` class |
| `control/core/job_processing.py` | `JobRunner` tracking integration |
| `control/core/multi_point_worker.py` | Throttle check in acquisition loop |
| `control/_def.py` | Configuration constants |

### Counter Lifecycle

```
dispatch():
  ├─ Increment pending_jobs
  ├─ Increment pending_bytes
  └─ Put job in queue (with rollback on failure)

run() finally block:
  ├─ Decrement pending_jobs (always)
  ├─ For normal jobs: decrement pending_bytes immediately
  ├─ For DownsampledViewJob:
  │     ├─ Intermediate FOV: accumulate bytes (don't decrement)
  │     └─ Final FOV: decrement ALL accumulated bytes for the well
  └─ Signal capacity_event (always, for all job types)
```

### Thread Safety

- All shared state uses `multiprocessing.Value` with locks
- Event operations are atomic
- Counter updates use `max(0, ...)` to prevent negative values
- Lock ordering is consistent to prevent deadlocks

## Edge Cases

1. **Worker crash**: If the subprocess crashes, counters won't decrement. Mitigated by calling `reset()` at acquisition start.

2. **Timeout**: If disk stalls > 30s, acquisition continues anyway with a warning. The timeout prevents indefinite hangs.

3. **Dispatch failure**: If `put_nowait()` fails, rollback logic in `dispatch()` decrements all counters to maintain accuracy.

## Disabling Backpressure

To disable throttling (not recommended for production):

```python
# In control/_def.py
ACQUISITION_THROTTLING_ENABLED = False
```

When disabled, the system behaves as before - unbounded queue growth with potential RAM exhaustion during fast acquisitions.
