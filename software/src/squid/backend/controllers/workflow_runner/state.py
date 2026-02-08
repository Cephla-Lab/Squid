"""
Workflow Runner state definitions.

Defines the state machine, transitions, commands, and events for the
Workflow Runner controller.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, Optional, Set

from squid.core.events import Event


class WorkflowRunnerState(Enum):
    """State machine states for the workflow runner."""

    IDLE = auto()
    RUNNING_SCRIPT = auto()
    RUNNING_ACQUISITION = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    ABORTED = auto()


# Valid state transitions
WORKFLOW_RUNNER_TRANSITIONS: Dict[WorkflowRunnerState, Set[WorkflowRunnerState]] = {
    WorkflowRunnerState.IDLE: {
        WorkflowRunnerState.RUNNING_SCRIPT,
        WorkflowRunnerState.RUNNING_ACQUISITION,
        WorkflowRunnerState.COMPLETED,
        WorkflowRunnerState.FAILED,
    },
    WorkflowRunnerState.RUNNING_SCRIPT: {
        WorkflowRunnerState.RUNNING_SCRIPT,
        WorkflowRunnerState.RUNNING_ACQUISITION,
        WorkflowRunnerState.PAUSED,
        WorkflowRunnerState.COMPLETED,
        WorkflowRunnerState.FAILED,
        WorkflowRunnerState.ABORTED,
    },
    WorkflowRunnerState.RUNNING_ACQUISITION: {
        WorkflowRunnerState.RUNNING_SCRIPT,
        WorkflowRunnerState.RUNNING_ACQUISITION,
        WorkflowRunnerState.PAUSED,
        WorkflowRunnerState.COMPLETED,
        WorkflowRunnerState.FAILED,
        WorkflowRunnerState.ABORTED,
    },
    WorkflowRunnerState.PAUSED: {
        WorkflowRunnerState.RUNNING_SCRIPT,
        WorkflowRunnerState.RUNNING_ACQUISITION,
        WorkflowRunnerState.ABORTED,
    },
    WorkflowRunnerState.COMPLETED: {WorkflowRunnerState.IDLE},
    WorkflowRunnerState.FAILED: {WorkflowRunnerState.IDLE},
    WorkflowRunnerState.ABORTED: {WorkflowRunnerState.IDLE},
}


# ============================================================================
# Workflow Runner Commands (UI -> Backend)
# ============================================================================


@dataclass
class StartWorkflowCommand(Event):
    """Command to start a workflow run."""

    workflow_dict: dict  # Serialized Workflow


@dataclass
class StopWorkflowCommand(Event):
    """Command to stop/abort the workflow."""

    pass


@dataclass
class PauseWorkflowCommand(Event):
    """Command to pause the workflow."""

    pass


@dataclass
class ResumeWorkflowCommand(Event):
    """Command to resume the workflow from pause."""

    pass


# ============================================================================
# Workflow Runner State Events (Backend -> UI)
# ============================================================================


@dataclass
class WorkflowRunnerStateChanged(Event):
    """Emitted when workflow runner state changes."""

    old_state: str  # WorkflowRunnerState.name
    new_state: str  # WorkflowRunnerState.name


@dataclass
class WorkflowCycleStarted(Event):
    """Emitted when a new cycle begins."""

    current_cycle: int  # 0-indexed
    total_cycles: int


@dataclass
class WorkflowSequenceStarted(Event):
    """Emitted when a sequence step starts."""

    sequence_index: int
    sequence_name: str


@dataclass
class WorkflowSequenceFinished(Event):
    """Emitted when a sequence step finishes."""

    sequence_index: int
    sequence_name: str
    success: bool


@dataclass
class WorkflowScriptOutput(Event):
    """Emitted for each line of script stdout/stderr."""

    line: str


@dataclass
class WorkflowError(Event):
    """Emitted when a workflow error occurs."""

    message: str


@dataclass
class WorkflowLoadConfigRequest(Event):
    """Emitted by controller to request UI to load acquisition config.

    The controller publishes this before starting an acquisition that
    has a config_path. The UI should load the YAML and publish a
    WorkflowLoadConfigResponse.
    """

    config_path: str  # Path to acquisition.yaml file


@dataclass
class WorkflowLoadConfigResponse(Event):
    """Response from UI after attempting to load acquisition config.

    Published by the UI after handling a WorkflowLoadConfigRequest.
    """

    success: bool
    error_message: Optional[str] = None
