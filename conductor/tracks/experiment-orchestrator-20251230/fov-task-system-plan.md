# FOV Task System + DAVE-Inspired Features

**Date:** 2025-01-12
**Status:** Planning (Revised with feedback)
**Depends On:** Experiment Orchestrator Implementation (95% complete)

## Summary

**Part A: FOV Task System** (Foundation)
- Make FOVs first-class tasks with stable IDs
- Non-destructive jump (cursor movement only), explicit skip action
- Backtracking reuses same fov_id with attempt+1
- Emit both fov_id and fov_index for backward compatibility

**Part B: DAVE-Inspired Features** (Building on Part A)
1. Test Mode / Validation Run - Pre-flight validation with time/disk estimates
2. Warning Accumulation - Non-fatal issues collected without stopping execution
3. UI Improvements - Current action highlighting, parameter inspection, time estimates

---

# Part A: FOV Task System

## Design Decisions (from feedback)

1. **Single cursor owner** - `FovTaskList.advance_and_get()` owns cursor movement; caller never does `cursor += 1`
2. **Non-destructive jump** - `jump_to()` only moves cursor, does NOT mark intervening tasks as SKIPPED
3. **Explicit skip** - `skip()` is a separate action that marks a task SKIPPED
4. **DEFERRED state** - Tasks can be DEFERRED (not skipped, will revisit) for backtracking
5. **Backtracking** - Going back re-executes with same `fov_id`, `attempt += 1`
6. **Compatibility** - File naming includes both `fov_id` and `fov_index` in metadata
7. **Thread safety** - Command queue uses `threading.Lock()`, mutations under lock
8. **Plan versioning** - Checkpoint includes `plan_hash` to detect FOV list changes on resume
9. **Context in events** - All commands/events include `round_index` and `time_point`

## Data Model

**File:** `software/src/squid/backend/controllers/multipoint/fov_task.py`

```python
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Any, Optional, List
import threading

class FovStatus(Enum):
    PENDING = auto()      # Not yet executed
    EXECUTING = auto()    # Currently running
    COMPLETED = auto()    # Successfully finished
    FAILED = auto()       # Failed (may retry)
    SKIPPED = auto()      # Explicitly skipped by user
    DEFERRED = auto()     # Temporarily skipped, will revisit

@dataclass
class FovTask:
    """A single FOV acquisition task with stable identity."""
    fov_id: str           # Stable: "{region_id}_{original_index:04d}"
    region_id: str
    fov_index: int        # Original index for compatibility
    x_mm: float
    y_mm: float
    z_mm: float
    status: FovStatus = FovStatus.PENDING
    attempt: int = 1      # Increments on retry/backtrack
    metadata: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None

    @classmethod
    def from_coordinate(cls, region_id: str, index: int, coord: tuple) -> "FovTask":
        """Create from legacy coordinate tuple."""
        x, y, z = coord
        return cls(
            fov_id=f"{region_id}_{index:04d}",
            region_id=region_id,
            fov_index=index,
            x_mm=x, y_mm=y, z_mm=z,
            metadata={"original_index": index}
        )

@dataclass
class FovTaskList:
    """Ordered list of FOV tasks with cursor and manipulation methods.

    Thread-safe: All mutations protected by internal lock.
    Single cursor owner: Only this class advances the cursor.
    """
    tasks: List[FovTask] = field(default_factory=list)
    cursor: int = 0
    plan_hash: str = ""   # Hash of original task list for resume validation
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def advance_and_get(self) -> Optional[FovTask]:
        """Move cursor to next PENDING task and return it.

        Caller does NOT modify cursor - this is the single owner.
        """
        with self._lock:
            while self.cursor < len(self.tasks):
                task = self.tasks[self.cursor]
                if task.status == FovStatus.PENDING:
                    return task
                self.cursor += 1
            return None

    def mark_complete(self, fov_id: str, success: bool, error_msg: Optional[str] = None):
        """Mark task complete and advance cursor.

        This is the single entry point for completion - caller never does cursor += 1.
        """
        with self._lock:
            task = self._find_task(fov_id)
            if task:
                task.status = FovStatus.COMPLETED if success else FovStatus.FAILED
                task.error_message = error_msg
                self.cursor += 1

    def jump_to(self, fov_id: str) -> bool:
        """Move cursor to task.

        NON-DESTRUCTIVE: Does not mark anything as skipped.
        Use skip() explicitly if you want to skip intervening tasks.
        """
        with self._lock:
            for i, task in enumerate(self.tasks):
                if task.fov_id == fov_id:
                    self.cursor = i
                    return True
            return False

    def skip(self, fov_id: str) -> bool:
        """Explicitly mark a PENDING task as SKIPPED."""
        with self._lock:
            task = self._find_task(fov_id)
            if task and task.status == FovStatus.PENDING:
                task.status = FovStatus.SKIPPED
                return True
            return False

    def defer(self, fov_id: str) -> bool:
        """Mark task as DEFERRED (will come back to it later)."""
        with self._lock:
            task = self._find_task(fov_id)
            if task and task.status == FovStatus.PENDING:
                task.status = FovStatus.DEFERRED
                return True
            return False

    def requeue(self, fov_id: str, before_current: bool = False) -> bool:
        """Re-add task for retry with attempt+1.

        IMPORTANT: Uses same fov_id, only increments attempt.
        This supports backtracking with consistent file naming.

        Args:
            fov_id: The task to requeue
            before_current: If True, insert before cursor (for backtracking).
                           If False, insert after cursor (for retry later).
        """
        with self._lock:
            original = self._find_task(fov_id)
            if not original:
                return False
            new_task = FovTask(
                fov_id=original.fov_id,  # SAME fov_id
                region_id=original.region_id,
                fov_index=original.fov_index,
                x_mm=original.x_mm, y_mm=original.y_mm, z_mm=original.z_mm,
                attempt=original.attempt + 1,
                metadata=original.metadata.copy(),
            )
            insert_pos = self.cursor if before_current else self.cursor + 1
            self.tasks.insert(insert_pos, new_task)
            return True

    def restore_deferred(self):
        """Reset all DEFERRED tasks back to PENDING.

        Called at end of time_point to revisit deferred tasks.
        """
        with self._lock:
            for task in self.tasks:
                if task.status == FovStatus.DEFERRED:
                    task.status = FovStatus.PENDING

    def _find_task(self, fov_id: str) -> Optional[FovTask]:
        """Find task by fov_id (internal, assumes lock held)."""
        for task in self.tasks:
            if task.fov_id == fov_id:
                return task
        return None

    # Status counts
    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.PENDING)

    def completed_count(self) -> int:
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.COMPLETED)

    def skipped_count(self) -> int:
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.SKIPPED)

    def deferred_count(self) -> int:
        with self._lock:
            return sum(1 for t in self.tasks if t.status == FovStatus.DEFERRED)

    # Serialization
    def to_checkpoint(self) -> dict:
        with self._lock:
            return {
                "cursor": self.cursor,
                "plan_hash": self.plan_hash,
                "tasks": [
                    {
                        "fov_id": t.fov_id,
                        "region_id": t.region_id,
                        "fov_index": t.fov_index,
                        "x_mm": t.x_mm, "y_mm": t.y_mm, "z_mm": t.z_mm,
                        "status": t.status.name,
                        "attempt": t.attempt,
                        "metadata": t.metadata,
                        "error_message": t.error_message,
                    }
                    for t in self.tasks
                ]
            }

    @classmethod
    def from_checkpoint(cls, data: dict) -> "FovTaskList":
        tasks = [
            FovTask(
                fov_id=t["fov_id"],
                region_id=t["region_id"],
                fov_index=t["fov_index"],
                x_mm=t["x_mm"], y_mm=t["y_mm"], z_mm=t["z_mm"],
                status=FovStatus[t["status"]],
                attempt=t["attempt"],
                metadata=t.get("metadata", {}),
                error_message=t.get("error_message"),
            )
            for t in data["tasks"]
        ]
        return cls(
            tasks=tasks,
            cursor=data["cursor"],
            plan_hash=data.get("plan_hash", ""),
        )
```

## Commands (UI → Backend)

All commands include context for multi-round scenarios:

**File:** `software/src/squid/backend/controllers/multipoint/events.py`

```python
from dataclasses import dataclass
from squid.core.events import Event

@dataclass(frozen=True)
class JumpToFovCommand(Event):
    """Non-destructive jump - just moves cursor, does not skip."""
    fov_id: str
    round_index: int
    time_point: int

@dataclass(frozen=True)
class SkipFovCommand(Event):
    """Explicitly skip a pending FOV."""
    fov_id: str
    round_index: int
    time_point: int

@dataclass(frozen=True)
class RequeueFovCommand(Event):
    """Re-add FOV for retry (same fov_id, attempt+1)."""
    fov_id: str
    round_index: int
    time_point: int
    before_current: bool = False  # True = insert before cursor (backtrack)

@dataclass(frozen=True)
class DeferFovCommand(Event):
    """Temporarily skip, will revisit at end of time_point."""
    fov_id: str
    round_index: int
    time_point: int
```

## Events (Backend → UI)

```python
from typing import Optional
from squid.backend.controllers.multipoint.fov_task import FovStatus

@dataclass(frozen=True)
class FovTaskStarted(Event):
    """Emitted when FOV task execution begins."""
    fov_id: str
    fov_index: int        # For compatibility with legacy tools
    region_id: str
    round_index: int
    time_point: int
    x_mm: float
    y_mm: float
    attempt: int
    pending_count: int
    completed_count: int

@dataclass(frozen=True)
class FovTaskCompleted(Event):
    """Emitted when FOV task completes."""
    fov_id: str
    fov_index: int
    round_index: int
    time_point: int
    status: FovStatus
    attempt: int
    error_message: Optional[str] = None

@dataclass(frozen=True)
class FovTaskListChanged(Event):
    """Emitted when task list is modified (jump/skip/requeue/defer)."""
    round_index: int
    time_point: int
    cursor: int
    pending_count: int
    completed_count: int
    skipped_count: int
    deferred_count: int
```

## Execution Loop

**File:** `software/src/squid/backend/controllers/multipoint/multi_point_worker.py`

```python
def run_coordinate_acquisition(self, round_index: int, time_point: int) -> bool:
    """Execute FOV tasks with command processing at boundaries.

    Key design points:
    - FovTaskList owns cursor movement (single owner)
    - Commands processed between FOVs (atomic FOV execution)
    - Thread-safe via FovTaskList internal lock
    """
    if self._fov_task_list is None:
        self._fov_task_list = self._build_fov_task_list()

    while True:
        # Process any pending commands (jump, skip, requeue, defer)
        self._process_pending_commands()

        # Get next task - FovTaskList owns cursor advancement
        task = self._fov_task_list.advance_and_get()
        if task is None:
            break  # All tasks processed

        # Check for pause/abort
        self._wait_if_paused()
        if self._abort_requested.is_set():
            return False

        # Execute the FOV
        task.status = FovStatus.EXECUTING
        self._publish_fov_started(task, round_index, time_point)

        try:
            success = self._execute_fov_task(task, time_point)
            # FovTaskList.mark_complete() handles cursor advancement
            self._fov_task_list.mark_complete(task.fov_id, success)
        except Exception as e:
            self._fov_task_list.mark_complete(task.fov_id, False, str(e))

        self._publish_fov_completed(task, round_index, time_point)

    # Restore any DEFERRED tasks for next time_point
    self._fov_task_list.restore_deferred()
    return True

def _process_pending_commands(self):
    """Process commands from queue. Thread-safe via FovTaskList internal lock."""
    while True:
        try:
            cmd = self._command_queue.get_nowait()
        except Empty:
            break

        if isinstance(cmd, JumpToFovCommand):
            self._fov_task_list.jump_to(cmd.fov_id)
        elif isinstance(cmd, SkipFovCommand):
            self._fov_task_list.skip(cmd.fov_id)
        elif isinstance(cmd, RequeueFovCommand):
            self._fov_task_list.requeue(cmd.fov_id, cmd.before_current)
        elif isinstance(cmd, DeferFovCommand):
            self._fov_task_list.defer(cmd.fov_id)

        self._publish_task_list_changed()

def queue_command(self, cmd: Event):
    """Queue a command for processing at next FOV boundary."""
    self._command_queue.put(cmd)
```

## File Naming

For backward compatibility, emit both `fov_id` and `fov_index`:

```python
def _build_file_id(self, task: FovTask, z_level: int) -> str:
    """Build file ID using stable fov_id.

    Format:
    - First attempt: {fov_id}_{z_level:04d}
    - Retry attempts: {fov_id}_attempt{N:02d}_{z_level:04d}
    """
    if task.attempt == 1:
        return f"{task.fov_id}_{z_level:04d}"
    else:
        return f"{task.fov_id}_attempt{task.attempt:02d}_{z_level:04d}"

# In metadata/CSV, include both for compatibility:
metadata = {
    "fov_id": task.fov_id,
    "fov_index": task.fov_index,  # For legacy tools expecting numeric index
    "attempt": task.attempt,
    "region_id": task.region_id,
}
```

## Checkpoint with Plan Validation

**File:** `software/src/squid/backend/controllers/multipoint/checkpoint.py`

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import hashlib

class CheckpointPlanMismatch(Exception):
    """Raised when checkpoint plan_hash doesn't match current plan."""
    pass

@dataclass
class MultiPointCheckpoint:
    """Checkpoint for resuming multipoint acquisition.

    Includes plan_hash to detect if FOV list changed since checkpoint.
    """
    experiment_id: str
    round_index: int
    time_point: int
    fov_task_list: dict
    plan_hash: str
    created_at: datetime

    def save(self, path: Path):
        """Save checkpoint atomically (write to temp, rename)."""
        data = {
            "experiment_id": self.experiment_id,
            "round_index": self.round_index,
            "time_point": self.time_point,
            "fov_task_list": self.fov_task_list,
            "plan_hash": self.plan_hash,
            "created_at": self.created_at.isoformat(),
        }
        temp_path = path.with_suffix('.tmp')
        temp_path.write_text(json.dumps(data, indent=2))
        temp_path.rename(path)

    @classmethod
    def load(cls, path: Path, current_plan_hash: str) -> "MultiPointCheckpoint":
        """Load checkpoint and validate plan_hash matches."""
        data = json.loads(path.read_text())
        checkpoint = cls(
            experiment_id=data["experiment_id"],
            round_index=data["round_index"],
            time_point=data["time_point"],
            fov_task_list=data["fov_task_list"],
            plan_hash=data["plan_hash"],
            created_at=datetime.fromisoformat(data["created_at"]),
        )
        if checkpoint.plan_hash != current_plan_hash:
            raise CheckpointPlanMismatch(
                f"Plan changed since checkpoint was created. "
                f"Checkpoint hash: {checkpoint.plan_hash[:8]}..., "
                f"Current hash: {current_plan_hash[:8]}... "
                f"Use --force-resume to override."
            )
        return checkpoint

def compute_plan_hash(fov_task_list: "FovTaskList") -> str:
    """Compute hash of original task list for resume validation."""
    # Hash based on fov_ids and positions, not status
    data = [
        (t.fov_id, t.region_id, t.x_mm, t.y_mm, t.z_mm)
        for t in fov_task_list.tasks
    ]
    return hashlib.sha256(str(data).encode()).hexdigest()[:16]
```

---

# Part B: DAVE-Inspired Features

## Feature 1: Test Mode / Validation

**Files:**
- `software/src/squid/backend/controllers/orchestrator/validation.py` (dataclasses)
- `software/src/squid/backend/controllers/orchestrator/protocol_validator.py` (implementation)
- `software/src/squid/ui/widgets/orchestrator/validation_dialog.py` (UI)

Validates protocol before execution:
- Check all referenced channels exist
- Validate output paths
- Estimate time per round (FOVs × seconds_per_fov)
- Estimate disk usage
- Show summary dialog with Start/Cancel

## Feature 2: Warning Accumulation

**Files:**
- `software/src/squid/backend/controllers/orchestrator/warnings.py` (dataclasses)
- `software/src/squid/backend/controllers/orchestrator/warning_manager.py` (implementation)
- `software/src/squid/ui/widgets/orchestrator/warning_panel.py` (UI)

Warnings include `fov_id` and `fov_index` for navigation:
```python
@dataclass(frozen=True)
class OrchestratorWarning:
    timestamp: datetime
    round_index: int
    time_point: int
    fov_id: Optional[str]       # Links to FovTask
    fov_index: Optional[int]    # For compatibility
    category: str               # "focus", "signal", "timing", "hardware"
    severity: str               # "info", "warning", "error"
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
```

## Feature 3: UI Improvements

**File:** `software/src/squid/ui/widgets/orchestrator/orchestrator_widget.py`

- Current action highlighting via `FovTaskStarted` events
- Context menu with **separate** Jump and Skip actions
- Parameter inspection panel
- Time estimates in tree

---

## Critical Files (Actual Repo Paths)

| File | Changes |
|------|---------|
| `software/src/squid/backend/controllers/multipoint/fov_task.py` | NEW - FovTask, FovStatus, FovTaskList |
| `software/src/squid/backend/controllers/multipoint/multi_point_worker.py` | Refactor run_coordinate_acquisition |
| `software/src/squid/backend/controllers/multipoint/events.py` | NEW - Commands and events |
| `software/src/squid/backend/controllers/multipoint/checkpoint.py` | NEW - Checkpoint with plan_hash |
| `software/src/squid/backend/controllers/multipoint/controller.py` | Add command handlers |
| `software/src/squid/backend/controllers/multipoint/job_processing.py` | Add fov_id to CaptureInfo |
| `software/src/squid/backend/controllers/orchestrator/state.py` | New events |
| `software/src/squid/backend/controllers/orchestrator/orchestrator_controller.py` | Integration |
| `software/src/squid/backend/controllers/orchestrator/warnings.py` | NEW - Warning dataclasses |
| `software/src/squid/backend/controllers/orchestrator/warning_manager.py` | NEW - Warning manager |
| `software/src/squid/backend/controllers/orchestrator/validation.py` | NEW - Validation dataclasses |
| `software/src/squid/backend/controllers/orchestrator/protocol_validator.py` | NEW - Protocol validator |
| `software/src/squid/ui/widgets/orchestrator/orchestrator_widget.py` | UI updates |
| `software/src/squid/ui/widgets/orchestrator/warning_panel.py` | NEW - Warning display |
| `software/src/squid/ui/widgets/orchestrator/validation_dialog.py` | NEW - Validation results |
| `software/src/squid/ui/widgets/orchestrator/parameter_panel.py` | NEW - Parameter inspection |

---

## Key Semantic Changes from Original Plan

| Original | Revised |
|----------|---------|
| `jump_to(skip_intervening=True)` | `jump_to()` non-destructive, `skip()` separate |
| Caller does `cursor += 1` | `FovTaskList.mark_complete()` owns cursor |
| No DEFERRED state | DEFERRED for "come back later" |
| Requeue creates new fov_id | Requeue keeps same fov_id, increments attempt |
| Events lack context | All events include round_index, time_point |
| Only fov_id in naming | Both fov_id and fov_index for compatibility |
| No plan validation | Checkpoint includes plan_hash, validates on resume |
| Implicit thread safety | Explicit `_lock` in FovTaskList |
