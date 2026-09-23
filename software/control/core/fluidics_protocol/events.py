"""What the ProtocolRunner tells its listener (one callable; the GUI marshals to the Qt thread)."""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Union

from control.models.fluidics_run import TecState


class RunnerState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    HELD = "HELD"
    ENDED = "ENDED"


class HoldAction(str, Enum):
    RESUME = "resume"  # fluidics: resume from the interrupted sequence (plan tail); imaging: same as RESTART
    RESTART = "restart"  # the step again from its beginning, as a new attempt
    SKIP = "skip"  # leave the step as it is and go on
    ACCEPT = "accept"  # completed_with_errors only: take the data and go on
    END = "end"  # end the run (outcome "stopped")


@dataclass(frozen=True)
class Hold:
    step_index: int
    attempt: int
    kind: str  # "fluidics" | "imaging"
    reason: str  # fluidics: stopped|failed; imaging: user_abort|error|failed_to_start|completed_with_errors; recovered
    message: Optional[str]
    resume_position: Optional[int]  # fluidics: absolute plan index to resume from
    tec_before: Optional[TecState]  # TEC targets/output before the step (offer "restore" on resume)
    can_resume: bool
    can_accept: bool


@dataclass(frozen=True)
class StateChanged:
    state: RunnerState
    hold: Optional[Hold] = None


@dataclass(frozen=True)
class StepStarted:
    step_index: int
    attempt: int
    kind: str
    label: str


@dataclass(frozen=True)
class StepEnded:
    step_index: int
    attempt: int
    outcome: str
    message: Optional[str] = None


@dataclass(frozen=True)
class SequenceProgress:
    step_index: int
    position: int  # absolute plan index
    total: int
    label: str


@dataclass(frozen=True)
class RunFinished:
    outcome: str  # finished | stopped | failed


RunnerEvent = Union[StateChanged, StepStarted, StepEnded, SequenceProgress, RunFinished]
Listener = Callable[[RunnerEvent], None]
