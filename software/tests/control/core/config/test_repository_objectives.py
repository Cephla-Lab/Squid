import os
import stat
from pathlib import Path

import pytest

import control.core.config.repository as repository
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
    with pytest.raises(repository.ChannelCopyError):
        repo.copy_objective_channel_configs("10x", "20x water")
    assert not (a / "20x water.yaml").exists()
    assert [p.name for p in a.iterdir()] == ["10x.yaml"]  # no temp file left behind either


def test_retry_after_the_fault_is_removed_copies_the_full_file(tmp_path, monkeypatch):
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(atomic_file.os, "fsync", _fail_mid_copy)
    with pytest.raises(repository.ChannelCopyError):
        repo.copy_objective_channel_configs("10x", "20x water")
    monkeypatch.undo()  # the fault is fixed
    assert repo.copy_objective_channel_configs("10x", "20x water") == ["a"]
    assert (a / "20x water.yaml").read_text() == "A10"


def _fail_mid_copy_non_oserror(fd):
    """Like _fail_mid_copy, but with an exception type that isn't a subclass of OSError."""
    raise ValueError("boom")


def test_non_oserror_during_copy_leaves_no_temp_file_and_is_reported(tmp_path, monkeypatch):
    # The cleanup must run for ANY exception during the copy/replace, not only OSError.
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(atomic_file.os, "fsync", _fail_mid_copy_non_oserror)
    with pytest.raises(repository.ChannelCopyError, match="boom") as raised:
        repo.copy_objective_channel_configs("10x", "20x water")
    assert isinstance(raised.value.__cause__, ValueError)
    assert [p.name for p in a.iterdir()] == ["10x.yaml"]  # no temp file left behind


def test_unlink_failure_during_copy_cleanup_does_not_mask_the_original_error(tmp_path, monkeypatch):
    _profile(tmp_path, "a", {"10x.yaml": "A10"})
    repo = ConfigRepository(base_path=tmp_path)

    def _raise_unlink(self, missing_ok=False):
        raise OSError("cannot unlink temp file")

    monkeypatch.setattr(atomic_file.os, "fsync", _fail_mid_copy)
    monkeypatch.setattr(Path, "unlink", _raise_unlink)
    with pytest.raises(repository.ChannelCopyError, match="disk full"):
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


@pytest.mark.skipif(os.name != "posix", reason="only POSIX fsyncs the directory")
def test_copy_whose_directory_sync_fails_is_published_and_counted(tmp_path, monkeypatch, caplog):
    # The copy is already under its final name when the directory sync fails: it counts as written,
    # and the remaining profiles are still copied.
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    b = _profile(tmp_path, "b", {"10x.yaml": "B10"})
    repo = ConfigRepository(base_path=tmp_path)
    real_fsync = os.fsync

    def _fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("I/O error")
        real_fsync(fd)

    monkeypatch.setattr(atomic_file.os, "fsync", _fsync)
    assert sorted(repo.copy_objective_channel_configs("10x", "20x water")) == ["a", "b"]
    assert (a / "20x water.yaml").read_text() == "A10"
    assert (b / "20x water.yaml").read_text() == "B10"
    assert "I/O error" in caplog.text


def _fail_for_profile(profile, real_write):
    def _write(path, data):
        if path.parent.parent.name == profile:
            raise OSError(f"disk full in {profile}")
        real_write(path, data)

    return _write


def test_copy_that_fails_in_a_later_profile_removes_the_earlier_copies(tmp_path, monkeypatch):
    # All or nothing: a profile left holding the copy would be kept by a retry ("never overwrite"),
    # so a retry from another source would leave the profiles on different sources.
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    b = _profile(tmp_path, "b", {"10x.yaml": "B10"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(repository, "write_atomically", _fail_for_profile("b", repository.write_atomically))
    with pytest.raises(repository.ChannelCopyError, match="disk full in b") as raised:
        repo.copy_objective_channel_configs("10x", "20x water")
    assert raised.value.leftover == {}
    assert not (a / "20x water.yaml").exists()
    assert not (b / "20x water.yaml").exists()


def _unremovable(path_to_keep):
    real_unlink = Path.unlink

    def _unlink(self, missing_ok=False):
        if self == path_to_keep:
            raise OSError("permission denied")
        real_unlink(self, missing_ok=missing_ok)

    return _unlink


def test_copy_whose_undo_fails_names_the_profiles_still_holding_it(tmp_path, monkeypatch):
    # Profile a copied, profile b failed, and a's copy could not be removed: the caller must learn
    # that a still holds it, or a retry from another source keeps it ("never overwrite").
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    _profile(tmp_path, "b", {"10x.yaml": "B10"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(repository, "write_atomically", _fail_for_profile("b", repository.write_atomically))
    monkeypatch.setattr(Path, "unlink", _unremovable(a / "20x water.yaml"))
    with pytest.raises(repository.ChannelCopyError, match="disk full in b") as raised:
        repo.copy_objective_channel_configs("10x", "20x water")
    assert list(raised.value.leftover) == ["a"]
    assert "permission denied" in str(raised.value.leftover["a"])
    assert (a / "20x water.yaml").exists()


def test_any_failure_in_a_later_profile_undoes_the_earlier_copies_and_names_what_stays(tmp_path, monkeypatch):
    # Not only OSError: whatever stops the copy in profile b, a's copy is undone or reported.
    a = _profile(tmp_path, "a", {"10x.yaml": "A10"})
    b = _profile(tmp_path, "b", {"10x.yaml": "B10"})
    repo = ConfigRepository(base_path=tmp_path)
    real_write = repository.write_atomically

    def _fail_in_b(path, data):
        if path.parent.parent.name == "b":
            raise MemoryError()
        real_write(path, data)

    monkeypatch.setattr(repository, "write_atomically", _fail_in_b)
    with pytest.raises(repository.ChannelCopyError, match="MemoryError") as raised:
        repo.copy_objective_channel_configs("10x", "20x water")
    assert isinstance(raised.value.__cause__, MemoryError) and raised.value.leftover == {}
    assert not (a / "20x water.yaml").exists() and not (b / "20x water.yaml").exists()

    monkeypatch.setattr(Path, "unlink", _unremovable(a / "20x water.yaml"))
    with pytest.raises(repository.ChannelCopyError) as raised:
        repo.copy_objective_channel_configs("10x", "20x water")
    assert list(raised.value.leftover) == ["a"]


def test_remove_objective_channel_configs_reports_what_it_could_not_remove(tmp_path, monkeypatch):
    a = _profile(tmp_path, "a", {"20x water.yaml": "A"})
    b = _profile(tmp_path, "b", {"20x water.yaml": "B"})
    repo = ConfigRepository(base_path=tmp_path)
    monkeypatch.setattr(Path, "unlink", _unremovable(a / "20x water.yaml"))
    failed = repo.remove_objective_channel_configs("20x water", ["a", "b"])
    assert list(failed) == ["a"] and "permission denied" in str(failed["a"])
    assert not (b / "20x water.yaml").exists()  # the other profile is still cleaned


def test_remove_objective_channel_configs_removes_only_the_named_profiles(tmp_path):
    a = _profile(tmp_path, "a", {"10x.yaml": "A10", "20x water.yaml": "A"})
    b = _profile(tmp_path, "b", {"10x.yaml": "B10", "20x water.yaml": "B"})
    repo = ConfigRepository(base_path=tmp_path)
    assert repo.remove_objective_channel_configs("20x water", ["a"]) == {}
    assert sorted(p.name for p in a.iterdir()) == ["10x.yaml"]
    assert sorted(p.name for p in b.iterdir()) == ["10x.yaml", "20x water.yaml"]
