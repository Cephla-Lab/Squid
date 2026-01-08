# Specification: Tissue Planning for MERFISH

## Context
MERFISH (Multiplexed Error Robust Fluorescence In Situ Hybridization) workflows require scanning tissue sections multiple times with fluidics-based probe cycling. Users need to:
1. Create a low-magnification overview scan to identify tissue regions
2. Draw polygons around tissue sections of interest
3. Generate tiled acquisition patterns at higher magnification
4. Save the planning session for use across multiple imaging rounds

## Goals
1. **Session Persistence**: Save and load mosaic images, polygon regions, and scan coordinates
2. **Objective-Aware Tiling**: Automatically recalculate tile grids when switching objectives
3. **Fluidics Integration**: Output coordinates compatible with existing fluidics_multipoint workflow
4. **Minimal UI Changes**: Extend existing widgets rather than creating new ones

## User Workflow
```
1. Mount slide, select low-mag objective (e.g., 4x)
2. Acquire mosaic scan covering slide area
3. Press 'D' to draw polygons around tissue sections
4. Click "Save Planning Session" → select output folder
5. Switch to high-mag objective (e.g., 20x or 60x)
   → System recalculates tile grid for new FOV size
6. Load coordinates.csv into fluidics_multipoint
7. Run multi-round MERFISH acquisition
```

## Requirements

### Functional
- Save mosaic image (downsampled composite, multi-channel TIFF)
- Save polygon regions (vertices in stage mm coordinates)
- Save scan coordinates (CSV format compatible with fluidics load)
- Save session metadata (objectives, overlap %, pixel size)
- Load session and restore mosaic display
- Auto-recalculate tiles when `ObjectiveChanged` event received
- S-pattern optimization for scan path (already exists)

### Non-Functional
- Polygon coordinates must be objective-independent (stage mm)
- CSV format must match existing `fluidics_multipoint.py` load format
- UI additions should be minimal (2 buttons in mosaic widget)

## Scope

### In Scope
- `core/events.py` - new save/load events
- `backend/io/tissue_session_io.py` - new file format handling
- `backend/managers/tissue_planning_session.py` - new session manager
- `ui/widgets/display/napari_mosaic.py` - Save/Load buttons

### Out of Scope
- New dedicated planning widget
- Full-resolution mosaic saving (uses viewer resolution)
- TSP-style path optimization (S-pattern is sufficient)
- Real-time tile preview while drawing

## Success Criteria
- Can save planning session with mosaic + polygons + coordinates
- Can load session and see mosaic restored in napari
- Coordinates load successfully into fluidics_multipoint
- Tile count updates when objective is changed
- Session files are human-readable (JSON, CSV, TIFF)
