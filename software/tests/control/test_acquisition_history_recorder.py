"""The acquisition history recorder turns controller events into history events and preview jobs."""

import dataclasses
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pytest

import control._def
import control.acquisition_history_recorder as recorder_module
from control.acquisition_history_recorder import (
    AcquisitionHistoryRecorder,
    NullAcquisitionHistoryRecorder,
    map_protocol_outcome,
    metadata_from_params,
)
from squid.acquisition_history import HistoryStore, entry_id_for
from tests.control.gallery_fixtures import write_acquisition

CHANNELS = ["BF LED matrix full", "Fluorescence 488 nm Ex"]

# ---------------------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------------------


def make_params(experiment_id="plate_2026-11-08_10-00-00.000000"):
    return SimpleNamespace(
        experiment_ID=experiment_id,
        base_path="unused",
        acquisition_start_time=1762596000.0,
        NZ=3,
        deltaZ=0.0015,
        Nt=2,
        deltat=30.0,
        selected_configurations=[SimpleNamespace(name=c) for c in CHANNELS],
        scan_position_information=SimpleNamespace(
            scan_region_names=["A1", "B2"],
            scan_region_fov_coords_mm={"A1": [(1.0, 2.0)] * 4, "B2": [(1.0, 2.0)] * 4},
        ),
        do_autofocus=False,
        do_reflection_autofocus=True,
        laser_af_per_stack=False,
        xy_mode="Select Wells",
        skip_saving=False,
        z_stacking_config="FROM BOTTOM",
        use_piezo=False,
    )


@pytest.fixture
def params():
    return make_params()


@pytest.fixture
def saving_root(tmp_path):
    root = tmp_path / "saved"
    root.mkdir()
    return root


@pytest.fixture
def folder(saving_root):
    return write_acquisition(saving_root, "plate_2026-11-08_10-00-00.000000", with_overview=True)


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history")


@pytest.fixture
def executor():
    ex = ThreadPoolExecutor(1)
    yield ex
    ex.shutdown(wait=True)


def make_recorder(store, executor, **kwargs):
    kwargs.setdefault("software_version", "v-test")
    kwargs.setdefault("machine", "scope-1")
    return AcquisitionHistoryRecorder(store, preview_executor=executor, **kwargs)


@pytest.fixture
def recorder(store, executor):
    return make_recorder(store, executor)


def events_of(store, type_, entry_id=None):
    return [e for e in store.events() if e.get("type") == type_ and (entry_id is None or e.get("id") == entry_id)]


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ---------------------------------------------------------------------------------------------------------
# started / finished / previews
# ---------------------------------------------------------------------------------------------------------


def test_started_and_finished_write_a_complete_entry_with_metadata_and_preview(store, recorder, params, folder):
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    assert entry_id == entry_id_for(str(folder))
    assert store.load()[0].is_running
    recorder.finished(entry_id, "completed", images=16, expected_images=16)
    assert store.load()[0].preview is None  # finished alone never makes the preview
    recorder.make_preview(entry_id, str(folder)).result(timeout=10)
    [e] = store.load()
    assert e.kind == "run" and e.name == os.path.basename(str(folder)) and e.parent is None
    assert e.outcome == "complete" and e.fraction_done == 1.0 and e.metadata["channels"] == CHANNELS
    assert e.metadata["software_version"] == "v-test" and e.metadata["machine"] == "scope-1" and e.pid == os.getpid()
    assert e.machine == "scope-1" and e.pid_started_at == pytest.approx(psutil.Process().create_time())
    assert e.metadata["file_format"] == control._def.FILE_SAVING_OPTION.value
    assert e.metadata["expected_images"] == 8 * 3 * 2 * 2
    assert e.preview == f"{entry_id}.png" and (store.previews_dir / e.preview).is_file() and e.bytes_on_disk > 0
    assert e.preview_channels == CHANNELS
    assert e.images == 16 and e.ended_at is not None


def test_metadata_from_params_reads_the_params_and_the_yaml(params, folder):
    md = metadata_from_params(params, str(folder))
    assert md == {
        "experiment_id": params.experiment_ID,
        "widget_type": "wellplate",
        "xy_mode": "Select Wells",
        "objective": "20x",
        "magnification": 20.0,
        "pixel_size_um": 0.325,
        "wellplate_format": "96 well plate",
        "channels": CHANNELS,
        "num_regions": 2,
        "num_fovs": 8,
        "nz": 3,
        "dz_um": pytest.approx(1.5),
        "nt": 2,
        "dt_s": 30.0,
        "z_stacking_config": "FROM BOTTOM",
        "use_piezo": False,
        "contrast_af": False,
        "laser_af": True,
        "laser_af_per_stack": False,
        "start_time": 1762596000.0,
        "image_format": None,
        "protocol": None,
    }


def test_metadata_from_params_without_a_readable_yaml_leaves_the_yaml_fields_empty(params, saving_root):
    folder = write_acquisition(saving_root, "noyaml", with_yaml=False)
    md = metadata_from_params(params, str(folder))
    assert md["widget_type"] is None and md["objective"] is None and md["wellplate_format"] is None
    assert md["channels"] == CHANNELS and md["nz"] == 3
    (folder / "acquisition.yaml").write_text("acquisition: [unclosed")
    assert metadata_from_params(params, str(folder))["pixel_size_um"] is None


def test_make_preview_is_idempotent_per_entry_unless_replacing(store, recorder, executor, params, folder):
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    recorder.finished(entry_id, "completed", images=96, expected_images=96)
    recorder.make_preview(entry_id, str(folder)).result(timeout=10)
    png = store.previews_dir / f"{entry_id}.png"
    first_mtime = png.stat().st_mtime_ns

    assert recorder.make_preview(entry_id, str(folder)) is None
    assert len(events_of(store, "preview", entry_id)) == 1

    # A fresh recorder (e.g. after a restart) also sees the preview already in the store.
    other = make_recorder(store, executor)
    assert other.make_preview(entry_id, str(folder)) is None

    time.sleep(0.05)
    recorder.make_preview(entry_id, str(folder), replace=True).result(timeout=10)
    assert len(events_of(store, "preview", entry_id)) == 2
    assert png.stat().st_mtime_ns != first_mtime
    assert store.load()[0].preview == f"{entry_id}.png"


def test_make_preview_does_not_repeat_a_job_that_finished_while_the_call_was_checking(store, recorder, params, folder):
    """The store pre-filter runs outside the lock: a job that completes between it and the lock must not run again."""
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    release = threading.Event()
    real_writer = recorder_module.write_preview_png

    def blocking_writer(folder, out_path):
        assert release.wait(10)
        return real_writer(folder, out_path)

    with patch.object(recorder_module, "write_preview_png", side_effect=blocking_writer):
        first = recorder.make_preview(entry_id, str(folder))
        assert first is not None

        def stale_pre_filter(_entry_id):
            # The interleaving: the scan read "no preview", then the pending job finished before the lock was taken.
            release.set()
            first.result(timeout=10)
            return False

        with patch.object(recorder, "_has_preview", side_effect=stale_pre_filter):
            assert recorder.make_preview(entry_id, str(folder)) is None
        assert len(events_of(store, "preview", entry_id)) == 1

        # replace=True still regenerates.
        recorder.make_preview(entry_id, str(folder), replace=True).result(timeout=10)
    assert len(events_of(store, "preview", entry_id)) == 2


def test_a_replacement_requested_during_a_pending_job_runs_after_it(store, recorder, params, saving_root):
    folder_a = write_acquisition(saving_root, "a")
    folder_b = write_acquisition(saving_root, "b")
    entry_id = recorder.started(params, str(folder_a), snap=False, skip_saving=False, protocol=None)
    release = threading.Event()
    called_with = []

    def blocking_writer(folder, out_path, max_side=512):
        called_with.append(folder)
        if len(called_with) == 1:
            assert release.wait(10)
        with open(out_path, "wb") as f:
            f.write(folder.encode("utf-8"))
        return [os.path.basename(folder)]

    with patch.object(recorder_module, "write_preview_png", side_effect=blocking_writer):
        first = recorder.make_preview(entry_id, str(folder_a))
        assert first is not None
        assert wait_for(lambda: called_with)  # the job is running
        assert recorder.make_preview(entry_id, str(folder_b), replace=True) is None
        assert recorder.make_preview(entry_id, str(folder_b)) is None  # still pending
        release.set()
        first.result(timeout=10)
        assert wait_for(lambda: len(events_of(store, "preview", entry_id)) == 2)

    assert called_with == [str(folder_a), str(folder_b)]
    previews = events_of(store, "preview", entry_id)
    assert [p["channels"] for p in previews] == [["a"], ["b"]]
    assert store.load()[0].preview_channels == ["b"]
    assert (store.previews_dir / f"{entry_id}.png").read_bytes() == str(folder_b).encode("utf-8")


def test_finished_without_a_preview_source_records_size_but_no_file(store, recorder, params, saving_root):
    folder = saving_root / "metadata_only"
    folder.mkdir()
    (folder / "acquisition parameters.json").write_text("{}")
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    recorder.finished(entry_id, "completed", images=0, expected_images=96)
    recorder.make_preview(entry_id, str(folder)).result(timeout=10)

    [ev] = events_of(store, "preview", entry_id)
    assert ev.get("file") is None and ev["channels"] == [] and ev["bytes_on_disk"] >= 2
    [e] = store.load()
    assert e.preview is None and e.preview_channels == [] and e.bytes_on_disk == ev["bytes_on_disk"]


def test_preview_failure_is_contained(store, recorder, params, folder):
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    recorder.finished(entry_id, "completed", images=96, expected_images=96)
    with patch.object(recorder_module, "write_preview_png", side_effect=RuntimeError("boom")):
        future = recorder.make_preview(entry_id, str(folder))
        future.result(timeout=10)  # does not raise
    assert events_of(store, "preview", entry_id) == []
    [e] = store.load()
    assert e.outcome == "complete" and e.preview is None
    # The failed job is not "pending" any more: a later attempt runs.
    recorder.make_preview(entry_id, str(folder)).result(timeout=10)
    assert len(events_of(store, "preview", entry_id)) == 1


# ---------------------------------------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------------------------------------


def test_user_abort_is_aborted_with_the_fraction_done(store, recorder, params, saving_root):
    a = write_acquisition(saving_root, "a")
    b = write_acquisition(saving_root, "b")
    id_a = recorder.started(params, str(a), snap=False, skip_saving=False, protocol=None)
    id_b = recorder.started(params, str(b), snap=False, skip_saving=False, protocol=None)
    recorder.finished(id_a, "user_abort", images=4, expected_images=16)
    recorder.finished(id_b, "user_abort", images=0, expected_images=16)
    assert (store.entry(id_a).outcome, store.entry(id_a).fraction_done) == ("aborted", 0.25)
    assert (store.entry(id_b).outcome, store.entry(id_b).fraction_done) == ("aborted", 0.0)
    assert store.entry(id_a).reason == "user_abort"


def test_completed_with_errors_is_complete_and_fraction_is_capped(store, recorder, params, folder):
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    recorder.finished(entry_id, "completed_with_errors", images=20, expected_images=16)
    e = store.entry(entry_id)
    assert e.outcome == "complete" and e.fraction_done == 1.0 and e.reason == "completed_with_errors"


def test_error_and_failed_to_start_are_failed(store, recorder, params, saving_root):
    a = write_acquisition(saving_root, "a")
    b = write_acquisition(saving_root, "b")
    id_a = recorder.started(params, str(a), snap=False, skip_saving=False, protocol=None)
    id_b = recorder.started(params, str(b), snap=False, skip_saving=False, protocol=None)
    recorder.finished(id_a, "error", images=3, expected_images=12)
    recorder.finished(id_b, "failed_to_start", images=0, expected_images=0)
    ea, eb = store.entry(id_a), store.entry(id_b)
    assert (ea.outcome, ea.reason, ea.fraction_done) == ("failed", "error", 0.25)
    assert (eb.outcome, eb.reason, eb.fraction_done) == ("failed", "failed_to_start", None)


def test_finished_for_no_entry_is_a_no_op(store, recorder):
    recorder.finished(None, "completed", images=1, expected_images=1)
    assert store.events() == []


def test_snaps_are_skipped_unless_enabled(store, executor, params, saving_root):
    a = write_acquisition(saving_root, "a")
    b = write_acquisition(saving_root, "b")
    off = make_recorder(store, executor, snaps_enabled=lambda: False)
    assert off.started(params, str(a), snap=True, skip_saving=False, protocol=None) is None
    assert store.events() == []
    on = make_recorder(store, executor, snaps_enabled=lambda: True)
    entry_id = on.started(params, str(b), snap=True, skip_saving=False, protocol=None)
    assert store.entry(entry_id).kind == "snap"


def test_snaps_default_to_the_preference(store, executor, params, folder, monkeypatch):
    rec = make_recorder(store, executor)
    monkeypatch.delattr(control._def, "RECORD_SNAPS_IN_HISTORY", raising=False)
    assert rec.started(params, str(folder), snap=True, skip_saving=False, protocol=None) is None
    monkeypatch.setattr(control._def, "RECORD_SNAPS_IN_HISTORY", True, raising=False)
    assert rec.started(params, str(folder), snap=True, skip_saving=False, protocol=None) is not None


def test_skip_saving_runs_are_not_recorded(store, recorder, params, folder):
    assert recorder.started(params, str(folder), snap=False, skip_saving=True, protocol=None) is None
    recorder.failed_start(str(folder), "failed_to_start", skip_saving=True)
    assert store.events() == []


def test_failed_start_records_a_failed_entry_only_when_the_folder_exists(store, executor, saving_root, folder):
    rec = make_recorder(store, executor, snaps_enabled=lambda: False)
    rec.failed_start(None, "failed_to_start")
    rec.failed_start(str(saving_root / "never_made"), "failed_to_start")
    assert store.events() == []

    rec.failed_start(str(folder), "failed_to_start", snap=True)  # snaps off
    assert store.events() == []

    rec.failed_start(str(folder), "failed_to_start")
    [e] = store.load()
    assert e.id == entry_id_for(str(folder)) and e.kind == "run"
    assert (e.outcome, e.reason, e.fraction_done, e.images) == ("failed", "failed_to_start", 0.0, 0)
    assert e.metadata["channels"] == CHANNELS and e.metadata["objective"] == "20x"
    assert e.metadata["software_version"] == "v-test" and e.pid == os.getpid() and e.machine == "scope-1"

    run_dir = saving_root / "protocol_run"
    run_dir.mkdir()
    round_folder = write_acquisition(run_dir, "round_1")
    protocol = {"protocol": "FISH", "run_name": "protocol_run", "round": 1, "step": 1, "attempt": 1}
    rec.failed_start(str(round_folder), "failed_to_start", protocol=protocol)
    assert store.entry(entry_id_for(str(round_folder))).parent == entry_id_for(str(run_dir))


def test_failed_start_never_overwrites_an_entry_already_recorded_for_the_folder(store, recorder, params, folder):
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    recorder.finished(entry_id, "completed", images=96, expected_images=96)
    recorder.failed_start(str(folder), "failed_to_start")  # a refused restart in the same experiment folder
    [e] = store.load()
    assert e.outcome == "complete"
    assert len(events_of(store, "started", entry_id)) == 1


def test_protocol_round_carries_its_parent(store, recorder, params, saving_root):
    run_dir = saving_root / "protocol_run"
    run_dir.mkdir()
    folder = write_acquisition(run_dir, "round_1")
    protocol = {"protocol": "FISH", "run_name": "protocol_run", "round": 1, "step": 1, "attempt": 1}
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=protocol)
    recorder.finished(entry_id, "completed", images=96, expected_images=96)

    [parent] = store.load()
    assert parent.id == entry_id_for(str(run_dir)) and parent.kind == "protocol_run"
    [round_entry] = parent.rounds
    assert round_entry.id == entry_id and round_entry.parent == parent.id and round_entry.protocol == protocol


def test_protocol_started_and_finished_drive_the_parent_outcome(store, recorder, params, saving_root):
    run_dir = saving_root / "protocol_run"
    run_dir.mkdir()
    recorder.protocol_started(str(run_dir), "protocol_run", "FISH")
    [p] = store.load()
    assert p.kind == "protocol_run" and p.outcome == "running" and p.id == entry_id_for(str(run_dir))
    assert p.pid == os.getpid() and p.machine == "scope-1" and p.name == "protocol_run"

    protocol = {"protocol": "FISH", "run_name": "protocol_run", "round": 1, "step": 1, "attempt": 1}
    for name in ("round_1", "round_2"):
        folder = write_acquisition(run_dir, name)
        rid = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=protocol)
        recorder.finished(rid, "completed", images=96, expected_images=96)
    [p] = store.load()
    assert p.outcome == "running" and len(p.rounds) == 2

    recorder.protocol_finished(str(run_dir), "stopped")
    [p] = store.load()
    assert p.outcome == "aborted" and p.reason == "stopped" and p.ended_at is not None


def test_map_protocol_outcome():
    assert map_protocol_outcome("finished") == "complete"
    assert map_protocol_outcome("stopped") == "aborted"
    assert map_protocol_outcome("failed") == "failed"


# ---------------------------------------------------------------------------------------------------------
# Orphan reconciliation
# ---------------------------------------------------------------------------------------------------------


def running_event(store, entry_id, *, pid, pid_started_at, machine, type_="started"):
    event = {"type": type_, "id": entry_id, "name": entry_id, "folder": f"/data/{entry_id}", "metadata": {}}
    event.update(pid=pid, pid_started_at=pid_started_at, machine=machine)
    if type_ == "started":
        event.update(kind="run", parent=None, protocol=None)
    store.append(event)


def test_reconcile_orphans_closes_dead_runs_of_this_machine_only(store, recorder):
    running_event(store, "dead", pid=111, pid_started_at=1000.0, machine="scope-1")
    running_event(store, "alive", pid=222, pid_started_at=2000.0, machine="scope-1")
    running_event(store, "elsewhere", pid=333, pid_started_at=3000.0, machine="scope-2")
    running_event(store, "proto", pid=444, pid_started_at=4000.0, machine="scope-1", type_="protocol_started")

    closed = recorder.reconcile_orphans(
        pid_alive=lambda pid: pid == 222, process_started_at=lambda pid: {222: 2000.5}.get(pid)
    )

    assert closed == 2
    dead = store.entry("dead")
    assert (dead.outcome, dead.reason, dead.fraction_done) == ("failed", "interrupted", None)
    assert store.entry("alive").outcome == "running"
    assert store.entry("elsewhere").outcome == "running"
    proto = store.entry("proto")
    assert (proto.outcome, proto.reason) == ("failed", "interrupted")
    assert [e["id"] for e in events_of(store, "protocol_finished")] == ["proto"]
    assert recorder.reconcile_orphans(pid_alive=lambda pid: pid == 222, process_started_at=lambda pid: 2000.5) == 0


def test_reconcile_orphans_closes_running_rounds_inside_a_protocol_run(store, recorder):
    store.append(
        {
            "type": "started",
            "id": "round",
            "kind": "run",
            "name": "round",
            "folder": "/data/run/round",
            "metadata": {},
            "parent": "run-1",
            "protocol": {"protocol": "FISH", "run_name": "run"},
            "pid": 111,
            "pid_started_at": 1000.0,
            "machine": "scope-1",
        }
    )
    assert recorder.reconcile_orphans(pid_alive=lambda pid: False, process_started_at=lambda pid: None) == 1
    assert store.entry("round").outcome == "failed"


def test_reconcile_orphans_treats_a_reused_pid_as_dead(store, recorder):
    running_event(store, "reused", pid=111, pid_started_at=1000.0, machine="scope-1")
    running_event(store, "old", pid=222, pid_started_at=None, machine="scope-1")

    closed = recorder.reconcile_orphans(pid_alive=lambda pid: True, process_started_at=lambda pid: 1000.0 + 3 * 3600)

    assert closed == 1
    assert (store.entry("reused").outcome, store.entry("reused").reason) == ("failed", "interrupted")
    assert store.entry("old").outcome == "running"


def test_reconcile_orphans_handles_a_vanished_or_unreadable_process(store, recorder):
    running_event(store, "vanished", pid=111, pid_started_at=1000.0, machine="scope-1")
    running_event(store, "denied", pid=222, pid_started_at=2000.0, machine="scope-1")
    running_event(store, "nopid", pid=None, pid_started_at=None, machine="scope-1")

    def started_at(pid):
        if pid == 222:
            raise psutil.AccessDenied(pid)
        return None

    closed = recorder.reconcile_orphans(pid_alive=lambda pid: True, process_started_at=started_at)

    assert closed == 3
    for entry_id in ("vanished", "denied", "nopid"):
        assert (store.entry(entry_id).outcome, store.entry(entry_id).reason) == ("failed", "interrupted")


def test_reconcile_orphans_survives_pid_alive_raising(store, recorder):
    running_event(store, "x", pid=111, pid_started_at=1000.0, machine="scope-1")

    def boom(pid):
        raise psutil.Error("nope")

    assert recorder.reconcile_orphans(pid_alive=boom, process_started_at=lambda pid: 1000.0) == 1
    assert store.entry("x").outcome == "failed"


def test_reconcile_orphans_default_lookups_keep_this_process_running(store, recorder, params, folder):
    entry_id = recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    assert recorder.reconcile_orphans() == 0
    assert store.entry(entry_id).outcome == "running"


# ---------------------------------------------------------------------------------------------------------
# Failures and the null recorder
# ---------------------------------------------------------------------------------------------------------


def test_store_failure_never_raises_and_logs_once(store, recorder, params, folder, caplog):
    caplog.set_level(logging.DEBUG)
    with patch.object(store, "append", side_effect=OSError("disk full")):
        assert recorder.started(params, str(folder), snap=False, skip_saving=False, protocol=None) is None
        recorder.finished(entry_id_for(str(folder)), "completed", images=1, expected_images=1)
        recorder.failed_start(str(folder), "failed_to_start")
        recorder.protocol_started(str(folder), "run", "FISH")
        recorder.protocol_finished(str(folder), "finished")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "history" in warnings[0].getMessage().lower()


def test_shutdown_stops_the_executor(store, params, folder):
    ex = ThreadPoolExecutor(1)
    rec = make_recorder(store, ex)
    rec.shutdown()
    entry_id = rec.started(params, str(folder), snap=False, skip_saving=False, protocol=None)
    assert rec.make_preview(entry_id, str(folder)) is None  # no executor left: contained, not raised


def test_null_recorder_does_nothing():
    rec = NullAcquisitionHistoryRecorder()
    assert rec.started(None, "x", snap=False, skip_saving=False, protocol=None) is None
    rec.finished("x", "completed", images=1, expected_images=1)
    assert rec.make_preview("x", "x") is None
    assert rec.make_preview("x", "x", replace=True) is None
    rec.failed_start("x", "failed_to_start", snap=False, skip_saving=False, protocol=None)
    rec.protocol_started("x", "x", "x")
    rec.protocol_finished("x", "finished")
    assert rec.reconcile_orphans() == 0
    rec.shutdown()


# ---------------------------------------------------------------------------------------------------------
# Controller hooks (simulated hardware)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def sim_controller(tmp_path, monkeypatch):
    import control.microscope
    import tests.control.test_stubs as ts
    from tests.control.test_MultiPointController import CountingTracker

    monkeypatch.setenv("SQUID_ACQUISITION_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("SQUID_WATCHDOG_STATE_DIR", str(tmp_path / "watchdog"))
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    scope = control.microscope.Microscope.build_from_global_config(True)
    executor = ThreadPoolExecutor(1)
    try:
        tt = CountingTracker()
        mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
        stage_config = mpc.stage.get_config()
        mpc.scanCoordinates.clear_regions()
        mpc.scanCoordinates.add_single_fov_region(
            "region_1",
            center_x=stage_config.X_AXIS.MIN_POSITION + 1.0,
            center_y=stage_config.Y_AXIS.MIN_POSITION + 1.0,
            center_z=stage_config.Z_AXIS.MIN_POSITION + 1.0,
        )
        names = [m.name for m in mpc.liveController.get_channels(scope.objective_store.current_objective)]
        mpc.set_selected_configurations(selected_configurations_name=names[:1])
        os.makedirs(str(tmp_path / "saved"))
        mpc.set_base_path(str(tmp_path / "saved"))
        mpc.start_new_experiment("history test")
        store = HistoryStore(tmp_path / "history")
        mpc.history_recorder = AcquisitionHistoryRecorder(
            store, preview_executor=executor, software_version="v-test", machine="scope-1"
        )
        yield SimpleNamespace(scope=scope, mpc=mpc, tt=tt, store=store)
    finally:
        executor.shutdown(wait=True)
        scope.close()


def test_simulated_acquisition_records_started_and_finished(sim_controller):
    mpc, tt, store = sim_controller.mpc, sim_controller.tt, sim_controller.store
    assert mpc.history_entry_id is None

    assert mpc.run_acquisition() is True
    experiment_path = os.path.join(mpc.base_path, mpc.experiment_ID)
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    assert mpc.history_entry_id is None
    [e] = store.load()
    assert e.outcome == "complete" and e.images > 0 and e.reason == "completed"
    assert e.id == entry_id_for(experiment_path) and e.kind == "run"
    assert e.metadata["expected_images"] == mpc.get_acquisition_image_count() == e.images
    assert e.fraction_done == 1.0


def test_history_entry_id_is_set_while_the_acquisition_runs(sim_controller):
    mpc, tt = sim_controller.mpc, sim_controller.tt
    seen = []
    tt_start = mpc.callbacks.signal_acquisition_start

    def on_start(params):
        seen.append(mpc.history_entry_id)
        tt_start(params)

    mpc.callbacks = dataclasses.replace(mpc.callbacks, signal_acquisition_start=on_start)

    assert mpc.run_acquisition() is True
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    assert seen == [entry_id_for(os.path.join(mpc.base_path, mpc.experiment_ID))]
    assert mpc.history_entry_id is None


def test_an_early_exit_before_the_watchdog_block_is_recorded_as_failed(sim_controller):
    mpc, tt, store = sim_controller.mpc, sim_controller.tt, sim_controller.store

    with patch.object(mpc.microcontroller, "wait_till_operation_is_completed", side_effect=TimeoutError("busy")):
        assert mpc.run_acquisition() is False

    assert tt.finished_event.wait(5)
    [e] = store.load()
    assert e.id == entry_id_for(os.path.join(mpc.base_path, mpc.experiment_ID))
    assert (e.outcome, e.reason, e.fraction_done) == ("failed", "failed_to_start", 0.0)
    assert mpc.history_entry_id is None


def test_consecutive_failed_starts_each_get_their_own_entry(sim_controller):
    import control.core.multi_point_controller as mpc_module

    mpc, store = sim_controller.mpc, sim_controller.store
    folder_a = os.path.join(mpc.base_path, mpc.experiment_ID)

    def broken_worker(*args, **kwargs):
        raise RuntimeError("worker could not be built")

    with patch.object(mpc_module, "MultiPointWorker", side_effect=broken_worker):
        with pytest.raises(RuntimeError, match="worker could not be built"):
            mpc.run_acquisition()
    id_a = entry_id_for(folder_a)
    assert len(events_of(store, "started", id_a)) == 1  # `started` ran before the worker failed
    assert (store.entry(id_a).outcome, store.entry(id_a).reason) == ("failed", "failed_to_start")
    assert mpc.history_entry_id is None

    mpc.start_new_experiment("history test second")
    folder_b = os.path.join(mpc.base_path, mpc.experiment_ID)
    assert folder_b != folder_a
    mpc.validate_acquisition_settings = lambda: False
    assert mpc.run_acquisition() is False

    id_b = entry_id_for(folder_b)
    assert (store.entry(id_b).outcome, store.entry(id_b).reason) == ("failed", "failed_to_start")
    assert len(events_of(store, "finished", id_a)) == 1  # the second refusal did not finish A again
    assert len(events_of(store, "finished", id_b)) == 1
    assert mpc.history_entry_id is None
