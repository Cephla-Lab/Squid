"""End-to-end offload: a simulated run on a virtual disk too small for the whole acquisition pauses on
low space, tools/upload_acquisition.py (follow + move) drains completed files to a "NAS" folder, the run
resumes and completes, and the destination holds a verified, complete copy."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import control._def
import control.microscope
import control.core.multi_point_worker as mpw
from control._def import FileSavingOption
from control.core.transfer_manifest import MANIFEST_FILE_NAME, read_manifest
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import select_some_configs
from tests.control.test_MultiPointController_pause import PauseTracker
from tests.control.test_transfer_manifest_integration import _one_fov, _tp_dir

TOOL = Path(__file__).resolve().parents[2] / "tools" / "upload_acquisition.py"


def _start_tool(exp: Path, dest: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(TOOL),
            str(exp),
            str(dest),
            "--follow",
            "--mode",
            "move",
            "--poll-s",
            "0.3",
            "--quiesce-s",
            "1",
        ],
        cwd=str(TOOL.parents[1]),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


PREFILL_MB = 120  # unlisted ballast inside the experiment folder: only the mover's work frees space
CAPACITY_MB = 235  # headroom above the ballast: 115 MB. Frames are ~17 MB (8-bit sim camera), so the guard
#                    needs 2 FOVs = 68 MB to run and 102 MB to resume; two 34 MB timepoints exhaust it.


def _run_with_mover(tmp_path, monkeypatch, saving_option: FileSavingOption, nt: int):
    control._def.MERGE_CHANNELS = False
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", saving_option)
    monkeypatch.setattr(mpw, "FILE_SAVING_OPTION", saving_option)
    monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", 0.0)
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 0.2)
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", CAPACITY_MB * 2**20 / 2**30)

    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = PauseTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
    base = tmp_path / "local"
    base.mkdir()
    dest = tmp_path / "nas"
    dest.mkdir()
    mpc.set_base_path(str(base))
    mpc.start_new_experiment("offload_run", add_timestamp=False)
    exp = base / "offload_run"
    with open(exp / "ballast.bin", "wb") as f:
        f.truncate(PREFILL_MB * 2**20)
    _one_fov(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_Nt(nt)
    mpc.set_deltat(0.0)
    mpc.set_large_acquisition_mode(True)

    tool = None
    try:
        mpc.run_acquisition()
        assert tt.started_event.wait(10)
        # The virtual disk fills after a couple of timepoints; only then does the mover start, so the
        # resume is unambiguously the mover's doing.
        assert tt.paused_event.wait(120), "the run must pause for disk space before the mover starts"
        assert not tt.finished_event.is_set(), "the run finished before the virtual disk filled: sizing is off"
        state, disk = tt.paused_states[0]
        assert state.reasons == ("disk_space",) and disk is not None and disk.holding

        tool = _start_tool(exp, dest)
        assert tt.resumed_event.wait(120), "the mover must free enough space for the run to resume"
        assert tt.finished_event.wait(240)
        mpc.thread.join(10)
        try:
            out, _ = tool.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            tool.kill()
            out, _ = tool.communicate()
            pytest.fail(f"upload tool did not exit after the end record:\n{out[-3000:]}")
    finally:
        if tool is not None and tool.poll() is None:
            tool.kill()
    assert tool.returncode == 0, out[-3000:]
    return exp, dest / "offload_run", tt, mpc, out


def _assert_verified(dest_exp: Path):
    result = subprocess.run(
        [sys.executable, str(TOOL), "verify", str(dest_exp), str(dest_exp.parent)],
        cwd=str(TOOL.parents[1]),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_individual_tiff_run_pauses_on_low_space_and_completes_while_the_mover_drains(tmp_path, monkeypatch):
    exp, dest_exp, tt, mpc, out = _run_with_mover(tmp_path, monkeypatch, FileSavingOption.INDIVIDUAL_IMAGES, nt=6)

    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count()
    assert tt.paused_states, "the run must have paused for disk space at least once"
    assert all(s.reasons == ("disk_space",) for s, _ in tt.paused_states)
    assert tt.resumed_seconds, "and resumed once the mover freed space"

    records = read_manifest(dest_exp / MANIFEST_FILE_NAME)
    assert records[-1]["event"] == "end" and records[-1]["reason"] == "completed"
    listed = [r for r in records if r["event"] == "complete"]
    for r in listed:
        p = dest_exp / Path(*r["path"].split("/"))
        assert p.is_file(), f"{r['path']} missing at the destination\n{out[-2000:]}"
        assert os.path.getsize(p) == r["bytes"]
    images_at_dest = [p for t in range(6) for p in (dest_exp / _tp_dir(t)).iterdir() if p.suffix == ".tiff"]
    assert len(images_at_dest) == tt.image_count
    # Nothing image-like is left locally; the local tree may keep empty folders or be gone entirely.
    leftovers = [p for p in exp.rglob("*") if p.is_file()] if exp.exists() else []
    assert leftovers == [], leftovers
    # Run-level files are unlisted and move only after the end record (ballast.bin proves that sweep ran).
    for name in ("acquisition parameters.json", "coordinates.csv", ".done", "acquisition.log", "ballast.bin"):
        assert (dest_exp / name).exists(), name
    _assert_verified(dest_exp)


def test_zarr_run_moves_chunk_directories_and_reassembles_a_readable_store(tmp_path, monkeypatch):
    pytest.importorskip("tensorstore")
    exp, dest_exp, tt, mpc, out = _run_with_mover(tmp_path, monkeypatch, FileSavingOption.ZARR_V3, nt=6)

    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count()
    assert tt.paused_states and tt.resumed_seconds

    records = read_manifest(dest_exp / MANIFEST_FILE_NAME)
    chunk_files = [r for r in records if r["event"] == "complete" and "/c/" in r["path"]]
    assert chunk_files and all(r["kind"] == "file" for r in chunk_files)
    stores = {r["path"].split("/c/")[0] for r in chunk_files}  # <store>/<array>/c/<t>/... -> <store>/<array>
    assert len(stores) == 1
    store = dest_exp / next(iter(stores))
    assert (store / "zarr.json").is_file(), "array metadata moved after end"
    timepoints = {r["path"].split("/c/")[1].split("/")[0] for r in chunk_files}
    assert timepoints == {str(t) for t in range(6)}
    for r in chunk_files:
        p = dest_exp / Path(*r["path"].split("/"))
        assert p.is_file() and os.path.getsize(p) == r["bytes"], r

    import tensorstore as ts_

    arr = ts_.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": str(store)}}, open=True).result()
    assert arr.shape[0] == 6, "T axis intact after chunk directories were moved mid-run"
    assert arr[5, 0, 0].read().result().any(), "last timepoint readable from the reassembled store"
    _assert_verified(dest_exp)
