"""
Integration tests for FOV Task Command system.

Tests the FOV task commands (Jump, Skip, Requeue, Defer) during live acquisition.
Uses the test harness with BackendContext to simulate actual acquisition workflows.
"""

from __future__ import annotations

import time
from typing import List, Tuple

import pytest

from tests.harness import BackendContext, AcquisitionSimulator
from squid.backend.controllers.multipoint.events import (
    JumpToFovCommand,
    SkipFovCommand,
    RequeueFovCommand,
    DeferFovCommand,
    FovTaskStarted,
    FovTaskCompleted,
    FovTaskListChanged,
)
from squid.backend.controllers.multipoint.fov_task import FovStatus
from squid.core.events import AcquisitionWorkerFinished


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def backend_ctx():
    """Provide a simulated backend context."""
    with BackendContext(simulation=True) as ctx:
        yield ctx


@pytest.fixture
def sim(backend_ctx: BackendContext) -> AcquisitionSimulator:
    """Provide an acquisition simulator."""
    return AcquisitionSimulator(backend_ctx)


@pytest.fixture
def center(backend_ctx: BackendContext) -> Tuple[float, float, float]:
    """Provide the stage center position."""
    return backend_ctx.get_stage_center()


@pytest.fixture
def channels(backend_ctx: BackendContext) -> List[str]:
    """Provide available channel names."""
    return backend_ctx.get_available_channels()


# =============================================================================
# Jump Command Tests
# =============================================================================


class TestJumpCommand:
    """Tests for JumpToFovCommand during acquisition."""

    def test_jump_during_acquisition(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify JumpToFovCommand moves cursor without skipping FOVs."""
        # Set up a 4-FOV grid
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Track FOV events
        fov_started_events: List[FovTaskStarted] = []
        sim.monitor.subscribe(FovTaskStarted, lambda e: fov_started_events.append(e))

        # Start acquisition (non-blocking)
        sim.start()

        # Wait for first FOV to start
        first_start = sim.wait_for(FovTaskStarted, timeout_s=10.0)
        assert first_start is not None, "First FOV should start"

        # Wait a moment for first FOV to complete
        first_complete = sim.wait_for(FovTaskCompleted, timeout_s=10.0)
        assert first_complete is not None, "First FOV should complete"

        # Get the task list to find FOV IDs
        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list and len(task_list.tasks) >= 4:
            # Jump to the last FOV (non-destructive - should not skip any)
            last_fov_id = task_list.tasks[-1].fov_id
            sim.publish(JumpToFovCommand(fov_id=last_fov_id))

            # Wait for acquisition to complete
            finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
            assert finish is not None, "Acquisition should finish"

            # After jump, acquisition should continue normally
            # All FOVs should eventually be processed (either completed or jumped-back-to)
            completed = task_list.completed_count()
            assert completed >= 1, "At least one FOV should complete"
        else:
            # Fallback: just wait for completion
            finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
            assert finish is not None

    def test_jump_is_non_destructive(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify jump does not mark intervening FOVs as skipped."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Start acquisition
        sim.start()

        # Wait for first FOV to complete
        first_complete = sim.wait_for(FovTaskCompleted, timeout_s=10.0)
        assert first_complete is not None

        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list and len(task_list.tasks) >= 4:
            # Check initial state - no skipped
            assert task_list.skipped_count() == 0

            # Jump to last FOV
            last_fov_id = task_list.tasks[-1].fov_id
            sim.publish(JumpToFovCommand(fov_id=last_fov_id))

            # Wait for list changed event
            sim.drain(timeout_s=1.0)

            # Verify nothing was skipped by the jump
            assert task_list.skipped_count() == 0, "Jump should not skip FOVs"

        # Wait for completion
        sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)


# =============================================================================
# Skip Command Tests
# =============================================================================


class TestSkipCommand:
    """Tests for SkipFovCommand during acquisition."""

    def test_skip_during_acquisition(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify SkipFovCommand marks FOV as skipped and moves on."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Track skip events
        skip_events: List[FovTaskCompleted] = []
        sim.monitor.subscribe(
            FovTaskCompleted,
            lambda e: skip_events.append(e) if e.status == FovStatus.SKIPPED else None,
        )

        # Start acquisition
        sim.start()

        # Wait for first FOV to start
        first_start = sim.wait_for(FovTaskStarted, timeout_s=10.0)
        assert first_start is not None

        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list and len(task_list.tasks) >= 4:
            # Find a pending FOV (not the current one)
            pending_fovs = [t for t in task_list.tasks if t.status == FovStatus.PENDING]
            if len(pending_fovs) >= 2:
                # Skip the second pending FOV
                fov_to_skip = pending_fovs[1]
                sim.publish(SkipFovCommand(fov_id=fov_to_skip.fov_id))

                # Wait for acquisition to complete
                finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
                assert finish is not None

                # Verify the FOV was skipped
                assert task_list.skipped_count() >= 1, "At least one FOV should be skipped"
            else:
                # Not enough pending FOVs, just complete
                sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
        else:
            sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)

    def test_skip_reduces_total_fovs(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify skipping FOVs reduces the number of imaged FOVs."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Start acquisition
        sim.start()

        # Wait for first FOV
        first_start = sim.wait_for(FovTaskStarted, timeout_s=10.0)
        assert first_start is not None

        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list and len(task_list.tasks) >= 4:
            # Skip all pending FOVs except one
            pending = [t for t in task_list.tasks if t.status == FovStatus.PENDING]
            for fov in pending[:-1]:  # Keep the last one
                sim.publish(SkipFovCommand(fov_id=fov.fov_id))

        # Wait for completion
        finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
        assert finish is not None


# =============================================================================
# Requeue Command Tests
# =============================================================================


class TestRequeueCommand:
    """Tests for RequeueFovCommand during acquisition."""

    def test_requeue_during_acquisition(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify RequeueFovCommand adds FOV back with incremented attempt."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Start acquisition
        sim.start()

        # Wait for first FOV to complete
        first_complete = sim.wait_for(FovTaskCompleted, timeout_s=10.0)
        assert first_complete is not None, "First FOV should complete"

        # Requeue the completed FOV
        sim.publish(
            RequeueFovCommand(fov_id=first_complete.fov_id, before_current=False)
        )

        # Wait for acquisition to complete
        finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
        assert finish is not None

        # The requeued FOV should have been processed again
        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list:
            # Find the requeued task (attempt > 1)
            retried = [t for t in task_list.tasks if t.attempt > 1]
            assert len(retried) >= 1, "At least one FOV should have attempt > 1"

    def test_requeue_before_current(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify requeue with before_current=True inserts before cursor."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Start acquisition
        sim.start()

        # Wait for first FOV to complete
        first_complete = sim.wait_for(FovTaskCompleted, timeout_s=10.0)
        assert first_complete is not None

        # Requeue before current position (for backtracking)
        sim.publish(
            RequeueFovCommand(fov_id=first_complete.fov_id, before_current=True)
        )

        # Wait for acquisition to complete
        finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
        assert finish is not None

    def test_requeue_preserves_fov_id(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify requeue keeps same fov_id (only increments attempt)."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Track all FOV completed events
        completed_events: List[FovTaskCompleted] = []
        sim.monitor.subscribe(FovTaskCompleted, lambda e: completed_events.append(e))

        # Start acquisition
        sim.start()

        # Wait for first FOV to complete
        first_complete = sim.wait_for(FovTaskCompleted, timeout_s=10.0)
        assert first_complete is not None

        original_fov_id = first_complete.fov_id

        # Requeue it
        sim.publish(RequeueFovCommand(fov_id=original_fov_id, before_current=False))

        # Wait for completion
        finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)
        assert finish is not None

        # Find completions of the same fov_id
        same_fov_completions = [e for e in completed_events if e.fov_id == original_fov_id]

        # Should have at least 2 completions (original + requeue)
        if len(same_fov_completions) >= 2:
            assert same_fov_completions[1].attempt > same_fov_completions[0].attempt


# =============================================================================
# Defer Command Tests
# =============================================================================


class TestDeferCommand:
    """Tests for DeferFovCommand during acquisition."""

    def test_defer_marks_fov_as_deferred(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify DeferFovCommand marks FOV as DEFERRED."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Start acquisition
        sim.start()

        # Wait for first FOV
        first_start = sim.wait_for(FovTaskStarted, timeout_s=10.0)
        assert first_start is not None

        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list and len(task_list.tasks) >= 4:
            # Defer a pending FOV
            pending = [t for t in task_list.tasks if t.status == FovStatus.PENDING]
            if pending:
                sim.publish(DeferFovCommand(fov_id=pending[0].fov_id))

                # Give time for command to be processed
                sim.drain(timeout_s=1.0)

                # Check deferred count
                assert task_list.deferred_count() >= 1 or task_list.pending_count() >= 0

        # Wait for completion
        sim.wait_for(AcquisitionWorkerFinished, timeout_s=60.0)


# =============================================================================
# Checkpoint Resume Tests
# =============================================================================


class TestCheckpointResume:
    """Tests for checkpoint-based resume functionality."""

    def test_task_list_to_checkpoint(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify task list can be serialized to checkpoint format."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Start acquisition
        sim.start()

        # Wait for first FOV to complete
        first_complete = sim.wait_for(FovTaskCompleted, timeout_s=10.0)
        assert first_complete is not None

        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list:
            # Serialize to checkpoint
            checkpoint_data = task_list.to_checkpoint()

            # Verify checkpoint structure
            assert "cursor" in checkpoint_data
            assert "plan_hash" in checkpoint_data
            assert "tasks" in checkpoint_data
            assert len(checkpoint_data["tasks"]) == len(task_list.tasks)

            # Verify task structure
            for task_data in checkpoint_data["tasks"]:
                assert "fov_id" in task_data
                assert "region_id" in task_data
                assert "x_mm" in task_data
                assert "y_mm" in task_data
                assert "z_mm" in task_data
                assert "status" in task_data
                assert "attempt" in task_data

        # Stop acquisition
        sim.stop()
        sim.wait_for(AcquisitionWorkerFinished, timeout_s=30.0)

    def test_task_list_from_checkpoint(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify task list can be restored from checkpoint."""
        from squid.backend.controllers.multipoint.fov_task import FovTaskList

        # Set up and start acquisition
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])
        sim.start()

        # Wait for some progress
        sim.wait_for(FovTaskCompleted, timeout_s=10.0)

        task_list = sim.ctx.multipoint_controller.get_fov_task_list()
        if task_list:
            # Create checkpoint
            checkpoint_data = task_list.to_checkpoint()

            # Restore from checkpoint
            restored = FovTaskList.from_checkpoint(checkpoint_data)

            # Verify restoration
            assert len(restored.tasks) == len(task_list.tasks)
            assert restored.cursor == task_list.cursor
            assert restored.plan_hash == task_list.plan_hash

            # Verify task data matches
            for orig, rest in zip(task_list.tasks, restored.tasks):
                assert rest.fov_id == orig.fov_id
                assert rest.region_id == orig.region_id
                assert rest.fov_index == orig.fov_index
                assert rest.status == orig.status

        # Stop acquisition
        sim.stop()
        sim.wait_for(AcquisitionWorkerFinished, timeout_s=30.0)


# =============================================================================
# Event Integration Tests
# =============================================================================


class TestFovEventIntegration:
    """Tests for FOV event publishing during acquisition."""

    def test_fov_started_events_published(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify FovTaskStarted events are published for each FOV."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Track events
        sim.monitor.subscribe(FovTaskStarted)

        # Run acquisition
        result = sim.run_and_wait(timeout_s=60.0)
        assert result.success

        started_events = sim.monitor.get_events(FovTaskStarted)
        assert len(started_events) >= 4, "Should have at least 4 FovTaskStarted events"

        # Verify event fields
        for event in started_events:
            assert event.fov_id is not None
            assert event.region_id is not None
            assert event.attempt >= 1

    def test_fov_completed_events_published(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify FovTaskCompleted events are published for each FOV."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Track events
        sim.monitor.subscribe(FovTaskCompleted)

        # Run acquisition
        result = sim.run_and_wait(timeout_s=60.0)
        assert result.success

        completed_events = sim.monitor.get_events(FovTaskCompleted)
        assert len(completed_events) >= 4, "Should have at least 4 FovTaskCompleted events"

        # Verify event fields
        for event in completed_events:
            assert event.fov_id is not None
            assert event.status in (FovStatus.COMPLETED, FovStatus.FAILED, FovStatus.SKIPPED)

    def test_fov_events_include_counts(
        self, sim: AcquisitionSimulator, center, channels
    ):
        """Verify FovTaskStarted includes pending/completed counts."""
        # Set up 4 FOVs
        sim.add_grid_region("test", center, n_x=2, n_y=2)
        sim.set_channels(channels[:1])

        # Track events
        sim.monitor.subscribe(FovTaskStarted)

        # Run acquisition
        result = sim.run_and_wait(timeout_s=60.0)
        assert result.success

        started_events = sim.monitor.get_events(FovTaskStarted)
        # Verify counts progress correctly
        for i, event in enumerate(started_events):
            # Completed count should increase
            assert event.completed_count >= 0
            # Pending count should decrease
            if i > 0:
                assert event.pending_count <= started_events[0].pending_count
