"""get_pixel_format() reports the format the sensor delivers; see _DELIVERED_PIXEL_FORMAT in the driver."""

import pytest

import squid.logging
from squid.abc import CameraPixelFormat
from squid.config import CameraConfig, CameraVariant, ToupcamCameraModel
import control.camera_toupcam as camera_toupcam

KMA26000 = ToupcamCameraModel.ITR3CMOS26000KMA
KMA09000 = ToupcamCameraModel.ITR3CMOS09000KMA  # not in the table: behaves as before


def _camera(requested, binning, model):
    """A ToupcamCamera with __init__ skipped: only the state _refresh_delivered_pixel_format() reads."""
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._config = CameraConfig(camera_type=CameraVariant.TOUPCAM, default_pixel_format=requested, camera_model=model)
    cam._pixel_format = cam._delivered_pixel_format = requested
    cam._binning = binning
    cam._log = squid.logging.get_logger("test_toupcam_pixel_format")
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

    cam._refresh_delivered_pixel_format()

    assert cam.get_pixel_format() == delivered


def test_pixel_format_follows_binning_changes():
    cam = _camera(CameraPixelFormat.MONO16, (2, 2), KMA26000)
    cam._refresh_delivered_pixel_format()
    assert cam.get_pixel_format() == CameraPixelFormat.MONO12

    cam._binning = (1, 1)
    cam._refresh_delivered_pixel_format()

    assert cam.get_pixel_format() == CameraPixelFormat.MONO16
