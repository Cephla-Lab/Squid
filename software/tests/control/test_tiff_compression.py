"""Tests for optional zlib compression of saved TIFFs (control._def.TIFF_COMPRESSION_LEVEL)."""

import os
import time

import imageio.v2 as imageio
import numpy as np
import pytest
import tifffile

import control._def as _def
from control import utils_acquisition
from control._def import FileSavingOption
from control.core.job_processing import CaptureInfo, JobImage, SaveImageJob
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings
import squid.abc


@pytest.fixture
def compression_level(monkeypatch):
    """Set TIFF_COMPRESSION_LEVEL for the duration of a test."""

    def set_level(level):
        monkeypatch.setattr(_def, "TIFF_COMPRESSION_LEVEL", level)

    return set_level


@pytest.fixture
def channel():
    return AcquisitionChannel(
        name="BF LED matrix full",
        display_color="#FFFFFF",
        camera=1,
        illumination_settings=IlluminationSettings(illumination_channel="BF LED matrix full", intensity=5.0),
        camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
        z_offset_um=0.0,
    )


@pytest.fixture
def compressible_image():
    """A frame with the smooth structure of a real image, so compression can do something."""
    y, x = np.mgrid[0:256, 0:256]
    return ((np.sin(x / 16) * np.cos(y / 24) + 1) * 8000).astype(np.uint16)


def capture_info(save_directory, channel, file_id="0_0_0"):
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=channel,
        save_directory=str(save_directory),
        file_id=file_id,
        region_id=1,
        fov=0,
        configuration_idx=0,
        z_piezo_um=None,
        time_point=0,
    )


def save_individual_image(image, save_directory, channel, file_id="0_0_0"):
    utils_acquisition.save_image(
        image=image, file_id=file_id, save_directory=str(save_directory), config=channel, is_color=False
    )
    return utils_acquisition.get_image_filepath(str(save_directory), file_id, channel.name, image.dtype)


def save_stack_page(image, save_directory, channel, monkeypatch):
    monkeypatch.setattr(_def, "FILE_SAVING_OPTION", FileSavingOption.MULTI_PAGE_TIFF)
    info = capture_info(save_directory, channel)
    job = SaveImageJob(capture_info=info, capture_image=JobImage(image_array=image))
    assert job.run()
    return save_directory / f"{info.region_id}_{info.fov:0{_def.FILE_ID_PADDING}}_stack.tiff"


class TestCompressionKwargs:
    def test_level_zero_writes_no_compression_arguments(self, compression_level):
        compression_level(0)
        assert utils_acquisition.tiff_compression_kwargs() == {}

    def test_level_is_read_when_the_write_happens(self, compression_level):
        """Preferences assigns control._def at runtime, so the level can't be captured at import."""
        compression_level(6)
        assert utils_acquisition.tiff_compression_kwargs() == {
            "compression": "zlib",
            "compressionargs": {"level": 6},
            "predictor": True,
        }


class TestIndividualImages:
    def test_uncompressed_by_default(self, tmp_path, channel, compressible_image, compression_level):
        compression_level(0)
        path = save_individual_image(compressible_image, tmp_path, channel)

        with tifffile.TiffFile(path) as tif:
            assert tif.pages[0].compression == tifffile.COMPRESSION.NONE

    def test_compressed_and_lossless(self, tmp_path, channel, compressible_image, compression_level):
        compression_level(6)
        path = save_individual_image(compressible_image, tmp_path, channel)

        with tifffile.TiffFile(path) as tif:
            page = tif.pages[0]
            assert page.compression == tifffile.COMPRESSION.ADOBE_DEFLATE
            assert page.predictor == tifffile.PREDICTOR.HORIZONTAL
        np.testing.assert_array_equal(tifffile.imread(path), compressible_image)

    def test_smaller_than_uncompressed(self, tmp_path, channel, compressible_image, compression_level):
        off_dir = tmp_path / "off"
        on_dir = tmp_path / "on"
        off_dir.mkdir()
        on_dir.mkdir()

        compression_level(0)
        uncompressed = save_individual_image(compressible_image, off_dir, channel)
        compression_level(6)
        compressed = save_individual_image(compressible_image, on_dir, channel)

        assert os.path.getsize(compressed) < os.path.getsize(uncompressed)

    def test_non_tiff_format_is_unaffected(self, tmp_path, channel, compression_level, monkeypatch):
        """8 bit images go to IMAGE_FORMAT (bmp by default), whose plugin rejects these arguments."""
        compression_level(6)
        monkeypatch.setattr(_def.Acquisition, "IMAGE_FORMAT", "bmp")
        image = np.full((32, 32), 42, dtype=np.uint8)

        path = save_individual_image(image, tmp_path, channel)

        assert path.endswith(".bmp")
        np.testing.assert_array_equal(imageio.imread(path), image)


class TestMultiPageTiff:
    def test_uncompressed_by_default(self, tmp_path, channel, compressible_image, compression_level, monkeypatch):
        compression_level(0)
        path = save_stack_page(compressible_image, tmp_path, channel, monkeypatch)

        with tifffile.TiffFile(path) as tif:
            assert tif.pages[0].compression == tifffile.COMPRESSION.NONE

    def test_compressed_and_lossless(self, tmp_path, channel, compressible_image, compression_level, monkeypatch):
        compression_level(6)
        path = save_stack_page(compressible_image, tmp_path, channel, monkeypatch)

        with tifffile.TiffFile(path) as tif:
            page = tif.pages[0]
            assert page.compression == tifffile.COMPRESSION.ADOBE_DEFLATE
            assert page.predictor == tifffile.PREDICTOR.HORIZONTAL
            # The channel name in PageName (285) and the JSON metadata must survive compression.
            assert page.tags[285].value.rstrip("\x00") == channel.name
        np.testing.assert_array_equal(tifffile.imread(path), compressible_image)
