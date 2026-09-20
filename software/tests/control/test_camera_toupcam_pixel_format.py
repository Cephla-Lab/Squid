"""Pixel formats mean what the sensor delivers; see _DELIVERED_PIXEL_FORMAT in the driver."""

from types import SimpleNamespace

import pytest

import squid.logging
from squid.abc import CameraFrameFormat, CameraPixelFormat
from squid.config import CameraConfig, CameraVariant, ToupcamCameraModel
import control.camera_toupcam as camera_toupcam

KMA26000 = ToupcamCameraModel.ITR3CMOS26000KMA
KMA09000 = ToupcamCameraModel.ITR3CMOS09000KMA  # not in the table: behaves as before


def _camera(requested, binning, model=KMA26000, max_bit_depth=16, frame_format=CameraFrameFormat.RAW):
    """A ToupcamCamera with __init__ skipped: only the state the format and black-level code reads."""
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._config = CameraConfig(camera_type=CameraVariant.TOUPCAM, default_pixel_format=requested, camera_model=model)
    cam._capabilities = SimpleNamespace(max_bit_depth=max_bit_depth)
    cam._pixel_format = requested
    cam._binning = binning
    cam._log = squid.logging.get_logger("test_toupcam_pixel_format")
    cam.get_frame_format = lambda: frame_format
    return cam


@pytest.mark.parametrize(
    "requested, binning, model, delivered",
    [
        (CameraPixelFormat.MONO16, (1, 1), KMA26000, CameraPixelFormat.MONO16),
        (CameraPixelFormat.MONO16, (2, 2), KMA26000, CameraPixelFormat.MONO12),
        (CameraPixelFormat.MONO16, (3, 3), KMA26000, CameraPixelFormat.MONO12),
        (CameraPixelFormat.MONO12, (1, 1), KMA26000, CameraPixelFormat.MONO16),  # the sensor is 16-bit unbinned
        (CameraPixelFormat.MONO16, (2, 2), KMA09000, CameraPixelFormat.MONO16),
        (CameraPixelFormat.MONO8, (2, 2), KMA26000, CameraPixelFormat.MONO8),
        (CameraPixelFormat.RGB48, (2, 2), KMA26000, CameraPixelFormat.RGB48),
    ],
)
def test_delivered_pixel_format(requested, binning, model, delivered):
    cam = _camera(requested, binning, model)

    cam._refresh_pixel_format()

    assert cam.get_pixel_format() == delivered


def test_pixel_format_follows_binning_changes():
    cam = _camera(CameraPixelFormat.MONO16, (2, 2))
    cam._refresh_pixel_format()
    assert cam.get_pixel_format() == CameraPixelFormat.MONO12

    cam._binning = (1, 1)
    cam._refresh_pixel_format()

    assert cam.get_pixel_format() == CameraPixelFormat.MONO16


@pytest.mark.parametrize(
    "binning, model, offered",
    [
        ((1, 1), KMA26000, [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]),
        ((2, 2), KMA26000, [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12]),
        ((2, 2), KMA09000, [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]),
    ],
)
def test_only_formats_the_sensor_can_deliver_are_offered(binning, model, offered):
    cam = _camera(CameraPixelFormat.MONO16, binning, model)

    assert list(cam.get_available_pixel_formats()) == offered


def test_rgb_output_also_offers_the_color_formats():
    cam = _camera(CameraPixelFormat.RGB24, (1, 1), frame_format=CameraFrameFormat.RGB)

    formats = list(cam.get_available_pixel_formats())

    assert formats[:2] == [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]
    assert {CameraPixelFormat.RGB24, CameraPixelFormat.RGB32, CameraPixelFormat.RGB48} <= set(formats)


@pytest.mark.parametrize(
    "pixel_format, max_bit_depth, factor",
    [
        (CameraPixelFormat.MONO8, 16, 1),
        (CameraPixelFormat.MONO12, 16, 256),  # binned: still 16-bit units on the SDK side
        (CameraPixelFormat.MONO16, 16, 256),
        (CameraPixelFormat.MONO16, 12, 16),  # a 12-bit ADC scales its black level by 16
        (CameraPixelFormat.RGB24, 16, 1),
        (CameraPixelFormat.RGB48, 16, 256),
    ],
)
def test_black_level_scales_with_the_sdk_mode_and_adc_depth_not_the_label(pixel_format, max_bit_depth, factor):
    cam = _camera(pixel_format, (2, 2), max_bit_depth=max_bit_depth)

    assert cam._get_black_level_factor() == factor
