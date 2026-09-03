"""Run folder layout and the manifest on disk (the runner is the only writer).

{run_name}_{YYYY-MM-DD_HH-MM-SS}/      the only timestamp anywhere
|-- protocol.yaml                      exactly what ran (blocks inlined)
|-- run_manifest.json                  status, cursor, attempts - atomic rewrite at every transition
|-- run.log                            protocol + fluidics log for the run
`-- R01_image/ ...                     one Squid experiment folder per imaging attempt (never renamed)
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from control.models.fluidics_run import RunManifest

MANIFEST_NAME = "run_manifest.json"
PROTOCOL_COPY_NAME = "protocol.yaml"
RUN_LOG_NAME = "run.log"
ABORTED_MARKER = "aborted.json"
STEP_INFO_NAME = "protocol_step.json"

PathLike = Union[str, os.PathLike]


_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def run_folder_name(run_name: str, when: datetime) -> str:
    safe = run_name.strip().replace(" ", "_")
    if not _RUN_NAME_RE.match(safe):
        raise ValueError(f"Run name '{run_name}' must be a plain name (letters, digits, spaces, . _ -)")
    return f"{safe}_{when:%Y-%m-%d_%H-%M-%S}"


def create_run_dir(base_dir: PathLike, run_name: str, when: Optional[datetime] = None) -> Path:
    run_dir = Path(base_dir) / run_folder_name(run_name, when or datetime.now())
    if run_dir.exists():
        raise FileExistsError(str(run_dir))
    run_dir.mkdir(parents=True)
    return run_dir


def atomic_write_json(path: PathLike, data: dict) -> None:
    """tmp file in the same directory + fsync + os.replace (the squid.acquisition_state pattern)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".manifest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_manifest(run_dir: PathLike, manifest: RunManifest) -> Path:
    path = Path(run_dir) / MANIFEST_NAME
    atomic_write_json(path, manifest.model_dump(mode="json"))
    return path


def read_manifest(run_dir: PathLike) -> RunManifest:
    with open(Path(run_dir) / MANIFEST_NAME, "r", encoding="utf-8") as f:
        return RunManifest.model_validate_json(f.read())


def write_aborted_marker(folder: PathLike, info: dict) -> None:
    atomic_write_json(Path(folder) / ABORTED_MARKER, info)


def write_step_info(folder: PathLike, info: dict) -> None:
    atomic_write_json(Path(folder) / STEP_INFO_NAME, info)


def sha256_of_file(path: PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find_unfinished_runs(base_dir: PathLike) -> List[RunManifest]:
    """Runs under base_dir whose manifest is non-terminal and whose writer process is gone, newest first."""
    base = Path(base_dir)
    if not base.is_dir():
        return []
    found: List[RunManifest] = []
    for child in base.iterdir():
        manifest_path = child / MANIFEST_NAME
        if not manifest_path.is_file():
            continue
        try:
            manifest = read_manifest(child)
        except Exception:
            continue
        if manifest.is_terminal or pid_alive(manifest.pid):
            continue
        found.append(manifest)
    return sorted(found, key=lambda man: man.started_at, reverse=True)


def reagent_totals(manifest) -> "tuple[dict[int, float], dict[int, float]]":
    """Per-port reagent use: (whole-run totals, the most recent reagent-using attempt)."""
    totals: dict = {}
    last_step: dict = {}
    for step in manifest.steps:
        for attempt in step.attempts:
            if not attempt.reagent_used_ul:
                continue
            for port_str, ul in attempt.reagent_used_ul.items():
                port = int(port_str)
                totals[port] = totals.get(port, 0.0) + ul
            last_step = {int(p): u for p, u in attempt.reagent_used_ul.items()}
    return totals, last_step
