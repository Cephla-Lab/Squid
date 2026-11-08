"""Reading saved acquisition folders, and bringing them into the acquisition history (Add folder to history).

Pure module: no Qt, so the controller side can import it. The gallery widget shows what read_acquisition_entry
returns; the history importer turns the same folders into `imported` events.
"""

import csv
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import yaml

import squid.logging
from squid.acquisition_history import HistoryStore, entry_id_for, normalize_folder

_log = squid.logging.get_logger(__name__)

# Any one of these marks a folder as a saved acquisition (older versions wrote only the last two).
METADATA_FILES = ("acquisition.yaml", "acquisition parameters.json", "acquisition_channels.yaml", "configurations.xml")
IMAGE_EXTENSIONS = (".tiff", ".tif", ".bmp", ".png", ".jpg", ".jpeg")
NO_IMAGES = "No images"

# MultiPointController names an experiment folder "<name>_YYYY-MM-DD_HH-MM-SS.ffffff".
_EXPERIMENT_NAME = re.compile(
    r"^(?P<label>.*?)_?(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?P<fraction>\.\d+)?$",
)


@dataclass
class AcquisitionEntry:
    """What the gallery knows about one saved acquisition folder."""

    path: str
    name: str  # the folder name
    label: str  # the experiment name the user typed, without the timestamp suffix
    started: Optional[datetime] = None
    modified: float = 0.0  # folder mtime, the sort key when no start time is known
    widget_type: Optional[str] = None  # "wellplate" or "flexible"
    objective: Optional[str] = None
    magnification: Optional[float] = None
    wellplate_format: Optional[str] = None
    channels: List[str] = field(default_factory=list)
    nz: Optional[int] = None
    nt: Optional[int] = None
    num_regions: Optional[int] = None
    num_fovs: Optional[int] = None
    image_format: str = NO_IMAGES
    thumbnail_source: Optional[str] = None  # an image file to make the thumbnail from
    has_settings: bool = False  # acquisition.yaml exists, so the settings can be loaded again

    @property
    def sort_time(self) -> float:
        return self.started.timestamp() if self.started else self.modified

    def summary_lines(self) -> List[str]:
        """Short facts for the tooltip and the details pane, only the ones known."""
        lines = []
        if self.started:
            lines.append(f"Started: {self.started:%Y-%m-%d %H:%M:%S}")
        if self.objective:
            mag = f" ({self.magnification:g}x)" if self.magnification else ""
            lines.append(f"Objective: {self.objective}{mag}")
        if self.wellplate_format:
            lines.append(f"Sample: {self.wellplate_format}")
        if self.widget_type:
            lines.append(f"Mode: {self.widget_type} multipoint")
        if self.channels:
            lines.append(f"Channels ({len(self.channels)}): " + ", ".join(self.channels))
        dims = []
        if self.num_regions is not None:
            dims.append(f"{self.num_regions} region{'s' if self.num_regions != 1 else ''}")
        if self.num_fovs is not None:
            dims.append(f"{self.num_fovs} FOV{'s' if self.num_fovs != 1 else ''}")
        if self.nz is not None:
            dims.append(f"Nz={self.nz}")
        if self.nt is not None:
            dims.append(f"Nt={self.nt}")
        if dims:
            lines.append("Scan: " + ", ".join(dims))
        lines.append(f"Format: {self.image_format}")
        return lines


# ---------------------------------------------------------------------------------------------------------
# Reading acquisition folders (pure functions, no Qt)
# ---------------------------------------------------------------------------------------------------------


def split_experiment_name(folder_name: str) -> Tuple[str, Optional[datetime]]:
    """The experiment label and start time encoded in a folder name; the whole name and None otherwise."""
    m = _EXPERIMENT_NAME.match(folder_name)
    if not m:
        return folder_name, None
    try:
        started = datetime.strptime(m.group("stamp"), "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return folder_name, None
    fraction = m.group("fraction")
    if fraction:
        try:
            started = started.replace(microsecond=int(round(float(fraction) * 1_000_000)))
        except ValueError:
            pass
    return m.group("label") or folder_name, started


def is_acquisition_folder(path: str) -> bool:
    return os.path.isdir(path) and any(os.path.isfile(os.path.join(path, name)) for name in METADATA_FILES)


def _load_yaml(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _as_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _channel_names(channels) -> List[str]:
    if not isinstance(channels, list):
        return []
    return [str(ch["name"]) for ch in channels if isinstance(ch, dict) and ch.get("name")]


def _count_csv_rows(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = sum(1 for row in csv.reader(f) if row)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    return max(rows - 1, 0)  # minus the header


def _timepoint_folders(path: str) -> List[str]:
    """The numbered per-timepoint folders, in acquisition order."""
    try:
        with os.scandir(path) as it:
            names = [d.name for d in it if d.is_dir(follow_symlinks=False) and d.name.isdigit()]
    except OSError:
        return []
    return [os.path.join(path, name) for name in sorted(names, key=int)]


def _first_image_file(folder: str) -> Optional[str]:
    """The first image file in a folder, by name; None when there is none. Reads only one directory."""
    try:
        with os.scandir(folder) as it:
            names = [e.name for e in it if e.is_file() and e.name.lower().endswith(IMAGE_EXTENSIONS)]
    except OSError:
        return None
    return os.path.join(folder, min(names)) if names else None


def _overview_file(timepoint_folder: str) -> Optional[str]:
    """The mosaic overview UnifiedMosaicWidget saves under <timepoint>/mosaic_view, if any."""
    mosaic_dir = os.path.join(timepoint_folder, "mosaic_view")
    try:
        with os.scandir(mosaic_dir) as it:
            names = [
                e.name
                for e in it
                if e.is_file() and e.name.startswith("mosaic_") and e.name.lower().endswith((".ome.tiff", ".ome.tif"))
            ]
    except OSError:
        return None
    return os.path.join(mosaic_dir, min(names)) if names else None


def _has_zarr_store(path: str) -> bool:
    if any(os.path.isdir(os.path.join(path, name)) for name in ("plate.ome.zarr", "plate.zarr")):
        return True
    zarr_dir = os.path.join(path, "zarr")
    try:
        with os.scandir(zarr_dir) as regions:
            for region in regions:
                if not region.is_dir(follow_symlinks=False):
                    continue
                with os.scandir(region.path) as stores:
                    if any(s.is_dir() and s.name.endswith(".zarr") for s in stores):
                        return True
    except OSError:
        pass
    return False


def inspect_images(path: str) -> Tuple[str, Optional[str]]:
    """How an acquisition's images are stored and which file to draw its thumbnail from.

    The thumbnail comes from the saved mosaic overview when there is one, else the first image of the first
    timepoint. Zarr stores get no thumbnail unless an overview was saved.
    """
    timepoints = _timepoint_folders(path)
    overview = next((f for f in (_overview_file(tp) for tp in timepoints) if f), None)

    if _has_zarr_store(path):
        return "OME-Zarr", overview

    ome_dir = os.path.join(path, "ome_tiff")
    ome_file = _first_image_file(ome_dir) if os.path.isdir(ome_dir) else None
    if ome_file and ".ome." in os.path.basename(ome_file).lower():
        return "OME-TIFF", overview or ome_file

    for tp in timepoints:
        image = _first_image_file(tp)
        if image is None:
            continue
        name = os.path.basename(image).lower()
        if ".ome." in name:
            return "OME-TIFF", overview or image
        if name.endswith("_stack.tiff") or name.endswith("_stack.tif"):
            return "Multi-page TIFF", overview or image
        ext = os.path.splitext(name)[1].lstrip(".").upper()
        return f"{ext} images", overview or image

    return NO_IMAGES, overview


def read_acquisition_entry(path: str) -> AcquisitionEntry:
    """Everything the gallery shows for one acquisition folder, from whichever metadata files it has.

    Each file is optional and read leniently: a folder with a broken acquisition.yaml still lists, with less
    said about it.
    """
    path = os.path.normpath(path)
    name = os.path.basename(path)
    label, started = split_experiment_name(name)
    entry = AcquisitionEntry(path=path, name=name, label=label, started=started)
    try:
        entry.modified = os.stat(path).st_mtime
    except OSError:
        pass

    yaml_path = os.path.join(path, "acquisition.yaml")
    entry.has_settings = os.path.isfile(yaml_path)
    data = _load_yaml(yaml_path) if entry.has_settings else None
    if data:
        acq = data.get("acquisition") or {}
        entry.widget_type = acq.get("widget_type") or None
        start_time = _as_float(acq.get("start_time"))
        if start_time and start_time > 0:
            try:
                entry.started = datetime.fromtimestamp(start_time)
            except (OverflowError, OSError, ValueError):
                pass
        objective = data.get("objective") or {}
        entry.objective = objective.get("name") or None
        entry.magnification = _as_float(objective.get("magnification"))
        entry.wellplate_format = (data.get("sample") or {}).get("wellplate_format") or None
        entry.nz = _as_int((data.get("z_stack") or {}).get("nz"))
        entry.nt = _as_int((data.get("time_series") or {}).get("nt"))
        entry.channels = _channel_names(data.get("channels"))
        regions = (data.get("wellplate_scan") or {}).get("regions") or (data.get("flexible_scan") or {}).get(
            "positions"
        )
        if isinstance(regions, list) and regions:
            entry.num_regions = len(regions)
            fovs = sum(len(r.get("fovs") or []) for r in regions if isinstance(r, dict))
            if fovs:
                entry.num_fovs = fovs

    params = _load_json(os.path.join(path, "acquisition parameters.json"))
    if params:
        if entry.nz is None:
            entry.nz = _as_int(params.get("Nz"))
        if entry.nt is None:
            entry.nt = _as_int(params.get("Nt"))
        objective = params.get("objective") or {}
        if isinstance(objective, dict):
            if entry.objective is None:
                entry.objective = objective.get("name") or None
            if entry.magnification is None:
                entry.magnification = _as_float(objective.get("magnification"))

    if not entry.channels or entry.objective is None:
        channels_data = _load_yaml(os.path.join(path, "acquisition_channels.yaml"))
        if channels_data:
            if not entry.channels:
                entry.channels = _channel_names(channels_data.get("channels"))
            if entry.objective is None:
                entry.objective = channels_data.get("objective") or None

    if entry.num_fovs is None:
        rows = _count_csv_rows(os.path.join(path, "coordinates.csv"))
        if rows:
            entry.num_fovs = rows

    entry.image_format, entry.thumbnail_source = inspect_images(path)
    return entry


def scan_acquisitions(root: str) -> List[AcquisitionEntry]:
    """The acquisition folders directly under root, newest first. An unreadable root gives an empty list."""
    entries = []
    try:
        with os.scandir(root) as it:
            for d in it:
                try:
                    if not d.is_dir(follow_symlinks=False) or d.name.startswith("."):
                        continue
                except OSError:
                    continue
                if is_acquisition_folder(d.path):
                    entries.append(read_acquisition_entry(d.path))
    except OSError:
        return []
    entries.sort(key=lambda e: e.sort_time, reverse=True)
    return entries


# ---------------------------------------------------------------------------------------------------------
# Metadata for the history
# ---------------------------------------------------------------------------------------------------------

# The widgets start snaps as "snapped images" + id; start_new_experiment turns the spaces into underscores.
SNAP_FOLDER_PREFIXES = ("snapped_images", "snapped images")

# The metadata every history entry carries from its folder, in display order. The recorder appends machine,
# software_version, file_format and expected_images after these.
RECORDED_METADATA_KEYS: Tuple[str, ...] = (
    "experiment_id",
    "widget_type",
    "xy_mode",
    "objective",
    "magnification",
    "pixel_size_um",
    "wellplate_format",
    "channels",
    "num_regions",
    "num_fovs",
    "nz",
    "dz_um",
    "nt",
    "dt_s",
    "z_stacking_config",
    "use_piezo",
    "contrast_af",
    "laser_af",
    "laser_af_per_stack",
    "start_time",
    "image_format",
    "protocol",
)

# The end words the protocol runner writes into run_manifest.json once a run has closed.
_PROTOCOL_END_WORDS = ("finished", "stopped", "failed")
_MANIFEST_FILE = "run_manifest.json"


_SAVING_OPTION_FORMATS = {"ZARR_V3": "OME-Zarr", "OME_TIFF": "OME-TIFF", "MULTI_PAGE_TIFF": "Multi-page TIFF"}


def image_format_for_saving_option(option) -> Optional[str]:
    """The image_format text (as inspect_images names it) for a FileSavingOption or its value; None when unknown.
    Individual images are named by the current Acquisition.IMAGE_FORMAT."""
    value = getattr(option, "value", option)
    if not isinstance(value, str):
        return None
    if value.upper() == "INDIVIDUAL_IMAGES":
        import control._def  # imported late: this module stays light for the controller side

        return f"{str(control._def.Acquisition.IMAGE_FORMAT).upper()} images"
    return _SAVING_OPTION_FORMATS.get(value.upper())


def is_snap_folder(path: str) -> bool:
    return os.path.basename(os.path.normpath(path)).lower().startswith(SNAP_FOLDER_PREFIXES)


def _get(data, *keys):
    """data[k1][k2]..., or None when any level is missing or not a mapping."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _scaled(value, factor):
    return value * factor if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def metadata_from_yaml(path: str) -> dict:
    """RECORDED_METADATA_KEYS from the folder's acquisition.yaml alone (None where it says nothing, an empty
    channel list when it names none). image_format is always None: it needs the images."""
    data = _load_yaml(os.path.join(path, "acquisition.yaml")) or {}
    scan = _get(data, "wellplate_scan", "regions")
    if scan is None:
        scan = _get(data, "flexible_scan", "positions")
    regions = scan if isinstance(scan, list) else None
    protocol = data.get("protocol")
    return {
        "experiment_id": _get(data, "acquisition", "experiment_id") or os.path.basename(os.path.normpath(path)),
        "widget_type": _get(data, "acquisition", "widget_type"),
        "xy_mode": _get(data, "acquisition", "xy_mode"),
        "objective": _get(data, "objective", "name"),
        "magnification": _get(data, "objective", "magnification"),
        "pixel_size_um": _get(data, "objective", "pixel_size_um"),
        "wellplate_format": _get(data, "sample", "wellplate_format"),
        "channels": _channel_names(data.get("channels")),
        "num_regions": len(regions) if regions is not None else None,
        "num_fovs": (
            sum(len(r.get("fovs") or []) for r in regions if isinstance(r, dict)) if regions is not None else None
        ),
        "nz": _get(data, "z_stack", "nz"),
        "dz_um": _scaled(_get(data, "z_stack", "delta_z_mm"), 1000.0),
        "nt": _get(data, "time_series", "nt"),
        "dt_s": _get(data, "time_series", "delta_t_s"),
        "z_stacking_config": _get(data, "z_stack", "config"),
        "use_piezo": _get(data, "z_stack", "use_piezo"),
        "contrast_af": _get(data, "autofocus", "contrast_af"),
        "laser_af": _get(data, "autofocus", "laser_af"),
        "laser_af_per_stack": _get(data, "autofocus", "laser_af_per_stack"),
        "start_time": _get(data, "acquisition", "start_time"),
        "image_format": None,
        "protocol": dict(protocol) if isinstance(protocol, dict) else None,
    }


def metadata_from_folder(path: str) -> dict:
    """RECORDED_METADATA_KEYS for a saved acquisition: acquisition.yaml first, then what older folders have
    ("acquisition parameters.json", acquisition_channels.yaml, coordinates.csv), and the image format found on disk."""
    path = os.path.normpath(path)
    md = metadata_from_yaml(path)
    params = _load_json(os.path.join(path, "acquisition parameters.json")) or {}
    if md["nz"] is None:
        md["nz"] = _as_int(params.get("Nz"))
    if md["nt"] is None:
        md["nt"] = _as_int(params.get("Nt"))
    objective = params.get("objective")
    if isinstance(objective, dict):
        if md["objective"] is None:
            md["objective"] = objective.get("name") or None
        if md["magnification"] is None:
            md["magnification"] = _as_float(objective.get("magnification"))
    if not md["channels"] or md["objective"] is None:
        channels_data = _load_yaml(os.path.join(path, "acquisition_channels.yaml")) or {}
        if not md["channels"]:
            md["channels"] = _channel_names(channels_data.get("channels"))
        if md["objective"] is None:
            md["objective"] = channels_data.get("objective") or None
    if md["num_fovs"] is None:
        md["num_fovs"] = _count_csv_rows(os.path.join(path, "coordinates.csv")) or None
    md["image_format"] = inspect_images(path)[0]
    return {key: md[key] for key in RECORDED_METADATA_KEYS}


# ---------------------------------------------------------------------------------------------------------
# Protocol runs
# ---------------------------------------------------------------------------------------------------------


def _round_protocol(folder: str) -> Optional[dict]:
    data = _load_yaml(os.path.join(folder, "acquisition.yaml"))
    protocol = data.get("protocol") if data else None
    return protocol if isinstance(protocol, dict) else None


def protocol_rounds(folder: str) -> List[str]:
    """The acquisition folders directly under a protocol run folder, in (round, attempt, name) order. Empty when
    folder holds none, i.e. is not a protocol run folder."""
    try:
        with os.scandir(folder) as it:
            subdirs = [d.path for d in it if d.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    keyed = []
    for sub in subdirs:
        protocol = _round_protocol(sub)
        if protocol is None:
            continue
        keyed.append(
            ((_as_int(protocol.get("round")) or 0, _as_int(protocol.get("attempt")) or 0, os.path.basename(sub)), sub)
        )
    return [sub for _, sub in sorted(keyed)]


def _read_manifest(run_dir: str) -> dict:
    return _load_json(os.path.join(run_dir, _MANIFEST_FILE)) or {}


# ---------------------------------------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------------------------------------


@dataclass
class ImportResult:
    added: List[str] = field(default_factory=list)  # entry ids
    skipped_existing: int = 0
    skipped_snaps: int = 0
    errors: List[str] = field(default_factory=list)


def _number(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _folder_started_at(path: str, metadata: dict) -> Optional[float]:
    """The metadata's start time, else the folder name's timestamp, else the folder's modification time."""
    start = _number(metadata.get("start_time"))
    if start is not None:
        return start
    _, stamp = split_experiment_name(os.path.basename(os.path.normpath(path)))
    if stamp is not None:
        try:
            return stamp.timestamp()
        except (OverflowError, OSError, ValueError):
            pass
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _imported_event(path: str, *, kind: str, parent: Optional[str] = None) -> dict:
    metadata = metadata_from_folder(path)
    return {
        "type": "imported",
        "id": entry_id_for(path),
        "kind": kind,
        "name": os.path.basename(os.path.normpath(path)),
        "folder": os.path.abspath(path),
        "metadata": metadata,
        "started_at": _folder_started_at(path, metadata),
        "outcome": "unknown" if metadata["image_format"] == NO_IMAGES else "complete",
        "parent": parent,
        "protocol": metadata["protocol"],
    }


@dataclass
class _Known:
    """What the history holds already: entry ids, and the normalised folders of its entries and rounds (a located
    entry keeps its id but not its folder, so a folder is known by either)."""

    ids: set
    folders: set

    def has(self, entry_id: str, folder: str) -> bool:
        return entry_id in self.ids or normalize_folder(folder) in self.folders


def _known(store: HistoryStore) -> _Known:
    ids, folders = set(), set()
    for entry in store.load():
        for e in [entry, *entry.rounds]:
            ids.add(e.id)
            if e.folder:
                folders.add(normalize_folder(e.folder))
    return _Known(ids, folders)


def _run_preview(make_preview, folder: str, entry_id: str, result: ImportResult) -> None:
    """make_preview(folder, entry_id), contained: a raising callback is logged and reported, never propagated."""
    if make_preview is None:
        return
    try:
        make_preview(folder, entry_id)
    except Exception as e:
        _log.exception(f"Preview callback failed for {folder}")
        result.errors.append(f"{folder}: preview failed: {e}")


def _import_protocol_run(
    store: HistoryStore, run_dir: str, rounds: List[str], known: _Known, result: ImportResult, make_preview
) -> None:
    from control.acquisition_history_recorder import map_protocol_outcome  # imported late: it needs psutil

    run_id = entry_id_for(run_dir)
    if known.has(run_id, run_dir):
        result.skipped_existing += 1
        return
    manifest = _read_manifest(run_dir)
    events = [_imported_event(r, kind="run", parent=run_id) for r in rounds if not known.has(entry_id_for(r), r)]
    if not events:
        result.skipped_existing += 1  # every round is in the history already: nothing to group
        return
    result.skipped_existing += len(rounds) - len(events)
    started = [e["started_at"] for e in events if e["started_at"] is not None]
    first = _round_protocol(rounds[0]) or {}
    run_name = manifest.get("run_name") if isinstance(manifest.get("run_name"), str) else first.get("run_name")
    store.append(
        {
            "type": "protocol_started",
            "id": run_id,
            "name": os.path.basename(os.path.normpath(run_dir)),
            "folder": os.path.abspath(run_dir),
            "protocol": {"protocol": first.get("protocol"), "run_name": run_name},
            "pid": None,
            "pid_started_at": None,
            "machine": None,
            "at": _number(manifest.get("started_at"))
            or (min(started) if started else None)
            or _folder_started_at(run_dir, {}),
        }
    )
    result.added.append(run_id)
    imported = []
    try:
        for event in events:
            store.append(event)
            imported.append(event)
            result.added.append(event["id"])
            _run_preview(make_preview, event["folder"], event["id"], result)
    finally:
        # Once protocol_started is written the run must be closed, whatever happened to its rounds.
        status = manifest.get("status")
        if status in _PROTOCOL_END_WORDS:
            outcome, reason = map_protocol_outcome(status), status
        else:
            outcome = "complete" if imported and all(e["outcome"] == "complete" for e in imported) else "aborted"
            reason = "derived"
        ended = (
            _number(manifest.get("ended_at")) or (max(started) if started else None) or _folder_started_at(run_dir, {})
        )
        store.append(
            {"type": "protocol_finished", "id": run_id, "outcome": outcome, "reason": reason, "ended_at": ended}
        )


def import_folder(
    store: HistoryStore,
    root: str,
    *,
    include_snaps: bool,
    make_preview: Optional[Callable[[str, str], None]] = None,
) -> ImportResult:
    """Bring the saved acquisitions in root (root itself when it is one, else its immediate subfolders) into the
    history. Folders already in it are left alone; protocol run folders come in whole, closed, with their rounds.

    make_preview(folder, entry_id) is called for every imported acquisition (not for a protocol run's own entry,
    which has no images of its own); the caller makes the preview and records it. Failures are reported in
    ImportResult.errors, one per folder, and never raised.
    """
    result = ImportResult()
    root = os.path.normpath(root)
    try:
        known = _known(store)
        if is_acquisition_folder(root) or protocol_rounds(root):
            candidates = [root]
        else:
            with os.scandir(root) as it:
                candidates = sorted(
                    d.path for d in it if d.is_dir(follow_symlinks=False) and not d.name.startswith(".")
                )
    except OSError as e:
        result.errors.append(f"{root}: {e}")
        return result

    for path in candidates:
        try:
            rounds = [] if path == root and is_acquisition_folder(root) else protocol_rounds(path)
            if rounds:
                _import_protocol_run(store, path, rounds, known, result, make_preview)
                continue
            if not is_acquisition_folder(path):
                continue
            snap = is_snap_folder(path)
            if snap and not include_snaps:
                result.skipped_snaps += 1
                continue
            entry_id = entry_id_for(path)
            if known.has(entry_id, path):
                result.skipped_existing += 1
                continue
            store.append(_imported_event(path, kind="snap" if snap else "run"))
            result.added.append(entry_id)
            _run_preview(make_preview, os.path.abspath(path), entry_id, result)
        except Exception as e:
            _log.exception(f"Could not import {path}")
            result.errors.append(f"{path}: {e}")
    return result
