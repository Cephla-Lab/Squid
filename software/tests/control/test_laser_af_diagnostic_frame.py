"""The failed-laser-AF diagnostic image must be a frame of the failed call, never an earlier FOV's frame.

LaserAutofocusController keeps the last focus-camera frame in ``image``. A call that fails before it reads a
frame (no reference, the AF laser does not switch on, every read returns None) used to leave the previous call's
frame there, and the worker saved it under the failed FOV's name.
"""

import os
from unittest.mock import MagicMock

import numpy as np
import pytest

import control.core.laser_auto_focus_controller as laf
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.multi_point_worker import MultiPointWorker

PREVIOUS_FOV_FRAME = np.full((8, 8), 7, dtype=np.uint8)


def _controller(*, has_reference=True, piezo=None):
    camera = MagicMock()
    camera.get_exposure_time.return_value = 1.0
    camera.read_frame.return_value = None
    ctrl = LaserAutofocusController(
        microcontroller=MagicMock(),
        camera=camera,
        liveController=MagicMock(),
        stage=MagicMock(),
        piezo=piezo,
        objectiveStore=None,
    )
    if has_reference:
        ctrl.laser_af_properties = ctrl.laser_af_properties.model_copy(
            update={"has_reference": True, "x_reference": 4.0, "pixel_to_um": 1.0, "laser_af_averaging_n": 2}
        )
    ctrl.image = PREVIOUS_FOV_FRAME  # left over from the previous FOV's successful AF
    return ctrl


class _WorkerStub:
    _laser_af_anchor = MultiPointWorker._laser_af_anchor
    _save_laser_af_diagnostic = MultiPointWorker._save_laser_af_diagnostic

    def __init__(self, controller, tmp_path):
        self.laser_auto_focus_controller = controller
        self.region_laser_af_offsets = {}
        self.use_piezo = False
        self._log = MagicMock()
        self.base_path = str(tmp_path)
        self.experiment_ID = "exp"
        self.time_point = 0
        (tmp_path / "exp" / "0").mkdir(parents=True)


def _saved_bmps(tmp_path):
    return sorted(os.listdir(tmp_path / "exp" / "0"))


def test_no_reference_clears_the_previous_frame():
    ctrl = _controller(has_reference=False)
    assert ctrl.move_to_target(0.0) is False
    assert ctrl.image is None


def test_af_laser_timeout_clears_the_previous_frame():
    ctrl = _controller()
    ctrl.microcontroller.wait_till_operation_is_completed.side_effect = TimeoutError("no ack")
    assert ctrl.move_to_target(0.0) is False
    assert ctrl.image is None


@pytest.fixture
def no_spot_display(monkeypatch):
    # The spot-image display emits the last frame read; keep it out of these tests.
    monkeypatch.setattr(laf.control._def, "LASER_AF_DISPLAY_SPOT_IMAGE", False)


def test_every_read_none_clears_the_previous_frame(no_spot_display):
    ctrl = _controller()
    assert ctrl.move_to_target(0.0) is False
    assert ctrl.camera.read_frame.call_count == 2
    assert ctrl.image is None


def test_failure_after_reads_that_all_returned_none_saves_no_diagnostic_image(tmp_path, no_spot_display):
    # The focus camera was read but gave no frame, then an error escapes (here the AF laser off command).
    ctrl = _controller()
    ctrl.microcontroller.turn_off_AF_laser.side_effect = RuntimeError("serial write failed")
    w = _WorkerStub(ctrl, tmp_path)

    assert w._laser_af_anchor("A1", 3, restore_on_failure=False) is False

    assert ctrl.camera.read_frame.call_count == 2
    assert _saved_bmps(tmp_path) == []


def test_failure_before_any_frame_saves_no_diagnostic_image(tmp_path):
    # An error that escapes move_to_target before the focus camera is read (here the AF laser command).
    ctrl = _controller()
    ctrl.microcontroller.turn_on_AF_laser.side_effect = RuntimeError("serial write failed")
    w = _WorkerStub(ctrl, tmp_path)

    assert w._laser_af_anchor("A1", 3, restore_on_failure=False) is False

    assert _saved_bmps(tmp_path) == [], "the previous FOV's frame must not be saved under this FOV's name"
    assert any("no focus-camera frame to save" in str(c) for c in w._log.warning.call_args_list)


def test_failure_after_a_frame_saves_that_frame(tmp_path, monkeypatch):
    # The correction move past the piezo's travel raises after this call's frame was read: that frame is saved.
    this_fov_frame = np.full((8, 8), 200, dtype=np.uint8)
    piezo = MagicMock()
    piezo.move_relative.side_effect = ValueError("past piezo travel")
    ctrl = _controller(piezo=piezo)
    ctrl.camera.read_frame.return_value = this_fov_frame
    monkeypatch.setattr(laf.utils, "find_spot_location", lambda *a, **k: (2.0, 4.0))  # 2 um from the reference
    w = _WorkerStub(ctrl, tmp_path)

    assert w._laser_af_anchor("A1", 3, restore_on_failure=False) is False

    saved = _saved_bmps(tmp_path)
    assert len(saved) == 1 and saved[0].startswith("A1_")
    import imageio.v3 as iio

    assert np.array_equal(iio.imread(tmp_path / "exp" / "0" / saved[0]), this_fov_frame)


@pytest.mark.parametrize("channel_name", [None, "Fluorescence 488 nm Ex"])
def test_diagnostic_without_a_frame_writes_nothing(tmp_path, channel_name):
    ctrl = MagicMock()
    ctrl.image = None
    w = _WorkerStub(ctrl, tmp_path)

    w._save_laser_af_diagnostic("A1", 0, channel_name)

    assert _saved_bmps(tmp_path) == []
