"""Unit tests for FovTaskList."""

import threading
import pytest

from squid.backend.controllers.multipoint.fov_task import (
    FovStatus,
    FovTask,
    FovTaskList,
)


class TestFovTaskListCreation:
    """Tests for FovTaskList creation and basic operations."""

    def test_task_list_creation_empty(self):
        """Test creating empty task list."""
        task_list = FovTaskList()
        assert len(task_list) == 0
        assert task_list.cursor == 0
        assert task_list.plan_hash == ""

    def test_task_list_creation_with_tasks(self):
        """Test creating task list with tasks."""
        tasks = [
            FovTask.from_coordinate("A1", i, (i * 1.0, i * 2.0, 0))
            for i in range(5)
        ]
        task_list = FovTaskList(tasks=tasks, plan_hash="abc123")

        assert len(task_list) == 5
        assert task_list.cursor == 0
        assert task_list.plan_hash == "abc123"

    def test_from_coordinates(self):
        """Test building task list from region/FOV coordinates."""
        coords = {
            "A1": [(0, 0, 0), (1, 0, 0), (2, 0, 0)],
            "A2": [(0, 1, 0), (1, 1, 0)],
        }
        task_list = FovTaskList.from_coordinates(coords, plan_hash="test_hash")

        assert len(task_list) == 5
        assert task_list.plan_hash == "test_hash"
        # Verify all tasks created
        assert task_list.tasks[0].fov_id == "A1_0000"
        assert task_list.tasks[2].fov_id == "A1_0002"
        assert task_list.tasks[3].fov_id == "A2_0000"


class TestFovTaskListAdvanceAndGet:
    """Tests for advance_and_get cursor operations."""

    def test_advance_and_get_returns_pending_task(self):
        """Test that advance_and_get returns the next PENDING task."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(3)]
        task_list = FovTaskList(tasks=tasks)

        task = task_list.advance_and_get()
        assert task is not None
        assert task.fov_id == "A1_0000"
        assert task_list.cursor == 0  # Cursor stays at current task

    def test_advance_and_get_skips_non_pending(self):
        """Test that advance_and_get skips completed/skipped tasks."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(5)]
        tasks[0].status = FovStatus.COMPLETED
        tasks[1].status = FovStatus.SKIPPED
        tasks[2].status = FovStatus.FAILED
        task_list = FovTaskList(tasks=tasks)

        task = task_list.advance_and_get()
        assert task is not None
        assert task.fov_id == "A1_0003"
        assert task_list.cursor == 3

    def test_advance_and_get_returns_none_when_all_done(self):
        """Test that advance_and_get returns None when all tasks processed."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(3)]
        for t in tasks:
            t.status = FovStatus.COMPLETED
        task_list = FovTaskList(tasks=tasks)

        task = task_list.advance_and_get()
        assert task is None

    def test_advance_and_get_returns_none_on_empty_list(self):
        """Test that advance_and_get returns None for empty list."""
        task_list = FovTaskList()
        assert task_list.advance_and_get() is None


class TestFovTaskListMarkComplete:
    """Tests for mark_complete operations."""

    def test_mark_complete_advances_cursor(self):
        """Test that mark_complete advances the cursor."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(3)]
        task_list = FovTaskList(tasks=tasks)

        task_list.mark_complete("A1_0000", success=True)

        assert task_list.cursor == 1
        assert tasks[0].status == FovStatus.COMPLETED

    def test_mark_complete_failed(self):
        """Test marking a task as failed."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        task_list.mark_complete("A1_0000", success=False, error_msg="Timeout")

        assert tasks[0].status == FovStatus.FAILED
        assert tasks[0].error_message == "Timeout"

    def test_mark_complete_unknown_id(self):
        """Test mark_complete with unknown fov_id does nothing."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        task_list.mark_complete("unknown", success=True)

        assert task_list.cursor == 0
        assert tasks[0].status == FovStatus.PENDING


class TestFovTaskListJump:
    """Tests for jump_to operations."""

    def test_jump_to_is_non_destructive(self):
        """Test that jump_to does NOT mark intervening tasks as skipped."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(10)]
        task_list = FovTaskList(tasks=tasks)

        # Jump to task 5
        result = task_list.jump_to("A1_0005")

        assert result is True
        assert task_list.cursor == 5
        # CRITICAL: intervening tasks should still be PENDING
        for i in range(5):
            assert tasks[i].status == FovStatus.PENDING, f"Task {i} should still be PENDING"

    def test_jump_to_backward(self):
        """Test jumping backward in the list."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(10)]
        task_list = FovTaskList(tasks=tasks, cursor=8)

        result = task_list.jump_to("A1_0002")

        assert result is True
        assert task_list.cursor == 2

    def test_jump_to_unknown_id(self):
        """Test jump_to with unknown fov_id returns False."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks)

        result = task_list.jump_to("unknown")

        assert result is False
        assert task_list.cursor == 0

    def test_jump_to_index(self):
        """Test jump_to_index moves cursor to specific index."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(10)]
        task_list = FovTaskList(tasks=tasks)

        result = task_list.jump_to_index(7)

        assert result is True
        assert task_list.cursor == 7

    def test_jump_to_index_invalid(self):
        """Test jump_to_index with invalid index returns False."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks)

        assert task_list.jump_to_index(-1) is False
        assert task_list.jump_to_index(10) is False
        assert task_list.cursor == 0


class TestFovTaskListSkip:
    """Tests for skip operations."""

    def test_skip_marks_task_skipped(self):
        """Test that skip() marks a PENDING task as SKIPPED."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks)

        result = task_list.skip("A1_0002")

        assert result is True
        assert tasks[2].status == FovStatus.SKIPPED

    def test_skip_only_pending(self):
        """Test that skip() only works on PENDING tasks."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(3)]
        tasks[0].status = FovStatus.COMPLETED
        tasks[1].status = FovStatus.EXECUTING
        task_list = FovTaskList(tasks=tasks)

        assert task_list.skip("A1_0000") is False  # Already completed
        assert task_list.skip("A1_0001") is False  # Executing
        assert task_list.skip("A1_0002") is True   # PENDING

    def test_skip_unknown_id(self):
        """Test skip() with unknown fov_id returns False."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        assert task_list.skip("unknown") is False


class TestFovTaskListDefer:
    """Tests for defer and restore_deferred operations."""

    def test_defer_marks_task_deferred(self):
        """Test that defer() marks a PENDING task as DEFERRED."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks)

        result = task_list.defer("A1_0001")

        assert result is True
        assert tasks[1].status == FovStatus.DEFERRED

    def test_defer_only_pending(self):
        """Test that defer() only works on PENDING tasks."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(3)]
        tasks[0].status = FovStatus.COMPLETED
        task_list = FovTaskList(tasks=tasks)

        assert task_list.defer("A1_0000") is False
        assert task_list.defer("A1_0001") is True

    def test_restore_deferred_resets_to_pending(self):
        """Test that restore_deferred() resets DEFERRED tasks to PENDING."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(5)]
        tasks[1].status = FovStatus.DEFERRED
        tasks[3].status = FovStatus.DEFERRED
        tasks[4].status = FovStatus.SKIPPED  # Should not be affected
        task_list = FovTaskList(tasks=tasks)

        count = task_list.restore_deferred()

        assert count == 2
        assert tasks[1].status == FovStatus.PENDING
        assert tasks[3].status == FovStatus.PENDING
        assert tasks[4].status == FovStatus.SKIPPED  # Unchanged


class TestFovTaskListRequeue:
    """Tests for requeue operations."""

    def test_requeue_keeps_same_fov_id(self):
        """Test that requeue creates task with same fov_id."""
        tasks = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(3)]
        task_list = FovTaskList(tasks=tasks, cursor=1)

        result = task_list.requeue("A1_0001")

        assert result is True
        assert len(task_list) == 4
        # New task should have same fov_id
        new_task = task_list.tasks[2]  # Inserted after cursor
        assert new_task.fov_id == "A1_0001"

    def test_requeue_increments_attempt(self):
        """Test that requeue increments attempt number."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        task_list.requeue("A1_0000")

        original = task_list.tasks[0]
        requeued = task_list.tasks[1]
        assert original.attempt == 1
        assert requeued.attempt == 2

    def test_requeue_before_current(self):
        """Test requeue with before_current=True inserts before cursor."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(3)]
        task_list = FovTaskList(tasks=tasks, cursor=2)

        task_list.requeue("A1_0001", before_current=True)

        assert len(task_list) == 4
        # New task should be at cursor position (before current)
        assert task_list.tasks[2].fov_id == "A1_0001"
        assert task_list.tasks[2].attempt == 2

    def test_requeue_after_current(self):
        """Test requeue with before_current=False inserts after cursor."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(3)]
        task_list = FovTaskList(tasks=tasks, cursor=1)

        task_list.requeue("A1_0000", before_current=False)

        assert len(task_list) == 4
        # New task should be after cursor
        assert task_list.tasks[2].fov_id == "A1_0000"
        assert task_list.tasks[2].attempt == 2

    def test_requeue_preserves_coordinates(self):
        """Test that requeue preserves task coordinates."""
        tasks = [FovTask.from_coordinate("A1", 0, (10.5, 20.5, 5.0))]
        task_list = FovTaskList(tasks=tasks)

        task_list.requeue("A1_0000")

        requeued = task_list.tasks[1]
        assert requeued.x_mm == 10.5
        assert requeued.y_mm == 20.5
        assert requeued.z_mm == 5.0

    def test_requeue_unknown_id(self):
        """Test requeue with unknown fov_id returns False."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        result = task_list.requeue("unknown")

        assert result is False
        assert len(task_list) == 1


class TestFovTaskListStatusCounts:
    """Tests for status count methods."""

    def test_status_counts(self):
        """Test all status count methods."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(10)]
        tasks[0].status = FovStatus.PENDING
        tasks[1].status = FovStatus.PENDING
        tasks[2].status = FovStatus.EXECUTING
        tasks[3].status = FovStatus.COMPLETED
        tasks[4].status = FovStatus.COMPLETED
        tasks[5].status = FovStatus.COMPLETED
        tasks[6].status = FovStatus.FAILED
        tasks[7].status = FovStatus.SKIPPED
        tasks[8].status = FovStatus.DEFERRED
        tasks[9].status = FovStatus.DEFERRED
        task_list = FovTaskList(tasks=tasks)

        assert task_list.pending_count() == 2
        assert task_list.executing_count() == 1
        assert task_list.completed_count() == 3
        assert task_list.failed_count() == 1
        assert task_list.skipped_count() == 1
        assert task_list.deferred_count() == 2


class TestFovTaskListCheckpoint:
    """Tests for checkpoint serialization."""

    def test_checkpoint_roundtrip(self):
        """Test checkpoint serialization and deserialization."""
        tasks = [FovTask.from_coordinate("A1", i, (i * 1.0, i * 2.0, 0.5)) for i in range(3)]
        tasks[0].status = FovStatus.COMPLETED
        tasks[1].status = FovStatus.FAILED
        tasks[1].error_message = "Test error"
        tasks[1].attempt = 2
        task_list = FovTaskList(tasks=tasks, cursor=2, plan_hash="test_hash")

        # Serialize
        checkpoint = task_list.to_checkpoint()

        # Deserialize
        restored = FovTaskList.from_checkpoint(checkpoint)

        assert len(restored) == 3
        assert restored.cursor == 2
        assert restored.plan_hash == "test_hash"

        # Check tasks
        assert restored.tasks[0].status == FovStatus.COMPLETED
        assert restored.tasks[1].status == FovStatus.FAILED
        assert restored.tasks[1].error_message == "Test error"
        assert restored.tasks[1].attempt == 2
        assert restored.tasks[2].status == FovStatus.PENDING

    def test_checkpoint_roundtrip_preserves_plan_hash(self):
        """Test that checkpoint preserves plan_hash."""
        task_list = FovTaskList(plan_hash="unique_plan_hash_123")
        checkpoint = task_list.to_checkpoint()
        restored = FovTaskList.from_checkpoint(checkpoint)
        assert restored.plan_hash == "unique_plan_hash_123"

    def test_checkpoint_preserves_metadata(self):
        """Test that checkpoint preserves task metadata."""
        tasks = [FovTask(
            fov_id="A1_0000",
            region_id="A1",
            fov_index=0,
            x_mm=0,
            y_mm=0,
            z_mm=0,
            metadata={"well": "A1", "custom": 123},
        )]
        task_list = FovTaskList(tasks=tasks)

        checkpoint = task_list.to_checkpoint()
        restored = FovTaskList.from_checkpoint(checkpoint)

        assert restored.tasks[0].metadata == {"well": "A1", "custom": 123}


class TestFovTaskListThreadSafety:
    """Tests for thread safety."""

    def test_thread_safety_with_lock(self):
        """Test that concurrent operations don't corrupt state."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(1000)]
        task_list = FovTaskList(tasks=tasks)

        errors = []
        completed = []

        def worker(worker_id: int):
            try:
                for _ in range(100):
                    task = task_list.advance_and_get()
                    if task:
                        task_list.mark_complete(task.fov_id, success=True)
                        completed.append(task.fov_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        # All tasks should be completed
        assert task_list.completed_count() == 1000

    def test_concurrent_jump_and_advance(self):
        """Test concurrent jump and advance operations."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(100)]
        task_list = FovTaskList(tasks=tasks)

        errors = []

        def jumper():
            try:
                for i in range(50):
                    task_list.jump_to(f"A1_{i:04d}")
            except Exception as e:
                errors.append(e)

        def advancer():
            try:
                for _ in range(50):
                    task = task_list.advance_and_get()
                    if task:
                        task_list.mark_complete(task.fov_id, success=True)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=jumper),
            threading.Thread(target=advancer),
            threading.Thread(target=jumper),
            threading.Thread(target=advancer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"


class TestFovTaskListHelpers:
    """Tests for helper methods."""

    def test_current_task(self):
        """Test current_task returns task at cursor."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks, cursor=2)

        task = task_list.current_task()
        assert task is not None
        assert task.fov_id == "A1_0002"

    def test_current_task_out_of_bounds(self):
        """Test current_task returns None when cursor out of bounds."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks, cursor=5)

        assert task_list.current_task() is None

    def test_get_task(self):
        """Test get_task finds task by ID."""
        tasks = [FovTask.from_coordinate("A1", i, (i, 0, 0)) for i in range(5)]
        task_list = FovTaskList(tasks=tasks)

        task = task_list.get_task("A1_0003")
        assert task is not None
        assert task.fov_id == "A1_0003"
        assert task.x_mm == 3.0

    def test_get_task_not_found(self):
        """Test get_task returns None for unknown ID."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        assert task_list.get_task("unknown") is None

    def test_mark_executing(self):
        """Test mark_executing sets status to EXECUTING."""
        tasks = [FovTask.from_coordinate("A1", 0, (0, 0, 0))]
        task_list = FovTaskList(tasks=tasks)

        result = task_list.mark_executing("A1_0000")

        assert result is True
        assert tasks[0].status == FovStatus.EXECUTING

    def test_reset_cursor(self):
        """Test reset_cursor moves cursor to beginning."""
        tasks = [FovTask.from_coordinate("A1", i, (0, 0, 0)) for i in range(10)]
        task_list = FovTaskList(tasks=tasks, cursor=7)

        task_list.reset_cursor()

        assert task_list.cursor == 0
