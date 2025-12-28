"""Tests to measure and diagnose plate view update timing."""

import tempfile
import os
import time
import queue
import threading

import numpy as np
import pytest

try:
    from control.core.job_processing import (
        DownsampledViewJob,
        DownsampledViewResult,
        JobRunner,
        CaptureInfo,
        JobImage,
    )

    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not MODULE_AVAILABLE, reason="modules not available")


def make_test_capture_info(region_id: str = "A1", fov: int = 0) -> CaptureInfo:
    """Create a CaptureInfo for testing."""
    # Use the existing test helper from the other test file
    from tests.control.core.test_job_processing_downsampled import make_test_capture_info as _make_info

    return _make_info(region_id=region_id, fov=fov)


class TestPlateViewTiming:
    """Tests to measure timing of plate view updates."""

    def test_single_job_timing(self):
        """Measure time for a single job to complete and return result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            # Create a realistic-sized tile (512x512)
            tile = np.random.randint(0, 65535, (512, 512), dtype=np.uint16)

            job = DownsampledViewJob(
                capture_info=make_test_capture_info(region_id="A1", fov=0),
                capture_image=JobImage(image_array=tile),
                well_id="A1",
                well_row=0,
                well_col=0,
                fov_index=0,
                total_fovs_in_well=1,
                channel_idx=0,
                total_channels=1,
                channel_name="BF",
                fov_position_in_well=(0.0, 0.0),
                overlap_pixels=(0, 0, 0, 0),
                pixel_size_um=1.0,
                target_resolutions_um=[5.0, 10.0, 20.0],
                plate_resolution_um=10.0,
                output_dir=output_dir,
                channel_names=["BF"],
            )

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            try:
                dispatch_time = time.time()
                runner.dispatch(job)

                runner.output_queue().get(timeout=30.0)
                complete_time = time.time()

                elapsed_ms = (complete_time - dispatch_time) * 1000
                print(f"\nSingle job timing: {elapsed_ms:.1f} ms")

                # Should complete in reasonable time
                assert elapsed_ms < 5000, f"Job took too long: {elapsed_ms:.1f} ms"
            finally:
                runner.shutdown(timeout_s=5.0)

    def test_multi_well_sequential_timing(self):
        """Measure time for multiple wells completing sequentially."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            num_wells = 4
            fovs_per_well = 4
            tile_size = 256

            timings = []

            try:
                for well_idx in range(num_wells):
                    well_id = f"{chr(ord('A') + well_idx)}1"
                    well_start = time.time()

                    # Simulate capturing FOVs for this well
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
                            fov_position_in_well=(fov_idx * 0.5, 0.0),
                            overlap_pixels=(0, 0, 0, 0),
                            pixel_size_um=1.0,
                            target_resolutions_um=[10.0],
                            plate_resolution_um=10.0,
                            output_dir=output_dir,
                            channel_names=["BF"],
                        )
                        runner.dispatch(job)

                    # Wait for this well to complete
                    runner.output_queue().get(timeout=30.0)
                    well_complete = time.time()

                    elapsed_ms = (well_complete - well_start) * 1000
                    timings.append(elapsed_ms)
                    print(f"\nWell {well_id} complete: {elapsed_ms:.1f} ms")

                print(f"\nTotal wells: {num_wells}")
                print(f"Mean per-well time: {np.mean(timings):.1f} ms")
                print(f"Max per-well time: {np.max(timings):.1f} ms")

            finally:
                runner.shutdown(timeout_s=5.0)

    def test_queue_polling_frequency(self):
        """Test how queue polling affects result availability."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            num_wells = 8
            tile_size = 128

            # Submit all jobs
            submit_times = {}

            try:
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
                    submit_times[well_id] = time.time()
                    runner.dispatch(job)
                    # Simulate inter-FOV delay (camera exposure + stage move)
                    time.sleep(0.05)  # 50ms

                all_submitted = time.time()
                print(f"\nAll {num_wells} jobs submitted")

                # Now poll for results (simulating acquisition loop)
                receive_times = {}
                out_queue = runner.output_queue()

                while len(receive_times) < num_wells:
                    try:
                        result = out_queue.get_nowait()
                        receive_times[result.result.well_id] = time.time()
                    except queue.Empty:
                        time.sleep(0.01)  # Poll every 10ms

                    if time.time() - all_submitted > 30:
                        break

                # Analyze timing
                print(f"\nResult arrival order:")
                for well_id in sorted(receive_times.keys(), key=lambda x: receive_times[x]):
                    submit = submit_times[well_id]
                    receive = receive_times[well_id]
                    latency_ms = (receive - submit) * 1000
                    print(
                        f"  {well_id}: submitted at +{(submit - submit_times['A1'])*1000:.0f}ms, "
                        f"received at +{(receive - submit_times['A1'])*1000:.0f}ms, "
                        f"latency={latency_ms:.0f}ms"
                    )

            finally:
                runner.shutdown(timeout_s=5.0)

    def test_callback_invocation_timing(self):
        """Test timing of callback invocation after result is available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            callback_times = []

            def mock_callback(result):
                callback_times.append(time.time())

            num_wells = 4
            tile_size = 128

            try:
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

                # Poll and invoke callbacks
                out_queue = runner.output_queue()
                received = 0
                poll_start = time.time()

                while received < num_wells:
                    try:
                        result = out_queue.get(timeout=0.01)
                        mock_callback(result.result)
                        received += 1
                    except queue.Empty:
                        # No result available yet; continue polling
                        pass

                    if time.time() - poll_start > 30:
                        break

                print(f"\nCallback invocation times (relative to first):")
                for i, t in enumerate(callback_times):
                    rel_time = (t - callback_times[0]) * 1000
                    print(f"  Callback {i+1}: +{rel_time:.1f}ms")

            finally:
                runner.shutdown(timeout_s=5.0)

    def test_tiff_saving_overhead(self):
        """Measure overhead of TIFF saving vs just computing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            tile = np.random.randint(0, 65535, (512, 512), dtype=np.uint16)

            # Test with TIFF saving (3 resolutions)
            runner1 = JobRunner()
            runner1.daemon = True
            runner1.start()

            try:
                job_with_save = DownsampledViewJob(
                    capture_info=make_test_capture_info(region_id="A1", fov=0),
                    capture_image=JobImage(image_array=tile.copy()),
                    well_id="A1",
                    well_row=0,
                    well_col=0,
                    fov_index=0,
                    total_fovs_in_well=1,
                    channel_idx=0,
                    total_channels=1,
                    channel_name="BF",
                    fov_position_in_well=(0.0, 0.0),
                    overlap_pixels=(0, 0, 0, 0),
                    pixel_size_um=1.0,
                    target_resolutions_um=[5.0, 10.0, 20.0],  # 3 resolutions
                    plate_resolution_um=10.0,
                    output_dir=output_dir,
                    channel_names=["BF"],
                )

                start = time.time()
                runner1.dispatch(job_with_save)
                runner1.output_queue().get(timeout=30.0)
                time_with_save = (time.time() - start) * 1000

            finally:
                runner1.shutdown(timeout_s=5.0)

            # Test with single resolution (plate only)
            output_dir2 = os.path.join(tmpdir, "downsampled2")
            os.makedirs(os.path.join(output_dir2, "wells"), exist_ok=True)

            runner2 = JobRunner()
            runner2.daemon = True
            runner2.start()

            try:
                job_single_res = DownsampledViewJob(
                    capture_info=make_test_capture_info(region_id="B1", fov=0),
                    capture_image=JobImage(image_array=tile.copy()),
                    well_id="B1",
                    well_row=1,
                    well_col=0,
                    fov_index=0,
                    total_fovs_in_well=1,
                    channel_idx=0,
                    total_channels=1,
                    channel_name="BF",
                    fov_position_in_well=(0.0, 0.0),
                    overlap_pixels=(0, 0, 0, 0),
                    pixel_size_um=1.0,
                    target_resolutions_um=[10.0],  # Single resolution
                    plate_resolution_um=10.0,
                    output_dir=output_dir2,
                    channel_names=["BF"],
                )

                start = time.time()
                runner2.dispatch(job_single_res)
                runner2.output_queue().get(timeout=30.0)
                time_single_res = (time.time() - start) * 1000

            finally:
                runner2.shutdown(timeout_s=5.0)

            print(f"\n3 resolutions (with TIFF saving): {time_with_save:.1f}ms")
            print(f"1 resolution (with TIFF saving): {time_single_res:.1f}ms")
            print(f"Overhead of extra resolutions: {time_with_save - time_single_res:.1f}ms")


class TestSignalEmissionTiming:
    """Test timing of Qt signal emission and processing."""

    def test_signal_emission_in_thread(self):
        """Simulate signal emission from worker thread."""
        from qtpy.QtCore import QObject, Signal, QThread
        from qtpy.QtWidgets import QApplication
        import sys

        # Need QApplication for signals to work
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

        signal_emit_times = []
        signal_receive_times = []

        class Emitter(QObject):
            update_signal = Signal(int, float)

        class Receiver(QObject):
            def __init__(self, receive_times):
                super().__init__()
                self.receive_times = receive_times

            def on_update(self, idx, value):
                self.receive_times.append((idx, time.time()))

        emitter = Emitter()
        receiver = Receiver(signal_receive_times)
        emitter.update_signal.connect(receiver.on_update)

        # Emit from main thread
        num_signals = 10
        for i in range(num_signals):
            emit_time = time.time()
            signal_emit_times.append((i, emit_time))
            emitter.update_signal.emit(i, emit_time)

        # Process events
        app.processEvents()

        print(f"\nSignal emission from main thread:")
        for (i, emit_t), (j, recv_t) in zip(signal_emit_times, signal_receive_times):
            latency_us = (recv_t - emit_t) * 1_000_000
            print(f"  Signal {i}: latency = {latency_us:.1f} µs")

        # Now test from worker thread
        signal_emit_times.clear()
        signal_receive_times.clear()

        def emit_from_thread():
            for i in range(num_signals):
                emit_time = time.time()
                signal_emit_times.append((i, emit_time))
                emitter.update_signal.emit(i, emit_time)
                time.sleep(0.01)

        thread = threading.Thread(target=emit_from_thread)
        thread.start()

        # Process events while thread runs
        while thread.is_alive() or len(signal_receive_times) < num_signals:
            app.processEvents()
            time.sleep(0.001)

        thread.join()

        print(f"\nSignal emission from worker thread:")
        for (i, emit_t), (j, recv_t) in zip(signal_emit_times, signal_receive_times):
            latency_us = (recv_t - emit_t) * 1_000_000
            print(f"  Signal {i}: latency = {latency_us:.1f} µs")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
