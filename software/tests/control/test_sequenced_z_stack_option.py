"""The per-acquisition "Fast Z-stack" option: when it applies, how it is saved and loaded,
and that the worker uses it instead of the ini flag."""

import contextlib
import logging
import threading
import time
import types
from unittest.mock import MagicMock

import pytest

import control._def
import control.core.multi_point_worker
import squid.abc
from control.acquisition_yaml_loader import AcquisitionYAMLData, parse_acquisition_yaml
from control.core.acquisition_settings import (
    acquisition_data_from_blocks,
    apply_acquisition_settings,
    export_acquisition_settings,
)
from control.core.multi_point_utils import (
    ScanPositionInformation,
    sequenced_z_stack_block_reason,
    stage_offers_sequenced_z_stack,
)
from control.core.multi_point_worker import MultiPointWorker
from tests.control.core.test_acquisition_settings import _channel_names, _controller, _some_fovs


class _Stage:
    def __init__(self, seq):
        self._seq = seq

    def sequenced_z_stack(self):
        return self._seq


_REAL = _Stage(types.SimpleNamespace(simulated=False))
_OK = dict(
    nz=10,
    use_piezo=False,
    hardware_trigger=True,
    n_channels=1,
    z_stack_order="CHANNEL FIRST",
    channel_offsets_applied=False,
)


def test_block_reason_is_none_when_everything_allows_it():
    assert sequenced_z_stack_block_reason(_REAL, **_OK) is None
    assert sequenced_z_stack_block_reason(_REAL, **{**_OK, "n_channels": 3, "z_stack_order": "Z FIRST"}) is None


@pytest.mark.parametrize(
    "stage, overrides, words",
    [
        (_Stage(None), {}, "cannot run z-stacks"),
        (_Stage(types.SimpleNamespace(simulated=True)), {}, "cannot run z-stacks"),
        (_REAL, {"nz": 1}, "Nz is 1"),
        (_REAL, {"use_piezo": True}, "piezo"),
        (_REAL, {"hardware_trigger": False}, "hardware trigger"),
        (_REAL, {"channel_offsets_applied": True}, "z offsets"),
        (_REAL, {"n_channels": 2}, "2 channels in channel-first order"),
    ],
)
def test_block_reason_names_what_is_in_the_way(stage, overrides, words):
    assert words in sequenced_z_stack_block_reason(stage, **{**_OK, **overrides})


def test_scan_unit_triggering_the_camera_blocks_it():
    assert "scan unit" in sequenced_z_stack_block_reason(_REAL, **_OK, scan_unit_triggers_camera=True)


def test_stage_offers_only_with_a_real_controller():
    assert stage_offers_sequenced_z_stack(_REAL)
    assert not stage_offers_sequenced_z_stack(_Stage(None))
    assert not stage_offers_sequenced_z_stack(_Stage(types.SimpleNamespace(simulated=True)))
    assert not stage_offers_sequenced_z_stack(None)


def _yaml(tmp_path, extra_z_stack_line=""):
    p = tmp_path / "acquisition.yaml"
    p.write_text(
        "acquisition:\n  widget_type: wellplate\n"
        "z_stack:\n  nz: 5\n  delta_z_mm: 0.002\n" + extra_z_stack_line + "channels:\n  - name: BF\n"
    )
    return str(p)


def test_yaml_loader_reads_the_option_and_keeps_none_for_older_files(tmp_path):
    assert parse_acquisition_yaml(_yaml(tmp_path)).use_sequenced_z_stack is None
    assert parse_acquisition_yaml(_yaml(tmp_path, "  use_sequenced_z_stack: true\n")).use_sequenced_z_stack is True
    assert parse_acquisition_yaml(_yaml(tmp_path, "  use_sequenced_z_stack: false\n")).use_sequenced_z_stack is False


def test_yaml_loader_rejects_a_non_boolean(tmp_path):
    with pytest.raises(ValueError, match="use_sequenced_z_stack"):
        parse_acquisition_yaml(_yaml(tmp_path, "  use_sequenced_z_stack: maybe\n"))


@pytest.mark.parametrize("saved, start, expected", [(True, False, True), (False, True, False), (None, True, True)])
def test_apply_sets_the_option_only_when_the_file_has_it(saved, start, expected):
    scope, mpc = _controller()
    mpc.set_use_sequenced_z_stack(start)
    data = AcquisitionYAMLData(
        widget_type="wellplate",
        channel_names=_channel_names(scope, mpc, 1),
        wellplate_regions=[{"name": "A1", "fovs": _some_fovs(mpc)}],
        use_sequenced_z_stack=saved,
    )
    apply_acquisition_settings(mpc, mpc.scanCoordinates, scope, data)
    assert mpc.use_sequenced_z_stack is expected


def test_export_then_apply_round_trips_the_option():
    scope, mpc = _controller()
    mpc.set_selected_configurations(_channel_names(scope, mpc, 1))
    mpc.set_use_sequenced_z_stack(True)
    mpc.scanCoordinates.add_region_from_fovs("A1", _some_fovs(mpc))
    settings, coordinates = export_acquisition_settings(mpc, mpc.scanCoordinates, scope.objective_store, scope.camera)
    assert settings["z_stack"]["use_sequenced_z_stack"] is True

    scope2, mpc2 = _controller()
    mpc2.set_use_sequenced_z_stack(False)
    apply_acquisition_settings(mpc2, mpc2.scanCoordinates, scope2, acquisition_data_from_blocks(settings, coordinates))
    assert mpc2.use_sequenced_z_stack is True


def test_controller_defaults_to_the_ini_flag_and_passes_the_option_to_the_worker(monkeypatch):
    monkeypatch.setattr(control._def, "USE_HARDWARE_SEQUENCED_Z_STACK", True)
    scope, mpc = _controller()
    assert mpc.use_sequenced_z_stack is True
    mpc.timestamp_acquisition_started = 0.0  # set by run_acquisition() in real use
    empty = ScanPositionInformation(scan_region_coords_mm=[], scan_region_names=[], scan_region_fov_coords_mm={})
    assert mpc.build_params(empty).use_sequenced_z_stack is True
    mpc.set_use_sequenced_z_stack(False)
    assert mpc.build_params(empty).use_sequenced_z_stack is False


def test_controller_block_reason_uses_overrides():
    scope, mpc = _controller()  # simulated stage: no controller-run stacks
    assert not mpc.sequenced_z_stack_available()
    assert "cannot run z-stacks" in mpc.sequenced_z_stack_block_reason(nz=5, n_channels=1)


def _worker(use_option, **overrides):
    fields = dict(
        use_sequenced_z_stack=use_option,
        stage=_REAL,
        NZ=10,
        use_piezo=False,
        liveController=types.SimpleNamespace(trigger_mode=control._def.TriggerMode.HARDWARE),
        selected_configurations=[object()],
        z_stack_order="CHANNEL FIRST",
        apply_channel_offset=False,
        do_reflection_af=False,
        _scan_unit_triggers_camera=lambda: False,
        _sequenced_block_reason_logged=None,
        _log=logging.getLogger("test_sequenced_z_stack_option"),
    )
    fields.update(overrides)
    return types.SimpleNamespace(**fields)


def test_worker_uses_the_option_not_the_ini_flag(monkeypatch):
    monkeypatch.setattr(control._def, "USE_HARDWARE_SEQUENCED_Z_STACK", True)
    assert MultiPointWorker._sequenced_stack_eligible(_worker(False)) is False
    monkeypatch.setattr(control._def, "USE_HARDWARE_SEQUENCED_Z_STACK", False)
    assert MultiPointWorker._sequenced_stack_eligible(_worker(True)) is True


def test_worker_logs_why_it_steps_z_itself_once(caplog):
    w = _worker(True, selected_configurations=[object(), object()])
    with caplog.at_level(logging.WARNING, logger="test_sequenced_z_stack_option"):
        assert MultiPointWorker._sequenced_stack_eligible(w) is False
        assert MultiPointWorker._sequenced_stack_eligible(w) is False
    msgs = [r.getMessage() for r in caplog.records if "Fast Z-stack selected but not used" in r.getMessage()]
    assert len(msgs) == 1 and "2 channels in channel-first order" in msgs[0]


# ---- the light switch-on pulse before a controller-run stack ---------------------------------------------


class _Clock:
    """monotonic() for the worker module; the worker's _sleep advances it, so deadlines pass instantly."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, s):
        self.t += s


class _StackStage:
    def __init__(self, seq, z_mm=1.0):
        self.seq = seq
        self.z_mm = z_mm
        self.moves = []

    def sequenced_z_stack(self):
        return self.seq

    def get_pos(self):
        return squid.abc.Pos(x_mm=10.0, y_mm=20.0, z_mm=self.z_mm, theta_rad=None)

    def move_z_to(self, z_mm):
        self.moves.append(z_mm)
        self.z_mm = z_mm


class _PulsingSeq:
    """prepare_light() reports a camera pulse. pulse_frame_arrives: whether the frame callback drops that
    frame (unarmed) in time. start() stands in for the controller plus the frame callback: each of its
    n_levels frames takes the next armed capture info."""

    simulated = False
    extra_steps_after_last_level = 1

    def __init__(self, worker, pulse_frame_arrives):
        self.w = worker
        self.pulse_frame_arrives = pulse_frame_arrives
        self.started_with = None
        self.levels_taken = []

    def prepare_light(self):
        if self.pulse_frame_arrives:
            self.w._unarmed_frames_dropped += 1
        return True

    def start(self, step_mm, n_levels):
        self.started_with = (step_mm, n_levels)
        for _ in range(n_levels):
            self.levels_taken.append(self.w._burst_capture_infos.popleft().z_index)
            self.w._burst_frames_received += 1

    def running(self):
        return False

    def finish(self):
        pass

    def abort(self):
        pass


def _stack_worker(monkeypatch, pulse_frame_arrives, nz=3):
    clock = _Clock()
    monkeypatch.setattr(
        control.core.multi_point_worker,
        "time",
        types.SimpleNamespace(monotonic=clock.monotonic, time=time.time, perf_counter=time.perf_counter),
    )
    w = MultiPointWorker.__new__(MultiPointWorker)
    seq = _PulsingSeq(w, pulse_frame_arrives)
    w.stage = _StackStage(seq)
    w.NZ = nz
    w.deltaZ = 0.001
    w.z_stack_order = "CHANNEL FIRST"
    w.selected_configurations = [MagicMock(name="BF")]
    w._config_already_applied = lambda config: True
    w._current_z_offset_um = 0.0
    w.time_point = 0
    w.total_scans = nz
    w.camera = MagicMock()
    w.camera.get_total_frame_time.return_value = 10.0
    w.camera.get_ready_for_trigger.return_value = True
    w._ready_for_next_trigger = threading.Event()
    w._ready_for_next_trigger.set()
    w._unarmed_frames_dropped = 0
    w._burst_capture_infos = None
    w._burst_frames_received = 0
    w._backpressure = MagicMock()
    w._backpressure.should_throttle.return_value = False
    w._timing = MagicMock()
    w._timing.get_timer.return_value = contextlib.nullcontext()
    w.callbacks = MagicMock()
    w._log = MagicMock()
    w._sleep = clock.advance
    w.abort_requested_fn = lambda: False
    w._abort_due_to_error = MagicMock()
    return w, seq


def test_stack_runs_once_the_pulse_frame_is_dropped(monkeypatch):
    w, seq = _stack_worker(monkeypatch, pulse_frame_arrives=True)
    steps = MultiPointWorker._acquire_sequenced_stack(w, w.selected_configurations[0], 0, 0, "/tmp", 0, False)
    assert seq.started_with == (0.001, 3)
    assert seq.levels_taken == [0, 1, 2]
    assert steps == 3 - 1 + seq.extra_steps_after_last_level
    w._abort_due_to_error.assert_not_called()


def test_pulse_frame_that_never_shows_up_stops_the_acquisition_before_the_stack(monkeypatch):
    # A pulse frame arriving after the wait would be taken as level 0 and shift every level by one
    # (review of PR 763), so the stack must not be armed or started without it.
    w, seq = _stack_worker(monkeypatch, pulse_frame_arrives=False)
    steps = MultiPointWorker._acquire_sequenced_stack(w, w.selected_configurations[0], 0, 0, "/tmp", 0, False)
    assert seq.started_with is None
    assert w._burst_capture_infos is None
    assert w._ready_for_next_trigger.is_set()  # a late frame is dropped as unarmed
    w._abort_due_to_error.assert_called_once()
    assert steps == 0
    assert w.stage.z_mm == 1.0
