"""Laser AF's alignment check must fail when the verification frame shows no spot.

A blank (all zeros) or uniform focus-camera frame has no defined correlation with the reference: the crop's
normalisation and np.corrcoef give NaN, and NaN is never below the threshold. The check must also fail when spot
detection finds nothing in the frame.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from control._def import SpotDetectionMode
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.models import LaserAFConfig

FRAME_SHAPE = (256, 1024)
SPOT_X_PX = 512.0
SPOT_Y_PX = 128


def spot_frame(x_px=SPOT_X_PX, shape=FRAME_SHAPE, y_px=SPOT_Y_PX):
    """One Gaussian spot, like the AF laser's reflection on the focus camera."""
    x = np.arange(shape[1])
    gx = np.exp(-((x - x_px) ** 2) / (2 * 5.0**2))
    gy = np.exp(-((np.arange(shape[0]) - y_px) ** 2) / (2 * 5.0**2))
    return np.round(255 * np.outer(gy, gx)).astype(np.uint8)


def make_controller(frames):
    """A controller with a reference set from a spot frame; get_new_frame then returns `frames` in turn."""
    live = SimpleNamespace(microscope=SimpleNamespace(config_repo=SimpleNamespace(current_profile=None)))
    ctrl = LaserAutofocusController(
        microcontroller=MagicMock(),
        camera=MagicMock(),
        liveController=live,
        stage=MagicMock(),
        piezo=None,
        objectiveStore=None,
    )
    ctrl.laser_af_properties = LaserAFConfig(
        pixel_to_um=0.2,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
    )
    ctrl.is_initialized = True
    ctrl.get_new_frame = lambda: spot_frame()
    assert ctrl.set_reference()

    queue = list(frames)
    ctrl.get_new_frame = lambda: queue.pop(0)
    return ctrl


def test_spot_at_the_reference_passes():
    ctrl = make_controller([spot_frame()])

    ok, correlation = ctrl._verify_spot_alignment()

    assert ok
    assert correlation == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize(
    "frame",
    [np.zeros(FRAME_SHAPE, np.uint8), np.full(FRAME_SHAPE, 7, np.uint8)],
    ids=["all-zeros", "constant"],
)
def test_blank_frame_fails(frame):
    ctrl = make_controller([frame])

    ok, _ = ctrl._verify_spot_alignment()

    assert not ok


def test_poisson_noise_frame_fails():
    rng = np.random.default_rng(0)
    ctrl = make_controller([rng.poisson(5, FRAME_SHAPE).astype(np.uint8)])

    ok, _ = ctrl._verify_spot_alignment()

    assert not ok


def test_no_detected_spot_fails_even_if_the_frame_correlates():
    """Spot detection's verdict counts: a frame it rejects is not a verified alignment."""
    ctrl = make_controller([])

    def no_detection(*args, **kwargs):
        ctrl.image = spot_frame()
        return None

    ctrl._get_laser_spot_centroid = no_detection

    ok, _ = ctrl._verify_spot_alignment()

    assert not ok


def test_undefined_correlation_fails_even_with_a_detected_spot():
    """A uniform crop gives a NaN correlation; NaN must count as a failure, not as above the threshold."""
    ctrl = make_controller([])

    def detection_on_a_uniform_frame(*args, **kwargs):
        ctrl.image = np.full(FRAME_SHAPE, 7, np.uint8)
        return (SPOT_X_PX, float(SPOT_Y_PX))

    ctrl._get_laser_spot_centroid = detection_on_a_uniform_frame

    ok, correlation = ctrl._verify_spot_alignment()

    assert not ok
    assert np.isnan(correlation)


def test_move_to_target_fails_and_moves_back_on_a_blank_verification_frame():
    # displacement read sees the spot 5 px (1 um) right of the reference, the verification frame is blank
    ctrl = make_controller([spot_frame(SPOT_X_PX + 5), np.zeros(FRAME_SHAPE, np.uint8)])

    assert not ctrl.move_to_target(0.0)

    moves_mm = [c.args[0] for c in ctrl.stage.move_z.call_args_list]
    assert len(moves_mm) == 2
    assert moves_mm[0] == pytest.approx(-moves_mm[1])
