"""Test to verify the drain_all=False issue."""

import tempfile
import os
import time
import queue

import numpy as np
import pytest

try:
    from control.core.job_processing import (
        DownsampledViewJob,
        JobRunner,
        JobImage,
    )
    from tests.control.core.test_job_processing_downsampled import make_test_capture_info

    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not MODULE_AVAILABLE, reason="modules not available")


class TestDrainAllIssue:
    """Demonstrate that drain_all=False causes delayed updates."""

    def test_drain_one_at_a_time_vs_drain_all(self):
        """Show that draining one at a time delays updates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            num_wells = 6
            tile_size = 64  # Small for fast processing

            try:
                # Submit all jobs
                print(f"\n=== Submitting {num_wells} jobs ===")
                for well_idx in range(num_wells):
                    well_id = f"{chr(ord('A') + well_idx)}1"
                    tile = np.random.randint(0, 65535, (tile_size, tile_size), dtype=np.uint16)

                    job = DownsampledViewJob(
                        capture_info=make_test_capture_info(region_id=well_id, fov=0),
                        capture_image=JobImage(image_array=tile),
                        well_id=well_id,
                        well_row=well_idx,
                        well_col=0,
                        fov_index=0,
                        total_fovs_in_well=1,
                        channel_idx=0,
                        total_channels=1,
                        channel_name="BF",
                        fov_position_in_well=(0.0, 0.0),
                        overlap_pixels=(0, 0, 0, 0),
                        pixel_size_um=1.0,
                        target_resolutions_um=[10.0],
                        plate_resolution_um=10.0,
                        output_dir=output_dir,
                        channel_names=["BF"],
                    )
                    runner.dispatch(job)

                # Wait for jobs to complete
                time.sleep(1.5)  # Wait for multiprocessing startup + job completion

                out_queue = runner.output_queue()

                # Check how many results are ready
                results_ready = 0
                temp_results = []
                while True:
                    try:
                        result = out_queue.get_nowait()
                        temp_results.append(result)
                        results_ready += 1
                    except queue.Empty:
                        break

                print(f"\n=== After waiting, {results_ready} results are ready ===")

                # Put them back for the actual test
                for r in temp_results:
                    out_queue.put(r)

                # Now simulate acquisition loop polling behavior
                print("\n=== Polling with drain_all=False (one at a time) ===")
                poll_count = 0
                results_processed = 0

                while results_processed < num_wells:
                    poll_count += 1
                    try:
                        # This is what _summarize_runner_outputs does with drain_all=False
                        result = out_queue.get_nowait()
                        results_processed += 1
                        print(f"  Poll {poll_count}: Got {result.result.well_id}")
                        # Simulate some work between polls (like moving stage, capturing image)
                        time.sleep(0.05)  # 50ms simulated inter-FOV time
                    except queue.Empty:
                        print(f"  Poll {poll_count}: Queue empty, waiting...")
                        time.sleep(0.05)

                    if poll_count > 20:  # Safety limit
                        break

                elapsed_polls = poll_count
                print(f"\nWith drain_all=False: {elapsed_polls} polls to get {results_processed} results")
                print(f"  Expected: ~{num_wells} polls (one result per poll)")
                print(f"  Actual inter-result delay: {elapsed_polls * 50}ms total")

                # The problem: If we only process ONE result per poll, and polls happen
                # once per FOV capture (~100-200ms), then with many wells ready,
                # we introduce artificial delays

            finally:
                runner.shutdown(timeout_s=5.0)

    def test_drain_all_processes_faster(self):
        """Show that drain_all=True processes all results immediately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            num_wells = 6
            tile_size = 64

            try:
                # Submit all jobs
                for well_idx in range(num_wells):
                    well_id = f"{chr(ord('A') + well_idx)}1"
                    tile = np.random.randint(0, 65535, (tile_size, tile_size), dtype=np.uint16)

                    job = DownsampledViewJob(
                        capture_info=make_test_capture_info(region_id=well_id, fov=0),
                        capture_image=JobImage(image_array=tile),
                        well_id=well_id,
                        well_row=well_idx,
                        well_col=0,
                        fov_index=0,
                        total_fovs_in_well=1,
                        channel_idx=0,
                        total_channels=1,
                        channel_name="BF",
                        fov_position_in_well=(0.0, 0.0),
                        overlap_pixels=(0, 0, 0, 0),
                        pixel_size_um=1.0,
                        target_resolutions_um=[10.0],
                        plate_resolution_um=10.0,
                        output_dir=output_dir,
                        channel_names=["BF"],
                    )
                    runner.dispatch(job)

                # Wait for jobs to complete
                time.sleep(1.5)

                out_queue = runner.output_queue()

                # Poll with drain_all=True behavior
                print("\n=== Polling with drain_all=True (drain entire queue) ===")
                poll_count = 0
                results_processed = 0
                start_time = time.time()

                while results_processed < num_wells:
                    poll_count += 1
                    # drain_all=True: process ALL available results
                    got_any = False
                    while True:
                        try:
                            result = out_queue.get_nowait()
                            results_processed += 1
                            print(f"  Poll {poll_count}: Got {result.result.well_id}")
                            got_any = True
                        except queue.Empty:
                            break

                    if not got_any:
                        time.sleep(0.05)

                    if poll_count > 20:
                        break

                elapsed_ms = (time.time() - start_time) * 1000
                print(f"\nWith drain_all=True: {poll_count} polls to get {results_processed} results")
                print(f"  All results processed in {elapsed_ms:.1f}ms")

            finally:
                runner.shutdown(timeout_s=5.0)

    def test_multi_fov_per_well_no_none_results(self):
        """Verify that None results (intermediate FOVs) are not queued."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            num_wells = 2
            fovs_per_well = 4
            tile_size = 64

            try:
                # Submit all jobs for all wells
                total_jobs_submitted = 0
                for well_idx in range(num_wells):
                    well_id = f"{chr(ord('A') + well_idx)}1"
                    for fov_idx in range(fovs_per_well):
                        tile = np.random.randint(0, 65535, (tile_size, tile_size), dtype=np.uint16)

                        job = DownsampledViewJob(
                            capture_info=make_test_capture_info(region_id=well_id, fov=fov_idx),
                            capture_image=JobImage(image_array=tile),
                            well_id=well_id,
                            well_row=well_idx,
                            well_col=0,
                            fov_index=fov_idx,
                            total_fovs_in_well=fovs_per_well,
                            channel_idx=0,
                            total_channels=1,
                            channel_name="BF",
                            fov_position_in_well=(fov_idx * 0.1, 0.0),
                            overlap_pixels=(0, 0, 0, 0),
                            pixel_size_um=1.0,
                            target_resolutions_um=[10.0],
                            plate_resolution_um=10.0,
                            output_dir=output_dir,
                            channel_names=["BF"],
                        )
                        runner.dispatch(job)
                        total_jobs_submitted += 1

                print(f"\n=== Submitted {total_jobs_submitted} jobs ({num_wells} wells x {fovs_per_well} FOVs) ===")

                # Wait for all jobs to complete
                time.sleep(2.0)

                # Count results in queue
                out_queue = runner.output_queue()
                results = []
                while True:
                    try:
                        result = out_queue.get_nowait()
                        results.append(result)
                    except queue.Empty:
                        break

                print(f"\n=== Results in queue: {len(results)} ===")
                for r in results:
                    print(f"  {r.result.well_id if r.result else 'None'}")

                # With the fix, we should only have num_wells results (not total_jobs_submitted)
                # Before fix: 8 results (all jobs, including None)
                # After fix: 2 results (only completed wells)
                assert len(results) == num_wells, f"Expected {num_wells} results, got {len(results)}"

                # Verify all results are actual DownsampledViewResults, not None
                for r in results:
                    assert r.result is not None, "Found None result in queue!"
                    assert r.result.well_images, "Result has no well_images!"

                print(f"\nFix verified: Only {num_wells} results (not {total_jobs_submitted})")

            finally:
                runner.shutdown(timeout_s=5.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
