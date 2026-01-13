"""Unit tests for CheckpointManager."""

import pytest
import tempfile
import os
import json
from datetime import datetime

from squid.backend.controllers.orchestrator.checkpoint import CheckpointManager
from squid.backend.controllers.orchestrator.state import Checkpoint


@pytest.fixture
def checkpoint_manager():
    """Create a CheckpointManager for testing."""
    return CheckpointManager()


@pytest.fixture
def sample_checkpoint():
    """Create a sample checkpoint for testing."""
    return Checkpoint(
        protocol_name="test_protocol",
        protocol_version="1.0.0",
        experiment_id="test_experiment_001",
        experiment_path="/tmp/experiments/test_001",
        round_index=2,
        fluidics_step_index=1,
        imaging_fov_index=5,
        created_at=datetime.now(),
    )


class TestCheckpointCreation:
    """Tests for checkpoint creation."""

    def test_create_checkpoint(self, checkpoint_manager):
        """Test creating a checkpoint from parameters."""
        checkpoint = checkpoint_manager.create_checkpoint(
            protocol_name="my_protocol",
            protocol_version="2.0.0",
            experiment_id="exp_123",
            experiment_path="/data/experiments/exp_123",
            round_index=3,
            fluidics_step_index=2,
            imaging_fov_index=10,
        )

        assert checkpoint.protocol_name == "my_protocol"
        assert checkpoint.protocol_version == "2.0.0"
        assert checkpoint.experiment_id == "exp_123"
        assert checkpoint.experiment_path == "/data/experiments/exp_123"
        assert checkpoint.round_index == 3
        assert checkpoint.fluidics_step_index == 2
        assert checkpoint.imaging_fov_index == 10
        assert checkpoint.created_at is not None

    def test_create_checkpoint_with_defaults(self, checkpoint_manager):
        """Test creating a checkpoint with default values."""
        checkpoint = checkpoint_manager.create_checkpoint(
            protocol_name="minimal_protocol",
            protocol_version="1.0",
            experiment_id="exp_minimal",
            experiment_path="/tmp/minimal",
            round_index=0,
        )

        assert checkpoint.fluidics_step_index == 0
        assert checkpoint.imaging_fov_index == 0


class TestCheckpointPersistence:
    """Tests for checkpoint save/load functionality."""

    def test_save_and_load_checkpoint(
        self, checkpoint_manager, sample_checkpoint
    ):
        """Test saving and loading a checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save checkpoint
            checkpoint_manager.save(sample_checkpoint, tmpdir)

            # Verify file exists
            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            assert os.path.exists(checkpoint_file)

            # Load checkpoint
            loaded = checkpoint_manager.load(tmpdir)

            assert loaded is not None
            assert loaded.protocol_name == sample_checkpoint.protocol_name
            assert loaded.protocol_version == sample_checkpoint.protocol_version
            assert loaded.experiment_id == sample_checkpoint.experiment_id
            assert loaded.round_index == sample_checkpoint.round_index
            assert loaded.fluidics_step_index == sample_checkpoint.fluidics_step_index
            assert loaded.imaging_fov_index == sample_checkpoint.imaging_fov_index

    def test_load_nonexistent_checkpoint(self, checkpoint_manager):
        """Test loading from a path with no checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = checkpoint_manager.load(tmpdir)
            assert loaded is None

    def test_clear_checkpoint(self, checkpoint_manager, sample_checkpoint):
        """Test clearing a checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save checkpoint
            checkpoint_manager.save(sample_checkpoint, tmpdir)
            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            assert os.path.exists(checkpoint_file)

            # Clear checkpoint
            checkpoint_manager.clear(tmpdir)

            # Verify file is gone
            assert not os.path.exists(checkpoint_file)

    def test_clear_nonexistent_checkpoint_no_error(self, checkpoint_manager):
        """Test that clearing nonexistent checkpoint doesn't error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Should not raise
            checkpoint_manager.clear(tmpdir)


class TestCheckpointFileFormat:
    """Tests for checkpoint file format."""

    def test_checkpoint_file_is_valid_json(
        self, checkpoint_manager, sample_checkpoint
    ):
        """Test that saved checkpoint is valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_manager.save(sample_checkpoint, tmpdir)

            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            with open(checkpoint_file, "r") as f:
                data = json.load(f)

            assert "protocol_name" in data
            assert "round_index" in data
            assert "created_at" in data

    def test_checkpoint_contains_all_fields(
        self, checkpoint_manager, sample_checkpoint
    ):
        """Test that checkpoint file contains all expected fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_manager.save(sample_checkpoint, tmpdir)

            checkpoint_file = os.path.join(tmpdir, "checkpoint.json")
            with open(checkpoint_file, "r") as f:
                data = json.load(f)

            expected_fields = [
                "protocol_name",
                "protocol_version",
                "experiment_id",
                "experiment_path",
                "round_index",
                "fluidics_step_index",
                "imaging_fov_index",
                "created_at",
            ]

            for field in expected_fields:
                assert field in data, f"Missing field: {field}"
