"""Unit tests for MultiPointCheckpoint."""

import json
import tempfile
from pathlib import Path

import pytest

from squid.backend.controllers.multipoint.checkpoint import (
    CheckpointPlanMismatch,
    MultiPointCheckpoint,
    compute_plan_hash,
    get_checkpoint_path,
    find_latest_checkpoint,
)
from squid.backend.controllers.multipoint.fov_task import (
    FovStatus,
    FovTask,
    FovTaskList,
)


class TestComputePlanHash:
    """Tests for compute_plan_hash function."""

    def test_hash_is_deterministic(self):
        """Test that same task list produces same hash."""
        tasks = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks)

        hash1 = compute_plan_hash(task_list)
        hash2 = compute_plan_hash(task_list)

        assert hash1 == hash2

    def test_hash_changes_with_coordinates(self):
        """Test that different coordinates produce different hash."""
        tasks1 = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(5)]
        tasks2 = [FovTask.from_coordinate("A1", i, (i, 1, 0)) for i in range(5)]  # Different y
        task_list1 = FovTaskList(tasks=tasks1)
        task_list2 = FovTaskList(tasks=tasks2)

        hash1 = compute_plan_hash(task_list1)
        hash2 = compute_plan_hash(task_list2)

        assert hash1 != hash2

    def test_hash_changes_with_fov_ids(self):
        """Test that different fov_ids produce different hash."""
        tasks1 = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(5)]
        tasks2 = [FovTask.from_coordinate("A2", i, (i, 0, 0)) for i in range(5)]  # Different region
        task_list1 = FovTaskList(tasks=tasks1)
        task_list2 = FovTaskList(tasks=tasks2)

        hash1 = compute_plan_hash(task_list1)
        hash2 = compute_plan_hash(task_list2)

        assert hash1 != hash2

    def test_hash_length(self):
        """Test that hash is 16 characters."""
        tasks = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks)

        hash_val = compute_plan_hash(task_list)

        assert len(hash_val) == 16


class TestMultiPointCheckpoint:
    """Tests for MultiPointCheckpoint class."""

    def test_from_state(self):
        """Test creating checkpoint from current state."""
        tasks = [FovTask.from_coordinate("A1", i, (i * 1.0, 0, 0)) for i in range(5)]
        tasks[0].status = FovStatus.COMPLETED
        tasks[1].status = FovStatus.COMPLETED
        task_list = FovTaskList(tasks=tasks, cursor=2, plan_hash="test_hash")

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="exp001",
            round_index=0,
            time_point=3,
            fov_task_list=task_list,
        )

        assert checkpoint.experiment_id == "exp001"
        assert checkpoint.round_index == 0
        assert checkpoint.time_point == 3
        assert checkpoint.plan_hash == "test_hash"
        assert checkpoint.created_at.endswith("Z")

    def test_save_and_load(self):
        """Test saving and loading checkpoint."""
        tasks = [FovTask.from_coordinate("A1", i, (i * 1.0, i * 2.0, 0)) for i in range(3)]
        tasks[0].status = FovStatus.COMPLETED
        task_list = FovTaskList(tasks=tasks, cursor=1, plan_hash="test_hash_123")

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="test_exp",
            round_index=2,
            time_point=5,
            fov_task_list=task_list,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "checkpoint.json"
            checkpoint.save(path)

            # Verify file exists
            assert path.exists()

            # Load and verify
            loaded = MultiPointCheckpoint.load(path)

            assert loaded.experiment_id == "test_exp"
            assert loaded.round_index == 2
            assert loaded.time_point == 5
            assert loaded.plan_hash == "test_hash_123"

    def test_load_validates_plan_hash(self):
        """Test that load raises CheckpointPlanMismatch on hash mismatch."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks, plan_hash="original_hash")

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="exp",
            round_index=0,
            time_point=0,
            fov_task_list=task_list,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "checkpoint.json"
            checkpoint.save(path)

            # Load with different plan hash should raise
            with pytest.raises(CheckpointPlanMismatch) as exc_info:
                MultiPointCheckpoint.load(path, current_plan_hash="different_hash")

            assert exc_info.value.expected_hash == "different_hash"
            assert exc_info.value.actual_hash == "original_hash"

    def test_load_allows_matching_plan_hash(self):
        """Test that load succeeds when plan hashes match."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks, plan_hash="matching_hash")

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="exp",
            round_index=0,
            time_point=0,
            fov_task_list=task_list,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "checkpoint.json"
            checkpoint.save(path)

            # Load with same plan hash should succeed
            loaded = MultiPointCheckpoint.load(path, current_plan_hash="matching_hash")
            assert loaded.plan_hash == "matching_hash"

    def test_load_without_validation(self):
        """Test that load succeeds without plan hash validation."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks, plan_hash="any_hash")

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="exp",
            round_index=0,
            time_point=0,
            fov_task_list=task_list,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "checkpoint.json"
            checkpoint.save(path)

            # Load without providing current_plan_hash
            loaded = MultiPointCheckpoint.load(path)
            assert loaded.plan_hash == "any_hash"

    def test_restore_fov_task_list(self):
        """Test restoring FovTaskList from checkpoint."""
        tasks = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(5)]
        tasks[0].status = FovStatus.COMPLETED
        tasks[1].status = FovStatus.COMPLETED
        tasks[2].status = FovStatus.FAILED
        tasks[2].error_message = "Test error"
        task_list = FovTaskList(tasks=tasks, cursor=3, plan_hash="hash123")

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="exp",
            round_index=0,
            time_point=0,
            fov_task_list=task_list,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "checkpoint.json"
            checkpoint.save(path)

            loaded = MultiPointCheckpoint.load(path)
            restored = loaded.restore_fov_task_list()

            assert len(restored) == 5
            assert restored.cursor == 3
            assert restored.plan_hash == "hash123"
            assert restored.tasks[0].status == FovStatus.COMPLETED
            assert restored.tasks[2].status == FovStatus.FAILED
            assert restored.tasks[2].error_message == "Test error"
            assert restored.tasks[4].status == FovStatus.PENDING

    def test_atomic_save(self):
        """Test that save is atomic (doesn't leave partial files)."""
        tasks = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(100)]
        task_list = FovTaskList(tasks=tasks, plan_hash="hash")

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="exp",
            round_index=0,
            time_point=0,
            fov_task_list=task_list,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir" / "checkpoint.json"

            # Save should create directory and file atomically
            checkpoint.save(path)

            assert path.exists()
            # No temp files should remain
            tmp_files = list(path.parent.glob(".checkpoint_*"))
            assert len(tmp_files) == 0

    def test_save_creates_parent_directories(self):
        """Test that save creates parent directories if needed."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        checkpoint = MultiPointCheckpoint.from_state(
            experiment_id="exp",
            round_index=0,
            time_point=0,
            fov_task_list=task_list,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "deep" / "nested" / "dir" / "checkpoint.json"

            checkpoint.save(path)

            assert path.exists()


class TestCheckpointPaths:
    """Tests for checkpoint path utilities."""

    def test_get_checkpoint_path(self):
        """Test get_checkpoint_path generates correct path."""
        path = get_checkpoint_path("/data/experiment001", 5)

        assert str(path) == "/data/experiment001/checkpoints/checkpoint_t0005.json"

    def test_get_checkpoint_path_zero_padded(self):
        """Test get_checkpoint_path uses zero-padded time points."""
        path1 = get_checkpoint_path("/data/exp", 0)
        path2 = get_checkpoint_path("/data/exp", 123)
        path3 = get_checkpoint_path("/data/exp", 9999)

        assert path1.name == "checkpoint_t0000.json"
        assert path2.name == "checkpoint_t0123.json"
        assert path3.name == "checkpoint_t9999.json"

    def test_find_latest_checkpoint_no_dir(self):
        """Test find_latest_checkpoint returns None if no checkpoint dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_latest_checkpoint(tmpdir)
            assert result is None

    def test_find_latest_checkpoint_empty_dir(self):
        """Test find_latest_checkpoint returns None if dir is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "checkpoints").mkdir()
            result = find_latest_checkpoint(tmpdir)
            assert result is None

    def test_find_latest_checkpoint(self):
        """Test find_latest_checkpoint returns most recent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"
            checkpoint_dir.mkdir()

            # Create some checkpoint files
            (checkpoint_dir / "checkpoint_t0000.json").touch()
            (checkpoint_dir / "checkpoint_t0003.json").touch()
            (checkpoint_dir / "checkpoint_t0001.json").touch()

            result = find_latest_checkpoint(tmpdir)

            assert result is not None
            assert result.name == "checkpoint_t0003.json"


class TestCheckpointPlanMismatch:
    """Tests for CheckpointPlanMismatch exception."""

    def test_exception_attributes(self):
        """Test exception has expected attributes."""
        exc = CheckpointPlanMismatch(
            expected_hash="expected",
            actual_hash="actual",
        )

        assert exc.expected_hash == "expected"
        assert exc.actual_hash == "actual"
        assert "expected" in str(exc)
        assert "actual" in str(exc)

    def test_exception_custom_message(self):
        """Test exception with custom message."""
        exc = CheckpointPlanMismatch(
            expected_hash="exp",
            actual_hash="act",
            message="Custom error message",
        )

        assert str(exc) == "Custom error message"
