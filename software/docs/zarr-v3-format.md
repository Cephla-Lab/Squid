# Zarr v3 Output Format

This document describes the Zarr v3 output format for Squid acquisitions.

## Overview

Squid supports saving acquisition data in Zarr v3 format with OME-NGFF 0.5 metadata. This format provides:

- **High performance**: TensorStore backend with sharding for ~200 MB/s write speed
- **Compression options**: None, Fast (LZ4), Balanced (Zstd), Best (Zstd level 9)
- **Streaming support**: Data can be read during acquisition
- **OME-NGFF compliance**: Metadata compatible with standard viewers

## Enabling Zarr v3

Settings > Preferences > File Saving Format: **ZARR_V3**

Additional options:
- **Compression Level**: None (fastest), Fast (LZ4), Balanced (Zstd), Best (Zstd level 9)
- **Use 6D FOV dimension**: Combine all FOVs in a region into a single 6D array (non-standard)

## Output Structures

### HCS (Wellplate) Mode

When acquiring from wellplate positions (region names match pattern `[A-Z]+\d+`):

```
{experiment}/
└── plate.ome.zarr/
    ├── zarr.json          # Plate metadata (ome.plate)
    ├── A/
    │   └── 1/
    │       ├── zarr.json  # Well metadata (ome.well)
    │       ├── 0/
    │       │   ├── zarr.json  # FOV group (ome.multiscales, omero)
    │       │   └── 0/         # 5D array (T, C, Z, Y, X)
    │       └── 1/
    │           └── ...
    └── B/
        └── ...
```

### Per-FOV Mode (Default)

For non-wellplate acquisitions:

```
{experiment}/
└── zarr/
    └── {region}/
        ├── fov_0.ome.zarr/
        │   ├── zarr.json      # OME metadata
        │   └── 0/             # 5D array (T, C, Z, Y, X)
        └── fov_1.ome.zarr/
            └── ...
```

### 6D Mode (ZARR_USE_6D_FOV_DIMENSION=True)

Combines all FOVs into a single zarr store per region. Non-standard but more efficient for some workflows:

```
{experiment}/
└── zarr/
    └── {region}/
        └── acquisition.zarr/
            ├── zarr.json      # OME metadata
            └── 0/             # 6D array (FOV, T, C, Z, Y, X)
```

## Array Structure

### 5D Arrays (Standard)

Shape: `(T, C, Z, Y, X)` where:
- T = number of timepoints
- C = number of channels
- Z = number of z-levels
- Y, X = image dimensions

### 6D Arrays (Non-standard)

Shape: `(FOV, T, C, Z, Y, X)` where:
- FOV = number of fields of view in the region
- Other dimensions same as 5D

## Metadata Structure (OME-NGFF 0.5)

```json
{
  "ome": {
    "version": "0.5",
    "multiscales": [{
      "version": "0.5",
      "axes": [
        {"name": "t", "type": "time", "unit": "second"},
        {"name": "c", "type": "channel"},
        {"name": "z", "type": "space", "unit": "micrometer"},
        {"name": "y", "type": "space", "unit": "micrometer"},
        {"name": "x", "type": "space", "unit": "micrometer"}
      ],
      "datasets": [{"path": "0", "coordinateTransformations": [...]}]
    }],
    "omero": {
      "version": "0.5",
      "channels": [
        {"label": "DAPI", "color": "0000FF", "window": {"start": 0, "end": 65535}},
        {"label": "GFP", "color": "00FF00", "window": {"start": 0, "end": 65535}}
      ]
    }
  },
  "_squid": {
    "structure": "5D-TCZYX",
    "pixel_size_um": 0.5,
    "compression": "fast",
    "acquisition_complete": true
  }
}
```

## Sharding and Chunks

Zarr v3 uses sharding to optimize both read and write performance:

- **Shard size**: One full frame (all z-levels for one timepoint/channel/FOV)
- **Chunk size**: Configurable via `ZARR_CHUNK_MODE`:
  - `full_frame`: Each chunk is a full image plane (simplest)
  - `tiled_512`: 512x512 pixel chunks for tiled visualization
  - `tiled_256`: 256x256 pixel chunks for fine-grained streaming

## Configuration Options

| Setting | Values | Description |
|---------|--------|-------------|
| `FILE_SAVING_OPTION` | ZARR_V3 | Enable Zarr v3 format |
| `ZARR_COMPRESSION` | none, fast, balanced, best | Compression level |
| `ZARR_CHUNK_MODE` | full_frame, tiled_512, tiled_256 | Chunk size |
| `ZARR_USE_6D_FOV_DIMENSION` | True/False | 6D array with FOV dimension |

## Live Viewing

When acquiring with Zarr v3 format, the NDViewer automatically uses the zarr push API for live viewing. This requires:

1. ndviewer_light with zarr support (PR #25)
2. The zarr stores to be accessible from the main process

See [NDViewer Tab](ndviewer-tab.md) for details on live viewing.

### Limitations

- **Multi-region 6D mode**: Only the first region is viewable during live acquisition. Reload the dataset after acquisition to view all regions.
- **Zarr support required**: If ndviewer_light doesn't have zarr support, a placeholder message will be shown instead of the live view.

## Opening Zarr Files

### Python (zarr-python)

```python
import zarr

# Open zarr v3 store
store = zarr.open("path/to/acquisition.zarr")
data = store["0"][:]  # Read entire array
```

### napari

```bash
napari path/to/acquisition.zarr
```

Or use the napari-ome-zarr plugin for full OME-NGFF support.

### TensorStore (for streaming)

```python
import tensorstore as ts

spec = {
    "driver": "zarr3",
    "kvstore": {"driver": "file", "path": "path/to/acquisition.zarr/0"},
}
store = ts.open(spec).result()
data = store.read().result()
```

## Related Documentation

- [NDViewer Tab](ndviewer-tab.md) - Live viewing during acquisition
- [Downsampled Plate View](downsampled-plate-view.md) - Overview visualization for wellplate acquisitions
