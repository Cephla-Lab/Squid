"""
MultiPoint Checkpoint System - Save and restore acquisition state.

This module provides checkpoint support for multipoint acquisitions:
- Atomic checkpoint saves (temp file + rename)
- Plan hash validation on resume
- FovTaskList state preservation
"""

import json
import hashlib
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from squid.backend.controllers.multipoint.fov_task import FovTaskList


class CheckpointPlanMismatch(Exception):
    """Raised when checkpoint plan_hash doesn't match current plan.

    This indicates the FOV list has changed since the checkpoint was created.
    """

    def __init__(self, expected_hash: str, actual_hash: str, message: str = ""):
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            message or f"Checkpoint plan_hash mismatch: expected {expected_hash}, got {actual_hash}"
        )


def compute_plan_hash(fov_task_list: FovTaskList) -> str:
    """Compute a hash of the FOV task list for plan validation.

    The hash includes fov_id and coordinates to detect any changes
    to the planned acquisition.

    Args:
        fov_task_list: The task list to hash

    Returns:
        A 16-character hex hash string
    """
    hash_data = [
        (t.fov_id, t.region_id, t.x_mm, t.y_mm, t.z_mm)
        for t in fov_task_list.tasks
    ]
    return hashlib.sha256(str(hash_data).encode()).hexdigest()[:16]


@dataclass
class MultiPointCheckpoint:
    """Checkpoint data for multipoint acquisition state.

    Attributes:
        experiment_id: The experiment identifier
        round_index: Current round index
        time_point: Current time point
        fov_task_list_data: Serialized FovTaskList data
        plan_hash: Hash of original plan for validation
        created_at: ISO format timestamp when checkpoint was created
    """

    experiment_id: str
    round_index: int
    time_point: int
    fov_task_list_data: dict
    plan_hash: str
    created_at: str

    def save(self, path: Path) -> None:
        """Save checkpoint to file atomically.

        Uses temp file + rename for atomic write to prevent corruption.

        Args:
            path: The file path to save to
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict for JSON serialization
        data = {
            "experiment_id": self.experiment_id,
            "round_index": self.round_index,
            "time_point": self.time_point,
            "fov_task_list_data": self.fov_task_list_data,
            "plan_hash": self.plan_hash,
            "created_at": self.created_at,
        }

        # Write to temp file first, then rename (atomic on most filesystems)
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=".checkpoint_",
            suffix=".json.tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            # Atomic rename
            os.replace(tmp_path, path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: Path, current_plan_hash: Optional[str] = None) -> "MultiPointCheckpoint":
        """Load checkpoint from file with optional plan validation.

        Args:
            path: The file path to load from
            current_plan_hash: If provided, validate that checkpoint plan_hash matches.
                              Raises CheckpointPlanMismatch if they differ.

        Returns:
            The loaded MultiPointCheckpoint

        Raises:
            CheckpointPlanMismatch: If current_plan_hash provided and doesn't match
            FileNotFoundError: If checkpoint file doesn't exist
            json.JSONDecodeError: If file is not valid JSON
        """
        path = Path(path)
        with open(path, "r") as f:
            data = json.load(f)

        checkpoint = cls(
            experiment_id=data["experiment_id"],
            round_index=data["round_index"],
            time_point=data["time_point"],
            fov_task_list_data=data["fov_task_list_data"],
            plan_hash=data["plan_hash"],
            created_at=data["created_at"],
        )

        # Validate plan hash if provided
        if current_plan_hash is not None and checkpoint.plan_hash != current_plan_hash:
            raise CheckpointPlanMismatch(
                expected_hash=current_plan_hash,
                actual_hash=checkpoint.plan_hash,
                message=(
                    f"FOV plan has changed since checkpoint was created. "
                    f"Expected plan_hash={current_plan_hash}, "
                    f"checkpoint has plan_hash={checkpoint.plan_hash}"
                ),
            )

        return checkpoint

    @classmethod
    def from_state(
        cls,
        experiment_id: str,
        round_index: int,
        time_point: int,
        fov_task_list: FovTaskList,
    ) -> "MultiPointCheckpoint":
        """Create a checkpoint from current acquisition state.

        Args:
            experiment_id: The experiment identifier
            round_index: Current round index
            time_point: Current time point
            fov_task_list: The current FovTaskList state

        Returns:
            A new MultiPointCheckpoint ready to save
        """
        return cls(
            experiment_id=experiment_id,
            round_index=round_index,
            time_point=time_point,
            fov_task_list_data=fov_task_list.to_checkpoint(),
            plan_hash=fov_task_list.plan_hash,
            created_at=datetime.utcnow().isoformat() + "Z",
        )

    def restore_fov_task_list(self) -> FovTaskList:
        """Restore FovTaskList from checkpoint data.

        Returns:
            The restored FovTaskList with cursor and status preserved
        """
        return FovTaskList.from_checkpoint(self.fov_task_list_data)


def get_checkpoint_path(experiment_path: str, time_point: int) -> Path:
    """Get the checkpoint file path for a given experiment and time point.

    Args:
        experiment_path: Base path for the experiment
        time_point: Current time point

    Returns:
        Path to the checkpoint file
    """
    return Path(experiment_path) / "checkpoints" / f"checkpoint_t{time_point:04d}.json"


def find_latest_checkpoint(experiment_path: str) -> Optional[Path]:
    """Find the most recent checkpoint file in an experiment directory.

    Args:
        experiment_path: Base path for the experiment

    Returns:
        Path to the latest checkpoint file, or None if no checkpoints exist
    """
    checkpoint_dir = Path(experiment_path) / "checkpoints"
    if not checkpoint_dir.exists():
        return None

    checkpoints = sorted(checkpoint_dir.glob("checkpoint_t*.json"))
    if not checkpoints:
        return None

    return checkpoints[-1]
