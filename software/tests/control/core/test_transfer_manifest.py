"""Tests for control.core.transfer_manifest: the append-only manifest the NAS upload tool follows, and the
main-side CompletionTracker that decides when a (timepoint, region, fov) unit is complete."""

import json
import os
import pickle
from types import SimpleNamespace

import pytest

from control.core.transfer_manifest import (
    MANIFEST_FILE_NAME,
    SCHEMA_VERSION,
    CompletionTracker,
    TransferManifestWriter,
    read_manifest,
)


def _result(t=0, region="A1", fov=0, z=0, c=0, immediate=(), unit=(), kind="file", nbytes=10, complete=None):
    return SimpleNamespace(
        time_point=t,
        region_id=region,
        fov=fov,
        z_index=z,
        channel_idx=c,
        immediate_paths=tuple(immediate),
        unit_paths=tuple(unit),
        unit_kind=kind,
        bytes_written=nbytes,
        unit_complete=complete,
    )


# --- writer / reader -----------------------------------------------------------------------------


def test_writer_creates_manifest_lazily_and_writes_start_line(tmp_path):
    writer = TransferManifestWriter(str(tmp_path))
    assert not (tmp_path / MANIFEST_FILE_NAME).exists()
    writer.start(experiment_id="exp", file_format="INDIVIDUAL_IMAGES", nt=3)
    lines = read_manifest(tmp_path / MANIFEST_FILE_NAME)
    assert lines[0]["event"] == "start"
    assert lines[0]["schema"] == SCHEMA_VERSION
    assert lines[0]["experiment_id"] == "exp"
    assert lines[0]["format"] == "INDIVIDUAL_IMAGES"
    assert lines[0]["nt"] == 3
    assert lines[0]["ts"] > 0
    writer.close()


def test_complete_paths_are_relative_posix_and_carry_identity(tmp_path):
    writer = TransferManifestWriter(str(tmp_path))
    writer.start(experiment_id="exp", file_format="INDIVIDUAL_IMAGES", nt=1)
    f = tmp_path / "00000" / "A1_0000_0000_BF.tiff"
    f.parent.mkdir()
    f.write_bytes(b"x" * 5)
    writer.complete(str(f), kind="file", nbytes=5, t=0, region="A1", fov=0)
    writer.complete(
        str(tmp_path / "plate.ome.zarr" / "A" / "1" / "0" / "0" / "c" / "0"),
        kind="dir",
        nbytes=None,
        t=0,
        region="A1",
        fov=0,
    )
    writer.close()
    lines = read_manifest(tmp_path / MANIFEST_FILE_NAME)
    assert lines[1] == {
        "event": "complete",
        "path": "00000/A1_0000_0000_BF.tiff",
        "kind": "file",
        "bytes": 5,
        "t": 0,
        "region": "A1",
        "fov": 0,
        "ts": lines[1]["ts"],
    }
    assert lines[2]["path"] == "plate.ome.zarr/A/1/0/0/c/0"
    assert lines[2]["kind"] == "dir" and lines[2]["bytes"] is None


def test_paths_outside_experiment_dir_are_rejected(tmp_path):
    writer = TransferManifestWriter(str(tmp_path / "exp"))
    (tmp_path / "exp").mkdir()
    writer.start(experiment_id="exp", file_format="INDIVIDUAL_IMAGES", nt=1)
    with pytest.raises(ValueError):
        writer.complete(str(tmp_path / "elsewhere.tiff"), kind="file", nbytes=1, t=0, region="A1", fov=0)
    writer.close()


def test_timepoint_done_and_end_fsync_and_end_closes(tmp_path, monkeypatch):
    fsyncs = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsyncs.append(fd), real_fsync(fd))[1])
    writer = TransferManifestWriter(str(tmp_path))
    writer.start(experiment_id="exp", file_format="ZARR_V3", nt=2)
    writer.complete(str(tmp_path / "a"), kind="file", nbytes=1, t=0, region="r", fov=0)
    assert fsyncs == [], "per-line writes flush but do not fsync"
    writer.timepoint_done(0)
    assert len(fsyncs) == 1
    writer.end("completed")
    assert len(fsyncs) == 2
    lines = read_manifest(tmp_path / MANIFEST_FILE_NAME)
    assert [l["event"] for l in lines] == ["start", "complete", "timepoint_done", "end"]
    assert lines[-1]["reason"] == "completed"
    # Writing after end is a programming error, not silently dropped.
    with pytest.raises(RuntimeError):
        writer.complete(str(tmp_path / "b"), kind="file", nbytes=1, t=1, region="r", fov=0)


def test_reader_tolerates_truncated_last_line_and_blank_lines(tmp_path):
    p = tmp_path / MANIFEST_FILE_NAME
    p.write_text(
        json.dumps({"event": "start", "schema": 1})
        + "\n\n"
        + json.dumps({"event": "complete", "path": "x"})
        + "\n"
        + '{"event":"comp'
    )
    lines = read_manifest(p)
    assert [l["event"] for l in lines] == ["start", "complete"]


def test_reader_returns_empty_for_missing_file(tmp_path):
    assert read_manifest(tmp_path / MANIFEST_FILE_NAME) == []


def test_writer_is_thread_safe(tmp_path):
    import threading

    writer = TransferManifestWriter(str(tmp_path))
    writer.start(experiment_id="exp", file_format="INDIVIDUAL_IMAGES", nt=1)

    def work(i):
        for j in range(50):
            writer.complete(str(tmp_path / f"{i}_{j}.tiff"), kind="file", nbytes=j, t=0, region="r", fov=i)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    writer.close()
    lines = read_manifest(tmp_path / MANIFEST_FILE_NAME)
    assert len(lines) == 1 + 200
    assert all(l["event"] == "complete" for l in lines[1:])


# --- completion tracker -----------------------------------------------------------------------------


def _tracker(expected=2):
    emitted = []

    def expected_fn(key):
        return expected

    tracker = CompletionTracker(expected_planes_fn=expected_fn, on_complete=lambda unit: emitted.append(unit))
    return tracker, emitted


def test_immediate_paths_are_emitted_on_arrival():
    tracker, emitted = _tracker(expected=2)
    tracker.feed(_result(z=0, immediate=("/exp/00000/a.tiff",), nbytes=7))
    assert len(emitted) == 1
    unit = emitted[0]
    assert unit.paths == ("/exp/00000/a.tiff",) and unit.kind == "file" and unit.nbytes == 7
    assert (unit.t, unit.region, unit.fov) == (0, "A1", 0)


def test_unit_paths_are_emitted_once_all_planes_arrive_in_any_order():
    tracker, emitted = _tracker(expected=3)
    tracker.feed(_result(z=2, unit=("/exp/00000/A1_0000_stack.tiff",), nbytes=10))
    tracker.feed(_result(z=0, unit=("/exp/00000/A1_0000_stack.tiff",), nbytes=10))
    assert emitted == []
    tracker.feed(_result(z=1, unit=("/exp/00000/A1_0000_stack.tiff",), nbytes=10))
    assert len(emitted) == 1
    assert emitted[0].paths == ("/exp/00000/A1_0000_stack.tiff",)
    assert emitted[0].nbytes == 30, "bytes accumulate over the unit's planes"
    # Extra results for a completed unit are ignored (logged), not re-emitted.
    tracker.feed(_result(z=1, unit=("/exp/00000/A1_0000_stack.tiff",)))
    assert len(emitted) == 1


def test_units_are_keyed_by_timepoint_region_and_fov():
    tracker, emitted = _tracker(expected=1)
    tracker.feed(_result(t=0, region="A1", fov=0, unit=("/exp/z/A1/fov_0/0/c/0",), kind="dir"))
    tracker.feed(_result(t=0, region="A1", fov=1, unit=("/exp/z/A1/fov_1/0/c/0",), kind="dir"))
    tracker.feed(_result(t=1, region="A1", fov=0, unit=("/exp/z/A1/fov_0/0/c/1",), kind="dir"))
    assert [u.paths[0] for u in emitted] == ["/exp/z/A1/fov_0/0/c/0", "/exp/z/A1/fov_1/0/c/0", "/exp/z/A1/fov_0/0/c/1"]
    assert all(u.kind == "dir" for u in emitted)


def test_fovs_sharing_a_6d_store_complete_independently():
    tracker, emitted = _tracker(expected=2)
    store = "/exp/zarr/R/acquisition.zarr"
    for z in (0, 1):
        tracker.feed(_result(t=0, region="R", fov=1, z=z, unit=(f"{store}/c/1/0",), kind="dir"))
    assert [u.paths for u in emitted] == [(f"{store}/c/1/0",)], "fov 1 is movable without waiting for fov 0"
    tracker.feed(_result(t=0, region="R", fov=0, z=0, unit=(f"{store}/c/0/0",), kind="dir"))
    assert len(emitted) == 1
    assert [(k.region, k.fov) for k in tracker.incomplete_units()] == [("R", 0)]


def test_writer_authoritative_completion_overrides_counting():
    tracker, emitted = _tracker(expected=99)
    tracker.feed(_result(z=0, unit=("/exp/ome_tiff/A1_0000.ome.tiff",), complete=False))
    assert emitted == []
    tracker.feed(_result(z=1, unit=("/exp/ome_tiff/A1_0000.ome.tiff",), complete=True))
    assert len(emitted) == 1


def test_results_without_paths_only_count():
    tracker, emitted = _tracker(expected=2)
    tracker.feed(_result(z=0))  # simulated I/O: nothing on disk
    tracker.feed(_result(z=1))
    assert emitted == [], "a unit with no paths completes silently"
    assert tracker.incomplete_units() == []


def test_incomplete_units_are_reported_not_emitted():
    tracker, emitted = _tracker(expected=2)
    tracker.feed(_result(t=0, z=0, unit=("/exp/x",)))
    tracker.feed(_result(t=1, z=0, unit=("/exp/y",)))
    tracker.feed(_result(t=1, z=1, unit=("/exp/y",)))
    assert [u.paths for u in emitted] == [("/exp/y",)]
    assert [(k.t, k.region, k.fov) for k in tracker.incomplete_units()] == [(0, "A1", 0)]


def test_conflicting_unit_paths_within_a_unit_raise():
    tracker, emitted = _tracker(expected=2)
    tracker.feed(_result(z=0, unit=("/exp/a",)))
    with pytest.raises(ValueError):
        tracker.feed(_result(z=1, unit=("/exp/b",)))


def test_completed_unit_is_picklable_for_logging():
    tracker, emitted = _tracker(expected=1)
    tracker.feed(_result(unit=("/exp/a",)))
    assert pickle.loads(pickle.dumps(emitted[0])) == emitted[0]
