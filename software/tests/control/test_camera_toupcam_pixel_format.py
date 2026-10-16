"""Pixel formats mean what the sensor delivers; see _DELIVERED_PIXEL_FORMAT in the driver.

MONO12PACKED is the packed 12-bit SDK mode, only available at 1x1 binning; see _pixel_format_for_binning.
"""

import threading
from types import SimpleNamespace

import numpy as np
import pytest

import squid.logging
from squid.abc import CameraFrameFormat, CameraPixelFormat
from squid.config import CameraConfig, CameraVariant, ToupcamCameraModel
import control.camera_toupcam as camera_toupcam

KMA26000 = ToupcamCameraModel.ITR3CMOS26000KMA
KMA09000 = ToupcamCameraModel.ITR3CMOS09000KMA  # not in the table: behaves as before
ITR10 = ToupcamCameraModel.ITR10CMOS26000KMA  # not in the table, but has the packed 12-bit mode


def _camera(
    requested,
    binning,
    model=KMA26000,
    max_bit_depth=16,
    frame_format=CameraFrameFormat.RAW,
    has_raw12_pack=False,
):
    """A ToupcamCamera with __init__ skipped: only the state the format and black-level code reads."""
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._config = SimpleNamespace(camera_model=model)
    cam._capabilities = SimpleNamespace(max_bit_depth=max_bit_depth, has_raw12_pack=has_raw12_pack)
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


@pytest.mark.parametrize(
    "binning, model, offered",
    [
        ((1, 1), KMA26000, [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]),
        ((2, 2), KMA26000, [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12]),
        # unverified sensor: every 16-bit-mode label stays selectable, so the current one is always listed
        (
            (2, 2),
            KMA09000,
            [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12, CameraPixelFormat.MONO14, CameraPixelFormat.MONO16],
        ),
    ],
)
def test_only_formats_the_sensor_can_deliver_are_offered(binning, model, offered):
    cam = _camera(CameraPixelFormat.MONO16, binning, model)

    assert list(cam.get_available_pixel_formats()) == offered


def test_rgb_output_also_offers_the_color_formats():
    cam = _camera(CameraPixelFormat.RGB24, (1, 1), frame_format=CameraFrameFormat.RGB)

    assert list(cam.get_available_pixel_formats()) == [
        CameraPixelFormat.MONO8,
        CameraPixelFormat.MONO16,
        CameraPixelFormat.RGB24,
        CameraPixelFormat.RGB32,
        CameraPixelFormat.RGB48,
    ]


@pytest.mark.parametrize(
    "pixel_format, max_bit_depth, factor",
    [
        (CameraPixelFormat.MONO8, 16, 1),
        (CameraPixelFormat.MONO12, 16, 256),  # binned: still 16-bit units on the SDK side
        (CameraPixelFormat.MONO16, 16, 256),
        (CameraPixelFormat.MONO16, 12, 16),  # a 12-bit ADC scales its black level by 16
        (CameraPixelFormat.MONO12PACKED, 16, 16),  # the packed mode is 12-bit on the SDK side whatever the ADC
        (CameraPixelFormat.RGB24, 16, 1),
        (CameraPixelFormat.RGB48, 16, 256),
    ],
)
def test_black_level_scales_with_the_sdk_mode_and_adc_depth_not_the_label(pixel_format, max_bit_depth, factor):
    cam = _camera(pixel_format, (2, 2), max_bit_depth=max_bit_depth)

    assert cam._get_black_level_factor() == factor


@pytest.mark.parametrize(
    "requested, binning, resolved",
    [
        (CameraPixelFormat.MONO12, (1, 1), CameraPixelFormat.MONO12PACKED),
        (CameraPixelFormat.MONO12PACKED, (1, 1), CameraPixelFormat.MONO12PACKED),
        (CameraPixelFormat.MONO12PACKED, (2, 2), CameraPixelFormat.MONO12),
        (CameraPixelFormat.MONO12, (2, 2), CameraPixelFormat.MONO12),
        (CameraPixelFormat.MONO16, (1, 1), CameraPixelFormat.MONO16),  # only the 12-bit formats are swapped
        (CameraPixelFormat.MONO8, (1, 1), CameraPixelFormat.MONO8),
    ],
)
def test_twelve_bit_is_packed_at_1x1_on_a_camera_with_packed_support(requested, binning, resolved):
    cam = _camera(requested, binning, model=ITR10, has_raw12_pack=True)

    assert cam._pixel_format_for_binning(requested, binning) == resolved


def test_without_packed_support_mono12_stays_and_packed_is_refused():
    cam = _camera(CameraPixelFormat.MONO12, (1, 1), model=KMA09000)

    assert cam._pixel_format_for_binning(CameraPixelFormat.MONO12, (1, 1)) == CameraPixelFormat.MONO12
    with pytest.raises(ValueError, match="MONO12PACKED"):
        cam._pixel_format_for_binning(CameraPixelFormat.MONO12PACKED, (1, 1))


@pytest.mark.parametrize(
    "binning, offered",
    [
        (
            (1, 1),
            [
                CameraPixelFormat.MONO8,
                CameraPixelFormat.MONO12PACKED,
                CameraPixelFormat.MONO14,
                CameraPixelFormat.MONO16,
            ],
        ),
        (
            (2, 2),
            [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12, CameraPixelFormat.MONO14, CameraPixelFormat.MONO16],
        ),
    ],
)
def test_packed_is_offered_in_place_of_mono12_at_1x1_only(binning, offered):
    cam = _camera(CameraPixelFormat.MONO16, binning, model=ITR10, has_raw12_pack=True)

    assert list(cam.get_available_pixel_formats()) == offered


def test_packed_is_its_own_sdk_mode_so_the_delivered_table_leaves_it_alone():
    cam = _camera(CameraPixelFormat.MONO12PACKED, (1, 1), model=KMA26000, has_raw12_pack=True)

    cam._refresh_pixel_format()

    assert cam.get_pixel_format() == CameraPixelFormat.MONO12PACKED


def test_packed_max_value_matches_mono12():
    assert CameraPixelFormat.MONO12PACKED.bit_depth == 12
    assert CameraPixelFormat.MONO12PACKED.max_value(np.uint16) == CameraPixelFormat.MONO12.max_value(np.uint16) == 65520


_W, _H = 8, 4


def _callback_camera(pixel_format, pixels: np.ndarray):
    """A ToupcamCamera whose fake SDK 'fills' the read buffer with ``pixels``; the real _on_frame_callback runs."""
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._raw_frame_callback_lock = threading.Lock()
    cam._internal_read_buffer = pixels.astype(np.uint16).tobytes()
    cam._current_frame = None
    cam._trigger_sent = True
    cam._raw_camera_stream_started = False
    cam._config = CameraConfig(camera_type=CameraVariant.TOUPCAM, default_pixel_format=pixel_format)
    cam._software_crop_width_ratio = 1.0
    cam._software_crop_height_ratio = 1.0
    cam._diag_frame_log_every = 30
    cam._diag_last_callback_start_ns = None
    cam._log = squid.logging.get_logger("test_toupcam_pixel_format")
    cam._camera = SimpleNamespace(PullImageV2=lambda buffer, bits, info: None)
    cam._get_pixel_size_in_bytes = lambda: 2
    cam.get_frame_format = lambda: CameraFrameFormat.RAW
    cam.get_pixel_format = lambda: pixel_format
    cam.get_region_of_interest = lambda: (0, 0, _W, _H)
    cam.get_binning = lambda: (1, 1)
    captured = {}
    cam._propogate_frame = lambda frame: captured.__setitem__("frame", np.array(frame.frame))
    return cam, captured


def test_packed_frames_are_left_aligned_like_the_16_bit_modes():
    """PullImageV2 unpacks to [0, 4095]; the frame must land on the same scale as MONO12 (clipped = 65520)."""
    pixels = np.arange(_W * _H, dtype=np.uint16).reshape(_H, _W) * 100  # 0..3100, within 12 bits
    pixels[0, 0] = 4095
    cam, captured = _callback_camera(CameraPixelFormat.MONO12PACKED, pixels)

    cam._on_frame_callback()

    assert captured["frame"].dtype == np.uint16
    np.testing.assert_array_equal(captured["frame"], pixels << 4)
    assert captured["frame"][0, 0] == CameraPixelFormat.MONO12PACKED.max_value(np.uint16)


def test_unpacked_16_bit_frames_are_passed_through():
    pixels = np.arange(_W * _H, dtype=np.uint16).reshape(_H, _W) * 2000
    cam, captured = _callback_camera(CameraPixelFormat.MONO12, pixels)

    cam._on_frame_callback()

    np.testing.assert_array_equal(captured["frame"], pixels)
