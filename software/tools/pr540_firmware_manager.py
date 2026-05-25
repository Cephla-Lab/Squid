"""PR #540 firmware manager.

Pure logic. No Qt imports. Resolves git refs, manages firmware worktrees, runs PlatformIO.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


LogCallback = Callable[[str], None]


class WorktreeMismatch(Exception):
    def __init__(self, path: Path, current_sha: str, expected_sha: str):
        super().__init__(f"Worktree at {path} is at {current_sha[:10]} but ref resolves to {expected_sha[:10]}")
        self.path = path
        self.current_sha = current_sha
        self.expected_sha = expected_sha


class BuildFailed(Exception):
    pass


class FlashFailed(Exception):
    pass


class PlatformIONotFound(Exception):
    pass


@dataclass
class FirmwareRef:
    label: str
    git_ref: str
    worktree_path: Path


def _sanitize_ref_for_path(ref: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", ref)
    return safe


POST_FIX = FirmwareRef(
    label="post-fix (origin/master)",
    git_ref="origin/master",
    worktree_path=Path("worktrees/.pr540-fw-master"),
)

PRE_FIX = FirmwareRef(
    label="pre-fix (fae3aa0a)",
    git_ref="fae3aa0a",
    worktree_path=Path("worktrees/.pr540-fw-fae3aa0a"),
)


def custom_ref(git_ref: str) -> FirmwareRef:
    sanitized = _sanitize_ref_for_path(git_ref)
    return FirmwareRef(
        label=f"custom ({git_ref})",
        git_ref=git_ref,
        worktree_path=Path(f"worktrees/.pr540-fw-{sanitized}"),
    )


def resolve_ref(git_ref: str, cwd: Optional[Path] = None) -> str:
    """Return full SHA for a git ref. Raises ValueError if unknown."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--verify", git_ref + "^{commit}"],
            cwd=cwd,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        raise ValueError(f"Unknown git ref {git_ref!r}: {e.stderr.decode(errors='replace').strip()}") from e
    return out.decode().strip()


def _worktree_head_sha(path: Path) -> str:
    out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path)
    return out.decode().strip()


def ensure_worktree(ref: FirmwareRef, *, allow_reset: bool, log_cb: LogCallback) -> Path:
    """Ensure a worktree exists at ref.worktree_path checked out to ref.git_ref.

    - If the path does not exist: ``git worktree add <path> <ref>``.
    - If the path exists at the same SHA: reuse.
    - If the path exists at a different SHA and allow_reset=True: ``git -C <path> reset --hard <ref>``.
    - If the path exists at a different SHA and allow_reset=False: raise WorktreeMismatch.
    """
    expected = resolve_ref(ref.git_ref)
    path = ref.worktree_path

    if not path.exists():
        log_cb(f"[firmware] creating worktree at {path} from {ref.git_ref} ({expected[:10]})")
        subprocess.check_call(["git", "worktree", "add", str(path), ref.git_ref])
        return path

    current = _worktree_head_sha(path)
    if current == expected:
        log_cb(f"[firmware] worktree at {path} already at {expected[:10]} — reusing")
        return path

    if not allow_reset:
        raise WorktreeMismatch(path=path, current_sha=current, expected_sha=expected)

    log_cb(f"[firmware] resetting worktree at {path}: {current[:10]} -> {expected[:10]}")
    subprocess.check_call(["git", "-C", str(path), "fetch", "origin"])
    subprocess.check_call(["git", "-C", str(path), "reset", "--hard", expected])
    return path
