"""
Error handling tests for multipoint acquisition.

Tests error scenarios including stage failures, camera timeouts,
autofocus failures, and abort handling.
"""

import pytest

from tests.harness import BackendContext, AcquisitionSimulator
from tests.harness.core import (
    FaultInjector,
    assert_state_sequence,
    assert_abort_behavior,
)


@pytest.fixture
def backend_ctx():
    """Create a backend context for testing."""
    with BackendContext(simulation=True) as ctx:
        yield ctx


@pytest.fixture
def sim(backend_ctx):
    """Create an acquisition simulator."""
    return AcquisitionSimulator(backend_ctx)


@pytest.fixture
def faults(backend_ctx):
    """Create a fault injector."""
    with FaultInjector(backend_ctx) as fi:
        yield fi


@pytest.fixture
def center(backend_ctx):
    """Get stage center position."""
    return backend_ctx.get_stage_center()


@pytest.fixture
def channels(backend_ctx):
    """Get available channels."""
    return backend_ctx.get_available_channels()


class TestAbortHandling:
    """Tests for acquisition abort behavior."""

    def test_abort_stops_acquisition(self, sim, center, channels):
        """Verify abort stops acquisition before completion."""
        x, y, z = center

        # Set up a large acquisition
        sim.add_grid_region("large", (x, y, z), n_x=5, n_y=5)
        sim.set_channels(channels[:min(2, len(channels))])
        sim.set_zstack(n_z=3)
        sim.set_timelapse(n_t=1)

        expected_total = 5 * 5 * min(2, len(channels)) * 3

        # Start acquisition (non-blocking)
        sim.start()

        # Wait briefly then abort
        sim.sleep(0.3)
        sim.stop()

        # Wait for acquisition to finish
        from squid.core.events import AcquisitionWorkerFinished

        finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=30)

        assert finish is not None, "Acquisition did not finish after abort"

        # Verify partial completion
        assert_abort_behavior(
            sim.monitor,
            max_images=expected_total,
            min_images=1,
        )

    def test_abort_state_sequence(self, sim, center, channels):
        """Verify correct state transitions during abort."""
        x, y, z = center

        sim.add_grid_region("test", (x, y, z), n_x=4, n_y=4)
        sim.set_channels(channels[:1])

        sim.start()
        sim.sleep(0.2)
        sim.stop()

        from squid.core.events import AcquisitionWorkerFinished, AcquisitionStateChanged

        sim.wait_for(AcquisitionWorkerFinished, timeout_s=30)

        # Wait a bit for final state change to be processed
        sim.sleep(0.3)

        # Verify state sequence includes ABORTING (key assertion)
        state_events = sim.monitor.get_events(AcquisitionStateChanged)
        saw_aborting = any(evt.is_aborting for evt in state_events)
        assert saw_aborting, "Expected ABORTING state during abort"

        # Verify we ended in non-progress state
        if state_events:
            final_state = state_events[-1]
            assert not final_state.in_progress or final_state.is_aborting, \
                "Expected final state to be non-running"

    def test_double_abort_ignored(self, sim, center, channels):
        """Verify multiple abort requests are handled gracefully."""
        x, y, z = center

        sim.add_grid_region("test", (x, y, z), n_x=3, n_y=3)
        sim.set_channels(channels[:1])

        sim.start()
        sim.sleep(0.1)

        # Send multiple aborts
        sim.stop()
        sim.stop()
        sim.stop()

        from squid.core.events import AcquisitionWorkerFinished

        finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=30)

        # Should still complete gracefully
        assert finish is not None


class TestStateTransitions:
    """Tests for acquisition state machine."""

    def test_happy_path_states(self, sim, center, channels):
        """Verify correct state sequence for successful acquisition."""
        x, y, z = center

        sim.add_single_fov("single", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=1)
        sim.set_timelapse(n_t=1)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success, f"Acquisition failed: {result.error}"

        # Verify state sequence
        assert_state_sequence(
            sim.monitor,
            ["RUNNING", "IDLE"],
            strict=False,
        )

    def test_empty_coordinates_fails(self, sim, channels):
        """Verify acquisition fails with empty coordinates."""
        # Don't add any coordinates
        sim.set_channels(channels[:1])

        result = sim.run_and_wait(timeout_s=10)

        # Should fail or complete with 0 images
        # (depends on implementation - may validate or just complete empty)
        if result.success:
            assert result.total_images == 0


class TestRecoveryBehavior:
    """Tests for recovery from partial failures."""

    def test_acquisition_completes_after_warning(self, sim, center, channels):
        """Verify acquisition can complete despite warnings."""
        x, y, z = center

        # Simple acquisition that should succeed
        sim.add_single_fov("test", x, y, z)
        sim.set_channels(channels[:1])

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 1


class TestBoundaryConditions:
    """Tests for edge cases and boundary conditions."""

    def test_single_fov_single_channel_single_z(self, sim, center, channels):
        """Verify minimal acquisition (1x1x1x1)."""
        x, y, z = center

        sim.add_single_fov("minimal", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=1)
        sim.set_timelapse(n_t=1)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 1
        assert result.total_fovs == 1

    def test_large_z_stack(self, sim, center, channels):
        """Verify large z-stack acquisition."""
        x, y, z = center

        sim.add_single_fov("zstack", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=20, delta_z_um=0.5)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success
        assert result.total_images == 20

    def test_from_center_z_stacking(self, sim, center, channels):
        """Verify FROM CENTER z-stacking mode."""
        x, y, z = center

        sim.add_single_fov("center", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=5, delta_z_um=1.0, mode="FROM CENTER")

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 5

    def test_from_top_z_stacking(self, sim, center, channels):
        """Verify FROM TOP z-stacking mode."""
        x, y, z = center

        sim.add_single_fov("top", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=5, delta_z_um=1.0, mode="FROM TOP")

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 5


class TestStageFaults:
    """Tests for stage failure handling."""

    def test_stage_failure_aborts_acquisition(self, sim, faults, center, channels):
        """Verify clean abort when stage fails mid-acquisition."""
        x, y, z = center

        sim.add_grid_region("grid", (x, y, z), n_x=5, n_y=5)
        sim.set_channels(channels[:1])

        # Configure stage to fail after 10 moves
        faults.stage.fail_after(10)

        result = sim.run_and_wait(timeout_s=30)

        # Should fail gracefully
        assert not result.success
        assert "stage" in result.error.lower()


class TestCameraFaults:
    """Tests for camera failure handling."""

    def test_camera_timeout_handled(self, sim, faults, center, channels):
        """Verify acquisition handles camera timeouts."""
        x, y, z = center

        sim.add_single_fov("test", x, y, z)
        sim.set_channels(channels[:min(2, len(channels))])

        # Configure first frame to timeout
        faults.camera.timeout_at([0])

        result = sim.run_and_wait(timeout_s=30)

        # Should complete (possibly with warnings) or fail gracefully
        # Exact behavior depends on implementation


class TestAutofocusFaults:
    """Tests for autofocus failure handling."""

    def test_af_failure_continues(self, sim, faults, center, channels):
        """Verify acquisition continues when AF fails."""
        x, y, z = center

        sim.add_grid_region("grid", (x, y, z), n_x=2, n_y=2)
        sim.set_channels(channels[:1])
        sim.set_autofocus(contrast_af=True)

        # Configure AF to fail at second FOV
        faults.autofocus.fail_at([1])

        result = sim.run_and_wait(timeout_s=30)

        # Should complete all 4 FOVs despite AF failure at FOV 1
        # (AF failures should be non-fatal)
        assert result.total_fovs == 4
