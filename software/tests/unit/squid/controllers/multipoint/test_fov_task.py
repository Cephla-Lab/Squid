"""Unit tests for FovTask and FovStatus."""

import pytest

from squid.backend.controllers.multipoint.fov_task import (
    FovStatus,
    FovTask,
)


class TestFovStatus:
    """Tests for FovStatus enum."""

    def test_all_statuses_exist(self):
        """Verify all expected status values exist."""
        assert FovStatus.PENDING
        assert FovStatus.EXECUTING
        assert FovStatus.COMPLETED
        assert FovStatus.FAILED
        assert FovStatus.SKIPPED
        assert FovStatus.DEFERRED

    def test_statuses_are_distinct(self):
        """Verify all status values are unique."""
        statuses = list(FovStatus)
        assert len(statuses) == len(set(statuses))
        assert len(statuses) == 6


class TestFovTask:
    """Tests for FovTask dataclass."""

    def test_fov_task_creation(self):
        """Test basic FovTask creation."""
        task = FovTask(
            fov_id="region_0001",
            region_id="region",
            fov_index=1,
            x_mm=10.0,
            y_mm=20.0,
            z_mm=5.0,
        )
        assert task.fov_id == "region_0001"
        assert task.region_id == "region"
        assert task.fov_index == 1
        assert task.x_mm == 10.0
        assert task.y_mm == 20.0
        assert task.z_mm == 5.0
        assert task.status == FovStatus.PENDING
        assert task.attempt == 1
        assert task.metadata == {}
        assert task.error_message is None

    def test_fov_task_with_metadata(self):
        """Test FovTask creation with custom metadata."""
        task = FovTask(
            fov_id="A1_0001",
            region_id="A1",
            fov_index=1,
            x_mm=0.0,
            y_mm=0.0,
            z_mm=0.0,
            metadata={"well": "A1", "row": 0, "col": 0},
        )
        assert task.metadata == {"well": "A1", "row": 0, "col": 0}

    def test_fov_task_with_error(self):
        """Test FovTask with error state."""
        task = FovTask(
            fov_id="region_0001",
            region_id="region",
            fov_index=1,
            x_mm=0.0,
            y_mm=0.0,
            z_mm=0.0,
            status=FovStatus.FAILED,
            error_message="Camera timeout",
        )
        assert task.status == FovStatus.FAILED
        assert task.error_message == "Camera timeout"

    def test_fov_task_from_coordinate(self):
        """Test FovTask.from_coordinate factory method."""
        coord = (10.5, 20.5, 5.0)
        task = FovTask.from_coordinate("A1", 5, coord)

        assert task.fov_id == "A1_0005"
        assert task.region_id == "A1"
        assert task.fov_index == 5
        assert task.x_mm == 10.5
        assert task.y_mm == 20.5
        assert task.z_mm == 5.0
        assert task.status == FovStatus.PENDING
        assert task.attempt == 1
        assert task.metadata == {"original_index": 5}

    def test_fov_task_from_coordinate_with_extra_values(self):
        """Test from_coordinate handles tuples with extra values."""
        coord = (10.0, 20.0, 5.0, "extra", 123)
        task = FovTask.from_coordinate("region", 0, coord)

        assert task.x_mm == 10.0
        assert task.y_mm == 20.0
        assert task.z_mm == 5.0

    def test_fov_task_fov_id_format(self):
        """Test fov_id format is consistent with zero-padding."""
        task0 = FovTask.from_coordinate("A1", 0, (0, 0, 0))
        task1 = FovTask.from_coordinate("A1", 1, (0, 0, 0))
        task99 = FovTask.from_coordinate("A1", 99, (0, 0, 0))
        task999 = FovTask.from_coordinate("A1", 999, (0, 0, 0))
        task9999 = FovTask.from_coordinate("A1", 9999, (0, 0, 0))

        assert task0.fov_id == "A1_0000"
        assert task1.fov_id == "A1_0001"
        assert task99.fov_id == "A1_0099"
        assert task999.fov_id == "A1_0999"
        assert task9999.fov_id == "A1_9999"

    def test_fov_task_includes_fov_index(self):
        """Test that fov_index is preserved for backward compatibility."""
        task = FovTask.from_coordinate("region", 42, (0, 0, 0))
        assert task.fov_index == 42
        assert task.fov_id == "region_0042"
        # Both should be accessible independently
        assert task.fov_index != task.fov_id

    def test_fov_task_mutable(self):
        """Test that FovTask fields can be mutated (not frozen)."""
        task = FovTask(
            fov_id="test_0001",
            region_id="test",
            fov_index=1,
            x_mm=0.0,
            y_mm=0.0,
            z_mm=0.0,
        )
        task.status = FovStatus.EXECUTING
        assert task.status == FovStatus.EXECUTING

        task.attempt = 2
        assert task.attempt == 2

        task.error_message = "Test error"
        assert task.error_message == "Test error"
