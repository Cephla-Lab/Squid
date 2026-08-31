"""Fluidics protocol file model.

A protocol is the Squid-Fluidics library's sequence file (a `sequences:` list of flow_reagent /
priming / clean_up / set_temperature ... rows) plus, per row, an optional `round:` label and a Squid-only
row type `imaging`, and a Squid-only `imaging:` header holding the settings and coordinate blocks the
imaging rows point at. The library accepts `round` natively; `imaging` is Squid-only, so
`strip_for_library()` produces the rows every library call receives (it also drops `round`, keeping
Squid tolerant of older library installs).

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
DEFAULT_FOLDER_PATTERN = "{round}_{step}"
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
    folder_pattern: str = DEFAULT_FOLDER_PATTERN
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


def strip_for_library(rows: List[dict]) -> List[dict]:
    """The rows the Squid-Fluidics library may see: no imaging rows, no `round` key. Copies; never mutates."""
    return [{k: v for k, v in row.items() if k != "round"} for row in rows if row.get("type") != IMAGING_TYPE]


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


def render_folder(
    pattern: str, *, round_label: Optional[str], step_name: Optional[str], index: int, run_name: str = ""
) -> str:
    """Fill a folder pattern: {round}, {step}, {index} (imaging ordinal, 1-based), {run}."""
    return pattern.format(round=round_label or "", step=step_name or "image", index=index, run=run_name)


def folder_problems(protocol: ProtocolFile) -> List[str]:
    """Every folder rule an imaging row can break: present, filesystem-safe, unique among included rows."""
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for i, row in protocol.imaging_rows():
        if not row.include:
            continue
        label = row.name or f"row {i + 1}"
        if not row.folder:
            problems.append(f"{label} ({row.round or 'no round'}): no folder name")
            continue
        if not _FOLDER_RE.match(row.folder):
            problems.append(f"{label}: folder '{row.folder}' must be a plain name (letters, digits, . _ -)")
        if row.folder in seen:
            problems.append(f"{label}: duplicate folder '{row.folder}' (also row {seen[row.folder] + 1})")
        else:
            seen[row.folder] = i
    return problems


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


def expand_rounds(
    protocol: ProtocolFile,
    template_round: str,
    count: int,
    label_pattern: str = "R{n:02d}",
    start: int = 2,
    port_row_name: Optional[str] = None,
    ports: Optional[List[int]] = None,
) -> ProtocolFile:
    """Insert `count` copies of the rows labelled `template_round` right after that round, relabelled
    `label_pattern.format(n=...)` from `start`; in each copy the row named `port_row_name` gets the next
    entry of `ports`, and imaging folders are re-rendered from the header pattern. Rows after the template
    round (a `final` clean-up group) stay last. Returns a new ProtocolFile."""
    template = [row for row in protocol.sequences if row.get("round") == template_round]
    if not template:
        raise ValueError(f"No rows carry the round label '{template_round}'")
    insert_at = max(i for i, row in enumerate(protocol.sequences) if row.get("round") == template_round) + 1
    if port_row_name is not None:
        if ports is None or len(ports) < count:
            have = 0 if ports is None else len(ports)
            raise ValueError(f"{count} rounds need {count} ports for '{port_row_name}', got {have}")
    new_rows: List[dict] = []
    # {index} in the folder pattern is the imaging ordinal at the insertion point, so rows after it keep theirs.
    imaging_ordinal = sum(
        1 for r in protocol.sequences[:insert_at] if r.get("type") == IMAGING_TYPE and r.get("include", True)
    )
    for k in range(count):
        label = label_pattern.format(n=start + k)
        for row in template:
            copy = dict(row)
            copy["round"] = label
            if port_row_name is not None and copy.get("name") == port_row_name and "fluidic_port" in copy:
                copy["fluidic_port"] = ports[k]
            if copy.get("type") == IMAGING_TYPE:
                imaging_ordinal += 1
                copy["folder"] = render_folder(
                    protocol.imaging.folder_pattern,
                    round_label=label,
                    step_name=copy.get("name"),
                    index=imaging_ordinal,
                )
            new_rows.append(copy)
    sequences = list(protocol.sequences)
    sequences[insert_at:insert_at] = new_rows
    return protocol.model_copy(update={"sequences": sequences})
