"""
Orchestrator state definitions and management.

Defines the state machine for experiment orchestration.
"""

import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Literal, Optional, Set, TYPE_CHECKING

from squid.core.events import Event

if TYPE_CHECKING:
    from squid.core.protocol.imaging_protocol import ImagingProtocol


class StepOutcome(Enum):
    """Outcome of a step execution."""

    SUCCESS = auto()
    SKIPPED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class StepResult:
    """Uniform result from executing any step (fluidics, imaging, intervention).

    Replaces the mixed pattern of exceptions + booleans + side effects with
    a single return type that callers can inspect.
    """

    outcome: StepOutcome
    step_type: str  # "fluidics", "imaging", "intervention"
    error_message: Optional[str] = None

    @property
    def success(self) -> bool:
        """True if the step completed normally or was skipped."""
        return self.outcome in (StepOutcome.SUCCESS, StepOutcome.SKIPPED)

    @staticmethod
    def ok(step_type: str) -> "StepResult":
        return StepResult(outcome=StepOutcome.SUCCESS, step_type=step_type)

    @staticmethod
    def skipped(step_type: str, reason: str = "") -> "StepResult":
        return StepResult(outcome=StepOutcome.SKIPPED, step_type=step_type, error_message=reason)

    @staticmethod
    def failed(step_type: str, message: str) -> "StepResult":
        return StepResult(outcome=StepOutcome.FAILED, step_type=step_type, error_message=message)

    @staticmethod
    def cancelled(step_type: str, message: str = "") -> "StepResult":
        return StepResult(outcome=StepOutcome.CANCELLED, step_type=step_type, error_message=message)


class OrchestratorState(Enum):
    """State machine states for the experiment orchestrator.

    Simplified 7-state model. The current activity (fluidics, imaging, etc.)
    is tracked via OrchestratorProgress.current_operation rather than
    separate states.
    """

    IDLE = auto()  # Not running, ready to start
    RUNNING = auto()  # Actively executing (fluidics, imaging, etc.)
    WAITING_INTERVENTION = auto()  # Paused for operator intervention
    PAUSED = auto()  # User-requested pause
    COMPLETED = auto()  # Experiment finished successfully
    FAILED = auto()  # Experiment finished with error
    ABORTED = auto()  # Experiment was aborted by user


# Valid state transitions
ORCHESTRATOR_TRANSITIONS: Dict[OrchestratorState, Set[OrchestratorState]] = {
    OrchestratorState.IDLE: {
        OrchestratorState.RUNNING,
    },
    OrchestratorState.RUNNING: {
        OrchestratorState.WAITING_INTERVENTION,
        OrchestratorState.PAUSED,
        OrchestratorState.COMPLETED,
        OrchestratorState.FAILED,
        OrchestratorState.ABORTED,
    },
    OrchestratorState.WAITING_INTERVENTION: {
        OrchestratorState.RUNNING,
        OrchestratorState.PAUSED,
        OrchestratorState.COMPLETED,
        OrchestratorState.FAILED,
        OrchestratorState.ABORTED,
    },
    OrchestratorState.PAUSED: {
        OrchestratorState.RUNNING,
        OrchestratorState.WAITING_INTERVENTION,
        OrchestratorState.COMPLETED,
        OrchestratorState.FAILED,
        OrchestratorState.ABORTED,
    },
    OrchestratorState.COMPLETED: {OrchestratorState.IDLE},
    OrchestratorState.FAILED: {OrchestratorState.IDLE},
    OrchestratorState.ABORTED: {OrchestratorState.IDLE},
}


@dataclass
class RoundProgress:
    """Progress within a single round.

    V2 adds step tracking for step-based round execution.
    """

    round_index: int
    round_name: str
    current_step_index: int = 0  # V2: position within round's steps list
    total_steps: int = 0  # V2: total steps in this round
    current_step_type: str = ""  # "fluidics", "imaging", "intervention", ""
    fluidics_step_index: int = 0
    total_fluidics_steps: int = 0
    imaging_fov_index: int = 0
    total_imaging_fovs: int = 0
    imaging_started: bool = False
    imaging_completed: bool = False
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class ExperimentProgress:
    """Overall experiment progress."""

    current_round_index: int = 0
    total_rounds: int = 0
    current_round: Optional[RoundProgress] = None
    current_step_index: int = 0  # V2: step position within current round
    started_at: Optional[datetime] = None
    estimated_completion: Optional[datetime] = None
    current_step_name: str = ""
    current_fov_label: str = ""
    current_attempt: int = 1
    elapsed_seconds: float = 0.0
    effective_run_seconds: float = 0.0
    paused_seconds: float = 0.0
    retry_overhead_seconds: float = 0.0
    intervention_overhead_seconds: float = 0.0
    subsystem_seconds: Dict[str, float] = field(default_factory=dict)

    @property
    def progress_percent(self) -> float:
        """Calculate overall progress percentage.

        Uses step-based progress: each step in the round contributes equally,
        with sub-progress from the active subsystem (fluidics FOV tracking or
        imaging FOV tracking) within the current step.
        """
        if self.total_rounds == 0:
            return 0.0

        # Base progress from completed rounds
        round_progress = self.current_round_index / self.total_rounds

        # Add progress within current round (only if round is still active)
        if self.current_round is not None and self.current_round_index < self.total_rounds:
            round_frac = 1.0 / self.total_rounds
            total_steps = self.current_round.total_steps

            if total_steps > 0:
                step_frac = round_frac / total_steps
                completed_steps = min(self.current_round.current_step_index, total_steps)
                # Progress from completed steps
                round_progress += completed_steps * step_frac

                # Sub-progress within current step (only if a step is still active)
                if completed_steps < total_steps:
                    sub = 0.0
                    step_type = self.current_round.current_step_type
                    if step_type == "imaging" and self.current_round.total_imaging_fovs > 0:
                        sub = min(
                            self.current_round.imaging_fov_index
                            / self.current_round.total_imaging_fovs,
                            1.0,
                        )
                    elif step_type == "fluidics" and self.current_round.total_fluidics_steps > 0:
                        sub = min(
                            self.current_round.fluidics_step_index
                            / self.current_round.total_fluidics_steps,
                            1.0,
                        )
                    round_progress += sub * step_frac
            else:
                # Fallback for rounds without known step count
                imaging_fovs = self.current_round.total_imaging_fovs
                fluidics_steps = self.current_round.total_fluidics_steps
                if imaging_fovs > 0:
                    round_progress += min(
                        self.current_round.imaging_fov_index / imaging_fovs, 1.0
                    ) * round_frac
                elif fluidics_steps > 0:
                    round_progress += min(
                        self.current_round.fluidics_step_index / fluidics_steps, 1.0
                    ) * round_frac

        return round_progress * 100.0


@dataclass
class Checkpoint:
    """Checkpoint for experiment recovery.

    Captures the state needed to resume an experiment after pause/crash.
    V2 adds step_index for step-based round execution.
    """

    protocol_name: str
    protocol_version: str
    experiment_id: str
    experiment_path: str

    # Position in protocol
    round_index: int
    step_index: int = 0  # V2: position within round's steps list
    imaging_fov_index: int = 0  # 0-based index for resume

    # Imaging state
    imaging_z_index: int = 0
    imaging_channel_index: int = 0

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    paused_at: Optional[datetime] = None

    # Additional state
    metadata: Dict[str, Any] = field(default_factory=dict)
    current_attempt: int = 1
    elapsed_seconds: float = 0.0
    paused_seconds: float = 0.0
    effective_run_seconds: float = 0.0


class ThroughputTracker:
    """Tracks FOV completion timestamps to compute rolling throughput."""

    def __init__(self):
        self._timestamps: list[float] = []

    def record_fov(self, fov_index: int) -> None:
        self._timestamps.append(_time.monotonic())

    def fovs_per_minute(self, window_seconds: float = 120.0) -> Optional[float]:
        if len(self._timestamps) < 2:
            return None
        now = _time.monotonic()
        cutoff = now - window_seconds
        recent = [t for t in self._timestamps if t >= cutoff]
        if len(recent) < 2:
            return None
        elapsed = recent[-1] - recent[0]
        if elapsed <= 0:
            return None
        fovs_completed = len(recent) - 1
        return (fovs_completed / elapsed) * 60.0

    def reset(self) -> None:
        self._timestamps.clear()


@dataclass(frozen=True)
class RunState:
    """Immutable snapshot of the entire experiment state at a point in time."""

    # Identity
    experiment_id: str
    state: OrchestratorState

    # Position
    round_index: int
    total_rounds: int
    round_name: str
    step_index: int
    total_steps: int
    step_type: str  # "imaging", "fluidics", "intervention", ""
    step_label: str
    fov_index: int
    total_fovs: int

    # Timing
    elapsed_s: float
    active_s: float
    paused_s: float
    eta_s: Optional[float]

    # Health
    attempt: int
    focus_status: Optional[str] = None
    focus_error_um: Optional[float] = None
    throughput_fov_per_min: Optional[float] = None

    # Subsystem timing
    subsystem_seconds: Dict[str, float] = field(default_factory=dict)

    # Timestamps
    started_at: Optional[datetime] = None
    snapshot_at: datetime = field(default_factory=datetime.now)

    @property
    def progress_percent(self) -> float:
        """Calculate overall progress percentage."""
        if self.total_rounds == 0:
            return 0.0
        round_progress = self.round_index / self.total_rounds
        if self.round_index < self.total_rounds and self.total_steps > 0:
            round_frac = 1.0 / self.total_rounds
            step_frac = round_frac / self.total_steps
            completed_steps = min(self.step_index, self.total_steps)
            round_progress += completed_steps * step_frac
            if completed_steps < self.total_steps:
                sub = 0.0
                if self.step_type == "imaging" and self.total_fovs > 0:
                    sub = min(self.fov_index / self.total_fovs, 1.0)
                elif self.step_type == "fluidics" and self.total_fovs > 0:
                    sub = min(self.fov_index / self.total_fovs, 1.0)
                round_progress += sub * step_frac
        return round_progress * 100.0

    def to_checkpoint(self, protocol_name: str, protocol_version: str, experiment_path: str) -> "Checkpoint":
        """Create a Checkpoint from this RunState."""
        return Checkpoint(
            protocol_name=protocol_name,
            protocol_version=protocol_version,
            experiment_id=self.experiment_id,
            experiment_path=experiment_path,
            round_index=self.round_index,
            step_index=self.step_index,
            imaging_fov_index=self.fov_index,
            created_at=self.snapshot_at,
            current_attempt=self.attempt,
            elapsed_seconds=self.elapsed_s,
            paused_seconds=self.paused_s,
            effective_run_seconds=self.active_s,
        )


# ============================================================================
# Orchestrator Events
# ============================================================================


@dataclass
class OrchestratorStateChanged(Event):
    """Emitted when orchestrator state changes."""

    old_state: str  # OrchestratorState.name
    new_state: str  # OrchestratorState.name
    experiment_id: str
    reason: str = ""


@dataclass
class RunStateUpdated(Event):
    """Published when run state changes."""

    run_state: RunState


@dataclass
class OrchestratorProgress(Event):
    """Emitted periodically during experiment execution."""

    experiment_id: str
    current_round: int
    total_rounds: int
    current_round_name: str
    progress_percent: float
    eta_seconds: Optional[float] = None
    current_operation: str = ""  # "fluidics", "imaging", "waiting"
    current_step_name: str = ""
    current_step_index: int = 0
    total_steps: int = 0
    current_fov_label: str = ""
    current_fov_index: int = 0
    total_fovs: int = 0
    attempt: int = 1
    elapsed_seconds: float = 0.0
    effective_run_seconds: float = 0.0
    paused_seconds: float = 0.0
    retry_overhead_seconds: float = 0.0
    intervention_overhead_seconds: float = 0.0


@dataclass
class OrchestratorRoundStarted(Event):
    """Emitted when a new round begins."""

    experiment_id: str
    round_index: int
    round_name: str
    round_type: str


@dataclass
class OrchestratorRoundCompleted(Event):
    """Emitted when a round completes."""

    experiment_id: str
    round_index: int
    round_name: str
    success: bool
    error: Optional[str] = None


@dataclass
class OrchestratorStepStarted(Event):
    """Emitted when a step within a round begins."""

    experiment_id: str
    round_index: int
    step_index: int
    step_type: str  # "fluidics", "imaging", "intervention"
    estimated_seconds: float = 0.0  # Estimated duration from validation
    imaging_protocol: Optional["ImagingProtocol"] = None  # Resolved protocol for imaging steps


@dataclass
class OrchestratorStepCompleted(Event):
    """Emitted when a step within a round completes."""

    experiment_id: str
    round_index: int
    step_index: int
    step_type: str
    success: bool
    error: Optional[str] = None
    duration_seconds: float = 0.0  # Actual duration in seconds


@dataclass
class OrchestratorInterventionRequired(Event):
    """Emitted when operator intervention is needed."""

    experiment_id: str
    round_index: int
    round_name: str
    message: str
    kind: str = "acknowledge"
    attempt: int = 1
    current_step_name: str = ""
    current_fov_label: str = ""
    allowed_actions: tuple[str, ...] = ("acknowledge",)


@dataclass
class OrchestratorTimingSnapshot(Event):
    """Periodic timing snapshot for UI and disk logging."""

    experiment_id: str
    elapsed_seconds: float
    effective_run_seconds: float
    paused_seconds: float
    retry_overhead_seconds: float
    intervention_overhead_seconds: float
    eta_seconds: Optional[float]
    subsystem_seconds: Dict[str, float]


@dataclass
class OrchestratorAttemptUpdate(Event):
    """Emitted when the orchestrator starts/completes/retries a step attempt."""

    experiment_id: str
    round_index: int
    step_index: int
    step_type: str
    attempt: int
    phase: str
    message: str = ""
    current_fov_index: Optional[int] = None
    current_fov_label: str = ""


@dataclass
class OrchestratorError(Event):
    """Emitted when an error occurs."""

    experiment_id: str
    error_type: str
    message: str
    recoverable: bool = False


# ============================================================================
# Orchestrator Commands
# ============================================================================


@dataclass
class StartOrchestratorCommand(Event):
    """Command to start an orchestrated experiment."""

    protocol_path: str
    base_path: str
    experiment_id: Optional[str] = None
    resume_from_checkpoint: bool = False
    start_from_round: int = 0
    start_from_step: int = 0
    start_from_fov: int = 0
    run_single_round: bool = False
    acquire_current_fov: bool = False


@dataclass
class StopOrchestratorCommand(Event):
    """Command to stop/abort the orchestrator."""

    pass


@dataclass
class PauseOrchestratorCommand(Event):
    """Command to pause the orchestrator."""

    pass


@dataclass
class ResumeOrchestratorCommand(Event):
    """Command to resume the orchestrator from pause or intervention."""

    pass


@dataclass
class AcknowledgeInterventionCommand(Event):
    """Command to acknowledge an intervention and continue."""

    pass


@dataclass
class ResolveInterventionCommand(Event):
    """Resolve a blocking intervention with a fixed operator action."""

    action: Literal["acknowledge", "retry", "skip", "abort"]


@dataclass
class SkipCurrentRoundCommand(Event):
    """Command to skip the remainder of the current round."""

    pass


@dataclass
class SkipToRoundCommand(Event):
    """Command to skip ahead to a specific round index (0-based)."""

    round_index: int


# ============================================================================
# Warning Events and Commands
# ============================================================================


@dataclass
class WarningRaised(Event):
    """Emitted when a new warning is generated during acquisition."""

    experiment_id: str
    category: str  # WarningCategory.name
    severity: str  # WarningSeverity.name
    message: str
    round_index: int
    round_name: str
    time_point: int
    fov_id: Optional[str]
    fov_index: Optional[int]
    total_warnings: int
    warnings_in_category: int


@dataclass
class WarningThresholdReached(Event):
    """Emitted when warning count reaches a threshold."""

    experiment_id: str
    threshold_type: str  # "total", "category", "severity"
    threshold_value: int
    current_count: int
    category: Optional[str] = None  # If category-specific threshold
    should_pause: bool = True


@dataclass
class WarningsCleared(Event):
    """Emitted when warnings are cleared."""

    experiment_id: str
    cleared_count: int
    categories_cleared: Optional[tuple] = None  # None = all categories


@dataclass
class ClearWarningsCommand(Event):
    """Command to clear accumulated warnings."""

    experiment_id: str
    categories: Optional[tuple] = None  # None = clear all


@dataclass
class SetWarningThresholdsCommand(Event):
    """Command to update warning thresholds."""

    pause_after_count: Optional[int] = None
    pause_on_critical: bool = True
    pause_on_high: bool = False
    max_stored_warnings: int = 1000


@dataclass
class AddWarningCommand(Event):
    """Command to add a warning from other subsystems."""

    category: str  # WarningCategory.name
    severity: str  # WarningSeverity.name
    message: str
    round_index: int = 0
    round_name: str = ""
    time_point: int = 0
    operation_type: str = ""
    operation_index: int = 0
    fov_id: Optional[str] = None
    fov_index: Optional[int] = None
    context: Optional[Dict[str, Any]] = None


# ============================================================================
# Validation Events and Commands
# ============================================================================


@dataclass
class ValidateProtocolCommand(Event):
    """Command to validate a protocol before execution."""

    protocol_path: str
    base_path: str
    fov_count: int = 0  # Number of FOV positions loaded (0 = use default of 1)


@dataclass
class ProtocolValidationStarted(Event):
    """Emitted when protocol validation begins."""

    protocol_path: str


@dataclass
class ProtocolValidationComplete(Event):
    """Emitted when protocol validation completes."""

    protocol_name: str
    valid: bool
    total_rounds: int
    estimated_seconds: float
    estimated_disk_bytes: int
    operation_estimates: tuple
    errors: tuple  # Tuple[str, ...]
    warnings: tuple  # Tuple[str, ...]
