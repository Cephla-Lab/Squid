# HCS Acquisition Performance Optimization

**Date:** 2026-01-07
**Based on:** Log analysis from acquisitions on Jan 7, 14:31-14:33

## Current Performance Baseline

| Metric | Value |
|--------|-------|
| Time per FOV | ~421ms (40 FOV acquisition) |
| send_trigger | 72ms mean |
| acquire_camera_image | 91ms mean |
| move_to_coordinate | 65ms mean |
| _image_callback | 22ms mean |

## Potential Optimizations

### 1. Overlap Movement with Image Readout (High Impact)
- **Current:** Movement waits until camera readout completes
- **Proposed:** Start stage movement while camera is reading out the last image of a position
- **Estimated savings:** ~65ms per FOV
- **Implementation:** Modify `acquire_at_position` to trigger movement before final image readout completes

### 2. Reduce Trigger Latency (Medium Impact)
- **Current:** `send_trigger` at 72ms (min 42ms, max 179ms)
- **Proposed:**
  - Use hardware triggering instead of software triggering
  - Pre-arm the trigger before movement completes
- **Estimated savings:** 20-40ms per image

### 3. Path Optimization (Medium Impact)
- **Current:** `move_to_coordinate` varies 51-160ms
- **Proposed:** Optimize FOV visit order using:
  - Snake/serpentine pattern within wells
  - Nearest-neighbor algorithm for well ordering
- **Estimated savings:** 10-30ms average per move

### 4. Reduce _image_callback Overhead (Lower Impact)
- **Current:** 22ms mean with high variance (0.3-45ms)
- **Proposed:**
  - Defer non-critical work (display updates)
  - Batch UI updates instead of per-image
  - Move job dispatch off critical path
- **Estimated savings:** 10-15ms per image

### 5. Exposure Time Optimization (Hardware Dependent)
- **Proposed:** Reduce exposure time with higher illumination intensity
- **Trade-off:** May affect image quality, phototoxicity

## Already Optimized

- Downsample mode: Using `AREA_FAST` (~1ms per tile)
- Well processing: Fast at 15-50ms average
- Job dispatch: Fast at ~1ms
- Queue wait times: Normal on Jan 7 (high waits on Jan 6 were historical)

## Files to Investigate for Implementation

- `software/control/core/multi_point_worker.py` - Main acquisition loop
- `software/control/core/multi_point_controller.py` - Controller coordination
- Camera trigger logic
- Stage movement coordination

---

## Memory Debugging (Added 2026-01-07)

### Issue: RAM usage went up to 40GB (estimated 33GB)

### Memory Hotspots Identified

1. **NapariMosaicDisplayWidget.updateLayer()** (widgets.py:9725-9776)
   - When mosaic grows, allocates **new arrays for ALL channels** before freeing old ones
   - Peak memory = (old_arrays + new_arrays) × num_channels
   - For 6 channels at 33GB estimated → could spike to 66GB during resize

2. **WellTileAccumulator** (job_processing.py)
   - Stores tiles in memory until well is complete
   - For MIP mode with many z-levels, stores running max per (channel, fov)
   - Class-level `_well_accumulators` dict persists across acquisitions if not cleared

3. **DownsampledViewManager** (downsampled_views.py)
   - Pre-allocated plate view: plate_size × well_slot_shape × channels

### Memory Profiling Added

Added memory profiling to key locations. Look for these log entries:

```
[MEM] ACQUISITION START: ...
[MEM] ACQUISITION COMPLETE: ...
[MEM] MOSAIC RESIZE: ... peak=XXX MB
[MEM] ACCUMULATOR: N wells in memory
[MEM] ACCUMULATOR CLEAR: clearing N well accumulators
```

### Files Modified for Debugging

- `software/control/core/memory_profiler.py` - New memory profiling utilities
- `software/control/widgets.py` - Added logging to mosaic resize
- `software/control/core/job_processing.py` - Added logging to accumulators
- `software/control/core/multi_point_controller.py` - Added logging at acq start/end

### How to Debug

1. **Run acquisition and monitor logs** for `[MEM]` entries
2. **Look for MOSAIC RESIZE warnings** - these show peak memory during resize
3. **Check ACCUMULATOR counts** - should not grow unbounded
4. **Compare process memory vs system memory** to find the culprit

### Potential Fixes

1. **Pre-allocate mosaic arrays** at full size at acquisition start (avoid resize spikes)
2. **Use memory-mapped arrays** for mosaic view (trade CPU for RAM)
3. **Limit concurrent well accumulators** (process wells sequentially)
4. **Clear old array references immediately** before allocating new ones
5. **Enable "Performance Mode"** to disable mosaic view during acquisition
