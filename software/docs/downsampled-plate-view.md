# Downsampled Plate View

This document describes the downsampled plate view feature for well-based acquisitions.

## Overview

When imaging multi-well plates using "Select Wells" or "Load Coordinates" mode, the system can generate downsampled overview images in real-time without slowing down acquisition. These overview images provide:

- **Per-well images** at multiple resolutions (default: 5, 10, 20 µm/pixel)
- **Plate view** showing all wells in a compact grid layout
- **Live GUI display** updated as each well completes

This is useful for monitoring acquisition progress and quickly identifying wells of interest.

## Enabling the Feature

In your `_def.py` configuration file:

```python
# Enable downsampled well image generation (saves TIFF files)
GENERATE_DOWNSAMPLED_WELL_IMAGES = True

# Enable plate view display during acquisition
DISPLAY_PLATE_VIEW = True
```

Both features are disabled by default to avoid surprises for existing users.

## Configuration Options

All configuration options are in `control/_def.py`:

```python
# Enable/disable downsampled well TIFF generation
GENERATE_DOWNSAMPLED_WELL_IMAGES = False  # Set to True to save well TIFFs

# Enable/disable plate view tab in GUI
DISPLAY_PLATE_VIEW = False  # Set to True to show plate view during acquisition

# Per-well image resolutions (µm/pixel)
DOWNSAMPLED_WELL_RESOLUTIONS_UM = [5.0, 10.0, 20.0]

# Resolution for the plate view grid (µm/pixel)
DOWNSAMPLED_PLATE_RESOLUTION_UM = 10.0

# Z-projection mode for z-stacks
DOWNSAMPLED_Z_PROJECTION = ZProjectionMode.MIP  # or ZProjectionMode.MIDDLE
```

### Z-Projection Modes

When acquiring z-stacks, the system needs to combine multiple z-levels into a single 2D image:

| Mode | Description | Use Case |
|------|-------------|----------|
| `MIP` | Maximum Intensity Projection - takes brightest pixel at each position across all z-levels | Fluorescence imaging, detecting bright objects at any z-level |
| `MIDDLE` | Uses the middle z-slice only (z = NZ // 2) | Transmitted light, when middle plane is representative |

MIP is computed efficiently using a running maximum that updates in-place, avoiding the need to store all z-levels in memory.

### Timeout Configuration

For very large plates (384-well, 1536-well), you may need to adjust the timeout values:

```python
# Maximum wait time for all jobs to complete (seconds)
DOWNSAMPLED_VIEW_JOB_TIMEOUT_S = 30.0  # Increase for large plates

# Time without new results before assuming completion (seconds)
DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S = 2.0
```

**Scaling guide for `DOWNSAMPLED_VIEW_JOB_TIMEOUT_S`:**
- 96-well plate: 30 seconds (default)
- 384-well plate: 60-120 seconds
- 1536-well plate: 300-800 seconds

## Output Files

### Per-well TIFF Files

Each well generates multipage TIFF files at each configured resolution:

```
{acquisition_folder}/downsampled_wells/
├── A1_5um.tiff   # Well A1 at 5 µm/pixel
├── A1_10um.tiff  # Well A1 at 10 µm/pixel
├── A1_20um.tiff  # Well A1 at 20 µm/pixel
├── A2_5um.tiff
├── ...
```

Each TIFF contains all channels as separate pages with OME metadata.

### Plate View TIFF

A combined plate view is saved per timepoint:

```
{acquisition_folder}/plate_t{timepoint}_10um.tiff
```

The plate view arranges wells in a compact grid (not stage coordinates) with wells immediately adjacent.

## Plate View Widget (GUI)

When the feature is enabled and you're running a well-based acquisition without z-stacking, a "Plate View" tab appears in the display area (replacing the Mosaic View tab).

### Features

- **Multi-channel display** with additive blending and per-channel colormaps
- **Linked contrast** with the main contrast manager
- **Well boundaries** shown as white rectangles
- **NDViewer navigation** - double-click any FOV to view it in the NDViewer tab
- **Zoom limits** - prevents zooming out beyond the plate or zooming in too far

### NDViewer Integration

When the NDViewer tab is available, double-clicking on any location in the plate view will:
1. Navigate the NDViewer to that specific well and FOV
2. Automatically switch to the NDViewer tab

This provides a quick workflow for browsing: use the plate view for overview, then double-click to inspect specific FOVs in detail.

See [NDViewer Tab](ndviewer-tab.md) for more information about the embedded viewer.

### When is Plate View Available?

The Plate View tab appears when:
1. `DISPLAY_PLATE_VIEW = True`
2. Acquisition mode is "Select Wells" OR "Load Coordinates" with well-based regions
3. No z-stacking (NZ = 1)

For z-stack acquisitions, the Mosaic View is used instead.

---

# Background Job Processing Architecture

## Overview

The downsampled view generation uses a multiprocessing-based job system to offload image processing from the main acquisition thread. This ensures image capture is never delayed by downsampling operations.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            MAIN PROCESS                                      │
│                                                                              │
│  ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐     │
│  │ MultiPointWorker │────▶│  Input Queue     │────▶│   JobRunner      │     │
│  │                  │     │ (mp.Queue)       │     │ (subprocess)     │     │
│  │  - Captures img  │     └──────────────────┘     │                  │     │
│  │  - Creates job   │                              │  - Runs job.run()│     │
│  │  - Dispatches    │     ┌──────────────────┐     │  - Returns result│     │
│  │                  │◀────│  Output Queue    │◀────│                  │     │
│  └──────────────────┘     │ (mp.Queue)       │     └──────────────────┘     │
│           │               └──────────────────┘              │               │
│           │                                                 │               │
│           ▼                                                 ▼               │
│  ┌──────────────────┐                          ┌──────────────────┐         │
│  │ DownsampledView  │                          │ WellTileAccum.   │         │
│  │ Manager          │                          │ (per-process)    │         │
│  │                  │                          │                  │         │
│  │ - Stores plate   │                          │ - Accumulates    │         │
│  │   view in RAM    │                          │   tiles per well │         │
│  │ - Updates GUI    │                          │ - Stitches when  │         │
│  │                  │                          │   well complete  │         │
│  └──────────────────┘                          └──────────────────┘         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Components

### Job (base class)
Abstract base class for background processing tasks. Each job receives:
- `capture_info`: Metadata about the capture (position, channel, z-index, etc.)
- `capture_image`: The image array to process

### DownsampledViewJob
Specialized job that:
1. Crops overlap from tile edges
2. Accumulates tiles for a well using a per-process class variable
3. When all FOVs for all channels/z-levels are received, stitches the well
4. Returns the stitched image for plate view update

### JobRunner (multiprocessing.Process)
A subprocess that:
- Pulls jobs from an input queue
- Executes `job.run()`
- Puts results on an output queue
- Only queues non-None results (intermediate FOVs return None)

### WellTileAccumulator
Manages tile accumulation for a single well:
- Tracks received tiles per channel
- Handles z-projection (MIP or middle layer)
- Stitches tiles when all FOVs are received

### DownsampledViewManager
Manages the plate-level view:
- Allocates plate array: `(num_channels, plate_height, plate_width)`
- Updates individual well slots
- Provides copy for GUI display
- Saves plate view to disk

## Data Flow

1. **Image Capture**: MultiPointWorker captures an image
2. **Job Creation**: Creates DownsampledViewJob with tile + metadata
3. **Job Dispatch**: Job sent to JobRunner via input queue
4. **Job Execution**: JobRunner subprocess runs the job
   - Tile cropped and added to WellTileAccumulator
   - If well incomplete: returns None (not queued)
   - If well complete: stitches and returns DownsampledViewResult
5. **Result Processing**: Main process polls output queue
   - Updates DownsampledViewManager
   - Emits signal to update GUI
6. **Timepoint End**: Wait for remaining jobs, save plate view

## Thread Safety

The job system is safe because:

1. **Process isolation**: Each JobRunner is a separate process with its own memory space
2. **Queue-based communication**: All data exchange uses `multiprocessing.Queue`
3. **No shared mutable state**: `_well_accumulators` class variable is process-local

**Warning**: Do NOT use DownsampledViewJob with thread-based executors (ThreadPoolExecutor). The class-level accumulator would be shared between threads, causing race conditions.

## Performance Considerations

### Why Multiprocessing?

- **No GIL contention**: Python's Global Interpreter Lock doesn't affect separate processes
- **Parallel execution**: Image processing runs concurrently with acquisition
- **Memory isolation**: Accumulator state doesn't bloat main process memory

### Optimizations Implemented

1. **In-place MIP**: `np.maximum(current, new, out=current)` avoids array allocation
2. **cv2.INTER_AREA downsampling**: High-quality downsampling for minification
3. **No None results in queue**: Intermediate FOVs don't flood the output queue
4. **Efficient overlap calculation**: O(n) algorithm by grouping FOVs into rows

### Startup Timing

The JobRunner subprocess needs ~600ms to initialize (Python module imports). This happens during `runner.start()`, which is called before image capture begins. The first job may need to wait for subprocess initialization, but this is hidden by the time spent on stage movement.

## Troubleshooting

### Plate view not appearing
- Check `DISPLAY_PLATE_VIEW = True` in `_def.py`
- Verify acquisition mode is "Select Wells" or well-based "Load Coordinates"
- Ensure NZ = 1 (no z-stacking)

### Plate view appears slowly
- First well may take longer due to subprocess initialization
- Check that `DOWNSAMPLED_VIEW_JOB_TIMEOUT_S` is sufficient
- Monitor console for timeout warnings

### Missing wells in plate view
- Check for timeout warnings in console
- Increase `DOWNSAMPLED_VIEW_JOB_TIMEOUT_S` for large plates
- Verify all wells have the same FOV grid pattern

### Memory usage
- Plate view is stored in RAM (one copy in main process)
- Memory = num_channels × plate_height × plate_width × dtype_size
- For 96-well plate at 10µm: ~100-500 MB depending on well size

## API Reference

### Configuration Constants (_def.py)

| Constant | Type | Default | Description |
|----------|------|---------|-------------|
| `GENERATE_DOWNSAMPLED_WELL_IMAGES` | bool | False | Enable well TIFF generation |
| `DISPLAY_PLATE_VIEW` | bool | False | Show plate view tab in GUI |
| `DOWNSAMPLED_WELL_RESOLUTIONS_UM` | List[float] | [5, 10, 20] | Per-well image resolutions |
| `DOWNSAMPLED_PLATE_RESOLUTION_UM` | float | 10.0 | Plate view resolution |
| `DOWNSAMPLED_Z_PROJECTION` | ZProjectionMode | MIP | Z-projection mode |
| `DOWNSAMPLED_VIEW_JOB_TIMEOUT_S` | float | 30.0 | Max job wait time |
| `DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S` | float | 2.0 | Idle detection time |

### ZProjectionMode Enum (_def.py)

```python
class ZProjectionMode(str, Enum):
    MIP = "mip"       # Maximum intensity projection
    MIDDLE = "middle" # Middle z-slice
```
