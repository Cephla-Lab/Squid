"""Tests for the SaveResult / ZarrWriteResult payloads returned by save jobs.

Save jobs must report which plane they wrote and which on-disk paths that write
completes, so the main process can build a transfer manifest of files that are
safe to move off the acquisition disk.  The results cross a multiprocessing
queue, so they must be picklable dataclasses of plain types.
"""

import os
import pickle
import time

import numpy as np
import pytest

import control._def
import squid.abc
from control import utils_acquisition
from control._def import FileSavingOption
from control.core.job_processing import (
    AcquisitionInfo,
    CaptureInfo,
    JobImage,
    SaveImageJob,
    SaveOMETiffJob,
    SaveResult,
    SaveZarrJob,
    ZarrWriteResult,
    ZarrWriterInfo,
)
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings

CHANNEL_NAMES = ("BF LED matrix full", "Fluorescence 488 nm Ex")


def make_channel(name: str = CHANNEL_NAMES[0]) -> AcquisitionChannel:
    return AcquisitionChannel(
        name=name,
        illumination_settings=IlluminationSettings(illumination_channel="LED", intensity=50.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
    )


def make_capture_info(
    save_directory: str,
    region_id="A1",
    fov: int = 0,
    z_index: int = 0,
    config_idx: int = 0,
    time_point: int = 0,
) -> CaptureInfo:
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None),
        z_index=z_index,
        capture_time=time.time(),
        configuration=make_channel(CHANNEL_NAMES[config_idx]),
        save_directory=str(save_directory),
        file_id=f"{region_id}_{fov}_{z_index}",
        region_id=region_id,
        fov=fov,
        configuration_idx=config_idx,
        time_point=time_point,
    )


def tiny_image() -> np.ndarray:
    return np.arange(64, dtype=np.uint16).reshape(8, 8)


def assert_picklable(result) -> None:
    assert pickle.loads(pickle.dumps(result)) == result


@pytest.fixture
def enable_simulated_io(monkeypatch):
    monkeypatch.setattr(control._def, "SIMULATED_DISK_IO_ENABLED", True)
    monkeypatch.setattr(control._def, "SIMULATED_DISK_IO_SPEED_MB_S", 100000.0)


class TestSaveImageJobResult:
    def test_individual_image_reports_immediate_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
        image = tiny_image()
        info = make_capture_info(tmp_path, region_id="A1", fov=3, z_index=2, config_idx=1, time_point=4)

        result = SaveImageJob(capture_info=info, capture_image=JobImage(image_array=image)).run()

        expected_path = utils_acquisition.get_image_filepath(
            str(tmp_path), info.file_id, info.configuration.name, image.dtype
        )
        assert isinstance(result, SaveResult)
        assert result.immediate_paths == (os.path.abspath(expected_path),)
        assert result.unit_paths == ()
        assert os.path.exists(expected_path)
        assert result.bytes_written == os.path.getsize(expected_path)
        assert (result.time_point, result.region_id, result.fov) == (4, "A1", 3)
        assert (result.z_index, result.channel_idx) == (2, 1)
        assert result.unit_complete is None
        assert_picklable(result)

    def test_multi_page_tiff_reports_unit_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", FileSavingOption.MULTI_PAGE_TIFF)
        image = tiny_image()
        info = make_capture_info(tmp_path, region_id="B2", fov=1, z_index=0, config_idx=0, time_point=0)

        result = SaveImageJob(capture_info=info, capture_image=JobImage(image_array=image)).run()

        expected_path = os.path.join(str(tmp_path), f"B2_{1:0{control._def.FILE_ID_PADDING}}_stack.tiff")
        assert isinstance(result, SaveResult)
        assert result.immediate_paths == ()
        assert result.unit_paths == (os.path.abspath(expected_path),)
        assert result.unit_kind == "file"
        assert result.unit_complete is None
        assert result.bytes_written == image.nbytes
        assert os.path.exists(expected_path)
        assert_picklable(result)

    def test_simulation_reports_identity_without_paths(self, tmp_path, monkeypatch, enable_simulated_io):
        monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", FileSavingOption.INDIVIDUAL_IMAGES)
        info = make_capture_info(tmp_path, region_id="C3", fov=7, z_index=5, config_idx=1, time_point=2)

        result = SaveImageJob(capture_info=info, capture_image=JobImage(image_array=tiny_image())).run()

        assert isinstance(result, SaveResult)
        assert result.immediate_paths == ()
        assert result.unit_paths == ()
        assert result.bytes_written > 0
        assert (result.time_point, result.region_id, result.fov) == (2, "C3", 7)
        assert (result.z_index, result.channel_idx) == (5, 1)
        assert not list(tmp_path.iterdir())
        assert_picklable(result)


class TestSaveOMETiffJobResult:
    def _acquisition_info(self, tmp_path) -> AcquisitionInfo:
        return AcquisitionInfo(
            total_time_points=1,
            total_z_levels=1,
            total_channels=2,
            channel_names=list(CHANNEL_NAMES),
            experiment_path=str(tmp_path),
        )

    def test_unit_complete_flips_on_last_plane(self, tmp_path):
        acq_info = self._acquisition_info(tmp_path)
        save_dir = tmp_path / "0"
        save_dir.mkdir()
        expected_path = os.path.join(str(tmp_path), "ome_tiff", f"A1_{0:0{control._def.FILE_ID_PADDING}}.ome.tiff")

        results = []
        for config_idx in range(2):
            info = make_capture_info(save_dir, region_id="A1", fov=0, config_idx=config_idx)
            job = SaveOMETiffJob(capture_info=info, capture_image=JobImage(image_array=tiny_image()))
            job.acquisition_info = acq_info
            results.append(job.run())

        for result, expect_complete in zip(results, (False, True)):
            assert isinstance(result, SaveResult)
            assert result.immediate_paths == ()
            assert result.unit_paths == (os.path.abspath(expected_path),)
            assert result.unit_kind == "file"
            assert result.unit_complete is expect_complete
            assert result.bytes_written == tiny_image().nbytes
            assert_picklable(result)

        assert results[1].channel_idx == 1
        assert os.path.exists(expected_path)

    def test_simulation_reports_identity_without_paths(self, tmp_path, enable_simulated_io):
        info = make_capture_info(tmp_path, region_id="D4", fov=2, z_index=0, config_idx=1, time_point=0)
        job = SaveOMETiffJob(capture_info=info, capture_image=JobImage(image_array=tiny_image()))
        job.acquisition_info = self._acquisition_info(tmp_path)

        result = job.run()

        assert isinstance(result, SaveResult)
        assert result.immediate_paths == ()
        assert result.unit_paths == ()
        assert result.unit_complete is False
        assert (result.time_point, result.region_id, result.fov) == (0, "D4", 2)
        assert result.channel_idx == 1
        assert_picklable(result)


class TestSaveZarrJobResult:
    def teardown_method(self):
        SaveZarrJob.clear_writers()

    def test_5d_store_reports_timepoint_chunk_dir(self, tmp_path):
        pytest.importorskip("tensorstore")
        zarr_info = ZarrWriterInfo(base_path=str(tmp_path), t_size=1, c_size=1, z_size=1, region_fov_counts={"A1": 1})
        info = make_capture_info(tmp_path, region_id="A1", fov=0, time_point=0)
        job = SaveZarrJob(capture_info=info, capture_image=JobImage(image_array=tiny_image()))
        job.zarr_writer_info = zarr_info

        result = job.run()
        SaveZarrJob.finalize_all_writers()

        output_path = zarr_info.get_output_path("A1", 0)
        assert isinstance(result, ZarrWriteResult)
        assert result.region_id == "A1"
        assert result.unit_paths == (os.path.join(output_path, "c", "0"),)
        assert result.unit_kind == "dir"
        assert result.unit_complete is None
        assert result.bytes_written == tiny_image().nbytes
        assert os.path.isdir(result.unit_paths[0])
        assert_picklable(result)

    def test_6d_store_unit_is_the_fovs_own_chunk_directory(self, tmp_path):
        pytest.importorskip("tensorstore")
        zarr_info = ZarrWriterInfo(
            base_path=str(tmp_path),
            t_size=1,
            c_size=1,
            z_size=1,
            is_hcs=False,
            use_6d_fov=True,
            region_fov_counts={"A1": 2},
        )
        info = make_capture_info(tmp_path, region_id="A1", fov=1, time_point=0)
        job = SaveZarrJob(capture_info=info, capture_image=JobImage(image_array=tiny_image()))
        job.zarr_writer_info = zarr_info

        result = job.run()
        SaveZarrJob.finalize_all_writers()

        output_path = zarr_info.get_output_path("A1", 1)
        # 6D chunk grid is (FOV, T, ...) with FOV and T extents of 1: this FOV's timepoint directory is
        # final on its own, so a region larger than the disk can still be offloaded FOV by FOV.
        assert result.unit_paths == (os.path.join(output_path, "c", "1", "0"),)
        assert os.path.isdir(os.path.join(output_path, "c", "1", "0")), "chunks of fov 1 / t 0 live under c/1/0"
        assert result.unit_kind == "dir"
        assert result.region_id == "A1"
        assert_picklable(result)

    def test_simulation_reports_identity_without_paths(self, tmp_path, enable_simulated_io):
        info = make_capture_info(tmp_path, region_id="E5", fov=4, z_index=1, config_idx=1, time_point=3)
        job = SaveZarrJob(capture_info=info, capture_image=JobImage(image_array=tiny_image()))
        job.zarr_writer_info = ZarrWriterInfo(base_path=str(tmp_path), t_size=4, c_size=2, z_size=2)

        result = job.run()

        assert isinstance(result, ZarrWriteResult)
        assert result.unit_paths == ()
        assert result.region_id == "E5"
        assert (result.time_point, result.fov, result.z_index) == (3, 4, 1)
        assert result.channel_name == CHANNEL_NAMES[1]
        assert not list(tmp_path.iterdir())
        assert_picklable(result)


def test_zarr_write_result_defaults_keep_old_construction_working():
    result = ZarrWriteResult(fov=1, time_point=2, z_index=3, channel_name="BF", region_idx=4)
    assert result.region_id == ""
    assert result.unit_paths == ()
    assert result.unit_kind == "dir"
    assert result.bytes_written == 0
    assert result.unit_complete is None
