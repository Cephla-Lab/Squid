#!/usr/bin/env python
"""Benchmark script for downsampled plate view performance.

This script simulates realistic acquisition scenarios to identify bottlenecks
in the plate view generation pipeline.

Usage:
    python scripts/benchmark_plate_view.py
"""

import tempfile
import os
import time
import sys
import logging

import numpy as np

# Setup logging to see [PERF] output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Enable DEBUG for detailed timing from downsampled view processing
logging.getLogger("squid.control.core.downsampled_views").setLevel(logging.DEBUG)

from control.core.job_processing import (
    DownsampledViewJob,
    JobRunner,
    CaptureInfo,
    JobImage,
)
from control.utils_config import ChannelMode
import squid.abc


def make_capture_info(region_id: str = "A1", fov: int = 0) -> CaptureInfo:
    """Create a CaptureInfo for testing."""
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=ChannelMode(
            id="0",
            name="BF LED matrix full",
            camera_sn="test",
            exposure_time=10.0,
            analog_gain=1.0,
            illumination_source=0,
            illumination_intensity=50.0,
            z_offset=0.0,
        ),
        save_directory="/tmp",
        file_id="test",
        region_id=region_id,
        fov=fov,
        configuration_idx=0,
    )


def benchmark_single_well(
    tile_size: int = 2048,
    num_fovs: int = 16,
    num_channels: int = 2,
    num_z: int = 1,
    skip_saving: bool = False,
):
    """Benchmark processing a single well with multiple FOVs."""
    print(f"\n{'='*70}")
    print(f"BENCHMARK: Single Well")
    print(f"  Tile size: {tile_size}x{tile_size}")
    print(f"  FOVs per well: {num_fovs} ({int(np.sqrt(num_fovs))}x{int(np.sqrt(num_fovs))} grid)")
    print(f"  Channels: {num_channels}")
    print(f"  Z-levels: {num_z}")
    print(f"  Skip saving: {skip_saving}")
    print(f"{'='*70}")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = os.path.join(tmpdir, "downsampled")
        os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

        runner = JobRunner()
        runner.daemon = True
        runner.start()

        channel_names = [f"Channel_{i}" for i in range(num_channels)]
        grid_size = int(np.sqrt(num_fovs))
        step_mm = tile_size * 0.9 / 1000.0  # Assume ~0.9um pixel size, slight overlap

        try:
            well_start = time.perf_counter()
            dispatch_times = []

            # Simulate acquiring all FOVs for all channels
            for z_idx in range(num_z):
                for ch_idx in range(num_channels):
                    for fov_idx in range(num_fovs):
                        row = fov_idx // grid_size
                        col = fov_idx % grid_size

                        # Generate random tile
                        tile = np.random.randint(0, 65535, (tile_size, tile_size), dtype=np.uint16)

                        job = DownsampledViewJob(
                            capture_info=make_capture_info(region_id="A1", fov=fov_idx),
                            capture_image=JobImage(image_array=tile),
                            well_id="A1",
                            well_row=0,
                            well_col=0,
                            fov_index=fov_idx,
                            total_fovs_in_well=num_fovs,
                            channel_idx=ch_idx,
                            total_channels=num_channels,
                            channel_name=channel_names[ch_idx],
                            fov_position_in_well=(col * step_mm, row * step_mm),
                            overlap_pixels=(50, 50, 50, 50),  # Simulate overlap
                            pixel_size_um=0.9,
                            target_resolutions_um=[5.0, 10.0, 20.0],
                            plate_resolution_um=10.0,
                            output_dir=output_dir,
                            channel_names=channel_names,
                            z_index=z_idx,
                            total_z_levels=num_z,
                            skip_saving=skip_saving,
                        )

                        t_dispatch = time.perf_counter()
                        runner.dispatch(job)
                        dispatch_times.append(time.perf_counter() - t_dispatch)

            dispatch_end = time.perf_counter()

            # Wait for result to ensure all jobs complete
            runner.output_queue().get(timeout=120.0)
            result_time = time.perf_counter()

            total_time = result_time - well_start
            dispatch_total = dispatch_end - well_start
            processing_time = result_time - dispatch_end

            print(f"\n--- TIMING SUMMARY ---")
            print(f"Total FOV dispatches: {num_fovs * num_channels * num_z}")
            print(f"Dispatch time (all jobs): {dispatch_total*1000:.1f}ms")
            print(f"  Avg per dispatch: {np.mean(dispatch_times)*1000:.2f}ms")
            print(f"Processing time (worker): {processing_time*1000:.1f}ms")
            print(f"TOTAL time: {total_time*1000:.1f}ms")

            # Estimate stitched size
            stitched_width = grid_size * (tile_size - 100)  # Account for overlap crop
            stitched_height = grid_size * (tile_size - 100)
            print(f"\nEstimated stitched size: ~{stitched_height}x{stitched_width} pixels")
            print(f"  = {stitched_height * stitched_width * 2 / 1e6:.1f} MB per channel (uint16)")

        finally:
            runner.shutdown(timeout_s=5.0)


def benchmark_plate_acquisition(
    num_wells: int = 8,
    tile_size: int = 1024,
    num_fovs: int = 4,
    num_channels: int = 1,
    skip_saving: bool = True,
):
    """Benchmark processing multiple wells (simulating plate acquisition)."""
    print(f"\n{'='*70}")
    print(f"BENCHMARK: Plate Acquisition ({num_wells} wells)")
    print(f"  Tile size: {tile_size}x{tile_size}")
    print(f"  FOVs per well: {num_fovs}")
    print(f"  Channels: {num_channels}")
    print(f"  Skip saving: {skip_saving}")
    print(f"{'='*70}")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = os.path.join(tmpdir, "downsampled")
        os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

        runner = JobRunner()
        runner.daemon = True
        runner.start()

        channel_names = [f"Channel_{i}" for i in range(num_channels)]
        grid_size = int(np.sqrt(num_fovs))
        step_mm = tile_size * 0.9 / 1000.0

        well_timings = []

        try:
            plate_start = time.perf_counter()

            for well_idx in range(num_wells):
                well_id = f"{chr(ord('A') + well_idx)}1"
                well_start = time.perf_counter()

                # Dispatch all FOVs for this well
                for ch_idx in range(num_channels):
                    for fov_idx in range(num_fovs):
                        row = fov_idx // grid_size
                        col = fov_idx % grid_size

                        tile = np.random.randint(0, 65535, (tile_size, tile_size), dtype=np.uint16)

                        job = DownsampledViewJob(
                            capture_info=make_capture_info(region_id=well_id, fov=fov_idx),
                            capture_image=JobImage(image_array=tile),
                            well_id=well_id,
                            well_row=well_idx,
                            well_col=0,
                            fov_index=fov_idx,
                            total_fovs_in_well=num_fovs,
                            channel_idx=ch_idx,
                            total_channels=num_channels,
                            channel_name=channel_names[ch_idx],
                            fov_position_in_well=(col * step_mm, row * step_mm),
                            overlap_pixels=(25, 25, 25, 25),
                            pixel_size_um=0.9,
                            target_resolutions_um=[10.0],
                            plate_resolution_um=10.0,
                            output_dir=output_dir,
                            channel_names=channel_names,
                            skip_saving=skip_saving,
                        )
                        runner.dispatch(job)

                # Wait for this well's result
                runner.output_queue().get(timeout=60.0)
                well_end = time.perf_counter()

                well_time = (well_end - well_start) * 1000
                well_timings.append(well_time)
                print(f"  Well {well_id}: {well_time:.1f}ms")

            plate_end = time.perf_counter()

            print(f"\n--- PLATE TIMING SUMMARY ---")
            print(f"Wells processed: {num_wells}")
            print(f"Mean well time: {np.mean(well_timings):.1f}ms")
            print(f"Max well time: {np.max(well_timings):.1f}ms")
            print(f"Min well time: {np.min(well_timings):.1f}ms")
            print(f"Total plate time: {(plate_end - plate_start)*1000:.1f}ms")

        finally:
            runner.shutdown(timeout_s=5.0)


def benchmark_comparison_saving_vs_nosaving():
    """Compare timing with and without TIFF saving."""
    print(f"\n{'='*70}")
    print("BENCHMARK: Saving vs No-Saving Comparison")
    print(f"{'='*70}")

    tile_size = 1024
    num_fovs = 9  # 3x3

    for skip_saving in [True, False]:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            grid_size = 3
            step_mm = tile_size * 0.9 / 1000.0

            try:
                start = time.perf_counter()

                for fov_idx in range(num_fovs):
                    row = fov_idx // grid_size
                    col = fov_idx % grid_size
                    tile = np.random.randint(0, 65535, (tile_size, tile_size), dtype=np.uint16)

                    job = DownsampledViewJob(
                        capture_info=make_capture_info(region_id="A1", fov=fov_idx),
                        capture_image=JobImage(image_array=tile),
                        well_id="A1",
                        well_row=0,
                        well_col=0,
                        fov_index=fov_idx,
                        total_fovs_in_well=num_fovs,
                        channel_idx=0,
                        total_channels=1,
                        channel_name="BF",
                        fov_position_in_well=(col * step_mm, row * step_mm),
                        overlap_pixels=(25, 25, 25, 25),
                        pixel_size_um=0.9,
                        target_resolutions_um=[5.0, 10.0, 20.0],
                        plate_resolution_um=10.0,
                        output_dir=output_dir,
                        channel_names=["BF"],
                        skip_saving=skip_saving,
                    )
                    runner.dispatch(job)

                runner.output_queue().get(timeout=60.0)
                elapsed = (time.perf_counter() - start) * 1000

                label = "NO saving" if skip_saving else "WITH saving (3 resolutions)"
                print(f"  {label}: {elapsed:.1f}ms")

            finally:
                runner.shutdown(timeout_s=5.0)


def benchmark_full_96_well_plate(
    tile_size: int = 2048,
    num_fovs: int = 4,
    num_channels: int = 1,
    skip_saving: bool = True,
):
    """Benchmark processing a full 96-well plate (8 rows x 12 cols)."""
    print(f"\n{'='*70}")
    print(f"BENCHMARK: Full 96-Well Plate")
    print(f"  Tile size: {tile_size}x{tile_size}")
    print(f"  FOVs per well: {num_fovs} ({int(np.sqrt(num_fovs))}x{int(np.sqrt(num_fovs))} grid)")
    print(f"  Channels: {num_channels}")
    print(f"  Skip saving: {skip_saving}")
    print(f"  Total wells: 96")
    print(f"  Total images: {96 * num_fovs * num_channels}")
    print(f"{'='*70}")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = os.path.join(tmpdir, "downsampled")
        os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

        runner = JobRunner()
        runner.daemon = True
        runner.start()

        channel_names = [f"Channel_{i}" for i in range(num_channels)]
        grid_size = int(np.sqrt(num_fovs))
        step_mm = tile_size * 0.9 / 1000.0

        well_timings = []
        rows = 8  # A-H
        cols = 12  # 1-12

        try:
            plate_start = time.perf_counter()

            for row_idx in range(rows):
                for col_idx in range(cols):
                    well_id = f"{chr(ord('A') + row_idx)}{col_idx + 1}"
                    well_start = time.perf_counter()

                    # Dispatch all FOVs for this well
                    for ch_idx in range(num_channels):
                        for fov_idx in range(num_fovs):
                            fov_row = fov_idx // grid_size
                            fov_col = fov_idx % grid_size

                            tile = np.random.randint(0, 65535, (tile_size, tile_size), dtype=np.uint16)

                            job = DownsampledViewJob(
                                capture_info=make_capture_info(region_id=well_id, fov=fov_idx),
                                capture_image=JobImage(image_array=tile),
                                well_id=well_id,
                                well_row=row_idx,
                                well_col=col_idx,
                                fov_index=fov_idx,
                                total_fovs_in_well=num_fovs,
                                channel_idx=ch_idx,
                                total_channels=num_channels,
                                channel_name=channel_names[ch_idx],
                                fov_position_in_well=(fov_col * step_mm, fov_row * step_mm),
                                overlap_pixels=(50, 50, 50, 50),
                                pixel_size_um=0.9,
                                target_resolutions_um=[10.0],  # Only plate resolution
                                plate_resolution_um=10.0,
                                output_dir=output_dir,
                                channel_names=channel_names,
                                skip_saving=skip_saving,
                            )
                            runner.dispatch(job)

                    # Wait for this well's result
                    runner.output_queue().get(timeout=120.0)
                    well_end = time.perf_counter()

                    well_time = (well_end - well_start) * 1000
                    well_timings.append(well_time)

                # Print progress after each row
                row_letter = chr(ord("A") + row_idx)
                row_times = well_timings[-cols:]
                print(f"  Row {row_letter}: mean={np.mean(row_times):.1f}ms, max={np.max(row_times):.1f}ms")

            plate_end = time.perf_counter()

            print(f"\n{'='*70}")
            print(f"96-WELL PLATE TIMING SUMMARY")
            print(f"{'='*70}")
            print(f"Total wells processed: {len(well_timings)}")
            print(f"Total images processed: {len(well_timings) * num_fovs * num_channels}")
            print(f"\nPer-well timing:")
            print(f"  Mean: {np.mean(well_timings):.1f}ms")
            print(f"  Median: {np.median(well_timings):.1f}ms")
            print(f"  Min: {np.min(well_timings):.1f}ms")
            print(f"  Max: {np.max(well_timings):.1f}ms")
            print(f"  Std: {np.std(well_timings):.1f}ms")
            print(f"\nTotal plate processing time: {(plate_end - plate_start):.2f}s")
            print(f"Throughput: {len(well_timings) / (plate_end - plate_start):.1f} wells/second")

            # Identify slow wells
            slow_threshold = np.mean(well_timings) + 2 * np.std(well_timings)
            slow_wells = [(i, t) for i, t in enumerate(well_timings) if t > slow_threshold]
            if slow_wells:
                print(f"\nSlow wells (>{slow_threshold:.0f}ms):")
                for idx, t in slow_wells:
                    row = idx // cols
                    col = idx % cols
                    well_id = f"{chr(ord('A') + row)}{col + 1}"
                    print(f"  {well_id}: {t:.1f}ms")

        finally:
            runner.shutdown(timeout_s=5.0)


if __name__ == "__main__":
    print("=" * 70)
    print("DOWNSAMPLED PLATE VIEW PERFORMANCE BENCHMARK")
    print("=" * 70)

    # Check for command line args
    if len(sys.argv) > 1 and sys.argv[1] == "96well":
        # Run only 96-well plate benchmark
        benchmark_full_96_well_plate(
            tile_size=2048,
            num_fovs=4,  # 2x2 grid per well
            num_channels=1,
            skip_saving=True,
        )
    else:
        # Run standard benchmarks
        # Benchmark 1: Single well with realistic parameters
        benchmark_single_well(
            tile_size=2048,
            num_fovs=16,  # 4x4 grid
            num_channels=2,
            num_z=1,
            skip_saving=True,
        )

        # Benchmark 2: Single well with saving enabled
        benchmark_single_well(
            tile_size=2048,
            num_fovs=16,
            num_channels=1,
            num_z=1,
            skip_saving=False,
        )

        # Benchmark 3: Plate acquisition simulation
        benchmark_plate_acquisition(
            num_wells=8,
            tile_size=1024,
            num_fovs=4,  # 2x2
            num_channels=1,
            skip_saving=True,
        )

        # Benchmark 4: Saving comparison
        benchmark_comparison_saving_vs_nosaving()

    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETE")
    print("=" * 70)
