"""Tests for the z-stack acquisition order (channel first vs z first) in MultiPointWorker.acquire_at_position.

A bare MultiPointWorker (no __init__) gets a fake stage that tracks z, so the tests check which
(z level, channel) each frame was taken at, the z it was taken at, and where z ends up.
"""

import contextlib
import os
from unittest.mock import MagicMock

import numpy as np

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

    def move_z_to(self, z_mm):
        self.z_mm = z_mm


class _FakePiezo:
    def __init__(self, position=100.0, range_um=300.0):
        self.position = position
        self.range_um = range_um

    def move_to(self, um):
        if not 0 <= um <= self.range_um:  # like PiezoStage.move_to
            raise ValueError(f"Position {um}um outside valid range 0-{self.range_um}um")
        self.position = um


class _FakeLaserAF:
    """A laser AF controller that moves its target (the fake piezo, or the fake stage) directly, like the real
    one (LaserAutofocusController._move_z), to a reference plane that may drift between calls."""

    def __init__(self, target, references, fail_calls=()):
        self.target = target
        self.references = list(references)
        self.fail_calls = set(fail_calls)  # 1-based move_to_target call indices that fail without moving
        self.positions_at_call = []
        self.image = np.zeros((4, 4), dtype=np.uint8)
        self.characterization_mode = False
        # Like the real controller: it holds the piezo when the scope has one, and _move_z then moves it.
        self.piezo = target if isinstance(target, _FakePiezo) else None

    def _position(self):
        return self.target.position if isinstance(self.target, _FakePiezo) else self.target.z_mm

    def _move_to(self, value):
        if isinstance(self.target, _FakePiezo):
            self.target.move_to(value)
        else:
            self.target.move_z_to(value)

    def move_to_target(self, displacement_um):
        assert displacement_um == 0.0
        self.positions_at_call.append(self._position())
        call = len(self.positions_at_call)
        reference = self.references.pop(0)
        if call in self.fail_calls:
            return False
        self._move_to(reference)
        return True

    def apply_relative_offset_um(self, offset_um):
        if isinstance(self.target, _FakePiezo):
            self.target.move_to(self.target.position + offset_um)
        else:
            self.target.move_z(offset_um / 1000)


def _channel(name, z_offset_um=0.0):
    ch = MagicMock()
    ch.name = name
    ch.z_offset_um = z_offset_um
    return ch


def _worker(
    order,
    nz=3,
    channels=("A", "B"),
    use_piezo=False,
    offsets=None,
    z_stacking_config="FROM BOTTOM",
    laser_af_per_stack=False,
    region_offsets=None,
    laser_af=None,
    tmp_path=None,
):
    w = MultiPointWorker.__new__(MultiPointWorker)
    offsets = offsets or {}
    w.laser_af_per_stack = laser_af_per_stack
    w._laser_af_successes = w._laser_af_failures = 0
    w._laser_af_per_stack_successes = w._laser_af_per_stack_failures = 0
    w.region_laser_af_offsets = region_offsets or {}
    w.experiment_ID, w.time_point = "exp", 0
    w.base_path = str(tmp_path) if tmp_path is not None else "/tmp"
    if tmp_path is not None:
        os.makedirs(os.path.join(w.base_path, "exp", "0"), exist_ok=True)  # the failure branch writes a bmp here
    w.z_stack_order = order
    w.NZ = nz
    w.deltaZ = 0.002
    w.z_stacking_config = z_stacking_config
    w.selected_configurations = [_channel(name, offsets.get(name, 0.0)) for name in channels]
    w.use_piezo = use_piezo
    w._sequenced_bursts = []  # software-sequenced: the worker drives the z loop itself
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
    w._laser_engine = None  # no laser engine: acquire_at_position notes engine use per FOV
    w._laser_channels_needed = []
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
    if laser_af is not None:  # the real per-FOV AF runs against the fake controller
        w.laser_auto_focus_controller = laser_af
        w.do_reflection_af = True
        del w.perform_autofocus
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


class _MicrostepStage(_FakeStage):
    """Rounds every move to whole microsteps, like a real stepper stage."""

    USTEP_MM = 0.0000305

    def move_z(self, dz_mm):
        self.z_mm += round(dz_mm / self.USTEP_MM) * self.USTEP_MM

    def move_z_to(self, z_mm):
        self.z_mm = round(z_mm / self.USTEP_MM) * self.USTEP_MM


def test_z_first_channels_image_the_same_planes_despite_microstep_rounding():
    w = _worker("Z FIRST", channels=("A", "B", "C", "D"))
    w.stage = _MicrostepStage(z_mm=round(1.0 / _MicrostepStage.USTEP_MM) * _MicrostepStage.USTEP_MM)
    w.acquire_at_position("A1", "/tmp", 0)

    planes_by_channel = {}
    for z_level, name, z_um in w.frames:
        planes_by_channel.setdefault(name, []).append(z_um)
    assert len(set(map(tuple, planes_by_channel.values()))) == 1, planes_by_channel


@pytest.mark.parametrize("order", ["CHANNEL FIRST", "Z FIRST"])
def test_from_center_piezo_stack_is_centred_by_the_piezo_and_leaves_the_stage_alone(order):
    """A piezo stack From Center starts half a stack below the piezo's position (not the stage's) and
    the piezo is back at that position afterwards; the stage never moves."""
    w = _worker(order, use_piezo=True, z_stacking_config="FROM CENTER")
    start_stage_z_mm = w.stage.z_mm
    start_piezo_um = w.piezo.position  # 100 um
    w.acquire_at_position("A1", "/tmp", 0)

    expected = [
        (z_level, name, pytest.approx(start_piezo_um - 2 + 2 * z_level)) for name in ("A", "B") for z_level in range(3)
    ]
    if order == "CHANNEL FIRST":
        expected.sort(key=lambda frame: frame[0])
    assert w.frames == expected
    assert w.piezo.position == pytest.approx(start_piezo_um)
    assert w.stage.z_mm == pytest.approx(start_stage_z_mm)


def test_from_center_piezo_stack_is_centred_where_the_per_fov_autofocus_left_the_piezo():
    """Laser AF moves the piezo directly, bypassing the worker's cached piezo position, so the half-stack
    move down must start from the piezo's real position, not from the previous FOV's cached one."""
    w = _worker("CHANNEL FIRST", use_piezo=True, z_stacking_config="FROM CENTER")
    w.z_piezo_um = w.piezo.position  # stale cache from the previous FOV

    def autofocus_moves_the_piezo(region_id, fov):
        w.piezo.move_to(110.0)  # as LaserAutofocusController._move_z does: piezo.move_relative, not the cache
        return True

    w.perform_autofocus = autofocus_moves_the_piezo
    w.acquire_at_position("A1", "/tmp", 0)

    assert [z for _, _, z in w.frames] == pytest.approx([108.0, 108.0, 110.0, 110.0, 112.0, 112.0])
    assert w.piezo.position == pytest.approx(110.0)


@pytest.mark.parametrize("order", ["CHANNEL FIRST", "Z FIRST"])
@pytest.mark.parametrize("abort_after_frames", [1, 2, 4, 5])
def test_abort_mid_stack_returns_a_from_center_piezo_stack_to_the_piezo_start(order, abort_after_frames):
    """An abort part-way through a From Center piezo stack still brings the piezo back to where the stack
    was centred, with the stage untouched."""
    w = _worker(order, use_piezo=True, z_stacking_config="FROM CENTER")
    start_stage_z_mm = w.stage.z_mm
    start_piezo_um = w.piezo.position
    w.abort_requested_fn = lambda: len(w.frames) >= abort_after_frames
    w.acquire_at_position("A1", "/tmp", 0)

    assert len(w.frames) == abort_after_frames
    assert w.piezo.position == pytest.approx(start_piezo_um)
    assert w.stage.z_mm == pytest.approx(start_stage_z_mm)


# --- Z First "AF per stack": laser AF before each later channel's stack ------------------------------------


def _planes(w):
    return [z for _, _, z in w.frames]


def test_z_first_re_anchors_each_later_channel(tmp_path):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0, 108.0])
    w = _worker(
        "Z FIRST", channels=("A", "B", "C"), use_piezo=True, laser_af_per_stack=True, laser_af=af, tmp_path=tmp_path
    )
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([100, 102, 104, 104, 106, 108, 108, 110, 112])
    assert af.positions_at_call == pytest.approx([100.0, 100.0, 104.0])  # per-FOV at 100, re-AFs at each stack start
    assert (w._laser_af_successes, w._laser_af_failures) == (1, 0)
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (2, 0)
    assert w.z_piezo_um == pytest.approx(piezo.position)


@pytest.mark.parametrize("order, option", [("CHANNEL FIRST", True), ("Z FIRST", False)])
def test_no_re_anchor_in_channel_first_or_with_the_option_off(tmp_path, order, option):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0, 108.0])
    w = _worker(
        order, channels=("A", "B", "C"), use_piezo=True, laser_af_per_stack=option, laser_af=af, tmp_path=tmp_path
    )
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    assert len(af.positions_at_call) == 1
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (0, 0)


def test_a_failed_re_anchor_keeps_the_previous_anchor_and_still_applies_the_offset(tmp_path):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0], fail_calls=(2,))
    w = _worker("Z FIRST", use_piezo=True, offsets={"B": -3.0}, laser_af_per_stack=True, laser_af=af, tmp_path=tmp_path)
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([100, 102, 104, 97, 99, 101])
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (0, 1)


def test_a_re_anchor_after_a_failed_per_fov_af_applies_the_offset(tmp_path):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0], fail_calls=(1,))
    w = _worker("Z FIRST", use_piezo=True, offsets={"B": 5.0}, laser_af_per_stack=True, laser_af=af, tmp_path=tmp_path)
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([100, 102, 104, 109, 111, 113])  # A unanchored as today; B anchored at 104 + 5
    assert (w._laser_af_successes, w._laser_af_failures) == (0, 1)
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (1, 0)


def test_three_channel_recovery_chain(tmp_path):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0, 108.0], fail_calls=(1, 3))
    w = _worker(
        "Z FIRST",
        channels=("A", "B", "C"),
        use_piezo=True,
        offsets={"C": -3.0},
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    # C's stack starts from B's verified anchor (104) plus C's offset (-3).
    assert _planes(w) == pytest.approx([100, 102, 104, 104, 106, 108, 101, 103, 105])
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (1, 1)


def test_from_center_re_anchor_runs_at_the_bottom_and_goes_down_half_a_stack_on_success(tmp_path):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0])
    w = _worker(
        "Z FIRST",
        use_piezo=True,
        z_stacking_config="FROM CENTER",
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([98, 100, 102, 102, 104, 106])
    assert af.positions_at_call == pytest.approx([100.0, 98.0])  # the re-AF runs at the stack bottom
    assert piezo.position == pytest.approx(104.0)  # back at B's centre after its stack


def test_from_center_failed_re_anchor_stays_at_the_bottom(tmp_path):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0], fail_calls=(2,))
    w = _worker(
        "Z FIRST",
        use_piezo=True,
        z_stacking_config="FROM CENTER",
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([98, 100, 102, 98, 100, 102])


def test_a_failed_re_anchor_restores_the_pre_af_z_when_the_region_offset_raises(tmp_path):
    # Review Focus 2: move_to_target succeeds, then the region offset runs the piezo past its travel.
    piezo = _FakePiezo(position=280.0, range_um=300.0)
    af = _FakeLaserAF(piezo, references=[280.0, 292.0])
    w = _worker(
        "Z FIRST",
        use_piezo=True,
        offsets={"A": 5.0, "B": 0.0},
        region_offsets={"A1": 10.0},
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo
    w.deltaZ = 0.001  # 1 um steps keep every plane inside the travel

    w.acquire_at_position("A1", "/tmp", 0)

    # per-FOV: 280 + 10 = 290, A offset +5 -> 295..297. B: re-AF reaches 292, +10 = 302 raises -> restored to 295,
    # B offset 0 -> 290..292.
    assert _planes(w) == pytest.approx([295, 296, 297, 290, 291, 292])
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (0, 1)
    assert w.z_piezo_um == pytest.approx(piezo.position)


def test_a_failed_restore_stops_the_acquisition(tmp_path):
    class _PiezoDiesOnRestore(_FakePiezo):
        dead = False

        def move_to(self, um):
            if self.dead:
                raise RuntimeError("piezo gone")
            try:
                super().move_to(um)
            except ValueError:
                self.dead = True  # the next move (the restore) fails too
                raise

    piezo = _PiezoDiesOnRestore(position=280.0, range_um=300.0)
    af = _FakeLaserAF(piezo, references=[280.0, 292.0])
    w = _worker(
        "Z FIRST",
        use_piezo=True,
        offsets={"A": 5.0, "B": 0.0},
        region_offsets={"A1": 10.0},
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo
    w.deltaZ = 0.001

    with pytest.raises(RuntimeError, match="piezo gone"):
        w.acquire_at_position("A1", "/tmp", 0)


def test_abort_before_a_re_anchor_skips_it(tmp_path):
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0])
    w = _worker("Z FIRST", use_piezo=True, laser_af_per_stack=True, laser_af=af, tmp_path=tmp_path)
    w.piezo = piezo
    w.abort_requested_fn = lambda: len(w.frames) >= 3

    w.acquire_at_position("A1", "/tmp", 0)

    assert len(af.positions_at_call) == 1


def test_stage_stacks_re_anchor_with_the_stage(tmp_path):
    w = _worker("Z FIRST", use_piezo=False, laser_af_per_stack=True, tmp_path=tmp_path)
    af = _FakeLaserAF(w.stage, references=[1.0, 1.004])  # mm
    w.laser_auto_focus_controller = af
    w.do_reflection_af = True
    del w.perform_autofocus

    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([1000, 1002, 1004, 1004, 1006, 1008])


def _sleep_recorder(w):
    sleeps = []
    w._sleep = sleeps.append
    return sleeps


@pytest.mark.parametrize("fail_calls", [(), (2,)])
def test_a_re_anchored_stack_settles_before_its_first_frame(tmp_path, fail_calls):
    """Review finding: the per-FOV path always settles after the AF (prepare_z_stack); the re-anchored stack must
    too, on success and after a restore."""
    import squid.stage.utils

    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0], fail_calls=fail_calls)
    w = _worker("Z FIRST", use_piezo=True, laser_af_per_stack=True, laser_af=af, tmp_path=tmp_path)
    w.piezo = piezo
    sleeps = _sleep_recorder(w)
    sleeps_at_af_call = []
    real_move_to_target = af.move_to_target
    af.move_to_target = lambda um: (sleeps_at_af_call.append(len(sleeps)), real_move_to_target(um))[1]
    sleeps_at_frame = []
    real_acquire = w.acquire_camera_image
    w.acquire_camera_image = lambda *a, **k: (sleeps_at_frame.append(len(sleeps)), real_acquire(*a, **k))[1]

    w.acquire_at_position("A1", "/tmp", 0)

    between = sleeps[sleeps_at_af_call[1] : sleeps_at_frame[3]]  # after the re-AF, before B's first frame
    assert between, "no settle between the re-AF and the first frame of the re-anchored stack"
    if not fail_calls:
        assert squid.stage.utils.z_stabilization_time_s() in between  # prepare_z_stack's settle, as per FOV


def test_a_failed_re_anchor_restores_the_piezo_the_laser_af_moved_even_for_a_stage_stack(tmp_path):
    """Review finding: on a stage + piezo system with Piezo Z-Stack unchecked the laser AF still moves the piezo,
    so the restore must move the piezo back, not the stage."""
    piezo = _FakePiezo(position=280.0, range_um=300.0)
    af = _FakeLaserAF(piezo, references=[280.0, 292.0])
    w = _worker(
        "Z FIRST",
        use_piezo=False,
        offsets={"A": 5.0, "B": 0.0},
        region_offsets={"A1": 10.0},
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo  # present on the scope, not running the stack

    w.acquire_at_position("A1", "/tmp", 0)

    # per-FOV: piezo 280 + 10 = 290; A offset +5 on the stage -> 1005..1009; B: re-AF reaches 292, +10 raises ->
    # the piezo (not the stage) is restored to 290; B offset 0 -> stage back to 1000..1004.
    assert _planes(w) == pytest.approx([1005, 1007, 1009, 1000, 1002, 1004])
    assert piezo.position == pytest.approx(290.0)
    assert w.stage.z_mm == pytest.approx(1.0)


def test_a_failed_diagnostic_save_does_not_skip_the_restore(tmp_path, monkeypatch):
    """Review finding: the focus-camera bitmap write sat inside the except block before the restore; an error from
    it left the piezo at the AF plane with a stale cache. The save is best effort, the restore unconditional."""
    import control.core.multi_point_worker as worker_module

    def imwrite_fails(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(worker_module.iio, "imwrite", imwrite_fails)
    piezo = _FakePiezo(position=280.0, range_um=300.0)
    af = _FakeLaserAF(piezo, references=[280.0, 292.0])
    w = _worker(
        "Z FIRST",
        use_piezo=True,
        offsets={"A": 5.0, "B": 0.0},
        region_offsets={"A1": 10.0},
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo
    w.deltaZ = 0.001

    w.acquire_at_position("A1", "/tmp", 0)  # the failed save is logged, not raised

    assert _planes(w) == pytest.approx([295, 296, 297, 290, 291, 292])
    assert w.z_piezo_um == pytest.approx(piezo.position)
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (0, 1)


def test_a_restore_settles_in_hardware_trigger_mode_too(tmp_path):
    """Review finding: the restore waited only in software-trigger mode; in hardware-trigger mode the next
    exposure could start before the piezo settled. The restore settles like prepare_z_stack, in both modes."""
    import squid.stage.utils

    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0], fail_calls=(2,))
    w = _worker("Z FIRST", use_piezo=True, laser_af_per_stack=True, laser_af=af, tmp_path=tmp_path)
    w.piezo = piezo
    w.liveController.trigger_mode = TriggerMode.HARDWARE
    sleeps = _sleep_recorder(w)
    sleeps_at_af_call = []
    real_move_to_target = af.move_to_target
    af.move_to_target = lambda um: (sleeps_at_af_call.append(len(sleeps)), real_move_to_target(um))[1]
    sleeps_at_frame = []
    real_acquire = w.acquire_camera_image
    w.acquire_camera_image = lambda *a, **k: (sleeps_at_frame.append(len(sleeps)), real_acquire(*a, **k))[1]

    w.acquire_at_position("A1", "/tmp", 0)

    between = sleeps[sleeps_at_af_call[1] : sleeps_at_frame[3]]
    assert squid.stage.utils.z_stabilization_time_s() in between


def test_a_successful_re_anchor_lands_at_reference_plus_region_and_channel_offsets(tmp_path):
    """Review minor: the success path applies the per-region laser-AF offset, then the channel offset."""
    piezo = _FakePiezo()
    af = _FakeLaserAF(piezo, references=[100.0, 104.0])
    w = _worker(
        "Z FIRST",
        use_piezo=True,
        offsets={"A": 0.0, "B": 3.0},
        region_offsets={"A1": 10.0},
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo

    w.acquire_at_position("A1", "/tmp", 0)

    # A: 100 + 10 (region); B: re-AF to 104 + 10 (region) + 3 (channel) = 117
    assert _planes(w) == pytest.approx([110, 112, 114, 117, 119, 121])


def test_failed_af_diagnostics_are_named_per_fov_and_channel(tmp_path):
    """Hongquan: one bitmap per failure, named by region, FOV and (for a re-AF) channel, so they do not overwrite."""
    piezo = _FakePiezo(position=280.0, range_um=300.0)
    af = _FakeLaserAF(piezo, references=[292.0, 292.0], fail_calls=())  # both AFs raise in the region offset
    w = _worker(
        "Z FIRST",
        use_piezo=True,
        offsets={"A": 0.0, "B": 0.0},
        region_offsets={"A1": 10.0},
        laser_af_per_stack=True,
        laser_af=af,
        tmp_path=tmp_path,
    )
    w.piezo = piezo
    w.deltaZ = 0.001

    w.acquire_at_position("A1", "/tmp", 7)

    names = sorted(p.name for p in (tmp_path / "exp" / "0").glob("*.bmp"))
    assert names == ["A1_7_B_focus_camera.bmp", "A1_7_focus_camera.bmp"]
