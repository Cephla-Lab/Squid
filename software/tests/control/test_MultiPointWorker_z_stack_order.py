"""Tests for the z-stack acquisition order (channel first vs z first) in MultiPointWorker.acquire_at_position.

A bare MultiPointWorker (no __init__) gets a fake stage that tracks z, so the tests check which
(z level, channel) each frame was taken at, the z it was taken at, and where z ends up.
"""

import contextlib
from unittest.mock import MagicMock

import pytest

import squid.abc
from control._def import TriggerMode
from control.core.multi_point_worker import MultiPointWorker


class _FakeStage:
    def __init__(self, z_mm=1.0):
        self.z_mm = z_mm

    def get_pos(self):
        return squid.abc.Pos(x_mm=10.0, y_mm=20.0, z_mm=self.z_mm, theta_rad=None)

    def move_z(self, dz_mm):
        self.z_mm += dz_mm


class _FakePiezo:
    range_um = 300.0

    def __init__(self, position=100.0):
        self.position = position

    def move_to(self, um):
        self.position = um


def _channel(name, z_offset_um=0.0):
    ch = MagicMock()
    ch.name = name
    ch.z_offset_um = z_offset_um
    return ch


def _worker(order, nz=3, channels=("A", "B"), use_piezo=False, offsets=None, z_stacking_config="FROM BOTTOM"):
    w = MultiPointWorker.__new__(MultiPointWorker)
    offsets = offsets or {}
    w.z_stack_order = order
    w.NZ = nz
    w.deltaZ = 0.002
    w.z_stacking_config = z_stacking_config
    w.selected_configurations = [_channel(name, offsets.get(name, 0.0)) for name in channels]
    w.use_piezo = use_piezo
    w.stage = _FakeStage()
    w.piezo = _FakePiezo() if use_piezo else None
    # Like the real worker, z_piezo_um only exists once a piezo stack sets it.
    w.liveController = MagicMock()
    w.liveController.trigger_mode = TriggerMode.SOFTWARE
    w.do_reflection_af = bool(offsets)
    w.do_autofocus = False
    w.apply_channel_offset = True
    w.Nt = 1
    w.laser_auto_focus_controller = None
    w._current_z_offset_um = 0.0
    w._last_time_point_z_pos = {}
    w.af_fov_count = 0
    w._timepoint_fov_count = 0
    w.total_scans = nz * len(channels)
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
        z = w.z_piezo_um if use_piezo else w.stage.z_mm * 1000
        w.frames.append((z_level, config.name, round(z, 6)))

    w.acquire_camera_image = acquire_camera_image
    return w


def test_channel_first_acquires_all_channels_at_each_z():
    w = _worker("CHANNEL FIRST")
    w.acquire_at_position("A1", "/tmp", 0)
    assert [(z, c) for z, c, _ in w.frames] == [(0, "A"), (0, "B"), (1, "A"), (1, "B"), (2, "A"), (2, "B")]


def test_z_first_acquires_whole_stack_per_channel():
    w = _worker("Z FIRST")
    w.acquire_at_position("A1", "/tmp", 0)
    assert [(z, c) for z, c, _ in w.frames] == [(0, "A"), (1, "A"), (2, "A"), (0, "B"), (1, "B"), (2, "B")]


@pytest.mark.parametrize("use_piezo", [False, True])
@pytest.mark.parametrize("z_stacking_config", ["FROM BOTTOM", "FROM CENTER"])
def test_z_first_images_each_plane_at_the_same_z_as_channel_first(use_piezo, z_stacking_config):
    channel_first = _worker("CHANNEL FIRST", use_piezo=use_piezo, z_stacking_config=z_stacking_config)
    z_first = _worker("Z FIRST", use_piezo=use_piezo, z_stacking_config=z_stacking_config)
    for w in (channel_first, z_first):
        w.acquire_at_position("A1", "/tmp", 0)

    assert sorted(z_first.frames) == sorted(channel_first.frames)
    # Both orders leave z where the channel-first order always has.
    assert z_first.stage.z_mm == pytest.approx(channel_first.stage.z_mm)
    if use_piezo:
        assert z_first.piezo.position == pytest.approx(channel_first.piezo.position)


def test_z_first_records_one_coordinates_row_per_z_level_without_channel_offset():
    w = _worker("Z FIRST", offsets={"A": 5.0, "B": -3.0})
    start_z_um = w.stage.z_mm * 1000
    w.acquire_at_position("A1", "/tmp", 0)

    assert list(w.coordinates_pd["z_level"]) == [0, 1, 2]
    assert list(w.coordinates_pd["z (um)"]) == pytest.approx([start_z_um, start_z_um + 2, start_z_um + 4])
    assert w.af_fov_count == 3


def test_z_first_applies_channel_offset_across_the_stack_and_resets_it():
    w = _worker("Z FIRST", offsets={"A": 5.0, "B": -3.0})
    start_z_um = w.stage.z_mm * 1000
    w.acquire_at_position("A1", "/tmp", 0)

    expected = [
        (z_level, name, pytest.approx(start_z_um + 2 * z_level + offset))
        for name, offset in (("A", 5.0), ("B", -3.0))
        for z_level in range(3)
    ]
    assert w.frames == expected
    assert w._current_z_offset_um == 0.0
    assert w.stage.z_mm * 1000 == pytest.approx(start_z_um)


def test_z_first_progress_counts_up_by_one_per_image():
    w = _worker("Z FIRST")
    w.acquire_at_position("A1", "/tmp", 0)
    progress = [c.args[0].current_fov for c in w.callbacks.signal_region_progress.call_args_list]
    assert progress == [1, 2, 3, 4, 5, 6]


def test_z_first_with_single_plane_falls_back_to_channel_loop():
    w = _worker("Z FIRST", nz=1)
    w.acquire_at_position("A1", "/tmp", 0)
    assert [(z, c) for z, c, _ in w.frames] == [(0, "A"), (0, "B")]


@pytest.mark.parametrize("order", ["CHANNEL FIRST", "Z FIRST"])
@pytest.mark.parametrize("abort_after_frames", [1, 2, 4, 5])
def test_abort_mid_stack_stops_and_returns_z_to_the_stack_start(order, abort_after_frames):
    w = _worker(order)
    start_z_mm = w.stage.z_mm
    w.abort_requested_fn = lambda: len(w.frames) >= abort_after_frames
    w.acquire_at_position("A1", "/tmp", 0)

    assert len(w.frames) == abort_after_frames
    assert w.stage.z_mm == pytest.approx(start_z_mm)
