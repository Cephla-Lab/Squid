# Chunk 4: LaserAFResult and Spot Metrics

## Goal

Add structured result type for laser autofocus measurements and surface spot quality metrics from existing code. This provides the data structures needed by the real controller.

## Dependencies

- None (can be done in parallel with Phase A)

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py` | Add `LaserAFResult` dataclass |
| `software/src/squid/core/utils/hardware_utils.py` | Surface intensity/SNR metrics |

## Critical Corrections

1. **Cropped region source**: The crop is created in `_get_laser_spot_centroid()` at lines 694-705, NOT returned by `find_spot_location()`.

   **Chosen approach**: Store the crop bounds when creating the crop, then compute local coordinates for SNR calculation.

   ```python
   # In _get_laser_spot_centroid() after creating the crop:
   self._last_crop = crop
   self._last_crop_bounds = (x_start, y_start, x_end, y_end)
   ```

2. **Frame storage**: `_get_laser_spot_centroid()` already stores `self.image = image` (line 778). The full frame is available via `self.image`, and the crop bounds tell us where the crop came from.

## Deliverables

### LaserAFResult Dataclass

**NOTE**: `is_good_reading` is NOT included - focus lock controller computes this (it owns the thresholds).

```python
@dataclass
class LaserAFResult:
    """Result of a laser autofocus displacement measurement.

    NOTE: is_good_reading is computed by the focus lock controller,
    not returned here, because thresholds are controller configuration.
    """
    displacement_um: float          # Displacement from reference
    spot_intensity: float           # Peak intensity of AF spot
    spot_snr: float                 # Signal-to-noise ratio
    correlation: float | None       # Correlation with reference (0-1), None if no reference
    spot_x_px: float | None         # Spot centroid X in pixels
    spot_y_px: float | None         # Spot centroid Y in pixels
    timestamp: float                # time.monotonic() for drift calculation
```

### SNR from Existing Cropped Region

Don't compute SNR on full frame (expensive). Use existing cropped region from `find_spot_location()`:

```python
def extract_spot_metrics(
    cropped_region: np.ndarray,
    spot_x_local: float,
    spot_y_local: float,
) -> tuple[float, float, float]:
    """
    Extract spot metrics from already-cropped region.

    Uses the region already cropped by find_spot_location() to avoid
    redundant full-frame operations.

    Args:
        cropped_region: The cropped image region around the spot
        spot_x_local: X position of spot WITHIN the crop (not global)
        spot_y_local: Y position of spot WITHIN the crop (not global)

    Returns:
        (snr, peak_intensity, background_level)
    """
    # Peak intensity from small region around centroid
    half_size = 5
    x, y = int(spot_x_local), int(spot_y_local)
    x1, x2 = max(0, x - half_size), min(cropped_region.shape[1], x + half_size)
    y1, y2 = max(0, y - half_size), min(cropped_region.shape[0], y + half_size)
    peak = float(cropped_region[y1:y2, x1:x2].max())

    # Background from edges of cropped region (outer ring)
    edge_width = 3
    edges = np.concatenate([
        cropped_region[:edge_width, :].ravel(),      # top
        cropped_region[-edge_width:, :].ravel(),     # bottom
        cropped_region[:, :edge_width].ravel(),      # left
        cropped_region[:, -edge_width:].ravel(),     # right
    ])
    background = float(np.median(edges))

    # SNR calculation
    snr = (peak - background) / max(background, 1.0)

    return snr, peak, background
```

### Global to Local Coordinate Conversion

The crop bounds are stored when creating the crop (see Critical Corrections above). Use them to convert global spot coordinates to local:

```python
# In _get_laser_spot_centroid() - store crop and bounds:
self._last_crop = crop
self._last_crop_bounds = (x_start, y_start, x_end, y_end)

# Later, when computing SNR:
# Global spot coordinates from find_spot_location()
global_x, global_y = find_spot_location(frame, ...)

# Use stored crop bounds
x_start, y_start, _, _ = self._last_crop_bounds

# Local coordinates within the crop
local_x = global_x - x_start
local_y = global_y - y_start

# Now use local coordinates for SNR calculation
snr, peak, background = extract_spot_metrics(self._last_crop, local_x, local_y)
```

**Storage summary**:
- `self.image`: Full frame (already stored at line 778)
- `self._last_crop`: Cropped region around spot (NEW)
- `self._last_crop_bounds`: (x_start, y_start, x_end, y_end) tuple (NEW)

### Integration Points

- Existing `measure_displacement()` unchanged (backwards compatible)
- Metrics extracted from existing cropped region in `_get_laser_spot_centroid()`
- `LaserAFResult` will be used by continuous measurement in Chunk 5

## Testing

```bash
cd software
pytest tests/unit/squid/backend/controllers/autofocus/ -v
pytest tests/unit/squid/core/utils/ -v
```

## Completion Checklist

### LaserAFResult
- [ ] Create `LaserAFResult` dataclass
- [ ] Add all required fields with types
- [ ] Add docstrings
- [ ] **NO `is_good_reading`** - computed by focus lock controller

### Crop Storage (in LaserAutofocusController)
- [ ] Store `self._last_crop` in `_get_laser_spot_centroid()`
- [ ] Store `self._last_crop_bounds` as (x_start, y_start, x_end, y_end)
- [ ] Use stored bounds for local coordinate conversion

### SNR Helper
- [ ] Create `extract_spot_metrics()` function
- [ ] Use stored `_last_crop` (NOT full frame)
- [ ] Convert global spot coords to local using `_last_crop_bounds`
- [ ] Handle edge cases (low signal)
- [ ] Return (snr, peak_intensity, background_level)

### Testing
- [ ] Unit test: LaserAFResult instantiation
- [ ] Unit test: LaserAFResult field access
- [ ] Unit test: extract_spot_metrics with synthetic data
- [ ] Unit test: edge cases (low signal)
- [ ] Existing tests still pass

### Verification
- [ ] `cd software && pytest tests/unit/squid/backend/controllers/autofocus/` passes
- [ ] `cd software && pytest tests/unit/squid/core/utils/` passes
- [ ] Existing AF behavior unchanged
