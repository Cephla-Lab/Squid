"""Fluidics protocol file model.

A protocol is the Squid-Fluidics library's sequence file (a `sequences:` list of flow_reagent /
priming / clean_up / set_temperature ... rows) plus, per row, an optional `round:` label and a Squid-only
row type `imaging`, and a Squid-only `imaging:` header holding the settings and coordinate blocks the
imaging rows point at. The library accepts `round` natively; `imaging` is Squid-only, so
`strip_for_library()` produces the rows every library call receives.

Qt-free, library-free: fluidics rows are kept as plain dicts here and validated by the library in
control.core.fluidics_protocol.resolve.
"""

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

IMAGING_TYPE = "imaging"
_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ObjectiveInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    magnification: Optional[float] = None
    pixel_size_um: Optional[float] = None
    NA: Optional[float] = None
    camera_binning: Optional[List[int]] = None
    sensor_pixel_size_um: Optional[float] = None


class ZStackSettings(BaseModel):
    nz: int = Field(1, ge=1)
    delta_z_um: float = 1.0
    config: str = "FROM CENTER"
    use_piezo: bool = False
    z_range_mm: Optional[List[float]] = None


class AutofocusSettings(BaseModel):
    contrast_af: bool = False
    laser_af: bool = False


class SettingsBlock(BaseModel):
    """Imaging settings for an imaging row - everything except positions (from Apply, or a saved acquisition)."""

    applied_at: Optional[str] = None
    source: Optional[str] = None
    source_path: Optional[str] = None
    objective: ObjectiveInfo = Field(default_factory=ObjectiveInfo)
    channels: List[str] = Field(default_factory=list)
    z_stack: ZStackSettings = Field(default_factory=ZStackSettings)
    autofocus: AutofocusSettings = Field(default_factory=AutofocusSettings)
    widget_type: str = "wellplate"
    xy_mode: str = "Load Coordinates"
    scan_size_mm: float = 0.0
    overlap_percent: float = 10.0
    skip_saving: bool = False


class Region(BaseModel):
    name: str
    fovs: List[List[float]] = Field(default_factory=list)
    center_mm: Optional[List[float]] = None
    shape: Optional[str] = None


class CoordinatesBlock(BaseModel):
    """Positions for an imaging row - regions with explicit FOV lists (from Capture, a saved acquisition or a CSV)."""

    captured_at: Optional[str] = None
    source: Optional[str] = None
    source_path: Optional[str] = None
    wellplate_format: Optional[str] = None
    regions: List[Region] = Field(default_factory=list)

    @property
    def fov_count(self) -> int:
        return sum(len(r.fovs) for r in self.regions)


class ImagingHeader(BaseModel):
    settings: Dict[str, SettingsBlock] = Field(default_factory=dict)
    coordinates: Dict[str, CoordinatesBlock] = Field(default_factory=dict)


class ImagingRow(BaseModel):
    """`type: imaging` - one acquisition into `folder`; `settings`/`coordinates` name a header block or a file."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["imaging"]
    name: Optional[str] = None
    round: Optional[str] = None
    include: bool = True
    folder: Optional[str] = None
    settings: Optional[str] = None
    coordinates: Optional[str] = None


class ProtocolFile(BaseModel):
    version: int = 1
    name: Optional[str] = None
    imaging: ImagingHeader = Field(default_factory=ImagingHeader)
    sequences: List[dict] = Field(default_factory=list)

    @field_validator("sequences")
    @classmethod
    def _rows_are_typed_dicts(cls, rows: List[dict]) -> List[dict]:
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("type"), str):
                raise ValueError(f"sequences[{i}] must be a mapping with a string 'type'")
            if row["type"] == IMAGING_TYPE:
                ImagingRow.model_validate(row)  # raises with the offending key
        return rows

    def imaging_rows(self) -> List[Tuple[int, ImagingRow]]:
        return [
            (i, ImagingRow.model_validate(row))
            for i, row in enumerate(self.sequences)
            if row.get("type") == IMAGING_TYPE
        ]

    def imaging_dicts(self) -> List[dict]:
        """The imaging rows as the raw dicts they are stored as - for repaint paths
        that must not pay per-row Pydantic validation (imaging_rows() validates)."""
        return [r for r in self.sequences if r.get("type") == IMAGING_TYPE]

    def round_labels(self) -> List[str]:
        """The distinct round labels, in first-appearance order."""
        return list(dict.fromkeys(r.get("round") for r in self.sequences if r.get("round")))

    def summary_line(self) -> str:
        imaging = len(self.imaging_dicts())
        return f"{len(self.round_labels())} rounds · {len(self.sequences)} rows · {imaging} imaging"


def strip_for_library(rows: List[dict]) -> List[dict]:
    """The rows the Squid-Fluidics library may see: no imaging rows (`round` passes through -
    the library accepts it natively). Copies; never mutates."""
    return [dict(row) for row in rows if row.get("type") != IMAGING_TYPE]


def load_protocol(path: str) -> ProtocolFile:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return ProtocolFile()
    if isinstance(data, list):  # a bare sequence list
        data = {"sequences": data}
    return ProtocolFile.model_validate(data)


def _type_first(row: dict) -> dict:
    ordered = {"type": row["type"]} if "type" in row else {}
    ordered.update({k: v for k, v in row.items() if k != "type"})
    return ordered


def protocol_to_dict(protocol: ProtocolFile) -> dict:
    header = protocol.imaging.model_dump(exclude_none=True)
    for key in ("settings", "coordinates"):
        if not header.get(key):
            header.pop(key, None)
    out = {"version": protocol.version}
    if protocol.name is not None:
        out["name"] = protocol.name
    out["imaging"] = header
    out["sequences"] = [_type_first(row) for row in protocol.sequences]
    return out


def save_protocol(protocol: ProtocolFile, path: str) -> None:
    """Write the protocol as plain YAML the standalone fluidics GUI also opens (atomic replace)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".protocol-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(protocol_to_dict(protocol), f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def included(row: dict) -> bool:
    """The editor checkbox's default, written once: rows run unless excluded."""
    return bool(row.get("include", True))


def ref_for_path(path: str, base: Optional[str]) -> str:
    """How a file-backed settings/coordinates reference is written: relative to the
    protocol file when the target sits under its directory, absolute otherwise."""
    if base and os.path.abspath(path).startswith(base + os.sep):
        return os.path.relpath(path, base)
    return path


def rebase_file_refs(protocol: ProtocolFile, old_base: str, new_base: str) -> None:
    """File-backed references are relative to the protocol file, so saving it into
    another directory must re-point them at the same files. Header keys are untouched."""
    for row in protocol.imaging_dicts():
        for field in ("settings", "coordinates"):
            ref = row.get(field)
            if not ref or ref in getattr(protocol.imaging, field) or os.path.isabs(ref):
                continue
            target = os.path.normpath(os.path.join(old_base, ref))
            row[field] = ref_for_path(target, new_base)


def imaging_folder(round_label: Optional[str], folder_name: str) -> str:
    """The actual output folder for an imaging row: '{round}_{folder_name}', or just the
    folder_name when the row carries no round. The folder_name field stays a stable base
    (e.g. 'image') so every round reads the same, and the round supplies the uniqueness."""
    base = (folder_name or "").strip()
    return f"{round_label}_{base}" if round_label else base


def folder_problems_by_row(protocol: ProtocolFile) -> Dict[int, str]:
    """Every folder rule an imaging row can break, keyed by the offending row's index
    (imaging rows commonly share a display name, so a name is no key)."""
    problems: Dict[int, str] = {}
    seen: Dict[str, int] = {}
    for i, row in protocol.imaging_rows():
        if not row.include:
            continue
        label = row.name or f"row {i + 1}"
        if not row.folder:
            problems[i] = f"{label} ({row.round or 'no round'}): no folder name"
            continue
        if not _FOLDER_RE.match(row.folder):
            problems[i] = f"{label}: folder name '{row.folder}' must be a plain name (letters, digits, . _ -)"
            continue
        actual = imaging_folder(row.round, row.folder)  # the output folder is {round}_{folder_name}
        if actual in seen:
            problems[i] = f"{label}: folder '{actual}' collides with row {seen[actual] + 1}"
        else:
            seen[actual] = i
    return problems


def folder_problems(protocol: ProtocolFile) -> List[str]:
    """The folder rules as messages (the pre-flight list); one per offending row."""
    return list(folder_problems_by_row(protocol).values())


@dataclass
class FluidicsStep:
    index: int
    round: Optional[str]
    rows: List[dict]
    row_indices: List[int]
    kind: str = field(default="fluidics", init=False)

    @property
    def label(self) -> str:
        return self.round or "fluidics"


@dataclass
class ImagingStep:
    index: int
    round: Optional[str]
    row: ImagingRow
    row_index: int
    kind: str = field(default="imaging", init=False)

    @property
    def label(self) -> str:
        return self.row.name or self.row.folder or "imaging"


Step = Union[FluidicsStep, ImagingStep]


def split_into_steps(protocol: ProtocolFile) -> List[Step]:
    """Included rows -> steps: a contiguous block of fluidics rows sharing a round label is one fluidics
    step (one library run, so the library's pause/resume-from-sequence work inside it); every imaging row
    is its own step. Excluded rows are dropped before grouping."""
    steps: List[Step] = []
    pending_rows: List[dict] = []
    pending_indices: List[int] = []
    pending_round: Optional[str] = None

    def flush():
        nonlocal pending_rows, pending_indices
        if pending_rows:
            steps.append(FluidicsStep(len(steps), pending_round, pending_rows, pending_indices))
            pending_rows, pending_indices = [], []

    for i, row in enumerate(protocol.sequences):
        if not row.get("include", True):
            continue
        if row.get("type") == IMAGING_TYPE:
            flush()
            steps.append(ImagingStep(len(steps), row.get("round"), ImagingRow.model_validate(row), i))
            continue
        if pending_rows and row.get("round") != pending_round:
            flush()
        pending_round = row.get("round")
        pending_rows.append(row)
        pending_indices.append(i)
    flush()
    return steps


def round_blocks(protocol: ProtocolFile) -> List[Tuple[Optional[str], List[int]]]:
    """The rows grouped into the contiguous round blocks the editor shows: each maximal run of
    consecutive rows sharing the same `round` value (a run with no round included) is one block,
    returned as (label, [sequence indices]). This is the grouping the tree headers and the
    whole-round move/remove/relabel operations act on, so it lives here in one place."""
    blocks: List[Tuple[Optional[str], List[int]]] = []
    current = object()  # sentinel unequal to any real label, including None
    for i, row in enumerate(protocol.sequences):
        label = row.get("round")
        if label != current:
            current = label
            blocks.append((label, []))
        blocks[-1][1].append(i)
    return blocks


def parse_port_list(spec: str) -> List[int]:
    """'2-4,7,9-10' -> [2, 3, 4, 7, 9, 10]."""
    ports: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)(?:-(\d+))?$", part)
        if not m:
            raise ValueError(f"Invalid port list entry: '{part}'")
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        if hi < lo:
            raise ValueError(f"Invalid port range: '{part}'")
        ports.extend(range(lo, hi + 1))
    return ports


# A well on a plate, as a sequence row's `well` names it after any `plate/` prefix. Rows and columns
# are the 96-well plate's, the only plate the fluidics library supports.
_WELL_RE = re.compile(r"^([A-Z])([1-9][0-9]?)$")
PLATE_ROWS = "ABCDEFGH"
PLATE_COLUMNS = 12


def _well_position(well: str) -> Tuple[int, int]:
    m = _WELL_RE.match(well.strip().upper())
    if not m or m.group(1) not in PLATE_ROWS or not 1 <= int(m.group(2)) <= PLATE_COLUMNS:
        raise ValueError(f"'{well}' is not a well (A1-{PLATE_ROWS[-1]}{PLATE_COLUMNS})")
    return PLATE_ROWS.index(m.group(1)), int(m.group(2))


def parse_well_list(spec: str) -> List[str]:
    """'A2-A4, B1' -> ['A2', 'A3', 'A4', 'B1']. A range runs along one row (A2-A12) or down one
    column (A1-H1). Wells only: each row of a round keeps its own plate."""
    wells: List[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            raise ValueError(f"'{part}': give wells only (A2), each row keeps its own plate")
        lo, _, hi = part.partition("-")
        (r0, c0), (r1, c1) = _well_position(lo), _well_position(hi or lo)
        if r0 == r1 and c0 <= c1:
            wells.extend(f"{PLATE_ROWS[r0]}{c}" for c in range(c0, c1 + 1))
        elif c0 == c1 and r0 <= r1:
            wells.extend(f"{PLATE_ROWS[r]}{c0}" for r in range(r0, r1 + 1))
        else:
            raise ValueError(f"Invalid well range: '{part}' (along one row, A2-A12, or down one column, A1-H1)")
    return wells


def next_wells(after: str, count: int) -> List[str]:
    """The `count` wells after `after` along its row, wrapping to the next row (A12 -> B1): the
    order the sample sipper protocols use (R01 on A1, R02 on A2). Stops at the plate's last well."""
    row, column = _well_position(after)
    wells: List[str] = []
    while len(wells) < count:
        column += 1
        if column > PLATE_COLUMNS:
            row, column = row + 1, 1
        if row >= len(PLATE_ROWS):
            break
        wells.append(f"{PLATE_ROWS[row]}{column}")
    return wells


def format_well_list(wells: List[str]) -> str:
    """The inverse of parse_well_list for runs along rows: ['A2', 'A3', 'A4', 'B1'] -> 'A2-A4, B1'."""
    runs: List[List[int]] = []  # [row, first column, last column]
    for r, c in map(_well_position, wells):
        if runs and runs[-1][0] == r and runs[-1][2] == c - 1:
            runs[-1][2] = c
        else:
            runs.append([r, c, c])
    return ", ".join(f"{PLATE_ROWS[r]}{c0}" + (f"-{PLATE_ROWS[r]}{c1}" if c1 != c0 else "") for r, c0, c1 in runs)


def well_rows(rows: List[dict]) -> List[dict]:
    """The rows that draw from a well (flow_reagent and push rows on a sipper rig)."""
    return [row for row in rows if row.get("well")]


def _move_to_well(ref: str, well: str) -> str:
    """`ref` (A1 or ssct/A1) moved to `well` on the same plate."""
    plate, slash, _ = ref.rpartition("/")
    return f"{plate}{slash}{well}"


def expand_rounds(
    protocol: ProtocolFile,
    template_round: str,
    count: int,
    label_pattern: str = "R{n:02d}",
    start: int = 2,
    port_row_name: Optional[str] = None,
    ports: Optional[List[int]] = None,
    wells: Optional[List[str]] = None,
) -> ProtocolFile:
    """Insert `count` copies of the rows labelled `template_round` right after that round, relabelled
    `label_pattern.format(n=...)` from `start`; in each copy the row named `port_row_name` gets the next
    entry of `ports`, every row that draws from a well gets the next entry of `wells` on its own plate
    (hyb A1 -> A2, push ssct/A1 -> ssct/A2), and each round's imaging folder gets its round label. Rows
    after the template round (a `final` clean-up group) stay last. Returns a new ProtocolFile."""
    template = [row for row in protocol.sequences if row.get("round") == template_round]
    if not template:
        raise ValueError(f"No rows carry the round label '{template_round}'")
    insert_at = max(i for i, row in enumerate(protocol.sequences) if row.get("round") == template_round) + 1
    if port_row_name is not None:
        if ports is None or len(ports) < count:
            have = 0 if ports is None else len(ports)
            raise ValueError(f"{count} rounds need {count} ports for '{port_row_name}', got {have}")
    if wells is not None:
        if not well_rows(template):
            raise ValueError(f"No row of round '{template_round}' draws from a well")
        if len(wells) < count:
            raise ValueError(f"{count} rounds need {count} wells, got {len(wells)}")
        # a typo is refused here, not at the first run; " b1" is written B1
        wells = [f"{PLATE_ROWS[r]}{c}" for r, c in map(_well_position, wells)]
    new_rows: List[dict] = []
    for k in range(count):
        label = label_pattern.format(n=start + k)
        for row in template:
            copy = dict(row)
            copy["round"] = label
            if port_row_name is not None and copy.get("name") == port_row_name and "fluidic_port" in copy:
                copy["fluidic_port"] = ports[k]
            if wells is not None and copy.get("well"):
                copy["well"] = _move_to_well(copy["well"], wells[k])
            # the folder_name stays the same base across rounds; imaging_folder derives the
            # per-round output folder ({round}_{folder_name}) at run time
            new_rows.append(copy)
    sequences = list(protocol.sequences)
    sequences[insert_at:insert_at] = new_rows
    return protocol.model_copy(update={"sequences": sequences})
