"""Per-machine list of the mounted objectives: machine_configs/objectives.yaml.

Leaf module. It imports only pydantic, yaml, the standard library and
control.objective_changer_constants: control._def loads it while it is still
initializing, and control/models/__init__.py (like most of control.*) imports _def.
"""

from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from control.objective_changer_constants import NIMOTION_TURRET_SLOTS, XERYON_SLOTS

# Read at call time, so tests can point it elsewhere before control._def is imported.
OBJECTIVES_YAML_PATH = Path(__file__).resolve().parent.parent / "machine_configs" / "objectives.yaml"

_RESERVED_NAMES = {"general", ".", ".."}


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
        if name.lower() in names:
            fail(
                f"{where}.name", f"'{name}' duplicates '{names[name.lower()]}' (names are compared case-insensitively)"
            )
        names[name.lower()] = name
        if not obj.magnification > 0:
            fail(f"{where}.magnification", "must be greater than 0")
        if not 0 < obj.na <= 1.5:
            fail(f"{where}.na", "must be greater than 0 and at most 1.5")
        if not obj.tube_lens_f_mm > 0:
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
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ObjectivesConfigError(path, "(file)", f"is not valid YAML: {e}") from e
    if data is None:
        raise ObjectivesConfigError(path, "(file)", "is empty")
    return parse_objectives_config(data, path)


def save_objectives_config(config: ObjectivesConfig, path=None) -> None:
    path = Path(path) if path is not None else OBJECTIVES_YAML_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))


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
