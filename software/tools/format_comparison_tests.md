# Storage Format Comparison Tests

## Test Configuration

Use the same acquisition parameters for all tests:
- **Wells**: B2, B3 (2 wells)
- **Z-stack**: Nz=3, delta_z=0.0015mm
- **Time series**: Nt=1 (single timepoint for fairness)
- **Channels**: 3 (BF LED, 405nm, 488nm)
- **Exposure**: 20ms per channel
- **Binning**: 2x2

This gives ~72 images per acquisition (2 wells × ~4 FOVs × 3z × 1t × 3ch).

## Tests to Run

### Test 1: Individual TIFF (Baseline)
1. Use current master branch
2. Settings > Preferences > General > File Saving Format: `INDIVIDUAL_IMAGES`
3. Run acquisition with above parameters
4. Note: Save to `/Volumes/Extreme SSD/test_tiff_{timestamp}/`

### Test 2: Zarr NONE (No Compression)
1. Use `zarr-v3-support` worktree
2. Settings > Preferences > General > File Saving Format: `ZARR_V3`
3. Settings > Preferences > General > Zarr Compression: `NONE`
4. Run acquisition with same parameters
5. Note: Save to `/Volumes/Extreme SSD/test_zarr_none_{timestamp}/`

### Test 3: Zarr FAST (LZ4 Compression)
1. Use `zarr-v3-support` worktree
2. Settings > Preferences > General > File Saving Format: `ZARR_V3`
3. Settings > Preferences > General > Zarr Compression: `FAST`
4. Run acquisition with same parameters
5. Note: Save to `/Volumes/Extreme SSD/test_zarr_fast_{timestamp}/`

### Test 4: Zarr BALANCED (Zstd Compression)
1. Use `zarr-v3-support` worktree
2. Settings > Preferences > General > File Saving Format: `ZARR_V3`
3. Settings > Preferences > General > Zarr Compression: `BALANCED`
4. Run acquisition with same parameters
5. Note: Save to `/Volumes/Extreme SSD/test_zarr_balanced_{timestamp}/`

## Running Tests

### Using the zarr-v3-support worktree:
```bash
cd "$SQUID_ROOT/worktrees/zarr-v3-support/software"
source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid
python3 main_hcs.py --simulation
```

### Using the main branch (for TIFF baseline):
```bash
cd "$SQUID_ROOT/software"
source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid
python3 main_hcs.py --simulation
```

## Analyzing Results

After running all tests, compare results:

```bash
cd "$SQUID_ROOT/software"
source /opt/miniconda3/etc/profile.d/conda.sh && conda activate squid

# Compare all test results
python tools/analyze_acquisition_logs.py \
  "/Volumes/Extreme SSD/test_tiff_*" \
  "/Volumes/Extreme SSD/test_zarr_none_*" \
  "/Volumes/Extreme SSD/test_zarr_fast_*" \
  "/Volumes/Extreme SSD/test_zarr_balanced_*" \
  --compare
```

## Expected Results

Based on preliminary analysis:

| Format | Expected Speed | Compression | RAM Usage |
|--------|---------------|-------------|-----------|
| Individual TIFF | Baseline | None | High (~3GB) |
| Zarr NONE | Similar to TIFF | None | Lower (~1.5GB) |
| Zarr FAST | 10-20% slower | ~2x | Lower (~1.5GB) |
| Zarr BALANCED | 20-30% slower | ~3-4x | Lower (~1.5GB) |

Note: Zarr has backpressure management overhead but uses significantly less RAM.

## Metrics to Compare

1. **Total acquisition time** (seconds)
2. **Images per second** (throughput)
3. **Milliseconds per image** (latency)
4. **Peak RAM usage** (MB)
5. **Output file size** (compression ratio)
