"""
Orchestrator state definitions and management.

Defines the state machine for experiment orchestration.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Optional, Set

from squid.core.events import Event


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
class OrchestratorProgress(Event):
    """Emitted periodically during experiment execution."""

    experiment_id: str
    current_round: int
    total_rounds: int
    current_round_name: str
    progress_percent: float
    eta_seconds: Optional[float] = None
    current_operation: str = ""  # "fluidics", "imaging", "waiting"


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
