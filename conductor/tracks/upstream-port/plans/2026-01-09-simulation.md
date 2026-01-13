# Simulation & Development Suite

**Status:** COMPLETED
**Ported:** 2026-01-12

## Upstream Commits

- [x] `5ad9252a` - fix: Regenerate SimulatedCamera frame when binning changes
  - **Our Commit:** 6c9cb672
  - **Tracking:** `commits/19-6c9cb672-simulation-throttling-ui.md`

- [x] `b91694f1` - feat: Add simulated disk I/O mode for development
  - **Our Commit:** a8121d30
  - **Tracking:** `commits/25-a8121d30-simulated-disk-io.md`

## Implementation Checklist

### SimulatedCamera Binning Fix (5ad9252a)
- [x] Modify `backend/drivers/cameras/simulated.py`
- [x] Invalidate frame cache in set_binning()
- [x] Verify frame regeneration on binning change

### Simulated Disk I/O (b91694f1)
- [x] Add SIMULATED_DISK_IO_* constants to _def.py
- [x] Create `backend/io/io_simulation.py`
- [x] Implement simulated_tiff_write() with throttling
- [x] Implement simulated_ome_tiff_write() with stack tracking
- [x] Add early-return in SaveImageJob.run()
- [x] Add early-return in SaveOMETiffJob.run()
- [x] Skip downsampled saves when simulating
- [x] Add Development Settings to Preferences > Advanced
- [x] Add startup warning dialog
- [x] Add red warning banner in main window
- [x] Add tests (18 tests)

## Notes

Simulated disk I/O encodes images to BytesIO buffers (exercising RAM/CPU) then throttles based on configured speed and discards. No files are written to disk.
