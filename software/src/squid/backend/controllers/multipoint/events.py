"""
FOV Task System Events - Commands and state events for FOV task management.

Commands (UI → Backend):
- JumpToFovCommand: Non-destructive jump (cursor only)
- SkipFovCommand: Explicitly mark task as SKIPPED
- RequeueFovCommand: Re-add task for retry (same fov_id, attempt+1)
- DeferFovCommand: Temporarily skip, will revisit
- ReorderFovsCommand: Reorder pending tasks by fov_id

State Events (Backend → UI):
- FovTaskStarted: FOV task execution begins
- FovTaskCompleted: FOV task completes
- FovTaskListChanged: Task list modified (jump/skip/requeue/defer)
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from squid.core.events import Event
from squid.backend.controllers.multipoint.fov_task import FovStatus


# ============================================================================
# Commands (UI → Backend)
# ============================================================================


@dataclass
class JumpToFovCommand(Event):
    """Non-destructive jump - just moves cursor, does not skip.

    Attributes:
        fov_id: The FOV to jump to
        round_index: Current round index (for context)
        time_point: Current time point (for context)
    """

    fov_id: str
    round_index: int = 0
    time_point: int = 0


@dataclass
class SkipFovCommand(Event):
    """Explicitly skip a pending FOV.

    Attributes:
        fov_id: The FOV to skip
        round_index: Current round index (for context)
        time_point: Current time point (for context)
    """

    fov_id: str
    round_index: int = 0
    time_point: int = 0


@dataclass
class RequeueFovCommand(Event):
    """Re-add FOV for retry (same fov_id, attempt+1).

    Attributes:
        fov_id: The FOV to requeue
        before_current: If True, insert before cursor (for backtracking).
                       If False, insert after cursor (for retry later).
        round_index: Current round index (for context)
        time_point: Current time point (for context)
    """

    fov_id: str
    before_current: bool = False
    round_index: int = 0
    time_point: int = 0


@dataclass
class DeferFovCommand(Event):
    """Temporarily skip, will revisit at end of time_point.

    Deferred tasks are restored to PENDING via restore_deferred().

    Attributes:
        fov_id: The FOV to defer
        round_index: Current round index (for context)
        time_point: Current time point (for context)
    """

    fov_id: str
    round_index: int = 0
    time_point: int = 0


@dataclass
class ReorderFovsCommand(Event):
    """Reorder pending FOVs by fov_id sequence."""

    fov_ids: Tuple[str, ...]
    round_index: int = 0
    time_point: int = 0


# ============================================================================
# State Events (Backend → UI)
# ============================================================================


@dataclass
class FovTaskStarted(Event):
    """Emitted when FOV task execution begins.

    Includes both fov_id and fov_index for backward compatibility.

    Attributes:
        fov_id: Stable FOV identifier (e.g., "A1_0005")
        fov_index: Original index for backward compatibility
        region_id: Region this FOV belongs to
        round_index: Current round index
        time_point: Current time point
        x_mm: X position in millimeters
        y_mm: Y position in millimeters
        attempt: Attempt number (1 for first try, increments on retry)
        pending_count: Number of remaining PENDING tasks
        completed_count: Number of COMPLETED tasks
    """

    fov_id: str
    fov_index: int
    region_id: str
    round_index: int
    time_point: int
    x_mm: float
    y_mm: float
    attempt: int
    pending_count: int
    completed_count: int


@dataclass
class FovTaskCompleted(Event):
    """Emitted when FOV task completes.

    Attributes:
        fov_id: Stable FOV identifier
        fov_index: Original index for backward compatibility
        round_index: Current round index
        time_point: Current time point
        status: Final status (COMPLETED, FAILED, or SKIPPED)
        attempt: Attempt number
        error_message: Error message if status is FAILED
    """

    fov_id: str
    fov_index: int
    round_index: int
    time_point: int
    status: FovStatus
    attempt: int
    error_message: Optional[str] = None


@dataclass
class FovTaskListChanged(Event):
    """Emitted when task list is modified (jump/skip/requeue/defer).

    Provides summary counts for UI display.

    Attributes:
        round_index: Current round index
        time_point: Current time point
        cursor: Current cursor position
        pending_count: Number of PENDING tasks
        completed_count: Number of COMPLETED tasks
        skipped_count: Number of SKIPPED tasks
        deferred_count: Number of DEFERRED tasks
    """

    round_index: int
    time_point: int
    cursor: int
    pending_count: int
    completed_count: int
    skipped_count: int
    deferred_count: int
