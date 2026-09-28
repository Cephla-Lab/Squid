import ast
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

import control.atomic_file as af


def test_module_imports_only_the_standard_library():
    """control.objectives_config imports this module, and control._def imports that one while it is
    still initializing: like objectives_config, this must stay a leaf."""
    tree = ast.parse(Path(af.__file__).read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
    for module in modules:
        assert module.split(".")[0] in sys.stdlib_module_names, module


def test_writes_the_bytes(tmp_path):
    path = tmp_path / "a.yaml"
    af.write_atomically(path, b"new")
    assert path.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]  # no temp file left behind


def test_replaces_an_existing_file(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_bytes(b"old")
    af.write_atomically(path, b"new")
    assert path.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]


def test_file_is_forced_to_disk_before_the_rename(tmp_path, monkeypatch):
    """Without an fsync before the rename, NTFS or ext4 can keep the rename across a power cut but
    lose the data, leaving an empty or zeroed file under the final name."""
    path = tmp_path / "a.yaml"
    calls = []
    real_fsync, real_replace = os.fsync, os.replace

    def _fsync(fd):
        calls.append(("fsync", "file" if stat.S_ISREG(os.fstat(fd).st_mode) else "directory"))
        real_fsync(fd)

    def _replace(src, dst):
        calls.append(("replace", Path(dst).name))
        real_replace(src, dst)

    monkeypatch.setattr(af.os, "fsync", _fsync)
    monkeypatch.setattr(af.os, "replace", _replace)
    af.write_atomically(path, b"new")
    if os.name == "posix":
        # The directory is synced after the rename, so the rename itself survives a power cut.
        assert calls == [("fsync", "file"), ("replace", "a.yaml"), ("fsync", "directory")]
    else:
        assert calls == [("fsync", "file"), ("replace", "a.yaml")]


def test_failing_replace_leaves_the_previous_file_and_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "a.yaml"
    path.write_bytes(b"old")
    error = OSError("disk full")

    def _raise(*a, **k):
        raise error

    monkeypatch.setattr(af.os, "replace", _raise)
    with pytest.raises(OSError) as raised:
        af.write_atomically(path, b"new")
    assert raised.value is error
    assert path.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]


def test_failing_write_leaves_no_temp_file_and_raises_the_original_error(tmp_path, monkeypatch):
    # An fsync failure is how a write that did not reach the disk shows up.
    path = tmp_path / "a.yaml"
    path.write_bytes(b"old")
    error = OSError("I/O error")

    def _raise(fd):
        raise error

    monkeypatch.setattr(af.os, "fsync", _raise)
    with pytest.raises(OSError) as raised:
        af.write_atomically(path, b"new")
    assert raised.value is error
    assert path.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]


def test_non_oserror_leaves_no_temp_file_and_propagates(tmp_path, monkeypatch):
    path = tmp_path / "a.yaml"

    def _raise(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(af.os, "replace", _raise)
    with pytest.raises(ValueError, match="boom"):
        af.write_atomically(path, b"new")
    assert list(tmp_path.iterdir()) == []


def test_cleanup_failure_is_logged_and_does_not_mask_the_original_error(tmp_path, monkeypatch, caplog):
    path = tmp_path / "a.yaml"

    def _raise_replace(*a, **k):
        raise OSError("disk full")

    def _raise_unlink(self, missing_ok=False):
        raise OSError("cannot unlink temp file")

    monkeypatch.setattr(af.os, "replace", _raise_replace)
    monkeypatch.setattr(Path, "unlink", _raise_unlink)
    with caplog.at_level(logging.ERROR, logger=af.__name__):
        with pytest.raises(OSError, match="disk full"):
            af.write_atomically(path, b"new")
    assert "cannot unlink temp file" in caplog.text and "disk full" in caplog.text
