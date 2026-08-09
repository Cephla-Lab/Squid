import numpy as np
import pytest

import squid.config
from control.utils import FlipVariant, crop_image, rotate_and_flip_image
from squid.camera.utils import SimulatedCamera
from squid.config import CameraPixelFormat

# The ambient camera config is 4168x4168, so a single RGB48 frame is ~100 MB. Tests that do not
# specifically need the ambient geometry override it with this tiny frame instead.
SMALL_FRAME = {"crop_width": 64, "crop_height": 48, "default_binning": (1, 1)}


def make_sim(pixel_format, **overrides):
    update = {"serial_number": "SIM-RGB", "default_pixel_format": pixel_format}
    update.update(overrides)
    config = squid.config.get_camera_config().model_copy(update=update)
    return SimulatedCamera(config, hw_trigger_fn=None, hw_set_strobe_delay_ms_fn=None)


def rgb_test_image(dtype):
    """(48, 64, 3) image with a distinct constant per channel plus a unique corner marker.

    The per-channel constants make a channel collapse (RGB->gray) or a channel reorder detectable;
    the corner marker makes it detectable that a transform actually moved pixels.
    """
    image = np.zeros((48, 64, 3), dtype=dtype)
    for channel, value in enumerate((10, 20, 30)):
        image[..., channel] = value
        image[0, 0, channel] = channel + 1  # marker: 1, 2, 3
    return image


def test_rgb24_frames_are_3_channel_uint8():
    cam = make_sim(CameraPixelFormat.RGB24)
    cam.send_trigger()
    frame = cam.read_camera_frame()
    assert frame.frame.ndim == 3 and frame.frame.shape[2] == 3
    assert frame.frame.dtype == np.uint8
    assert frame.is_color()
    assert cam.is_color is True


def test_rgb48_frames_are_3_channel_uint16():
    cam = make_sim(CameraPixelFormat.RGB48)
    cam.send_trigger()
    frame = cam.read_camera_frame()
    assert frame.frame.ndim == 3 and frame.frame.shape[2] == 3
    assert frame.frame.dtype == np.uint16


def test_rgb_formats_advertised():
    cam = make_sim(CameraPixelFormat.RGB24)
    formats = cam.get_available_pixel_formats()
    assert CameraPixelFormat.RGB24 in formats
    assert CameraPixelFormat.RGB48 in formats


# --- _process_raw_frame transforms must not mangle the channel axis ---------------------------
# AbstractCamera._process_raw_frame pipes every frame through rotate_and_flip_image + crop_image.
# Those two helpers are shared by all cameras but had no test coverage for any format, so these
# lock in the (H, W, 3) contract that colour cameras (and Tasks 11-13) depend on.


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
@pytest.mark.parametrize("angle", [None, 90, -90, 180])
@pytest.mark.parametrize("flip", [None, FlipVariant.VERTICAL, FlipVariant.HORIZONTAL, FlipVariant.BOTH])
def test_rotate_and_flip_image_preserves_rgb_channels(dtype, angle, flip):
    image = rgb_test_image(dtype)

    rotated = rotate_and_flip_image(image, rotate_image_angle=angle, flip_image=flip)

    assert rotated.ndim == 3 and rotated.shape[2] == 3
    assert rotated.dtype == dtype
    # A quarter turn transposes height and width; 180/None do not.
    assert rotated.shape[:2] == ((64, 48) if angle in (90, -90) else (48, 64))
    # Each channel still holds exactly its own two values: no collapse to grayscale, no reorder.
    for channel, value in enumerate((10, 20, 30)):
        assert sorted(np.unique(rotated[..., channel]).tolist()) == sorted([value, channel + 1])


def test_rotate_and_flip_image_actually_moves_rgb_pixels():
    image = rgb_test_image(np.uint8)

    rotated = rotate_and_flip_image(image, rotate_image_angle=180, flip_image=None)

    # The (0, 0) marker ends up in the opposite corner, with its per-channel values intact.
    assert list(rotated[-1, -1]) == [1, 2, 3]
    assert list(rotated[0, 0]) == [10, 20, 30]


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_crop_image_preserves_rgb_channel_axis(dtype):
    image = rgb_test_image(dtype)

    cropped = crop_image(image, 32, 24)

    assert cropped.shape == (24, 32, 3)
    assert cropped.dtype == dtype
    # The centred crop dropped the (0, 0) marker but kept every channel's constant.
    for channel, value in enumerate((10, 20, 30)):
        assert np.unique(cropped[..., channel]).tolist() == [value]


def test_crop_image_with_none_dimensions_keeps_rgb_axis():
    image = rgb_test_image(np.uint8)

    # None means "do not crop this axis" - the channel axis must still survive.
    assert crop_image(image, None, 24).shape == (24, 64, 3)
    assert crop_image(image, 32, None).shape == (48, 32, 3)
    assert crop_image(image, None, None).shape == (48, 64, 3)


def test_process_raw_frame_keeps_rgb_3_channel_end_to_end():
    # A quarter turn is what makes crop_image genuinely cut in the SimulatedCamera path: the raw
    # frame is generated at post-crop size (get_resolution derives from crop_width/crop_height), so
    # without rotation the crop is a no-op. Rotated, the 48x64 frame becomes 64x48 and the 64x48
    # crop window trims 8 rows off each end -> 48x48.
    cam = make_sim(CameraPixelFormat.RGB24, rotate_image_angle=90, flip=FlipVariant.BOTH, **SMALL_FRAME)
    cam.send_trigger()

    frame = cam.read_camera_frame()

    assert frame.frame.shape == (48, 48, 3)
    assert frame.frame.dtype == np.uint8
    assert frame.is_color()


# --- white balance -----------------------------------------------------------------------------


def test_auto_white_balance_gains_takes_the_on_flag():
    """The per-camera settings tab's Auto WB button calls set_auto_white_balance_gains(on=...)
    from a Qt slot, so a signature that omits `on` fails as a swallowed TypeError with no
    white balance applied — on the RGB simulation path the docs point people at."""
    cam = make_sim(CameraPixelFormat.RGB24, **SMALL_FRAME)
    cam.set_white_balance_gains(2.0, 3.0, 4.0)

    assert cam.set_auto_white_balance_gains(on=True) == (1.0, 1.0, 1.0)
    assert cam.get_white_balance_gains() == (1.0, 1.0, 1.0)


def test_auto_white_balance_off_leaves_the_gains_alone():
    """Off is "stop auto-balancing", not "re-balance": the widget reads the gains back and
    re-applies them, which only makes sense if turning it off keeps what is there."""
    cam = make_sim(CameraPixelFormat.RGB24, **SMALL_FRAME)
    cam.set_white_balance_gains(2.0, 3.0, 4.0)

    assert cam.set_auto_white_balance_gains(on=False) == (2.0, 3.0, 4.0)
    assert cam.get_white_balance_gains() == (2.0, 3.0, 4.0)


# --- switching pixel format must invalidate the cached raw frame ------------------------------


def test_pixel_format_switch_invalidates_cached_frame():
    # Reachable from the live GUI pixel-format dropdown: without invalidation the cached mono frame
    # is np.roll'd and re-served, so frame.shape disagrees with frame_pixel_format / is_color().
    cam = make_sim(CameraPixelFormat.MONO16, **SMALL_FRAME)
    cam.send_trigger()
    assert cam.read_camera_frame().frame.ndim == 2

    cam.set_pixel_format(CameraPixelFormat.RGB24)
    cam.send_trigger()

    frame = cam.read_camera_frame()
    assert frame.frame.ndim == 3 and frame.frame.shape[2] == 3
    assert frame.frame.dtype == np.uint8
    assert frame.is_color()


def test_pixel_format_switch_back_to_mono_invalidates_cached_frame():
    cam = make_sim(CameraPixelFormat.RGB24, **SMALL_FRAME)
    cam.send_trigger()
    assert cam.read_camera_frame().frame.ndim == 3

    cam.set_pixel_format(CameraPixelFormat.MONO8)
    cam.send_trigger()

    frame = cam.read_camera_frame()
    assert frame.frame.ndim == 2
    assert frame.frame.dtype == np.uint8
    assert not frame.is_color()
