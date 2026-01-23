# NDViewer Tab

This document describes the embedded NDViewer tab for viewing acquisitions within the Squid GUI.

## Overview

The NDViewer tab provides an integrated viewer for browsing acquisition data without leaving the main application. It uses [ndviewer_light](https://github.com/Cephla-Lab/ndviewer_light), a lightweight viewer optimized for large multi-dimensional datasets.

Key features:
- **Push-based updates**: Images appear immediately as they're saved (no polling)
- **Plate view integration**: Double-click navigation from plate view to specific FOVs
- **Animated playback**: Play buttons for cycling through timepoints and FOVs
- **Memory-efficient**: LRU cache with 256MB memory limit for z-stack viewing

## Enabling the Feature

The NDViewer tab can be enabled/disabled in Settings > Preferences > Views.

If the tab doesn't appear, check the logs for import errors related to `ndviewer_light`.

## Usage

### Viewing Acquisitions

1. Start an acquisition (wellplate or flexible region mode)
2. The NDViewer tab automatically configures for the acquisition
3. Images appear as they're saved during acquisition
4. Use the sliders to navigate:
   - **T**: Timepoint (hidden if only 1 timepoint)
   - **FOV**: Field of view
   - Internal NDV sliders for channel and z-slice

### Play Button Animation

Both T and FOV sliders have play buttons (▶) for animated playback:
- Click ▶ to start cycling through values at 500ms intervals
- Click ⏸ to pause
- Animation loops back to the beginning

### Plate View Navigation

When using wellplate acquisitions with the plate view enabled:

1. Double-click any FOV in the plate view
2. The NDViewer automatically navigates to that well/FOV
3. The tab switches to NDViewer to show the selected location

This provides a quick way to jump between the overview (plate view) and detailed view (NDViewer).

See [Downsampled Plate View](downsampled-plate-view.md) for more information about the plate view feature.

## Architecture

### Push-Based API

The NDViewer uses a push-based API for live acquisition viewing, eliminating filesystem polling:

```
Acquisition Start
     │
     ▼
start_acquisition(channels, num_z, height, width, fov_labels)
     │
     ├── Configures LUTs based on channel wavelengths
     ├── Sets up FOV slider with labels like "A1:0", "A1:1"
     └── Initializes NDV viewer with placeholder array

Each Image Saved
     │
     ▼
register_image(t, fov_idx, z, channel, filepath)
     │
     ├── Called on GUI thread via Qt signal from worker
     ├── Updates viewer's internal file index
     └── Auto-loads if image is for current FOV position

Acquisition End
     │
     ▼
end_acquisition()
     │
     └── Navigation continues to work for browsing
```

### Thread Safety

- Worker thread emits `ndviewer_register_image` Qt signal
- Qt marshals the signal to the GUI thread (AutoConnection → QueuedConnection)
- `NDViewerTab.register_image()` executes on the GUI thread
- All viewer state updates happen on a single thread (GUI), avoiding race conditions

### Memory Management

The viewer uses a memory-bounded LRU cache for image planes:

- **Cache limit**: 256MB (configurable via `PLANE_CACHE_MAX_MEMORY_BYTES`)
- **LRU eviction**: Least-recently-used planes are evicted first
- **Per-FOV loading**: Each `load_fov()` call loads all z-levels × channels
- **Typical usage**: ~7-8 planes cached for 4K images

### FOV Mapping

The plate view uses well IDs (e.g., "A1", "B2") and per-well FOV indices. NDViewer uses a flat FOV index with labels:

```
Plate view: Well "B2", FOV 3
     ↓
Label: "B2:3"
     ↓
NDViewer: Flat FOV index (e.g., 27)
```

The `_ndviewer_region_fov_offset` dict maps region names to their starting flat index.

## Troubleshooting

### NDViewer tab doesn't appear

Check the application logs for errors. Common causes:
- NDViewer disabled in Settings > Preferences > Views
- Missing ndviewer_light submodule (run `git submodule update --init`)
- Import errors due to missing dependencies

### Images appear black

Check the logs for warnings like "Failed to load image plane". Possible causes:
- File not yet saved when load attempted (race condition)
- File permissions prevent reading
- Corrupted TIFF file

### Double-click navigation doesn't work

Check logs for warnings. Possible causes:
- FOV not registered yet (during early acquisition)
- Well ID format mismatch (e.g., "A1" vs "A01")
- NDViewer not in push mode (no acquisition started)

### Play button doesn't animate

The slider must have a range > 0:
- T slider only appears when Nt > 1
- FOV slider needs at least 2 FOVs

## Technical Reference

### Signal Connections

```python
# In gui_hcs.py make_connections():
multipointController.ndviewer_start_acquisition.connect(ndviewerTab.start_acquisition)
multipointController.ndviewer_register_image.connect(ndviewerTab.register_image)
multipointController.acquisition_finished.connect(ndviewerTab.end_acquisition)
```

### Key Classes

- `NDViewerTab` (widgets.py): Wrapper widget with placeholder and error handling
- `LightweightViewer` (ndviewer_light/core.py): Core viewer with push-based API
- `MemoryBoundedLRUCache` (ndviewer_light/core.py): Memory-efficient plane cache

### Configuration Constants

```python
# ndviewer_light/core.py
SLIDER_PLAY_INTERVAL_MS = 500  # Animation speed
PLANE_CACHE_MAX_MEMORY_BYTES = 256 * 1024 * 1024  # 256MB cache limit
```
