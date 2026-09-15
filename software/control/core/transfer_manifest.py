"""Transfer manifest: which acquisition files are complete and safe to move off the disk.

Large acquisitions are offloaded to a NAS *during* the run by an external tool
(``tools/upload_acquisition.py`` or the user's own mover). The tool must never touch a file Squid is
still writing, so Squid publishes an append-only JSON-lines manifest, ``transfer_manifest.jsonl``, in
the experiment folder (only when large acquisition mode is on):

    {"event":"start","schema":1,"experiment_id":"exp","format":"ZARR_V3","nt":10,"ts":...}
    {"event":"complete","path":"00000/A1_0000_0000_BF.tiff","kind":"file","bytes":8388608,"t":0,"region":"A1","fov":0,"ts":...}
    {"event":"complete","path":"plate.ome.zarr/A/1/0/0/c/0","kind":"dir","bytes":null,"t":0,"region":"A1","fov":0,"ts":...}
    {"event":"timepoint_done","t":0,"ts":...}
    {"event":"end","reason":"completed","ts":...}

Contract (see docs/transfer-manifest.md): only ``complete`` entries are movable; ``path`` is relative to
the experiment folder with POSIX separators; a ``dir`` entry is a whole subtree. Everything that is not
listed moves only after ``end``. Readers must tolerate a truncated last line.

Completion is decided on the main side by :class:`CompletionTracker`, fed with the results the save
jobs return (``SaveResult`` / ``ZarrWriteResult`` in ``control.core.job_processing``): a result's
``immediate_paths`` are final on arrival, its ``unit_paths`` once every plane of the
(timepoint, region, fov) unit has arrived (or once the writer itself says so via ``unit_complete``).
"""

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Callable, Dict, List, Optional, Tuple

import squid.logging

_log = squid.logging.get_logger(__name__)

SCHEMA_VERSION = 1
MANIFEST_FILE_NAME = "transfer_manifest.jsonl"


class TransferManifestWriter:
    """Append-only writer for ``transfer_manifest.jsonl``. Thread-safe; the file is created lazily."""

    def __init__(self, experiment_dir: str) -> None:
        self._dir = os.path.abspath(str(experiment_dir))
        self._path = os.path.join(self._dir, MANIFEST_FILE_NAME)
        self._lock = threading.Lock()
        self._fh = None
        self._ended = False

    @property
    def path(self) -> str:
        return self._path

    def start(self, experiment_id: str, file_format: str, nt: int) -> None:
        self._write(
            {
                "event": "start",
                "schema": SCHEMA_VERSION,
                "experiment_id": experiment_id,
                "format": file_format,
                "nt": int(nt),
            }
        )

    def complete(
        self, path: str, kind: str, nbytes: Optional[int], t: int, region: Optional[str], fov: Optional[int]
    ) -> None:
        """Record that ``path`` (a file, or a directory subtree when ``kind == "dir"``) is final."""
        if kind not in ("file", "dir"):
            raise ValueError(f"kind must be 'file' or 'dir', got {kind!r}")
        self._write(
            {
                "event": "complete",
                "path": self._relative(path),
                "kind": kind,
                "bytes": None if nbytes is None else int(nbytes),
                "t": int(t),
                "region": region,
                "fov": fov,
            }
        )

    def timepoint_done(self, t: int) -> None:
        self._write({"event": "timepoint_done", "t": int(t)}, fsync=True)

    def end(self, reason: str) -> None:
        """Terminal record. After this, everything unlisted is movable and the writer refuses further writes."""
        self._write({"event": "end", "reason": reason}, fsync=True)
        with self._lock:
            self._ended = True
            self._close_locked()

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _relative(self, path: str) -> str:
        abs_path = os.path.abspath(str(path))
        try:
            rel = Path(abs_path).relative_to(self._dir)
        except ValueError:
            raise ValueError(f"{path!r} is not inside the experiment folder {self._dir!r}")
        return PurePath(rel).as_posix()

    def _write(self, record: dict, fsync: bool = False) -> None:
        with self._lock:
            if self._ended:
                raise RuntimeError("transfer manifest already ended")
            if self._fh is None:
                os.makedirs(self._dir, exist_ok=True)
                self._fh = open(self._path, "a", encoding="utf-8")
            record["ts"] = time.time()
            self._fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._fh.flush()
            if fsync:
                os.fsync(self._fh.fileno())

    def _close_locked(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def read_manifest(path) -> List[dict]:
    """Read every complete record. A truncated last line (writer died mid-write) is ignored; a
    malformed line anywhere else is corruption and raises ValueError. Missing file -> []."""
    p = Path(path)
    if not p.exists():
        return []
    raw_lines = p.read_text(encoding="utf-8").split("\n")
    records: List[dict] = []
    last_index = len(raw_lines) - 1
    for index, line in enumerate(raw_lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            if index >= last_index - 1 and all(not l.strip() for l in raw_lines[index + 1 :]):
                _log.warning(f"Ignoring truncated last line of {p}")
                break
            raise ValueError(f"Malformed manifest line {index + 1} in {p}") from exc
    return records


@dataclass(frozen=True)
class UnitKey:
    """Identity of a completion unit: (timepoint, region, fov), or (timepoint, region) when fov is None."""

    t: int
    region: str
    fov: Optional[int]


@dataclass(frozen=True)
class CompletedUnit:
    t: int
    region: Optional[str]
    fov: Optional[int]
    paths: Tuple[str, ...]
    kind: str
    nbytes: Optional[int]


@dataclass
class _UnitState:
    paths: Tuple[str, ...]
    kind: str
    planes: int = 0
    nbytes: int = 0
    done: bool = False


class CompletionTracker:
    """Turns per-plane save results into "this unit is complete" events.

    ``expected_planes_fn(key)`` returns how many plane results make the unit complete (z levels x
    channels, times the region's FOV count for region-scoped units). ``on_complete`` receives one
    :class:`CompletedUnit` per immediate path as it arrives and one per unit when it completes.
    """

    def __init__(
        self, expected_planes_fn: Callable[[UnitKey], int], on_complete: Callable[[CompletedUnit], None]
    ) -> None:
        self._expected_planes_fn = expected_planes_fn
        self._on_complete = on_complete
        self._units: Dict[UnitKey, _UnitState] = {}

    def feed(self, result) -> None:
        t = int(result.time_point)
        region = str(result.region_id)
        fov = None if result.unit_per_region else int(result.fov)
        nbytes = int(result.bytes_written)

        immediate = tuple(result.immediate_paths)
        for path in immediate:
            self._on_complete(CompletedUnit(t, region, fov, (path,), "file", nbytes if len(immediate) == 1 else None))

        unit_paths = tuple(result.unit_paths)
        if not unit_paths:
            return

        key = UnitKey(t, region, fov)
        state = self._units.get(key)
        if state is None:
            state = _UnitState(paths=unit_paths, kind=result.unit_kind)
            self._units[key] = state
        elif state.paths != unit_paths:
            raise ValueError(f"Unit {key} reported {unit_paths} but earlier planes reported {state.paths}")
        if state.done:
            _log.debug(f"Ignoring plane for already-complete unit {key}")
            return

        state.planes += 1
        state.nbytes += nbytes
        if result.unit_complete is not None:
            complete = bool(result.unit_complete)
        else:
            complete = state.planes >= self._expected_planes_fn(key)
        if complete:
            state.done = True
            self._on_complete(CompletedUnit(t, region, fov, state.paths, state.kind, state.nbytes))

    def incomplete_units(self) -> List[UnitKey]:
        return [key for key, state in self._units.items() if not state.done]
