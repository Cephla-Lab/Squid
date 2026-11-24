"""Run manifest - how a fluidics protocol run went. Pure data, written atomically by the ProtocolRunner
(the only writer) at every transition; read by the GUI's status card and by crash recovery at start-up."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

RUN_STATUSES = ("running", "paused", "held", "finished", "stopped", "failed")
TERMINAL_STATUSES = ("finished", "stopped", "failed")


class TecState(BaseModel):
    targets: List[float] = Field(default_factory=list)
    output_enabled: List[bool] = Field(default_factory=list)


class ReleaseSettleRecord(BaseModel):
    """What the release-and-settle wait before an imaging attempt saw (imaging.release_and_settle)."""

    port_before: Optional[int] = None
    release_port: int
    mode: Literal["laser_af", "fixed_wait"]
    settled: Optional[bool] = None  # None for a fixed wait
    settle_s: Optional[float] = None  # after the valve reached the port, when settled
    waited_s: float = 0.0  # after the valve reached the port, in all
    valve_move_s: Optional[float] = None  # how long the valve took to get there
    baseline_um: Optional[float] = None  # laser AF displacement before the move (median of one window)
    final_um: Optional[float] = None  # ... and over the last window
    rise_um: Optional[float] = None  # final - baseline: how far the coverslip came back
    last_change_um: Optional[float] = None  # median change between the last two windows
    # flow-sensor volume from the move on, in the draw direction; None: no sensor, or no reading before the move
    inflow_ul: Optional[float] = None
    message: Optional[str] = None


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
    release_and_settle: Optional[ReleaseSettleRecord] = None  # imaging: the wait before the acquisition


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
