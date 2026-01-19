"""
Error handling configuration for V2 protocol schema.

Defines protocol-level error handling behaviors for different failure scenarios.
"""

from enum import Enum

from pydantic import BaseModel


class FailureAction(str, Enum):
    """Action to take when a failure occurs.

    Values:
        SKIP: Skip the failed operation and continue
        ABORT: Abort the entire experiment
        PAUSE: Pause and wait for operator intervention
        WARN: Log warning and continue
    """

    SKIP = "skip"
    ABORT = "abort"
    PAUSE = "pause"
    WARN = "warn"


class ErrorHandlingConfig(BaseModel):
    """Protocol-level error handling configuration.

    Defines how the orchestrator should respond to different failure types:

    focus_failure: When AutofocusExecutor.perform_autofocus() returns False
        - Default: skip (continue imaging at current z position)

    fluidics_failure: When FluidicsController reaches FAILED state
        - Default: abort (fluidics failures are typically critical)

    imaging_failure: When ImagingExecutor.execute_with_config() returns False
        - Default: warn (log and continue to next step)

    Example:
        error_handling:
          focus_failure: skip
          fluidics_failure: abort
          imaging_failure: warn
    """

    focus_failure: FailureAction = FailureAction.SKIP
    fluidics_failure: FailureAction = FailureAction.ABORT
    imaging_failure: FailureAction = FailureAction.WARN
