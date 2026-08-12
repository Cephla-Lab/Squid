"""Contrast limits must be remembered per channel, in that channel's own dtype.

A channel's dtype is its camera's dtype: all of one camera's channels share it, and it changes
only when that camera's pixel format changes. So a camera switch must never reinterpret the
other camera's channels — which is what a single run-wide acquisition_dtype plus a global
rescale did, rewriting a mono channel's limits into the colour camera's 0-255 range.
"""

import numpy as np

from control.core.contrast_manager import ContrastManager

MONO = "Fluorescence 488 nm Ex"  # uint16 camera
COLOR = "BF LED matrix full"  # uint8 camera


def test_defaults_follow_each_channels_own_dtype():
    cm = ContrastManager()
    # The mono camera's frame arrives first, latching acquisition_dtype.
    assert cm.get_limits_for_dtype(MONO, np.uint16) == (0, 65535)
    assert cm.get_limits_for_dtype(COLOR, np.uint8) == (0, 255)
    # ...and the order does not matter.
    other = ContrastManager()
    assert other.get_limits_for_dtype(COLOR, np.uint8) == (0, 255)
    assert other.get_limits_for_dtype(MONO, np.uint16) == (0, 65535)


def test_a_camera_switch_does_not_touch_the_other_cameras_limits():
    cm = ContrastManager()
    cm.update_limits(MONO, 800, 1600, dtype=np.uint16)
    cm.update_limits(COLOR, 10, 200, dtype=np.uint8)

    # Frames now alternate between the two cameras, as a mixed acquisition does.
    for _ in range(3):
        assert cm.get_limits_for_dtype(COLOR, np.uint8) == (10, 200)
        assert cm.get_limits_for_dtype(MONO, np.uint16) == (800, 1600)


def test_the_global_dtype_announcement_no_longer_rewrites_stored_limits():
    """scale_contrast_limits is still called by the live/multichannel init paths on a dtype
    change; it must only record the dtype now."""
    cm = ContrastManager()
    cm.update_limits(MONO, 800, 1600, dtype=np.uint16)

    cm.scale_contrast_limits(np.uint8)  # a colour frame arrived

    assert cm.acquisition_dtype == np.dtype(np.uint8)
    assert cm.get_limits_for_dtype(MONO, np.uint16) == (800, 1600), "the mono channel was rescaled by a colour frame"


def test_a_channels_own_dtype_change_still_converts_its_limits():
    """The legitimate case the rescaling existed for: one camera's pixel format changes, so
    that camera's channels must carry their contrast selection into the new range."""
    cm = ContrastManager()
    cm.update_limits(MONO, 0, 32768, dtype=np.uint16)  # half scale in uint16

    converted = cm.get_limits_for_dtype(MONO, np.uint8)

    assert converted[0] == 0
    assert round(converted[1]) == 128, converted  # half scale in uint8
    # The conversion is recorded, so re-reading is stable rather than compounding.
    assert cm.get_limits_for_dtype(MONO, np.uint8) == converted


def test_limits_recorded_without_a_dtype_adopt_the_first_dtype_they_are_read_at():
    """Callers that predate the dtype argument must not have their values rescaled from a
    guessed source dtype."""
    cm = ContrastManager()
    cm.update_limits(MONO, 800, 1600)  # no dtype

    assert cm.get_limits_for_dtype(MONO, np.uint16) == (800, 1600)
    assert cm.limit_dtypes[MONO] == np.dtype(np.uint16)


def test_get_scaled_limits_converts_without_rewriting_the_record():
    """The mosaic renders a channel at its own depth; that must not re-anchor the channel."""
    cm = ContrastManager()
    cm.update_limits(MONO, 0, 65535, dtype=np.uint16)

    assert cm.get_scaled_limits(MONO, np.uint8) == (0, 255)
    assert cm.contrast_limits[MONO] == (0, 65535), "the stored record was rewritten"
    assert cm.limit_dtypes[MONO] == np.dtype(np.uint16)


def test_unset_channel_falls_back_to_the_requested_dtype():
    cm = ContrastManager()
    assert cm.get_scaled_limits("never seen", np.uint8) == (0, 255)
    assert cm.get_limits_for_dtype("never seen", np.uint16) == (0, 65535)
