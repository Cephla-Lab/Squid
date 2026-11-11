# squid/acquisition_history.py
"""Per-machine acquisition history: an append-only JSON-lines event log plus PNG previews.

Writers (the acquisition recorder, the folder importer) append events; readers fold the
log into `HistoryEntry` objects with `HistoryStore.load()`.

Stdlib-only leaf module: must NOT import anything from `control`.

Concurrency: `append` is serialised by a process-wide lock, so the threads of one Squid process
(GUI, preview executor, import worker, TCP server) never interleave or lose events. Two Squid
processes appending to the same history file at once are NOT locked against each other.
"""

import hashlib
import json
import os
import posixpath
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import platformdirs

import squid.logging

_log = squid.logging.get_logger(__name__)

HISTORY_FILE_NAME = "history.jsonl"
PREVIEWS_DIR_NAME = "previews"
ENV_VAR = "SQUID_ACQUISITION_HISTORY_DIR"

OUTCOMES = ("running", "complete", "aborted", "failed", "unknown")

_APPEND_LOCK = threading.Lock()  # process-wide; see the module docstring

# POSIX mount roots under which a missing folder most likely means an unplugged drive.
_REMOVABLE_MEDIA_ROOTS = ("/", "/media", "/run/media", "/mnt")
_REMOVABLE_MEDIA_USER_PARENTS = ("/media", "/run/media")  # /media/<user>, /run/media/<user>


def history_dir() -> Path:
    """Per-user history dir. Overridable via SQUID_ACQUISITION_HISTORY_DIR."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    return Path(platformdirs.user_state_path("squid", "cephla")) / "acquisition_history"


def normalize_folder(folder: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(folder)))


def entry_id_for(folder: str) -> str:
    digest = hashlib.sha1(normalize_folder(folder).encode("utf-8")).hexdigest()[:8]
    return f"{os.path.basename(os.path.normpath(folder))}-{digest}"


def folder_availability(folder: str, *, exists=os.path.exists, posix: Optional[bool] = None) -> str:
    """Return online, drive_offline (the drive/mount is gone) or folder_missing. Path tests only.

    Windows: the drive (anchor) is gone -> drive_offline. POSIX has no drive letters, so a heuristic:
    when the nearest existing ancestor is a removable-media root (/, /media, /media/<user>,
    /run/media, /run/media/<user>, /mnt) -> drive_offline.
    """
    if exists(folder):
        return "online"
    if posix is None:
        posix = os.name != "nt"
    if not posix:
        anchor = Path(folder).anchor
        if anchor and not exists(anchor):
            return "drive_offline"
        return "folder_missing"
    current = posixpath.normpath(folder)
    while True:
        parent = posixpath.dirname(current)
        if parent == current:  # walked past the root without finding anything
            return "drive_offline"
        current = parent
        if exists(current):
            break
    if current in _REMOVABLE_MEDIA_ROOTS or posixpath.dirname(current) in _REMOVABLE_MEDIA_USER_PARENTS:
        return "drive_offline"
    return "folder_missing"


def _as_float(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    as_float = _as_float(value)
    if as_float is None or as_float != as_float or as_float in (float("inf"), float("-inf")):
        return None
    return int(as_float)


def _as_str(value) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return list(value) if isinstance(value, list) else []


@dataclass
class HistoryEntry:
    id: str
    kind: str  # "run" | "snap" | "protocol_run"
    name: str
    folder: str
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    outcome: str = "unknown"
    reason: Optional[str] = None
    fraction_done: Optional[float] = None
    images: Optional[int] = None
    bytes_on_disk: Optional[int] = None
    metadata: dict = field(default_factory=dict)
    preview: Optional[str] = None  # file name under previews/
    preview_channels: List[str] = field(default_factory=list)
    parent: Optional[str] = None
    protocol: Optional[dict] = None
    rounds: List["HistoryEntry"] = field(default_factory=list)
    source: str = "recorded"  # "recorded" | "imported"
    pid: Optional[int] = None
    pid_started_at: Optional[float] = None
    machine: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.outcome == "running"


class HistoryStore:
    def __init__(self, directory: Path):
        self._dir = Path(directory)

    @property
    def path(self) -> Path:
        return self._dir / HISTORY_FILE_NAME

    @property
    def previews_dir(self) -> Path:
        d = self._dir / PREVIEWS_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def append(self, event: dict) -> None:
        if "at" not in event:
            event = {**event, "at": time.time()}
        line = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
        self._dir.mkdir(parents=True, exist_ok=True)
        with _APPEND_LOCK, open(self.path, "ab+") as f:
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                needs_newline = f.read(1) != b"\n"
            else:
                needs_newline = False
            f.seek(0, os.SEEK_END)
            if needs_newline:
                f.write(b"\n")  # terminate a cut line rather than extend it
            f.write(line)
            f.flush()

    def events(self) -> List[dict]:
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return []
        events = []
        skipped = 0
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except ValueError:  # includes JSONDecodeError and UnicodeDecodeError
                skipped += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                skipped += 1
        if skipped:
            _log.warning(f"Skipped {skipped} malformed line(s) in {self.path}")
        return events

    def load(self) -> List[HistoryEntry]:
        entries: Dict[str, HistoryEntry] = {}
        first_at: Dict[str, float] = {}
        removed = set()
        skipped = 0

        for ev in self.events():
            id_ = ev.get("id")
            kind = ev.get("type")
            at = _as_float(ev.get("at"))
            if not isinstance(id_, str):
                skipped += 1
                continue
            if at is not None:
                first_at.setdefault(id_, at)
            old = entries.get(id_)
            if kind in ("started", "imported", "protocol_started"):
                removed.discard(id_)
                entry = HistoryEntry(
                    id=id_,
                    kind=(_as_str(ev.get("kind")) or "run") if kind != "protocol_started" else "protocol_run",
                    name=_as_str(ev.get("name")) or id_,
                    folder=_as_str(ev.get("folder")) or "",
                    metadata=_as_dict(ev.get("metadata")),
                    parent=_as_str(ev.get("parent")),
                    protocol=ev.get("protocol") if isinstance(ev.get("protocol"), dict) else None,
                )
                if kind == "imported":
                    entry.source = "imported"
                    entry.started_at = _as_float(ev.get("started_at"))
                    entry.outcome = _as_str(ev.get("outcome")) or "unknown"
                else:
                    entry.started_at = at
                    entry.outcome = "running"
                    entry.pid = _as_int(ev.get("pid"))
                    entry.pid_started_at = _as_float(ev.get("pid_started_at"))
                    entry.machine = _as_str(ev.get("machine"))
                if old is not None:
                    entry.preview = old.preview
                    entry.preview_channels = old.preview_channels
                    entry.bytes_on_disk = old.bytes_on_disk
                entries[id_] = entry
            elif kind in ("finished", "protocol_finished"):
                if old is None:
                    continue
                old.outcome = _as_str(ev.get("outcome")) or old.outcome
                old.reason = _as_str(ev.get("reason"))
                old.ended_at = _as_float(ev.get("ended_at"))
                if kind == "finished":
                    old.fraction_done = _as_float(ev.get("fraction_done"))
                    old.images = _as_int(ev.get("images"))
            elif kind == "preview":
                if old is None:
                    continue
                old.preview = _as_str(ev.get("file"))
                old.preview_channels = [c for c in (_as_str(c) for c in _as_list(ev.get("channels"))) if c]
                old.bytes_on_disk = _as_int(ev.get("bytes_on_disk"))
            elif kind == "relocated":
                if old is not None:
                    old.folder = _as_str(ev.get("folder")) or old.folder
            elif kind == "removed":
                entries.pop(id_, None)
                removed.add(id_)

        if skipped:
            _log.warning(f"Skipped {skipped} history event(s) without a string id in {self.path}")

        def sort_time(e: HistoryEntry) -> float:
            if e.started_at is not None:
                return e.started_at
            return first_at.get(e.id, 0.0)

        children: Dict[str, List[HistoryEntry]] = {}
        top: List[HistoryEntry] = []
        for e in entries.values():
            if e.parent is None:
                top.append(e)
            elif e.parent not in removed:
                children.setdefault(e.parent, []).append(e)

        parents = {e.id: e for e in top}
        for parent_id, rounds in children.items():
            rounds.sort(key=sort_time)
            parent = parents.get(parent_id)
            if parent is None:
                parent = self._implicit_parent(parent_id, rounds)
                top.append(parent)
            parent.rounds = rounds
            latest_preview = next((r for r in reversed(rounds) if r.preview), None)
            if latest_preview is not None:
                parent.preview = latest_preview.preview
                parent.preview_channels = list(latest_preview.preview_channels)

        top.sort(key=sort_time, reverse=True)
        return top

    @staticmethod
    def _implicit_parent(parent_id: str, rounds: List[HistoryEntry]) -> HistoryEntry:
        """A protocol run that was never `protocol_started`: built from, and summarising, its rounds."""
        first, last = rounds[0], rounds[-1]
        protocol = first.protocol or {}
        if any(r.is_running for r in rounds):
            outcome = "running"
        elif all(r.outcome == "complete" for r in rounds):
            outcome = "complete"
        else:
            outcome = "aborted"
        return HistoryEntry(
            id=parent_id,
            kind="protocol_run",
            name=protocol.get("run_name") or parent_id,
            folder=os.path.dirname(first.folder),
            started_at=first.started_at,
            ended_at=None if outcome == "running" else last.ended_at,
            outcome=outcome,
            protocol=first.protocol,
            source=first.source,
        )

    def entry(self, entry_id: str) -> Optional[HistoryEntry]:
        for e in self.load():
            if e.id == entry_id:
                return e
            for r in e.rounds:
                if r.id == entry_id:
                    return r
        return None
