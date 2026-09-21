"""Tests for tools/upload_acquisition.py, the manifest-driven acquisition mover.

NOTE: this directory deliberately has no __init__.py. `tests/tools.py` already exists
as a module (imported as `tests.tools` by several test files); a regular package named
`tests/tools/` would shadow it and break those imports. Without __init__.py the module
still wins the `tests.tools` name and this file is collected as a top-level test module.

The tool lives in `tools/`, which is not an importable package, so it is loaded from its
path here rather than imported.
"""

import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

_TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "upload_acquisition.py"
_spec = importlib.util.spec_from_file_location("upload_acquisition", _TOOL_PATH)
ua = importlib.util.module_from_spec(_spec)
# @dataclass resolves annotations through sys.modules[cls.__module__], so register first.
sys.modules[_spec.name] = ua
_spec.loader.exec_module(ua)


# Far enough in the future that anything written by a test counts as quiescent.
FUTURE = time.time() + 100_000.0


def write_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def write_manifest(experiment_dir: Path, events, truncated_tail: str = "") -> Path:
    path = experiment_dir / ua.MANIFEST_NAME
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
        if truncated_tail:
            f.write(truncated_tail)  # no trailing newline: a writer killed mid-line
    return path


def complete(path, nbytes=None, kind="file", t=0):
    return {
        "event": "complete",
        "path": path,
        "kind": kind,
        "bytes": nbytes,
        "t": t,
        "region": "A1",
        "fov": 0,
        "ts": 2.0,
    }


@pytest.fixture
def experiment(tmp_path):
    """A tiny experiment: two loose images plus a sharded-zarr chunk *directory* unit.

    `z_final.txt` sorts after `transfer_manifest.jsonl`, so a test that asserts the
    manifest moves last cannot pass by alphabetical accident.
    """
    exp = tmp_path / "exp"
    write_file(exp / "00000" / "A1_0000_0000_BF.tiff", b"BF" * 8)
    write_file(exp / "00000" / "A1_0000_0000_FL.tiff", b"FL" * 16)
    write_file(exp / "plate.ome.zarr" / "A" / "1" / "0" / "0" / "c" / "0" / "shard.bin", b"chunk" * 4)
    write_file(exp / "acquisition.log", b"log lines\n")
    write_file(exp / "acquisition parameters.json", b"{}")
    write_file(exp / "z_final.txt", b"tail\n")
    return exp


@pytest.fixture
def destination(tmp_path):
    dest = tmp_path / "nas"
    dest.mkdir()
    return dest


def dest_exp(destination: Path, experiment: Path) -> Path:
    return destination / experiment.name


# --------------------------------------------------------------------------------------
# manifest-driven transfers
# --------------------------------------------------------------------------------------


def test_manifest_copy_preserves_tree_and_sizes(experiment, destination):
    write_manifest(
        experiment,
        [
            {"event": "start", "schema": 1, "experiment_id": "exp", "format": "INDIVIDUAL_IMAGES", "nt": 1, "ts": 1.0},
            complete("00000/A1_0000_0000_BF.tiff", 16),
            complete("plate.ome.zarr/A/1/0/0/c/0", None, kind="dir"),
        ],
    )

    summary = ua.run_pass(experiment, destination, mode="copy", now=FUTURE)

    out = dest_exp(destination, experiment)
    assert (out / "00000" / "A1_0000_0000_BF.tiff").read_bytes() == b"BF" * 8
    assert (out / "plate.ome.zarr" / "A" / "1" / "0" / "0" / "c" / "0" / "shard.bin").read_bytes() == b"chunk" * 4
    # copy leaves the sources alone
    assert (experiment / "00000" / "A1_0000_0000_BF.tiff").exists()
    # unlisted files are not touched before `end`
    assert not (out / "acquisition.log").exists()
    assert not (out / "00000" / "A1_0000_0000_FL.tiff").exists()
    assert summary.files == 2
    assert summary.failed == 0
    assert summary.bytes == 16 + 20


def test_manifest_move_unlinks_sources_but_keeps_dirs_before_end(experiment, destination):
    write_manifest(
        experiment,
        [
            complete("00000/A1_0000_0000_BF.tiff", 16),
            complete("plate.ome.zarr/A/1/0/0/c/0", None, kind="dir"),
        ],
    )

    ua.run_pass(experiment, destination, mode="move", now=FUTURE)

    assert not (experiment / "00000" / "A1_0000_0000_BF.tiff").exists()
    assert not (experiment / "plate.ome.zarr" / "A" / "1" / "0" / "0" / "c" / "0" / "shard.bin").exists()
    # never delete a directory before `end` - not even an emptied `dir` unit
    assert (experiment / "plate.ome.zarr" / "A" / "1" / "0" / "0" / "c" / "0").is_dir()
    assert (experiment / "00000").is_dir()


def test_truncated_last_manifest_line_is_ignored(experiment, destination):
    write_manifest(
        experiment,
        [complete("00000/A1_0000_0000_BF.tiff", 16)],
        truncated_tail='{"event":"complete","path":"00000/A1_0000_0000_FL.t',
    )

    entries = ua.load_manifest(experiment / ua.MANIFEST_NAME)
    assert len(entries) == 1

    summary = ua.run_pass(experiment, destination, mode="copy", now=FUTURE)
    assert summary.files == 1
    assert summary.failed == 0


def test_truncated_last_line_ignored_by_the_inline_reader(experiment, monkeypatch):
    # The tool is meant to run on a transfer box with no Squid checkout, where the lazy
    # import fails and the inline reader takes over.
    monkeypatch.setattr(ua, "_import_read_manifest", lambda: None)
    write_manifest(
        experiment,
        [complete("00000/A1_0000_0000_BF.tiff", 16)],
        truncated_tail='{"event":"complete","path":"00000/A1_0000_0000_FL.t',
    )

    assert [e["path"] for e in ua.load_manifest(experiment / ua.MANIFEST_NAME)] == ["00000/A1_0000_0000_BF.tiff"]


def test_squid_reader_is_used_when_importable(experiment, monkeypatch):
    called = []
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])

    def fake_reader(path):
        called.append(path)
        return [complete("from/squid/reader.tiff", 1)]

    monkeypatch.setattr(ua, "_import_read_manifest", lambda: fake_reader)
    assert [e["path"] for e in ua.load_manifest(experiment / ua.MANIFEST_NAME)] == ["from/squid/reader.tiff"]
    assert called


def test_corrupt_middle_line_falls_back_instead_of_stranding_data(experiment, destination, monkeypatch):
    # Squid's reader raises ValueError on a malformed line that is not the last one.
    def strict_reader(path):
        raise ValueError("Malformed manifest line 2")

    monkeypatch.setattr(ua, "_import_read_manifest", lambda: strict_reader)
    manifest = write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])
    with open(manifest, "a", encoding="utf-8") as f:
        f.write("{not json}\n")
        f.write(json.dumps(complete("00000/A1_0000_0000_FL.tiff", 32)) + "\n")

    summary = ua.run_pass(experiment, destination, mode="copy", now=FUTURE)

    assert summary.files == 2
    assert summary.failed == 0


def test_newer_schema_is_flagged(experiment, destination, caplog):
    write_manifest(
        experiment,
        [
            {"event": "start", "schema": ua.SUPPORTED_SCHEMA + 1, "experiment_id": "exp", "nt": 1, "ts": 1.0},
            complete("00000/A1_0000_0000_BF.tiff", 16),
        ],
    )

    with caplog.at_level("WARNING", logger="upload_acquisition"):
        ua.run_pass(experiment, destination, mode="copy", now=FUTURE)

    assert any("newer than this tool understands" in record.message for record in caplog.records)


def test_second_pass_picks_up_appended_entries(experiment, destination):
    manifest = write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])

    first = ua.run_pass(experiment, destination, mode="move", now=FUTURE)
    assert first.files == 1

    with open(manifest, "a", encoding="utf-8") as f:
        f.write(json.dumps(complete("00000/A1_0000_0000_FL.tiff", 32)) + "\n")

    second = ua.run_pass(experiment, destination, mode="move", now=FUTURE)
    assert second.files == 1
    out = dest_exp(destination, experiment)
    assert (out / "00000" / "A1_0000_0000_FL.tiff").read_bytes() == b"FL" * 16


# --------------------------------------------------------------------------------------
# post-end sweep
# --------------------------------------------------------------------------------------


def _ended_manifest(experiment):
    return write_manifest(
        experiment,
        [
            complete("00000/A1_0000_0000_BF.tiff", 16),
            complete("00000/A1_0000_0000_FL.tiff", 32),
            complete("plate.ome.zarr/A/1/0/0/c/0", None, kind="dir"),
            {"event": "timepoint_done", "t": 0, "ts": 4.0},
            {"event": "end", "reason": "completed", "ts": 9.0},
        ],
    )


def test_unlisted_files_move_only_after_end_and_quiescence(experiment, destination):
    _ended_manifest(experiment)

    # Not quiescent yet: the tree was written a moment ago.
    early = ua.run_pass(experiment, destination, mode="copy", quiesce_s=30.0, now=time.time())
    out = dest_exp(destination, experiment)
    assert not early.finished
    assert not (out / "acquisition.log").exists()
    assert not (out / ua.MANIFEST_NAME).exists()

    late = ua.run_pass(experiment, destination, mode="copy", quiesce_s=30.0, now=FUTURE)
    assert late.finished
    assert (out / "acquisition.log").read_bytes() == b"log lines\n"
    assert (out / "acquisition parameters.json").exists()
    assert (out / ua.MANIFEST_NAME).exists()


def test_manifest_is_transferred_last(experiment, destination, monkeypatch):
    _ended_manifest(experiment)
    order = []
    real = ua.transfer_file

    def spy(src, dst, mode, checksum=False, dry_run=False):
        order.append(Path(dst).name)
        return real(src, dst, mode, checksum=checksum, dry_run=dry_run)

    monkeypatch.setattr(ua, "transfer_file", spy)

    ua.run_pass(experiment, destination, mode="move", quiesce_s=30.0, now=FUTURE)

    assert order[-1] == ua.MANIFEST_NAME
    assert order.count(ua.MANIFEST_NAME) == 1


def test_empty_source_dirs_removed_only_in_move_mode(experiment, destination):
    _ended_manifest(experiment)

    copy_summary = ua.run_pass(experiment, destination, mode="copy", quiesce_s=30.0, now=FUTURE)
    assert copy_summary.finished
    assert experiment.is_dir()
    assert (experiment / "00000").is_dir()

    move_dest = destination.parent / "nas2"
    move_dest.mkdir()
    move_summary = ua.run_pass(experiment, move_dest, mode="move", quiesce_s=30.0, now=FUTURE)
    assert move_summary.finished
    assert not (experiment / "00000").exists()
    assert not (experiment / "plate.ome.zarr").exists()


# --------------------------------------------------------------------------------------
# failure handling and resume
# --------------------------------------------------------------------------------------


def test_failed_copy_keeps_source_and_is_reported(experiment, destination, monkeypatch):
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])

    def boom(*args, **kwargs):
        raise OSError("NAS went away")

    monkeypatch.setattr(ua, "_copy_and_digest", boom)

    summary = ua.run_pass(experiment, destination, mode="move", now=FUTURE)

    assert summary.failed == 1
    assert any("A1_0000_0000_BF.tiff" in f for f in summary.failures)
    assert (experiment / "00000" / "A1_0000_0000_BF.tiff").exists()
    out = dest_exp(destination, experiment)
    assert not (out / "00000" / "A1_0000_0000_BF.tiff").exists()
    assert not list(out.rglob("*.partial"))


def test_failed_entry_retried_on_next_pass(experiment, destination, monkeypatch):
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])

    monkeypatch.setattr(ua, "_copy_and_digest", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert ua.run_pass(experiment, destination, mode="move", now=FUTURE).failed == 1

    monkeypatch.undo()
    retry = ua.run_pass(experiment, destination, mode="move", now=FUTURE)
    assert retry.failed == 0
    assert retry.files == 1


def test_resume_skips_file_already_at_destination(experiment, destination, monkeypatch):
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])
    out = dest_exp(destination, experiment)
    write_file(out / "00000" / "A1_0000_0000_BF.tiff", b"BF" * 8)

    calls = []
    real = ua._copy_and_digest
    monkeypatch.setattr(ua, "_copy_and_digest", lambda *a, **k: (calls.append(a), real(*a, **k))[1])

    summary = ua.run_pass(experiment, destination, mode="copy", now=FUTURE)

    assert calls == []
    assert summary.skipped == 1
    assert summary.files == 0


def test_checksum_recopies_size_equal_but_different_content(experiment, destination):
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])
    out = dest_exp(destination, experiment)
    corrupt = write_file(out / "00000" / "A1_0000_0000_BF.tiff", b"XX" * 8)
    assert corrupt.stat().st_size == (experiment / "00000" / "A1_0000_0000_BF.tiff").stat().st_size

    skipped = ua.run_pass(experiment, destination, mode="copy", checksum=False, now=FUTURE)
    assert skipped.skipped == 1
    assert corrupt.read_bytes() == b"XX" * 8

    fixed = ua.run_pass(experiment, destination, mode="copy", checksum=True, now=FUTURE)
    assert fixed.files == 1
    assert corrupt.read_bytes() == b"BF" * 8


def test_move_of_already_present_file_still_unlinks_source(experiment, destination):
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])
    out = dest_exp(destination, experiment)
    write_file(out / "00000" / "A1_0000_0000_BF.tiff", b"BF" * 8)

    ua.run_pass(experiment, destination, mode="move", now=FUTURE)

    assert not (experiment / "00000" / "A1_0000_0000_BF.tiff").exists()


def test_manifest_path_escaping_experiment_dir_is_refused(experiment, destination):
    write_manifest(experiment, [complete("../escape.tiff", 4), complete("/etc/passwd", 4)])

    summary = ua.run_pass(experiment, destination, mode="copy", now=FUTURE)

    assert summary.files == 0
    assert not (destination.parent / "escape.tiff").exists()


def test_mtime_is_preserved(experiment, destination):
    src = experiment / "00000" / "A1_0000_0000_BF.tiff"
    os.utime(src, (1_600_000_000, 1_600_000_000))
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])

    ua.run_pass(experiment, destination, mode="copy", now=FUTURE)

    copied = dest_exp(destination, experiment) / "00000" / "A1_0000_0000_BF.tiff"
    assert int(copied.stat().st_mtime) == 1_600_000_000


# --------------------------------------------------------------------------------------
# legacy (no manifest) sources of truth
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("pad", [5, 0])
def test_legacy_timepoint_done_markers_do_not_authorize_mid_run_moves(tmp_path, destination, pad):
    # A timepoint .done only means imaging finished; asynchronous saves may still be writing, so
    # without a manifest nothing moves until the root .done says the acquisition is over.
    first, second = f"{0:0{pad}d}", f"{1:0{pad}d}"
    exp = tmp_path / "legacy"
    write_file(exp / first / "A1_0000_0000_BF.tiff", b"a" * 10)
    write_file(exp / first / "coordinates.csv", b"region,x (mm),y (mm)\n")
    write_file(exp / first / ".done", b"")
    write_file(exp / second / "A1_0000_0000_BF.tiff", b"b" * 10)
    write_file(exp / "acquisition.log", b"log\n")

    assert ua.legacy_movable(exp, quiesce_s=30.0, now=FUTURE) == []
    summary = ua.run_pass(exp, destination, mode="move", quiesce_s=30.0, now=FUTURE)

    assert not (destination / "legacy").exists()
    assert (exp / first / "A1_0000_0000_BF.tiff").exists()
    assert summary.files == 0 and not summary.finished
    assert ua.main([str(exp), str(destination), "--mode", "move", "--quiesce-s", "0"]) == ua.EXIT_NOTHING_MOVABLE


def test_timepoint_folders_are_ordered_numerically(tmp_path):
    exp = tmp_path / "legacy"
    for name in ("0", "1", "2", "10", "11"):
        write_file(exp / name / ".done", b"")
    write_file(exp / "not_a_timepoint" / "x.tiff", b"x")

    assert [p.name for p in ua.timepoint_dirs(exp)] == ["0", "1", "2", "10", "11"]


def test_destination_that_aliases_or_overlaps_the_source_is_rejected(tmp_path):
    exp = tmp_path / "data" / "exp"
    write_file(exp / "0" / "A1_0000_0000_BF.tiff", b"a" * 10)
    write_file(exp / ".done", b"")

    # dest/<name> == source: every file would "match" itself and move mode would delete the run.
    with pytest.raises(ValueError):
        ua.run_pass(exp, exp.parent, mode="move", quiesce_s=0.0, now=FUTURE)
    # destination inside the source
    with pytest.raises(ValueError):
        ua.run_pass(exp, exp / "nas", mode="move", quiesce_s=0.0, now=FUTURE)
    # symlinked alias of the parent
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "data")
    with pytest.raises(ValueError):
        ua.run_pass(exp, alias, mode="move", quiesce_s=0.0, now=FUTURE)
    assert (exp / "0" / "A1_0000_0000_BF.tiff").exists(), "nothing may be touched when the destination is rejected"
    assert ua.main([str(exp), str(exp.parent), "--mode", "move"]) == ua.EXIT_PROBLEMS
    assert (exp / "0" / "A1_0000_0000_BF.tiff").exists()

    # A disjoint sibling is fine.
    sibling = tmp_path / "data" / "nas"
    sibling.mkdir()
    summary = ua.run_pass(exp, sibling, mode="copy", quiesce_s=0.0, now=FUTURE)
    assert summary.finished and (sibling / "exp" / "0" / "A1_0000_0000_BF.tiff").exists()


def test_transfer_file_refuses_the_same_file(tmp_path):
    src = write_file(tmp_path / "a.tiff", b"a" * 10)
    result = ua.transfer_file(src, src, mode="move")
    assert result.status == "failed" and src.exists()


def test_legacy_root_done_moves_everything(tmp_path, destination):
    exp = tmp_path / "legacy"
    write_file(exp / "0" / "A1_0000_0000_BF.tiff", b"a" * 10)
    write_file(exp / "0" / ".done", b"")
    write_file(exp / "acquisition.log", b"log\n")
    write_file(exp / ".done", b"")

    summary = ua.run_pass(exp, destination, mode="move", quiesce_s=30.0, now=FUTURE)

    out = destination / "legacy"
    assert summary.finished
    assert (out / "acquisition.log").exists()
    assert (out / "0" / "A1_0000_0000_BF.tiff").exists()
    assert not (exp / "0").exists()


def test_fully_listed_timepoints(experiment):
    manifest = [
        {"event": "start", "schema": 1, "experiment_id": "exp", "format": "INDIVIDUAL_IMAGES", "nt": 2, "ts": 1.0},
        complete("00000/A1_0000_0000_BF.tiff", 16, t=0),
        {"event": "timepoint_done", "t": 0, "ts": 4.0},
        complete("00001/A1_0000_0000_BF.tiff", 16, t=1),
    ]

    # timepoint_done follows every complete record of that timepoint, so t=0 is fully
    # listed while t=1 is still open.
    assert ua.fully_listed_timepoints(manifest) == [0]


def test_is_quiescent(tmp_path):
    tree = tmp_path / "tree"
    write_file(tree / "a" / "b.txt", b"x")
    assert not ua.is_quiescent(tree, quiesce_s=30.0, now=time.time())
    assert ua.is_quiescent(tree, quiesce_s=30.0, now=FUTURE)


# --------------------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------------------


def _verified_tree(destination, name="exp"):
    out = destination / name
    write_file(out / "00000" / "A1_0000_0000_BF.tiff", b"BF" * 8)
    write_file(out / "00000" / "coordinates.csv", b"region,x (mm),y (mm)\n")
    write_file(out / "00000" / ".done", b"")
    return out


def test_verify_clean_tree(tmp_path, destination):
    exp = tmp_path / "exp"
    exp.mkdir()
    out = _verified_tree(destination)
    write_manifest(
        out, [complete("00000/A1_0000_0000_BF.tiff", 16), {"event": "end", "reason": "completed", "ts": 9.0}]
    )

    assert ua.verify_destination(exp, destination) == []
    assert ua.main(["verify", str(exp), str(destination)]) == 0


def test_verify_reports_missing_listed_file(tmp_path, destination):
    exp = tmp_path / "exp"
    exp.mkdir()
    out = _verified_tree(destination)
    write_manifest(
        out,
        [
            complete("00000/A1_0000_0000_BF.tiff", 16),
            complete("00000/A1_0000_0000_FL.tiff", 32),
            {"event": "end", "reason": "completed", "ts": 9.0},
        ],
    )

    problems = ua.verify_destination(exp, destination)
    assert any("A1_0000_0000_FL.tiff" in p for p in problems)
    assert ua.main(["verify", str(exp), str(destination)]) == 1


def test_verify_reports_short_file(tmp_path, destination):
    exp = tmp_path / "exp"
    exp.mkdir()
    out = _verified_tree(destination)
    (out / "00000" / "A1_0000_0000_BF.tiff").write_bytes(b"BF")
    write_manifest(
        out, [complete("00000/A1_0000_0000_BF.tiff", 16), {"event": "end", "reason": "completed", "ts": 9.0}]
    )

    problems = ua.verify_destination(exp, destination)
    assert any("size" in p and "A1_0000_0000_BF.tiff" in p for p in problems)


@pytest.mark.parametrize("timepoint", ["00000", "0"])
def test_verify_reports_missing_done_and_coordinates(tmp_path, destination, timepoint):
    exp = tmp_path / "exp"
    exp.mkdir()
    out = destination / "exp"
    write_file(out / timepoint / "A1_0000_0000_BF.tiff", b"BF" * 8)

    problems = ua.verify_destination(exp, destination)
    assert any(".done" in p for p in problems)
    assert any("coordinates.csv" in p for p in problems)


def test_verify_reports_incomplete_zarr(tmp_path, destination):
    exp = tmp_path / "exp"
    exp.mkdir()
    out = destination / "exp"
    zarr_json = {"attributes": {"_squid": {"acquisition_complete": False}}}
    write_file(out / "plate.ome.zarr" / "zarr.json", json.dumps(zarr_json).encode())
    write_file(out / "plate.ome.zarr" / "A" / "zarr.json", b'{"attributes":{}}')
    write_file(out / "plate.ome.zarr" / "A" / "1" / "zarr.json", b'{"attributes":{}}')

    problems = ua.verify_destination(exp, destination)
    assert any("acquisition_complete" in p for p in problems)


def test_verify_reports_missing_well_metadata(tmp_path, destination):
    exp = tmp_path / "exp"
    exp.mkdir()
    out = destination / "exp"
    write_file(out / "plate.ome.zarr" / "zarr.json", b'{"attributes":{}}')
    write_file(out / "plate.ome.zarr" / "A" / "1" / "0" / "zarr.json", b'{"attributes":{}}')

    problems = ua.verify_destination(exp, destination)
    assert any("zarr.json" in p for p in problems)


def test_verify_reports_stray_ome_metadata(tmp_path, destination):
    exp = tmp_path / "exp"
    exp.mkdir()
    out = destination / "exp"
    write_file(out / "ome_tiff" / "A1_0.ome.tiff", b"tiff")
    write_file(out / "squid_ome_deadbeef_metadata.json", b"{}")

    problems = ua.verify_destination(exp, destination)
    assert any("squid_ome" in p for p in problems)


def test_verify_missing_destination_is_reported(tmp_path, destination):
    exp = tmp_path / "exp"
    exp.mkdir()

    problems = ua.verify_destination(exp, destination)
    assert problems and "exp" in problems[0]


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_main_copies_and_returns_zero(experiment, destination):
    _ended_manifest(experiment)
    old = time.time() - 10_000
    for path in experiment.rglob("*"):
        os.utime(path, (old, old))
    os.utime(experiment, (old, old))

    rc = ua.main([str(experiment), str(destination), "--mode", "copy"])

    assert rc == 0
    assert (dest_exp(destination, experiment) / ua.MANIFEST_NAME).exists()


def test_main_dry_run_changes_nothing(experiment, destination):
    _ended_manifest(experiment)
    before = sorted(p.name for p in destination.rglob("*"))

    rc = ua.main([str(experiment), str(destination), "--mode", "move", "--dry-run"])

    assert rc == 0
    assert sorted(p.name for p in destination.rglob("*")) == before
    assert (experiment / "00000" / "A1_0000_0000_BF.tiff").exists()


def test_main_returns_two_when_nothing_movable_yet(tmp_path, destination):
    exp = tmp_path / "exp"
    write_file(exp / "00000" / "A1_0000_0000_BF.tiff", b"a")

    assert ua.main([str(exp), str(destination)]) == 2


def test_main_returns_one_on_transfer_failure(experiment, destination, monkeypatch):
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])
    monkeypatch.setattr(ua, "_copy_and_digest", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))

    assert ua.main([str(experiment), str(destination)]) == 1


def test_main_rejects_unknown_mode(experiment, destination):
    with pytest.raises(SystemExit):
        ua.main([str(experiment), str(destination), "--mode", "teleport"])


def test_main_parses_all_options(experiment, destination, monkeypatch):
    seen = {}
    real = ua.run_pass

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(ua, "run_pass", spy)
    write_manifest(experiment, [complete("00000/A1_0000_0000_BF.tiff", 16)])

    ua.main([str(experiment), str(destination), "--checksum", "--quiesce-s", "5", "--log-level", "DEBUG"])

    assert seen["checksum"] is True
    assert seen["quiesce_s"] == 5.0
    assert seen["mode"] == "copy"


def test_follow_exits_after_end(experiment, destination, monkeypatch):
    _ended_manifest(experiment)
    monkeypatch.setattr(ua.time, "sleep", lambda _s: None)

    rc = ua.main([str(experiment), str(destination), "--follow", "--mode", "move", "--quiesce-s", "0", "--poll-s", "0"])

    assert rc == 0
    assert (dest_exp(destination, experiment) / ua.MANIFEST_NAME).exists()
    assert not (experiment / ua.MANIFEST_NAME).exists()


def test_transfer_file_verifies_checksum(tmp_path):
    src = write_file(tmp_path / "src.bin", b"payload" * 100)
    dst = tmp_path / "out" / "src.bin"
    dst.parent.mkdir()

    result = ua.transfer_file(src, dst, mode="copy", checksum=True)

    assert result.status == "transferred"
    assert result.bytes == src.stat().st_size
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == hashlib.sha256(src.read_bytes()).hexdigest()


def _zarr_store_with_chunks(root: Path, name: str = "fov_0.ome.zarr"):
    store = root / name
    write_file(store / "zarr.json", b'{"attributes": {}}')
    write_file(store / "0" / "zarr.json", b'{"attributes": {"_squid": {"acquisition_complete": true}}}')
    chunks = [
        write_file(store / "0" / "c" / "0" / c / "0" / "0" / "0", bytes([c_i]) * 16) for c_i, c in enumerate(("0", "1"))
    ]
    return store, chunks


def test_verify_detects_a_zarr_array_that_lost_its_metadata_or_chunks_after_the_move(tmp_path):
    dest = tmp_path / "nas" / "exp"
    store, chunks = _zarr_store_with_chunks(dest / "zarr" / "A1")
    rel = lambda p: p.relative_to(dest).as_posix()
    # Squid lists chunk directories file by file, so the manifest is the inventory.
    write_manifest(
        dest,
        [
            {"event": "start", "schema": 1, "experiment_id": "exp", "format": "ZARR_V3", "nt": 1, "ts": 1.0},
            complete(rel(chunks[0]), 16, t=0),
            complete(rel(chunks[1]), 16, t=0),
            {"event": "end", "reason": "completed", "ts": 9.0},
        ],
    )
    write_file(dest / ".done", b"")
    assert ua.verify_destination(tmp_path / "local" / "exp", dest.parent) == []

    (store / "0" / "zarr.json").unlink()
    problems = ua.verify_destination(tmp_path / "local" / "exp", dest.parent)
    assert any("missing array metadata" in p for p in problems), problems

    write_file(store / "0" / "zarr.json", b'{"attributes": {}}')
    chunks[1].unlink()
    problems = ua.verify_destination(tmp_path / "local" / "exp", dest.parent)
    assert any(p.startswith("missing file:") and "c/0/1/" in p for p in problems), problems

    for chunk in chunks:
        chunk.unlink(missing_ok=True)
    problems = ua.verify_destination(tmp_path / "local" / "exp", dest.parent)
    assert any("no chunk files" in p for p in problems), problems
