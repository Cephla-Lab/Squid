"""Simulated OME-TIFF runs with the opt-in per-timepoint split: one file per timepoint under <t>/ome_tiff/,
each listed in the transfer manifest as soon as its planes are written, and movable by the upload tool."""

import subprocess
import sys
from pathlib import Path

import tifffile

import control._def
import control.microscope
import control.core.multi_point_worker as mpw
from control._def import FileSavingOption
from control.core.transfer_manifest import MANIFEST_FILE_NAME, read_manifest
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker, select_some_configs
from tests.control.test_transfer_manifest_integration import _one_fov, _tp_dir

TOOL = Path(__file__).resolve().parents[2] / "tools" / "upload_acquisition.py"


def _run(tmp_path, monkeypatch, split: bool, nt: int = 2):
    control._def.MERGE_CHANNELS = False
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", FileSavingOption.OME_TIFF)
    monkeypatch.setattr(mpw, "FILE_SAVING_OPTION", FileSavingOption.OME_TIFF)
    monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
    monkeypatch.setattr(control._def, "OME_TIFF_SPLIT_TIMEPOINTS", False)
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", 1000.0)
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", 0.0)

    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
    mpc.set_base_path(str(tmp_path))
    mpc.start_new_experiment("ome_run", add_timestamp=False)
    _one_fov(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_Nt(nt)
    mpc.set_deltat(0.0)
    mpc.set_large_acquisition_mode(True)
    mpc.set_split_ome_timepoints(split)

    mpc.run_acquisition()
    assert tt.started_event.wait(10)
    assert tt.finished_event.wait(120)
    mpc.thread.join(10)
    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count()
    return tmp_path / "ome_run", tt, mpc


def test_unsplit_ome_tiff_layout_is_unchanged_and_listed_only_when_the_stack_completes(tmp_path, monkeypatch):
    exp, tt, mpc = _run(tmp_path, monkeypatch, split=False, nt=2)
    files = sorted((exp / "ome_tiff").glob("*.ome.tiff"))
    assert len(files) == 1, files
    with tifffile.TiffFile(files[0]) as tif:
        assert 'SizeT="2"' in tif.ome_metadata, "one file holds both timepoints"
    records = read_manifest(exp / MANIFEST_FILE_NAME)
    completes = [r for r in records if r["event"] == "complete" and r["path"].endswith(".ome.tiff")]
    assert (
        len(completes) == 1 and completes[0]["t"] == 1
    ), "listed once, when the writer finalized at the last timepoint"
    assert not any(exp.glob("*/ome_tiff")), "no per-timepoint folders without the split"


def test_split_ome_tiff_writes_one_file_per_timepoint_and_lists_each(tmp_path, monkeypatch):
    exp, tt, mpc = _run(tmp_path, monkeypatch, split=True, nt=2)
    assert not (exp / "ome_tiff").exists(), "split runs have no run-level ome_tiff folder"
    per_t = {t: sorted((exp / _tp_dir(t) / "ome_tiff").glob("*.ome.tiff")) for t in range(2)}
    assert all(len(files) == 1 for files in per_t.values()), per_t
    n_channels = len(mpc.selected_configurations)
    for t, files in per_t.items():
        with tifffile.TiffFile(files[0]) as tif:
            assert 'SizeT="1"' in tif.ome_metadata, f"SizeT must be 1 in {files[0]}"
            assert f'SizeC="{n_channels}"' in tif.ome_metadata
    stale = list(Path(__import__("tempfile").gettempdir()).glob("squid_ome_*_metadata.json"))
    assert not [p for p in stale if "ome_run" in p.read_text()], "no leftover OME progress files for this run"

    records = read_manifest(exp / MANIFEST_FILE_NAME)
    listed = [r for r in records if r["event"] == "complete" and r["path"].endswith(".ome.tiff")]
    assert sorted(r["path"] for r in listed) == sorted(f"{_tp_dir(t)}/ome_tiff/{per_t[t][0].name}" for t in range(2))
    for r in listed:
        assert r["bytes"] is not None or r["kind"] == "file"
        assert (exp / Path(*r["path"].split("/"))).is_file()
    # Each timepoint's file precedes its timepoint_done record.
    events = [(r["event"], r.get("t")) for r in records]
    for t in range(2):
        assert events.index(("timepoint_done", t)) > max(
            i
            for i, r in enumerate(records)
            if r["event"] == "complete" and r.get("t") == t and r["path"].endswith(".ome.tiff")
        )
    assert (exp / "acquisition.yaml").read_text().find("ome_tiff_split_timepoints: true") >= 0
    assert mpc.split_ome_timepoints is False, "per-run flag reset after the run"


def test_upload_tool_moves_and_verifies_a_split_run(tmp_path, monkeypatch):
    exp, tt, mpc = _run(tmp_path, monkeypatch, split=True, nt=2)
    dest = tmp_path / "nas"
    dest.mkdir()
    run = subprocess.run(
        [sys.executable, str(TOOL), str(exp), str(dest), "--mode", "move", "--quiesce-s", "0"],
        cwd=str(TOOL.parents[1]),
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    dest_exp = dest / "ome_run"
    for t in range(2):
        assert list((dest_exp / _tp_dir(t) / "ome_tiff").glob("*.ome.tiff")), t
    assert [p for p in exp.rglob("*") if p.is_file()] == [] if exp.exists() else True
    verify = subprocess.run(
        [sys.executable, str(TOOL), "verify", str(dest_exp), str(dest)],
        cwd=str(TOOL.parents[1]),
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
