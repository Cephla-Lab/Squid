#!/usr/bin/env python3
"""Move or copy a running (or finished) Squid acquisition to a mounted destination.

Large acquisitions can outgrow the acquisition disk. Squid's "large acquisition mode"
appends a transfer manifest (``transfer_manifest.jsonl``) that says which files are
finished and therefore safe to take away while the run continues; this tool consumes
that manifest and streams the finished data to a NAS share (or any mounted path).

Usage
-----
    upload_acquisition.py <experiment_dir> <destination_dir> [--mode copy|move] [--follow]
                          [--checksum] [--quiesce-s 30] [--poll-s 2] [--dry-run]
                          [--log-level LEVEL]
    upload_acquisition.py verify <experiment_dir> <destination_dir>

See docs/transfer-manifest.md (the file format) and docs/upload-acquisition.md (usage).

Exit codes: 0 success, 1 transfer failures or verification problems, 2 nothing was
movable yet and the run is still in progress.

Standard library only, Python 3.10+: this file is meant to be copied onto a transfer
box that has no Squid checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

MANIFEST_NAME = "transfer_manifest.jsonl"  # control.core.transfer_manifest.MANIFEST_FILE_NAME
SUPPORTED_SCHEMA = 1  # control.core.transfer_manifest.SCHEMA_VERSION
DONE_MARKER = ".done"
PARTIAL_SUFFIX = ".partial"
COORDINATES_NAME = "coordinates.csv"
# Timepoint folders are the timepoint index formatted with FILE_ID_PADDING, which is
# configurable: "00000" with the usual padding of 5, but plain "0", "1", "2" when it is 0
# (as in the CI configuration). Match any all-digit name and order them numerically.
TIMEPOINT_RE = re.compile(r"^\d+$")

DEFAULT_QUIESCE_S = 30.0
DEFAULT_POLL_S = 2.0
_COPY_CHUNK_BYTES = 4 * 1024 * 1024

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_NOTHING_MOVABLE = 2

_SOFTWARE_DIR = Path(__file__).resolve().parent.parent

log = logging.getLogger("upload_acquisition")


# --------------------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One movable unit: a file, or a directory whose whole subtree is movable."""

    path: str  # relative to the experiment dir, POSIX separators
    kind: str = "file"  # "file" or "dir"
    bytes: Optional[int] = None  # as reported by the manifest; None for directories


@dataclass
class FileResult:
    status: str  # "transferred", "skipped" or "failed"
    bytes: int = 0
    error: Optional[str] = None


@dataclass
class Summary:
    entries: int = 0
    files: int = 0
    bytes: int = 0
    skipped: int = 0
    failed: int = 0
    failures: List[str] = field(default_factory=list)
    finished: bool = False  # the post-end sweep completed; nothing is left to transfer

    def record(self, rel_path: str, result: FileResult) -> None:
        if result.status == "transferred":
            self.files += 1
            self.bytes += result.bytes
        elif result.status == "skipped":
            self.skipped += 1
        else:
            self.failed += 1
            self.failures.append(f"{rel_path}: {result.error}")

    def merge(self, other: "Summary") -> None:
        self.entries += other.entries
        self.files += other.files
        self.bytes += other.bytes
        self.skipped += other.skipped
        self.failed += other.failed
        self.failures.extend(other.failures)
        self.finished = self.finished or other.finished


# --------------------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------------------


def load_manifest(manifest_path) -> List[dict]:
    """Read ``transfer_manifest.jsonl`` into a list of events (tolerant of a truncated tail)."""
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        return []

    # Prefer Squid's own reader so the tool cannot drift from the writer. The import is
    # lazy *and* falls back to the inline reader below because this script is meant to be
    # copied onto a transfer/NAS box that has no Squid checkout (and importing `control`
    # drags in machine config). The JSONL format is a documented contract
    # (docs/transfer-manifest.md), so a self-contained reader is safe here; this is the
    # only fallback in the tool.
    read_manifest = _import_read_manifest()
    if read_manifest is None:
        events = _read_manifest_fallback(manifest_path)
    else:
        try:
            events = read_manifest(str(manifest_path))
        except Exception as exc:
            # Squid's reader raises on a malformed line anywhere but the end. One corrupt
            # line must not strand a whole acquisition on a full disk, so fall back to the
            # line-skipping reader and say so loudly.
            log.warning("control.core.transfer_manifest.read_manifest failed (%s); using the inline reader", exc)
            events = _read_manifest_fallback(manifest_path)
    return [event for event in events if isinstance(event, dict)]


def check_schema(manifest: Sequence[dict]) -> None:
    """Warn when the manifest was written by a newer Squid than this tool understands."""
    for event in manifest:
        if event.get("event") != "start":
            continue
        schema = event.get("schema")
        if isinstance(schema, int) and schema > SUPPORTED_SCHEMA:
            log.warning(
                "manifest schema %d is newer than this tool understands (%d); "
                "entry semantics may have changed - check docs/transfer-manifest.md",
                schema,
                SUPPORTED_SCHEMA,
            )
        return


def _import_read_manifest():
    if str(_SOFTWARE_DIR) not in sys.path:
        sys.path.append(str(_SOFTWARE_DIR))
    try:
        from control.core.transfer_manifest import read_manifest

        return read_manifest
    except Exception as exc:
        log.debug("no Squid manifest reader available (%s); using the inline reader", exc)
        return None


def _read_manifest_fallback(manifest_path: Path) -> List[dict]:
    with open(manifest_path, "r", encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()

    events: List[dict] = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                log.debug("ignoring truncated last manifest line")
            else:
                log.warning("skipping unparsable manifest line %d of %s", index + 1, manifest_path)
    return events


def manifest_end(manifest: Sequence[dict]) -> Optional[dict]:
    """The ``end`` event, if the writer got that far."""
    for event in reversed(list(manifest)):
        if event.get("event") == "end":
            return event
    return None


def fully_listed_timepoints(manifest: Sequence[dict]) -> List[int]:
    """Timepoints whose ``timepoint_done`` record has been written, in order.

    The writer emits ``timepoint_done`` after every ``complete`` record of that timepoint,
    so a mover may treat these timepoints as fully listed: no further ``complete`` record
    will name a file belonging to them. Presence is best-effort (a crashed writer may skip
    one), so this is progress information, never a precondition for moving anything.
    """
    timepoints: List[int] = []
    for event in manifest:
        if event.get("event") != "timepoint_done":
            continue
        t = event.get("t")
        if isinstance(t, int) and t not in timepoints:
            timepoints.append(t)
    return timepoints


def _safe_parts(rel_path: str) -> Optional[Tuple[str, ...]]:
    """Split a manifest path into components, refusing anything that escapes the experiment dir.

    Manifest paths are POSIX-relative; joining them on Windows works because we join the
    components ourselves rather than trusting the string.
    """
    if not isinstance(rel_path, str) or not rel_path or rel_path.startswith("/") or "\\" in rel_path:
        return None
    parts = tuple(part for part in rel_path.split("/") if part not in ("", "."))
    if not parts or any(part == ".." or part.endswith(":") for part in parts):
        return None
    return parts


def movable_entries(experiment_dir, manifest: Sequence[dict], now: Optional[float] = None) -> List[Entry]:
    """The ``complete`` entries that are present on disk and therefore movable right now.

    ``now`` is accepted so a caller can drive every stage of a pass off one clock; manifest
    entries need no quiescence check, because ``complete`` already means "the writer is done
    with this path".
    """
    del now  # see docstring
    experiment_dir = Path(experiment_dir)
    entries: List[Entry] = []
    seen = set()
    for event in manifest:
        if event.get("event") != "complete":
            continue
        parts = _safe_parts(event.get("path"))
        if parts is None:
            log.warning("refusing manifest path outside the experiment dir: %r", event.get("path"))
            continue
        rel = "/".join(parts)
        if rel in seen:
            continue
        seen.add(rel)
        if not experiment_dir.joinpath(*parts).exists():
            log.debug("manifest entry no longer on disk (already moved?): %s", rel)
            continue
        kind = "dir" if event.get("kind") == "dir" else "file"
        entries.append(Entry(path=rel, kind=kind, bytes=event.get("bytes")))
    return entries


# --------------------------------------------------------------------------------------
# legacy (no manifest) sources of truth
# --------------------------------------------------------------------------------------


def timepoint_dirs(parent) -> List[Path]:
    """The timepoint folders directly under ``parent``, oldest first (numeric, not lexical).

    Lexical order would put "10" before "2" whenever FILE_ID_PADDING is small.
    """
    parent = Path(parent)
    if not parent.is_dir():
        return []
    found = [child for child in parent.iterdir() if child.is_dir() and TIMEPOINT_RE.match(child.name)]
    return sorted(found, key=lambda child: (int(child.name), child.name))


def legacy_movable(experiment_dir, quiesce_s: float = DEFAULT_QUIESCE_S, now: Optional[float] = None) -> List[Entry]:
    """Nothing is movable mid-run without a manifest.

    A timepoint folder's ``.done`` marker only says the worker finished *imaging* that timepoint;
    its save jobs run asynchronously and can still be writing (or stalled) long after, and a quiet
    period cannot prove they finished. Pre-manifest runs are therefore uploaded once the root
    ``.done`` marks the acquisition finished (see ``finish_after_end``).
    """
    pending = [child.name for child in timepoint_dirs(experiment_dir) if (child / DONE_MARKER).exists()]
    if pending:
        log.debug(
            "timepoints %s are imaged but their saves cannot be confirmed without a manifest; waiting for the root %s",
            ", ".join(pending),
            DONE_MARKER,
        )
    return []


def has_root_done(experiment_dir) -> bool:
    return (Path(experiment_dir) / DONE_MARKER).exists()


def is_quiescent(path, quiesce_s: float = DEFAULT_QUIESCE_S, now: Optional[float] = None) -> bool:
    """True when nothing under ``path`` has been modified within ``quiesce_s`` seconds."""
    now = time.time() if now is None else now
    newest = _newest_mtime(Path(path))
    if newest is None:
        return True
    return (now - newest) >= quiesce_s


def _newest_mtime(path: Path) -> Optional[float]:
    newest: Optional[float] = None

    def consider(candidate: str) -> None:
        nonlocal newest
        try:
            mtime = os.stat(candidate).st_mtime
        except OSError:
            return
        if newest is None or mtime > newest:
            newest = mtime

    if not path.exists():
        return None
    consider(str(path))
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            consider(os.path.join(root, name))
    return newest


# --------------------------------------------------------------------------------------
# transfers
# --------------------------------------------------------------------------------------


def destination_experiment_dir(experiment_dir, destination_dir) -> Path:
    """``dest/<experiment name>`` - the experiment folder name is appended to the destination."""
    return Path(destination_dir) / Path(experiment_dir).name


def ensure_disjoint(experiment_dir, dest_dir) -> None:
    """Refuse a destination that is, contains, or lies inside the experiment folder.

    ``upload_acquisition.py /data/exp /data`` would otherwise compute ``/data/exp`` as the
    destination: every file "matches" itself and move mode deletes the acquisition. Resolving
    both paths also catches symlinked aliases of the same tree.
    """
    src = Path(experiment_dir).resolve()
    dst = Path(dest_dir).resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        raise ValueError(f"destination {dst} overlaps the experiment folder {src}; choose a disjoint destination")


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_COPY_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _copy_and_digest(src: Path, dst: Path, checksum: bool = False) -> Optional[str]:
    """Copy ``src`` to ``dst``, fsync it, and return the sha256 of the bytes copied."""
    hasher = hashlib.sha256() if checksum else None
    with open(src, "rb") as source, open(dst, "wb") as target:
        while True:
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            target.write(chunk)
            if hasher is not None:
                hasher.update(chunk)
        target.flush()
        os.fsync(target.fileno())
    return hasher.hexdigest() if hasher is not None else None


def _destination_matches(src: Path, dst: Path, src_stat: os.stat_result, checksum: bool) -> bool:
    try:
        dst_stat = dst.stat()
    except OSError:
        return False
    if dst_stat.st_size != src_stat.st_size:
        return False
    if checksum and _sha256(dst) != _sha256(src):
        return False
    return True


def transfer_file(src, dst, mode: str = "copy", checksum: bool = False, dry_run: bool = False) -> FileResult:
    """Copy one file through ``<dst>.partial`` and rename it into place; unlink the source in move mode.

    A failure never removes the source and never leaves a half-written file at the
    destination, so the entry can simply be retried on the next pass.
    """
    src = Path(src)
    dst = Path(dst)
    try:
        src_stat = src.stat()
    except OSError as exc:
        return FileResult("failed", 0, f"source unreadable: {exc}")

    partial = dst.with_name(dst.name + PARTIAL_SUFFIX)
    try:
        if dst.exists() and os.path.samefile(src, dst):
            return FileResult("failed", 0, "source and destination are the same file")
        if _destination_matches(src, dst, src_stat, checksum):
            if mode == "move" and not dry_run:
                src.unlink()
            log.debug("already at the destination, skipping: %s", dst)
            return FileResult("skipped", src_stat.st_size)

        if dry_run:
            log.info("[dry-run] would %s %s -> %s (%d bytes)", mode, src, dst, src_stat.st_size)
            return FileResult("transferred", src_stat.st_size)

        dst.parent.mkdir(parents=True, exist_ok=True)
        digest = _copy_and_digest(src, partial, checksum)
        written = partial.stat().st_size
        if written != src_stat.st_size:
            raise OSError(f"size mismatch after copy: wrote {written}, source is {src_stat.st_size}")
        if checksum and _sha256(partial) != digest:
            raise OSError("checksum mismatch after copy")
        os.utime(partial, (src_stat.st_atime, src_stat.st_mtime))
        os.replace(partial, dst)
        if mode == "move":
            src.unlink()
        return FileResult("transferred", src_stat.st_size)
    except Exception as exc:
        try:
            partial.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            log.error("could not remove %s: %s (original error: %s)", partial, cleanup_exc, exc)
        log.error("transfer failed for %s: %s", src, exc)
        return FileResult("failed", 0, str(exc))


def transfer_entry(
    experiment_dir,
    dest_dir,
    entry: Entry,
    mode: str = "copy",
    checksum: bool = False,
    dry_run: bool = False,
    remove_empty_dirs: bool = False,
) -> Summary:
    """Transfer one manifest/legacy entry; directory units are walked file by file."""
    experiment_dir = Path(experiment_dir)
    dest_dir = Path(dest_dir)
    summary = Summary(entries=1)

    parts = _safe_parts(entry.path)
    if parts is None:
        summary.failed += 1
        summary.failures.append(f"{entry.path}: path escapes the experiment directory")
        return summary

    source = experiment_dir.joinpath(*parts)
    target = dest_dir.joinpath(*parts)

    if entry.kind == "dir" and source.is_dir():
        for src_file in sorted(p for p in source.rglob("*") if p.is_file()):
            rel = src_file.relative_to(source)
            result = transfer_file(src_file, target.joinpath(*rel.parts), mode, checksum=checksum, dry_run=dry_run)
            summary.record(f"{entry.path}/{rel.as_posix()}", result)
        # Never delete a directory before `end`: only the post-end sweep passes
        # remove_empty_dirs, because the writer may still create files in this subtree.
        if mode == "move" and remove_empty_dirs and not dry_run and summary.failed == 0:
            _remove_empty_dirs(source, remove_root=True)
    else:
        summary.record(entry.path, transfer_file(source, target, mode, checksum=checksum, dry_run=dry_run))
    return summary


def _remove_empty_dirs(root: Path, remove_root: bool = False) -> None:
    if not root.is_dir():
        return
    for current, dirs, _files in os.walk(root, topdown=False):
        for name in dirs:
            try:
                os.rmdir(os.path.join(current, name))
            except OSError:
                pass  # not empty, or vanished
    if remove_root:
        try:
            os.rmdir(root)
        except OSError:
            pass


def finish_after_end(
    experiment_dir,
    dest_dir,
    mode: str = "copy",
    checksum: bool = False,
    quiesce_s: float = DEFAULT_QUIESCE_S,
    now: Optional[float] = None,
    dry_run: bool = False,
) -> Summary:
    """Move everything the manifest never lists, once the folder has gone quiet.

    That is acquisition.log, acquisition parameters.json, configurations.xml, the root
    coordinates.csv, acquisition.yaml, mosaic_view/, zarr plate/well metadata, ``.done``
    markers - and, last of all, the manifest itself.
    """
    experiment_dir = Path(experiment_dir)
    dest_dir = Path(dest_dir)
    summary = Summary()

    if not is_quiescent(experiment_dir, quiesce_s, now):
        log.info("acquisition ended; waiting for %s to be quiet for %.0fs", experiment_dir, quiesce_s)
        return summary

    manifest_src = experiment_dir / MANIFEST_NAME
    remaining = [p for p in sorted(experiment_dir.rglob("*")) if p.is_file() and p != manifest_src]
    for src_file in remaining:
        rel = src_file.relative_to(experiment_dir)
        summary.entries += 1
        result = transfer_file(src_file, dest_dir.joinpath(*rel.parts), mode, checksum=checksum, dry_run=dry_run)
        summary.record(rel.as_posix(), result)

    if summary.failed:
        # The manifest is the resume token: leave it at the source until everything it
        # describes has actually landed.
        log.warning("%d file(s) failed the final sweep; leaving %s in place", summary.failed, MANIFEST_NAME)
        return summary

    if manifest_src.is_file():
        summary.entries += 1
        result = transfer_file(manifest_src, dest_dir / MANIFEST_NAME, mode, checksum=checksum, dry_run=dry_run)
        summary.record(MANIFEST_NAME, result)
        if result.status == "failed":
            return summary

    summary.finished = True
    if mode == "move" and not dry_run:
        _remove_empty_dirs(experiment_dir)  # the experiment folder itself is left behind
    return summary


def run_pass(
    experiment_dir,
    destination_dir,
    mode: str = "copy",
    checksum: bool = False,
    quiesce_s: float = DEFAULT_QUIESCE_S,
    now: Optional[float] = None,
    dry_run: bool = False,
) -> Summary:
    """One sweep: transfer everything that is movable, and finish up if the run has ended."""
    experiment_dir = Path(experiment_dir)
    dest_dir = destination_experiment_dir(experiment_dir, destination_dir)
    ensure_disjoint(experiment_dir, dest_dir)
    summary = Summary()

    manifest_path = experiment_dir / MANIFEST_NAME
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        check_schema(manifest)
        entries = movable_entries(experiment_dir, manifest, now)
        ended = manifest_end(manifest) is not None
        listed = fully_listed_timepoints(manifest)
        if listed:
            log.debug("timepoints fully listed by the manifest: %s", ", ".join(str(t) for t in listed))
    else:
        entries = legacy_movable(experiment_dir, quiesce_s, now)
        ended = has_root_done(experiment_dir)
        if not ended:
            log.info("no manifest and no root %s yet; nothing can be moved safely until the run finishes", DONE_MARKER)

    for entry in entries:
        summary.merge(transfer_entry(experiment_dir, dest_dir, entry, mode, checksum=checksum, dry_run=dry_run))

    if ended:
        summary.merge(
            finish_after_end(
                experiment_dir,
                dest_dir,
                mode,
                checksum=checksum,
                quiesce_s=quiesce_s,
                now=now,
                dry_run=dry_run,
            )
        )

    log.info(
        "%s: %d entries, %d files %s (%.1f MB), %d skipped, %d failed%s",
        experiment_dir.name,
        summary.entries,
        summary.files,
        "copied" if mode == "copy" else "moved",
        summary.bytes / 1e6,
        summary.skipped,
        summary.failed,
        ", run complete" if summary.finished else "",
    )
    for failure in summary.failures:
        log.error("failed: %s", failure)
    return summary


# --------------------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------------------


def verify_destination(experiment_dir, destination_dir) -> List[str]:
    """Check the destination against the manifest and against per-format expectations."""
    experiment_dir = Path(experiment_dir)
    dest_dir = destination_experiment_dir(experiment_dir, destination_dir)
    if not dest_dir.is_dir():
        return [f"destination folder is missing: {dest_dir}"]

    # After a move the manifest lives at the destination; during/after a copy it is still
    # at the source.
    manifest = load_manifest(dest_dir / MANIFEST_NAME) or load_manifest(experiment_dir / MANIFEST_NAME)

    problems: List[str] = []
    if manifest:
        problems.extend(_verify_manifest(dest_dir, manifest))
    problems.extend(_verify_formats(dest_dir))
    return problems


def _verify_manifest(dest_dir: Path, manifest: Sequence[dict]) -> List[str]:
    problems: List[str] = []
    for event in manifest:
        if event.get("event") != "complete":
            continue
        parts = _safe_parts(event.get("path"))
        if parts is None:
            problems.append(f"manifest lists an unusable path: {event.get('path')!r}")
            continue
        rel = "/".join(parts)
        target = dest_dir.joinpath(*parts)
        if event.get("kind") == "dir":
            if not target.is_dir():
                problems.append(f"missing directory: {rel}")
            continue
        if not target.is_file():
            problems.append(f"missing file: {rel}")
            continue
        expected = event.get("bytes")
        if isinstance(expected, int):
            actual = target.stat().st_size
            if actual != expected:
                problems.append(f"size mismatch: {rel} is {actual} bytes, manifest says {expected}")

    if manifest_end(manifest) is None:
        problems.append("manifest has no `end` event: the acquisition or the transfer did not finish")
    return problems


def _verify_formats(dest_dir: Path) -> List[str]:
    problems: List[str] = []
    timepoints = timepoint_dirs(dest_dir)

    # TIFF-based runs: every timepoint folder carries its completion marker and coordinates.
    for timepoint in timepoints:
        if not (timepoint / DONE_MARKER).is_file():
            problems.append(f"{timepoint.name}/: missing {DONE_MARKER} marker")
        if not (timepoint / COORDINATES_NAME).is_file():
            problems.append(f"{timepoint.name}/: missing {COORDINATES_NAME}")

    # OME-TIFF runs: ome_tiff/ at the root or inside each timepoint, and no stray sidecars.
    ome_dirs = [d for d in [dest_dir / "ome_tiff"] + [t / "ome_tiff" for t in timepoints] if d.is_dir()]
    for ome_dir in ome_dirs:
        if not any(p.is_file() for p in ome_dir.iterdir()):
            problems.append(f"{ome_dir.relative_to(dest_dir).as_posix()}/: no OME-TIFF files")
    for stray in sorted(dest_dir.rglob("squid_ome_*_metadata.json")):
        problems.append(f"stray OME-TIFF metadata sidecar: {stray.relative_to(dest_dir).as_posix()}")

    # Zarr runs.
    for store in sorted(dest_dir.rglob("*.zarr")):
        if store.is_dir():
            problems.extend(_verify_zarr_store(store, dest_dir))
    return problems


def _verify_zarr_store(store: Path, dest_dir: Path) -> List[str]:
    problems: List[str] = []
    for zarr_json in sorted(store.rglob("zarr.json")):
        rel = zarr_json.relative_to(dest_dir).as_posix()
        try:
            attributes = json.loads(zarr_json.read_text()).get("attributes") or {}
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            problems.append(f"{rel}: unreadable ({exc})")
            continue
        squid = attributes.get("_squid") or {}
        if "acquisition_complete" in squid and not squid["acquisition_complete"]:
            problems.append(f"{rel}: acquisition_complete is not true")

    if store.name == "plate.ome.zarr":
        if not (store / "zarr.json").is_file():
            problems.append(f"{store.relative_to(dest_dir).as_posix()}/zarr.json: missing plate metadata")
        for row in sorted(p for p in store.iterdir() if p.is_dir()):
            if not (row / "zarr.json").is_file():
                problems.append(f"{row.relative_to(dest_dir).as_posix()}/zarr.json: missing row metadata")
            for well in sorted(p for p in row.iterdir() if p.is_dir()):
                if not (well / "zarr.json").is_file():
                    problems.append(f"{well.relative_to(dest_dir).as_posix()}/zarr.json: missing well metadata")
    return problems


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _configure_logging(level: str) -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
    log.setLevel(getattr(logging, str(level).upper(), logging.INFO))


def _upload_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upload_acquisition.py",
        description="Copy or move a Squid acquisition to a mounted destination, "
        "following the transfer manifest while the run is still going.",
        epilog="Run `upload_acquisition.py verify <experiment_dir> <destination_dir>` afterwards.",
    )
    parser.add_argument("experiment_dir", help="the acquisition folder Squid is writing to")
    parser.add_argument("destination_dir", help="a mounted destination; <experiment name>/ is created inside it")
    parser.add_argument("--mode", choices=("copy", "move"), default="copy", help="default: copy")
    parser.add_argument("--follow", action="store_true", help="keep polling until the acquisition ends")
    parser.add_argument("--checksum", action="store_true", help="verify sha256 as well as size")
    parser.add_argument("--quiesce-s", type=float, default=DEFAULT_QUIESCE_S, dest="quiesce_s")
    parser.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S, dest="poll_s")
    parser.add_argument("--dry-run", action="store_true", help="report what would move, change nothing")
    parser.add_argument("--log-level", default="INFO", dest="log_level")
    return parser


def _verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upload_acquisition.py verify",
        description="Check a transferred acquisition against its manifest and per-format expectations.",
    )
    parser.add_argument("experiment_dir")
    parser.add_argument("destination_dir")
    parser.add_argument("--log-level", default="INFO", dest="log_level")
    return parser


def _run_upload(argv: Sequence[str]) -> int:
    args = _upload_parser().parse_args(list(argv))
    _configure_logging(args.log_level)

    experiment_dir = Path(args.experiment_dir).expanduser()
    destination_dir = Path(args.destination_dir).expanduser()
    if not experiment_dir.is_dir():
        log.error("no such experiment directory: %s", experiment_dir)
        return EXIT_PROBLEMS
    if not destination_dir.is_dir():
        log.error("destination is not a mounted directory: %s", destination_dir)
        return EXIT_PROBLEMS

    try:
        ensure_disjoint(experiment_dir, destination_experiment_dir(experiment_dir, destination_dir))
    except ValueError as exc:
        log.error("%s", exc)
        return EXIT_PROBLEMS

    totals = Summary()
    while True:
        summary = run_pass(
            experiment_dir,
            destination_dir,
            mode=args.mode,
            checksum=args.checksum,
            quiesce_s=args.quiesce_s,
            dry_run=args.dry_run,
        )
        totals.merge(summary)
        if not args.follow or summary.finished or args.dry_run:
            break
        time.sleep(args.poll_s)

    if totals.failed:
        return EXIT_PROBLEMS
    if totals.files == 0 and totals.skipped == 0 and not totals.finished:
        log.info("nothing is movable yet; the acquisition is still in progress")
        return EXIT_NOTHING_MOVABLE
    return EXIT_OK


def _run_verify(argv: Sequence[str]) -> int:
    args = _verify_parser().parse_args(list(argv))
    _configure_logging(args.log_level)

    problems = verify_destination(Path(args.experiment_dir).expanduser(), Path(args.destination_dir).expanduser())
    if not problems:
        log.info("verified: %s", destination_experiment_dir(args.experiment_dir, args.destination_dir))
        return EXIT_OK
    log.error("%d problem(s) found:", len(problems))
    for problem in problems:
        log.error("  %s", problem)
    return EXIT_PROBLEMS


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "verify":
        return _run_verify(argv[1:])
    return _run_upload(argv)


if __name__ == "__main__":
    sys.exit(main())
