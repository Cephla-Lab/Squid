"""The diagnostic laser AF sweep: every candidate at every z, z restored, laser off."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from control._def import SpotDetectionMode
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.models import LaserAFConfig

START_Z_UM = 1000.0
FRAME_SHAPE = (256, 1024)
STATIC_X_PX = 800


class FakeStage:
    def __init__(self):
        self.z_um = START_Z_UM

    def get_pos(self):
        return SimpleNamespace(z_mm=self.z_um / 1000)

    def move_z(self, delta_mm):
        self.z_um += delta_mm * 1000


class FakeSweepController:
    """run_af_sweep over a fake z axis with one moving reflection (2 px/um) and one static one."""

    run_af_sweep = LaserAutofocusController.run_af_sweep
    get_current_z_um = LaserAutofocusController.get_current_z_um
    _build_search_positions = LaserAutofocusController._build_search_positions
    _spot_detection_params = LaserAutofocusController._spot_detection_params
    _restore_to_position = LaserAutofocusController._restore_to_position
    _move_z = LaserAutofocusController._move_z

    def __init__(self, mode=SpotDetectionMode.DUAL_LEFT):
        self._log = MagicMock()
        self.stage = FakeStage()
        self.piezo = None
        self.objectiveStore = None
        self.camera = MagicMock()
        self.camera.get_region_of_interest.return_value = (0, 0, FRAME_SHAPE[1], FRAME_SHAPE[0])
        self.laser_on = False
        self.frames_taken_with_laser_off = 0
        self.signal_af_sweep_sample = MagicMock()
        self.signal_af_sweep_finished = MagicMock()
        self.laser_af_properties = LaserAFConfig(spot_detection_mode=mode, filter_sigma=None)

    def _turn_on_laser(self):
        self.laser_on = True

    def _turn_off_laser(self):
        self.laser_on = False

    def get_new_frame(self):
        self.frames_taken_with_laser_off += not self.laser_on
        image = np.zeros(FRAME_SHAPE, dtype=np.uint8)
        moving_x = int(round(400 + 2.0 * (self.stage.z_um - START_Z_UM)))
        for x in (moving_x, STATIC_X_PX):
            image[123:134, x - 5 : x + 6] = 200
        return image


def test_sweep_records_every_candidate_in_z_order_and_restores_z():
    controller = FakeSweepController()

    samples = controller.run_af_sweep(range_um=20, step_um=5)

    assert [s.dz_um for s in samples] == [-20, -15, -10, -5, 0, 5, 10, 15, 20]
    for sample in samples:
        assert [round(c["x"]) for c in sample.candidates] == [400 + 2 * sample.dz_um, STATIC_X_PX]
        assert sample.selected_x == pytest.approx(400 + 2 * sample.dz_um)  # leftmost
    assert controller.stage.z_um == pytest.approx(START_Z_UM)
    assert not controller.laser_on
    assert controller.frames_taken_with_laser_off == 0
    assert controller.signal_af_sweep_sample.emit.call_count == len(samples)
    controller.signal_af_sweep_finished.emit.assert_called_once_with(samples)


def test_sweep_keeps_candidates_the_mode_cannot_choose_between():
    controller = FakeSweepController(mode=SpotDetectionMode.SINGLE)

    samples = controller.run_af_sweep(range_um=5, step_um=5)

    assert all(len(s.candidates) == 2 and s.selected_x is None for s in samples)


def test_cancelled_sweep_returns_what_it_has_and_still_restores_z():
    controller = FakeSweepController()
    keep_running = threading.Event()
    keep_running.set()
    # cancel once three positions have been reported
    controller.signal_af_sweep_sample.emit.side_effect = lambda sample: (
        keep_running.clear() if controller.signal_af_sweep_sample.emit.call_count >= 3 else None
    )

    samples = controller.run_af_sweep(range_um=20, step_um=5, keep_running=keep_running)

    assert len(samples) == 3
    assert controller.stage.z_um == pytest.approx(START_Z_UM)
    assert not controller.laser_on


def test_sweep_aborts_when_the_crop_changes_underneath_it():
    controller = FakeSweepController()
    rois = [(0, 0, 1024, 256)] * 3 + [(8, 0, 1024, 256)] * 20
    controller.camera.get_region_of_interest.side_effect = rois

    samples = controller.run_af_sweep(range_um=20, step_um=5)

    assert len(samples) == 2  # the first read latches the ROI; the fourth sees it changed
    assert controller.stage.z_um == pytest.approx(START_Z_UM)
    assert not controller.laser_on
