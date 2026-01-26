# NDViewer Light - Zarr Notification Timing Fix

## Summary

Fix black image bug in 6D zarr mode where FOV 0 would display black for all or some channels. The root cause was that `notify_zarr_frame` was called when frames were dispatched to the subprocess (before write), not when writes completed.

**Main repo changes** (already committed): Move notification to job result processing so it fires AFTER subprocess confirms write is complete.

**NDViewer changes** (this document): Simplify store handling and ensure TensorStore sees fresh data via recheck options.

## Changes Required

### File: `ndviewer_light/core.py`

#### 1. Add TensorStore recheck options (line ~927)

In `open_zarr_tensorstore()`, add options to revalidate cached data on each read:

```python
spec = {
    "driver": driver,
    "kvstore": {"driver": "file", "path": str(full_path)},
    # Revalidate cache on access to see fresh writes from other processes
    "recheck_cached_metadata": True,
    "recheck_cached_data": True,
}
```

**Why:** With these options, TensorStore checks if data changed on disk before returning cached values. This is much cheaper than reopening the entire store for every read.

#### 2. Remove unused state variables (line ~1418)

Remove these class attributes that are no longer needed:

```python
# REMOVE these lines from class definition:
_zarr_acquisition_path: Optional[Path]
_zarr_acquisition_store: Optional[Any]  # zarr.Array
```

Also remove their initialization in `__init__`:

```python
# REMOVE these lines:
self._zarr_acquisition_path: Optional[Path] = None
self._zarr_acquisition_store = None  # zarr.Array handle
```

#### 3. Update `start_zarr_acquisition()` signature (line ~2122)

Change from:
```python
def start_zarr_acquisition(
    self,
    zarr_path: str,
    channels: List[str],
    num_z: int,
    fov_labels: List[str],
    height: int,
    width: int,
    fov_paths: Optional[List[str]] = None,
):
```

To:
```python
def start_zarr_acquisition(
    self,
    fov_paths: List[str],
    channels: List[str],
    num_z: int,
    fov_labels: List[str],
    height: int,
    width: int,
):
```

This is now strictly for per-FOV 5D mode. Update docstring accordingly.

#### 4. Simplify `start_zarr_acquisition()` body

Remove the `zarr_path` handling and make `fov_paths` required:

```python
# Validate inputs
if not fov_paths:
    raise ValueError("fov_paths must not be empty")

# ... later in the method, remove the if/else branch for fov_paths ...

# Validate and set per-FOV paths
if len(fov_paths) != len(fov_labels):
    logger.warning(
        f"fov_paths length ({len(fov_paths)}) does not match "
        f"fov_labels length ({len(fov_labels)}), truncating to shorter"
    )
    min_len = min(len(fov_paths), len(fov_labels))
    fov_paths = list(fov_paths)[:min_len]
    fov_labels = list(fov_labels)[:min_len]
    self._fov_labels = fov_labels
self._zarr_fov_paths = [Path(p) for p in fov_paths]
```

#### 5. Keep store caching in `_load_zarr_plane_6d()` (line ~2545)

**DO NOT reopen stores for every read.** Keep the existing lazy-open-and-cache pattern. The `recheck_cached_*` options in `open_zarr_tensorstore()` ensure fresh data is visible:

```python
# Open store once, cache for reuse (recheck options handle fresh data)
with self._zarr_stores_lock:
    if region_idx not in self._zarr_region_stores:
        region_path = self._zarr_region_paths[region_idx]
        logger.debug(f"Opening zarr store for region {region_idx}: {region_path}")
        ts_arr = open_zarr_tensorstore(region_path, array_path="")
        if ts_arr is None:
            logger.debug(f"Zarr store not accessible for region {region_idx}")
            return np.zeros((self._image_height, self._image_width), dtype=np.uint16)
        self._zarr_region_stores[region_idx] = ts_arr
    arr = self._zarr_region_stores[region_idx]

# Read plane - TensorStore's recheck options ensure we see fresh data
plane = arr[local_fov_idx, t, channel_idx, z, :, :].read().result()
```

#### 6. Keep store caching in `_load_zarr_plane()` similarly (line ~2619)

Same pattern - open once, cache, rely on recheck options:

```python
# Open store once, cache for reuse
with self._zarr_stores_lock:
    if fov_idx not in self._zarr_fov_stores:
        fov_path = self._zarr_fov_paths[fov_idx]
        ts_arr = open_zarr_tensorstore(fov_path, array_path="0")
        if ts_arr is None:
            logger.debug(f"Zarr store not accessible for FOV {fov_idx}")
            return np.zeros((self._image_height, self._image_width), dtype=np.uint16)
        self._zarr_fov_stores[fov_idx] = ts_arr
    arr = self._zarr_fov_stores[fov_idx]
```

Also remove the "single-store mode" branch since it's no longer used.

#### 7. Add zarr.json existence check for 6D mode (line ~2696)

Before loading, check if the store is ready:

```python
# Check if store is ready for 6D regions mode
if self._zarr_6d_regions_mode and self._zarr_region_paths:
    region_idx, _ = self._global_to_region_fov(fov_idx)
    if region_idx < len(self._zarr_region_paths):
        zarr_json = self._zarr_region_paths[region_idx] / "zarr.json"
        if not zarr_json.exists():
            # Store not ready yet, retry later
            if self._zarr_acquisition_active:
                QTimer.singleShot(500, self._load_current_zarr_fov)
            return
```

#### 8. Update `is_zarr_push_mode()` (line ~2742)

Remove reference to removed `_zarr_acquisition_path`:

```python
def is_zarr_push_mode(self) -> bool:
    """Check if zarr push-based mode is active."""
    return (
        self._zarr_acquisition_active
        or bool(self._zarr_fov_paths)
        or self._zarr_6d_regions_mode
    )
```

#### 9. Update `stop_zarr_acquisition()` (line ~2782)

Remove cleanup of removed variables:

```python
# Clean up zarr state
self._zarr_acquisition_active = False
with self._zarr_stores_lock:
    self._zarr_fov_stores.clear()
    self._zarr_region_stores.clear()
self._zarr_fov_paths = []
# Clean up 6d_regions state
self._zarr_region_paths = []
```

## Why This Works

The notification timing fix in the main repo ensures:

```
1. Subprocess: write_frame() blocks until data ON DISK
2. Subprocess: job completes, returns ZarrWriteResult
3. Main process: receives result, emits notification
4. Viewer: invalidates cache, schedules debounced load (200ms)
5. Viewer: reads plane (data was written 200ms+ ago)
```

With `recheck_cached_metadata: True` and `recheck_cached_data: True`, TensorStore verifies data freshness on each read without the overhead of reopening stores.

## Testing

After applying these changes:
1. Run a 6D zarr acquisition with 4+ FOVs
2. Verify FOV 0 displays correctly (not black) for all channels
3. Verify navigation works during and after acquisition
4. Verify no performance regression from store reopening
