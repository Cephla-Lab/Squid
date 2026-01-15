#!/usr/bin/env python3
"""
Manual imaging workflow testing script.

This script allows interactive testing of imaging acquisition workflows
without the full orchestrator. Useful for testing tiled imaging, z-stacks,
and multi-region acquisitions.

Usage:
    # From software directory:
    python -m tests.e2e.manual.run_imaging --grid 3x3 --zstack 5

    # Single FOV
    python -m tests.e2e.manual.run_imaging --single-fov

    # 2x2 grid with 3-plane z-stack
    python -m tests.e2e.manual.run_imaging --grid 2x2 --zstack 3

    # Multi-region
    python -m tests.e2e.manual.run_imaging --grid 2x2 --regions 2
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from tests.harness import BackendContext
from tests.harness.simulators import AcquisitionSimulator


def parse_grid(grid_str: str) -> tuple:
    """Parse grid string like '3x3' into (n_x, n_y)."""
    if "x" in grid_str:
        parts = grid_str.lower().split("x")
        return int(parts[0]), int(parts[1])
    else:
        n = int(grid_str)
        return n, n


def main():
    parser = argparse.ArgumentParser(
        description="Manual imaging workflow testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--grid",
        type=str,
        default=None,
        help="Grid size (e.g., '3x3' or just '3' for square)",
    )
    parser.add_argument(
        "--single-fov",
        action="store_true",
        help="Acquire single FOV only",
    )
    parser.add_argument(
        "--regions",
        type=int,
        default=1,
        help="Number of separate regions to acquire",
    )
    parser.add_argument(
        "--zstack",
        type=int,
        default=1,
        help="Number of z-planes",
    )
    parser.add_argument(
        "--z-step",
        type=float,
        default=1.0,
        help="Z step size in um",
    )
    parser.add_argument(
        "--z-mode",
        type=str,
        choices=["FROM BOTTOM", "FROM CENTER", "FROM TOP"],
        default="FROM BOTTOM",
        help="Z-stack direction",
    )
    parser.add_argument(
        "--channels",
        type=str,
        nargs="+",
        default=None,
        help="Channel names to acquire (uses first available if not specified)",
    )
    parser.add_argument(
        "--timelapse",
        type=int,
        default=1,
        help="Number of timepoints",
    )
    parser.add_argument(
        "--time-interval",
        type=float,
        default=1.0,
        help="Time interval in seconds",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (uses temp dir if not specified)",
    )
    parser.add_argument(
        "--save-images",
        action="store_true",
        help="Actually save images to disk",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=10.0,
        help="FOV overlap percentage",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout in seconds",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Determine grid size
    if args.single_fov:
        n_x, n_y = 1, 1
    elif args.grid:
        n_x, n_y = parse_grid(args.grid)
    else:
        n_x, n_y = 1, 1

    # Setup output directory
    if args.output:
        output_dir = args.output
    else:
        output_dir = tempfile.mkdtemp(prefix="squid_imaging_test_")

    print("=" * 60)
    print("MANUAL IMAGING WORKFLOW TEST")
    print("=" * 60)
    print(f"Grid: {n_x}x{n_y}")
    print(f"Regions: {args.regions}")
    print(f"Z-stack: {args.zstack} planes @ {args.z_step}um ({args.z_mode})")
    print(f"Timelapse: {args.timelapse} timepoints @ {args.time_interval}s")
    print(f"Output: {output_dir}")
    print(f"Save images: {args.save_images}")
    print("=" * 60)
    print()

    print("Starting simulated backend...")

    with BackendContext(simulation=True, base_path=output_dir) as ctx:
        sim = AcquisitionSimulator(ctx)

        # Get stage center and available channels
        center = ctx.get_stage_center()
        available_channels = ctx.get_available_channels()

        print(f"Stage center: {center}")
        print(f"Available channels: {available_channels[:5]}...")

        # Set up channels
        if args.channels:
            channels = args.channels
        else:
            channels = [available_channels[0]] if available_channels else []

        print(f"Using channels: {channels}")
        print()

        # Add regions
        for i in range(args.regions):
            # Offset regions by 2mm
            x_offset = (i - args.regions // 2) * 2.0
            region_center = (center[0] + x_offset, center[1], center[2])

            if n_x == 1 and n_y == 1:
                sim.add_single_fov(
                    f"region_{i+1}",
                    x=region_center[0],
                    y=region_center[1],
                    z=region_center[2],
                )
            else:
                sim.add_grid_region(
                    f"region_{i+1}",
                    center=region_center,
                    n_x=n_x,
                    n_y=n_y,
                    overlap_pct=args.overlap,
                )

            print(f"Added region_{i+1} at {region_center}")

        # Configure acquisition
        sim.set_channels(channels)
        sim.set_zstack(n_z=args.zstack, delta_z_um=args.z_step, mode=args.z_mode)
        sim.set_timelapse(n_t=args.timelapse, delta_t_s=args.time_interval)
        sim.set_skip_saving(not args.save_images)

        # Calculate expected images
        total_fovs = n_x * n_y * args.regions
        total_images = total_fovs * args.zstack * args.timelapse * len(channels)
        print()
        print(f"Expected FOVs: {total_fovs}")
        print(f"Expected total images: {total_images}")
        print()

        # Run acquisition
        print("Starting acquisition...")
        print("-" * 40)

        result = sim.run_and_wait(timeout_s=args.timeout)

        print("-" * 40)
        print()

        # Print results
        print("=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(f"Success: {result.success}")
        print(f"Total FOVs acquired: {result.total_fovs}")
        print(f"Total images: {result.total_images}")
        print(f"Elapsed time: {result.elapsed_time_s:.2f}s")

        if result.error:
            print(f"Error: {result.error}")

        if args.verbose:
            print()
            print("State changes:")
            for sc in result.state_changes:
                print(f"  {sc}")

            print()
            print("Progress events:")
            for p in result.progress_events[:10]:
                print(f"  {p}")
            if len(result.progress_events) > 10:
                print(f"  ... and {len(result.progress_events) - 10} more")

        print()
        if result.success:
            print("IMAGING TEST PASSED")
            return 0
        else:
            print("IMAGING TEST FAILED")
            return 1


if __name__ == "__main__":
    sys.exit(main())
