# squid/acquisition_history.py
"""Per-machine acquisition history: an append-only JSON-lines event log plus PNG previews.

Writers (the acquisition recorder, the folder importer) append events; readers fold the
log into `HistoryEntry` objects with `HistoryStore.load()`.

Stdlib-only leaf module: must NOT import anything from `control`.
"""

import hashlib
import json
import os
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


def folder_availability(folder: str, *, exists=os.path.exists) -> str:
    """Return online, drive_offline (the drive/mount is gone) or folder_missing. Path tests only."""
    if exists(folder):
        return "online"
    anchor = Path(folder).anchor
    if anchor and not exists(anchor):
        return "drive_offline"
    return "folder_missing"


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
        with open(self.path, "ab+") as f:
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

        for ev in self.events():
            id_ = ev.get("id")
            kind = ev.get("type")
            at = ev.get("at")
            if not isinstance(id_, str):
                continue
            if at is not None:
                first_at.setdefault(id_, at)
            old = entries.get(id_)
            if kind in ("started", "imported", "protocol_started"):
                removed.discard(id_)
                entry = HistoryEntry(
                    id=id_,
                    kind=ev.get("kind", "run") if kind != "protocol_started" else "protocol_run",
                    name=ev.get("name", id_),
                    folder=ev.get("folder", ""),
                    metadata=ev.get("metadata") or {},
                    parent=ev.get("parent"),
                    protocol=ev.get("protocol"),
                )
                if kind == "imported":
                    entry.source = "imported"
                    entry.started_at = ev.get("started_at")
                    entry.outcome = ev.get("outcome", "unknown")
                else:
                    entry.started_at = at
                    entry.outcome = "running"
                    entry.pid = ev.get("pid")
                    entry.pid_started_at = ev.get("pid_started_at")
                    entry.machine = ev.get("machine")
                if old is not None:
                    entry.preview = old.preview
                    entry.preview_channels = old.preview_channels
                    entry.bytes_on_disk = old.bytes_on_disk
                entries[id_] = entry
            elif kind in ("finished", "protocol_finished"):
                if old is None:
                    continue
                old.outcome = ev.get("outcome", old.outcome)
                old.reason = ev.get("reason")
                old.ended_at = ev.get("ended_at")
                if kind == "finished":
                    old.fraction_done = ev.get("fraction_done")
                    old.images = ev.get("images")
            elif kind == "preview":
                if old is None:
                    continue
                old.preview = ev.get("file")
                old.preview_channels = list(ev.get("channels") or [])
                old.bytes_on_disk = ev.get("bytes_on_disk")
            elif kind == "relocated":
                if old is not None:
                    old.folder = ev.get("folder", old.folder)
            elif kind == "removed":
                entries.pop(id_, None)
                removed.add(id_)

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
