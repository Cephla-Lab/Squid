"""
FOV Task System - First-class FOV tasks with stable IDs.

This module provides data structures for managing FOV acquisition tasks with:
- Stable IDs that persist across jumps/retries
- Non-destructive cursor movement (jump doesn't skip)
- Explicit skip/defer actions
- Thread-safe task list mutations
- Checkpoint support for resume
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Any, Optional, List
import threading


class FovStatus(Enum):
    """Status of an FOV acquisition task."""

    PENDING = auto()  # Not yet executed
    EXECUTING = auto()  # Currently running
    COMPLETED = auto()  # Successfully finished
    FAILED = auto()  # Failed (may retry)
    SKIPPED = auto()  # Explicitly skipped by user
    DEFERRED = auto()  # Temporarily skipped, will revisit


@dataclass
class FovTask:
    """A single FOV acquisition task with stable identity.

    The fov_id is stable across retries and serves as the primary identifier
    for file naming and metadata. The fov_index is preserved for backward
    compatibility with legacy tools expecting numeric indices.

    Attributes:
        fov_id: Stable ID format: "{region_id}_{original_index:04d}"
        region_id: ID of the region this FOV belongs to
        fov_index: Original index for backward compatibility
        x_mm: X position in millimeters
        y_mm: Y position in millimeters
        z_mm: Z position in millimeters
        status: Current execution status
        attempt: Attempt number (increments on retry/backtrack)
        metadata: Additional metadata (e.g., original_index)
        error_message: Error message if status is FAILED
    """

    fov_id: str
    region_id: str
    fov_index: int
    x_mm: float
    y_mm: float
    z_mm: float
    status: FovStatus = FovStatus.PENDING
    attempt: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None

    @classmethod
    def from_coordinate(cls, region_id: str, index: int, coord: tuple) -> "FovTask":
        """Create an FovTask from a legacy coordinate tuple.

        Args:
            region_id: The region identifier
            index: The FOV index within the region
            coord: Tuple of (x_mm, y_mm[, z_mm]) coordinates

        Returns:
            A new FovTask instance
        """
        if len(coord) < 2:
            raise ValueError(f"Invalid coordinate for region {region_id}: {coord}")
        has_z = len(coord) >= 3
        x, y = coord[0], coord[1]
        z = coord[2] if has_z else 0.0
        return cls(
            fov_id=f"{region_id}_{index:04d}",
            region_id=region_id,
            fov_index=index,
            x_mm=x,
            y_mm=y,
            z_mm=z,
            metadata={"original_index": index, "has_z": has_z},
        )


@dataclass
class FovTaskList:
    """Ordered list of FOV tasks with cursor and manipulation methods.

    This class manages a list of FOV tasks with thread-safe operations.
    It owns cursor movement - callers should use advance_and_get() and
    mark_complete() rather than manipulating the cursor directly.

    Thread-safe: All mutations protected by internal lock.
    Single cursor owner: Only this class advances the cursor.

    Attributes:
        tasks: List of FovTask instances
        cursor: Current position in task list
        plan_hash: Hash of original task list for resume validation
    """

    tasks: List[FovTask] = field(default_factory=list)
    cursor: int = 0
    plan_hash: str = ""
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self):
        # Ensure _lock is always initialized (needed for from_checkpoint)
        if not hasattr(self, "_lock") or self._lock is None:
            object.__setattr__(self, "_lock", threading.RLock())

    def __len__(self) -> int:
        """Return the number of tasks."""
        with self._lock:
            return len(self.tasks)

    def advance_and_get(self) -> Optional[FovTask]:
        """Move cursor to next PENDING task and return it.

        This is the single entry point for getting the next task.
        Caller does NOT modify cursor - this class is the single owner.

        Returns:
            The next PENDING task, or None if all tasks are processed.
        """
        with self._lock:
            while self.cursor < len(self.tasks):
                task = self.tasks[self.cursor]
                if task.status == FovStatus.PENDING:
                    return task
                self.cursor += 1
            return None

    def current_task(self) -> Optional[FovTask]:
        """Get the task at the current cursor position without advancing.

        Returns:
            The task at cursor position, or None if cursor is out of bounds.
        """
        with self._lock:
            if 0 <= self.cursor < len(self.tasks):
                return self.tasks[self.cursor]
            return None

    def mark_complete(self, fov_id: str, success: bool, error_msg: Optional[str] = None) -> None:
        """Mark task complete and advance cursor.

        This is the single entry point for completion - caller never does cursor += 1.

        Args:
            fov_id: The ID of the task to mark complete
            success: Whether the task completed successfully
            error_msg: Optional error message if success is False
        """
        with self._lock:
            task = self._find_next_task(fov_id, require_pending=False)
            if task is None:
                return
            task.status = FovStatus.COMPLETED if success else FovStatus.FAILED
            task.error_message = error_msg
            self.cursor += 1

    def mark_complete_task(
        self,
        task: FovTask,
        success: bool,
        error_msg: Optional[str] = None,
    ) -> None:
        """Mark a specific task complete and advance cursor."""
        with self._lock:
            task.status = FovStatus.COMPLETED if success else FovStatus.FAILED
            task.error_message = error_msg
            self.cursor += 1

    def mark_executing(self, fov_id: str) -> bool:
        """Mark a task as currently executing.

        Args:
            fov_id: The ID of the task to mark executing

        Returns:
            True if task was found and marked, False otherwise
        """
        with self._lock:
            task = self._find_next_task(fov_id, require_pending=False)
            if task is None:
                return False
            task.status = FovStatus.EXECUTING
            return True

    def mark_executing_task(self, task: FovTask) -> None:
        """Mark a specific task as currently executing."""
        with self._lock:
            task.status = FovStatus.EXECUTING

    def jump_to(self, fov_id: str) -> bool:
        """Move cursor to task.

        NON-DESTRUCTIVE: Does not mark anything as skipped.
        Use skip() explicitly if you want to skip intervening tasks.

        Args:
            fov_id: The ID of the task to jump to

        Returns:
            True if task was found and cursor moved, False otherwise
        """
        with self._lock:
            index = self._find_task_index(
                fov_id,
                start_index=self.cursor,
                status_filter={FovStatus.PENDING},
            )
            if index is None:
                index = self._find_task_index(fov_id, start_index=0)
            if index is None:
                return False
            self.cursor = index
            return True

    def jump_to_index(self, index: int) -> bool:
        """Move cursor to a specific index.

        NON-DESTRUCTIVE: Does not mark anything as skipped.

        Args:
            index: The index to jump to

        Returns:
            True if index is valid and cursor moved, False otherwise
        """
        with self._lock:
            if 0 <= index < len(self.tasks):
                self.cursor = index
                return True
            return False

    def skip(self, fov_id: str) -> bool:
        """Explicitly mark a PENDING task as SKIPPED.

        Args:
            fov_id: The ID of the task to skip

        Returns:
            True if task was found and marked, False otherwise
        """
        with self._lock:
            task = self._find_next_task(fov_id, require_pending=True)
            if task is None:
                return False
            task.status = FovStatus.SKIPPED
            return True

    def defer(self, fov_id: str) -> bool:
        """Mark task as DEFERRED (will come back to it later).

        DEFERRED tasks are different from SKIPPED - they will be
        restored to PENDING at the end of a time_point via restore_deferred().

        Args:
            fov_id: The ID of the task to defer

        Returns:
            True if task was found and marked, False otherwise
        """
        with self._lock:
            task = self._find_next_task(fov_id, require_pending=True)
            if task is None:
                return False
            task.status = FovStatus.DEFERRED
            return True

    def requeue(self, fov_id: str, before_current: bool = False) -> bool:
        """Re-add task for retry with attempt+1.

        IMPORTANT: Uses same fov_id, only increments attempt.
        This supports backtracking with consistent file naming.

        Args:
            fov_id: The task to requeue
            before_current: If True, insert before cursor (for backtracking).
                           If False, insert after cursor (for retry later).

        Returns:
            True if task was found and requeued, False otherwise
        """
        with self._lock:
            original = self._find_task_for_requeue(fov_id)
            if original is None:
                return False
            new_task = FovTask(
                fov_id=original.fov_id,  # SAME fov_id
                region_id=original.region_id,
                fov_index=original.fov_index,
                x_mm=original.x_mm,
                y_mm=original.y_mm,
                z_mm=original.z_mm,
                attempt=original.attempt + 1,
                metadata=original.metadata.copy(),
            )
            insert_pos = self.cursor if before_current else self.cursor + 1
            self.tasks.insert(insert_pos, new_task)
            return True

    def reorder(self, fov_ids: List[str]) -> bool:
        """Reorder pending tasks based on the provided fov_id list."""
        with self._lock:
            pending_tasks = [task for task in self.tasks if task.status == FovStatus.PENDING]
            if not pending_tasks:
                return False

            pending_by_id: Dict[str, List[FovTask]] = {}
            for task in pending_tasks:
                pending_by_id.setdefault(task.fov_id, []).append(task)

            new_pending: List[FovTask] = []
            used_task_ids = set()
            for fov_id in fov_ids:
                if fov_id in pending_by_id and pending_by_id[fov_id]:
                    task = pending_by_id[fov_id].pop(0)
                    new_pending.append(task)
                    used_task_ids.add(id(task))

            # Append remaining pending tasks in original order.
            for task in pending_tasks:
                if id(task) in used_task_ids:
                    continue
                new_pending.append(task)

            if len(new_pending) != len(pending_tasks):
                return False

            new_iter = iter(new_pending)
            for i, task in enumerate(self.tasks):
                if task.status == FovStatus.PENDING:
                    self.tasks[i] = next(new_iter)

            next_pending = self._find_task_index(
                fov_id="",
                start_index=self.cursor,
                status_filter={FovStatus.PENDING},
            )
            if next_pending is None:
                next_pending = self._find_task_index(
                    fov_id="",
                    start_index=0,
                    status_filter={FovStatus.PENDING},
                )
            if next_pending is not None:
                self.cursor = next_pending
            return True

    def restore_deferred(self) -> int:
        """Reset all DEFERRED tasks back to PENDING.

        Called at end of time_point to revisit deferred tasks.

        Returns:
            The number of tasks restored
        """
        with self._lock:
            count = 0
            for task in self.tasks:
                if task.status == FovStatus.DEFERRED:
                    task.status = FovStatus.PENDING
                    count += 1
            return count

    def reset_for_timepoint(self) -> int:
        """Reset task statuses for a new timepoint.

        COMPLETED/FAILED/EXECUTING/DEFERRED tasks are set back to PENDING.
        SKIPPED tasks remain skipped.
        """
        with self._lock:
            count = 0
            for task in self.tasks:
                if task.status in (
                    FovStatus.COMPLETED,
                    FovStatus.FAILED,
                    FovStatus.EXECUTING,
                    FovStatus.DEFERRED,
                ):
                    task.status = FovStatus.PENDING
                    task.error_message = None
                    count += 1
            self.cursor = 0
            return count

    def reset_cursor(self) -> None:
        """Reset cursor to the beginning of the task list."""
        with self._lock:
            self.cursor = 0

    def _find_task_index(
        self,
        fov_id: str,
        *,
        start_index: int = 0,
        status_filter: Optional[set] = None,
        reverse: bool = False,
    ) -> Optional[int]:
        """Find task index by fov_id with optional status filter."""
        if reverse:
            indices = range(len(self.tasks) - 1, -1, -1)
        else:
            indices = range(max(start_index, 0), len(self.tasks))
        for i in indices:
            task = self.tasks[i]
            if fov_id and task.fov_id != fov_id:
                continue
            if status_filter is not None and task.status not in status_filter:
                continue
            return i
        return None

    def _find_task_for_requeue(self, fov_id: str) -> Optional[FovTask]:
        """Find the most recent task for requeue (highest attempt)."""
        matches = [task for task in self.tasks if task.fov_id == fov_id]
        if not matches:
            return None
        return max(matches, key=lambda task: task.attempt)

    def _find_next_task(self, fov_id: str, *, require_pending: bool) -> Optional[FovTask]:
        """Find the next matching task, preferring the cursor position."""
        status_filter = {FovStatus.PENDING} if require_pending else None
        index = self._find_task_index(
            fov_id,
            start_index=self.cursor,
            status_filter=status_filter,
        )
        if index is None:
            index = self._find_task_index(
                fov_id,
                start_index=0,
                status_filter=status_filter,
            )
        if index is None:
            return None
        return self.tasks[index]

    def _find_task(self, fov_id: str) -> Optional[FovTask]:
        """Find task by fov_id (internal, assumes lock held)."""
        index = self._find_task_index(fov_id, start_index=0)
        if index is None:
            return None
        return self.tasks[index]

    def get_task(self, fov_id: str) -> Optional[FovTask]:
        """Find task by fov_id (public, thread-safe)."""
        with self._lock:
            return self._find_task(fov_id)

    # Status counts
    def pending_count(self) -> int:
        """Return the number of PENDING tasks."""
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.PENDING)

    def completed_count(self) -> int:
        """Return the number of COMPLETED tasks."""
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.COMPLETED)

    def failed_count(self) -> int:
        """Return the number of FAILED tasks."""
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.FAILED)

    def skipped_count(self) -> int:
        """Return the number of SKIPPED tasks."""
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.SKIPPED)

    def deferred_count(self) -> int:
        """Return the number of DEFERRED tasks."""
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.DEFERRED)

    def executing_count(self) -> int:
        """Return the number of EXECUTING tasks."""
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.EXECUTING)

    # Serialization
    def to_checkpoint(self) -> dict:
        """Serialize task list to checkpoint format.

        Returns:
            Dictionary suitable for JSON serialization
        """
        with self._lock:
            return {
                "cursor": self.cursor,
                "plan_hash": self.plan_hash,
                "tasks": [
                    {
                        "fov_id": t.fov_id,
                        "region_id": t.region_id,
                        "fov_index": t.fov_index,
                        "x_mm": t.x_mm,
                        "y_mm": t.y_mm,
                        "z_mm": t.z_mm,
                        "status": t.status.name,
                        "attempt": t.attempt,
                        "metadata": t.metadata,
                        "error_message": t.error_message,
                    }
                    for t in self.tasks
                ],
            }

    @classmethod
    def from_checkpoint(cls, data: dict) -> "FovTaskList":
        """Deserialize task list from checkpoint format.

        Note: This only deserializes - plan_hash validation happens
        in MultiPointCheckpoint.load().

        Args:
            data: Dictionary from checkpoint file

        Returns:
            Reconstructed FovTaskList
        """
        tasks = [
            FovTask(
                fov_id=t["fov_id"],
                region_id=t["region_id"],
                fov_index=t["fov_index"],
                x_mm=t["x_mm"],
                y_mm=t["y_mm"],
                z_mm=t["z_mm"],
                status=FovStatus[t["status"]],
                attempt=t["attempt"],
                metadata=t.get("metadata", {}),
                error_message=t.get("error_message"),
            )
            for t in data["tasks"]
        ]
        task_list = cls(
            tasks=tasks,
            cursor=data["cursor"],
            plan_hash=data.get("plan_hash", ""),
        )
        return task_list

    @classmethod
    def from_coordinates(
        cls, region_fov_coords: Dict[str, List[tuple]], plan_hash: str = ""
    ) -> "FovTaskList":
        """Build task list from region/FOV coordinate dictionary.

        Args:
            region_fov_coords: Dict mapping region_id to list of (x, y, z) tuples
            plan_hash: Optional hash of the original plan for validation

        Returns:
            New FovTaskList with tasks created from coordinates
        """
        tasks = []
        for region_id, coords in region_fov_coords.items():
            for index, coord in enumerate(coords):
                task = FovTask.from_coordinate(region_id, index, coord)
                tasks.append(task)
        return cls(tasks=tasks, cursor=0, plan_hash=plan_hash)
