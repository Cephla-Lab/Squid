"""Tests for DownsampledViewJob and multiprocessing integration."""

import pickle
import tempfile
import os
import time

import numpy as np
import pytest

# These imports will fail until we implement the module
try:
    from control.core.job_processing import (
        DownsampledViewJob,
        DownsampledViewResult,
        JobRunner,
        CaptureInfo,
        JobImage,
    )
    from control.core.downsampled_views import DownsampledViewManager
    import squid.abc

    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not MODULE_AVAILABLE, reason="downsampled view job not yet implemented")


def make_test_capture_info(
    region_id: str = "A1",
    fov: int = 0,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    z_mm: float = 0.0,
) -> CaptureInfo:
    """Create a CaptureInfo for testing."""
    from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings

    return CaptureInfo(
        position=squid.abc.Pos(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            display_color="#FFFFFF",
            camera=1,  # v1.0: camera is int ID
            illumination_settings=IlluminationSettings(
                illumination_channel="BF LED matrix full",
                intensity=50.0,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=10.0,
                gain_mode=1.0,
            ),
            z_offset_um=0.0,  # v1.0: at channel level
        ),
        save_directory="/tmp/test",
        file_id="test_0_0",
        region_id=region_id,
        fov=fov,
        configuration_idx=0,
    )


class TestDownsampledViewResult:
    """Tests for DownsampledViewResult serialization."""

    def test_downsampled_view_result_creation(self):
        """Test result can be created with expected fields."""
        well_image = np.ones((100, 100), dtype=np.uint16) * 5000

        result = DownsampledViewResult(
            well_id="A1",
            well_row=0,
            well_col=0,
            well_images={0: well_image},
            channel_names=["BF"],
        )

        assert result.well_id == "A1"
        assert result.well_row == 0
        assert result.well_col == 0
        assert 0 in result.well_images
        assert result.well_images[0].shape == (100, 100)
        assert result.channel_names == ["BF"]

    def test_downsampled_view_result_serialization(self):
        """Test result can be pickled/unpickled (for multiprocessing queue)."""
        well_image = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

        result = DownsampledViewResult(
            well_id="B3",
            well_row=1,
            well_col=2,
            well_images={0: well_image},
            channel_names=["BF"],
        )

        # Pickle and unpickle (simulates queue transfer)
        pickled = pickle.dumps(result)
        unpickled = pickle.loads(pickled)

        assert unpickled.well_id == "B3"
        assert unpickled.well_row == 1
        assert unpickled.well_col == 2
        assert np.array_equal(unpickled.well_images[0], well_image)

    def test_downsampled_view_result_none_image(self):
        """Test result with empty images dict (for intermediate FOVs)."""
        result = DownsampledViewResult(
            well_id="A1",
            well_row=0,
            well_col=0,
            well_images={},
            channel_names=[],
        )

        pickled = pickle.dumps(result)
        unpickled = pickle.loads(pickled)

        assert unpickled.well_images == {}


class TestDownsampledViewJob:
    """Tests for DownsampledViewJob execution."""

    def test_downsampled_view_job_single_fov_well(self):
        """Test job with single FOV produces correct output files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            tile = np.random.randint(0, 65535, (200, 200), dtype=np.uint16)
            capture_info = make_test_capture_info(region_id="A1", fov=0)

            job = DownsampledViewJob(
                capture_info=capture_info,
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

            result = job.run()

            # Check files created (multipage TIFF)
            assert os.path.exists(os.path.join(output_dir, "wells", "A1_5um.tiff"))
            assert os.path.exists(os.path.join(output_dir, "wells", "A1_10um.tiff"))
            assert os.path.exists(os.path.join(output_dir, "wells", "A1_20um.tiff"))

            # Check result returned
            assert result is not None
            assert result.well_id == "A1"
            assert 0 in result.well_images
            assert result.well_images[0].shape == (20, 20)  # 200/10 = 20
            assert result.channel_names == ["BF"]

            # Verify multipage TIFF structure
            import tifffile

            with tifffile.TiffFile(os.path.join(output_dir, "wells", "A1_10um.tiff")) as tif:
                assert len(tif.pages) == 1  # 1 channel
                assert tif.pages[0].shape == (20, 20)

    def test_downsampled_view_job_multi_fov_well(self):
        """Test job accumulates FOVs and stitches on last FOV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            pixel_size_um = 1.0
            tile_size = 100
            step_mm = 0.1  # 100 um = 100 pixels

            # Create 4 tiles for 2x2 grid
            tiles = [
                np.ones((tile_size, tile_size), dtype=np.uint16) * 1000,
                np.ones((tile_size, tile_size), dtype=np.uint16) * 2000,
                np.ones((tile_size, tile_size), dtype=np.uint16) * 3000,
                np.ones((tile_size, tile_size), dtype=np.uint16) * 4000,
            ]
            positions = [
                (0.0, 0.0),
                (step_mm, 0.0),
                (0.0, step_mm),
                (step_mm, step_mm),
            ]

            results = []
            for i, (tile, pos) in enumerate(zip(tiles, positions)):
                job = DownsampledViewJob(
                    capture_info=make_test_capture_info(region_id="B2", fov=i, x_mm=pos[0], y_mm=pos[1]),
                    capture_image=JobImage(image_array=tile),
                    well_id="B2",
                    well_row=1,
                    well_col=1,
                    fov_index=i,
                    total_fovs_in_well=4,
                    channel_idx=0,
                    total_channels=1,
                    channel_name="BF",
                    fov_position_in_well=pos,
                    overlap_pixels=(0, 0, 0, 0),
                    pixel_size_um=pixel_size_um,
                    target_resolutions_um=[10.0],
                    plate_resolution_um=10.0,
                    output_dir=output_dir,
                    channel_names=["BF"],
                )
                result = job.run()
                results.append(result)

            # Only the last FOV should return a result
            assert results[0] is None
            assert results[1] is None
            assert results[2] is None
            assert results[3] is not None

            # Check stitched image shape (multi-channel: well_images is dict)
            assert 0 in results[3].well_images
            assert results[3].well_images[0].shape == (20, 20)  # 200/10 = 20

    def test_downsampled_view_job_multi_channel(self):
        """Test job accumulates multiple channels and saves multipage TIFF."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            # Create 2 channels for 1 FOV
            channel_names = ["BF", "Fluorescence"]
            tiles = [
                np.ones((100, 100), dtype=np.uint16) * 1000,  # Channel 0: BF
                np.ones((100, 100), dtype=np.uint16) * 2000,  # Channel 1: Fluorescence
            ]

            results = []
            for ch_idx, tile in enumerate(tiles):
                job = DownsampledViewJob(
                    capture_info=make_test_capture_info(region_id="A1", fov=0),
                    capture_image=JobImage(image_array=tile),
                    well_id="A1",
                    well_row=0,
                    well_col=0,
                    fov_index=0,
                    total_fovs_in_well=1,
                    channel_idx=ch_idx,
                    total_channels=2,
                    channel_name=channel_names[ch_idx],
                    fov_position_in_well=(0.0, 0.0),
                    overlap_pixels=(0, 0, 0, 0),
                    pixel_size_um=1.0,
                    target_resolutions_um=[10.0],
                    plate_resolution_um=10.0,
                    output_dir=output_dir,
                    channel_names=channel_names,
                )
                result = job.run()
                results.append(result)

            # First channel should return None (waiting for all channels)
            assert results[0] is None
            # Last channel should return result
            assert results[1] is not None
            assert results[1].well_id == "A1"

            # Verify multipage TIFF
            import tifffile

            tiff_path = os.path.join(output_dir, "wells", "A1_10um.tiff")
            assert os.path.exists(tiff_path)

            with tifffile.TiffFile(tiff_path) as tif:
                data = tif.asarray()
                assert data.shape == (2, 10, 10)  # 2 channels, 100/10 = 10
                # Channel 0 should have value ~1000, Channel 1 should have value ~2000
                assert np.isclose(data[0].mean(), 1000, rtol=0.01)
                assert np.isclose(data[1].mean(), 2000, rtol=0.01)

            # Plate view should have both channels
            assert 0 in results[1].well_images
            assert 1 in results[1].well_images
            assert np.isclose(results[1].well_images[0].mean(), 1000, rtol=0.01)
            assert np.isclose(results[1].well_images[1].mean(), 2000, rtol=0.01)

    def test_downsampled_view_job_with_overlap_cropping(self):
        """Test job correctly crops overlap before stitching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            # Create tile with distinct borders to verify cropping
            tile = np.zeros((100, 100), dtype=np.uint16)
            tile[10:90, 10:90] = 5000  # Center region
            tile[0:10, :] = 1000  # Top border (will be cropped)
            tile[90:100, :] = 2000  # Bottom border (will be cropped)
            tile[:, 0:10] = 3000  # Left border (will be cropped)
            tile[:, 90:100] = 4000  # Right border (will be cropped)

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
                overlap_pixels=(10, 10, 10, 10),  # Crop 10 pixels from each side
                pixel_size_um=1.0,
                target_resolutions_um=[1.0],  # No downsampling for easier verification
                plate_resolution_um=1.0,
                output_dir=output_dir,
                channel_names=["BF"],
            )

            result = job.run()

            # Cropped tile should be 80x80, all center value
            assert 0 in result.well_images
            assert result.well_images[0].shape == (80, 80)
            assert np.all(result.well_images[0] == 5000)


class TestJobRunnerIntegration:
    """Tests for DownsampledViewJob with JobRunner multiprocessing."""

    @pytest.mark.slow
    def test_downsampled_job_runs_in_job_runner(self):
        """Test job executes correctly via JobRunner multiprocessing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            tile = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

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
                target_resolutions_um=[10.0],
                plate_resolution_um=10.0,
                output_dir=output_dir,
                channel_names=["BF"],
            )

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            try:
                runner.dispatch(job)

                # Wait for result with timeout
                result = runner.output_queue().get(timeout=10.0)

                assert result.exception is None
                assert result.result is not None
                assert result.result.well_id == "A1"
            finally:
                runner.shutdown(timeout_s=5.0)

    @pytest.mark.slow
    def test_downsampled_result_returned_via_queue(self):
        """Test 10um image returned via output queue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            tile = np.ones((100, 100), dtype=np.uint16) * 12345

            job = DownsampledViewJob(
                capture_info=make_test_capture_info(region_id="C5", fov=0),
                capture_image=JobImage(image_array=tile),
                well_id="C5",
                well_row=2,
                well_col=4,
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

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            try:
                runner.dispatch(job)
                result = runner.output_queue().get(timeout=10.0)

                # Verify the well images were transferred correctly (multi-channel)
                assert 0 in result.result.well_images
                assert result.result.well_images[0].shape == (10, 10)
                # Mean should be close to 12345 (some averaging due to downsampling)
                assert np.isclose(result.result.well_images[0].mean(), 12345, rtol=0.01)
            finally:
                runner.shutdown(timeout_s=5.0)

    @pytest.mark.slow
    def test_main_process_receives_well_image(self):
        """Test main process can deserialize and use returned image for plate view."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "downsampled")
            os.makedirs(os.path.join(output_dir, "wells"), exist_ok=True)

            tile = np.ones((100, 100), dtype=np.uint16) * 7777

            job = DownsampledViewJob(
                capture_info=make_test_capture_info(region_id="B2", fov=0),
                capture_image=JobImage(image_array=tile),
                well_id="B2",
                well_row=1,
                well_col=1,
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

            runner = JobRunner()
            runner.daemon = True
            runner.start()

            try:
                runner.dispatch(job)
                job_result = runner.output_queue().get(timeout=10.0)

                # Simulate main process updating plate view (multi-channel)
                manager = DownsampledViewManager(8, 12, (10, 10))
                result = job_result.result
                manager.update_well(result.well_row, result.well_col, result.well_images)

                # Verify plate view was updated correctly (channel 0)
                y_start = 1 * 10
                x_start = 1 * 10
                well_region = manager.plate_view[0, y_start : y_start + 10, x_start : x_start + 10]
                assert np.isclose(well_region.mean(), 7777, rtol=0.01)
            finally:
                runner.shutdown(timeout_s=5.0)
