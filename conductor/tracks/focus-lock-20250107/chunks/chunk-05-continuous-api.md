# Chunk 5: Continuous Measurement API

## Goal

Add `measure_displacement_continuous()` method and measurement lock to the laser autofocus controller. This is the fast-path measurement for continuous focus lock.

## Dependencies

- Chunk 4 (LaserAFResult)

## Files to Modify

| File | Changes |
|------|---------|
| `software/src/squid/backend/controllers/autofocus/laser_auto_focus_controller.py` | Add continuous method and lock |

## Critical Corrections

1. **Use correct camera API**: `CameraService.get_acquisition_mode()` / `read_frame()` (NOT non-existent `get_trigger_mode()/get_frame()`)

2. **Correct attribute name**: LaserAutofocusController uses `self._camera_service` (NOT `self._focus_camera_service`)

3. **Don't disable callbacks**: Unlike `_get_laser_spot_centroid()`, the continuous method should NOT disable camera callbacks. It assumes the focus camera is already streaming.

4. **Don't duplicate reference storage**: `LaserAFConfig.reference_image` already exists. Use it.

5. **Return `float("nan")` not `None`**: Existing callers use `math.isnan()`.

6. **Store frame for preview**: The method MUST store the frame in `self.image` (same as `_get_laser_spot_centroid()`) so that the preview handler in Chunk 11 can access it.

7. **Buffer contention**: When polling via `read_frame()` while callbacks are enabled, there may be buffer contention. Validate that `read_frame()` is safe in this mode or consider adding a "latest frame" cache if needed. The current implementation assumes the camera SDK handles this correctly.

## Design Decision: Polling vs Stream

**Chosen approach: Polling** - simpler to implement, read latest frame directly.

The key difference from existing `_get_laser_spot_centroid()`:
- Does NOT disable camera callbacks (continuous operation)
- Does NOT toggle laser (caller manages)
- Uses software trigger if needed, then reads frame

## Deliverables

### Measurement Lock

```python
class LaserAutofocusController:
    def __init__(self, ...):
        ...
        self._measurement_lock = threading.Lock()
```

### Continuous Measurement Method

```python
def measure_displacement_continuous(self) -> LaserAFResult:
    """
    Measure displacement assuming laser is already ON.

    This is the fast-path for continuous focus lock:
    - Does NOT toggle laser (caller manages laser state)
    - Does NOT publish LaserAFDisplacementMeasured event
    - Uses CameraService API (not non-existent get_frame())
    - Returns structured LaserAFResult with metrics

    Thread-safe via _measurement_lock.

    Returns:
        LaserAFResult with displacement and metrics.
        displacement_um will be nan if measurement failed.
    """
    with self._measurement_lock:
        # Use correct camera API (CameraAcquisitionMode from squid.core.abc)
        from squid.core.abc import CameraAcquisitionMode
        acquisition_mode = self._camera_service.get_acquisition_mode()
        if acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            self._camera_service.send_trigger()

        # Get frame via correct API
        frame = self._camera_service.read_frame()

        # Store frame for preview (Chunk 11)
        self.image = frame
        if frame is None:
            return LaserAFResult(
                displacement_um=float("nan"),
                spot_intensity=0.0,
                spot_snr=0.0,
                correlation=None,
                spot_x_px=None,
                spot_y_px=None,
                timestamp=time.monotonic(),
            )

        # Compute displacement and metrics using shared helper
        # NOTE: Do NOT call _get_laser_spot_centroid() directly - it disables callbacks.
        # Instead, extract the spot detection logic into a callback-safe helper:
        result = self._detect_spot_and_compute_displacement(frame)
        if result is None:
            return LaserAFResult(
                displacement_um=float("nan"),
                spot_intensity=0.0,
                spot_snr=0.0,
                correlation=None,
                spot_x_px=None,
                spot_y_px=None,
                timestamp=time.monotonic(),
            )

        displacement_um, spot_x, spot_y, snr, intensity, correlation = result

        # Store crop bounds for SNR calculation (see Chunk 4)
        # These are computed during spot detection

        return LaserAFResult(
            displacement_um=displacement_um,
            spot_intensity=intensity,
            spot_snr=snr,
            correlation=correlation,
            spot_x_px=spot_x,
            spot_y_px=spot_y,
            timestamp=time.monotonic(),
        )
```

### Shared Spot Detection Helper

Extract callback-safe spot detection logic from `_get_laser_spot_centroid()`:

```python
def _detect_spot_and_compute_displacement(
    self, frame: np.ndarray
) -> tuple[float, float, float, float, float, float | None] | None:
    """Callback-safe spot detection and displacement calculation.

    This is the shared logic used by both:
    - _get_laser_spot_centroid() (disables callbacks, toggles laser)
    - measure_displacement_continuous() (callbacks stay enabled, laser already on)

    Returns:
        (displacement_um, spot_x, spot_y, snr, intensity, correlation) or None if failed
    """
    # Crop around reference position
    center_x = int(self.laser_af_properties.x_reference)
    center_y = int(frame.shape[0] / 2)
    crop_size = self.laser_af_properties.spot_crop_size

    x_start = max(0, center_x - crop_size // 2)
    y_start = max(0, center_y - crop_size // 2)
    x_end = min(frame.shape[1], center_x + crop_size // 2)
    y_end = min(frame.shape[0], center_y + crop_size // 2)

    crop = frame[y_start:y_end, x_start:x_end]

    # Store crop and bounds for SNR calculation (Chunk 4)
    self._last_crop = crop
    self._last_crop_bounds = (x_start, y_start, x_end, y_end)

    # Find spot using existing find_spot_location()
    spot_result = find_spot_location(crop, ...)
    if spot_result is None:
        return None

    local_x, local_y = spot_result
    global_x = local_x + x_start
    global_y = local_y + y_start

    # Compute displacement from reference
    displacement_um = (global_x - self.laser_af_properties.x_reference) * self.laser_af_properties.pixel_to_um

    # Compute SNR using stored crop (Chunk 4)
    snr, intensity, _ = extract_spot_metrics(crop, local_x, local_y)

    # Compute correlation if reference exists
    correlation = self._compute_correlation(crop) if self.laser_af_properties.has_reference else None

    return (displacement_um, global_x, global_y, snr, intensity, correlation)
```

**Key difference from `_get_laser_spot_centroid()`**: This helper does NOT disable callbacks or toggle the laser. The caller manages those.

### Use Existing Reference

Don't duplicate - use `LaserAFConfig.reference_image`:

```python
def _compute_correlation(self, frame: np.ndarray) -> float | None:
    """Compute correlation with reference image."""
    if self._laser_af_properties.reference_image is None:
        return None
    # Compute normalized cross-correlation
    ...
```

### Wrap Existing Method with Lock

```python
def measure_displacement(self) -> float:
    """Existing single-shot method - now acquires lock.

    Returns nan (not None) if lock cannot be acquired, to match
    existing callers that use math.isnan().
    """
    if not self._measurement_lock.acquire(timeout=0.1):
        self._log.warning("Measurement blocked - continuous lock is running")
        return float("nan")
    try:
        # Existing implementation unchanged
        ...
    finally:
        self._measurement_lock.release()
```

## Testing

```bash
cd software
pytest tests/unit/squid/backend/controllers/autofocus/ -v
pytest tests/integration/ -k "laser_auto" -v
```

## Completion Checklist

### Measurement Lock
- [ ] Add `_measurement_lock: threading.Lock`
- [ ] Wrap `measure_displacement()` with lock
- [ ] Wrap `measure_displacement_continuous()` with lock
- [ ] Return `float("nan")` (not `None`) when blocked

### Shared Spot Detection Helper
- [ ] Extract `_detect_spot_and_compute_displacement()` from `_get_laser_spot_centroid()`
- [ ] Helper does NOT disable callbacks or toggle laser
- [ ] Helper stores `_last_crop` and `_last_crop_bounds` for SNR calculation
- [ ] Helper computes: displacement, spot coords, SNR, intensity, correlation
- [ ] Refactor `_get_laser_spot_centroid()` to use shared helper

### Continuous Measurement (Polling Approach)
- [ ] Create `measure_displacement_continuous()` method
- [ ] Use correct camera API: `get_acquisition_mode()` / `read_frame()`
- [ ] Use correct attribute name: `self._camera_service` (NOT `self._focus_camera_service`)
- [ ] Do NOT disable camera callbacks (unlike `_get_laser_spot_centroid()`)
- [ ] Store frame in `self.image` for preview handler (Chunk 11)
- [ ] Call `_detect_spot_and_compute_displacement()` for spot detection
- [ ] Return structured LaserAFResult (nan for failed measurement)
- [ ] Validate `read_frame()` is safe with callbacks enabled (or add frame cache if needed)

### Reference Management
- [ ] Use existing `LaserAFConfig.reference_image` (don't duplicate)
- [ ] Compute correlation when reference exists
- [ ] Clear reference via existing `set_reference()` / `clear_reference()`

### Testing
- [ ] Unit test: Continuous measurement returns LaserAFResult
- [ ] Unit test: Measurement lock prevents concurrent access
- [ ] Unit test: Returns nan when blocked
- [ ] Integration test: Measure with laser already on
- [ ] Existing single-shot tests still pass

### Verification
- [ ] `cd software && pytest tests/unit/squid/backend/controllers/autofocus/` passes
- [ ] `cd software && pytest tests/integration/` passes
- [ ] Existing AF behavior unchanged
