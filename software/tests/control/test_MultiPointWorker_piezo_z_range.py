"""Set Z-range on a piezo z-stack: the range is piezo positions, the piezo sweeps it, the stepper holds.

A bare MultiPointWorker (no __init__) with a fake stage and a real PiezoStage (fake microcontroller),
so the piezo's travel check is the real one.
"""

import contextlib
from unittest.mock import MagicMock

import pytest

import squid.abc
from control._def import TriggerMode
from control.core.multi_point_worker import MultiPointWorker
from control.piezo import PiezoStage


class _FakeStage:
    def __init__(self, z_mm=1.0):
        self.z_mm = z_mm

    def get_pos(self):
        return squid.abc.Pos(x_mm=10.0, y_mm=20.0, z_mm=self.z_mm, theta_rad=None)

    def move_z(self, dz_mm):
        self.z_mm += dz_mm

    def move_z_to(self, z_mm):
        self.z_mm = z_mm


def _channel(name):
    ch = MagicMock()
    ch.name = name
    ch.z_offset_um = 0.0
    return ch


def _worker(piezo_at_um, piezo_z_range_um, nz=4, z_stacking_config="FROM BOTTOM"):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w.z_stack_order = "CHANNEL FIRST"
    w.NZ = nz
    w.deltaZ = 0.002  # 2 µm
    w.z_stacking_config = z_stacking_config
    w.selected_configurations = [_channel("A")]
    w.use_piezo = True
    w.stage = _FakeStage(z_mm=1.0)
    # The widget holds the stepper where it is: z_range is the stepper's z, not the piezo range.
    w.z_range = (1.0, 1.0)
    w.piezo_z_range_um = piezo_z_range_um
    w.piezo = PiezoStage(MagicMock(), {"OBJECTIVE_PIEZO_RANGE_UM": 300})
    w.piezo.move_to(piezo_at_um)
    w.liveController = MagicMock()
    w.liveController.trigger_mode = TriggerMode.SOFTWARE
    w.do_reflection_af = False
    w.do_autofocus = False
    w.apply_channel_offset = True
    w.Nt = 1
    w.laser_auto_focus_controller = None
    w._current_z_offset_um = 0.0
    w._last_time_point_z_pos = {}
    w.af_fov_count = 0
    w._timepoint_fov_count = 0
    w.total_scans = nz
    w.callbacks = MagicMock()
    w._timing = MagicMock()
    w._timing.get_timer.return_value = contextlib.nullcontext()
    w._log = MagicMock()
    w._sleep = MagicMock()
    w.wait_till_operation_is_completed = MagicMock()
    w.abort_requested_fn = lambda: False
    w.perform_autofocus = MagicMock(return_value=True)
    w.initialize_coordinates_dataframe()

    w.frames = []

    def acquire_camera_image(config, file_ID, current_path, z_level, region_id, fov, config_idx):
        w.frames.append((fov, round(w.piezo.position, 6)))

    w.acquire_camera_image = acquire_camera_image
    return w


@pytest.mark.parametrize(
    "z_stacking_config, expected_planes_um",
    [
        ("FROM BOTTOM", [20.0, 22.0, 24.0, 26.0]),
        ("FROM TOP", [26.0, 24.0, 22.0, 20.0]),
    ],
)
def test_piezo_range_stack_covers_z_min_to_z_max_wherever_the_piezo_was_left(z_stacking_config, expected_planes_um):
    # The piezo is off the range when Start is pressed (e.g. focused elsewhere after Set Z-max).
    w = _worker(piezo_at_um=80.0, piezo_z_range_um=(20.0, 26.0), z_stacking_config=z_stacking_config)

    w.initialize_z_stack()
    for fov in (0, 1):
        w.acquire_at_position("A1", "/tmp", fov)

    assert [z for fov, z in w.frames if fov == 0] == expected_planes_um
    assert [z for fov, z in w.frames if fov == 1] == expected_planes_um
    assert w.stage.z_mm == 1.0  # the stepper holds; only the piezo sweeps


def test_piezo_stack_without_a_range_still_starts_from_the_current_piezo_position():
    w = _worker(piezo_at_um=80.0, piezo_z_range_um=None, nz=3)

    w.initialize_z_stack()
    w.acquire_at_position("A1", "/tmp", 0)

    assert [z for _, z in w.frames] == [80.0, 82.0, 84.0]
    assert w.stage.z_mm == 1.0
