"""Every laser AF operation that turns the AF laser on must send laser-off on every way out.

That includes a timed-out acknowledgement of the laser-on command (the microcontroller may still have executed
it) and an exception raised while the laser is on. A failure to turn the laser off must not replace the
original error.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from control._def import SpotDetectionMode
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.models import LaserAFConfig


class FakeMicrocontroller:
    """Records the AF laser commands; the laser-on ack can time out, and laser-off can fail."""

    def __init__(self, on_ack_times_out=False, off_raises=None):
        self.commands = []
        self.on_ack_times_out = on_ack_times_out
        self.off_raises = off_raises

    def turn_on_AF_laser(self):
        self.commands.append("on")

    def turn_off_AF_laser(self):
        self.commands.append("off")
        if self.off_raises is not None:
            raise self.off_raises

    def wait_till_operation_is_completed(self, timeout_limit_s=5):
        if self.commands[-1] == "on" and self.on_ack_times_out:
            raise TimeoutError("laser-on acknowledgement timed out")

    def laser_left_off(self):
        return bool(self.commands) and self.commands[-1] == "off"


def spot_frame(shape=(256, 1024), x_px=512.0, y_px=128):
    x = np.arange(shape[1])
    gx = np.exp(-((x - x_px) ** 2) / (2 * 5.0**2))
    gy = np.exp(-((np.arange(shape[0]) - y_px) ** 2) / (2 * 5.0**2))
    return np.round(255 * np.outer(gy, gx)).astype(np.uint8)


def make_controller(mcu):
    live = SimpleNamespace(microscope=SimpleNamespace(config_repo=SimpleNamespace(current_profile=None)))
    camera = MagicMock()
    camera.read_frame.return_value = spot_frame()
    ctrl = LaserAutofocusController(
        microcontroller=mcu,
        camera=camera,
        liveController=live,
        stage=MagicMock(),
        piezo=None,
        objectiveStore=None,
    )
    ctrl.laser_af_properties = LaserAFConfig(
        x_reference=512.0,
        pixel_to_um=0.2,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
    )
    half = ctrl.laser_af_properties.spot_crop_size // 2
    crop = spot_frame()[128 - half : 128 + half, 512 - half : 512 + half].astype(np.float32)
    ctrl.reference_crop = (crop - np.mean(crop)) / np.max(crop)
    ctrl.is_initialized = True
    ctrl.get_new_frame = lambda: spot_frame()
    return ctrl


SITES = {
    "measure_displacement": lambda c: c.measure_displacement(),
    "set_reference": lambda c: c.set_reference(),
    "verify_spot_alignment": lambda c: c._verify_spot_alignment(),
    "calibrate_pixel_to_um": lambda c: c._calibrate_pixel_to_um(),
    "initialize_auto": lambda c: c.initialize_auto(),
    "get_image": lambda c: c.get_image(),
}


def _is_nan(value):
    return isinstance(value, float) and np.isnan(value)


# What each operation returns when the laser-on ack times out (unchanged by the fix).
ON_TIMEOUT_RESULT = {
    "measure_displacement": _is_nan,
    "set_reference": lambda r: r is False,
    "verify_spot_alignment": lambda r: r[0] is False and _is_nan(r[1]),
    "calibrate_pixel_to_um": lambda r: r is False,
    "get_image": lambda r: r is None,
}


@pytest.mark.parametrize("site", sorted(SITES))
def test_success_turns_the_laser_off(site):
    mcu = FakeMicrocontroller()
    SITES[site](make_controller(mcu))

    assert mcu.laser_left_off()
    assert mcu.commands.count("on") == mcu.commands.count("off")


@pytest.mark.parametrize("site", sorted(ON_TIMEOUT_RESULT))
def test_laser_on_ack_timeout_sends_laser_off(site):
    mcu = FakeMicrocontroller(on_ack_times_out=True)

    result = SITES[site](make_controller(mcu))

    assert ON_TIMEOUT_RESULT[site](result)
    assert mcu.commands == ["on", "off"]


def test_initialize_auto_laser_on_ack_timeout_sends_laser_off_and_still_raises():
    mcu = FakeMicrocontroller(on_ack_times_out=True)

    with pytest.raises(TimeoutError):
        make_controller(mcu).initialize_auto()

    assert mcu.commands == ["on", "off"]


def test_laser_on_ack_timeout_result_survives_a_failing_laser_off():
    mcu = FakeMicrocontroller(on_ack_times_out=True, off_raises=OSError("serial link lost"))

    assert np.isnan(make_controller(mcu).measure_displacement())
    assert mcu.commands == ["on", "off"]


@pytest.mark.parametrize("site", sorted(set(SITES) - {"get_image"}))
def test_exception_while_the_laser_is_on_sends_laser_off_and_propagates(site):
    mcu = FakeMicrocontroller()
    ctrl = make_controller(mcu)
    ctrl.camera.enable_callbacks.side_effect = RuntimeError("camera fault")

    with pytest.raises(RuntimeError, match="camera fault"):
        SITES[site](ctrl)

    assert mcu.laser_left_off()


def test_get_image_camera_error_sends_laser_off():
    mcu = FakeMicrocontroller()
    ctrl = make_controller(mcu)
    ctrl.camera.send_trigger.side_effect = RuntimeError("camera fault")

    assert ctrl.get_image() is None
    assert mcu.commands == ["on", "off"]


def test_calibration_stage_error_sends_laser_off_and_propagates():
    mcu = FakeMicrocontroller()
    ctrl = make_controller(mcu)
    ctrl.stage.move_z.side_effect = RuntimeError("stage fault")

    with pytest.raises(RuntimeError, match="stage fault"):
        ctrl._calibrate_pixel_to_um()

    assert mcu.commands == ["on", "off"]


def test_a_failing_laser_off_does_not_mask_the_original_error():
    mcu = FakeMicrocontroller(off_raises=OSError("serial link lost"))
    ctrl = make_controller(mcu)
    ctrl.camera.enable_callbacks.side_effect = RuntimeError("camera fault")

    with pytest.raises(RuntimeError, match="camera fault"):
        ctrl.measure_displacement()

    assert mcu.commands == ["on", "off"]
