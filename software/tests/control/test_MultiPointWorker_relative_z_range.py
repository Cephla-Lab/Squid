"""The worker's relative z-stack (Set Range with laser AF on): per FOV, lock on to the laser AF reference, image
every plane of the range relative to that anchor (Channel First), never clamp a frame, and return to the anchor.

A bare MultiPointWorker (no __init__) with a fake stage, a fake piezo and a fake laser AF controller that moves
its actuator directly, like LaserAutofocusController._move_z. acquire_camera_image is replaced to record frames.
"""

import contextlib
import json
import os
import threading
import time
from unittest.mock import MagicMock, call

import numpy as np
import pytest

import squid.abc
import squid.stage.utils
from control._def import TriggerMode
from control.core.job_processing import (
    CaptureInfo,
    SaveImageJob,
    SaveOMETiffJob,
    SaveSettings,
    SaveZarrJob,
    ZarrWriterInfo,
)
from control.core.laser_auto_focus_controller import LockOnRecord
from control.core.multi_point_worker import MultiPointWorker, SummarizeResult
from control.core.relative_z_range import nz_for_range
from squid.abc import CameraFrame, CameraFrameFormat
from squid.config import CameraPixelFormat

FIRST_FOV_MESSAGE = (
    "Couldn't lock onto the laser AF reference at the first FOV: move near the reference plane, "
    "or measure a relative z once."
)


class _FakeStage:
    def __init__(self, z_mm=1.0, x_mm=10.0, y_mm=20.0):
        self.x_mm, self.y_mm, self.z_mm = x_mm, y_mm, z_mm
        self.moves = []  # ("z", relative mm) or ("z_to", absolute mm)

    def get_pos(self):
        return squid.abc.Pos(x_mm=self.x_mm, y_mm=self.y_mm, z_mm=self.z_mm, theta_rad=None)

    def move_x_to(self, x_mm):
        self.x_mm = x_mm

    def move_y_to(self, y_mm):
        self.y_mm = y_mm

    def move_z(self, dz_mm):
        self.moves.append(("z", dz_mm))
        self.z_mm += dz_mm

    def move_z_to(self, z_mm):
        self.moves.append(("z_to", z_mm))
        self.z_mm = z_mm


class _FakePiezo:
    def __init__(self, position=100.0, range_um=300.0):
        self.position = position
        self.range_um = range_um
        self.moves = []  # every commanded target, valid or not

    def move_to(self, um):
        self.moves.append(um)
        if not 0 <= um <= self.range_um:  # like PiezoStage.move_to
            raise ValueError(f"Position {um}um outside valid range 0-{self.range_um}um")
        self.position = um


class _FakeLaserAF:
    """Moves its actuator (the fake piezo, or the fake stage) to a reference plane (µm) that may drift between
    calls. fail_calls fail without moving; raise_calls move 5 µm toward the reference and then raise, like an
    exception in _verify_spot_alignment after _move_z (the 100 -> 95 µm case). Holds a last lock-on record."""

    def __init__(self, target, references, fail_calls=(), raise_calls=(), record_anchor_um=None, stage=None):
        self.target = target
        self.references = list(references)
        self.fail_calls = set(fail_calls)
        self.raise_calls = set(raise_calls)
        self.positions_at_call = []
        self.stage_at_call = []
        self.stage = stage
        self.image = np.zeros((4, 4), dtype=np.uint8)
        self.characterization_mode = False
        self.piezo = target if isinstance(target, _FakePiezo) else None
        self.records = []
        if record_anchor_um is not None:
            self.records.append(self._record(record_anchor_um))

    def _position_um(self):
        return self.target.position if isinstance(self.target, _FakePiezo) else self.target.z_mm * 1000

    def _move_to_um(self, um):
        if isinstance(self.target, _FakePiezo):
            self.target.move_to(um)
        else:
            self.target.move_z_to(um / 1000)

    def move_to_target(self, displacement_um):
        assert displacement_um == 0.0
        self.positions_at_call.append(self._position_um())
        if self.stage is not None:
            pos = self.stage.get_pos()
            self.stage_at_call.append((pos.x_mm, pos.y_mm, pos.z_mm))
        call = len(self.positions_at_call)
        reference = self.references.pop(0)
        if call in self.fail_calls:
            return False
        if call in self.raise_calls:
            here = self._position_um()
            self._move_to_um(here + (5.0 if reference > here else -5.0))
            raise RuntimeError("spot centroid read failed during verification")
        self._move_to_um(reference)
        return True

    def apply_relative_offset_um(self, offset_um):
        raise AssertionError("per-region offsets are refused in relative mode")

    def _record(self, anchor_um):
        return LockOnRecord(x_mm=0.0, y_mm=0.0, stage_z_mm=None, anchor_um=anchor_um, identity=("ref",))

    def record_lock_on(self):
        self.records.append(self._record(self._position_um()))

    def valid_lock_on(self):
        return self.records[-1] if self.records else None


def _channel(name, z_offset_um=0.0):
    ch = MagicMock()
    ch.name = name
    ch.z_offset_um = z_offset_um
    return ch


def _worker(
    tmp_path,
    relative_range=(-2.0, 4.0),
    channels=("A", "B"),
    offsets=None,
    piezo_scope=True,
    references=(100.0,),
    fail_calls=(),
    raise_calls=(),
    record_anchor_um=None,
    order="CHANNEL FIRST",
    piezo=None,
    stage=None,
    nt=1,
):
    w = MultiPointWorker.__new__(MultiPointWorker)
    offsets = offsets or {}
    w.laser_af_relative_range_um = relative_range
    w._relative_mode = True
    w._anchor_um = None
    w._require_first_lock_on = False
    w.z_stack_order = order
    w.deltaZ = 0.002  # 2 µm
    w.NZ = nz_for_range(*relative_range, 2.0)
    w.z_stacking_config = "FROM BOTTOM"
    w.selected_configurations = [_channel(name, offsets.get(name, 0.0)) for name in channels]
    w.stage = stage or _FakeStage()
    w.piezo = (piezo or _FakePiezo()) if piezo_scope else None
    w.use_piezo = piezo_scope
    w.z_range = (w.stage.z_mm, w.stage.z_mm)
    w.piezo_z_range_um = None
    w.laser_auto_focus_controller = _FakeLaserAF(
        w.piezo if piezo_scope else w.stage,
        references,
        fail_calls=fail_calls,
        raise_calls=raise_calls,
        record_anchor_um=record_anchor_um,
        stage=w.stage,
    )
    w.do_reflection_af = True
    w.do_autofocus = False
    w.apply_channel_offset = True
    w.region_laser_af_offsets = {}
    w.laser_af_per_stack = False
    w._laser_af_successes = w._laser_af_failures = 0
    w._laser_af_per_stack_successes = w._laser_af_per_stack_failures = 0
    w.experiment_ID, w.time_point = "exp", 0
    w.base_path = str(tmp_path)
    os.makedirs(os.path.join(w.base_path, "exp", "0"), exist_ok=True)  # the failed-AF bitmap goes here
    w.Nt = nt
    w.liveController = MagicMock()
    w.liveController.trigger_mode = TriggerMode.SOFTWARE
    w._laser_engine = None
    w._laser_channels_needed = []
    w._current_z_offset_um = 0.0
    w._last_time_point_z_pos = {}
    w.af_fov_count = 0
    w._timepoint_fov_count = 0
    w.total_scans = w.NZ * len(channels)
    w.callbacks = MagicMock()
    w._timing = MagicMock()
    w._timing.get_timer.return_value = contextlib.nullcontext()
    w._log = MagicMock()
    w._sleep = MagicMock()
    w.wait_till_operation_is_completed = MagicMock()
    w._abort_cause = None
    w._aborted = []
    w.request_abort_fn = lambda: w._aborted.append(True)
    w.abort_requested_fn = lambda: bool(w._aborted)
    w._alignment_widget = None
    w.microcontroller = MagicMock()
    w._wait_for_outstanding_callback_images = MagicMock()
    w._backpressure = MagicMock()
    w._summarize_runner_outputs = MagicMock(return_value=SummarizeResult(none_failed=True, had_results=False))
    w._abort_on_failed_job = True
    w._acquisition_error_count = 0
    w.experiment_path = os.path.join(w.base_path, "exp")
    # Skip notices: no save runner unless a test adds one (no runner, no notice); no frame seen yet.
    w._job_runners = []
    w._save_settings = SaveSettings()
    w._skip_notice_lock = threading.Lock()
    w._pending_skip_notices = []
    w._frame_format = None
    w._relative_frames_skipped = w._relative_fovs_with_skips = 0
    w.initialize_coordinates_dataframe()

    w.frames = []

    def acquire_camera_image(config, file_ID, current_path, z_level, region_id, fov, config_idx):
        if piezo_scope:
            assert w.z_piezo_um == pytest.approx(w.piezo.position)  # CaptureInfo records the cache
            z = w.piezo.position
        else:
            z = w.stage.z_mm * 1000
        w.frames.append((fov, z_level, config.name, round(z, 6)))

    w.acquire_camera_image = acquire_camera_image
    return w


def _planes(w, fov=None):
    return [(k, name, z) for f, k, name, z in w.frames if fov is None or f == fov]


def _frames(planes):
    """Expected (k, channel, z) frames, z rounded as the recorder rounds it."""
    return [(k, name, round(z, 6)) for k, name, z in planes]


def _scan(w, coords, region_id="A1"):
    w.scan_region_fov_coords_mm = {region_id: list(coords)}
    w.scan_region_coords_mm = {region_id: coords[0]}


def _logged(w):
    """Every message the worker logged, at any level."""
    calls = w._log.info.call_args_list + w._log.warning.call_args_list + w._log.error.call_args_list
    return [str(c.args[0]) for c in calls if c.args]


def test_channel_first_images_every_plane_relative_to_each_fovs_anchor(tmp_path):
    w = _worker(tmp_path, references=[100.0, 108.0], piezo=_FakePiezo(position=90.0))

    w.acquire_at_position("A1", str(tmp_path), 0)
    w.acquire_at_position("A1", str(tmp_path), 1)

    expected_0 = [(k, c, 98.0 + 2 * k) for k in range(4) for c in ("A", "B")]
    expected_1 = [(k, c, 106.0 + 2 * k) for k in range(4) for c in ("A", "B")]
    assert _planes(w, 0) == _frames(expected_0)
    assert _planes(w, 1) == _frames(expected_1)
    # fov 1's lock-on starts at fov 0's anchor: the stack returned there.
    assert w.laser_auto_focus_controller.positions_at_call == pytest.approx([90.0, 100.0])
    assert (w._laser_af_successes, w._laser_af_failures) == (2, 0)


def test_planes_follow_the_channel_offset_and_skip_only_frames_past_travel(tmp_path):
    w = _worker(tmp_path, relative_range=(-2.0, 0.0), offsets={"A": 0.0, "B": 5.0}, references=[1.0])
    reported = []
    real_report = w._report_skipped_frames
    w._report_skipped_frames = lambda r, p, f, s: (reported.append(list(s)), real_report(r, p, f, s))[1]

    w.acquire_at_position("A1", str(tmp_path), 0)

    # A: 1 - 2 = -1 is past the travel (skipped, never clamped to 0), 1 imaged; B: -1 + 5 = 4 and 6 both imaged.
    assert _planes(w) == _frames([(0, "B", 4.0), (1, "A", 1.0), (1, "B", 6.0)])
    assert reported == [[(0, 0)]]
    assert min(w.piezo.moves) >= 0.0
    warnings = [str(c.args[0]) for c in w._log.warning.call_args_list]
    assert len(warnings) == 1
    assert "plane 0" in warnings[0] and "'A'" in warnings[0] and "below" in warnings[0]


def test_lock_on_that_moves_then_raises_restores_z_pre(tmp_path):
    # Review Focus 1: the lock-on moves Z (100 -> 95 µm), then raises in verification; Z goes back to 100.
    w = _worker(tmp_path, references=[90.0], raise_calls=(1,), record_anchor_um=100.0)

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert w.piezo.moves[:2] == pytest.approx([95.0, 100.0])  # the partial AF move, then the absolute restore
    expected = [(k, c, 98.0 + 2 * k) for k in range(4) for c in ("A", "B")]
    assert _planes(w) == _frames(expected)
    assert (w._laser_af_successes, w._laser_af_failures) == (0, 1)
    assert w.piezo.position == pytest.approx(100.0)
    assert len(w.laser_auto_focus_controller.records) == 1  # a failed lock-on writes no record
    assert any("stacking from" in m for m in _logged(w))


class _PiezoDiesAfter(_FakePiezo):
    def __init__(self, good_moves, **kwargs):
        super().__init__(**kwargs)
        self.good_moves = good_moves

    def move_to(self, um):
        if len(self.moves) >= self.good_moves:
            self.moves.append(um)
            raise RuntimeError("piezo gone")
        super().move_to(um)


def _prepare_run(w):
    w.camera = MagicMock()
    w.microscope = MagicMock()
    w.microscope.addons.squid_laser_engine = None
    w._compute_illumination_wavelengths = lambda: []
    w._run_state = MagicMock()
    w._slack_notifier = None
    w._finish_jobs = MagicMock()
    w.dt = 0
    w.image_count = 0
    w._acquisition_error_count = 0
    w.timestamp_acquisition_started = time.time()


def test_failed_restore_ends_the_run(tmp_path):
    piezo = _PiezoDiesAfter(good_moves=1)  # the AF's partial move works, the restore fails
    w = _worker(tmp_path, references=[90.0], raise_calls=(1,), record_anchor_um=100.0, piezo=piezo)

    with pytest.raises(RuntimeError, match="piezo gone"):
        w.acquire_at_position("A1", str(tmp_path), 0)
    assert w.frames == []

    w2 = _worker(
        tmp_path, references=[90.0], raise_calls=(1,), record_anchor_um=100.0, piezo=_PiezoDiesAfter(good_moves=1)
    )
    _prepare_run(w2)
    w2.run_single_time_point = lambda: w2.acquire_at_position("A1", str(tmp_path), 0)
    with pytest.raises(RuntimeError, match="piezo gone"):
        w2.run()
    assert w2.end_reason == "error"
    assert w2.frames == []


@pytest.mark.parametrize("piezo_scope", [True, False])
def test_returns_to_the_anchor_after_each_fov(tmp_path, piezo_scope):
    references = [100.0, 108.0] if piezo_scope else [1000.0, 1008.0]
    w = _worker(tmp_path, piezo_scope=piezo_scope, references=references)

    for fov, anchor in enumerate(references):
        w.acquire_at_position("A1", str(tmp_path), fov)
        position = w.piezo.position if piezo_scope else w.stage.z_mm * 1000
        assert position == pytest.approx(anchor)
        assert w._anchor_um == pytest.approx(anchor)
        if piezo_scope:
            assert w.z_piezo_um == pytest.approx(anchor)


@pytest.mark.parametrize("order", ["CHANNEL FIRST", "Z FIRST"])
def test_single_plane_relative_range_runs_the_full_path(tmp_path, order):
    w = _worker(tmp_path, relative_range=(3.0, 3.0), offsets={"A": 0.0, "B": 2.0}, order=order)
    assert w.NZ == 1

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert len(w.laser_auto_focus_controller.positions_at_call) == 1
    assert _planes(w) == _frames([(0, "A", 103.0), (0, "B", 105.0)])
    assert w.piezo.position == pytest.approx(100.0)


@pytest.mark.parametrize("piezo_scope, anchor_um", [(True, 120.0), (False, 1200.0)])
def test_time_point_starts_at_the_recorded_anchor(tmp_path, piezo_scope, anchor_um):
    # The Start-time z is usually Z-max: the piezo was left at 180.
    w = _worker(tmp_path, piezo_scope=piezo_scope, record_anchor_um=anchor_um, piezo=_FakePiezo(position=180.0))
    w._require_first_lock_on = True

    w.initialize_z_stack()

    if piezo_scope:
        assert w.piezo.position == pytest.approx(120.0)
        assert w.z_piezo_um == pytest.approx(120.0)
        assert w.stage.z_mm == pytest.approx(w.z_range[0])  # the stepper holds where it is
    else:
        assert w.stage.z_mm == pytest.approx(1.2)
    assert w._require_first_lock_on is False


def test_no_record_requires_the_first_fov_lock_on_and_stops_before_imaging(tmp_path):
    w = _worker(tmp_path, references=[100.0, 100.0], fail_calls=(1,), piezo=_FakePiezo(position=150.0))
    _scan(w, [(1.0, 2.0), (1.5, 2.0)])

    w.initialize_z_stack()
    assert w._require_first_lock_on is True
    assert w.piezo.position == pytest.approx(150.0)  # no record: nothing to move to

    w.run_coordinate_acquisition(str(tmp_path))

    assert w.frames == []
    assert len(w.laser_auto_focus_controller.positions_at_call) == 1  # the second FOV is never visited
    assert w.abort_requested_fn()
    assert w._abort_cause == "error"
    assert any(c.args and c.args[0] == FIRST_FOV_MESSAGE for c in w._log.error.call_args_list)
    assert w.piezo.position == pytest.approx(150.0)  # the failed lock-on left Z at z_pre
    assert not any("stacking from" in m for m in _logged(w))  # the run stops: nothing is stacked


def test_no_record_moves_to_the_first_fov_and_its_coordinate_z_before_locking_on(tmp_path):
    w = _worker(tmp_path, references=[100.0, 104.0], piezo=_FakePiezo(position=150.0))
    _scan(w, [(1.0, 2.0, 1.05), (1.5, 2.0, 1.05)])

    w.initialize_z_stack()
    assert w._require_first_lock_on is True
    w.run_coordinate_acquisition(str(tmp_path))

    af = w.laser_auto_focus_controller
    assert af.stage_at_call[0] == pytest.approx((1.0, 2.0, 1.05))
    assert af.positions_at_call[0] == pytest.approx(150.0)  # where it stood: no record to start from
    assert _planes(w, 0) == _frames([(k, c, 98.0 + 2 * k) for k in range(4) for c in ("A", "B")])
    assert _planes(w, 1) == _frames([(k, c, 102.0 + 2 * k) for k in range(4) for c in ("A", "B")])


def test_coordinate_z_is_honoured_and_a_failed_lock_on_stacks_from_it(tmp_path):
    w = _worker(tmp_path, piezo_scope=False, references=[1000.0], fail_calls=(1,), record_anchor_um=1000.0)
    _scan(w, [(1.0, 2.0, 1.050)])

    w.initialize_z_stack()
    assert w.stage.z_mm == pytest.approx(1.0)  # the record's anchor
    w.run_coordinate_acquisition(str(tmp_path))

    assert w.laser_auto_focus_controller.positions_at_call == pytest.approx([1050.0])
    expected = [(k, c, 1048.0 + 2 * k) for k in range(4) for c in ("A", "B")]
    assert _planes(w) == _frames(expected)
    assert w.stage.z_mm == pytest.approx(1.050)


def test_successful_lock_ons_update_the_record(tmp_path):
    w = _worker(tmp_path, references=[100.0, 108.0, 112.0], fail_calls=(2,))
    af = w.laser_auto_focus_controller
    w.initialize_z_stack()
    assert w._require_first_lock_on is True

    w.acquire_at_position("A1", str(tmp_path), 0)
    assert [r.anchor_um for r in af.records] == pytest.approx([100.0])
    assert w._require_first_lock_on is False

    w.acquire_at_position("A1", str(tmp_path), 1)  # fails: imaged from z_pre (the previous anchor), no record
    assert [r.anchor_um for r in af.records] == pytest.approx([100.0])
    assert _planes(w, 1) == _frames([(k, c, 98.0 + 2 * k) for k in range(4) for c in ("A", "B")])
    assert not w.abort_requested_fn()

    w.acquire_at_position("A1", str(tmp_path), 2)
    assert [r.anchor_um for r in af.records] == pytest.approx([100.0, 112.0])


def test_stepper_relative_stack_moves_the_stage_absolutely(tmp_path):
    w = _worker(tmp_path, piezo_scope=False, references=[1000.0])

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert [m for m in w.stage.moves if m[0] == "z"] == []
    expected = [(k, c, 998.0 + 2 * k) for k in range(4) for c in ("A", "B")]
    assert _planes(w) == _frames(expected)
    assert w.stage.z_mm == pytest.approx(1.0)


def test_relative_rows_record_every_plane_with_z_rel_and_skips(tmp_path):
    # anchor 1, range -2..2 (k = 0, 1, 2): A at -1 (skipped), 1, 3; B (offset -2) at -3, -1 (both skipped), 1.
    w = _worker(tmp_path, relative_range=(-2.0, 2.0), offsets={"A": 0.0, "B": -2.0}, references=[1.0])

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert _planes(w) == _frames([(1, "A", 1.0), (2, "A", 3.0), (2, "B", 1.0)])
    rows = w.coordinates_pd
    assert list(rows["z_level"]) == [0, 1, 2]
    assert list(rows["z_rel (um)"]) == pytest.approx([-2.0, 0.0, 2.0])
    assert list(rows["out_of_travel"]) == ["A;B", "B", ""]
    assert list(rows["z_piezo (um)"]) == pytest.approx([-1.0, 1.0, 3.0])  # nominal, un-offset, even off-travel


def test_absolute_rows_have_no_relative_columns():
    w = MultiPointWorker.__new__(MultiPointWorker)
    w.use_piezo = True
    w.z_piezo_um = 50.0
    w.initialize_coordinates_dataframe()
    w.update_coordinates_dataframe("A1", 0, squid.abc.Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None), fov=0)

    assert "z_rel (um)" not in w.coordinates_pd.columns
    assert "out_of_travel" not in w.coordinates_pd.columns


@pytest.mark.parametrize("piezo_scope", [False, True])
def test_time_lapse_restore_uses_the_anchor_on_a_stepper(tmp_path, piezo_scope):
    references = [1000.0] if not piezo_scope else [100.0]
    w = _worker(tmp_path, piezo_scope=piezo_scope, references=references, nt=2)

    w.acquire_at_position("A1", str(tmp_path), 0)

    # A stepper records the FOV's anchor (not plane 0, 2 µm below it); a piezo scope records the stage z.
    assert _planes(w)[0] == _frames([(0, "A", references[0] - 2.0)])[0]
    assert w._last_time_point_z_pos[("A1", 0)] == pytest.approx(1.0)
    w.stage.z_mm = 1.3
    w.time_point = 1
    w.move_to_coordinate((10.0, 20.0), "A1", 0)
    assert w.stage.z_mm == pytest.approx(1.0)


_Z_SETTLE_S = 0.0123  # a stabilization time no other sleep uses


def _record_events(w, monkeypatch):
    """One ordered log of lock-ons, piezo moves, sleeps and frames."""
    monkeypatch.setattr(squid.stage.utils, "z_stabilization_time_s", lambda: _Z_SETTLE_S)
    events = []
    real_move = w.piezo.move_to
    w.piezo.move_to = lambda um: (events.append(("move", um)), real_move(um))[1]
    w._sleep = lambda sec: events.append(("sleep", sec))
    af = w.laser_auto_focus_controller
    real_af = af.move_to_target
    af.move_to_target = lambda um: (events.append(("af",)), real_af(um))[1]
    real_acquire = w.acquire_camera_image
    w.acquire_camera_image = lambda *a, **k: (events.append(("frame",)), real_acquire(*a, **k))[1]
    return events


@pytest.mark.parametrize("trigger_mode", [TriggerMode.HARDWARE, TriggerMode.SOFTWARE])
@pytest.mark.parametrize("fail_calls", [(), (1,)])
def test_the_first_frame_after_the_lock_on_settles_once_in_both_trigger_modes(
    tmp_path, monkeypatch, trigger_mode, fail_calls
):
    """Ruling R8: as prepare_z_stack after the per-FOV AF, the move to the FOV's first frame settles the
    stabilization time, in hardware trigger too; the later plane steps keep the per-mode piezo delay only."""
    w = _worker(tmp_path, references=[100.0], fail_calls=fail_calls, record_anchor_um=100.0)
    w.liveController.trigger_mode = trigger_mode
    events = _record_events(w, monkeypatch)

    w.acquire_at_position("A1", str(tmp_path), 0)

    first_frame = events.index(("frame",))
    last_move = max(i for i, e in enumerate(events[:first_frame]) if e[0] == "move")
    assert events.index(("af",)) < last_move  # the move to the first frame comes after the lock-on
    assert ("sleep", _Z_SETTLE_S) in events[last_move:first_frame]
    after_first_frame = events[first_frame:]
    assert ("sleep", _Z_SETTLE_S) not in after_first_frame  # the plane steps do not each add it
    later_sleeps = [e[1] for e in after_first_frame if e[0] == "sleep"]
    if trigger_mode == TriggerMode.HARDWARE:
        assert later_sleeps == []
    else:
        assert later_sleeps and all(sec == pytest.approx(0.02) for sec in later_sleeps)  # MULTIPOINT_PIEZO_DELAY_MS


def test_a_fov_with_no_eligible_frame_does_not_settle(tmp_path, monkeypatch):
    # anchor 1, range -10..-4: every target (-9 .. -3) is below the piezo travel.
    w = _worker(tmp_path, relative_range=(-10.0, -4.0), references=[1.0])
    w.liveController.trigger_mode = TriggerMode.HARDWARE
    events = _record_events(w, monkeypatch)

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert w.frames == []
    assert ("sleep", _Z_SETTLE_S) not in events
    assert w.piezo.position == pytest.approx(1.0)


# --- Z First -----------------------------------------------------------------------------------------------


def test_z_first_images_each_channels_eligible_planes_in_order(tmp_path):
    w = _worker(tmp_path, order="Z FIRST", offsets={"B": 1.0}, references=[100.0])

    w.acquire_at_position("A1", str(tmp_path), 0)

    expected = [(k, "A", 98.0 + 2 * k) for k in range(4)] + [(k, "B", 99.0 + 2 * k) for k in range(4)]
    assert _planes(w) == _frames(expected)
    assert len(w.laser_auto_focus_controller.positions_at_call) == 1  # no AF per stack
    assert list(w.coordinates_pd["z_level"]) == [0, 1, 2, 3]
    assert w.piezo.position == pytest.approx(100.0)
    assert w._timepoint_fov_count == 1


@pytest.mark.parametrize("relative_range, last_plane_um", [((-30.0, 0.0), 100.0), ((-10.0, 4.0), 104.0)])
def test_af_per_stack_relocks_from_the_anchor_not_the_first_plane(tmp_path, relative_range, last_plane_um):
    w = _worker(tmp_path, order="Z FIRST", relative_range=relative_range, references=[100.0, 103.0])
    w.laser_af_per_stack = True

    w.acquire_at_position("A1", str(tmp_path), 0)

    af = w.laser_auto_focus_controller
    assert af.positions_at_call == pytest.approx([100.0, 100.0])  # not the first plane, not the last (104)
    z_min = relative_range[0]
    n = len(_planes(w)) // 2
    assert [z for _, name, z in _planes(w) if name == "A"] == pytest.approx([100.0 + z_min + 2 * k for k in range(n)])
    assert [z for _, name, z in _planes(w) if name == "B"] == pytest.approx([103.0 + z_min + 2 * k for k in range(n)])
    assert (w._laser_af_successes, w._laser_af_per_stack_successes) == (1, 1)
    assert w.piezo.position == pytest.approx(103.0)  # the anchor of the last lock-on


def test_failed_per_stack_relock_keeps_the_anchor_with_z_restored(tmp_path):
    w = _worker(tmp_path, order="Z FIRST", references=[100.0, 103.0], fail_calls=(2,))
    w.laser_af_per_stack = True

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert w.laser_auto_focus_controller.positions_at_call == pytest.approx([100.0, 100.0])
    # Channel B stacks from the standing anchor 100.
    assert _planes(w) == _frames([(k, c, 98.0 + 2 * k) for c in ("A", "B") for k in range(4)])
    assert (w._laser_af_per_stack_successes, w._laser_af_per_stack_failures) == (0, 1)
    assert (w._laser_af_successes, w._laser_af_failures) == (1, 0)  # per-stack lock-ons are counted apart
    assert w._anchor_um == pytest.approx(100.0)
    assert w.piezo.position == pytest.approx(100.0)
    assert not w.abort_requested_fn()


def test_per_stack_relock_recomputes_eligibility_for_that_channel(tmp_path):
    # The second anchor (299, piezo travel 0..300) puts B's last plane (301) past the travel.
    w = _worker(tmp_path, order="Z FIRST", relative_range=(-2.0, 2.0), references=[100.0, 299.0])
    w.laser_af_per_stack = True

    w.acquire_at_position("A1", str(tmp_path), 0)

    expected = [(k, "A", 98.0 + 2 * k) for k in range(3)] + [(0, "B", 297.0), (1, "B", 299.0)]
    assert _planes(w) == _frames(expected)
    assert max(w.piezo.moves) <= 300.0  # never commanded past the travel
    warnings = [str(c.args[0]) for c in w._log.warning.call_args_list]
    assert len(warnings) == 1
    assert "plane 2" in warnings[0] and "'B'" in warnings[0] and "past" in warnings[0]
    assert list(w.coordinates_pd["out_of_travel"]) == ["", "", "B"]
    assert w.piezo.position == pytest.approx(299.0)


def test_rows_cover_every_plane_when_the_first_channel_skips_one(tmp_path):
    # anchor 1, range -2..2: A at -1 (skipped), 1, 3; B (offset +2) at 1, 3, 5.
    w = _worker(tmp_path, order="Z FIRST", relative_range=(-2.0, 2.0), offsets={"A": 0.0, "B": 2.0}, references=[1.0])

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert _planes(w) == _frames([(1, "A", 1.0), (2, "A", 3.0), (0, "B", 1.0), (1, "B", 3.0), (2, "B", 5.0)])
    rows = w.coordinates_pd
    assert list(rows["z_level"]) == [0, 1, 2]
    assert list(rows["z_rel (um)"]) == pytest.approx([-2.0, 0.0, 2.0])
    assert list(rows["out_of_travel"]) == ["A", "", ""]
    assert list(rows["z_piezo (um)"]) == pytest.approx([-1.0, 1.0, 3.0])


def test_abort_mid_relative_z_first_writes_rows_reached_and_returns_to_anchor(tmp_path):
    w = _worker(tmp_path, order="Z FIRST", references=[100.0])
    real = w.acquire_camera_image

    def acquire_then_abort(*args, **kwargs):
        real(*args, **kwargs)
        if len(w.frames) == 2:  # channel A, plane 1
            w._aborted.append(True)

    w.acquire_camera_image = acquire_then_abort

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert _planes(w) == _frames([(0, "A", 98.0), (1, "A", 100.0)])
    assert list(w.coordinates_pd["z_level"]) == [0, 1]  # planes 2 and 3 were never reached
    assert w.piezo.position == pytest.approx(100.0)
    assert w._timepoint_fov_count == 1


def test_z_first_settles_only_after_each_lock_on(tmp_path, monkeypatch):
    w = _worker(tmp_path, order="Z FIRST", references=[100.0, 103.0])
    w.laser_af_per_stack = True
    w.liveController.trigger_mode = TriggerMode.HARDWARE
    events = _record_events(w, monkeypatch)

    w.acquire_at_position("A1", str(tmp_path), 0)

    # Hardware trigger adds no per-step delay: the only sleeps are the two post-lock-on settles.
    assert [e for e in events if e[0] == "sleep"] == [("sleep", _Z_SETTLE_S)] * 2
    afs = [i for i, e in enumerate(events) if e == ("af",)]
    frames = [i for i, e in enumerate(events) if e == ("frame",)]
    sleeps = [i for i, e in enumerate(events) if e[0] == "sleep"]
    assert afs[0] < sleeps[0] < frames[0]
    assert frames[3] < afs[1] < sleeps[1] < frames[4]


def _warnings(w):
    return [str(c.args[0]) for c in w._log.warning.call_args_list]


@pytest.mark.parametrize(
    "order, per_stack", [("CHANNEL FIRST", False), ("Z FIRST", False), ("Z FIRST", True)]  # per stack is Z First only
)
def test_one_skipped_frames_warning_per_fov_listing_every_channel(tmp_path, order, per_stack):
    # anchor 1, range -2..0: both channels' plane 0 (-1) is below the travel.
    w = _worker(tmp_path, order=order, relative_range=(-2.0, 0.0), references=[1.0, 1.0])
    w.laser_af_per_stack = per_stack

    w.acquire_at_position("A1", str(tmp_path), 0)

    warnings = _warnings(w)
    assert len(warnings) == 1
    assert "plane 0 'A'" in warnings[0] and "plane 0 'B'" in warnings[0]
    assert "2 frame(s)" in warnings[0]
    assert list(w.coordinates_pd["out_of_travel"]) == ["A;B", ""]
    assert (w._relative_frames_skipped, w._relative_fovs_with_skips) == (2, 1)  # one FOV, however many groups


def test_without_af_per_stack_the_targets_are_computed_once_per_fov(tmp_path):
    w = _worker(tmp_path, order="Z FIRST", references=[100.0])
    calls = []
    real = w._relative_targets
    w._relative_targets = lambda *a, **k: (calls.append(1), real(*a, **k))[1]

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert len(calls) == 1


def test_z_first_settle_carries_to_channel_1_when_channel_0_is_entirely_out_of_travel(tmp_path, monkeypatch):
    w = _worker(tmp_path, order="Z FIRST", offsets={"A": -500.0}, references=[100.0])
    w.liveController.trigger_mode = TriggerMode.HARDWARE
    events = _record_events(w, monkeypatch)

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert [name for _, _, name, _ in w.frames] == ["B"] * 4
    first_frame = events.index(("frame",))
    assert [e for e in events if e[0] == "sleep"] == [("sleep", _Z_SETTLE_S)]
    assert events.index(("sleep", _Z_SETTLE_S)) < first_frame


def test_z_first_settle_carries_past_an_entirely_skipped_relocked_channel(tmp_path, monkeypatch):
    w = _worker(
        tmp_path, order="Z FIRST", channels=("A", "B", "C"), offsets={"B": -500.0}, references=[100.0, 101.0, 102.0]
    )
    w.laser_af_per_stack = True
    w.liveController.trigger_mode = TriggerMode.HARDWARE
    events = _record_events(w, monkeypatch)

    w.acquire_at_position("A1", str(tmp_path), 0)

    assert sorted({name for _, _, name, _ in w.frames}) == ["A", "C"]
    sleeps = [i for i, e in enumerate(events) if e[0] == "sleep"]
    frames = [i for i, e in enumerate(events) if e == ("frame",)]
    afs = [i for i, e in enumerate(events) if e == ("af",)]
    assert len(afs) == 3 and len(sleeps) == 2  # A's first move, C's first move: B had none
    assert afs[0] < sleeps[0] < frames[0]
    assert afs[2] < sleeps[1] < frames[4]


# --- Skip notices: the skipped frames reach the Zarr and OME-TIFF writers ------------------------------------


class _FakeRunner:
    """Records what the worker dispatches, in one event list shared with the frames."""

    def __init__(self, name, events):
        self.name, self.events, self.jobs = name, events, []

    def dispatch(self, job):
        self.jobs.append(job)
        self.events.append((self.name, job))
        return True

    def wait_ready(self, timeout_s=None):
        return True


class _InlineRunner(_FakeRunner):
    """Runs each job at once, with the writer info JobRunner.dispatch injects."""

    def __init__(self, name, events, zarr_writer_info):
        super().__init__(name, events)
        self.zarr_writer_info = zarr_writer_info

    def dispatch(self, job):
        super().dispatch(job)
        job.zarr_writer_info = self.zarr_writer_info
        job.run()
        return True


def _runners(w, events):
    runners = {cls: _FakeRunner(cls.__name__, events) for cls in (SaveZarrJob, SaveOMETiffJob, SaveImageJob)}
    w._job_runners = list(runners.items())
    return runners


def _notices(runner):
    return [job.capture_info for job in runner.jobs if job.capture_info.skipped_frames is not None]


def _arm_frame_callback(w):
    """The state _image_callback needs; with a trigger outstanding (cleared event) a frame it receives is saved."""
    w._ready_for_next_trigger = threading.Event()
    w._ready_for_next_trigger.set()
    w._image_callback_idle = threading.Event()
    w._image_callback_idle.set()
    w._current_capture_info = None
    w._first_job_dispatched = True
    w._timepoint_image_count = w.image_count = 0
    w._run_state = MagicMock()
    w._emit_plate_layout = lambda image: None


def _capture_info(w, config, file_ID, current_path, z_level, region_id, fov, config_idx):
    return CaptureInfo(
        position=w.stage.get_pos(),
        z_index=z_level,
        capture_time=time.time(),
        configuration=config,
        save_directory=current_path,
        file_id=file_ID,
        region_id=region_id,
        fov=fov,
        configuration_idx=config_idx,
        time_point=w.time_point,
    )


def _deliver_frames(w, shape, dtype=np.uint16):
    """Every frame the harness records also goes through the worker's frame callback, as a camera delivers it."""
    _arm_frame_callback(w)
    record = w.acquire_camera_image

    def acquire(config, file_ID, current_path, z_level, region_id, fov, config_idx):
        record(config, file_ID, current_path, z_level, region_id, fov, config_idx)
        w._current_capture_info = _capture_info(w, config, file_ID, current_path, z_level, region_id, fov, config_idx)
        w._ready_for_next_trigger.clear()
        frame = np.full(shape, len(w.frames), dtype=dtype)
        w._image_callback(
            CameraFrame(len(w.frames), time.time(), frame, CameraFrameFormat.RAW, CameraPixelFormat.MONO16)
        )

    w.acquire_camera_image = acquire


def _record_frames_as_events(w, events):
    record = w.acquire_camera_image

    def acquire(config, file_ID, current_path, z_level, region_id, fov, config_idx):
        events.append(("frame", config_idx, z_level))
        record(config, file_ID, current_path, z_level, region_id, fov, config_idx)

    w.acquire_camera_image = acquire


@pytest.mark.parametrize("order, per_stack", [("CHANNEL FIRST", False), ("Z FIRST", False), ("Z FIRST", True)])
def test_skipped_frames_dispatch_one_notice_per_lock_on_to_zarr_and_ome_tiff_runners(tmp_path, order, per_stack):
    # anchor 1, range -2..0: both channels' plane 0 (-1 µm) is below the travel.
    w = _worker(tmp_path, order=order, relative_range=(-2.0, 0.0), references=[1.0, 1.0])
    w.laser_af_per_stack = per_stack
    w.time_point = 3
    w._frame_format = ((300, 500), "uint16")  # the run has had its first frame
    events = []
    runners = _runners(w, events)
    _record_frames_as_events(w, events)

    w.acquire_at_position("A1", str(tmp_path), 0)

    groups = [((0, 0),), ((1, 0),)] if per_stack else [((0, 0), (1, 0))]
    for cls in (SaveZarrJob, SaveOMETiffJob):
        notices = _notices(runners[cls])
        assert [n.skipped_frames.frames for n in notices] == groups
        for notice in notices:
            assert (notice.region_id, notice.fov, notice.time_point) == ("A1", 0, 3)
            assert (notice.skipped_frames.frame_shape, notice.skipped_frames.dtype) == ((300, 500), "uint16")
            assert notice.save_directory == str(tmp_path)
        for job in runners[cls].jobs:
            assert job.capture_image.image_array is None
            assert job.save_settings is w._save_settings
    assert runners[SaveImageJob].jobs == []  # individual TIFFs write nothing for a skipped frame
    # Each group's notice goes out before that group's first frame.
    sequence = [
        ("notice", e[1].capture_info.skipped_frames.frames) if e[0] == "SaveZarrJob" else e
        for e in events
        if e[0] != "SaveOMETiffJob"
    ]
    if per_stack:
        assert sequence == [("notice", ((0, 0),)), ("frame", 0, 1), ("notice", ((1, 0),)), ("frame", 1, 1)]
    else:
        assert sequence == [("notice", ((0, 0), (1, 0))), ("frame", 0, 1), ("frame", 1, 1)]


def test_skip_notice_carries_the_shape_of_a_nonsquare_frame(tmp_path):
    # fov 0 (anchor 100) images every plane; fov 1's anchor 1 puts plane 0 (-1 µm) below the travel.
    w = _worker(tmp_path, references=[100.0, 1.0])
    runners = _runners(w, [])
    _deliver_frames(w, shape=(300, 500))

    w.acquire_at_position("A1", str(tmp_path), 0)
    w.acquire_at_position("A1", str(tmp_path), 1)

    for cls in (SaveZarrJob, SaveOMETiffJob):
        (notice,) = _notices(runners[cls])  # fov 0 skipped nothing: no notice
        assert notice.fov == 1
        assert notice.skipped_frames.frames == ((0, 0), (1, 0))
        assert notice.skipped_frames.frame_shape == (300, 500)  # (height, width), not (500, 300)
        assert notice.skipped_frames.dtype == "uint16"


def test_wholly_skipped_first_fov_is_deferred_until_the_first_real_frame(tmp_path):
    pytest.importorskip("tensorstore")
    # Range -10..-4 (4 planes): fov 0's anchor 1 puts every target (-9 .. -3 µm) below the travel; fov 1's
    # anchor 100 none.
    w = _worker(tmp_path, relative_range=(-10.0, -4.0), references=[1.0, 100.0])
    zarr_info = ZarrWriterInfo(
        base_path=str(tmp_path), t_size=1, c_size=2, z_size=w.NZ, region_fov_counts={"A1": 2}, channel_names=["A", "B"]
    )
    runner = _InlineRunner("SaveZarrJob", [], zarr_info)
    w._job_runners = [(SaveZarrJob, runner)]
    _deliver_frames(w, shape=(300, 500), dtype=np.uint16)
    SaveZarrJob.clear_writers()
    try:
        w.acquire_at_position("A1", str(tmp_path), 0)
        assert runner.jobs == []  # no frame yet: nothing to size fov 0's notice with
        w.acquire_at_position("A1", str(tmp_path), 1)
        assert SaveZarrJob.finalize_all_writers()
    finally:
        SaveZarrJob.clear_writers()

    first_image, notice = runner.jobs[0].capture_info, runner.jobs[1].capture_info
    assert (first_image.fov, first_image.skipped_frames) == (1, None)  # fov 1's first frame, then the notice
    assert notice.fov == 0
    assert notice.skipped_frames.frames == tuple((c, k) for c in range(2) for k in range(4))
    assert (notice.skipped_frames.frame_shape, notice.skipped_frames.dtype) == ((300, 500), "uint16")
    assert len(runner.jobs) == 2 + 7  # and fov 1's other frames
    for fov in (0, 1):
        with open(os.path.join(zarr_info.get_output_path("A1", fov), "zarr.json")) as f:
            assert json.load(f)["shape"] == [1, 2, w.NZ, 300, 500]


def test_a_run_with_no_frame_at_all_sends_its_notices_with_the_processed_frame_shape(tmp_path):
    # Range -10..-4 from anchor 1: every frame of both FOVs is below the travel.
    w = _worker(tmp_path, relative_range=(-10.0, -4.0), references=[1.0, 1.0])
    _prepare_run(w)
    events = []
    runners = _runners(w, events)
    w._frame_shape_without_a_frame = MagicMock(
        side_effect=lambda: (events.append(("probe",)), ((300, 500), "uint16"))[1]
    )
    w._wait_for_outstanding_callback_images = MagicMock(side_effect=lambda: events.append(("wait",)))
    w.camera.remove_frame_callback.side_effect = lambda *_: events.append(("remove callback",))
    w._finish_jobs = MagicMock(side_effect=lambda: events.append(("finish jobs",)))
    w.run_single_time_point = lambda: [w.acquire_at_position("A1", str(tmp_path), fov) for fov in (0, 1)]

    w.run()

    assert w.frames == []
    w._frame_shape_without_a_frame.assert_called_once()
    for cls in (SaveZarrJob, SaveOMETiffJob):
        notices = _notices(runners[cls])
        assert [n.fov for n in notices] == [0, 1]
        for notice in notices:
            assert notice.skipped_frames.frames == tuple((c, k) for c in range(2) for k in range(4))
            assert (notice.skipped_frames.frame_shape, notice.skipped_frames.dtype) == ((300, 500), "uint16")
    assert runners[SaveImageJob].jobs == []
    # The notices go out after the last frames are waited for, while the runners are still open.
    assert [e[0] for e in events] == [
        "wait",
        "wait",
        "probe",
        "SaveZarrJob",
        "SaveOMETiffJob",
        "SaveZarrJob",
        "SaveOMETiffJob",
        "remove callback",
        "finish jobs",
    ]
    assert w.end_reason == "completed"


@pytest.fixture(scope="module")
def _sim_scope():
    from control.microscope import Microscope

    scope = Microscope.build_from_global_config(simulated=True)
    scope.camera.start_streaming()
    try:
        yield scope
    finally:
        scope.camera.stop_streaming()
        scope.close()


@pytest.fixture
def sim_camera(_sim_scope):
    """The simulated scope's camera, streaming, back at its full-frame setup after each test."""
    camera = _sim_scope.camera
    roi = camera.get_region_of_interest()
    binning = camera.get_binning()
    yield camera
    camera.set_binning(*binning)
    camera.set_software_crop_ratio(1.0, 1.0)
    camera.set_region_of_interest(*roi)
    camera.enable_callbacks(True)


def _probe_worker(camera):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w.camera = camera
    w._log = MagicMock()
    return w


@pytest.mark.parametrize("setup", ["binning 1x1", "binning 2x2", "software crop", "partial hardware ROI"])
def test_frame_shape_without_a_frame_matches_the_frames_an_acquisition_delivers(sim_camera, setup):
    camera = sim_camera
    if setup == "binning 1x1":
        camera.set_binning(1, 1)
    elif setup == "binning 2x2":
        camera.set_binning(2, 2)
    elif setup == "software crop":
        camera.set_software_crop_ratio(0.5, 0.25)
    else:
        camera.set_region_of_interest(16, 8, 1200, 800)

    probed = _probe_worker(camera)._frame_shape_without_a_frame()

    delivered = []
    callback_id = camera.add_frame_callback(delivered.append)
    try:
        while not camera.get_ready_for_trigger():
            time.sleep(0.001)
        camera.send_trigger()
    finally:
        camera.remove_frame_callback(callback_id)
    (frame,) = delivered
    assert probed == (frame.frame.shape[:2], str(frame.frame.dtype))
    if setup == "binning 2x2":
        # The simulated camera keeps an unbinned ROI while delivering binned frames.
        _, _, roi_width, roi_height = camera.get_region_of_interest()
        assert probed[0] != (roi_height, roi_width)
    if setup == "software crop":
        assert probed[0][0] != probed[0][1]


def test_the_shape_frame_never_reaches_the_save_callbacks(tmp_path, sim_camera):
    w = _worker(tmp_path)
    w.camera = sim_camera
    runners = _runners(w, [])
    _arm_frame_callback(w)
    # A trigger outstanding: a frame reaching the acquisition callback now would be saved.
    w._current_capture_info = _capture_info(w, w.selected_configurations[0], "f", str(tmp_path), 0, "A1", 0, 0)
    w._ready_for_next_trigger.clear()
    seen = []
    callback_ids = [sim_camera.add_frame_callback(seen.append), sim_camera.add_frame_callback(w._image_callback)]
    try:
        probed = w._frame_shape_without_a_frame()
    finally:
        for callback_id in callback_ids:
            sim_camera.remove_frame_callback(callback_id)

    assert probed is not None
    assert seen == []
    assert all(runner.jobs == [] for runner in runners.values())
    assert w.image_count == 0
    assert sim_camera.get_callbacks_enabled()


@pytest.mark.parametrize("failure", ["never ready", "trigger raises", "read returns None", "read raises"])
def test_a_shape_frame_failure_is_a_run_error_and_cleanup_continues(tmp_path, failure):
    # Range -10..-4 from anchor 1: every frame is below the travel, so the run ends with no frame.
    w = _worker(tmp_path, relative_range=(-10.0, -4.0), references=[1.0])
    _prepare_run(w)
    runners = _runners(w, [])
    camera = w.camera
    camera.get_ready_for_trigger.return_value = failure != "never ready"
    if failure == "trigger raises":
        camera.send_trigger.side_effect = RuntimeError("trigger failed")
    if failure == "read returns None":
        camera.read_camera_frame.return_value = None
    else:
        frame = np.zeros((300, 500), dtype=np.uint16)
        camera.read_camera_frame.return_value = CameraFrame(
            1, 0.0, frame, CameraFrameFormat.RAW, CameraPixelFormat.MONO16
        )
    if failure == "read raises":
        camera.read_camera_frame.side_effect = RuntimeError("read failed")
    w._frame_wait_timeout_s = lambda: 0.05
    durations = []
    probe = w._frame_shape_without_a_frame

    def timed_probe():
        start = time.monotonic()
        try:
            return probe()
        finally:
            durations.append(time.monotonic() - start)

    w._frame_shape_without_a_frame = timed_probe
    w.run_single_time_point = lambda: w.acquire_at_position("A1", str(tmp_path), 0)

    w.run()

    assert len(durations) == 1 and durations[0] < 1.0  # the deadline is 0.05 s
    assert [c for c in camera.mock_calls if c[0] == "enable_callbacks"] == [
        call.enable_callbacks(False),
        call.enable_callbacks(True),
    ]
    if failure == "never ready":
        camera.send_trigger.assert_not_called()
    w._finish_jobs.assert_called_once()
    camera.remove_frame_callback.assert_called_once()
    assert w._acquisition_error_count == 1
    assert w.end_reason == "completed_with_errors"
    assert any("skip notice" in str(c.args[0]) for c in w._log.error.call_args_list)
    assert all(runner.jobs == [] for runner in runners.values())
    assert list(w.coordinates_pd["out_of_travel"]) == ["A;B"] * 4
    assert (w._relative_frames_skipped, w._relative_fovs_with_skips) == (8, 1)


def test_skips_are_counted_for_the_timepoint_summary(tmp_path):
    # Range -2..4 (planes at -2, 0, 2, 4 µm). Time point 0: anchor 1 skips plane 0 of both channels, anchor 100
    # nothing, anchor 299 planes 2 and 3 of both channels (piezo travel 0..300). Time point 1 skips nothing.
    w = _worker(tmp_path, references=[1.0, 100.0, 299.0, 100.0, 100.0, 100.0], nt=2)
    _scan(w, [(1.0, 2.0), (1.5, 2.0), (2.0, 2.0)])
    w._slack_notifier = MagicMock()
    w._slack_notifier.estimate_remaining_time.return_value = 0.0
    w.timestamp_acquisition_started = time.time()

    for t in range(2):
        w.time_point = t
        w.run_single_time_point()

    stats = [c.args[0] for c in w.callbacks.signal_slack_timepoint_notification.call_args_list]
    assert [(s.relative_frames_skipped, s.relative_fovs_with_skips) for s in stats] == [(6, 2), (0, 0)]
