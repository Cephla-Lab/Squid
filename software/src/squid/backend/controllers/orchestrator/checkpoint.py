"""
Checkpoint manager for experiment recovery.

Saves and loads checkpoints to enable resuming experiments after
pauses, crashes, or system restarts.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import squid.core.logging
from squid.backend.controllers.orchestrator.state import Checkpoint

_log = squid.core.logging.get_logger(__name__)


class CheckpointManager:
    """Manages experiment checkpoints for recovery.

    Checkpoints are saved as JSON files in the experiment directory.
    They capture the state needed to resume an experiment from any point.

    Usage:
        manager = CheckpointManager()

        # Save checkpoint
        manager.save(checkpoint, experiment_path)

        # Load checkpoint
        checkpoint = manager.load(experiment_path)

        # Clear checkpoint on completion
        manager.clear(experiment_path)
    """

    CHECKPOINT_FILENAME = "checkpoint.json"

    def __init__(self):
        """Initialize the checkpoint manager."""
        pass

    def save(
        self,
        checkpoint: Checkpoint,
        experiment_path: str,
    ) -> str:
        """Save a checkpoint to the experiment directory.

        Args:
            checkpoint: Checkpoint to save
            experiment_path: Path to experiment directory

        Returns:
            Path to the saved checkpoint file
        """
        # Ensure directory exists
        os.makedirs(experiment_path, exist_ok=True)

        checkpoint_path = os.path.join(experiment_path, self.CHECKPOINT_FILENAME)

        data = {
            "protocol_name": checkpoint.protocol_name,
            "protocol_version": checkpoint.protocol_version,
            "experiment_id": checkpoint.experiment_id,
            "experiment_path": checkpoint.experiment_path,
            "round_index": checkpoint.round_index,
            "step_index": checkpoint.step_index,  # V2: step position within round
            "imaging_fov_index": checkpoint.imaging_fov_index,
            "imaging_z_index": checkpoint.imaging_z_index,
            "imaging_channel_index": checkpoint.imaging_channel_index,
            "created_at": checkpoint.created_at.isoformat(),
            "paused_at": checkpoint.paused_at.isoformat() if checkpoint.paused_at else None,
            "metadata": checkpoint.metadata,
        }

        with open(checkpoint_path, "w") as f:
            json.dump(data, f, indent=2)

        _log.info(f"Saved checkpoint to {checkpoint_path}")
        return checkpoint_path

    def load(self, experiment_path: str) -> Optional[Checkpoint]:
        """Load a checkpoint from the experiment directory.

        Args:
            experiment_path: Path to experiment directory

        Returns:
            Checkpoint if found, None otherwise
        """
        checkpoint_path = os.path.join(experiment_path, self.CHECKPOINT_FILENAME)

        if not os.path.exists(checkpoint_path):
            return None

        try:
            with open(checkpoint_path, "r") as f:
                data = json.load(f)

            checkpoint = Checkpoint(
                protocol_name=data["protocol_name"],
                protocol_version=data["protocol_version"],
                experiment_id=data["experiment_id"],
                experiment_path=data["experiment_path"],
                round_index=data["round_index"],
                step_index=data.get("step_index", 0),  # V2: step position within round
                imaging_fov_index=data.get("imaging_fov_index", 0),
                imaging_z_index=data.get("imaging_z_index", 0),
                imaging_channel_index=data.get("imaging_channel_index", 0),
                created_at=datetime.fromisoformat(data["created_at"]),
                paused_at=datetime.fromisoformat(data["paused_at"]) if data.get("paused_at") else None,
                metadata=data.get("metadata", {}),
            )

            _log.info(f"Loaded checkpoint from {checkpoint_path}")
            return checkpoint

        except Exception as e:
            _log.error(f"Failed to load checkpoint: {e}")
            return None

    def clear(self, experiment_path: str) -> bool:
        """Clear the checkpoint for an experiment.

        Called when an experiment completes successfully.

        Args:
            experiment_path: Path to experiment directory

        Returns:
            True if checkpoint was cleared, False if no checkpoint existed
        """
        checkpoint_path = os.path.join(experiment_path, self.CHECKPOINT_FILENAME)

        if not os.path.exists(checkpoint_path):
            return False

        try:
            os.remove(checkpoint_path)
            _log.info(f"Cleared checkpoint from {checkpoint_path}")
            return True
        except Exception as e:
            _log.warning(f"Failed to clear checkpoint: {e}")
            return False

    def exists(self, experiment_path: str) -> bool:
        """Check if a checkpoint exists for an experiment.

        Args:
            experiment_path: Path to experiment directory

        Returns:
            True if checkpoint exists
        """
        checkpoint_path = os.path.join(experiment_path, self.CHECKPOINT_FILENAME)
        return os.path.exists(checkpoint_path)

    def get_checkpoint_path(self, experiment_path: str) -> str:
        """Get the path to the checkpoint file.

        Args:
            experiment_path: Path to experiment directory

        Returns:
            Path to checkpoint file (may not exist)
        """
        return os.path.join(experiment_path, self.CHECKPOINT_FILENAME)

    def create_checkpoint(
        self,
        protocol_name: str,
        protocol_version: str,
        experiment_id: str,
        experiment_path: str,
        round_index: int,
        step_index: int = 0,
        imaging_fov_index: int = 0,
        imaging_z_index: int = 0,
        imaging_channel_index: int = 0,
        metadata: Optional[dict] = None,
    ) -> Checkpoint:
        """Create a new checkpoint object.

        Convenience method for creating checkpoints.

        Args:
            protocol_name: Name of the protocol
            protocol_version: Version of the protocol
            experiment_id: Experiment identifier
            experiment_path: Path to experiment directory
            round_index: Current round index
            step_index: V2 step position within round's steps list
            imaging_fov_index: Current imaging FOV index
            imaging_z_index: Current z-plane index
            imaging_channel_index: Current channel index
            metadata: Additional metadata

        Returns:
            New Checkpoint object
        """
        return Checkpoint(
            protocol_name=protocol_name,
            protocol_version=protocol_version,
            experiment_id=experiment_id,
            experiment_path=experiment_path,
            round_index=round_index,
            step_index=step_index,
            imaging_fov_index=imaging_fov_index,
            imaging_z_index=imaging_z_index,
            imaging_channel_index=imaging_channel_index,
            metadata=metadata or {},
        )
