import subprocess
from pathlib import Path
import pytest

from tools.pr540_firmware_manager import (
    FirmwareRef,
    POST_FIX,
    PRE_FIX,
    custom_ref,
    resolve_ref,
    ensure_worktree,
    WorktreeMismatch,
    PlatformIONotFound,
)


def test_presets_are_defined():
    assert POST_FIX.git_ref == "origin/master"
    assert PRE_FIX.git_ref == "fae3aa0a"
    assert POST_FIX.worktree_path.name == ".pr540-fw-master"
    assert PRE_FIX.worktree_path.name == ".pr540-fw-fae3aa0a"


def test_custom_ref_sanitizes_filesystem_unsafe_chars():
    r = custom_ref("feature/some-branch")
    assert "/" not in r.worktree_path.name
    assert r.git_ref == "feature/some-branch"


def test_resolve_ref_calls_git_rev_parse(monkeypatch):
    captured = {}

    def fake_check_output(args, cwd=None, **kw):
        captured["args"] = args
        return b"abcdef1234567890\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    sha = resolve_ref("origin/master")
    assert sha == "abcdef1234567890"
    assert captured["args"][:3] == ["git", "rev-parse", "--verify"]


def test_resolve_ref_raises_on_unknown_ref(monkeypatch):
    def fake_check_output(*a, **kw):
        raise subprocess.CalledProcessError(returncode=128, cmd="git", stderr=b"unknown revision")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    with pytest.raises(ValueError):
        resolve_ref("not-a-ref")


def test_ensure_worktree_mismatch_without_allow_reset(tmp_path, monkeypatch):
    # Worktree exists with a different SHA; ensure_worktree must raise.
    wt = tmp_path / ".pr540-fw-master"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: fake")

    def fake_check_output(args, cwd=None, **kw):
        if args[:3] == ["git", "rev-parse", "--verify"]:
            return b"newexpectedsha\n"
        if args == ["git", "rev-parse", "HEAD"]:
            return b"differentsha\n"
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    ref = FirmwareRef(label="test", git_ref="origin/master", worktree_path=wt)
    with pytest.raises(WorktreeMismatch):
        ensure_worktree(ref, allow_reset=False, log_cb=lambda s: None)
