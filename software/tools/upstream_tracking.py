#!/usr/bin/env python3
"""
Upstream commit tracking library for porting commits from upstream/master.

This library provides functions for the upstream-check skill to:
- Fetch and discover upstream commits
- Maintain the canonical upstream-status.yaml file
- Verify consistency between YAML and git history
- Generate summaries and reports

Usage:
    python tools/upstream_tracking.py verify
    python tools/upstream_tracking.py summary
    python tools/upstream_tracking.py add-pending
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import yaml

# Paths relative to repository root
REPO_ROOT = Path(__file__).parent.parent.parent
STATUS_FILE = REPO_ROOT / "conductor" / "tracks" / "upstream-port" / "upstream-status.yaml"
COMMITS_DIR = REPO_ROOT / "conductor" / "tracks" / "upstream-port" / "commits"

# Configuration
DEFAULT_SOURCE_BRANCH = "upstream/master"
DEFAULT_TARGET_BRANCH = "HEAD"
STALE_IN_PROGRESS_DAYS = 7

# Cutoff date: commits before this date are from before the arch_v2 divergence
# and should not be tracked. This is the date of the oldest commit that was
# semantically ported to arch_v2.
CUTOFF_DATE = "2025-12-13"

Status = Literal["ported", "skipped", "pending", "in-progress"]
SkipReason = Literal["not-applicable", "superseded", "already-fixed", "deferred"]


@dataclass
class CommitInfo:
    """Information about an upstream commit."""

    hash: str  # Short hash (7 chars)
    full_hash: str  # Full hash (40 chars)
    title: str
    date: str  # ISO date


@dataclass
class StatusEntry:
    """Entry in the upstream status file."""

    hash: str
    title: str
    status: Status
    # For ported commits
    ported_in: str | None = None
    ported_date: str | None = None
    ported_with: list[str] = field(default_factory=list)
    # For skipped commits
    skip_reason: SkipReason | None = None
    skip_justification: str | None = None
    skip_date: str | None = None
    # For in-progress
    started_date: str | None = None
    # Common fields
    category: str | None = None
    priority: str | None = None
    estimated_effort: str | None = None
    notes: str | None = None
    analysis_file: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        d: dict[str, str | list[str] | None] = {"title": self.title, "status": self.status}

        if self.status == "ported":
            if self.ported_in:
                d["ported_in"] = self.ported_in
            if self.ported_date:
                d["ported_date"] = self.ported_date
            if self.ported_with:
                d["ported_with"] = self.ported_with

        elif self.status == "skipped":
            if self.skip_reason:
                d["skip_reason"] = self.skip_reason
            if self.skip_justification:
                d["skip_justification"] = self.skip_justification
            if self.skip_date:
                d["skip_date"] = self.skip_date

        elif self.status == "in-progress":
            if self.started_date:
                d["started_date"] = self.started_date

        # Common optional fields
        if self.category:
            d["category"] = self.category
        if self.priority:
            d["priority"] = self.priority
        if self.estimated_effort:
            d["estimated_effort"] = self.estimated_effort
        if self.notes:
            d["notes"] = self.notes
        if self.analysis_file:
            d["analysis_file"] = self.analysis_file

        return d

    @classmethod
    def from_dict(cls, hash: str, d: dict) -> StatusEntry:
        """Create from dictionary loaded from YAML."""
        return cls(
            hash=hash,
            title=d.get("title", ""),
            status=d.get("status", "pending"),
            ported_in=d.get("ported_in"),
            ported_date=d.get("ported_date"),
            ported_with=d.get("ported_with", []),
            skip_reason=d.get("skip_reason"),
            skip_justification=d.get("skip_justification"),
            skip_date=d.get("skip_date"),
            started_date=d.get("started_date"),
            category=d.get("category"),
            priority=d.get("priority"),
            estimated_effort=d.get("estimated_effort"),
            notes=d.get("notes"),
            analysis_file=d.get("analysis_file"),
        )


@dataclass
class StatusFile:
    """The upstream status file contents."""

    source_branch: str = DEFAULT_SOURCE_BRANCH
    target_branch: str = "multipoint-refactor"
    cutoff_date: str = CUTOFF_DATE  # Commits before this are pre-divergence
    created: str = ""
    last_verified: str = ""
    commits: dict[str, StatusEntry] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        return {
            "meta": {
                "source_branch": self.source_branch,
                "target_branch": self.target_branch,
                "cutoff_date": self.cutoff_date,
                "created": self.created,
                "last_verified": self.last_verified,
            },
            "commits": {h: e.to_dict() for h, e in self.commits.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> StatusFile:
        """Create from dictionary loaded from YAML."""
        meta = d.get("meta", {})
        commits_dict = d.get("commits", {})
        commits = {h: StatusEntry.from_dict(h, v) for h, v in commits_dict.items()}
        return cls(
            source_branch=meta.get("source_branch", DEFAULT_SOURCE_BRANCH),
            target_branch=meta.get("target_branch", "multipoint-refactor"),
            cutoff_date=meta.get("cutoff_date", CUTOFF_DATE),
            created=meta.get("created", ""),
            last_verified=meta.get("last_verified", ""),
            commits=commits,
        )


@dataclass
class VerificationResult:
    """Result of consistency verification."""

    missing_from_status: list[CommitInfo] = field(default_factory=list)
    ported_not_in_git: list[tuple[str, str]] = field(default_factory=list)  # (upstream, our_commit)
    git_not_in_status: list[tuple[str, str]] = field(default_factory=list)  # (our_commit, upstream)
    skipped_no_justification: list[str] = field(default_factory=list)
    stale_in_progress: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """Return True if no issues found."""
        return not any(
            [
                self.missing_from_status,
                self.ported_not_in_git,
                self.git_not_in_status,
                self.skipped_no_justification,
                self.stale_in_progress,
            ]
        )


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Git command failed: git {' '.join(args)}\n{result.stderr}")
    return result


def fetch_upstream(remote: str = "upstream") -> None:
    """Fetch the upstream remote."""
    run_git("fetch", remote, "master")


def get_upstream_commits(
    source: str = DEFAULT_SOURCE_BRANCH,
    target: str = DEFAULT_TARGET_BRANCH,
    cutoff_date: str | None = CUTOFF_DATE,
) -> list[CommitInfo]:
    """Get commits in source branch that are not in target branch.

    Args:
        source: Source branch to check for commits
        target: Target branch to compare against
        cutoff_date: Only include commits on or after this date (YYYY-MM-DD).
                     Commits before this date are from before the arch_v2 divergence.
    """
    args = [
        "log",
        "--format=%H %h %ad %s",
        "--date=short",
    ]

    # Add date filter if cutoff specified
    if cutoff_date:
        args.extend(["--since", cutoff_date])

    args.extend([source, "--not", target])

    result = run_git(*args)

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(" ", 3)
        if len(parts) >= 4:
            full_hash, short_hash, commit_date, title = parts
            commits.append(
                CommitInfo(
                    hash=short_hash,
                    full_hash=full_hash,
                    title=title,
                    date=commit_date,
                )
            )
    return commits


def get_ported_commits_from_git(target: str = DEFAULT_TARGET_BRANCH) -> dict[str, str]:
    """
    Parse Ports-Upstream trailers from git log.

    Returns: dict mapping upstream hash -> our commit hash
    """
    # Get commits with their trailers
    result = run_git(
        "log",
        "--format=%H%n%(trailers:key=Ports-Upstream,valueonly)",
        target,
    )

    ported = {}
    lines = result.stdout.strip().split("\n")
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue

        our_commit = lines[i][:7]  # Short hash
        i += 1

        # Collect all trailers for this commit
        while i < len(lines) and lines[i] and not re.match(r"^[0-9a-f]{40}$", lines[i]):
            upstream_hash = lines[i].strip()[:7]
            if upstream_hash:
                ported[upstream_hash] = our_commit
            i += 1

    return ported


def get_skipped_commits_from_git(target: str = DEFAULT_TARGET_BRANCH) -> dict[str, str]:
    """
    Parse Skips-Upstream trailers from git log.

    Returns: dict mapping upstream hash -> our commit hash where skip was documented
    """
    result = run_git(
        "log",
        "--format=%H%n%(trailers:key=Skips-Upstream,valueonly)",
        target,
    )

    skipped = {}
    lines = result.stdout.strip().split("\n")
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue

        our_commit = lines[i][:7]
        i += 1

        while i < len(lines) and lines[i] and not re.match(r"^[0-9a-f]{40}$", lines[i]):
            upstream_hash = lines[i].strip()[:7]
            if upstream_hash:
                skipped[upstream_hash] = our_commit
            i += 1

    return skipped


def get_ported_from_parenthetical(target: str = DEFAULT_TARGET_BRANCH) -> dict[str, str]:
    """
    Parse parenthetical upstream references from commit titles.
    E.g., "feat: Port X from upstream (abc123, def456)"

    Returns: dict mapping upstream hash -> our commit hash
    """
    result = run_git("log", "--format=%H %s", target)

    ported = {}
    # Match patterns like (abc123) or (abc123, def456)
    pattern = re.compile(r"\(([0-9a-f]{7}(?:,\s*[0-9a-f]{7})*)\)")

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        our_commit, title = parts[0][:7], parts[1]

        matches = pattern.findall(title)
        for match in matches:
            for upstream_hash in re.findall(r"[0-9a-f]{7}", match):
                ported[upstream_hash] = our_commit

    return ported


def load_status() -> StatusFile:
    """Load the upstream status file."""
    if not STATUS_FILE.exists():
        return StatusFile(created=date.today().isoformat())

    with open(STATUS_FILE) as f:
        data = yaml.safe_load(f) or {}

    return StatusFile.from_dict(data)


def save_status(status: StatusFile) -> None:
    """Save the upstream status file."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Update last_verified timestamp
    status.last_verified = datetime.now().isoformat(timespec="seconds")

    with open(STATUS_FILE, "w") as f:
        yaml.dump(
            status.to_dict(),
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )


def add_pending_commits(
    status: StatusFile,
    source: str = DEFAULT_SOURCE_BRANCH,
    target: str = DEFAULT_TARGET_BRANCH,
) -> list[CommitInfo]:
    """
    Add new upstream commits as pending.

    Returns: list of newly added commits
    """
    upstream_commits = get_upstream_commits(source, target)
    added = []

    for commit in upstream_commits:
        if commit.hash not in status.commits:
            status.commits[commit.hash] = StatusEntry(
                hash=commit.hash,
                title=commit.title,
                status="pending",
            )
            added.append(commit)

    return added


def mark_ported(
    status: StatusFile,
    upstream_hash: str,
    our_hash: str,
    notes: str | None = None,
    ported_with: list[str] | None = None,
) -> None:
    """Mark an upstream commit as ported."""
    if upstream_hash not in status.commits:
        raise ValueError(f"Commit {upstream_hash} not found in status file")

    entry = status.commits[upstream_hash]
    entry.status = "ported"
    entry.ported_in = our_hash
    entry.ported_date = date.today().isoformat()
    if notes:
        entry.notes = notes
    if ported_with:
        entry.ported_with = ported_with


def mark_skipped(
    status: StatusFile,
    upstream_hash: str,
    reason: SkipReason,
    justification: str,
) -> None:
    """Mark an upstream commit as skipped."""
    if upstream_hash not in status.commits:
        raise ValueError(f"Commit {upstream_hash} not found in status file")

    entry = status.commits[upstream_hash]
    entry.status = "skipped"
    entry.skip_reason = reason
    entry.skip_justification = justification
    entry.skip_date = date.today().isoformat()


def mark_in_progress(status: StatusFile, upstream_hash: str) -> None:
    """Mark an upstream commit as in-progress."""
    if upstream_hash not in status.commits:
        raise ValueError(f"Commit {upstream_hash} not found in status file")

    entry = status.commits[upstream_hash]
    entry.status = "in-progress"
    entry.started_date = date.today().isoformat()


def verify_consistency(
    status: StatusFile,
    source: str = DEFAULT_SOURCE_BRANCH,
    target: str = DEFAULT_TARGET_BRANCH,
) -> VerificationResult:
    """
    Verify consistency between status file and git history.
    """
    result = VerificationResult()

    # Get all upstream commits
    upstream_commits = get_upstream_commits(source, target)

    # Get ported commits from git (trailers + parenthetical)
    git_ported_trailers = get_ported_commits_from_git(target)
    git_ported_parens = get_ported_from_parenthetical(target)
    git_ported = {**git_ported_parens, **git_ported_trailers}  # Trailers take precedence

    # Get skipped commits from git
    git_skipped = get_skipped_commits_from_git(target)

    # Check: upstream commits missing from status
    for commit in upstream_commits:
        if commit.hash not in status.commits:
            result.missing_from_status.append(commit)

    # Check: ported commits match git
    for hash_, entry in status.commits.items():
        if entry.status == "ported":
            if hash_ not in git_ported:
                result.ported_not_in_git.append((hash_, entry.ported_in or "unknown"))

    # Check: git has port trailers not in status
    for upstream_hash, our_commit in git_ported.items():
        if upstream_hash in status.commits:
            entry = status.commits[upstream_hash]
            if entry.status != "ported":
                result.git_not_in_status.append((our_commit, upstream_hash))

    # Check: skipped commits have justification
    for hash_, entry in status.commits.items():
        if entry.status == "skipped" and not entry.skip_justification:
            result.skipped_no_justification.append(hash_)

    # Check: git has skip trailers - update status if needed (informational)
    # git_skipped is available if we need to cross-reference skip trailers
    _ = git_skipped  # Reserved for future use

    # Check: stale in-progress
    today = date.today()
    for hash_, entry in status.commits.items():
        if entry.status == "in-progress" and entry.started_date:
            started = date.fromisoformat(entry.started_date)
            if (today - started).days > STALE_IN_PROGRESS_DAYS:
                result.stale_in_progress.append(hash_)

    return result


def get_summary(status: StatusFile) -> dict[str, int]:
    """Get summary counts by status."""
    counts: dict[str, int] = {"ported": 0, "skipped": 0, "pending": 0, "in-progress": 0}
    for entry in status.commits.values():
        counts[entry.status] = counts.get(entry.status, 0) + 1
    return counts


def print_summary(status: StatusFile) -> None:
    """Print a summary of the status file."""
    counts = get_summary(status)
    total = sum(counts.values())

    print("=" * 60)
    print("UPSTREAM TRACKING SUMMARY")
    print("=" * 60)
    print(f"Source: {status.source_branch}")
    print(f"Target: {status.target_branch}")
    print(f"Last verified: {status.last_verified or 'never'}")
    print()
    print(f"Total commits tracked: {total}")
    print(f"  Ported:      {counts['ported']}")
    print(f"  Skipped:     {counts['skipped']}")
    print(f"  Pending:     {counts['pending']}")
    print(f"  In-progress: {counts['in-progress']}")
    print("=" * 60)


def print_verification_report(result: VerificationResult, status: StatusFile) -> None:
    """Print a verification report."""
    print("=" * 70)
    print("UPSTREAM TRACKING VERIFICATION REPORT")
    print("=" * 70)
    print(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    print()

    counts = get_summary(status)
    print("SUMMARY")
    print("-" * 70)
    print(f"Ported:      {counts['ported']}")
    print(f"Skipped:     {counts['skipped']}")
    print(f"Pending:     {counts['pending']}")
    print(f"In-progress: {counts['in-progress']}")
    print()

    if result.is_clean:
        print("STATUS: ALL CHECKS PASSED")
        print("=" * 70)
        return

    print("STATUS: ISSUES DETECTED")
    print()

    if result.missing_from_status:
        print("-" * 70)
        print("MISSING FROM STATUS FILE")
        print("-" * 70)
        print("These upstream commits have no entry in upstream-status.yaml:\n")
        for commit in result.missing_from_status:
            print(f"  {commit.hash}  {commit.title[:50]}")
        print("\nACTION: Run 'python tools/upstream_tracking.py add-pending'")
        print()

    if result.ported_not_in_git:
        print("-" * 70)
        print("PORTED BUT NO GIT TRAILER")
        print("-" * 70)
        print("These commits are marked 'ported' but no Ports-Upstream trailer found:\n")
        for upstream, ours in result.ported_not_in_git:
            entry = status.commits.get(upstream)
            title = entry.title[:40] if entry else "unknown"
            print(f"  {upstream} -> {ours}  ({title})")
        print("\nACTION: Amend commits or create documentation commit with trailers")
        print()

    if result.git_not_in_status:
        print("-" * 70)
        print("GIT TRAILER BUT STATUS DISAGREES")
        print("-" * 70)
        print("These commits have Ports-Upstream trailers but status file says not ported:\n")
        for ours, upstream in result.git_not_in_status:
            print(f"  {ours} ports {upstream}")
        print("\nACTION: Update status file to mark as ported")
        print()

    if result.skipped_no_justification:
        print("-" * 70)
        print("SKIPPED WITHOUT JUSTIFICATION")
        print("-" * 70)
        print("These commits are marked 'skipped' but lack skip_justification:\n")
        for hash_ in result.skipped_no_justification:
            entry = status.commits.get(hash_)
            title = entry.title[:50] if entry else "unknown"
            print(f"  {hash_}  {title}")
        print("\nACTION: Add skip_justification to these entries")
        print()

    if result.stale_in_progress:
        print("-" * 70)
        print("STALE IN-PROGRESS")
        print("-" * 70)
        print(f"These commits have been in-progress for more than {STALE_IN_PROGRESS_DAYS} days:\n")
        for hash_ in result.stale_in_progress:
            entry = status.commits.get(hash_)
            title = entry.title[:50] if entry else "unknown"
            started = entry.started_date if entry else "unknown"
            print(f"  {hash_}  {title}  (started: {started})")
        print("\nACTION: Complete or reset these commits")
        print()

    print("=" * 70)


def cmd_verify(args: argparse.Namespace) -> int:
    """Run verification and print report."""
    status = load_status()

    # Fetch if requested
    if args.fetch:
        print("Fetching upstream...")
        fetch_upstream()

    result = verify_consistency(status, status.source_branch, status.target_branch)
    print_verification_report(result, status)

    # Update timestamp
    save_status(status)

    return 0 if result.is_clean else 1


def cmd_summary(_args: argparse.Namespace) -> int:
    """Print summary."""
    status = load_status()
    print_summary(status)
    return 0


def cmd_add_pending(args: argparse.Namespace) -> int:
    """Add new upstream commits as pending."""
    status = load_status()

    # Fetch if requested
    if args.fetch:
        print("Fetching upstream...")
        fetch_upstream()

    added = add_pending_commits(status, status.source_branch, status.target_branch)

    if added:
        print(f"Added {len(added)} new commits as pending:")
        for commit in added:
            print(f"  {commit.hash}  {commit.title[:50]}")
        save_status(status)
    else:
        print("No new commits to add.")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List commits by status."""
    status = load_status()

    filter_status = args.status if args.status != "all" else None

    for hash_, entry in status.commits.items():
        if filter_status and entry.status != filter_status:
            continue
        status_marker = {
            "ported": "[+]",
            "skipped": "[-]",
            "pending": "[ ]",
            "in-progress": "[~]",
        }.get(entry.status, "[?]")
        print(f"{status_marker} {hash_}  {entry.title[:55]}")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Upstream commit tracking tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify tracking consistency")
    verify_parser.add_argument("--fetch", action="store_true", help="Fetch upstream before verifying")
    verify_parser.set_defaults(func=cmd_verify)

    # summary command
    summary_parser = subparsers.add_parser("summary", help="Show tracking summary")
    summary_parser.set_defaults(func=cmd_summary)

    # add-pending command
    add_parser = subparsers.add_parser("add-pending", help="Add new upstream commits as pending")
    add_parser.add_argument("--fetch", action="store_true", help="Fetch upstream first")
    add_parser.set_defaults(func=cmd_add_pending)

    # list command
    list_parser = subparsers.add_parser("list", help="List commits")
    list_parser.add_argument(
        "--status",
        choices=["all", "ported", "skipped", "pending", "in-progress"],
        default="all",
        help="Filter by status",
    )
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
