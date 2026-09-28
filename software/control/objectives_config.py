"""Per-machine list of the mounted objectives: machine_configs/objectives.yaml.

Leaf module. It imports only pydantic, yaml, the standard library and
control.objective_changer_constants: control._def loads it while it is still
initializing, and control/models/__init__.py (like most of control.*) imports _def.
"""

import ast
import math
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from control.objective_changer_constants import NIMOTION_TURRET_SLOTS, XERYON_SLOTS

# Read at call time, so tests can point it elsewhere before control._def is imported.
OBJECTIVES_YAML_PATH = Path(__file__).resolve().parent.parent / "machine_configs" / "objectives.yaml"

_RESERVED_NAMES = {"general", ".", ".."}
# Names become channel_configs/<name>.yaml file stems: reject characters that are illegal in a
# Windows file name (":" also makes an NTFS alternate data stream) and Windows' reserved device
# names, so a config authored on macOS/Linux still produces valid file names on a Windows bench PC.
_WINDOWS_ILLEGAL_NAME_CHARS = '<>:"|?*'
_WINDOWS_RESERVED_DEVICE_NAMES = (
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{d}" for d in range(1, 10)} | {f"LPT{d}" for d in range(1, 10)}
)


class ObjectivesConfigError(Exception):
    def __init__(self, path, field: str, reason: str):
        self.path = Path(path) if path is not None else None
        self.field = field
        self.reason = reason
        super().__init__(
            f"{path}: {field}: {reason}. Fix or delete this file; without it the objective list "
            f"comes from objectives.csv and the machine .ini, as before."
        )


class ChangerKind(str, Enum):
    NIMOTION_TURRET = "nimotion_turret"
    XERYON = "xeryon"
    NONE = "none"


SLOT_COUNTS = {
    ChangerKind.NIMOTION_TURRET: NIMOTION_TURRET_SLOTS,
    ChangerKind.XERYON: XERYON_SLOTS,
    ChangerKind.NONE: 0,
}


class ChangerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ChangerKind


class ObjectiveEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    magnification: float
    na: float
    tube_lens_f_mm: float
    slot: Optional[int] = None
    model: str = ""
    serial: str = ""


class ObjectivesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    changer: ChangerConfig
    objectives: List[ObjectiveEntry]


def changer_kind_for_flags(use_xeryon: bool, use_turret: bool) -> ChangerKind:
    if use_turret:
        return ChangerKind.NIMOTION_TURRET
    if use_xeryon:
        return ChangerKind.XERYON
    return ChangerKind.NONE


def parse_objectives_config(data, path=None) -> ObjectivesConfig:
    try:
        return ObjectivesConfig.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "(file)"
        raise ObjectivesConfigError(path, field, first["msg"]) from e


def validate_objectives_config(config: ObjectivesConfig, *, use_xeryon: bool, use_turret: bool, path=None) -> None:
    def fail(field, reason):
        raise ObjectivesConfigError(path, field, reason)

    if config.version != 1:
        fail("version", f"is {config.version}, but only version 1 is supported")
    if not config.objectives:
        fail("objectives", "at least one objective is required")
    expected = changer_kind_for_flags(use_xeryon, use_turret)
    if config.changer.kind is not expected:
        fail(
            "changer.kind",
            f"is {config.changer.kind.value}, but the machine .ini selects {expected.value} "
            f"(USE_OBJECTIVE_TURRET / USE_XERYON)",
        )
    n_slots = SLOT_COUNTS[config.changer.kind]
    names: Dict[str, str] = {}
    slots: Dict[int, str] = {}
    for i, obj in enumerate(config.objectives):
        where = f"objectives[{i}]"
        name = obj.name
        if not name or name != name.strip():
            fail(f"{where}.name", "must be non-empty, with no leading or trailing spaces")
        if "/" in name or "\\" in name or name.lower() in _RESERVED_NAMES:
            fail(f"{where}.name", f"'{name}' is reserved or contains a path separator (names become file names)")
        for ch in name:
            if ch in _WINDOWS_ILLEGAL_NAME_CHARS:
                fail(f"{where}.name", f"'{name}' contains '{ch}', which is invalid in a Windows file name")
            if ord(ch) < 32:
                fail(f"{where}.name", f"'{name}' contains a control character, which is invalid in a file name")
        if name.split(".")[0].upper() in _WINDOWS_RESERVED_DEVICE_NAMES:
            fail(f"{where}.name", f"'{name}' is a reserved Windows device name and cannot be used as a file name")
        if name.lower() in names:
            fail(
                f"{where}.name", f"'{name}' duplicates '{names[name.lower()]}' (names are compared case-insensitively)"
            )
        names[name.lower()] = name
        if not (math.isfinite(obj.magnification) and obj.magnification > 0):
            fail(f"{where}.magnification", "must be greater than 0")
        if not (math.isfinite(obj.na) and 0 < obj.na <= 1.5):
            fail(f"{where}.na", "must be greater than 0 and at most 1.5")
        if not (math.isfinite(obj.tube_lens_f_mm) and obj.tube_lens_f_mm > 0):
            fail(f"{where}.tube_lens_f_mm", "must be greater than 0")
        if n_slots == 0:
            if obj.slot is not None:
                fail(f"{where}.slot", "must be empty when changer.kind is none")
            continue
        if obj.slot is None:
            fail(f"{where}.slot", f"is required on a {config.changer.kind.value}")
        if not 1 <= obj.slot <= n_slots:
            fail(f"{where}.slot", f"must be 1..{n_slots}")
        if obj.slot in slots:
            fail(
                f"{where}.slot",
                f"slot {obj.slot} is already used by '{slots[obj.slot]}' (one mounted objective per slot)",
            )
        slots[obj.slot] = name


def load_objectives_config(path=None) -> Optional[ObjectivesConfig]:
    path = Path(path) if path is not None else OBJECTIVES_YAML_PATH
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ObjectivesConfigError(path, "(file)", f"cannot be read: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ObjectivesConfigError(path, "(file)", f"is not valid YAML: {e}") from e
    if data is None:
        raise ObjectivesConfigError(path, "(file)", "is empty")
    return parse_objectives_config(data, path)


def save_objectives_config(config: ObjectivesConfig, path=None) -> None:
    """Write machine_configs/objectives.yaml. Writes to a temp file in the same directory and
    os.replace()s it onto `path` (an atomic publish on both POSIX and Windows for a
    same-filesystem rename): a reader never observes a partially-written file, and a failed
    publish leaves the previous file untouched, with no temp file left behind (R6b)."""
    path = Path(path) if path is not None else OBJECTIVES_YAML_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def to_objectives_dict(config: ObjectivesConfig) -> Dict[str, Dict[str, float]]:
    return {
        o.name: {"magnification": o.magnification, "NA": o.na, "tube_lens_f_mm": o.tube_lens_f_mm}
        for o in config.objectives
    }


def to_turret_positions(config: ObjectivesConfig) -> Dict[str, int]:
    return {o.name: o.slot for o in config.objectives}


def to_xeryon_lists(config: ObjectivesConfig) -> Tuple[List[str], List[str]]:
    return (
        [o.name for o in config.objectives if o.slot == 1],
        [o.name for o in config.objectives if o.slot == 2],
    )


def serial_matches(recorded: str, current: str) -> bool:
    """Spec A §4.5, used by B and C: a serial counts only when one was recorded at calibration."""
    return recorded == "" or recorded == current


def _as_name_list(value) -> List[str]:
    """An ini-derived list of objective names; the shipped Xeryon ini gives a Python-literal string."""
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _as_slot_map(value) -> Dict[str, int]:
    """An ini-derived objective -> slot map; tolerates a Python-literal string like _as_name_list."""
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return {}
    if isinstance(value, dict):
        return {str(k): int(v) for k, v in value.items()}
    return {}


@dataclass
class EditorRow:
    """One row of the Objectives editor. Optics are None while blank; copy_from names the
    mounted objective whose channel settings a newly added row copies (None for existing rows)."""

    name: str
    magnification: Optional[float]
    na: Optional[float]
    tube_lens_f_mm: Optional[float]
    slot: Optional[int]
    model: str = ""
    serial: str = ""
    copy_from: Optional[str] = None


def _row_from_catalog(name, slot, catalog) -> EditorRow:
    optics = catalog.get(name)
    if optics is None:
        return EditorRow(name, None, None, None, slot)
    return EditorRow(name, optics["magnification"], optics["NA"], optics["tube_lens_f_mm"], slot)


def seed_rows(kind, *, catalog, turret_positions, xeryon_pos_1, xeryon_pos_2) -> List[EditorRow]:
    """First-open rows when no objectives.yaml exists (spec A §5)."""
    turret_positions = _as_slot_map(turret_positions)
    xeryon_pos_1 = _as_name_list(xeryon_pos_1)
    xeryon_pos_2 = _as_name_list(xeryon_pos_2)

    if kind is ChangerKind.NIMOTION_TURRET:
        pairs = sorted(turret_positions.items(), key=lambda item: item[1])
    elif kind is ChangerKind.XERYON:
        pairs = [(name, 1) for name in xeryon_pos_1] + [(name, 2) for name in xeryon_pos_2]
    else:
        pairs = [(name, None) for name in catalog]
    return [_row_from_catalog(name, slot, catalog) for name, slot in pairs]


def config_to_rows(config: ObjectivesConfig) -> List[EditorRow]:
    return [
        EditorRow(o.name, o.magnification, o.na, o.tube_lens_f_mm, o.slot, o.model, o.serial) for o in config.objectives
    ]


def rows_to_config(kind: ChangerKind, rows: List[EditorRow]) -> ObjectivesConfig:
    return parse_objectives_config(
        {
            "version": 1,
            "changer": {"kind": kind.value},
            "objectives": [
                {
                    "name": r.name,
                    "magnification": r.magnification,
                    "na": r.na,
                    "tube_lens_f_mm": r.tube_lens_f_mm,
                    "slot": r.slot,
                    "model": r.model,
                    "serial": r.serial,
                }
                for r in rows
            ],
        }
    )


def slot_conflicts(rows: List[EditorRow]) -> Set[int]:
    """Indexes of rows whose slot another row also uses."""
    by_slot: Dict[int, List[int]] = {}
    for i, row in enumerate(rows):
        if row.slot is not None:
            by_slot.setdefault(row.slot, []).append(i)
    return {i for indexes in by_slot.values() if len(indexes) > 1 for i in indexes}


def nearest_by_magnification(magnification: Optional[float], rows: List[EditorRow]) -> Optional[str]:
    """The row with optics closest in magnification (the lowest one when magnification is None)."""
    candidates = [r for r in rows if r.magnification is not None]
    if not candidates:
        return None
    if magnification is None:
        return min(candidates, key=lambda r: r.magnification).name
    return min(candidates, key=lambda r: abs(r.magnification - magnification)).name
