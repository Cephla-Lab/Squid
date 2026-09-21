"""Simulated acquisitions writing a transfer manifest (large acquisition mode) — and none when the mode is off."""

import concurrent.futures
import dataclasses
import os
import threading
import time
from pathlib import Path

import pytest

import control._def
import control.microscope
import control.core.multi_point_worker as mpw
from control._def import FileSavingOption
from control.core.transfer_manifest import MANIFEST_FILE_NAME, read_manifest
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker, select_some_configs


def _tp_dir(t: int) -> str:
    """Timepoint folder name as the worker builds it (FILE_ID_PADDING may be 0 -> no padding)."""
    return f"{t:0{control._def.FILE_ID_PADDING}}"


def _one_fov(mpc):
    stage = mpc.stage
    cfg = stage.get_config()
    mpc.scanCoordinates.add_single_fov_region(
        "region_1",
        center_x=cfg.X_AXIS.MIN_POSITION + 1.0,
        center_y=cfg.Y_AXIS.MIN_POSITION + 1.0,
        center_z=cfg.Z_AXIS.MIN_POSITION + 1.0,
    )


def _run(tmp_path, monkeypatch, mode_on: bool, saving_option: FileSavingOption, nt: int = 2, writer=None):
    control._def.MERGE_CHANNELS = False
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", saving_option)
    monkeypatch.setattr(mpw, "FILE_SAVING_OPTION", saving_option)  # the worker binds the name at import
    monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", 1000.0)
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", 0.0)

    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    callbacks = tt.get_callbacks()
    holder = {}
    if writer is not None:
        # Stand-in for the GUI's mosaic view: answers every timepoint_finished from another thread.
        callbacks = dataclasses.replace(callbacks, signal_timepoint_finished=lambda t: writer(holder["mpc"], t))
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=callbacks)
    holder["mpc"] = mpc
    if writer is not None:
        mpc.attach_timepoint_output_writer()
    mpc.set_base_path(str(tmp_path))
    mpc.start_new_experiment("manifest_run", add_timestamp=False)
    _one_fov(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_Nt(nt)
    mpc.set_deltat(0.0)
    mpc.set_large_acquisition_mode(mode_on)

    mpc.run_acquisition()
    assert tt.started_event.wait(10)
    assert tt.finished_event.wait(120)
    mpc.thread.join(10)
    assert mpc.last_end_reason == "completed"
    return tmp_path / "manifest_run", tt, mpc


def test_mode_off_writes_no_manifest(tmp_path, monkeypatch):
    exp, tt, mpc = _run(tmp_path, monkeypatch, mode_on=False, saving_option=FileSavingOption.INDIVIDUAL_IMAGES)
    assert not (exp / MANIFEST_FILE_NAME).exists()
    assert tt.image_count == mpc.get_acquisition_image_count() > 0


def test_individual_images_manifest_lists_every_image_and_timepoint(tmp_path, monkeypatch):
    exp, tt, mpc = _run(tmp_path, monkeypatch, mode_on=True, saving_option=FileSavingOption.INDIVIDUAL_IMAGES, nt=2)
    records = read_manifest(exp / MANIFEST_FILE_NAME)
    events = [r["event"] for r in records]

    assert events[0] == "start" and records[0]["format"] == "INDIVIDUAL_IMAGES" and records[0]["nt"] == 2
    assert events[-1] == "end" and records[-1]["reason"] == "completed"
    assert events.count("timepoint_done") == 2

    completes = [r for r in records if r["event"] == "complete"]
    assert all(r["kind"] == "file" for r in completes)
    listed = {r["path"] for r in completes}
    for r in completes:
        p = exp / Path(*r["path"].split("/"))
        assert p.is_file(), r
        assert r["bytes"] == os.path.getsize(p), r
        assert not os.path.isabs(r["path"]) and "\\" not in r["path"]

    on_disk_images = {
        str(p.relative_to(exp).as_posix())
        for t in range(2)
        for p in (exp / _tp_dir(t)).iterdir()
        if p.suffix in (".tiff", ".tif", ".png", ".bmp")
    }
    assert on_disk_images, "the run must have saved images"
    assert on_disk_images <= listed, on_disk_images - listed
    assert len(on_disk_images) == tt.image_count
    assert {f"{_tp_dir(0)}/coordinates.csv", f"{_tp_dir(1)}/coordinates.csv"} <= listed
    # Never listed mid-run: run-level files.
    for never in ("acquisition parameters.json", "configurations.xml", "coordinates.csv", MANIFEST_FILE_NAME):
        assert never not in listed
    # Ordering: each timepoint's images precede its timepoint_done.
    done_idx = [i for i, e in enumerate(events) if e == "timepoint_done"]
    t0_complete_idx = [i for i, r in enumerate(records) if r["event"] == "complete" and r.get("t") == 0]
    assert max(t0_complete_idx) < done_idx[0]


def test_zarr_manifest_lists_each_timepoints_chunk_files(tmp_path, monkeypatch):
    pytest.importorskip("tensorstore")
    exp, tt, mpc = _run(tmp_path, monkeypatch, mode_on=True, saving_option=FileSavingOption.ZARR_V3, nt=2)
    records = read_manifest(exp / MANIFEST_FILE_NAME)
    completes = [r for r in records if r["event"] == "complete"]
    chunk_files = [r for r in completes if "/c/" in r["path"]]
    assert chunk_files, "chunk files must be listed"
    assert all(r["kind"] == "file" for r in chunk_files), "chunk folders are listed file by file (the inventory)"
    timepoint_dirs = {r["path"].split("/c/")[0] + "/c/" + r["path"].split("/c/")[1].split("/")[0] for r in chunk_files}
    assert len(timepoint_dirs) == 2, timepoint_dirs  # one FOV x two timepoints
    assert {d.rsplit("/", 1)[1] for d in timepoint_dirs} == {"0", "1"}
    for r in chunk_files:
        p = exp / Path(*r["path"].split("/"))
        assert p.is_file() and os.path.getsize(p) == r["bytes"], r
    listed = {r["path"] for r in completes}
    assert not any(p.endswith("zarr.json") for p in listed), "store metadata is only movable after end"
    assert records[-1]["event"] == "end"


def test_outputs_written_outside_the_worker_are_listed_per_timepoint_before_timepoint_done(tmp_path, monkeypatch):
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def mosaic_like_writer(mpc, t):
        def reply():  # like the GUI thread handling the queued signal a little later
            time.sleep(0.2)
            out = Path(mpc.base_path) / mpc.experiment_ID / _tp_dir(t) / "mosaic_view"

            def save():
                time.sleep(0.2)
                out.mkdir(parents=True, exist_ok=True)
                (out / "mosaic.yaml").write_text(f"t: {t}\n")

            mpc.register_pending_output(t, pool.submit(save), str(out))

        threading.Thread(target=reply, daemon=True).start()

    exp, tt, mpc = _run(
        tmp_path,
        monkeypatch,
        mode_on=True,
        saving_option=FileSavingOption.INDIVIDUAL_IMAGES,
        nt=3,
        writer=mosaic_like_writer,
    )
    records = read_manifest(exp / MANIFEST_FILE_NAME)
    for t in range(3):
        mosaic = [i for i, r in enumerate(records) if r.get("path") == f"{_tp_dir(t)}/mosaic_view/mosaic.yaml"]
        assert len(mosaic) == 1, (t, mosaic)
        assert records[mosaic[0]]["t"] == t, "listed under the timepoint it belongs to"
        done = [i for i, r in enumerate(records) if r["event"] == "timepoint_done" and r["t"] == t]
        assert len(done) == 1 and mosaic[0] < done[0], "timepoint_done follows the timepoint's mosaic files"
        assert not any(
            r["event"] == "complete" and r["path"].startswith(f"{_tp_dir(t)}/") for r in records[done[0] + 1 :]
        ), "nothing of a timepoint is listed after its timepoint_done"


def test_a_silent_writer_never_blocks_the_run_but_holds_back_the_markers_until_it_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(mpw.MultiPointWorker, "_TIMEPOINT_OUTPUTS_TIMEOUT_S", 0.3)
    monkeypatch.setattr(mpw.MultiPointWorker, "_PENDING_OUTPUTS_TIMEOUT_S", 0.3)
    exp, tt, mpc = _run(
        tmp_path,
        monkeypatch,
        mode_on=True,
        saving_option=FileSavingOption.INDIVIDUAL_IMAGES,
        nt=2,
        writer=lambda m, t: None,
    )
    # The run itself completed; what is withheld is every claim that outputs are finished, so a mover
    # keeps to the listed files and never sweeps the rest while a save might still be coming.
    events = [r["event"] for r in read_manifest(exp / MANIFEST_FILE_NAME)]
    assert "timepoint_done" not in events and "end" not in events
    assert events.count("complete") >= tt.image_count

    for t in range(2):  # the writer finally answers
        mpc.report_no_timepoint_output(t)
    assert read_manifest(exp / MANIFEST_FILE_NAME)[-1]["event"] == "end"


def test_mode_off_never_expects_or_waits_for_outside_writers(tmp_path, monkeypatch):
    monkeypatch.setattr(mpw.MultiPointWorker, "_TIMEPOINT_OUTPUTS_TIMEOUT_S", 60.0)
    started = time.monotonic()
    exp, tt, mpc = _run(
        tmp_path,
        monkeypatch,
        mode_on=False,
        saving_option=FileSavingOption.INDIVIDUAL_IMAGES,
        nt=2,
        writer=lambda m, t: None,
    )
    assert not (exp / MANIFEST_FILE_NAME).exists()
    assert time.monotonic() - started < 45, "a silent writer must not slow a mode-off run"
    assert mpc._pending_outputs.wait(None, 0.0).complete


def test_end_waits_for_a_writer_that_outlasts_the_run_and_lists_its_files_first(tmp_path, monkeypatch):
    """A mosaic save still running when the run ends: end must not appear while it writes (the mover would
    sweep a half-written file), and must appear, after the file is listed, once it has finished."""
    monkeypatch.setattr(mpw.MultiPointWorker, "_TIMEPOINT_OUTPUTS_TIMEOUT_S", 0.2)
    monkeypatch.setattr(mpw.MultiPointWorker, "_PENDING_OUTPUTS_TIMEOUT_S", 0.2)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    futures = []

    def slow_writer(mpc, t):
        out = Path(mpc.base_path) / mpc.experiment_ID / _tp_dir(t) / "mosaic_view"

        def save():
            assert release.wait(60)
            out.mkdir(parents=True, exist_ok=True)
            (out / "mosaic.yaml").write_text(f"t: {t}\n")

        future = pool.submit(save)
        futures.append(future)
        mpc.register_pending_output(t, future, str(out))

    exp, tt, mpc = _run(
        tmp_path, monkeypatch, mode_on=True, saving_option=FileSavingOption.INDIVIDUAL_IMAGES, nt=1, writer=slow_writer
    )
    events = [r["event"] for r in read_manifest(exp / MANIFEST_FILE_NAME)]
    assert "end" not in events and "timepoint_done" not in events, "the writer is still running"

    release.set()
    concurrent.futures.wait(futures, timeout=30)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        records = read_manifest(exp / MANIFEST_FILE_NAME)
        if records and records[-1]["event"] == "end":
            break
        time.sleep(0.05)
    paths = [r.get("path") for r in records]
    assert records[-1] == {**records[-1], "event": "end", "reason": "completed"}
    assert paths.index(f"{_tp_dir(0)}/mosaic_view/mosaic.yaml") < len(records) - 1, "listed before the end record"
