# Session Notes: 2026-01-25 - Zarr Overhead Optimization

## Summary

Investigated and fixed TIFF vs Zarr acquisition overhead. After optimization, Zarr mode has comparable performance to TIFF mode.

## Changes Made

### 1. Committed: OME-NGFF Path Structure Fix (d5a9d6f8)

Separated GROUP and ARRAY paths for OME-NGFF compliance:
- `build_hcs_zarr_fov_path()` returns GROUP path (field level)
- `build_per_fov_zarr_path()` returns GROUP path
- `ZarrWriterInfo.get_output_path()` appends `/0` for ARRAY path

### 2. Pending (ndviewer_light submodule - needs separate repo commit)

File: `control/ndviewer_light/ndviewer_light/core.py`

Changes:
- Removed blocking `open_zarr_tensorstore()` call in `start_zarr_acquisition()`
- Added `cache.invalidate()` method for O(1) single-entry removal
- Fixed cache race condition with `was_written_before_read` tracking

## Performance Results

| Metric | TIFF | ZARR (optimized) |
|--------|------|------------------|
| Start overhead | 135ms | 138ms |
| Acquisition time | 11.604s | 11.555s |
| Total time | 13.686s | 13.696s |

## Pending Issues

See `qmetaobject-invokemethod-error.md` for a cosmetic error that needs fixing in a separate PR.

## Testing Methodology

Added to CLAUDE.md - see "Performance Testing" section for how to run TIFF vs Zarr comparison tests.
