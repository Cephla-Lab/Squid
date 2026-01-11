"""
Integration tests for multipoint acquisition workflows.

These tests use the test harness to simulate GUI-driven acquisition workflows,
providing comprehensive coverage of the multipoint acquisition system.
"""

from __future__ import annotations

import time
from typing import List, Tuple

import pytest

from tests.harness import BackendContext, AcquisitionSimulator
from tests.harness.core.assertions import (
    assert_acquisition_completed,
    assert_progress_monotonic,
    assert_no_errors,
    assert_image_count,
    assert_fov_count,
)


# =============================================================================
# Fixtures (using harness fixtures)
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
# Basic Acquisition Tests
# =============================================================================


class TestBasicAcquisition:
    """Test basic acquisition scenarios."""

    def test_single_fov_single_channel(self, sim: AcquisitionSimulator, center, channels):
        """Simplest case: 1 FOV, 1 channel, no z-stack."""
        x, y, z = center
        sim.add_single_fov("single", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=1)
        sim.set_timelapse(n_t=1)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_images == 1
        assert result.total_fovs == 1

    def test_single_fov_multi_channel(self, sim: AcquisitionSimulator, center, channels):
        """1 FOV with multiple channels."""
        x, y, z = center
        sim.add_single_fov("multi_ch", x, y, z)

        num_channels = min(3, len(channels))
        sim.set_channels(channels[:num_channels])

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == num_channels

    def test_grid_3x3(self, sim: AcquisitionSimulator, center, channels):
        """3x3 FOV grid."""
        sim.add_grid_region("grid", center, n_x=3, n_y=3)
        sim.set_channels(channels[:1])

        result = sim.run_and_wait(timeout_s=60)

        assert result.success
        assert result.total_images == 9
        assert result.total_fovs == 9


# =============================================================================
# Z-Stack Tests
# =============================================================================


class TestZStackAcquisition:
    """Test z-stack acquisition scenarios."""

    def test_zstack_from_bottom(self, sim: AcquisitionSimulator, center, channels):
        """Z-stack from bottom: 5 z-planes."""
        x, y, z = center
        sim.add_single_fov("zstack", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=5, delta_z_um=2.0, mode="FROM BOTTOM")

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 5

    def test_zstack_from_center(self, sim: AcquisitionSimulator, center, channels):
        """Z-stack from center: symmetric around focus."""
        x, y, z = center
        sim.add_single_fov("zstack_center", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=5, delta_z_um=1.0, mode="FROM CENTER")

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 5

    def test_zstack_multichannel(self, sim: AcquisitionSimulator, center, channels):
        """Z-stack with multiple channels."""
        x, y, z = center
        sim.add_single_fov("zstack_multi", x, y, z)

        num_ch = min(2, len(channels))
        sim.set_channels(channels[:num_ch])
        sim.set_zstack(n_z=3, delta_z_um=2.0)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 3 * num_ch


# =============================================================================
# Time-Lapse Tests
# =============================================================================


class TestTimeLapseAcquisition:
    """Test time-lapse acquisition scenarios."""

    def test_timelapse_short_interval(self, sim: AcquisitionSimulator, center, channels):
        """Time-lapse: 3 timepoints with 0.5s interval."""
        x, y, z = center
        sim.add_single_fov("timelapse", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_timelapse(n_t=3, delta_t_s=0.5)

        start = time.time()
        result = sim.run_and_wait(timeout_s=30)
        elapsed = time.time() - start

        assert result.success
        assert result.total_images == 3
        # Should take at least 1 second (2 intervals)
        assert elapsed >= 0.8

    def test_timelapse_with_zstack(self, sim: AcquisitionSimulator, center, channels):
        """Time-lapse with z-stack at each timepoint."""
        x, y, z = center
        sim.add_single_fov("timelapse_z", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=3, delta_z_um=1.0)
        # Use longer interval to ensure timepoints aren't skipped due to acquisition duration
        sim.set_timelapse(n_t=2, delta_t_s=2.0)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 2 * 3  # 2 timepoints × 3 z-levels


# =============================================================================
# Multi-Region Tests
# =============================================================================


class TestMultiRegionAcquisition:
    """Test multi-region acquisition scenarios."""

    def test_multiple_single_fov_regions(self, sim: AcquisitionSimulator, center, channels):
        """Multiple regions, each with single FOV."""
        x, y, z = center

        for i in range(4):
            sim.add_single_fov(f"region_{i}", x + i * 0.5, y + i * 0.5, z)

        sim.set_channels(channels[:1])

        result = sim.run_and_wait(timeout_s=60)

        assert result.success
        assert result.total_images == 4
        assert result.total_fovs == 4

    def test_mixed_region_sizes(self, sim: AcquisitionSimulator, center, channels):
        """Regions with different sizes: 1 + 4 + 9 FOVs."""
        x, y, z = center

        sim.add_single_fov("single", x, y, z)
        sim.add_grid_region("small", (x + 3, y, z), n_x=2, n_y=2)
        sim.add_grid_region("large", (x + 6, y, z), n_x=3, n_y=3)

        sim.set_channels(channels[:1])

        result = sim.run_and_wait(timeout_s=90)

        assert result.success
        assert result.total_images == 1 + 4 + 9


# =============================================================================
# Abort Tests
# =============================================================================


class TestAbortHandling:
    """Test abort handling during acquisition."""

    def test_abort_mid_acquisition(self, sim: AcquisitionSimulator, center, channels):
        """Abort during acquisition."""
        # Large acquisition to give time to abort
        sim.add_grid_region("large", center, n_x=5, n_y=5)
        sim.set_channels(channels[:min(2, len(channels))])
        sim.set_zstack(n_z=3)

        # Start non-blocking
        sim.start()

        # Wait a bit then abort
        time.sleep(0.5)
        sim.stop()

        # Wait for completion
        from squid.core.events import AcquisitionWorkerFinished
        finish = sim.wait_for(AcquisitionWorkerFinished, timeout_s=30)

        # Should complete (aborted) with fewer images than expected
        expected_total = 5 * 5 * min(2, len(channels)) * 3
        actual_images = len(sim.monitor.get_events(
            __import__("squid.core.events", fromlist=["AcquisitionCoordinates"]).AcquisitionCoordinates
        ))

        assert actual_images < expected_total


# =============================================================================
# Autofocus Tests
# =============================================================================


class TestAutofocusIntegration:
    """Test autofocus integration during acquisition."""

    def test_with_contrast_af(self, sim: AcquisitionSimulator, center, channels):
        """Acquisition with contrast-based autofocus."""
        x, y, z = center

        sim.add_single_fov("af1", x, y, z)
        sim.add_single_fov("af2", x + 1, y + 1, z)

        sim.set_channels(channels[:1])
        sim.set_autofocus(contrast_af=True)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success
        assert result.total_images == 2


# =============================================================================
# Complex Workflow Tests
# =============================================================================


class TestComplexWorkflows:
    """Test complex, realistic acquisition workflows."""

    def test_full_experiment_simulation(self, sim: AcquisitionSimulator, center, channels):
        """
        Full experiment: 4 regions × 2x2 FOVs × 2 channels × 3 z-planes × 2 timepoints.

        This simulates a realistic high-content screening experiment.
        """
        x, y, z = center

        # 4 regions (simulating wells)
        for i in range(4):
            sim.add_grid_region(
                f"well_{i}",
                (x + i * 2, y, z),
                n_x=2,
                n_y=2,
            )

        num_ch = min(2, len(channels))
        sim.set_channels(channels[:num_ch])
        sim.set_zstack(n_z=3, delta_z_um=1.0)
        # Use longer interval to ensure timepoints aren't skipped due to acquisition duration
        sim.set_timelapse(n_t=2, delta_t_s=60.0)

        # Expected: 4 regions × 4 FOVs × 2 channels × 3 z × 2 timepoints
        expected = 4 * 4 * num_ch * 3 * 2

        result = sim.run_and_wait(timeout_s=300)

        assert result.success, f"Failed: {result.error}"
        assert result.total_images == expected

    def test_load_coordinates_workflow(self, sim: AcquisitionSimulator, center, channels):
        """Load coordinates workflow (simulates loading from CSV)."""
        x, y, z = center

        # Explicit coordinates
        coords = {
            "region_A": [
                (x, y, z),
                (x + 0.5, y, z),
                (x, y + 0.5, z),
            ],
            "region_B": [
                (x + 2, y + 2, z),
                (x + 2.5, y + 2, z),
            ],
        }

        sim.load_coordinates(coords)
        sim.set_channels(channels[:1])

        result = sim.run_and_wait(xy_mode="Load Coordinates", timeout_s=60)

        assert result.success
        assert result.total_images == 5  # 3 + 2 FOVs


# =============================================================================
# Progress Tracking Tests
# =============================================================================


class TestProgressTracking:
    """Test progress event publishing."""

    def test_progress_events_received(self, sim: AcquisitionSimulator, center, channels):
        """Verify progress events are published during acquisition."""
        sim.add_grid_region("progress", center, n_x=3, n_y=3)
        sim.set_channels(channels[:min(2, len(channels))])
        sim.set_zstack(n_z=2)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success
        assert len(result.progress_events) > 0
        assert len(result.worker_progress_events) > 0

    def test_progress_monotonic_increase(self, sim: AcquisitionSimulator, center, channels):
        """Verify progress increases monotonically."""
        sim.add_grid_region("mono", center, n_x=4, n_y=4)
        sim.set_channels(channels[:1])

        result = sim.run_and_wait(timeout_s=60)

        assert result.success

        # Verify progress is monotonic
        for i in range(1, len(result.progress_events)):
            prev = result.progress_events[i - 1]
            curr = result.progress_events[i]

            # Either same FOV or increased
            assert (
                curr.current_fov >= prev.current_fov or
                curr.current_round > prev.current_round
            ), f"Progress decreased: {prev} -> {curr}"


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_z_level(self, sim: AcquisitionSimulator, center, channels):
        """NZ=1 should work correctly."""
        x, y, z = center
        sim.add_single_fov("nz1", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=1)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 1

    def test_single_timepoint(self, sim: AcquisitionSimulator, center, channels):
        """Nt=1 should work correctly."""
        x, y, z = center
        sim.add_single_fov("nt1", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_timelapse(n_t=1)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success
        assert result.total_images == 1

    def test_large_zstack(self, sim: AcquisitionSimulator, center, channels):
        """Large z-stack (20 levels)."""
        x, y, z = center
        sim.add_single_fov("large_z", x, y, z)
        sim.set_channels(channels[:1])
        sim.set_zstack(n_z=20, delta_z_um=0.5)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success
        assert result.total_images == 20
