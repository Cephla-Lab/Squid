"""Run manifest - how a fluidics protocol run went. Pure data, written atomically by the ProtocolRunner
(the only writer) at every transition; read by the GUI's status card and by crash recovery at start-up."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

RUN_STATUSES = ("running", "paused", "held", "finished", "stopped", "failed")
TERMINAL_STATUSES = ("finished", "stopped", "failed")


class TecState(BaseModel):
    targets: List[float] = Field(default_factory=list)
    output_enabled: List[bool] = Field(default_factory=list)


class AttemptRecord(BaseModel):
    attempt: int
    # fluidics: finished|stopped|failed; imaging: completed|completed_with_errors|user_abort|error|failed_to_start
    outcome: Optional[str] = None
    message: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    elapsed_s: Optional[float] = None
    ended_position: Optional[int] = None  # fluidics: plan index in flight when it ended early (absolute)
    fluidics_run_id: Optional[str] = None
    folder: Optional[str] = None  # imaging: session folder name inside the run folder
    images: Optional[int] = None
    reagent_used_ul: Dict[str, float] = Field(default_factory=dict)  # port -> uL


class StepRecord(BaseModel):
    index: int
    kind: Literal["fluidics", "imaging"]
    round: Optional[str] = None
    label: str
    row_indices: List[int] = Field(default_factory=list)
    attempts: List[AttemptRecord] = Field(default_factory=list)
    skipped: bool = False


class RunCursor(BaseModel):
    step: Optional[int] = None
    attempt: int = 0
    sequence: Optional[int] = None  # last started plan position of a fluidics step (absolute)


class RunManifest(BaseModel):
    schema_version: int = 1
    run_name: str
    run_dir: str
    protocol_name: Optional[str] = None
    protocol_sha256: Optional[str] = None
    status: str = "running"
    hold_reason: Optional[str] = None
    hold_message: Optional[str] = None
    cursor: RunCursor = Field(default_factory=RunCursor)
    steps: List[StepRecord] = Field(default_factory=list)
    tec: Optional[TecState] = None
    pid: int
    heartbeat_at: float
    started_at: float
    ended_at: Optional[float] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def step(self, index: int) -> StepRecord:
        return self.steps[index]
