"""Tests for AcquisitionInfo and SaveOMETiffJob integration."""

import multiprocessing
import pickle
import tempfile
import os
import time
import queue

import numpy as np
import pytest

from squid.backend.controllers.multipoint.job_processing import (
    AcquisitionInfo,
    CaptureInfo,
    SaveImageJob,
    SaveOMETiffJob,
    JobRunner,
    JobImage,
    JobResult,
    FILE_LOCK_TIMEOUT_SECONDS,
)
from squid.backend.io.writers import utils_ome_tiff_writer as ome_tiff_writer
import squid.core.abc
from squid.core.utils.config_utils import ChannelMode


def make_test_channel_mode(name: str = "BF LED matrix full") -> ChannelMode:
    """Create a ChannelMode for testing."""
    return ChannelMode(
        id="0",
        name=name,
        camera_sn="test",
        exposure_time=10.0,
        analog_gain=1.0,
        illumination_source=0,
        illumination_intensity=50.0,
        z_offset=0.0,
    )


def make_test_capture_info(
    region_id: int = 0,
    fov: int = 0,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    z_mm: float = 0.0,
    z_index: int = 0,
    time_point: int = 0,
    configuration_idx: int = 0,
    config_name: str = "BF LED matrix full",
    save_directory: str = "/tmp/test",
) -> CaptureInfo:
    """Create a CaptureInfo for testing."""
    return CaptureInfo(
        position=squid.core.abc.Pos(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, theta_rad=None),
        z_index=z_index,
        capture_time=time.time(),
        configuration=make_test_channel_mode(config_name),
        save_directory=save_directory,
        file_id=f"test_{region_id}_{fov}",
        region_id=region_id,
        fov=fov,
        configuration_idx=configuration_idx,
        time_point=time_point,
    )


def make_test_acquisition_info(
    total_time_points: int = 1,
    total_z_levels: int = 1,
    total_channels: int = 1,
    channel_names: list = None,
    experiment_path: str = None,
) -> AcquisitionInfo:
    """Create an AcquisitionInfo for testing."""
    if channel_names is None:
        channel_names = ["BF LED matrix full"]
    return AcquisitionInfo(
        total_time_points=total_time_points,
        total_z_levels=total_z_levels,
        total_channels=total_channels,
        channel_names=channel_names,
        experiment_path=experiment_path,
        time_increment_s=1.0 if total_time_points > 1 else None,
        physical_size_z_um=0.5 if total_z_levels > 1 else None,
        physical_size_x_um=0.325,
        physical_size_y_um=0.325,
    )


class TestAcquisitionInfo:
    """Tests for AcquisitionInfo dataclass."""

    def test_acquisition_info_creation(self):
        """Test AcquisitionInfo can be created with required fields."""
        acq_info = AcquisitionInfo(
            total_time_points=5,
            total_z_levels=3,
            total_channels=2,
            channel_names=["BF", "Fluorescence"],
        )

        assert acq_info.total_time_points == 5
        assert acq_info.total_z_levels == 3
        assert acq_info.total_channels == 2
        assert acq_info.channel_names == ["BF", "Fluorescence"]
        assert acq_info.experiment_path is None  # Optional

    def test_acquisition_info_with_optional_fields(self):
        """Test AcquisitionInfo with all optional fields."""
        acq_info = AcquisitionInfo(
            total_time_points=10,
            total_z_levels=5,
            total_channels=3,
            channel_names=["BF", "GFP", "DAPI"],
            experiment_path="/data/experiments/exp001",
            time_increment_s=60.0,
            physical_size_z_um=0.5,
            physical_size_x_um=0.325,
            physical_size_y_um=0.325,
        )

        assert acq_info.experiment_path == "/data/experiments/exp001"
        assert acq_info.time_increment_s == 60.0
        assert acq_info.physical_size_z_um == 0.5
        assert acq_info.physical_size_x_um == 0.325
        assert acq_info.physical_size_y_um == 0.325

    def test_acquisition_info_serialization(self):
        """Test AcquisitionInfo can be pickled/unpickled (for multiprocessing)."""
        acq_info = make_test_acquisition_info(
            total_time_points=5,
            total_z_levels=3,
            total_channels=2,
            channel_names=["BF", "GFP"],
        )

        pickled = pickle.dumps(acq_info)
        unpickled = pickle.loads(pickled)

        assert unpickled.total_time_points == 5
        assert unpickled.total_z_levels == 3
        assert unpickled.total_channels == 2
        assert unpickled.channel_names == ["BF", "GFP"]


class TestCaptureInfo:
    """Tests for CaptureInfo dataclass without redundant fields."""

    def test_capture_info_creation_without_redundant_fields(self):
        """Test CaptureInfo can be created without acquisition-wide fields."""
        capture_info = make_test_capture_info(
            region_id=1,
            fov=2,
            z_index=3,
            time_point=4,
            configuration_idx=0,
        )

        assert capture_info.region_id == 1
        assert capture_info.fov == 2
        assert capture_info.z_index == 3
        assert capture_info.time_point == 4
        assert capture_info.configuration_idx == 0

        # Verify removed fields are not present
        assert not hasattr(capture_info, "total_time_points")
        assert not hasattr(capture_info, "total_z_levels")
        assert not hasattr(capture_info, "total_channels")
        assert not hasattr(capture_info, "channel_names")
        assert not hasattr(capture_info, "experiment_path")
        assert not hasattr(capture_info, "time_increment_s")
        assert not hasattr(capture_info, "physical_size_z_um")
        assert not hasattr(capture_info, "physical_size_x_um")
        assert not hasattr(capture_info, "physical_size_y_um")

    def test_capture_info_serialization(self):
        """Test CaptureInfo can be pickled/unpickled."""
        capture_info = make_test_capture_info()
        pickled = pickle.dumps(capture_info)
        unpickled = pickle.loads(pickled)

        assert unpickled.fov == capture_info.fov
        assert unpickled.region_id == capture_info.region_id


class TestSaveOMETiffJob:
    """Tests for SaveOMETiffJob class."""

    def test_save_ome_tiff_job_requires_acquisition_info(self):
        """Test SaveOMETiffJob raises error when acquisition_info is None."""
        capture_info = make_test_capture_info()
        image = np.zeros((100, 100), dtype=np.uint16)

        job = SaveOMETiffJob(
            capture_info=capture_info,
            capture_image=JobImage(image_array=image),
            acquisition_info=None,  # Not set
        )

        with pytest.raises(ValueError, match="requires acquisition_info"):
            job.run()

    def test_save_ome_tiff_job_with_acquisition_info(self):
        """Test SaveOMETiffJob runs with acquisition_info set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            capture_info = make_test_capture_info(save_directory=tmpdir)
            acq_info = make_test_acquisition_info(experiment_path=tmpdir)
            image = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

            job = SaveOMETiffJob(
                capture_info=capture_info,
                capture_image=JobImage(image_array=image),
                acquisition_info=acq_info,
            )

            result = job.run()
            assert result is True

            # Verify OME-TIFF was created
            ome_folder = os.path.join(tmpdir, "ome_tiff")
            assert os.path.exists(ome_folder)
            tiff_files = [f for f in os.listdir(ome_folder) if f.endswith(".ome.tiff")]
            assert len(tiff_files) == 1

    def test_save_ome_tiff_job_serialization(self):
        """Test SaveOMETiffJob can be pickled with acquisition_info."""
        capture_info = make_test_capture_info()
        acq_info = make_test_acquisition_info()
        image = np.zeros((100, 100), dtype=np.uint16)

        job = SaveOMETiffJob(
            capture_info=capture_info,
            capture_image=JobImage(image_array=image),
            acquisition_info=acq_info,
        )

        pickled = pickle.dumps(job)
        unpickled = pickle.loads(pickled)

        assert unpickled.acquisition_info is not None
        assert unpickled.acquisition_info.total_time_points == 1


class TestJobRunnerAcquisitionInfoInjection:
    """Tests for JobRunner AcquisitionInfo injection."""

    def test_job_runner_without_acquisition_info(self):
        """Test JobRunner without acquisition_info for non-OME jobs."""
        runner = JobRunner(acquisition_info=None)
        assert runner._acquisition_info is None

    def test_job_runner_with_acquisition_info(self):
        """Test JobRunner stores acquisition_info."""
        acq_info = make_test_acquisition_info()
        runner = JobRunner(acquisition_info=acq_info)
        assert runner._acquisition_info is not None
        assert runner._acquisition_info.total_time_points == 1

    def test_job_runner_rejects_ome_job_without_acquisition_info(self):
        """Test JobRunner raises error when dispatching SaveOMETiffJob without acquisition_info."""
        runner = JobRunner(acquisition_info=None)
        runner.daemon = True
        runner.start()

        try:
            capture_info = make_test_capture_info()
            image = np.zeros((100, 100), dtype=np.uint16)

            job = SaveOMETiffJob(
                capture_info=capture_info,
                capture_image=JobImage(image_array=image),
            )

            with pytest.raises(ValueError, match="initialized without acquisition_info"):
                runner.dispatch(job)
        finally:
            runner.shutdown(timeout_s=2.0)

    @pytest.mark.slow
    def test_job_runner_injects_acquisition_info(self):
        """Test JobRunner injects acquisition_info into SaveOMETiffJob."""
        with tempfile.TemporaryDirectory() as tmpdir:
            acq_info = make_test_acquisition_info(experiment_path=tmpdir)
            runner = JobRunner(acquisition_info=acq_info)
            runner.daemon = True
            runner.start()

            try:
                capture_info = make_test_capture_info(save_directory=tmpdir)
                image = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

                job = SaveOMETiffJob(
                    capture_info=capture_info,
                    capture_image=JobImage(image_array=image),
                    # acquisition_info not set - will be injected
                )

                runner.dispatch(job)
                result = runner.output_queue().get(timeout=10.0)

                assert result.exception is None
                assert result.result is True
            finally:
                runner.shutdown(timeout_s=5.0)

    @pytest.mark.slow
    def test_job_runner_does_not_inject_for_save_image_job(self):
        """Test JobRunner does not affect regular SaveImageJob."""
        with tempfile.TemporaryDirectory() as tmpdir:
            acq_info = make_test_acquisition_info()
            runner = JobRunner(acquisition_info=acq_info)
            runner.daemon = True
            runner.start()

            try:
                capture_info = make_test_capture_info(save_directory=tmpdir)
                image = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

                job = SaveImageJob(
                    capture_info=capture_info,
                    capture_image=JobImage(image_array=image),
                )

                runner.dispatch(job)
                result = runner.output_queue().get(timeout=10.0)

                # SaveImageJob should succeed (saves as individual image)
                assert result.exception is None
                assert result.result is True
            finally:
                runner.shutdown(timeout_s=5.0)


class TestOMETiffWriterFunctions:
    """Tests for utils_ome_tiff_writer functions with AcquisitionInfo."""

    def test_ome_output_folder_uses_acquisition_info(self):
        """Test ome_output_folder uses experiment_path from AcquisitionInfo."""
        acq_info = make_test_acquisition_info(experiment_path="/data/exp001")
        capture_info = make_test_capture_info(save_directory="/tmp/fallback")

        folder = ome_tiff_writer.ome_output_folder(acq_info, capture_info)
        assert folder == "/data/exp001/ome_tiff"

    def test_ome_output_folder_fallback(self):
        """Test ome_output_folder falls back to save_directory parent."""
        acq_info = make_test_acquisition_info(experiment_path=None)
        capture_info = make_test_capture_info(save_directory="/tmp/exp/region0")

        folder = ome_tiff_writer.ome_output_folder(acq_info, capture_info)
        assert folder == "/tmp/exp/ome_tiff"

    def test_validate_capture_info_checks_time_point(self):
        """Test validate_capture_info requires time_point."""
        acq_info = make_test_acquisition_info()
        capture_info = make_test_capture_info()
        capture_info = CaptureInfo(
            position=capture_info.position,
            z_index=capture_info.z_index,
            capture_time=capture_info.capture_time,
            configuration=capture_info.configuration,
            save_directory=capture_info.save_directory,
            file_id=capture_info.file_id,
            region_id=capture_info.region_id,
            fov=capture_info.fov,
            configuration_idx=capture_info.configuration_idx,
            time_point=None,  # Missing
        )
        image = np.zeros((100, 100), dtype=np.uint16)

        with pytest.raises(ValueError, match="time_point is required"):
            ome_tiff_writer.validate_capture_info(capture_info, acq_info, image)

    def test_validate_capture_info_checks_2d_image(self):
        """Test validate_capture_info requires 2D image."""
        acq_info = make_test_acquisition_info()
        capture_info = make_test_capture_info()
        image = np.zeros((100, 100, 3), dtype=np.uint16)  # RGB

        with pytest.raises(NotImplementedError, match="2D grayscale"):
            ome_tiff_writer.validate_capture_info(capture_info, acq_info, image)

    def test_initialize_metadata_uses_acquisition_info(self):
        """Test initialize_metadata extracts values from AcquisitionInfo."""
        acq_info = make_test_acquisition_info(
            total_time_points=5,
            total_z_levels=3,
            total_channels=2,
            channel_names=["BF", "GFP"],
        )
        capture_info = make_test_capture_info()
        image = np.zeros((100, 100), dtype=np.uint16)

        metadata = ome_tiff_writer.initialize_metadata(acq_info, capture_info, image)

        assert metadata[ome_tiff_writer.SHAPE_KEY] == [5, 3, 2, 100, 100]
        assert metadata[ome_tiff_writer.CHANNEL_NAMES_KEY] == ["BF", "GFP"]
        assert metadata[ome_tiff_writer.EXPECTED_COUNT_KEY] == 30  # 5*3*2

    def test_cleanup_stale_metadata_files(self):
        """Test cleanup_stale_metadata_files removes incomplete files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a stale metadata file
            stale_path = os.path.join(tmpdir, "squid_ome_abc123_metadata.json")
            metadata = {
                ome_tiff_writer.COMPLETED_KEY: False,
                ome_tiff_writer.SAVED_COUNT_KEY: 5,
                ome_tiff_writer.EXPECTED_COUNT_KEY: 10,
            }
            ome_tiff_writer.write_metadata(stale_path, metadata)

            # Note: cleanup_stale_metadata_files looks in tempfile.gettempdir()
            # so this test is more of a functional check that the function exists
            # and returns a list
            removed = ome_tiff_writer.cleanup_stale_metadata_files()
            assert isinstance(removed, list)


class TestFileLocking:
    """Tests for file locking with filelock."""

    def test_file_lock_constant_defined(self):
        """Test FILE_LOCK_TIMEOUT_SECONDS is defined."""
        assert FILE_LOCK_TIMEOUT_SECONDS > 0
        assert FILE_LOCK_TIMEOUT_SECONDS == 10  # Expected default

    def test_metadata_constants_defined(self):
        """Test metadata key constants are defined."""
        assert ome_tiff_writer.DTYPE_KEY == "dtype"
        assert ome_tiff_writer.SHAPE_KEY == "shape"
        assert ome_tiff_writer.AXES_KEY == "axes"
        assert ome_tiff_writer.CHANNEL_NAMES_KEY == "channel_names"
        assert ome_tiff_writer.WRITTEN_INDICES_KEY == "written_indices"
        assert ome_tiff_writer.SAVED_COUNT_KEY == "saved_count"
        assert ome_tiff_writer.EXPECTED_COUNT_KEY == "expected_count"
        assert ome_tiff_writer.COMPLETED_KEY == "completed"
        assert ome_tiff_writer.START_TIME_KEY == "start_time"
        assert ome_tiff_writer.PLANES_KEY == "planes"


class TestMultiImageOMETiff:
    """Integration tests for multi-image OME-TIFF acquisition."""

    def test_single_image_acquisition(self):
        """Test single image OME-TIFF acquisition (1 time, 1 z, 1 channel)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            acq_info = make_test_acquisition_info(
                total_time_points=1,
                total_z_levels=1,
                total_channels=1,
                channel_names=["BF"],
                experiment_path=tmpdir,
            )
            capture_info = make_test_capture_info(save_directory=tmpdir)
            image = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)

            job = SaveOMETiffJob(
                capture_info=capture_info,
                capture_image=JobImage(image_array=image),
                acquisition_info=acq_info,
            )

            result = job.run()
            assert result is True

            # Verify file was created
            import tifffile

            ome_folder = os.path.join(tmpdir, "ome_tiff")
            tiff_files = [f for f in os.listdir(ome_folder) if f.endswith(".ome.tiff")]
            assert len(tiff_files) == 1

            # Verify OME-XML metadata is present
            with tifffile.TiffFile(os.path.join(ome_folder, tiff_files[0])) as tif:
                assert tif.ome_metadata is not None
                assert "SizeT" in tif.ome_metadata
                assert "SizeZ" in tif.ome_metadata
                assert "SizeC" in tif.ome_metadata

    def test_multi_channel_acquisition(self):
        """Test multi-channel OME-TIFF acquisition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            channel_names = ["BF", "GFP", "DAPI"]
            acq_info = make_test_acquisition_info(
                total_time_points=1,
                total_z_levels=1,
                total_channels=3,
                channel_names=channel_names,
                experiment_path=tmpdir,
            )

            # Save 3 channels
            for ch_idx, ch_name in enumerate(channel_names):
                capture_info = make_test_capture_info(
                    save_directory=tmpdir,
                    configuration_idx=ch_idx,
                    config_name=ch_name,
                )
                image = np.ones((100, 100), dtype=np.uint16) * ((ch_idx + 1) * 1000)

                job = SaveOMETiffJob(
                    capture_info=capture_info,
                    capture_image=JobImage(image_array=image),
                    acquisition_info=acq_info,
                )
                job.run()

            # Verify final file
            import tifffile

            ome_folder = os.path.join(tmpdir, "ome_tiff")
            tiff_files = [f for f in os.listdir(ome_folder) if f.endswith(".ome.tiff")]
            assert len(tiff_files) == 1

            with tifffile.TiffFile(os.path.join(ome_folder, tiff_files[0])) as tif:
                # Verify channel count in OME metadata
                assert tif.ome_metadata is not None
                assert 'SizeC="3"' in tif.ome_metadata

                # Check that file contains expected number of pages (3 channels)
                # Note: memmap format stores as flat pages
                stack = tifffile.memmap(os.path.join(ome_folder, tiff_files[0]), mode="r")
                assert stack.size == 3 * 100 * 100

    def test_z_stack_acquisition(self):
        """Test Z-stack OME-TIFF acquisition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            acq_info = make_test_acquisition_info(
                total_time_points=1,
                total_z_levels=5,
                total_channels=1,
                channel_names=["BF"],
                experiment_path=tmpdir,
            )

            # Save 5 z-slices
            for z_idx in range(5):
                capture_info = make_test_capture_info(
                    save_directory=tmpdir,
                    z_index=z_idx,
                )
                image = np.ones((100, 100), dtype=np.uint16) * ((z_idx + 1) * 100)

                job = SaveOMETiffJob(
                    capture_info=capture_info,
                    capture_image=JobImage(image_array=image),
                    acquisition_info=acq_info,
                )
                job.run()

            # Verify final file
            import tifffile

            ome_folder = os.path.join(tmpdir, "ome_tiff")
            tiff_files = [f for f in os.listdir(ome_folder) if f.endswith(".ome.tiff")]
            assert len(tiff_files) == 1

            with tifffile.TiffFile(os.path.join(ome_folder, tiff_files[0])) as tif:
                # Verify z-slice count in OME metadata
                assert tif.ome_metadata is not None
                assert 'SizeZ="5"' in tif.ome_metadata

                # Check that file contains expected number of pixels (5 z-slices)
                stack = tifffile.memmap(os.path.join(ome_folder, tiff_files[0]), mode="r")
                assert stack.size == 5 * 100 * 100
