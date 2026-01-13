# Simulated Disk I/O Mode

**Our Commit:** a8121d30
**Date:** 2026-01-12
**Status:** PORTED

## Upstream Commits Ported

| Hash | Title |
|------|-------|
| b91694f1 | feat: Add simulated disk I/O mode for development |

## Summary

Adds a development mode that simulates disk I/O without actually writing files. Images are encoded to memory buffers (exercising RAM/CPU realistically) then throttled and discarded. Useful for testing acquisition throughput without SSD wear.

## Files Created/Modified

### Created
- `backend/io/io_simulation.py` (181 lines) - Core simulation module
- `tests/unit/backend/io/test_io_simulation.py` (244 lines) - **18 tests**

### Modified
- `src/_def.py` - Configuration constants
- `backend/controllers/multipoint/job_processing.py` - Early-return for SaveImageJob/SaveOMETiffJob
- `backend/controllers/multipoint/multi_point_worker.py` - Skip downsampled saves
- `ui/widgets/config.py` - Development Settings UI
- `ui/gui/layout_builder.py` - Warning banner helper
- `ui/main_window.py` - Warning banner and startup dialog
- `main_hcs.py` - Startup warning dialog

## Configuration

```python
SIMULATED_DISK_IO_ENABLED = False
SIMULATED_DISK_IO_SPEED_MB_S = 200.0  # Target write speed
SIMULATED_DISK_IO_COMPRESSION = True  # Exercise compression CPU/RAM
```

## Key Features

### Simulation Module
```python
def simulated_tiff_write(image: np.ndarray) -> int:
    """Encode to buffer, throttle, discard. Returns bytes."""
    buffer = BytesIO()
    tifffile.imwrite(buffer, image, compression="lzw")
    bytes_written = buffer.tell()
    throttle_for_speed(bytes_written, speed_mb_s)
    return bytes_written
```

### Job Processing
```python
class SaveImageJob(Job):
    def run(self) -> bool:
        if is_simulation_enabled():
            bytes_written = simulated_tiff_write(image)
            return True  # Early return, no disk write
        # ... normal save logic
```

### UI Warnings
- **Startup dialog:** Warning message when application starts
- **Banner:** Persistent red banner at top of main window
- **Settings:** Development Settings section in Preferences > Advanced

## Speed Presets

| Disk Type | Speed |
|-----------|-------|
| HDD | 50-100 MB/s |
| SATA SSD | 200-500 MB/s |
| NVMe | 1000-3000 MB/s |

## Tests

**File:** `tests/unit/backend/io/test_io_simulation.py`
**Count:** 18 tests

Covers:
- Configuration functions
- Throttle delay calculation
- TIFF encoding to buffer
- OME-TIFF stack tracking
- Compression toggle

## Audit

- [x] Logic matches upstream
- [x] arch_v2 patterns followed
- [x] Lazy imports to avoid circular dependencies
- [x] Tests added (18 tests)
