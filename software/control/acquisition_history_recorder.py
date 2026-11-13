"""Records acquisitions in the per-machine history store: turns the controller's start and end of a run, and the
GUI's preview requests, into history events and preview jobs.

Nothing here may break an acquisition: every store and preview failure is caught and logged. No preview work runs
while an acquisition does: the GUI marks the recorder busy (set_busy) for the length of a run, and previews asked
for meanwhile are queued and made once it is over.
"""

import os
import socket
import threading
import time
from concurrent.futures import Executor, Future
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import psutil

import control._def
import squid.logging
import squid.version
from control.gallery_import import _scaled, image_format_for_saving_option, metadata_from_yaml
from squid.acquisition_history import HistoryEntry, HistoryStore, entry_id_for

_log = squid.logging.get_logger(__name__)

# The controller's end reasons (MultiPointController.last_end_reason) -> history outcome; anything else is "failed".
_RUN_OUTCOMES = {
    "completed": "complete",
    "completed_with_errors": "complete",
    "user_abort": "aborted",
}

# The protocol runner's end words -> history outcome.
_PROTOCOL_OUTCOMES = {
    "finished": "complete",
    "stopped": "aborted",
    "failed": "failed",
}

# A recorded process creation time and the live one must agree this closely for the process to be the same one.
_PID_START_TOLERANCE_S = 2.0

# How long a preview job may spend adding up a folder's size before the size is left unknown.
FOLDER_SIZE_BUDGET_S = 5.0
FOLDER_SIZE_CHECK_EVERY = 256  # directory entries between two looks at the clock while adding up a folder's size


def write_preview_png(folder: str, out_path: str) -> Optional[List[str]]:
    """control.gallery_previews.write_preview_png, imported on first use: it pulls in Qt and image libraries, which
    the acquisition controller (it imports this module) does not otherwise need."""
    from control.gallery_previews import write_preview_png as write

    return write(folder, out_path)


def map_protocol_outcome(word: str) -> str:
    """The protocol runner's `finished` / `stopped` / `failed` as a history outcome."""
    return _PROTOCOL_OUTCOMES.get(word, "failed")


def _run_outcome(reason: str) -> str:
    return _RUN_OUTCOMES.get(reason, "failed")


# ---------------------------------------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------------------------------------


def metadata_from_params(params, experiment_path: str) -> dict:
    """The gallery's metadata for a run: the folder's acquisition.yaml fields (metadata_from_yaml: widget type,
    objective, wellplate format, ...), overlaid with what the run's AcquisitionParameters know."""
    metadata = metadata_from_yaml(experiment_path)
    spi = getattr(params, "scan_position_information", None)
    region_names = getattr(spi, "scan_region_names", None)
    fov_coords = getattr(spi, "scan_region_fov_coords_mm", None)
    configurations = getattr(params, "selected_configurations", None) or []
    metadata.update(
        {
            "experiment_id": getattr(params, "experiment_ID", None),
            "xy_mode": getattr(params, "xy_mode", None),
            "channels": [getattr(c, "name", str(c)) for c in configurations],
            "num_regions": len(region_names) if region_names is not None else None,
            "num_fovs": sum(len(c) for c in fov_coords.values()) if isinstance(fov_coords, dict) else None,
            "nz": getattr(params, "NZ", None),
            "dz_um": _scaled(getattr(params, "deltaZ", None), 1000.0),
            "nt": getattr(params, "Nt", None),
            "dt_s": getattr(params, "deltat", None),
            "z_stacking_config": getattr(params, "z_stacking_config", None),
            "use_piezo": getattr(params, "use_piezo", None),
            "contrast_af": getattr(params, "do_autofocus", None),
            "laser_af": getattr(params, "do_reflection_autofocus", None),
            "laser_af_per_stack": getattr(params, "laser_af_per_stack", None),
            "start_time": getattr(params, "acquisition_start_time", None),
        }
    )
    return metadata


def _expected_images(metadata: dict) -> Optional[int]:
    counts = [metadata.get("num_fovs"), metadata.get("nt"), metadata.get("nz")]
    if not all(isinstance(c, int) and not isinstance(c, bool) for c in counts):
        return None
    fovs, nt, nz = counts
    return fovs * nt * nz * len(metadata.get("channels") or [])


# ---------------------------------------------------------------------------------------------------------
# Folder size and process identity
# ---------------------------------------------------------------------------------------------------------


def _folder_size(folder: str, *, clock: Callable[[], float] = time.monotonic) -> Optional[int]:
    """Total size of the files under folder; unreadable entries are skipped and no symlink is followed. None when
    adding it up takes longer than FOLDER_SIZE_BUDGET_S (a huge run, a slow network drive). The clock is read
    between directories and every FOLDER_SIZE_CHECK_EVERY entries inside one, so one huge timepoint directory
    cannot run far past the budget either."""
    deadline = clock() + FOLDER_SIZE_BUDGET_S
    total = 0
    seen = 0
    stack = [folder]

    def out_of_time() -> bool:
        if clock() <= deadline:
            return False
        _log.info(f"Gave up adding up the size of {folder} after {FOLDER_SIZE_BUDGET_S:g} s")
        return True

    while stack:
        if out_of_time():
            return None
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    seen += 1
                    if seen % FOLDER_SIZE_CHECK_EVERY == 0 and out_of_time():
                        return None
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _process_started_at(pid: int) -> Optional[float]:
    """The creation time of process pid, or None when it is gone or cannot be inspected."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _walk(entries: Iterable[HistoryEntry]) -> Iterable[HistoryEntry]:
    for entry in entries:
        yield entry
        yield from entry.rounds


# ---------------------------------------------------------------------------------------------------------
# Recorders
# ---------------------------------------------------------------------------------------------------------


class AcquisitionHistoryRecorder:
    def __init__(
        self,
        store: HistoryStore,
        *,
        preview_executor: Executor,
        machine: Optional[str] = None,
        software_version: Optional[str] = None,
        snaps_enabled: Callable[[], bool] = lambda: getattr(control._def, "RECORD_SNAPS_IN_HISTORY", False),
    ):
        self._store = store
        self._executor = preview_executor
        self._machine = machine or socket.gethostname()
        self._software_version = software_version or squid.version.software_version()
        self._snaps_enabled = snaps_enabled
        self._failed_once = False
        self._own_started_at: Optional[float] = None
        self._lock = threading.Lock()
        self._pending: Set[str] = set()  # ids with a preview job submitted and not yet done
        self._replace_after: Dict[str, str] = {}  # id -> folder to regenerate from once the pending job is done
        self._previewed: Set[str] = set()  # ids this recorder has written a preview event for
        self._busy = False  # an acquisition is running: previews wait
        self._queued: Dict[str, Tuple[str, bool]] = {}  # id -> (folder, replace) asked for while busy

    # --- store access ---------------------------------------------------------------------------------

    def _append(self, event: dict) -> bool:
        try:
            self._store.append(event)
            return True
        except Exception as e:
            self._store_failed(e)
            return False

    def _store_failed(self, error: Exception) -> None:
        if not self._failed_once:
            self._failed_once = True
            _log.warning(f"Could not write to the acquisition history at {self._store.path}: {error}")
        else:
            _log.debug(f"Could not write to the acquisition history: {error}")

    def _pid_started_at(self) -> Optional[float]:
        if self._own_started_at is None:
            self._own_started_at = _process_started_at(os.getpid())
        return self._own_started_at

    def _records(self, *, snap: bool, skip_saving: bool) -> bool:
        if skip_saving:
            return False
        return not snap or bool(self._snaps_enabled())

    def _started_event(self, experiment_path: str, metadata: dict, *, snap: bool, protocol: Optional[dict]) -> dict:
        return {
            "type": "started",
            "id": entry_id_for(experiment_path),
            "kind": "snap" if snap else "run",
            "name": os.path.basename(os.path.normpath(experiment_path)),
            "folder": os.path.abspath(experiment_path),
            "metadata": metadata,
            "parent": entry_id_for(os.path.dirname(os.path.normpath(experiment_path))) if protocol else None,
            "protocol": dict(protocol) if protocol else None,
            "pid": os.getpid(),
            "pid_started_at": self._pid_started_at(),
            "machine": self._machine,
        }

    def _common_metadata(self) -> dict:
        return {
            "machine": self._machine,
            "software_version": self._software_version,
            "file_format": control._def.FILE_SAVING_OPTION.value,
        }

    # --- runs -----------------------------------------------------------------------------------------

    def started(
        self, params, experiment_path: str, *, snap: bool, skip_saving: bool, protocol: Optional[dict]
    ) -> Optional[str]:
        """Record a run (or snap) that is starting; its entry id, or None when it is not recorded."""
        try:
            if not self._records(snap=snap, skip_saving=skip_saving):
                return None
            metadata = metadata_from_params(params, experiment_path)
            metadata.update(self._common_metadata())
            metadata["image_format"] = image_format_for_saving_option(control._def.FILE_SAVING_OPTION)
            metadata["expected_images"] = _expected_images(metadata) or 0
            event = self._started_event(experiment_path, metadata, snap=snap, protocol=protocol)
            self._store.append(event)
            return event["id"]
        except Exception as e:
            self._store_failed(e)
            return None

    def finished(self, entry_id: Optional[str], reason: str, *, images: int, expected_images: int) -> None:
        """Record the end of a recorded run. Does not make the preview: the GUI asks for it (make_preview) once the
        mosaic overview it is made from has been saved."""
        if entry_id is None:
            return
        try:
            fraction = min(1.0, images / expected_images) if expected_images else None
            event = {
                "type": "finished",
                "id": entry_id,
                "outcome": _run_outcome(reason),
                "reason": reason,
                "fraction_done": fraction,
                "images": images,
                "ended_at": time.time(),
            }
        except Exception:
            _log.exception(f"Could not build the history end event for {entry_id}")
            return
        self._append(event)

    def failed_start(
        self,
        experiment_path: Optional[str],
        reason: str,
        *,
        snap: bool = False,
        skip_saving: bool = False,
        protocol: Optional[dict] = None,
    ) -> None:
        """Record a run that never started as a failed entry, when its folder exists and is not recorded already."""
        try:
            if not experiment_path or not os.path.isdir(experiment_path):
                return
            if not self._records(snap=snap, skip_saving=skip_saving):
                return
            entry_id = entry_id_for(experiment_path)
            if self._store.entry(entry_id) is not None:
                # The folder already has an entry (e.g. a refused restart in a finished run's folder): keep it.
                return
            metadata = metadata_from_yaml(experiment_path)
            metadata.update(self._common_metadata())
            metadata["expected_images"] = _expected_images(metadata)
            self._store.append(self._started_event(experiment_path, metadata, snap=snap, protocol=protocol))
            self._store.append(
                {
                    "type": "finished",
                    "id": entry_id,
                    "outcome": "failed",
                    "reason": reason,
                    "fraction_done": 0.0,
                    "images": 0,
                    "ended_at": time.time(),
                }
            )
        except Exception as e:
            self._store_failed(e)

    # --- previews -------------------------------------------------------------------------------------

    def _has_preview(self, entry_id: str) -> bool:
        if entry_id in self._previewed:
            return True
        try:
            return any(e.get("type") == "preview" and e.get("id") == entry_id for e in self._store.events())
        except Exception:
            _log.exception("Could not read the acquisition history")
            return False

    def set_busy(self, active: bool) -> List[Future]:
        """While busy (an acquisition runs), make_preview only queues; going idle submits the queue, once. The futures
        of the jobs submitted by going idle."""
        with self._lock:
            self._busy = bool(active)
            if self._busy:
                return []
            queued, self._queued = self._queued, {}
        futures = []
        for entry_id, (folder, replace) in queued.items():
            future = self.make_preview(entry_id, folder, replace=replace)
            if future is not None:
                futures.append(future)
        return futures

    def make_preview(self, entry_id: str, folder: str, *, replace: bool = False) -> Optional[Future]:
        """Make (or with replace=True, remake) the entry's preview on the executor; None when nothing was submitted
        (including while busy: the request is queued, keeping the newest folder and replace flag per id)."""
        if entry_id is None:
            return None
        with self._lock:
            if self._busy:
                self._queued.pop(entry_id, None)
                self._queued[entry_id] = (folder, replace)
                return None
        if not replace and self._has_preview(entry_id):
            return None
        with self._lock:
            if entry_id in self._pending:
                if replace:
                    self._replace_after[entry_id] = folder
                return None
            if not replace and entry_id in self._previewed:
                # A job finished between the pre-filter above and taking the lock. Safe because a job adds the id to
                # _previewed before its finally clears _pending.
                return None
            self._pending.add(entry_id)
        return self._submit(entry_id, folder)

    def _submit(self, entry_id: str, folder: str) -> Optional[Future]:
        try:
            return self._executor.submit(self._preview_job, entry_id, folder)
        except Exception as e:  # RuntimeError once the executor is shut down
            _log.warning(f"Could not schedule the preview of {folder}: {e}")
            with self._lock:
                self._pending.discard(entry_id)
                self._replace_after.pop(entry_id, None)
            return None

    def _preview_job(self, entry_id: str, folder: str) -> None:
        try:
            self._make_preview(entry_id, folder)
        except Exception:
            _log.exception(f"Could not make the preview of {folder}")
        finally:
            # Clear the pending mark before the job's future resolves, or hand over to a replacement asked for while
            # this job ran (the id stays pending until that one is done).
            with self._lock:
                next_folder = self._replace_after.pop(entry_id, None)
                if next_folder is None:
                    self._pending.discard(entry_id)
            if next_folder is not None:
                self._submit(entry_id, next_folder)

    def _make_preview(self, entry_id: str, folder: str) -> None:
        try:
            out_path = self._store.previews_dir / f"{entry_id}.png"
            channels = write_preview_png(folder, str(out_path))
        except Exception:
            _log.exception(f"Could not make the preview of {folder}")
            return
        try:
            size = _folder_size(folder)
        except Exception:
            _log.exception(f"Could not add up the size of {folder}")
            size = None
        event = {
            "type": "preview",
            "id": entry_id,
            "channels": list(channels) if channels is not None else [],
            "bytes_on_disk": size,
        }
        if channels is not None:
            event["file"] = out_path.name
        if self._append(event):
            self._previewed.add(entry_id)

    # --- protocol runs --------------------------------------------------------------------------------

    def protocol_started(self, run_dir: str, run_name: str, protocol_name: str) -> None:
        try:
            event = {
                "type": "protocol_started",
                "id": entry_id_for(run_dir),
                "name": run_name,
                "folder": os.path.abspath(run_dir),
                "protocol": {"protocol": protocol_name, "run_name": run_name},
                "pid": os.getpid(),
                "pid_started_at": self._pid_started_at(),
                "machine": self._machine,
            }
        except Exception:
            _log.exception(f"Could not build the history start event for the protocol run {run_dir}")
            return
        self._append(event)

    def protocol_finished(self, run_dir: str, outcome: str) -> None:
        try:
            event = {
                "type": "protocol_finished",
                "id": entry_id_for(run_dir),
                "outcome": map_protocol_outcome(outcome),
                "reason": outcome,
                "ended_at": time.time(),
            }
        except Exception:
            _log.exception(f"Could not build the history end event for the protocol run {run_dir}")
            return
        self._append(event)

    # --- startup reconciliation -----------------------------------------------------------------------

    def reconcile_orphans(
        self,
        *,
        pid_alive: Callable[[int], bool] = psutil.pid_exists,
        process_started_at: Callable[[int], Optional[float]] = _process_started_at,
    ) -> int:
        """Close this machine's entries still `running` whose recording process is gone, and any entry still `running`
        with no recording process at all (an import that never finished: it records no machine); the number closed."""
        try:
            entries = self._store.load()
        except Exception:
            _log.exception("Could not read the acquisition history to close interrupted runs")
            return 0
        closed = 0
        for entry in _walk(entries):
            if not entry.is_running:
                continue
            if entry.pid is not None and entry.machine != self._machine:
                continue
            if entry.pid is None and any(r.is_running for r in entry.rounds):
                continue  # a protocol run never recorded itself, running through its rounds: closing them closes it
            if self._recording_process_alive(entry, pid_alive, process_started_at):
                continue
            if entry.kind == "protocol_run":
                event = {
                    "type": "protocol_finished",
                    "id": entry.id,
                    "outcome": "failed",
                    "reason": "interrupted",
                    "ended_at": time.time(),
                }
            else:
                event = {
                    "type": "finished",
                    "id": entry.id,
                    "outcome": "failed",
                    "reason": "interrupted",
                    "fraction_done": None,
                    "images": None,
                    "ended_at": time.time(),
                }
            if self._append(event):
                closed += 1
        return closed

    @staticmethod
    def _recording_process_alive(entry: HistoryEntry, pid_alive, process_started_at) -> bool:
        pid = entry.pid
        if pid is None:
            return False
        try:
            if not pid_alive(pid):
                return False
        except Exception:
            return False
        if entry.pid_started_at is None:  # an old event: the pid is all there is
            return True
        try:
            started_at = process_started_at(pid)
        except Exception:
            return False
        if not isinstance(started_at, (int, float)):
            return False
        return abs(started_at - entry.pid_started_at) <= _PID_START_TOLERANCE_S

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


class NullAcquisitionHistoryRecorder:
    """Records nothing: the controller's recorder until the GUI installs a real one."""

    def started(self, params, experiment_path, *, snap, skip_saving, protocol) -> Optional[str]:
        return None

    def finished(self, entry_id, reason, *, images, expected_images) -> None:
        pass

    def make_preview(self, entry_id, folder, *, replace=False) -> Optional[Future]:
        return None

    def set_busy(self, active) -> list:
        return []

    def failed_start(self, experiment_path, reason, *, snap=False, skip_saving=False, protocol=None) -> None:
        pass

    def protocol_started(self, run_dir, run_name, protocol_name) -> None:
        pass

    def protocol_finished(self, run_dir, outcome) -> None:
        pass

    def reconcile_orphans(self, *, pid_alive=None, process_started_at=None) -> int:
        return 0

    def shutdown(self) -> None:
        pass
