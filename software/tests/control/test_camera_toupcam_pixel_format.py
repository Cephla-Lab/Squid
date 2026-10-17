"""Pixel formats mean what the sensor delivers; see _DELIVERED_PIXEL_FORMAT in the driver.

MONO12PACKED is the packed 12-bit SDK mode; the ITR10CMOS26000KMA uses it unbinned (the camera only accepts it at 1x1).
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import numpy as np
import pytest

import squid.logging
import control.toupcam as toupcam
from squid.abc import CameraFrameFormat, CameraPixelFormat
from squid.config import CameraConfig, CameraVariant, ToupcamCameraModel
import control.camera_toupcam as camera_toupcam

KMA26000 = ToupcamCameraModel.ITR3CMOS26000KMA
KMA09000 = ToupcamCameraModel.ITR3CMOS09000KMA  # not in the table: behaves as before
ITR10 = ToupcamCameraModel.ITR10CMOS26000KMA  # packed 12-bit unbinned, 12-bit in the 16-bit mode binned

_RESOLUTIONS = {(1, 1): (6208, 4168), (2, 2): (3104, 2084), (3, 3): (2069, 1389)}


def _camera(requested, binning, model=KMA26000, max_bit_depth=16, frame_format=CameraFrameFormat.RAW):
    """A ToupcamCamera with __init__ skipped: the state the format, binning and black-level code reads, over a
    MagicMock SDK."""
    cam = object.__new__(camera_toupcam.ToupcamCamera)
    cam._config = SimpleNamespace(camera_model=model)
    cam._capabilities = SimpleNamespace(
        max_bit_depth=max_bit_depth, has_black_level=False, binning_to_resolution=_RESOLUTIONS
    )
    cam._pixel_format = requested
    cam._binning = binning
    cam._log = squid.logging.get_logger("test_toupcam_pixel_format")
    cam._camera = MagicMock()
    cam._trigger_lock = threading.RLock()
    cam.get_frame_format = lambda: frame_format
    cam.get_is_streaming = lambda: False
    cam._update_internal_settings = lambda: None  # strobe and buffer bookkeeping, not under test here
    return cam


@pytest.mark.parametrize(
    "requested, binning, model, delivered",
    [
        (CameraPixelFormat.MONO16, (1, 1), KMA26000, CameraPixelFormat.MONO16),
        (CameraPixelFormat.MONO16, (2, 2), KMA26000, CameraPixelFormat.MONO12),
        (CameraPixelFormat.MONO16, (3, 3), KMA26000, CameraPixelFormat.MONO12),
        (CameraPixelFormat.MONO12, (1, 1), KMA26000, CameraPixelFormat.MONO16),  # the sensor is 16-bit unbinned
        (CameraPixelFormat.MONO12PACKED, (1, 1), KMA26000, CameraPixelFormat.MONO16),  # no packed mode on the ITR3
        (CameraPixelFormat.MONO16, (2, 2), KMA09000, CameraPixelFormat.MONO16),
        (CameraPixelFormat.MONO8, (2, 2), KMA26000, CameraPixelFormat.MONO8),
        (CameraPixelFormat.RGB48, (2, 2), KMA26000, CameraPixelFormat.RGB48),
        (CameraPixelFormat.MONO16, (1, 1), ITR10, CameraPixelFormat.MONO12PACKED),  # 12-bit, packed, is all it has
        (CameraPixelFormat.MONO12, (1, 1), ITR10, CameraPixelFormat.MONO12PACKED),
        (CameraPixelFormat.MONO12PACKED, (2, 2), ITR10, CameraPixelFormat.MONO12),  # packed is 1x1 only
        (CameraPixelFormat.MONO8, (1, 1), ITR10, CameraPixelFormat.MONO8),
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
        ((1, 1), ITR10, [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12PACKED]),
        ((2, 2), ITR10, [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12]),
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


def test_a_12_bit_request_on_the_itr10_selects_the_packed_sdk_mode_unbinned_and_the_16_bit_mode_binned():
    cam = _camera(CameraPixelFormat.MONO8, (1, 1), model=ITR10)
    cam.set_pixel_format(CameraPixelFormat.MONO12)
    assert cam.get_pixel_format() == CameraPixelFormat.MONO12PACKED
    cam._camera.put_Option.assert_called_once_with(
        toupcam.TOUPCAM_OPTION_PIXEL_FORMAT, toupcam.TOUPCAM_PIXELFORMAT_RAW12PACK
    )

    cam = _camera(CameraPixelFormat.MONO8, (2, 2), model=ITR10)
    cam.set_pixel_format(CameraPixelFormat.MONO12PACKED)
    assert cam.get_pixel_format() == CameraPixelFormat.MONO12
    cam._camera.put_Option.assert_called_once_with(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)


def test_binning_leaves_the_packed_mode_before_resizing_and_enters_it_after():
    """The camera only accepts the packed mode at full resolution."""
    cam = _camera(CameraPixelFormat.MONO12PACKED, (1, 1), model=ITR10)

    cam.set_binning(2, 2)

    assert cam.get_pixel_format() == CameraPixelFormat.MONO12
    assert cam._camera.mock_calls == [
        call.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1),
        call.put_Size(3104, 2084),
    ]

    cam._camera.reset_mock()
    cam.set_binning(1, 1)

    assert cam.get_pixel_format() == CameraPixelFormat.MONO12PACKED
    assert cam._camera.mock_calls == [
        call.put_Size(6208, 4168),
        call.put_Option(toupcam.TOUPCAM_OPTION_PIXEL_FORMAT, toupcam.TOUPCAM_PIXELFORMAT_RAW12PACK),
    ]


def test_binning_relabels_without_changing_the_sdk_mode_on_the_itr3():
    """MONO16 and MONO12 are the same 16-bit SDK mode there; only the label follows the binning."""
    cam = _camera(CameraPixelFormat.MONO16, (1, 1), model=KMA26000)

    cam.set_binning(2, 2)

    assert cam.get_pixel_format() == CameraPixelFormat.MONO12
    assert cam._camera.mock_calls == [call.put_Size(3104, 2084)]


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
