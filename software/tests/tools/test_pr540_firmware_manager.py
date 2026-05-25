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


from tools.pr540_firmware_manager import build_firmware, flash_firmware, BuildFailed, FlashFailed


class _FakePopen:
    def __init__(self, lines, returncode):
        self._lines = list(lines)
        self.returncode = returncode
        self.stdout = self
        self.args = None

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return ""

    def wait(self):
        return self.returncode

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line


def test_build_firmware_success(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "firmware" / "controller").mkdir(parents=True)
    captured = {}

    def fake_popen(args, **kw):
        captured["args"] = args
        return _FakePopen(["Building...\n", "Linking...\n", "SUCCESS\n"], returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    lines = []
    build_firmware(wt, log_cb=lines.append)
    assert any("SUCCESS" in line for line in lines)
    assert "pio" in captured["args"][0] or captured["args"][0].endswith("pio")
    assert "-d" in captured["args"]
    assert "-e" in captured["args"]
    assert "teensy41" in captured["args"]


def test_build_firmware_failure_raises(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "firmware" / "controller").mkdir(parents=True)

    def fake_popen(args, **kw):
        return _FakePopen(["Error: missing lib\n"], returncode=1)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(BuildFailed):
        build_firmware(wt, log_cb=lambda s: None)


def test_build_firmware_missing_pio_raises(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "firmware" / "controller").mkdir(parents=True)

    def fake_popen(args, **kw):
        raise FileNotFoundError("pio not found")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(PlatformIONotFound):
        build_firmware(wt, log_cb=lambda s: None)


def test_flash_firmware_invokes_upload_target(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "firmware" / "controller").mkdir(parents=True)
    captured = {}

    def fake_popen(args, **kw):
        captured["args"] = args
        return _FakePopen(["Uploading...\n", "Done.\n"], returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    flash_firmware(wt, log_cb=lambda s: None)
    assert "-t" in captured["args"]
    assert "upload" in captured["args"]


def test_flash_firmware_failure_raises(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "firmware" / "controller").mkdir(parents=True)

    def fake_popen(args, **kw):
        return _FakePopen(["No Teensy detected\n"], returncode=1)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    with pytest.raises(FlashFailed):
        flash_firmware(wt, log_cb=lambda s: None)
