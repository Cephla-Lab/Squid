import os
from pathlib import Path

import pytest

from control.core.config.repository import ConfigRepository
import control.atomic_file as atomic_file
import control.objectives_config as oc


def _config():
    return oc.parse_objectives_config(
        {
            "version": 1,
            "changer": {"kind": "none"},
            "objectives": [{"name": "10x", "magnification": 10, "na": 0.3, "tube_lens_f_mm": 180}],
        }
    )


def _profile(base, name, files):
    channel_configs = base / "user_profiles" / name / "channel_configs"
    channel_configs.mkdir(parents=True)
    for file_name, text in files.items():
        (channel_configs / file_name).write_text(text)
    return channel_configs


def test_objectives_config_round_trip(tmp_path):
    repo = ConfigRepository(base_path=tmp_path)
    assert repo.get_objectives_config() is None
    repo.save_objectives_config(_config())
    assert (tmp_path / "machine_configs" / "objectives.yaml").exists()
    assert repo.get_objectives_config() == _config()


def test_copy_into_every_profile_that_has_the_source(tmp_path):
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    b = _profile(tmp_path, "b", {"10x.yaml": "B10"})
    c = _profile(tmp_path, "c", {"general.yaml": "G"})  # no source file
    repo = ConfigRepository(base_path=tmp_path)
    assert repo.copy_objective_channel_configs("10x", "20x water") == ["a", "b"]
    assert (a / "20x water.yaml").read_text() == "A10"
    assert (b / "20x water.yaml").read_text() == "B10"
    assert not (c / "20x water.yaml").exists()


def test_copy_never_overwrites(tmp_path):
    a = _profile(tmp_path, "a", {"10x.yaml": "A10", "20x.yaml": "KEEP"})
    repo = ConfigRepository(base_path=tmp_path)
    assert repo.copy_objective_channel_configs("10x", "20x") == []
    assert (a / "20x.yaml").read_text() == "KEEP"


def test_copy_never_moves_or_deletes_the_source(tmp_path):
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)
    repo.copy_objective_channel_configs("10x", "10x oil")
    assert (a / "10x.yaml").read_text() == "A10"
    assert (a / "10x oil.yaml").read_text() == "A10"


def _fail_mid_copy(fd):
    """Simulate a copy that dies partway through: the bytes are in the temp file, but fsync fails."""
    raise OSError("disk full")


def test_failed_copy_leaves_no_target_file(tmp_path, monkeypatch):
    # R6(a): copy_objective_channel_configs must publish through write_atomically, so a copy that
    # dies partway through never leaves a <target>.yaml behind (which would otherwise be trusted as
    # "already copied" and block a retry, per "never overwrite").
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(atomic_file.os, "fsync", _fail_mid_copy)
    with pytest.raises(OSError):
        repo.copy_objective_channel_configs("10x", "20x water")
    assert not (a / "20x water.yaml").exists()
    assert [p.name for p in a.iterdir()] == ["10x.yaml"]  # no temp file left behind either


def test_retry_after_the_fault_is_removed_copies_the_full_file(tmp_path, monkeypatch):
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(atomic_file.os, "fsync", _fail_mid_copy)
    with pytest.raises(OSError):
        repo.copy_objective_channel_configs("10x", "20x water")
    monkeypatch.undo()  # the fault is fixed
    assert repo.copy_objective_channel_configs("10x", "20x water") == ["a"]
    assert (a / "20x water.yaml").read_text() == "A10"


def _fail_mid_copy_non_oserror(fd):
    """Like _fail_mid_copy, but with an exception type that isn't a subclass of OSError."""
    raise ValueError("boom")


def test_non_oserror_during_copy_leaves_no_temp_file_and_propagates(tmp_path, monkeypatch):
    # The cleanup must run for ANY exception during the copy/replace, not only OSError.
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(atomic_file.os, "fsync", _fail_mid_copy_non_oserror)
    with pytest.raises(ValueError, match="boom"):
        repo.copy_objective_channel_configs("10x", "20x water")
    assert [p.name for p in a.iterdir()] == ["10x.yaml"]  # no temp file left behind


def test_unlink_failure_during_copy_cleanup_does_not_mask_the_original_error(tmp_path, monkeypatch):
    _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)

    def _raise_unlink(self, missing_ok=False):
        raise OSError("cannot unlink temp file")

    monkeypatch.setattr(atomic_file.os, "fsync", _fail_mid_copy)
    monkeypatch.setattr(Path, "unlink", _raise_unlink)
    with pytest.raises(OSError, match="disk full"):
        repo.copy_objective_channel_configs("10x", "20x water")


def test_copy_is_forced_to_disk_before_it_is_published(tmp_path, monkeypatch):
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)
    calls = []
    real_fsync, real_replace = os.fsync, os.replace

    def _fsync(fd):
        calls.append("fsync")
        real_fsync(fd)

    def _replace(src, dst):
        calls.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(atomic_file.os, "fsync", _fsync)
    monkeypatch.setattr(atomic_file.os, "replace", _replace)
    repo.copy_objective_channel_configs("10x", "20x water")
    assert calls[:2] == ["fsync", "replace"]
    assert (a / "20x water.yaml").read_text() == "A10"
