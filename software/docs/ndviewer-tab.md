# NDViewer Tab

This document describes the embedded NDViewer tab for viewing acquisitions within the Squid GUI.

## Overview

The NDViewer tab provides an integrated viewer for browsing acquisition data without leaving the main application. It uses [ndviewer_light](https://github.com/Cephla-Lab/ndviewer_light), a lightweight viewer optimized for large multi-dimensional datasets.

Key features:
- **Auto-load**: Automatically points to the current acquisition when it starts
- **Live updates**: Refreshes periodically to show new data during acquisition
- **Plate view integration**: Double-click navigation from plate view to specific FOVs
- **Dimension sliders**: Navigate through channels, z-slices, timepoints, and FOVs

## Enabling the Feature

The NDViewer tab is enabled by default when ndviewer_light is available. No configuration is required.

If the tab doesn't appear, check the logs for import errors related to `ndviewer_light`.

## Usage

### Viewing Acquisitions

1. Start an acquisition (wellplate or flexible region mode)
2. The NDViewer tab automatically loads the acquisition folder
3. Use the dimension sliders to navigate:
   - **c**: Channel
   - **z**: Z-slice
   - **t**: Timepoint
   - **fov**: Field of view

### Plate View Navigation

When using wellplate acquisitions with the plate view enabled:

1. Double-click any FOV in the plate view
2. The NDViewer automatically navigates to that well/FOV
3. The tab switches to NDViewer to show the selected location

This provides a quick way to jump between the overview (plate view) and detailed view (NDViewer).

See [Downsampled Plate View](downsampled-plate-view.md) for more information about the plate view feature.

## Architecture

The NDViewer tab uses a lightweight embedding approach:

- **Lazy loading**: ndviewer_light is imported only when the first acquisition starts, minimizing startup time
- **Submodule**: ndviewer_light is included as a git submodule at `software/control/ndviewer_light`
- **Public APIs**: All interactions use public ndviewer_light methods for stability

## Troubleshooting

### NDViewer tab doesn't appear

Check the application logs for errors. Common causes:
- Missing ndviewer_light submodule (run `git submodule update --init`)
- Import errors due to missing dependencies

### Viewer shows "waiting for acquisition"

The viewer needs an active acquisition to display. Start an acquisition and the viewer will automatically load the dataset.

### Viewer shows error loading dataset

This can happen if:
- The acquisition folder doesn't exist yet (wait for acquisition to start)
- The dataset format is not supported by ndviewer_light
- File permissions prevent reading the data

### Double-click navigation doesn't work

Ensure both features are enabled:
- Plate view must be visible (see `DISPLAY_PLATE_VIEW` in `_def.py`)
- NDViewer tab must be initialized successfully

Check logs for debug messages like "Could not navigate to FOV" which indicate the specific failure reason.

## Technical Details

### FOV Mapping

The plate view uses well IDs (e.g., "A1", "B2") and per-well FOV indices. NDViewer uses a flat FOV index across all wells. The navigation code maps between these:

```
Plate view: Well "B2", FOV 3
     ↓
NDViewer: Flat FOV index (e.g., 27)
```

### Resource Cleanup

The NDViewer tab properly cleans up resources (file handles, refresh timers) when the application closes. This is handled automatically in the GUI's close event.
